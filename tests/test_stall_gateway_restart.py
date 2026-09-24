"""Stall-retry gateway restart (ledger §21).

A stalled turn used to be retried by killing only the agent CLI, so attempt 2
was re-sent into the same wedged `openclaw gateway` — the process that owns the
frozen upstream stream and `chat.jsonl.lock`. 7/7 such stalls were fatal.

T1 pins the kill script's `[o]`-class pattern (a bare `openclaw agent` pattern
also matches the wrapper shell's own cmdline, so the shell SIGTERMs itself and
the SIGKILL + lock removal never run). T2 pins the readiness poll's marker
arithmetic, which a restart depends on because gateway.log is appended across
gateway lifetimes.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.agents.openclaw.runner as ocr  # noqa: E402


# --- T1: the kill script must not signal its own wrapper shell --------------

_PATTERN = re.compile(r"'(\[o\]|o)penclaw (?:agent|gateway)'")


def _captured_script(terminator, task_id="task-t1"):
    """The `-lc` script the terminator hands to `docker exec`.

    Patched by hand rather than with monkeypatch because runner.py does a plain
    `import subprocess`: `ocr.subprocess` IS this module, so a fixture-scoped
    patch would still be live when this test runs its own real processes and
    every one of them would silently return the stub.
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    real, subprocess.run = subprocess.run, fake_run
    try:
        terminator(task_id)
    finally:
        subprocess.run = real
    assert seen["cmd"][:3] == ["docker", "exec", task_id]
    return seen["cmd"][-1]


def _retarget(script: str, token: str, home: Path) -> str:
    """Point the script at a throwaway process and lock dir while PRESERVING
    the bracket shape, which is the only thing under test.

    The literal swap is a safety requirement, not tidiness: the host-side
    `docker exec … openclaw agent --session-id …` argv of every live turn
    contains `openclaw agent`, so running the captured script verbatim under
    pytest would pkill running batches off this box.
    """
    def sub(m):
        return (f"'[{token[0]}]{token[1:]}'" if m.group(1) == "[o]"
                else f"'{token}'")

    return _PATTERN.sub(sub, script).replace("/root/.openclaw",
                                             f"{home}/.openclaw")


@pytest.mark.skipif(shutil.which("pkill") is None, reason="needs pkill")
@pytest.mark.parametrize("terminator", ["_terminate_agent_invocations",
                                        "_terminate_gateway"])
