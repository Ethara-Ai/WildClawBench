"""Two tasks, mixed lanes, one shared sidecar log: nothing crosses.

Threat model: `bash script/run.sh --input-dir X --parallel-tasks 6
--use-claude-oauth --judge-provider bedrock`. Under -P N there are N OS
processes, each with its own os.environ, sharing exactly three mutable
surfaces: the sidecar container, the cc-bridge container, and the on-disk
usage/heartbeat files. The last two are what this module pins.

Both proofs are STRUCTURAL rather than a matter of tagging discipline:

  * the judge writes no usage row because the row writer is a LiteLLM callback
    mounted INSIDE the sidecar container, keyed on the incoming request's
    bearer, and the judge is host-side on both lanes -- bridge-direct or
    Bedrock-direct. It never transits the sidecar, so the callback never runs
    for it. If it ever did, the retry machinery would start counting judge
    traffic as agent liveness and a dead turn would read as alive.

  * the judge cannot bump the shared lane-bridge heartbeat, because that file
    is touched only through StreamTee, which the bridge constructs only on its
    streaming branch, and the judge is unconditionally stream:False. If it
    could, an unrelated judge would hold a wedged agent's stall clock open.

No Docker, no network, no credentials: both transports are faked.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run_batch import _attribute_per_message_cost, _lane_routes, save_usage  # noqa: E402
from src.agents.openclaw import OpenClawAgent  # noqa: E402
from src.utils import auth_provider as ap  # noqa: E402
from src.utils import judge_litellm  # noqa: E402
from src.utils.claude_oauth import stream_tee  # noqa: E402

KEY_A = "wcb::task-a::aaaa1111"
KEY_B = "wcb::task-b::bbbb2222"
BASE = 5000.0
SONNET_ARN = (
    "bedrock/arn:aws:bedrock:ap-south-1:1:application-inference-profile/sonnet"
)

_ENV = ("WCB_AUTH_PROVIDER", "WCB_JUDGE_AUTH_PROVIDER", "WCB_USE_CLAUDE_OAUTH",
        "WCB_CC_ACCOUNT_POOL", "KENSEI_JUDGE_OAUTH_BRIDGE_URL",
        "KENSEI_JUDGE_USE_LITELLM", "KENSEI_AWS_BEARER_TOKEN")


@pytest.fixture
def mixed_lanes(monkeypatch):
    """The owner's real launch shape: agent on the subscription, judge on
    Bedrock. Every child of a -P N run exports exactly this pair."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WCB_AUTH_PROVIDER", ap.OAUTH)
    monkeypatch.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.BEDROCK)
    monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:34567")
    monkeypatch.setenv("KENSEI_AWS_BEARER_TOKEN", "bearer-xyz")
    judge_litellm._registered_tails.clear()
    return monkeypatch


def _row(run_key, *, started=BASE, duration=2.0, kind="agent", cost=0.0123,
         in_tok=1200, out_tok=45):
    end = datetime.fromtimestamp(started + duration, timezone.utc)
    return {
        "ts": end.isoformat(), "model": "claude-opus-4.7", "kind": kind,
        "run_key": run_key, "input_tokens": in_tok, "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0, "audio_seconds": 0.0,
        "cost_usd": cost, "duration_s": round(duration, 3),
    }


def _write(path: Path, *rows):
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _traj(n_messages: int) -> dict:
    return {"messages": [
        {"role": "assistant", "content": f"m{i}"} for i in range(n_messages)
    ]}


def _fake_litellm(seen: list[dict]):
    def _completion(**kw):
        seen.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="1. c [[SATISFIED: Yes]]"))],
            usage={"prompt_tokens": 500, "completion_tokens": 20},
        )

    return SimpleNamespace(completion=_completion, register_model=lambda *a, **k: None)


def _run_a_judge(monkeypatch, family="sonnet"):
    seen: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(seen))
    judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u", max_output_tokens=64,
        cost_fn=lambda *a, **k: (0.25, True), family=family,
    )
    return seen


