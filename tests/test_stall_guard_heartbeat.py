"""Turn-stall guard × liveness heartbeat (src/agents/openclaw/runner.py).

The guard used to read ONE signal: tagged rows the usage callback appends at
request COMPLETION. A request that streams happily for twenty minutes writes no
row for its whole duration, so healthy long calls were killed as wedges —
cite: alpha 2026-09-19, 3 healthy 1P reps killed at 600s+poll; koji's row
landed 3min AFTER the kill.

Invariants under test:
  OR-predicate    a fresh heartbeat resets the stall clock on its own, with
                  zero usage rows; rows still do too
  no regression   with no heartbeat file the verdict is bit-for-bit the old
                  row-count behaviour, and a STALE heartbeat is silence
  cadence         the wait timeout is WCB_STALL_POLL_SECONDS (default 90s),
                  and thresholds/floor/retry count are untouched
  key agreement   the names the two writers use are the names the reader
                  looks for — they live in standalone modules that cannot
                  import each other, so drift is only catchable here
  lane scoping    the shared OAuth bridge lane counts by default and can be
                  dropped with WCB_STALL_HEARTBEAT_SHARED_LANE=0
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RUN_KEY = "wcb::t1::" + "c" * 32


class _FakeProc:
    """Never exits within `waits_before_exit` polls; ignores the timeout so the
    loop's cadence can be asserted without real sleeping."""

    def __init__(self, waits_before_exit: int | None = None) -> None:
        self.calls = 0
        self.waits_before_exit = waits_before_exit
        self.timeouts: list[float] = []

    def wait(self, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.waits_before_exit is not None and self.calls > self.waits_before_exit:
            return 0
        raise subprocess.TimeoutExpired(cmd="agent", timeout=timeout or 0)


def _agent(tmp_path, run_key: str = RUN_KEY):
    from src.agents.openclaw.runner import OpenClawAgent
    a = OpenClawAgent(gateway_port=1, litellm_master_key="")
    a.litellm_usage_log = str(tmp_path / "usage.jsonl")
    (tmp_path / "usage.jsonl").write_text("")
    a._run_keys["t1"] = run_key
    return a


def _spec():
    from src.agents.base import AgentTaskSpec
    return AgentTaskSpec(task_id="t1", task={}, workspace_path="/tmp", prompt="p",
                         timeout_seconds=30, output_dir=Path("/tmp"), model="m")


@pytest.fixture()
def guarded(monkeypatch):
    """Run-key tagging live + a short, patched stall threshold."""
    from src.agents.openclaw import runner as ocr
    monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
    monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "1")
    monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
    monkeypatch.delenv("WCB_STALL_HEARTBEAT_SHARED_LANE", raising=False)
    monkeypatch.setattr(ocr.OpenClawAgent, "_STALL_FLOOR_S", 0.4)
    return ocr


def _beat(agent, name: str | None = None, at: float | None = None) -> Path:
    from src.agents.openclaw.runner import OpenClawAgent
    directory = Path(agent._heartbeat_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or OpenClawAgent._heartbeat_name(agent._run_keys["t1"]))
    path.touch()
    if at is not None:
        os.utime(path, (at, at))
    return path


# --------------------------------------------------------------- cadence


class TestPollCadence:
    def test_default_and_overrides(self, monkeypatch):
        from src.agents.openclaw.runner import OpenClawAgent
        monkeypatch.delenv("WCB_STALL_POLL_SECONDS", raising=False)
        assert OpenClawAgent._stall_poll_seconds() == 90.0
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "30")
        assert OpenClawAgent._stall_poll_seconds() == 30.0
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "0")
        assert OpenClawAgent._stall_poll_seconds() == 90.0
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "-5")
        assert OpenClawAgent._stall_poll_seconds() == 90.0
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "garbage")
        assert OpenClawAgent._stall_poll_seconds() == 90.0

    def test_wait_timeout_uses_the_configured_cadence(self, tmp_path, monkeypatch, guarded):
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "7")
        a = _agent(tmp_path)
        proc = _FakeProc(waits_before_exit=2)
        # Deadline far enough out that `remaining` never clamps the cadence.
        assert a._turn_wait_outcome(proc, _spec(), time.time() + 600) == "ok"
        assert proc.timeouts[0] == pytest.approx(7.0)

    def test_remaining_budget_still_clamps_the_last_poll(self, tmp_path, monkeypatch, guarded):
        monkeypatch.setenv("WCB_STALL_POLL_SECONDS", "90")
        a = _agent(tmp_path)
        proc = _FakeProc(waits_before_exit=2)
        assert a._turn_wait_outcome(proc, _spec(), time.time() + 3) == "ok"
        assert proc.timeouts[0] <= 3.0, "a poll must never outlive the turn deadline"


