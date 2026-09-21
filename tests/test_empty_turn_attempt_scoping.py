"""Attempt-scoped empty-turn detection (L1) and the bounded settle (L2).

The empty check reads the sidecar's usage rows the instant the agent process
exits, but those rows are appended ASYNCHRONOUSLY, after the request completes
and off the agent's critical path (koji: a row landed 3 MINUTES behind its
request). An unscoped "did the count move?" predicate therefore has three
holes, one per direction of that skew:

  A. the row for a healthy turn has not landed yet  -> live turn read as empty,
     burning its retry (and, twice in a row, aborting a live run);
  B. a LATE row belonging to an EARLIER turn lands inside this turn's window
     -> a dead turn reads as alive (pablo_carroll: 7 dead turns in 21s shipped
     as 18/18);
  C. the retry attempt is compared against the baseline taken before attempt 0,
     so a late attempt-0 row vouches for attempt 1 -> the empty-twice abort
     never fires, which is the one guard standing between a dead route and a
     full laddered run.

L1 closes B and C by counting only rows whose request STARTED at or after the
attempt began, reconstructed from fields the writer already emits
(ts - duration_s); L2 closes A by re-reading for a bounded settle window before
declaring the turn empty. Both are fail-open: a row that cannot be placed in
time is COUNTED, so no parse failure can invent an abort.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.agents.openclaw.runner as ocr  # noqa: E402
from src.agents.openclaw import OpenClawAgent  # noqa: E402
from test_multiturn_runner import (  # noqa: E402
    _FakeProc,
    _FakeSession,
    _ScriptedSource,
    _agent,
    _neutralize,
    _spec,
    _turn_outcomes,
)
from test_usage_callbacks import EXPECTED_KEYS  # noqa: E402

KEY = "wcb::task-mt-1::abc123def456"
OTHER_KEY = "wcb::task-other::999888"
BASE = 5000.0


def _row(run_key=KEY, *, started=BASE, duration=2.0, kind="agent", **overrides):
    """A usage row shaped exactly like the sidecar writer's.

    `started` is the request's START on the host clock; the writer stamps `ts`
    at write time (~end_time) and `duration_s` = end - start, so the fixture
    derives ts from started + duration rather than storing a start field. That
    asymmetry IS the thing under test: nothing in the schema records a start.
    """
    end = datetime.fromtimestamp(started + duration, timezone.utc)
    row = {
        "ts": end.isoformat(),
        "model": "claude-opus-4.7",
        "kind": kind,
        "run_key": run_key,
        "input_tokens": 1200,
        "output_tokens": 45,
        "total_tokens": 1245,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "audio_seconds": 0.0,
        "cost_usd": 0.0123,
        "duration_s": round(duration, 3),
    }
    row.update(overrides)
    return row


def _write(path, *rows):
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _bare_agent(tmp_path, *, create_log=True):
    agent = OpenClawAgent.__new__(OpenClawAgent)
    agent.litellm_usage_log = str(tmp_path / "usage.jsonl")
    if create_log:
        Path(agent.litellm_usage_log).touch()
    return agent


class _Clock:
    """Manually advanced stand-in for time.time()/time.sleep()."""

    def __init__(self, now=BASE, on_sleep=None):
        self.now = now
        self.sleeps = 0
        self._on_sleep = on_sleep

    def time(self):
        return self.now

    def sleep(self, seconds=0.0):
        self.sleeps += 1
        self.now += seconds
        if self._on_sleep is not None:
            self._on_sleep(self)

    def install(self, monkeypatch):
        monkeypatch.setattr(ocr.time, "time", self.time)
        monkeypatch.setattr(ocr.time, "sleep", self.sleep)
        return self


# --- the row fixture is the real schema, not an approximation ---------------


def test_fixture_row_matches_the_writer_schema():
    """If the writer's schema ever drifts, these fixtures must drift with it —
    otherwise this module proves things about rows that no longer exist."""
    assert set(_row().keys()) == EXPECTED_KEYS | {"run_key"}


# --- L1: request-start reconstruction --------------------------------------


class TestRowStartedEpoch:
    def test_reconstructs_start_from_ts_minus_duration(self):
        started = OpenClawAgent._row_started_epoch(_row(started=BASE, duration=7.5))
        assert started == pytest.approx(BASE, abs=1e-3)

    @pytest.mark.parametrize("row", [
        _row(ts="not-a-timestamp"),
        _row(ts=""),
        {k: v for k, v in _row().items() if k != "ts"},
        {k: v for k, v in _row().items() if k != "duration_s"},
        _row(duration_s="fifteen"),
        _row(duration_s=None),
        _row(ts=datetime.utcnow().isoformat()),          # naive: no zone to trust
    ])
    def test_unplaceable_rows_report_none(self, row):
        assert OpenClawAgent._row_started_epoch(row) is None

    def test_tolerates_z_suffix(self):
        row = _row(ts="2026-09-21T12:00:10Z", duration_s=10.0)
        expected = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        assert OpenClawAgent._row_started_epoch(row) == pytest.approx(expected)


# --- L1: since_epoch filtering ---------------------------------------------


class TestSinceEpochCounting:
    def test_none_is_identical_to_the_legacy_count(self, tmp_path):
        """since_epoch=None is what the stall guard and the partial-turn check
        call; it must stay byte-for-byte the pre-existing behaviour."""
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log,
               _row(started=BASE - 900),                       # ancient success
               _row(started=BASE + 10),                        # recent success
               _row(started=BASE + 20, kind="preflight"),      # probe
               _row(started=BASE + 30, kind="failure"),        # 400-storm row
               _row(OTHER_KEY, started=BASE + 40),             # another run
               _row(started=BASE + 50, ts="garbage"),          # unplaceable
               {k: v for k, v in _row(started=BASE + 60).items() if k != "duration_s"})
        with open(agent.litellm_usage_log, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")

        raw = Path(agent.litellm_usage_log).read_text(encoding="utf-8")
        assert agent._count_run_key_rows(KEY) == raw.count(KEY)
        assert agent._count_run_key_rows(KEY, successes_only=True) == sum(
            1 for line in raw.splitlines()
            if KEY in line and _kind_of(line) == "agent")
        assert agent._count_run_key_rows(KEY) == 6, "every KEY row, any kind"
        assert agent._count_run_key_rows(KEY, successes_only=True) == 4, (
            "two ordinary successes plus the two unplaceable rows; the "
            "preflight probe, the failure row and the other run stay out")

    def test_rows_that_started_earlier_are_excluded(self, tmp_path):
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log,
               _row(started=BASE - 60, duration=5.0),
               _row(started=BASE + 5, duration=5.0))
        assert agent._count_run_key_rows(KEY, successes_only=True) == 2
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 1

    def test_a_row_still_running_when_the_attempt_began_is_excluded(self, tmp_path):
        """Hole B in miniature: the row LANDS after the attempt starts (ts is
        later) but the request it describes STARTED before it — it belongs to
        the previous turn and must not vouch for this one."""
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log, _row(started=BASE - 30, duration=120.0))
        assert agent._count_run_key_rows(KEY, successes_only=True) == 1
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 0

    def test_boundary_row_started_exactly_at_the_attempt_counts(self, tmp_path):
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log, _row(started=BASE, duration=3.0))
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 1

    @pytest.mark.parametrize("row,why", [
        (_row(started=BASE - 600, ts="not-a-timestamp"), "unparseable ts"),
        ({k: v for k, v in _row(started=BASE - 600).items() if k != "ts"},
         "missing ts"),
        ({k: v for k, v in _row(started=BASE - 600).items() if k != "duration_s"},
         "missing duration_s"),
        (_row(started=BASE - 600, duration_s="fifteen"), "non-numeric duration_s"),
        (_row(started=BASE - 600, ts=datetime.utcnow().isoformat()), "naive ts"),
    ])
    def test_unplaceable_rows_fail_open_and_count(self, tmp_path, row, why):
        """Fail open: counting can only make a turn look ALIVE, so a parse
        failure degrades to today's behaviour instead of inventing an abort."""
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log, row)
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 1, why

    def test_failure_and_preflight_rows_still_never_count(self, tmp_path):
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log,
               _row(started=BASE + 1, kind="failure"),
               _row(started=BASE + 2, kind="preflight"))
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 0

    def test_missing_log_returns_zero(self, tmp_path):
        agent = _bare_agent(tmp_path, create_log=False)
        assert agent._count_run_key_rows(
            KEY, successes_only=True, since_epoch=BASE) == 0


