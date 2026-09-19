"""Runner-level tests for the pull-based turn loop (P1).

Reuses the run_task fake harness pattern from tests/test_openclaw_runner_units.py
(every docker helper monkeypatched on the runner namespace; run_background
returns scripted fake procs — gateway first, then one per agent turn).

Asserts the verified invariants from docs/MULTITURN_IMPLEMENTATION_PLAN.md:
pull ordering (source pull → before_turn → marker → send; mutation-in-pull
precedes the send), timeout drops remaining turns WITHOUT pre-pulling (so a
never-run stage never mutates the environment), turn accounting on
AgentExecution, single-turn adapter parity (no per-turn log lines), and the
open-ended (total=None) display path.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.agents.openclaw.runner as ocr  # noqa: E402
from src.agents.base import AgentTaskSpec  # noqa: E402
from src.agents.openclaw import OpenClawAgent  # noqa: E402


class _FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class _TimeoutProc(_FakeProc):
    def wait(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="openclaw agent", timeout=timeout or 0)
        return -9


class _ScriptedSource:
    """Recording TurnSource with a scripted message list."""

    def __init__(self, messages, events, open_ended=False, raise_at=None):
        self._messages = list(messages)
        self._events = events
        self._open_ended = open_ended
        self._raise_at = raise_at

    def next_message(self, turn_index):
        if self._raise_at is not None and turn_index == self._raise_at:
            self._events.append(("pull_raise", turn_index))
            raise RuntimeError("source failure")
        self._events.append(("pull", turn_index))
        if turn_index < len(self._messages):
            self._events.append(("mutate", turn_index))  # ClawMark: mutation in pull
            return self._messages[turn_index]
        return None

    def total(self):
        return None if self._open_ended else len(self._messages)

    def source_name(self):
        return "scripted"


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSession:
    """Stands in for the container's chat.jsonl under the session-restore
    guard: answers the `awk END{print NR}` row probe and applies `head -n N`.
    Every other docker exec (connection kill, process kill) reports failure,
    which those best-effort call sites already tolerate."""

    def __init__(self, lines=0, exists=True):
        self.lines = lines
        self.exists = exists
        self.probes = 0
        self.truncated_to: list[int] = []

    def __call__(self, cmd, *a, **k):
        script = cmd[-1] if isinstance(cmd, (list, tuple)) and cmd else ""
        if "awk" in script:
            self.probes += 1
            return _Completed(0, f"{self.lines if self.exists else 0}\n")
        if "head -n" in script:
            n = int(script.split("head -n ")[1].split()[0])
            self.truncated_to.append(n)
            self.lines = min(self.lines, n)
            return _Completed(0, "")
        return _Completed(1, "", "stubbed")


def _neutralize(monkeypatch, procs, events, cmds, session=None):
    for name in (
        "start_container", "inject_lobster_workspace", "inject_data_into_workspace",
        "inject_persona_into_workspace", "inject_openclaw_models",
        "inject_api_connectors", "run_warmup", "setup_skills",
        "setup_workspace", "snapshot_workspace_state",
    ):
        monkeypatch.setattr(ocr, name, lambda *a, **k: None)
    it = iter(procs)

    def fake_run_background(task_id, bash_cmd=None, log_path=None, **k):
        if bash_cmd and "openclaw agent" in bash_cmd:
            events.append(("send", len(cmds)))
            cmds.append(bash_cmd)
            if session is not None:
                # openclaw persists the user message as the turn starts —
                # the row an aborted attempt would orphan.
                session.lines += 1
        return next(it)

    monkeypatch.setattr(ocr, "run_background", fake_run_background)
    monkeypatch.setattr(
        ocr, "write_turn_marker",
        lambda task_id, i: events.append(("marker", i)))
    monkeypatch.setattr(ocr.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ocr.time, "perf_counter", lambda: 1000.0)
    monkeypatch.setattr(ocr.time, "time", lambda: 5000.0)


def _agent(monkeypatch, guarded=False):
    a = OpenClawAgent(gateway_port=8080, image_model="")
    for m in ("_set_bootstrap_limits", "_index_memory", "_set_model",
              "_inject_auth", "_set_image_model"):
        monkeypatch.setattr(a, m, lambda *x, **k: None)
    monkeypatch.setattr(a, "_wait_for_llm_route_ready", lambda *x, **k: True)
    if guarded:
        # Both retry paths (stall and empty) require run-key tagging.
        a.litellm_usage_log = "/tmp/wcb-test-usage.jsonl"
        monkeypatch.setattr(a, "_run_key_bearer_live", lambda *x, **k: True)
    return a


def _spec(tmp_path, **overrides):
    ws = tmp_path / "ws"; ws.mkdir(exist_ok=True)
    out = tmp_path / "out"; out.mkdir(exist_ok=True)
    base = dict(
        task_id="task-mt-1", task={}, workspace_path=str(ws),
        prompt="the single prompt", timeout_seconds=30, output_dir=out,
        model="claude-opus-4.7", thinking=None, models_config=None, lobster=None,
    )
    base.update(overrides)
    return AgentTaskSpec(**base)


def test_multi_turn_source_drives_n_sends_in_order(monkeypatch, tmp_path):
    events, cmds = [], []
    src = _ScriptedSource(["m0", "m1", "m2"], events)
    before = []
    _neutralize(monkeypatch, [_FakeProc()] * 4, events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=src,
                 before_turn=lambda i: events.append(("before", i)) or before.append(i))

    result = a.run_task(spec)

    assert result.error is None
    assert result.turns_completed == 3
    assert result.timed_out_turn is None
    assert len(cmds) == 3
    assert "--message 'm0'" in cmds[0]
    assert "--message 'm2'" in cmds[2]
    assert "--session-id chat" in cmds[0]  # same session every turn
    # ordering: pull(i)+mutate(i) precede send(i); before_turn only for i>=1,
    # after the pull and before the send; final exhausted pull(3) happens.
    assert events == [
        ("pull", 0), ("mutate", 0), ("send", 0),
        ("pull", 1), ("mutate", 1), ("before", 1), ("send", 1),
        ("pull", 2), ("mutate", 2), ("before", 2), ("send", 2),
        ("pull", 3),
    ]
    assert before == [1, 2]


def test_timeout_drops_remaining_turns_without_prepull(monkeypatch, tmp_path):
    events, cmds = [], []
    src = _ScriptedSource(["m0", "m1", "m2"], events)
    _neutralize(monkeypatch, [_FakeProc(), _FakeProc(), _TimeoutProc()],
                events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=src)

    result = a.run_task(spec)

    assert result.error is None
    assert result.turns_completed == 1     # turn 0 finished; turn 1 timed out
    assert result.timed_out_turn == 1
    assert result.elapsed_time == float(spec.timeout_seconds)  # budget contract
    assert len(cmds) == 2                  # turn 2 never sent...
    pulls = [e for e in events if e[0] == "pull"]
    assert pulls == [("pull", 0), ("pull", 1)]  # ...and never pulled/mutated


def test_single_turn_parity_no_per_turn_lines(monkeypatch, tmp_path, caplog):
    events, cmds = [], []
    _neutralize(monkeypatch, [_FakeProc(), _FakeProc()], events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path)  # no turns, no turn_source

    with caplog.at_level(logging.INFO):
        result = a.run_task(spec)

    assert result.error is None
    assert result.turns_completed == 1
    assert result.timed_out_turn is None
    assert len(cmds) == 1
    assert "--message 'the single prompt'" in cmds[0]
    # single-turn stays silent about turns (legacy len>1 gate preserved)
    assert not any("Agent turn" in r.message and "starting" in r.message
                   for r in caplog.records)


def test_static_turns_tuple_still_honored(monkeypatch, tmp_path, caplog):
    events, cmds = [], []
    _neutralize(monkeypatch, [_FakeProc()] + [_FakeProc()] * 2, events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turns=("t0", "t1"))

    with caplog.at_level(logging.INFO):
        result = a.run_task(spec)

    assert result.turns_completed == 2
    assert len(cmds) == 2
    # known total renders the legacy N/M line byte-shape
    assert any("Agent turn 1/2 starting" in r.message for r in caplog.records)


def test_open_ended_source_renders_without_denominator(monkeypatch, tmp_path, caplog):
    events, cmds = [], []
    src = _ScriptedSource(["m0"], events, open_ended=True)
    _neutralize(monkeypatch, [_FakeProc(), _FakeProc()], events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=src)

    with caplog.at_level(logging.INFO):
        result = a.run_task(spec)

    assert result.turns_completed == 1
    assert any(r.message.endswith("Agent turn %d starting") or
               "Agent turn 1 starting" in r.getMessage()
               for r in caplog.records)
    assert not any("1/1" in r.getMessage() for r in caplog.records)


def test_source_exception_ends_run_gracefully(monkeypatch, tmp_path):
    events, cmds = [], []
    src = _ScriptedSource(["m0", "m1"], events, raise_at=1)
    _neutralize(monkeypatch, [_FakeProc(), _FakeProc()], events, cmds)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=src)

    result = a.run_task(spec)

    assert result.error is None            # run completes; scoring judges output
    assert result.turns_completed == 1
    assert len(cmds) == 1
    assert ("pull_raise", 1) in events


def test_agent_log_aggregates_across_turns(monkeypatch, tmp_path):
    """Per-turn relaunches must append to agent.log, not truncate it.

    Regression for the ops-reported bug where a 5-turn run's agent.log held
    only the last turn: run_background opened the log with mode "w" on every
    turn. The turn loop now passes append=turn_index > 0 (turn 0 truncates so
    a retried run_N starts clean; later turns append). This fake honors the
    append flag by actually writing to log_path in the requested mode.
    """
    events, cmds, append_flags = [], [], []
    src = _ScriptedSource(["m0", "m1", "m2"], events)
    for name in (
        "start_container", "inject_lobster_workspace", "inject_data_into_workspace",
        "inject_persona_into_workspace", "inject_openclaw_models",
        "inject_api_connectors", "run_warmup", "setup_skills",
        "setup_workspace", "snapshot_workspace_state",
    ):
        monkeypatch.setattr(ocr, name, lambda *a, **k: None)

    def fake_run_background(task_id, bash_cmd=None, log_path=None, *, append=False, **k):
        if bash_cmd and "openclaw agent" in bash_cmd:
            n = len(cmds)
            cmds.append(bash_cmd)
            append_flags.append(append)
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a" if append else "w", encoding="utf-8") as fh:
                fh.write(f"turn-{n} output\n")
        return _FakeProc()

    monkeypatch.setattr(ocr, "run_background", fake_run_background)
    monkeypatch.setattr(ocr, "write_turn_marker", lambda *a, **k: None)
    monkeypatch.setattr(ocr.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ocr.time, "perf_counter", lambda: 1000.0)
    monkeypatch.setattr(ocr.time, "time", lambda: 5000.0)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=src)

    # Stale content from a previous attempt in the same run_N dir: turn 0's
    # truncating open must wipe it.
    agent_log = spec.output_dir / "agent.log"
    agent_log.write_text("stale content from previous attempt\n", encoding="utf-8")

    result = a.run_task(spec)

    assert result.error is None
    assert result.turns_completed == 3
    assert append_flags == [False, True, True]
    text = agent_log.read_text(encoding="utf-8")
    assert "stale content" not in text
    assert text == "turn-0 output\nturn-1 output\nturn-2 output\n"


# --- session-restore guard (duplicate user turns) --------------------------
#
# A retry re-sends a turn the session already stored, so without the rollback
# chat.jsonl ends up with two identical user rows (T14/T16/T19 x2 across six
# shipped runs), shifting the judge's turn count and the feedback anchor.


def _turn_outcomes(monkeypatch, agent, outcomes):
    it = iter(outcomes)
    monkeypatch.setattr(agent, "_turn_wait_outcome", lambda *a, **k: next(it))


def test_stall_retry_restores_session_before_resend(monkeypatch, tmp_path):
    events, cmds = [], []
    session = _FakeSession(lines=5)
    _neutralize(monkeypatch, [_FakeProc() for _ in range(4)], events, cmds, session)
    monkeypatch.setattr(ocr.subprocess, "run", session)
    a = _agent(monkeypatch, guarded=True)
    _turn_outcomes(monkeypatch, a, ["stalled", "ok"])
    rows = iter(range(100))
    monkeypatch.setattr(a, "_count_run_key_rows", lambda *x, **k: next(rows))
    spec = _spec(tmp_path, turn_source=_ScriptedSource(["m0"], events))

    result = a.run_task(spec)

    assert result.error is None
    assert len(cmds) == 2                    # same turn sent twice
    assert session.truncated_to == [5]       # rolled back to the pre-attempt state
    assert session.lines == 6                # exactly ONE user row for the turn
    assert result.turns_duplicated == [0]
    assert result.turns_completed == 1


def test_empty_turn_retry_restores_session(monkeypatch, tmp_path):
    monkeypatch.delenv("WCB_EMPTY_TURN_LIMIT", raising=False)
    events, cmds = [], []
    session = _FakeSession(lines=5)
    _neutralize(monkeypatch, [_FakeProc() for _ in range(4)], events, cmds, session)
    monkeypatch.setattr(ocr.subprocess, "run", session)
    a = _agent(monkeypatch, guarded=True)
    _turn_outcomes(monkeypatch, a, ["ok", "ok"])
    # First attempt produces no successful row; the retry does. Keyed on the
    # number of attempts sent so HarnessV2's empty-turn grace polling (extra
    # reads of the count) cannot shift the sequence.
    def _rows(run_key, successes_only=False):
        return max(0, len(cmds) - 1) if successes_only else 0

    monkeypatch.setattr(a, "_count_run_key_rows", _rows)
    spec = _spec(tmp_path, turn_source=_ScriptedSource(["m0"], events))

    result = a.run_task(spec)

    assert len(cmds) == 2
    assert session.truncated_to == [5]
    assert session.lines == 6
    assert result.turns_empty == [0]
    assert result.turns_duplicated == [0]


def test_no_restore_when_attempt_left_no_orphan_row(monkeypatch, tmp_path):
    """Not every retry duplicates: one observed run stalled twice and still
    recorded 18/18 user turns (the retry ran embedded after a gateway 1008).
    The guard inspects the live count and leaves such a session untouched."""
    events, cmds = [], []
    session = _FakeSession(lines=5)
    # session=None -> sends do NOT append a row, i.e. the aborted attempt
    # never got as far as persisting the user message.
    _neutralize(monkeypatch, [_FakeProc() for _ in range(4)], events, cmds)
    monkeypatch.setattr(ocr.subprocess, "run", session)
    a = _agent(monkeypatch, guarded=True)
    _turn_outcomes(monkeypatch, a, ["stalled", "ok"])
    rows = iter(range(100))
    monkeypatch.setattr(a, "_count_run_key_rows", lambda *x, **k: next(rows))
    spec = _spec(tmp_path, turn_source=_ScriptedSource(["m0"], events))

    result = a.run_task(spec)

    assert len(cmds) == 2
    assert session.truncated_to == []
    assert session.lines == 5
    assert result.turns_duplicated == [0]


def test_unguarded_run_never_probes_the_session(monkeypatch, tmp_path):
    # No run-key guards -> no retry is reachable -> the snapshot must not cost
    # a docker exec per turn.
    events, cmds = [], []
    session = _FakeSession(lines=5)
    _neutralize(monkeypatch, [_FakeProc() for _ in range(5)], events, cmds, session)
    monkeypatch.setattr(ocr.subprocess, "run", session)
    a = _agent(monkeypatch)
    spec = _spec(tmp_path, turn_source=_ScriptedSource(["m0", "m1", "m2"], events))

    result = a.run_task(spec)

    assert result.turns_completed == 3
    assert session.probes == 0
    assert session.truncated_to == []
