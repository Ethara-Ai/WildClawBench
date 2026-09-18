"""Offline replay of the 2026-09-18 willie_prince run that the heartbeat broke.

That run shipped ``usage_attribution: {"status": "failed", "rows_unmatched": 1}``
and 114 assistant messages with no cost block, over a single surplus row: the
gateway's own 30-minute heartbeat, which is a full agent turn nobody asked for
and which no assistant message can claim.

The fixture holds that run's REAL usage rows (the 155 carrying its run_key in
the shared sidecar log), its REAL message roles and its REAL user turns. This
drives them through the classifier and the attribution path as they now stand
and requires the gate to close.

The request shapes are reconstructed per row from the agent bundle's own call
builders — the usage log records tokens, not prompts — and the reconstruction
is what ``_classify_internal_purpose`` is handed. The token numbers, the row
count, the message count and the expected totals are all the run's own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_batch import (
    _attribute_per_message_cost,
    _count_heartbeat_turns_in_transcript,
    _usage_row_purpose,
)
from src.utils import litellm_usage_callback as uc
from src.utils.grading import extract_usage_from_litellm_log

FIXTURE = Path(__file__).parent / "fixtures" / "usage_replay_willie_20260918.json"

_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
_RUN_KEY = (
    "wcb::willie_prince_6dfaf967-c57b-4a45-9c99-f43d65b8539b_claude-opus-5"
    "_20260918_2116_50315f::b48135b72343434caaaf06f9094878e9"
)

# The user message runHeartbeatOnce sent at 21:54:21, assembled in the bundle's
# own order: resolveHeartbeatPrompt's default (dist/reply-BCcP6j4h.js:9084),
# then appendHeartbeatWorkspacePathHint (dist/health-BxAgqqNt.js:390), then
# appendCronStyleCurrentTimeLine (dist/reply-BCcP6j4h.js:36579).
_HEARTBEAT_BODY = (
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
    "Do not infer or repeat old tasks from prior chats. If nothing needs "
    "attention, reply HEARTBEAT_OK.\n"
    "When reading HEARTBEAT.md, use workspace file /workspace/HEARTBEAT.md "
    "(exact case). Do not read docs/heartbeat.md.\n"
    "Current time: Fri, Sep 18, 2026 at 9:54 PM (UTC) / 2026-09-18 21:54 UTC"
)
_AGENT_SYSTEM = "You are openclaw, an autonomous coding agent."


@pytest.fixture(scope="module")
def replay():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _kwargs_for(row, replay):
    """The request that produced ``row``, in the shape its caller builds it.

    embeddings — extensions/memory-lancedb/index.ts posts {model, input} to
      /v1/embeddings; litellm reports the call under an embedding call_type.
    image      — src/agents/tools/image-tool.ts::buildImageContext sends one
      system-less user message of [text block, image block...].
    compaction — pi-coding-agent's SUMMARIZATION_SYSTEM_PROMPT over a
      <conversation>-wrapped transcript.
    heartbeat  — a full agent turn: the gateway's prompt behind the run's own
      system prompt and the session history it fired into.
    turn       — the conversation so far under the agent's system prompt.
    """
    if "embedding" in row.get("model", ""):
        return {"call_type": "aembedding", "model": row["model"],
                "litellm_params": {"proxy_server_request": {"body": {
                    "model": row["model"], "input": "corridor release rulebook"}}}}
    if row["ts"] in set(replay["image_tool_row_ts"]):
        return {"litellm_params": {"proxy_server_request": {"body": {
            "system": "",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "read the release notes in this screenshot"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": "QUJD"}},
            ]}]}}}}
    if row["ts"] in set(replay["compaction_row_ts"]):
        return {"messages": [
            {"role": "system", "content": uc._COMPACTION_SYSTEM_HEAD},
            {"role": "user", "content": "<conversation>\nturn 1\n</conversation>"},
        ]}
    if row["ts"] == replay["heartbeat_row_ts"]:
        return {"litellm_params": {"proxy_server_request": {"body": {
            "system": _AGENT_SYSTEM,
            "messages": [
                {"role": "user", "content": "walk me through the corridor push"},
                {"role": "assistant", "content": "reading the changelog"},
                {"role": "user", "content": _HEARTBEAT_BODY},
            ]}}}}
    return {"messages": [
        {"role": "system", "content": _AGENT_SYSTEM},
        {"role": "user", "content": "check the pieces against the rulebook"},
        {"role": "assistant", "content": "pulling the in-force version"},
        {"role": "user", "content": "carry on"},
    ]}


def _classified_rows(replay, *, name_heartbeat=True):
    """The run's rows as the sidecar would write them today.

    ``name_heartbeat=False`` reproduces the shipped build, so a test can show
    the difference is the label and not something else about the replay.
    """
    out = []
    for row in replay["rows"]:
        fresh = {k: v for k, v in row.items() if k != "purpose"}
        purpose = uc._classify_internal_purpose(_kwargs_for(row, replay))
        if purpose == "heartbeat" and not name_heartbeat:
            purpose = ""
        if purpose:
            fresh["purpose"] = purpose
        out.append(fresh)
    return out


def _traj(replay):
    """The delivered trajectory: real roles, and the real text of every user turn."""
    return {"messages": [
        {"message": {"role": role, "content": [{"type": "text", "text": text}]}}
        for role, text in zip(replay["message_roles"], replay["message_texts"])
    ]}


def _write_log(tmp_path, rows, name="usage.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _attribute(replay, tmp_path, *, name_heartbeat=True, name="usage.jsonl"):
    traj = _traj(replay)
    path = _write_log(tmp_path, _classified_rows(
        replay, name_heartbeat=name_heartbeat), name)
    report = _attribute_per_message_cost(
        traj, str(path), _RUN_KEY, oauth_route=True, model="claude-opus-5")
    return traj, report, path


# ============================================================================
# The run as delivered
# ============================================================================


def test_fixture_is_the_run_that_failed(replay):
    assert replay["reported_attribution"] == {
        "status": "failed", "messages": 114, "rows_selected": 155,
        "rows_internal": 40, "rows_post_agent": 0, "rows_unmatched": 1,
    }
    assert len(replay["rows"]) == 155
    assert replay["message_roles"].count("assistant") == 114


def test_the_orphan_row_is_the_heartbeat_the_forensics_named(replay):
    """One row, 5.368s long, 1.17s past the first idle moment after 30 minutes."""
    row = next(r for r in replay["rows"] if r["ts"] == replay["heartbeat_row_ts"])
    assert row["input_tokens"] == 2
    assert row["output_tokens"] == 263
    assert row["cache_write_tokens"] == 28463
    assert row["cache_read_tokens"] == 0
    assert row["reasoning_tokens"] == 48
    assert row["duration_s"] == 5.368
    assert "purpose" not in row


def test_the_shipped_build_leaves_exactly_one_row_unnamed(replay):
    """115 unlabelled rows for 114 messages — the whole of the failure."""
    rows = _classified_rows(replay, name_heartbeat=False)
    assert len([r for r in rows if not r.get("purpose")]) == 115
    assert len([r for r in rows if r.get("purpose")]) == 40


# ============================================================================
# The classifier, on this run's rows
# ============================================================================


def test_every_surplus_row_is_now_labelled(replay):
    rows = _classified_rows(replay)
    by_purpose = {}
    for r in rows:
        if r.get("purpose"):
            by_purpose.setdefault(r["purpose"], []).append(r)
    assert {k: len(v) for k, v in by_purpose.items()} == {
        "embeddings": 22, "image": 16, "compaction": 2, "heartbeat": 1,
    }


def test_the_turns_are_left_unlabelled(replay):
    rows = _classified_rows(replay)
    assert len([r for r in rows if not r.get("purpose")]) == 114


def test_the_heartbeat_label_lands_on_the_orphan_row_and_nothing_else(replay):
    rows = _classified_rows(replay)
    named = [r for r in rows if r.get("purpose") == "heartbeat"]
    assert len(named) == 1
    assert named[0]["ts"] == replay["heartbeat_row_ts"]


# ============================================================================
# The gate
# ============================================================================


def test_replay_reaches_attributed(replay, tmp_path):
    _, report, _ = _attribute(replay, tmp_path)

    assert report["status"] == "attributed"
    assert report["rows_unmatched"] == 0
    assert report["messages"] == 114
    assert report["rows_selected"] == 155
    assert report["rows_internal"] == 41


def test_without_the_heartbeat_label_the_same_log_stays_failed(replay, tmp_path):
    """The counterfactual, so the test above is attributable to the label.

    Nothing else about the replay changes — same rows, same other labels, same
    messages — and the report comes back as the artifact actually shipped.
    """
    _, report, _ = _attribute(replay, tmp_path, name_heartbeat=False)

    assert report["status"] == "failed"
    assert report["rows_internal"] == 40
    assert report["rows_unmatched"] == 1
    assert {k: report[k] for k in replay["reported_attribution"]} == \
        replay["reported_attribution"]


def test_replay_fills_every_assistant_message(replay, tmp_path):
    traj, _, _ = _attribute(replay, tmp_path)

    assistants = [m for m in traj["messages"]
                  if m["message"]["role"] == "assistant"]
    assert len(assistants) == 114
    for msg in assistants:
        usage = msg["message"]["usage"]
        assert usage["totalTokens"] > 0
        assert usage["cost"]["total"] > 0.0


# ============================================================================
# The ledger — "heartbeat" rides the existing by_purpose mechanism
# ============================================================================


def test_the_heartbeat_is_its_own_ledger_line_with_its_real_tokens(replay, tmp_path):
    """usage.json carries the row named, not folded into an anonymous total."""
    _, report, _ = _attribute(replay, tmp_path)
    line = report["internal_calls"]["by_purpose"]["heartbeat"]

    assert line["request_count"] == 1
    assert line["input_tokens"] == 2
    assert line["output_tokens"] == 263
    assert line["cache_write_tokens"] == 28463
    assert line["cache_read_tokens"] == 0
    assert line["total_tokens"] == 28728


def test_the_ledger_needed_no_change_to_accept_the_new_label(replay, tmp_path):
    """_usage_row_purpose passes the label through and by_purpose groups on it.

    Neither carries a list of known purposes, which is what 517eacc's audit
    said and what a new label is the test of.
    """
    row = next(r for r in _classified_rows(replay)
               if r.get("purpose") == "heartbeat")
    assert _usage_row_purpose(row) == "heartbeat"

    _, report, _ = _attribute(replay, tmp_path)
    assert set(report["internal_calls"]["by_purpose"]) == {
        "compaction", "embeddings", "heartbeat", "image",
    }
    assert "unlabelled" not in report["internal_calls"]["by_purpose"]


def test_naming_the_heartbeat_does_not_move_money_out_of_the_total(replay, tmp_path):
    """The label must not change what the agent is billed."""
    before = extract_usage_from_litellm_log(
        _write_log(tmp_path, replay["rows"], "before.jsonl"), 0.0, 0.0, _RUN_KEY)
    after = extract_usage_from_litellm_log(
        _write_log(tmp_path, _classified_rows(replay), "after.jsonl"),
        0.0, 0.0, _RUN_KEY)

    assert after == before
    for column in (*_TOKEN_COLUMNS, "total_tokens", "request_count"):
        assert after[column] == replay["expected_agent_totals"][column], column


def test_the_ledgers_add_back_up_to_sources_agent(replay, tmp_path):
    """Σ(per-message) + internal_calls + post_agent_calls == sources.agent.

    The reconciliation the artifact promises, on the run that prompted it,
    with the row that used to have nowhere to go.
    """
    traj, report, path = _attribute(replay, tmp_path)

    per_message = {k: 0 for k in _TOKEN_COLUMNS}
    key = {"input_tokens": "input", "output_tokens": "output",
           "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    for msg in traj["messages"]:
        usage = msg["message"].get("usage")
        if usage:
            for col in _TOKEN_COLUMNS:
                per_message[col] += usage[key[col]]

    totals = extract_usage_from_litellm_log(path, 0.0, 0.0, _RUN_KEY)
    internal = report["internal_calls"]
    post = report.get("post_agent_calls") or {k: 0 for k in
                                              (*_TOKEN_COLUMNS, "request_count")}
    for col in _TOKEN_COLUMNS:
        assert per_message[col] + internal[col] + post[col] == totals[col], col
    assert report["messages"] + internal["request_count"] + post["request_count"] \
        == totals["request_count"]


def test_the_buckets_stay_disjoint(replay, tmp_path):
    """No row is billed twice: the three counts partition rows_selected."""
    _, report, _ = _attribute(replay, tmp_path)

    assert report["messages"] + report["rows_internal"] \
        + report["rows_post_agent"] == report["rows_selected"]


# ============================================================================
# The transcript side — this run's heartbeat was pruned, so nothing is stamped
# ============================================================================


def test_this_runs_heartbeat_left_no_turn_in_the_delivered_transcript(replay, tmp_path):
    """Its reply was the bare HEARTBEAT_OK token, so the pair was truncated out.

    dist/health-BxAgqqNt.js:604 pruneHeartbeatTranscript. That is why the run
    failed quietly: the money was in the log and the turn was not in the chat,
    so nothing on the transcript side could have caught it. The detector must
    therefore stay silent here, or it would fire on every pruned run.
    """
    assert _count_heartbeat_turns_in_transcript(_traj(replay)["messages"]) == 0

    _, report, _ = _attribute(replay, tmp_path)
    assert "heartbeat_turns_in_transcript" not in report


def test_the_real_user_turns_are_carried_and_none_of_them_matches(replay):
    """The detector is run against this run's actual human prompts, not blanks."""
    texts = [t for t, role in zip(replay["message_texts"], replay["message_roles"])
             if role == "user"]
    assert len(texts) == 20
    assert all(t.strip() for t in texts)
    assert not any(uc._is_heartbeat_prompt([{"role": "user", "content": t}])
                   for t in texts)
