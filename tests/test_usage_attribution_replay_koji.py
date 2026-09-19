"""Offline replay of the 2026-09-19 koji_sloan shared-sidecar rerun, both lanes.

Two runs of one task against one shared usage log, and both shipped
``usage_attribution: {"status": "failed"}`` with every per-message cost block
empty — run_1 two rows over its message count, run_2 twelve. Neither is the
willie_run3 fault: no row in either lane billed zero in every column. Both are
surplus ROWS, and both are surplus for the same reason.

The agent's own ``cron`` tool created jobs with ``sessionTarget: "main"`` and
``payload.kind: "systemEvent"``. That pair never reaches the isolated cron
runner — ``validateCronJob`` rejects a main job whose payload is anything else
(dist/gateway-cli-BjsM6fWb.js:5001) — it is enqueued as a system event with
``contextKey: `cron:${job.id}` `` (:6316) and delivered by the HEALTH loop,
which swaps ``buildCronEventPrompt`` in for the default heartbeat body at
dist/health-BxAgqqNt.js:403. So these are heartbeat turns wearing the
cron-event body, they are pruned back out of the transcript like every other
heartbeat (``pruneHeartbeatTranscript``, :302, called at :575/:604/:629), and
the shipped classifier missed them because it knew the default prompt and
nothing else.

The fixture holds both runs' REAL usage rows, byte for byte including the
``purpose`` column the sidecar wrote at request time, their REAL message roles
and delivered user turns, and the attribution report each run actually
shipped. The one reconstructed step is the row's request body, because the
sidecar logs none: the cron-event prompt is rebuilt from each run's OWN
systemEvent text, captured verbatim from the cron tool calls in chat.jsonl,
inside buildCronEventPrompt's shipped sentences.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_batch import (
    _attribute_per_message_cost,
    _count_heartbeat_turns_in_transcript,
    _count_memory_flush_turns_in_transcript,
    _usage_row_bills_no_tokens,
    _usage_row_purpose,
)
from src.utils import litellm_usage_callback as uc

FIXTURE = (Path(__file__).parent / "fixtures"
           / "usage_replay_koji_20260919.json")

_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
_LANES = ("run_1", "run_2")


@pytest.fixture(scope="module")
def replay():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _lane(replay, name):
    return replay["runs"][name]


def _traj(lane):
    return {"messages": [
        {"message": {"role": role, "content": [{"type": "text", "text": text}]}}
        for role, text in zip(lane["message_roles"], lane["message_texts"])
    ]}


def _rows_as_reported(lane):
    """The rows the run's own report counted.

    run_2's third closing side-session row landed in the shared log after
    usage.json had been written, so the delivered report knows 177 rows and
    two post-agent ones, not 178 and three.
    """
    trailing = lane.get("trailing_row_ts")
    if not trailing:
        return list(lane["rows"])
    return [r for r in lane["rows"] if r["ts"] != trailing]


def _write_log(tmp_path, rows, name="usage.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _cron_event_prompt(event_text, deliver_to_user=True):
    """buildCronEventPrompt's non-empty branch, dist/health-BxAgqqNt.js:85-86.

    Verbatim, including the two-blank-line joins:

        "A scheduled reminder has been triggered. The reminder content is:\\n\\n"
          + eventText + "\\n\\nHandle this reminder internally. ..."      (:85)
        "A scheduled reminder has been triggered. The reminder content is:\\n\\n"
          + eventText + "\\n\\nPlease relay this reminder ..."            (:86)
    """
    tail = ("Please relay this reminder to the user in a helpful and friendly "
            "way." if deliver_to_user else
            "Handle this reminder internally. Do not relay it to the user "
            "unless explicitly requested.")
    return ("A scheduled reminder has been triggered. The reminder content is:"
            "\n\n" + event_text + "\n\n" + tail)


def _request_on_a_live_session(body):
    """What the sidecar is handed for a heartbeat: system prompt, history, body.

    A cron event is delivered into the session the agent is already on, so the
    request carries that session's history — which is exactly what the two
    shape guards below the self-issued labels would drop it on.
    """
    return {"messages": [
        {"role": "system", "content": "You are openclaw, an autonomous agent."},
        {"role": "user", "content": "go through the placard set and mark each"},
        {"role": "assistant", "content": "working through them now"},
        {"role": "user", "content": body},
    ]}


def _label_for(lane, deliver_to_user=True):
    """The label the sidecar would have written, from the run's own event text."""
    body = _cron_event_prompt(lane["cron_jobs"][0]["event_text"],
                              deliver_to_user)
    return uc._classify_internal_purpose(_request_on_a_live_session(body))