def _kind_of(line):
    try:
        return json.loads(line).get("kind")
    except ValueError:
        return None


# --- L2: the bounded settle -------------------------------------------------


class TestSettle:
    def test_row_landing_on_the_third_poll_saves_the_turn(
            self, tmp_path, monkeypatch, caplog):
        agent = _bare_agent(tmp_path)
        log = agent.litellm_usage_log

        def _land_late(clock):
            if clock.sleeps == 3:
                _write(log, _row(started=BASE + 1, duration=0.5))

        clock = _Clock(on_sleep=_land_late).install(monkeypatch)

        with caplog.at_level(logging.INFO):
            n = agent._settled_attempt_success_rows(KEY, BASE, 0, "task-mt-1", 4)

        assert n == 1, "the late row must rescue the turn"
        assert clock.sleeps == 3, "polling must stop as soon as a row lands"
        assert any("looked EMPTY at process exit" in r.message
                   and r.levelno == logging.INFO for r in caplog.records)

    def test_settle_expiry_declares_empty_and_warns(
            self, tmp_path, monkeypatch, caplog):
        agent = _bare_agent(tmp_path)
        clock = _Clock().install(monkeypatch)

        with caplog.at_level(logging.INFO):
            n = agent._settled_attempt_success_rows(KEY, BASE, 0, "task-mt-1", 4)

        assert n == 0
        assert clock.now == pytest.approx(BASE + 15.0), "spent its whole budget"
        assert any("still shows no successful LLM traffic" in r.message
                   and r.levelno == logging.WARNING for r in caplog.records)

    def test_a_healthy_turn_never_polls(self, tmp_path, monkeypatch):
        agent = _bare_agent(tmp_path)
        _write(agent.litellm_usage_log, _row(started=BASE + 1))
        clock = _Clock().install(monkeypatch)

        assert agent._settled_attempt_success_rows(
            KEY, BASE, 0, "task-mt-1", 4) == 1
        assert clock.sleeps == 0, "rows already on disk must cost zero latency"

    def test_rows_from_before_the_attempt_do_not_end_the_settle(
            self, tmp_path, monkeypatch):
        """L1 and L2 compose: a late row from an earlier turn landing mid-settle
        must not be mistaken for this attempt's traffic."""
        agent = _bare_agent(tmp_path)
        log = agent.litellm_usage_log

        def _land_stale(clock):
            if clock.sleeps == 2:
                _write(log, _row(started=BASE - 120, duration=1.0))

        clock = _Clock(on_sleep=_land_stale).install(monkeypatch)

        assert agent._settled_attempt_success_rows(
            KEY, BASE, 0, "task-mt-1", 4) == 0
        assert clock.sleeps == 30, "a stale row must not short-circuit the wait"

    def test_zero_disables_polling_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WCB_EMPTY_SETTLE_SECONDS", "0")
        agent = _bare_agent(tmp_path)
        clock = _Clock().install(monkeypatch)

        assert agent._settled_attempt_success_rows(
            KEY, BASE, 0, "task-mt-1", 4) == 0
        assert clock.sleeps == 0
        assert clock.now == BASE

    def test_missing_log_skips_the_wait(self, tmp_path, monkeypatch):
        """Nothing has ever been written for this run, so nothing can be in
        flight; polling would only add latency to an unchangeable verdict."""
        agent = _bare_agent(tmp_path, create_log=False)
        clock = _Clock().install(monkeypatch)

        assert agent._settled_attempt_success_rows(
            KEY, BASE, 0, "task-mt-1", 4) == 0
        assert clock.sleeps == 0

    def test_terminates_under_a_frozen_clock(self, tmp_path, monkeypatch):
        """The deadline alone cannot bound the loop: the runner tests freeze
        time.time() and no-op time.sleep(), which would spin forever. The
        iteration cap is what guarantees termination."""
        agent = _bare_agent(tmp_path)
        sleeps = []
        monkeypatch.setattr(ocr.time, "time", lambda: BASE)
        monkeypatch.setattr(ocr.time, "sleep", lambda *a, **k: sleeps.append(1))

        assert agent._settled_attempt_success_rows(
            KEY, BASE, 0, "task-mt-1", 4) == 0
        assert len(sleeps) == 30, "15s / 0.5s poll, capped by iterations"

    def test_retry_attempt_waits_longer_than_the_first(self):
        assert OpenClawAgent._empty_settle_seconds(0) == 15.0
        assert OpenClawAgent._empty_settle_seconds(1) == 60.0

    @pytest.mark.parametrize("attempt,var", [
        (0, "WCB_EMPTY_SETTLE_SECONDS"),
        (1, "WCB_EMPTY_SETTLE_SECONDS_RETRY"),
    ])
    def test_settle_budgets_are_tunable(self, monkeypatch, attempt, var):
        monkeypatch.setenv(var, "3.5")
        assert OpenClawAgent._empty_settle_seconds(attempt) == 3.5

    def test_garbage_budget_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("WCB_EMPTY_SETTLE_SECONDS", "soon")
        assert OpenClawAgent._empty_settle_seconds(0) == 15.0