def test_kill_script_survives_its_own_pattern(tmp_path, terminator):
    token = f"wcb-decoy-{os.getpid()}"
    script = _retarget(
        _captured_script(getattr(ocr.OpenClawAgent, terminator)),
        token, tmp_path)
    assert token[1:] in script and not _PATTERN.search(script)
    lock = tmp_path / ".openclaw/agents/main/sessions/chat.jsonl.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1967\n")

    decoy = subprocess.Popen(["/bin/bash", "-c", f"exec -a {token} sleep 300"])
    try:
        for _ in range(50):  # the exec is async; wait for the real cmdline
            if subprocess.run(["pgrep", "-f", token],
                              capture_output=True).returncode == 0:
                break
            time.sleep(0.1)
        r = subprocess.run(["/bin/bash", "-lc", script],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (
            f"the kill script signalled its own wrapper shell (rc={r.returncode}): "
            "a bare pattern also matches the `bash -lc '…pkill -f …'` cmdline "
            "that is running it, so everything after the first pkill — the "
            "SIGKILL and the stale-lock removal — never runs")
        try:
            decoy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("the target survived: the SIGKILL rung never ran")
        assert not lock.exists(), (
            "the stale chat.jsonl.lock survived; every later attempt then dies "
            "in openclaw's 'session file locked' failover loop")
    finally:
        if decoy.poll() is None:
            decoy.kill()
        decoy.wait()


# --- T2: a restart must wait for a NEW marker, and a bind failure is not one --

_LISTEN = "INFO gateway listening on ws://0.0.0.0:8080\n"
_BIND_FAIL = ("ERROR GatewayLockError: another gateway instance is already "
              "listening on ws://0.0.0.0:8080\n")


class _AliveProc:
    pid = 4242
    returncode = None

    def poll(self):
        return None


def _wait(log: Path, baseline: int) -> bool:
    return ocr.OpenClawAgent._wait_gateway_listening(
        "task-t2", log, _AliveProc(), baseline=baseline)


def test_restart_waits_for_a_new_listen_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_READY_TIMEOUT", "1")
    log = tmp_path / "gateway.log"
    log.write_text(_LISTEN)

    assert _wait(log, 0) is True, (
        "baseline 0 is the initial start and must behave exactly like the old "
        "`'listening on ws' in text` test")
    assert _wait(log, 1) is False, (
        "gateway.log is opened with append=True across restarts, so the "
        "PREVIOUS lifetime's marker is still in the file; counting it as "
        "readiness returns before the new gateway has bound anything")

    with log.open("a") as fh:
        fh.write(_LISTEN)
    assert _wait(log, 1) is True


def test_bind_failure_is_not_read_as_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_READY_TIMEOUT", "1")
    log = tmp_path / "gateway.log"
    log.write_text(_LISTEN + _BIND_FAIL)

    assert _wait(log, 1) is False, (
        "GatewayLockError's 'another gateway instance is already listening on "
        "ws://…' contains the readiness literal, so a plain count reports the "
        "gateway back up on the exact bind failure this poll exists to catch")


# --- T3: what the first stall actually does, in order ----------------------


class _StubProc:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_first_stall_restarts_the_gateway_before_resending(monkeypatch,
                                                           tmp_path):
    from src.agents.base import AgentTaskSpec
    from src.agents.openclaw import OpenClawAgent

    order, launches, procs = [], [], []
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("WCB_EMPTY_TURN_LIMIT", "0")

    for name in ("start_container", "inject_lobster_workspace",
                 "inject_data_into_workspace", "inject_persona_into_workspace",
                 "inject_openclaw_models", "inject_api_connectors",
                 "run_warmup", "setup_skills", "setup_workspace",
                 "snapshot_workspace_state", "write_turn_marker"):
        monkeypatch.setattr(ocr, name, lambda *a, **k: None)
    monkeypatch.setattr(ocr.time, "sleep", lambda *a, **k: None)

    def fake_run_background(task_id, bash_cmd=None, log_path=None, **k):
        launches.append((bash_cmd, log_path, k.get("append", False)))
        if "openclaw gateway" in (bash_cmd or ""):
            order.append(("launch_gateway", k.get("append", False)))
            # A real relaunch appends its own marker; the poll waits for it.
            with Path(log_path).open("a") as fh:
                fh.write(_LISTEN)
        procs.append(_StubProc(pid=1000 + len(procs)))
        return procs[-1]

    monkeypatch.setattr(ocr, "run_background", fake_run_background)

    def fake_exec(cmd, **kw):
        script = cmd[-1] if isinstance(cmd, (list, tuple)) else ""
        for pattern in ("[o]penclaw gateway", "[o]penclaw agent"):
            if pattern in script:
                order.append(("kill", pattern))
        return subprocess.CompletedProcess(cmd, 1, "0\n", "stubbed")

    monkeypatch.setattr(ocr.subprocess, "run", fake_exec)

    real_wait = ocr.OpenClawAgent._wait_gateway_listening

    def spy_wait(task_id, gateway_log, gateway_proc, baseline=0):
        order.append(("wait", baseline))
        return real_wait(task_id, gateway_log, gateway_proc, baseline)

    monkeypatch.setattr(ocr.OpenClawAgent, "_wait_gateway_listening",
                        staticmethod(spy_wait))

    agent = OpenClawAgent(gateway_port=8080, image_model="")
    for m in ("_set_bootstrap_limits", "_index_memory", "_set_model",
              "_inject_auth", "_set_image_model"):
        monkeypatch.setattr(agent, m, lambda *a, **k: None)
    monkeypatch.setattr(agent, "_wait_for_llm_route_ready", lambda *a, **k: True)
    agent.litellm_usage_log = "/tmp/wcb-test-usage.jsonl"
    monkeypatch.setattr(agent, "_run_key_bearer_live", lambda *a, **k: True)
    monkeypatch.setattr(agent, "_count_run_key_rows", lambda *a, **k: 0)
    outcomes = iter(["stalled", "ok"])
    monkeypatch.setattr(agent, "_turn_wait_outcome", lambda *a, **k: next(outcomes))
    monkeypatch.setattr(ocr.OpenClawAgent, "_restore_session_to",
                        classmethod(lambda cls, *a: order.append(("restore",))))

    result = agent.run_task(AgentTaskSpec(
        task_id="task-t3", task={}, workspace_path=str(tmp_path),
        prompt="p", timeout_seconds=30, output_dir=out, model="m",
        thinking=None, models_config=None, lobster=None))

    assert result.error is None
    assert order == [
        ("launch_gateway", False),
        ("wait", 0),
        ("kill", "[o]penclaw agent"),
        ("kill", "[o]penclaw gateway"),
        ("launch_gateway", True),
        ("wait", 1),
        ("restore",),
    ], order
    gateway_launches = [x for x in launches if "openclaw gateway" in (x[0] or "")]
    assert gateway_launches[1][1] == out / "gateway.log"
    assert gateway_launches[1][2] is True, (
        "append=False truncates gateway.log, which destroys the forensics that "
        "solved this incident AND makes the baseline count pass on an empty file")
    assert result.gateway_proc is procs[2], (
        "the AgentExecution must carry the RELAUNCHED gateway; run_batch tears "
        "down whatever proc it finds there and would otherwise leak this one")