def _attribute(lane, tmp_path, rows, name):
    return _attribute_per_message_cost(
        _traj(lane), str(_write_log(tmp_path, rows, name)), lane["run_key"],
        agent_finished_ts=lane.get("agent_finished_ts"))


# ============================================================================
# The runs as delivered
# ============================================================================


def test_fixture_is_the_pair_of_runs_that_failed(replay):
    assert _lane(replay, "run_1")["reported_attribution"] == {
        "status": "failed", "messages": 161, "rows_selected": 216,
        "rows_internal": 53, "rows_post_agent": 0, "rows_zero_token": 0,
        "rows_unmatched": 2,
    }
    assert _lane(replay, "run_2")["reported_attribution"] == {
        "status": "failed", "messages": 124, "rows_selected": 177,
        "rows_internal": 39, "rows_post_agent": 2, "rows_zero_token": 0,
        "rows_unmatched": 12,
    }


@pytest.mark.parametrize("name,rows,assistants", [
    ("run_1", 216, 161), ("run_2", 178, 124)])
def test_the_rows_and_the_transcript_are_the_delivered_ones(
        replay, name, rows, assistants):
    lane = _lane(replay, name)
    assert len(lane["rows"]) == rows
    assert lane["message_roles"].count("assistant") == assistants
    assert all(r["run_key"] == lane["run_key"] for r in lane["rows"])
    assert all(r["kind"] == "agent" for r in lane["rows"])


@pytest.mark.parametrize("name", _LANES)
def test_no_row_in_either_lane_billed_nothing(replay, name):
    """Not the willie_run3 fault. The zero-token split cannot help here."""
    lane = _lane(replay, name)
    assert [r for r in lane["rows"] if _usage_row_bills_no_tokens(r)] == []


@pytest.mark.parametrize("name,expected", [
    ("run_1", {"image": 26, "embeddings": 24, "compaction": 2, "heartbeat": 1}),
    ("run_2", {"embeddings": 25, "image": 9, "compaction": 3, "heartbeat": 2})])
def test_the_sidecar_had_already_labelled_every_row_it_knew(
        replay, name, expected):
    by_purpose: dict[str, int] = {}
    for row in _lane(replay, name)["rows"]:
        purpose = _usage_row_purpose(row)
        if purpose:
            by_purpose[purpose] = by_purpose.get(purpose, 0) + 1
    assert by_purpose == expected


@pytest.mark.parametrize("name", _LANES)
def test_the_delivered_transcript_holds_no_self_issued_turn(replay, name):
    """Which is the whole problem: the rows are there, the messages are not."""
    traj = _traj(_lane(replay, name))
    assert _count_heartbeat_turns_in_transcript(traj["messages"]) == 0
    assert _count_memory_flush_turns_in_transcript(traj["messages"]) == 0
    users = [t for r, t in zip(_lane(replay, name)["message_roles"],
                               _lane(replay, name)["message_texts"])
             if r == "user"]
    assert len(users) == 18
    assert all(t.startswith("[") and "PDT]" in t[:40] for t in users)


# ============================================================================
# What the container actually scheduled
# ============================================================================


@pytest.mark.parametrize("name", _LANES)
def test_every_cron_job_is_a_main_session_system_event(replay, name):
    """Which is what routes them through the health loop instead of the runner.

    dist/gateway-cli-BjsM6fWb.js:5001 rejects a main job whose payload is not
    a systemEvent, so this pair is the ONLY shape a main cron job can have,
    and a main job is dispatched by enqueueSystemEvent (:6316) rather than by
    runCronIsolatedAgentTurn.
    """
    jobs = _lane(replay, name)["cron_jobs"]
    assert jobs
    for job in jobs:
        assert job["sessionTarget"] == "main"
        assert job["payload_kind"] == "systemEvent"
        assert job["schedule"]["kind"] == "at"
        assert job["event_text"].strip()


@pytest.mark.parametrize("name", _LANES)
def test_the_scheduled_bodies_are_named_heartbeat_not_cron(replay, name):
    """The label, derived from the run's own event text rather than asserted.

    A cron EVENT is the health loop's, a cron JOB is the cron runner's, and
    only the second one carries the `[cron:<id> <name>]` prefix this file's
    `cron` label anchors on. Both deliverToUser variants are checked because
    the prompt builder picks between them on config this run did not set.
    """
    lane = _lane(replay, name)
    for deliver in (True, False):
        assert _label_for(lane, deliver) == "heartbeat"
    body = _cron_event_prompt(lane["cron_jobs"][0]["event_text"])
    assert not uc._is_cron_prompt([{"role": "user", "content": body}])
    assert uc._is_heartbeat_prompt([{"role": "user", "content": body}])