# --- L1 end to end, through run_task ---------------------------------------


def _drive(monkeypatch, tmp_path, messages, on_send, *, outcomes=None,
           session_lines=5):
    """run_task with a REAL usage log that on_send(send_index, agent, clock)
    appends to, standing in for the sidecar writing rows as turns run."""
    events, cmds = [], []
    session = _FakeSession(lines=session_lines)
    _neutralize(monkeypatch, [], events, cmds, session)
    monkeypatch.setattr(ocr.subprocess, "run", session)
    monkeypatch.setenv("WCB_EMPTY_SETTLE_SECONDS", "0")
    monkeypatch.setenv("WCB_EMPTY_SETTLE_SECONDS_RETRY", "0")

    agent = _agent(monkeypatch, guarded=True)
    usage_log = tmp_path / "usage.jsonl"
    usage_log.touch()
    agent.litellm_usage_log = str(usage_log)
    clock = _Clock().install(monkeypatch)
    starts = []

    def fake_run_background(task_id, bash_cmd=None, log_path=None, **k):
        if bash_cmd and "openclaw agent" in bash_cmd:
            i = len(cmds)
            cmds.append(bash_cmd)
            session.lines += 1
            starts.append(clock.now)
            on_send(i, agent, clock)
            clock.now += 30.0            # the attempt burns wall clock
        return _FakeProc()

    monkeypatch.setattr(ocr, "run_background", fake_run_background)
    _turn_outcomes(monkeypatch, agent, outcomes or ["ok"] * 8)
    spec = _spec(tmp_path, turn_source=_ScriptedSource(messages, events))

    result = agent.run_task(spec)
    return result, cmds, starts, agent