# ------------------------------------------------------------ OR-predicate


class TestHeartbeatPredicate:
    def test_fresh_heartbeat_prevents_stall_past_threshold(self, tmp_path, guarded):
        """The healthy-long-call case: ZERO usage rows for the whole wait, but
        chunks keep landing, so the clock keeps resetting."""
        a = _agent(tmp_path)
        beat = _beat(a)

        class _Streaming(_FakeProc):
            def wait(self, timeout=None):
                time.sleep(0.06)
                beat.touch()
                os.utime(beat, None)
                return super().wait(timeout)

        proc = _Streaming(waits_before_exit=12)   # ~0.72s > 0.4s threshold
        assert a._turn_wait_outcome(proc, _spec(), time.time() + 30) == "ok"
        assert Path(a.litellm_usage_log).read_text() == "", "no rows landed at all"

    def test_stale_heartbeat_and_no_rows_still_stalls(self, tmp_path, guarded):
        a = _agent(tmp_path)
        _beat(a, at=time.time() - 3600)
        assert a._turn_wait_outcome(_FakeProc(), _spec(), time.time() + 30) == "stalled"

    def test_no_heartbeat_file_is_the_old_behaviour(self, tmp_path, guarded):
        a = _agent(tmp_path)
        assert not Path(a._heartbeat_dir()).exists()
        assert a._heartbeat_mtime(RUN_KEY) == 0.0
        assert a._turn_wait_outcome(_FakeProc(), _spec(), time.time() + 30) == "stalled"

    def test_unmounted_heartbeat_dir_is_the_old_behaviour(self, tmp_path, guarded, monkeypatch):
        monkeypatch.setenv("WCB_HEARTBEAT_HOST_DIR", str(tmp_path / "never_mounted"))
        a = _agent(tmp_path)
        assert a._heartbeat_mtime(RUN_KEY) == 0.0
        assert a._turn_wait_outcome(_FakeProc(), _spec(), time.time() + 30) == "stalled"

    def test_rows_alone_still_reset_the_clock(self, tmp_path, guarded):
        a = _agent(tmp_path)
        log = Path(a.litellm_usage_log)

        class _Rows(_FakeProc):
            def wait(self, timeout=None):
                time.sleep(0.06)
                with open(log, "a") as fh:
                    fh.write('{"run_key": "%s"}\n' % RUN_KEY)
                return super().wait(timeout)

        assert a._turn_wait_outcome(_Rows(waits_before_exit=12), _spec(),
                                    time.time() + 30) == "ok"

    def test_heartbeat_that_stops_advancing_is_silence(self, tmp_path, guarded):
        """A file left behind by a request that DID stream and then wedged must
        not read as liveness forever — the check is strictly-greater mtime."""
        a = _agent(tmp_path)
        beat = _beat(a)

        class _StopsBeating(_FakeProc):
            def wait(self, timeout=None):
                time.sleep(0.05)
                if self.calls < 2:
                    os.utime(beat, None)
                return super().wait(timeout)

        assert a._turn_wait_outcome(_StopsBeating(), _spec(),
                                    time.time() + 30) == "stalled"

    def test_threshold_floor_and_retry_semantics_untouched(self, monkeypatch):
        from src.agents.openclaw.runner import OpenClawAgent
        assert OpenClawAgent._STALL_FLOOR_S == 600.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "120")
        assert OpenClawAgent._stall_seconds() == 600.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "0")
        assert OpenClawAgent._stall_seconds() == 0.0


# ------------------------------------------------------------ dir + lanes