# ---------------------------------------------------------------------------
# The shared usage log
# ---------------------------------------------------------------------------


def test_two_tasks_rows_never_cross_run_keys(mixed_lanes, tmp_path):
    log = tmp_path / "usage.jsonl"
    _write(log,
           _row(KEY_A, started=BASE + 0, cost=0.10, in_tok=1000, out_tok=10),
           _row(KEY_B, started=BASE + 1, cost=0.20, in_tok=2000, out_tok=20),
           _row(KEY_A, started=BASE + 2, cost=0.30, in_tok=3000, out_tok=30),
           _row(KEY_B, started=BASE + 3, cost=0.40, in_tok=4000, out_tok=40))

    traj_a, traj_b = _traj(2), _traj(2)
    _attribute_per_message_cost(traj_a, str(log), KEY_A, model="claude-opus-4.7")
    _attribute_per_message_cost(traj_b, str(log), KEY_B, model="claude-opus-4.7")

    def _tok(t):
        return sum(int((m.get("usage") or {}).get("input", 0) or 0)
                   for m in t["messages"])

    assert _tok(traj_a) == 4000
    assert _tok(traj_b) == 6000
    assert _tok(traj_a) + _tok(traj_b) == 10000


def test_a_judge_call_leaves_the_usage_log_byte_identical(mixed_lanes, tmp_path,
                                                          monkeypatch):
    log = tmp_path / "usage.jsonl"
    _write(log, _row(KEY_A), _row(KEY_B), _row(KEY_A, kind="failure"))
    before = log.read_bytes()

    _run_a_judge(monkeypatch)
    mixed_lanes.setenv("WCB_AUTH_PROVIDER", ap.BEDROCK)
    mixed_lanes.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.OAUTH)
    mixed_lanes.setenv("WCB_CC_STUB_KEY", "sk-wcb-oauth-stub")
    _run_a_judge(monkeypatch)

    assert log.read_bytes() == before, "a judge call wrote into usage.jsonl"


def test_a_judge_call_does_not_move_the_attempt_scoped_row_count(
    mixed_lanes, tmp_path, monkeypatch
):
    """The retry/rebaseline lock. If a judge could add a row the empty-turn
    predicate would read a dead turn as alive and the empty-twice abort -- the
    one guard between a dead route and a full laddered run -- would never fire."""
    log = tmp_path / "usage.jsonl"
    _write(log, _row(KEY_A, started=BASE), _row(KEY_A, started=BASE + 1))
    agent = OpenClawAgent.__new__(OpenClawAgent)
    agent.litellm_usage_log = str(log)

    before = agent._count_run_key_rows(KEY_A, successes_only=True, since_epoch=BASE)
    _run_a_judge(monkeypatch)
    after = agent._count_run_key_rows(KEY_A, successes_only=True, since_epoch=BASE)
    assert before == after == 2


def test_a_judge_shaped_row_is_still_rejected_by_kind(tmp_path):
    """Defence in depth against a future sidecar-routed judge: even a row that
    forged the agent's run_key would not count, because the predicate parses
    `kind` rather than matching a substring."""
    log = tmp_path / "usage.jsonl"
    _write(log, _row(KEY_A, started=BASE))
    agent = OpenClawAgent.__new__(OpenClawAgent)
    agent.litellm_usage_log = str(log)
    before = agent._count_run_key_rows(KEY_A, successes_only=True, since_epoch=BASE)

    _write(log, _row(KEY_A, started=BASE + 1, kind="judge"))
    after = agent._count_run_key_rows(KEY_A, successes_only=True, since_epoch=BASE)
    assert before == after == 1


# ---------------------------------------------------------------------------
# The shared heartbeat lane
# ---------------------------------------------------------------------------