def test_hole_c_late_attempt_0_row_cannot_vouch_for_the_retry(
        monkeypatch, tmp_path):
    """A row from attempt 0 that lands DURING attempt 1 used to make the dead
    retry read as alive, disarming the empty-twice abort: the run then laddered
    to schedule end on a dead route."""
    key_box = {}

    def on_send(i, agent, clock):
        key_box["key"] = agent._run_keys["task-mt-1"]
        if i == 1:
            # Attempt 0's row, landing late — it STARTED inside attempt 0.
            _write(agent.litellm_usage_log,
                   _row(key_box["key"], started=clock.now - 25.0, duration=1.0))

    result, cmds, starts, agent = _drive(
        monkeypatch, tmp_path, ["m0"], on_send)

    assert len(cmds) == 2, "the turn is sent once, then retried once"
    assert starts[1] > starts[0]
    assert result.turns_empty == [0, 0], "both attempts must read empty"
    assert result.turns_completed == 0
    assert result.timed_out_turn is None, "an empty abort is not a timeout"
    # Control: the pre-L1 predicate saw this row and called the retry alive.
    assert agent._count_run_key_rows(key_box["key"], successes_only=True) == 1


def test_hole_b_late_row_from_an_earlier_turn_cannot_revive_a_dead_turn(
        monkeypatch, tmp_path):
    """pablo_carroll shipped 18/18 with 7 dead turns in 21 seconds: each dead
    turn was credited with a straggler row from a turn that had really run."""
    key_box, turn0 = {}, {}

    def on_send(i, agent, clock):
        key = key_box.setdefault("key", agent._run_keys["task-mt-1"])
        if i == 0:
            turn0["start"] = clock.now
            _write(agent.litellm_usage_log,
                   _row(key, started=clock.now + 1.0, duration=2.0))
        if i == 1:
            # Turn 0's SECOND request, landing during turn 1.
            _write(agent.litellm_usage_log,
                   _row(key, started=turn0["start"] + 3.0, duration=1.0))

    result, cmds, starts, agent = _drive(
        monkeypatch, tmp_path, ["m0", "m1"], on_send)

    assert result.turns_completed == 1, "turn 0 was real; turn 1 was not"
    assert result.turns_empty == [1, 1]
    assert result.turns_duplicated == [1]
    assert len(cmds) == 3, "m0, then m1 twice"
    # Control: unscoped, turn 1 'gained' a row and would have passed.
    assert agent._count_run_key_rows(key_box["key"], successes_only=True) == 2


def test_a_turn_with_its_own_row_is_never_retried(monkeypatch, tmp_path):
    """The healthy path must not regress: rows started inside the attempt
    count, and the turn proceeds without a re-send."""
    def on_send(i, agent, clock):
        _write(agent.litellm_usage_log,
               _row(agent._run_keys["task-mt-1"],
                    started=clock.now + 0.5, duration=4.0))

    result, cmds, starts, agent = _drive(
        monkeypatch, tmp_path, ["m0", "m1"], on_send)

    assert result.turns_completed == 2
    assert result.turns_empty == []
    assert result.turns_duplicated == []
    assert len(cmds) == 2


def test_empty_first_attempt_recovers_when_the_retry_produces_its_own_row(
        monkeypatch, tmp_path):
    """The retry contract itself: one empty attempt costs a re-send and a
    session rollback, not the run."""
    def on_send(i, agent, clock):
        if i == 1:
            _write(agent.litellm_usage_log,
                   _row(agent._run_keys["task-mt-1"],
                        started=clock.now + 0.5, duration=2.0))

    result, cmds, starts, agent = _drive(
        monkeypatch, tmp_path, ["m0"], on_send)

    assert len(cmds) == 2
    assert result.turns_empty == [0]
    assert result.turns_duplicated == [0]
    assert result.turns_completed == 1