class TestHeartbeatLocation:
    def test_dir_is_a_sibling_of_the_usage_log(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
        a = _agent(tmp_path)
        assert Path(a._heartbeat_dir()) == tmp_path / "heartbeats"

    def test_env_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WCB_HEARTBEAT_HOST_DIR", "/elsewhere/hb")
        assert _agent(tmp_path)._heartbeat_dir() == "/elsewhere/hb"

    def test_no_usage_log_means_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
        a = _agent(tmp_path)
        a.litellm_usage_log = ""
        assert a._heartbeat_dir() == ""
        assert a._heartbeat_mtime(RUN_KEY) == 0.0

    def test_shared_bridge_lane_counts_by_default(self, tmp_path, monkeypatch, guarded):
        from src.agents.openclaw.runner import OpenClawAgent
        a = _agent(tmp_path)
        _beat(a, name=OpenClawAgent._HEARTBEAT_LANE_BRIDGE)
        assert a._heartbeat_mtime(RUN_KEY) > 0.0

    def test_shared_lane_can_be_dropped(self, tmp_path, monkeypatch, guarded):
        from src.agents.openclaw.runner import OpenClawAgent
        a = _agent(tmp_path)
        _beat(a, name=OpenClawAgent._HEARTBEAT_LANE_BRIDGE)
        monkeypatch.setenv("WCB_STALL_HEARTBEAT_SHARED_LANE", "0")
        assert a._heartbeat_mtime(RUN_KEY) == 0.0

    def test_another_runs_heartbeat_is_not_this_runs_liveness(self, tmp_path, guarded):
        from src.agents.openclaw.runner import OpenClawAgent
        a = _agent(tmp_path)
        _beat(a, name=OpenClawAgent._heartbeat_name("wcb::t2::" + "d" * 32))
        assert a._heartbeat_mtime(RUN_KEY) == 0.0


# ------------------------------------------------------- key agreement


class TestWriterReaderKeyAgreement:
    """The writers ship standalone (the sidecar image has ONLY its callback
    module; the bridge image has ONLY claude_oauth/). They cannot import the
    reader, so nothing but this test stops the two key schemes from drifting
    apart — and a drift silently disables the heartbeat half of the guard."""

    def test_sidecar_filename_matches_the_reader(self):
        import src.utils.litellm_heartbeat_callback as hb
        from src.agents.openclaw.runner import OpenClawAgent
        for key in (RUN_KEY, "wcb::a/b::x", "wcb::" + "z" * 300 + "::y"):
            assert hb.safe_name(key) == OpenClawAgent._heartbeat_name(key)

    def test_bridge_lane_name_matches_the_reader(self):
        import src.utils.claude_oauth.stream_tee as tee
        from src.agents.openclaw.runner import OpenClawAgent
        assert tee.HEARTBEAT_LANE_BRIDGE == OpenClawAgent._HEARTBEAT_LANE_BRIDGE

    def test_sidecar_write_is_found_by_the_reader(self, tmp_path, monkeypatch):
        """End-to-end across the two processes' contracts: the callback writes
        where the sidecar's /var/litellm_usage mount lands host-side, and the
        runner finds it from the usage-log path alone."""
        hb_dir = tmp_path / "heartbeats"
        monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
        monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
        monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
        import src.utils.litellm_heartbeat_callback as hb
        hb = importlib.reload(hb)
        hb.touch(RUN_KEY)
        a = _agent(tmp_path)
        assert a._heartbeat_mtime(RUN_KEY) > 0.0

    def test_bridge_write_is_found_by_the_reader(self, tmp_path, monkeypatch):
        hb_dir = tmp_path / "heartbeats"
        monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
        monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
        monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
        monkeypatch.delenv("WCB_STALL_HEARTBEAT_SHARED_LANE", raising=False)
        import src.utils.claude_oauth.stream_tee as tee
        tee = importlib.reload(tee)
        tee.touch_heartbeat()
        a = _agent(tmp_path)
        assert a._heartbeat_mtime(RUN_KEY) > 0.0


# --------------------------------------------------- sidecar/bridge wiring


class TestSidecarWiring:
    def test_heartbeat_dir_is_a_sibling_of_the_usage_mount(self, monkeypatch):
        from src.utils import litellm_sidecar as sc
        monkeypatch.delenv("WCB_HEARTBEAT_HOST_DIR", raising=False)
        assert sc.heartbeat_host_dir("/work/litellm-usage-b1") == \
            "/work/litellm-usage-b1/heartbeats"
        assert sc.heartbeat_host_dir("") == ""
        monkeypatch.setenv("WCB_HEARTBEAT_HOST_DIR", "/hb")
        assert sc.heartbeat_host_dir("/work/litellm-usage-b1") == "/hb"

    def test_container_path_matches_the_usage_mount(self):
        from src.utils import litellm_sidecar as sc
        assert sc.HEARTBEAT_DIR_CONTAINER.startswith("/var/litellm_usage/")
        assert sc.HEARTBEAT_DIR_CONTAINER != "/var/litellm_usage/usage.jsonl"

    def test_callback_module_ships_beside_the_config(self):
        from src.utils import litellm_sidecar as sc
        path = Path(sc.heartbeat_callback_module_path())
        assert path.is_file()
        assert path.name == f"{sc.HEARTBEAT_CALLBACK_MODULE}.py"
