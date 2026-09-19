"""Tests for docker/agent_faketime_shim.js and its harness wiring.

Regression cover for the anchor defect: cron (and anything else launched from a
scrubbed environment) inherits neither NODE_OPTIONS nor WCB_FAKE_CLOCK_EPOCH_MS,
so such a process ran on the REAL host clock while the agent ran in persona
time; and a process that adopted the anchor later than another disagreed with it
by exactly the real time between the two adoptions.

The node-driven cases execute the real shim, so they assert on the behaviour the
agent container actually gets rather than on a Python re-implementation.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SHIM = REPO_ROOT / "docker" / "agent_faketime_shim.js"
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node not installed")

# 2026-10-26T07:33:20Z — a persona window months away from any real run date,
# so "shim inactive" is unmistakable rather than a near-miss.
ANCHOR_MS = 1_793_000_000_000
# The discrepancy reported from the field: work scheduled ~17 minutes after the
# harness set the anchor came back that far behind the agent's own clock.
LATE_START_S = 1008
# Slack for process spawn + node bootstrap.
TOLERANCE_MS = 15_000


def _anchor_file(tmp_path, epoch_ms=ANCHOR_MS, age_s=0):
    """Write an anchor file, optionally backdating its mtime by ``age_s``."""
    p = tmp_path / "clock_epoch"
    p.write_text(str(epoch_ms), encoding="utf-8")
    if age_s:
        when = time.time() - age_s
        os.utime(p, (when, when))
    return p


def _run_node(env, script="process.stdout.write(String(Date.now()))"):
    r = subprocess.run(
        [NODE, "-r", str(SHIM), "-e", script],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, f"node failed: {r.stderr}"
    return r.stdout.strip(), r.stderr


def _observed_now_ms(env):
    out, _ = _run_node(env)
    return int(out)


def _scrubbed_env(anchor_path):
    """What cron leaves a job with: no NODE_OPTIONS, no anchor epoch, no TZ."""
    env = {"PATH": os.environ["PATH"], "HOME": "/root", "SHELL": "/bin/sh"}
    if anchor_path is not None:
        env["WCB_FAKE_CLOCK_FILE"] = str(anchor_path)
    return env


def _agent_env(anchor_path, epoch_ms=ANCHOR_MS):
    """What the long-lived agent process gets from `docker run -e`."""
    env = _scrubbed_env(anchor_path)
    env["WCB_FAKE_CLOCK_EPOCH_MS"] = str(epoch_ms)
    return env


# --------------------------------------------------------------------------
# A. The cron defect: scrubbed env must not fall back to the real host clock.
# --------------------------------------------------------------------------

@requires_node
def test_scrubbed_env_process_still_sees_simulated_clock(tmp_path):
    anchor = _anchor_file(tmp_path)
    observed = _observed_now_ms(_scrubbed_env(anchor))
    real_now = time.time() * 1000
    assert abs(observed - ANCHOR_MS) < TOLERANCE_MS
    # The whole point: it must NOT be the real host clock.
    assert abs(observed - real_now) > 86_400_000


@requires_node
def test_scrubbed_env_without_any_anchor_is_a_noop(tmp_path):
    # No env epoch and no readable file -> stay on real time (opt-in preserved).
    observed = _observed_now_ms(_scrubbed_env(tmp_path / "absent"))
    assert abs(observed - time.time() * 1000) < TOLERANCE_MS


def test_shim_default_anchor_path_matches_harness_constant():
    """A fully scrubbed process loses WCB_FAKE_CLOCK_FILE too, so the shim's
    built-in default is the only thing that can find the anchor."""
    from src.utils.docker_utils import AGENT_SIM_CLOCK_FILE

    src = SHIM.read_text(encoding="utf-8")
    m = re.search(r'WCB_FAKE_CLOCK_FILE\s*\|\|\s*"([^"]+)"', src)
    assert m, "shim no longer has a literal default anchor path"
    assert m.group(1) == AGENT_SIM_CLOCK_FILE


# --------------------------------------------------------------------------
# B. The anchor-skew defect: adopting an anchor late must not lose real time.
# --------------------------------------------------------------------------

@requires_node
def test_late_starter_recovers_time_elapsed_since_anchor_was_written(tmp_path):
    anchor = _anchor_file(tmp_path, age_s=LATE_START_S)
    observed = _observed_now_ms(_scrubbed_env(anchor))
    expected = ANCHOR_MS + LATE_START_S * 1000
    assert abs(observed - expected) < TOLERANCE_MS
    # Anchoring on read-time instead of the anchor's mtime lands here.
    assert observed - ANCHOR_MS > (LATE_START_S * 1000) // 2


@requires_node
def test_agent_and_scrubbed_child_report_the_same_instant(tmp_path):
    anchor = _anchor_file(tmp_path, age_s=LATE_START_S)
    agent = _observed_now_ms(_agent_env(anchor))
    cron_child = _observed_now_ms(_scrubbed_env(anchor))
    assert abs(agent - cron_child) < TOLERANCE_MS


@requires_node
def test_stale_env_epoch_loses_to_the_live_anchor_file(tmp_path):
    # Turn 0 rode the env var; the file holds whatever the last turn set.
    anchor = _anchor_file(tmp_path, epoch_ms=ANCHOR_MS)
    observed = _observed_now_ms(_agent_env(anchor, epoch_ms=ANCHOR_MS - 5 * 86_400_000))
    assert abs(observed - ANCHOR_MS) < TOLERANCE_MS


# --------------------------------------------------------------------------
# C. Pre-existing behaviour that must not regress.
# --------------------------------------------------------------------------

@requires_node
def test_env_anchor_still_used_when_file_absent(tmp_path):
    observed = _observed_now_ms(_agent_env(tmp_path / "absent"))
    assert abs(observed - ANCHOR_MS) < TOLERANCE_MS


@requires_node
def test_per_turn_reanchor_still_applies_mid_process(tmp_path):
    anchor = _anchor_file(tmp_path)
    next_turn = ANCHOR_MS + 3 * 86_400_000
    script = (
        "const fs=require('fs');"
        "const first=Date.now();"
        f"fs.writeFileSync({json.dumps(str(anchor))}, {json.dumps(str(next_turn))});"
        "setTimeout(()=>{process.stdout.write(JSON.stringify("
        "{first, second: Date.now()}));}, 1300);"
    )
    out, _ = _run_node(_agent_env(anchor), script=script)
    seen = json.loads(out)
    assert abs(seen["first"] - ANCHOR_MS) < TOLERANCE_MS
    assert abs(seen["second"] - next_turn) < TOLERANCE_MS


@requires_node
def test_kill_switch_forces_real_clock(tmp_path):
    env = _agent_env(_anchor_file(tmp_path))
    env["WCB_DISABLE_AGENT_CLOCK_SIM"] = "1"
    assert abs(_observed_now_ms(env) - time.time() * 1000) < TOLERANCE_MS


@requires_node
def test_date_surface_is_preserved(tmp_path):
    script = (
        "process.stdout.write(JSON.stringify({"
        "inst: new Date() instanceof Date,"
        "explicit: new Date(0).toISOString(),"
        "parse: Date.parse('2026-01-01T00:00:00Z'),"
        "utc: typeof Date.UTC}))"
    )
    out, _ = _run_node(_agent_env(_anchor_file(tmp_path)), script=script)
    seen = json.loads(out)
    assert seen["inst"] is True
    assert seen["explicit"] == "1970-01-01T00:00:00.000Z"
    assert seen["parse"] == 1767225600000
    assert seen["utc"] == "function"


@requires_node
def test_debug_flag_does_not_abort_preload(tmp_path):
    env = _agent_env(_anchor_file(tmp_path))
    env["WCB_FAKE_CLOCK_DEBUG"] = "1"
    out, err = _run_node(env, script="process.stdout.write('ok')")
    assert out == "ok"
    assert "[wcb-clock-shim] active" in err


# --------------------------------------------------------------------------
# D. Harness wiring: the anchor + preload must reach a scrubbed environment.
# --------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, returncode=0, stdout="abc123\n", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def capture_run(monkeypatch):
    """Capture every subprocess.run argv list without spawning anything."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _import_docker_utils_fresh():
    if "src.utils.docker_utils" in sys.modules:
        del sys.modules["src.utils.docker_utils"]
    from src.utils import docker_utils

    return docker_utils