def test_the_judge_is_non_streaming_on_both_lanes(mixed_lanes, monkeypatch):
    """The premise of the heartbeat proof. lane-bridge is touched only through
    StreamTee, which the bridge builds only on its streaming branch."""
    seen = _run_a_judge(monkeypatch)
    mixed_lanes.setenv("WCB_AUTH_PROVIDER", ap.BEDROCK)
    mixed_lanes.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.OAUTH)
    mixed_lanes.setenv("WCB_CC_STUB_KEY", "sk-wcb-oauth-stub")
    seen += _run_a_judge(monkeypatch)
    assert len(seen) == 2
    assert all(kw["stream"] is False for kw in seen)


def test_a_judge_call_does_not_touch_the_shared_lane_bridge_heartbeat(
    mixed_lanes, tmp_path, monkeypatch
):
    lane = tmp_path / stream_tee.HEARTBEAT_LANE_BRIDGE
    lane.write_text("", encoding="utf-8")
    import os

    os.utime(lane, (BASE, BASE))
    before = lane.stat().st_mtime

    agent = OpenClawAgent.__new__(OpenClawAgent)
    agent.litellm_usage_log = str(tmp_path / "usage.jsonl")
    Path(agent.litellm_usage_log).touch()
    agent._heartbeat_dir = str(tmp_path)

    _run_a_judge(monkeypatch)
    assert lane.stat().st_mtime == before


def test_touch_heartbeat_is_reachable_only_through_stream_tee():
    """Pins the proof at its root rather than at one call site."""
    src = (Path(__file__).resolve().parents[1] / "src" / "utils"
           / "claude_oauth" / "stream_tee.py").read_text(encoding="utf-8")
    assert "HEARTBEAT_LANE_BRIDGE" in src
    bridge = (Path(__file__).resolve().parents[1] / "src" / "utils"
              / "claude_oauth" / "bridge.py").read_text(encoding="utf-8")
    assert bridge.count("StreamTee(") >= 1
    assert "_forward_non_streaming" in bridge
    for line in bridge.splitlines():
        if "StreamTee(" in line and "source=" in line:
            assert '"agent"' in line or "'agent'" in line, (
                "a judge-sourced tee would make the heartbeat proof false"
            )


# ---------------------------------------------------------------------------
# Per-task artifacts
# ---------------------------------------------------------------------------


def test_both_tasks_get_identical_lane_stamps(mixed_lanes, tmp_path):
    agent_usage = {
        "input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "total_tokens": 1100, "request_count": 5,
        "cost_usd": 0.0,
    }
    judge_usage = {
        "input_tokens": 4000, "output_tokens": 200, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "total_tokens": 4200, "request_count": 1,
        "cost_usd": 1.2345,
    }
    agent_route, judge_route = _lane_routes()
    assert (agent_route, judge_route) == (True, False)

    stamps = []
    for name in ("run_a", "run_b"):
        d = tmp_path / name
        d.mkdir()
        res = save_usage(d, {}, dict(agent_usage), name,
                         judge_usage=dict(judge_usage), model="claude-opus-4-6",
                         oauth_route=agent_route, judge_oauth_route=judge_route)
        usage = res["usage"]
        stamps.append((usage["auth_provider"], usage["judge_auth_provider"]))
        assert usage["sources"]["judge"]["cost_usd"] == 1.2345, (
            "a Bedrock judge's cost must not be re-derived by the agent's route"
        )
        assert usage["sources"]["agent"]["cost_usd"] > 0.0, (
            "a prepaid agent must not ship at $0"
        )

    assert stamps == [(ap.OAUTH, ap.BEDROCK)] * 2


def test_each_process_resolves_its_own_lanes(monkeypatch):
    """-P N forks PROCESSES, so there is no cross-task os.environ race. A child
    that lost the flag would resolve the agent's provider for both lanes, which
    is precisely what the wargs test in test_dual_provider_run_sh.py prevents."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WCB_AUTH_PROVIDER", ap.OAUTH)
    assert _lane_routes() == (True, True), "a child without the flag falls back"
    monkeypatch.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.BEDROCK)
    assert _lane_routes() == (True, False)