@pytest.mark.parametrize("name", _LANES)
def test_the_shipped_build_could_not_name_those_bodies(replay, name):
    """Why the runs failed, checked against the classifier minus one tuple.

    _HEARTBEAT_EVENT_HEADS is the whole of the difference. Take it away and
    the request the sidecar saw goes back to being unnameable — it is a full
    agent turn, so it carries a system prompt and history and both shape
    guards drop it.
    """
    lane = _lane(replay, name)
    body = _cron_event_prompt(lane["cron_jobs"][0]["event_text"])
    kwargs = _request_on_a_live_session(body)
    original = uc._HEARTBEAT_EVENT_HEADS
    try:
        uc._HEARTBEAT_EVENT_HEADS = ()
        assert uc._classify_internal_purpose(kwargs) == ""
    finally:
        uc._HEARTBEAT_EVENT_HEADS = original
    assert uc._classify_internal_purpose(kwargs) == "heartbeat"


@pytest.mark.parametrize("name,sizes,total", [
    ("run_1", [2], 2), ("run_2", [7, 5, 3], 15)])
def test_the_surplus_rows_are_exactly_the_side_session_chains(
        replay, name, sizes, total):
    """The row evidence, independent of any prompt.

    Chaining rows by cache_read -> a preceding row's cache_read+cache_write
    separates the main lineage from everything running beside it. run_1's one
    side chain is its two unmatched rows; run_2's first two are its twelve,
    and the third opens at the very end of the run.
    """
    lane = _lane(replay, name)
    assert lane["side_session_chain_sizes"] == sizes
    assert len(lane["side_session_row_ts"]) == total
    reported = lane["reported_attribution"]
    during = [ts for ts in lane["side_session_row_ts"]
              if ts in {r["ts"] for r in _rows_as_reported(lane)}]
    assert len(during) - reported["rows_post_agent"] == reported["rows_unmatched"]
    assert all(not r.get("purpose") for r in lane["rows"]
               if r["ts"] in set(lane["side_session_row_ts"]))


# ============================================================================
# The gate
# ============================================================================


@pytest.mark.parametrize("name,internal,post_agent", [
    ("run_1", 55, 0), ("run_2", 51, 2)])
def test_replay_reaches_attributed(replay, tmp_path, name, internal, post_agent):
    lane = _lane(replay, name)
    label = _label_for(lane)
    named = {ts for ts in lane["side_session_row_ts"]}
    rows = [dict(r, purpose=label) if r["ts"] in named else r
            for r in _rows_as_reported(lane)]
    report = _attribute(lane, tmp_path, rows, f"{name}.jsonl")

    assert report["status"] == "attributed"
    assert report["rows_unmatched"] == 0
    assert report["messages"] == lane["reported_attribution"]["messages"]
    assert report["rows_selected"] == lane["reported_attribution"]["rows_selected"]
    assert report["rows_internal"] == internal
    assert report["rows_post_agent"] == post_agent
    assert report["rows_zero_token"] == 0
    assert "heartbeat_turns_in_transcript" not in report
    assert "memory_flush_turns_in_transcript" not in report


@pytest.mark.parametrize("name,before,after", [
    ("run_1", 1, 3), ("run_2", 2, 14)])
def test_the_named_rows_join_the_heartbeat_line_they_belong_on(
        replay, tmp_path, name, before, after):
    """The two the sidecar already named and the ones it now names are one bucket."""
    lane = _lane(replay, name)
    label = _label_for(lane)
    named = set(lane["side_session_row_ts"])
    rows = [dict(r, purpose=label) if r["ts"] in named else r
            for r in _rows_as_reported(lane)]
    report = _attribute(lane, tmp_path, rows, f"{name}.jsonl")

    assert report["internal_calls"]["by_purpose"]["heartbeat"]["request_count"] \
        == after
    plain = _attribute(lane, tmp_path, _rows_as_reported(lane), f"{name}_p.jsonl")
    assert plain["internal_calls"]["by_purpose"]["heartbeat"]["request_count"] \
        == before