def _sim_start(tmp_path, du):
    ws = tmp_path / "ws"
    ws.mkdir()
    du.start_container("task-clock", str(ws), sim_clock_epoch_ms=ANCHOR_MS,
                       sim_tz="America/Chicago")


def test_start_container_seeds_anchor_and_cron_env(tmp_path, monkeypatch, capture_run):
    monkeypatch.setenv("DOCKER_IMAGE", "wildclawbench-ubuntu:v1.3")
    du = _import_docker_utils_fresh()
    _sim_start(tmp_path, du)

    run_cmd = capture_run[0]
    assert run_cmd[0:3] == ["docker", "run", "-d"]
    assert f"NODE_OPTIONS=--require /opt/wcb/faketime_shim.js" in run_cmd

    seeds = [c for c in capture_run if c[0:2] == ["docker", "exec"]]
    assert seeds, "container start never seeded the sim-clock anchor"
    script = seeds[0][-1]
    # Anchor file exists from turn 0, so a cron job in turn 0 is covered too.
    assert f"> {du.AGENT_SIM_CLOCK_FILE}" in script
    assert str(ANCHOR_MS) in script
    # cron reads /etc/environment; without this the shim is never preloaded.
    assert "/etc/environment" in script
    assert "NODE_OPTIONS=--require /opt/wcb/faketime_shim.js" in script
    assert f"WCB_FAKE_CLOCK_FILE={du.AGENT_SIM_CLOCK_FILE}" in script


def test_start_container_without_sim_clock_seeds_nothing(tmp_path, monkeypatch,
                                                         capture_run):
    monkeypatch.setenv("DOCKER_IMAGE", "wildclawbench-ubuntu:v1.3")
    du = _import_docker_utils_fresh()
    ws = tmp_path / "ws"
    ws.mkdir()
    du.start_container("task-noclock", str(ws))

    joined = " ".join(" ".join(c) for c in capture_run)
    assert "/etc/environment" not in joined
    assert "clock_epoch" not in joined


def test_set_agent_sim_clock_rewrites_the_anchor_file(monkeypatch, capture_run):
    du = _import_docker_utils_fresh()
    monkeypatch.setattr(du, "_container_running", lambda _tid: True)
    assert du.set_agent_sim_clock("task-clock", ANCHOR_MS) is True

    script = capture_run[-1][-1]
    assert f"> {du.AGENT_SIM_CLOCK_FILE}" in script
    assert str(ANCHOR_MS) in script


def test_seed_agent_sim_clock_survives_a_failed_write(monkeypatch):
    du = _import_docker_utils_fresh()
    monkeypatch.setattr(
        du.subprocess, "run",
        lambda *a, **k: _FakeCompleted(returncode=1, stderr="no such container"))
    assert du.seed_agent_sim_clock("task-clock", ANCHOR_MS, "--require x") is False