def test_the_post_agent_cron_chain_stops_being_anonymous(replay, tmp_path):
    """run_2's closing chain is late, not internal, and now it is named late.

    The post-agent boundary is applied BEFORE the purpose labels, so naming
    the row does not move it out of post_agent_calls — it only stops that
    block reporting openclaw's own turn as `unlabelled`.
    """
    lane = _lane(replay, "run_2")
    named = set(lane["side_session_row_ts"])
    plain = _attribute(lane, tmp_path, _rows_as_reported(lane), "r2_plain.jsonl")
    assert set(plain["post_agent_calls"]["by_purpose"]) == {"unlabelled"}
    assert plain["post_agent_calls"]["by_purpose"]["unlabelled"] == {
        "input_tokens": 4, "output_tokens": 594, "cache_read_tokens": 44515,
        "cache_write_tokens": 46481, "total_tokens": 91594,
        "audio_seconds": 0.0, "cost_usd": 0.0, "request_count": 2,
    }

    label = _label_for(lane)
    rows = [dict(r, purpose=label) if r["ts"] in named else r
            for r in _rows_as_reported(lane)]
    fixed = _attribute(lane, tmp_path, rows, "r2_fixed.jsonl")
    assert set(fixed["post_agent_calls"]["by_purpose"]) == {"heartbeat"}
    assert fixed["post_agent_calls"]["by_purpose"]["heartbeat"]["request_count"] == 2
    for column in _TOKEN_COLUMNS:
        assert (fixed["post_agent_calls"]["by_purpose"]["heartbeat"][column]
                == plain["post_agent_calls"]["by_purpose"]["unlabelled"][column])


@pytest.mark.parametrize("name", _LANES)
def test_without_the_event_heads_the_same_log_reproduces_the_shipped_failure(
        replay, tmp_path, name):
    """The counterfactual, field for field against the delivered usage.json.

    Nothing about the replay changes — same rows, same labels the sidecar
    wrote, same transcript, same boundary. The rows the fix names are simply
    left unnamed, which is exactly the build that shipped, and the report
    comes back as the artifact actually carries it.
    """
    lane = _lane(replay, name)
    report = _attribute(lane, tmp_path, _rows_as_reported(lane), f"{name}_cf.jsonl")
    assert {k: report[k] for k in lane["reported_attribution"]} \
        == lane["reported_attribution"]
    assert "per_message_cost_usd" not in report


# ============================================================================
# Reconciliation
# ============================================================================


@pytest.mark.parametrize("name", _LANES)
def test_the_four_buckets_add_back_up_to_the_agent_total(
        replay, tmp_path, name):
    """per-message + internal + post_agent + zero_token == sources.agent.

    On all four token columns and on the request count, for the run as the
    fix attributes it. ``expected_agent_totals`` is what the delivered
    usage.json booked for the agent, so this is the arithmetic the shipped
    artifact is held to. It needs no adjustment for run_2's late row:
    sources.agent was totalled from the same 177 rows the report counted, so
    the row that landed afterwards is outside both sides of this equation.
    """
    lane = _lane(replay, name)
    label = _label_for(lane)
    named = set(lane["side_session_row_ts"])
    reported_rows = _rows_as_reported(lane)
    rows = [dict(r, purpose=label) if r["ts"] in named else r
            for r in reported_rows]
    traj = _traj(lane)
    path = _write_log(tmp_path, rows, f"{name}_rec.jsonl")
    report = _attribute_per_message_cost(
        traj, str(path), lane["run_key"],
        agent_finished_ts=lane.get("agent_finished_ts"))
    assert report["status"] == "attributed"

    expected = lane["expected_agent_totals"]
    key = {"input_tokens": "input", "output_tokens": "output",
           "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    per_message = {c: 0 for c in _TOKEN_COLUMNS}
    attributed_messages = 0
    for msg in traj["messages"]:
        usage = msg["message"].get("usage")
        if usage:
            attributed_messages += 1
            for column in _TOKEN_COLUMNS:
                per_message[column] += usage[key[column]]

    empty = {k: 0 for k in (*_TOKEN_COLUMNS, "request_count")}
    internal = report["internal_calls"]
    post = report.get("post_agent_calls") or empty
    zero = report.get("zero_token_calls") or empty
    for column in _TOKEN_COLUMNS:
        assert (per_message[column] + internal[column] + post[column]
                + zero[column]) == expected[column], column
    assert attributed_messages == report["messages"]
    assert (report["messages"] + internal["request_count"]
            + post["request_count"] + zero["request_count"]) \
        == expected["request_count"] == report["rows_selected"]
