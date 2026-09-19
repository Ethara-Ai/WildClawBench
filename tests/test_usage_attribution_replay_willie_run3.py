"""Offline replay of the 2026-09-18 willie_prince run_3 release-gate rerun.

run_2 was broken by a heartbeat that BILLED — a full agent turn nobody asked
for, which the classifier can now name from its prompt. run_3 is the other end
of the same fault: the heartbeat tick died inside openclaw's own gateway before
dispatch, so the request never reached the model, carried no recoverable body
and could not be labelled by anything — and the sidecar booked a row for it
anyway, 15.002 seconds long, with all four token columns zero.

That row was neither internal nor late, so it fell through to the turn
candidates as a 110th claimant on 109 assistant messages, and the run shipped
``usage_attribution: {"status": "failed", "rows_unmatched": 1}`` with every
per-message cost block empty.

The fixture holds that run's REAL usage rows — the 151 carrying its run_key in
the shared sidecar log, byte for byte including the ``purpose`` column the
sidecar wrote at request time — and its REAL message roles and user turns.
Nothing is reconstructed: run_3 already ran with the classifier under test in
run_2's replay, and the fix this drives is entirely attribution-side.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from eval.run_batch import (
    _attribute_per_message_cost,
    _count_heartbeat_turns_in_transcript,
    _usage_row_bills_no_tokens,
    _usage_row_purpose,
)
from src.utils import litellm_usage_callback as uc
from src.utils.grading import extract_usage_from_litellm_log

FIXTURE = (Path(__file__).parent / "fixtures"
           / "usage_replay_willie_run3_20260918.json")

_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
_RUN_KEY = (
    "wcb::willie_prince_6dfaf967-c57b-4a45-9c99-f43d65b8539b_claude-opus-5"
    "_20260918_2310_2afc83::b8ecb0ba69a24be0827c135d67a742af"
)


@pytest.fixture(scope="module")
def replay():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _traj(replay):
    return {"messages": [
        {"message": {"role": role, "content": [{"type": "text", "text": text}]}}
        for role, text in zip(replay["message_roles"], replay["message_texts"])
    ]}


def _write_log(tmp_path, rows, name="usage.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _rows_without_the_orphan(replay):
    return [r for r in replay["rows"] if r["ts"] != replay["zero_token_row_ts"]]


def _attribute(replay, tmp_path, *, rows=None, name="usage.jsonl", **kwargs):
    traj = _traj(replay)
    path = _write_log(tmp_path, replay["rows"] if rows is None else rows, name)
    report = _attribute_per_message_cost(
        traj, str(path), _RUN_KEY, oauth_route=True, model="claude-opus-5",
        **kwargs)
    return traj, report, path


# ============================================================================
# The run as delivered
# ============================================================================


def test_fixture_is_the_run_that_failed(replay):
    assert replay["reported_attribution"] == {
        "status": "failed", "messages": 109, "rows_selected": 151,
        "rows_internal": 41, "rows_post_agent": 0, "rows_unmatched": 1,
    }
    assert len(replay["rows"]) == 151
    assert replay["message_roles"].count("assistant") == 109


def test_the_orphan_row_billed_nothing_in_any_column(replay):
    row = next(r for r in replay["rows"] if r["ts"] == replay["zero_token_row_ts"])
    assert row["kind"] == "agent"
    assert row["model"] == "claude-opus-5"
    assert [row[c] for c in _TOKEN_COLUMNS] == [0, 0, 0, 0]
    assert row["total_tokens"] == 0
    assert row["reasoning_tokens"] == 0
    assert row["cost_usd"] == 0.0
    assert row["audio_seconds"] == 0.0
    assert row["duration_s"] == 15.002
    assert "purpose" not in row


def test_the_orphan_is_the_only_zero_token_row_in_the_run(replay):
    zero = [r for r in replay["rows"] if _usage_row_bills_no_tokens(r)]
    assert [r["ts"] for r in zero] == [replay["zero_token_row_ts"]]


def test_the_other_150_rows_all_billed_something(replay):
    billed = [r for r in replay["rows"] if not _usage_row_bills_no_tokens(r)]
    assert len(billed) == 150
    assert all(any(int(r.get(c, 0) or 0) for c in _TOKEN_COLUMNS) for r in billed)


def test_the_sidecar_had_already_labelled_every_row_it_could(replay):
    """41 internal rows, named at request time by the shipped classifier.

    The gap this closes is not a missing label — it is a row no label could
    honestly be invented for.
    """
    by_purpose: dict[str, int] = {}
    for row in replay["rows"]:
        purpose = _usage_row_purpose(row)
        if purpose:
            by_purpose[purpose] = by_purpose.get(purpose, 0) + 1
    assert by_purpose == {"embeddings": 22, "image": 17, "compaction": 2}
    assert sum(by_purpose.values()) == 41


def test_a_request_with_no_recoverable_body_is_unclassifiable(replay):
    """Why the fix could not be classifier-side, checked against the classifier.

    The tick died in the gateway before dispatch, so there is no prompt to
    fingerprint. Handed what such a request leaves behind, the classifier
    correctly declines to name it — including on the heartbeat fingerprint,
    which matches on prompt shape and has no shape to match here.
    """
    assert uc._classify_internal_purpose({}) == ""
    assert uc._classify_internal_purpose(
        {"model": "claude-opus-5", "litellm_params": {}}) == ""
    assert uc._classify_internal_purpose(
        {"litellm_params": {"proxy_server_request": {"body": {}}}}) == ""
    assert not uc._is_heartbeat_prompt([])


# ============================================================================
# The gate
# ============================================================================


def test_replay_reaches_attributed(replay, tmp_path):
    _, report, _ = _attribute(replay, tmp_path)

    assert report["status"] == "attributed"
    assert report["rows_unmatched"] == 0
    assert report["messages"] == 109
    assert report["rows_selected"] == 151
    assert report["rows_internal"] == 41
    assert report["rows_post_agent"] == 0
    assert report["rows_zero_token"] == 1


def test_without_the_split_the_same_log_reproduces_the_shipped_failure(
        replay, tmp_path, monkeypatch):
    """The counterfactual, field for field against the delivered score.json.

    Nothing about the replay changes — same 151 rows, same labels, same 109
    messages. Only the new predicate is disabled, which is exactly the build
    that shipped, and the report comes back as the artifact actually carries.
    """
    import eval.run_batch as rb

    monkeypatch.setattr(rb, "_usage_row_bills_no_tokens", lambda r: False)
    _, report, _ = _attribute(replay, tmp_path)

    assert report["status"] == "failed"
    assert report["rows_unmatched"] == 1
    assert report["rows_zero_token"] == 0
    assert {k: report[k] for k in replay["reported_attribution"]} == \
        replay["reported_attribution"]


def test_dropping_the_orphan_row_would_have_closed_it_too_and_is_not_the_fix(
        replay, tmp_path):
    """Deleting the row also makes the counts match — and loses its request.

    Kept as the contrast the bucket exists for: the split must keep the row in
    the run, not make it disappear, which is what the request_count assertion
    below pins.
    """
    _, report, path = _attribute(
        replay, tmp_path, rows=_rows_without_the_orphan(replay), name="cut.jsonl")

    assert report["status"] == "attributed"
    assert report["rows_selected"] == 150
    assert report["rows_zero_token"] == 0
    assert extract_usage_from_litellm_log(path, 0.0, 0.0, _RUN_KEY)[
        "request_count"] == 150
    assert replay["expected_agent_totals"]["request_count"] == 151


def test_replay_fills_every_assistant_message(replay, tmp_path):
    traj, _, _ = _attribute(replay, tmp_path)

    assistants = [m for m in traj["messages"]
                  if m["message"]["role"] == "assistant"]
    assert len(assistants) == 109
    for msg in assistants:
        usage = msg["message"]["usage"]
        assert usage["totalTokens"] > 0
        assert usage["cost"]["total"] > 0.0


def test_no_message_is_billed_the_orphans_zero(replay, tmp_path):
    """A 109-for-110 positional pass would have shifted every block by one."""
    traj, _, _ = _attribute(replay, tmp_path)
    billed = [r for r in replay["rows"]
              if not _usage_row_purpose(r) and not _usage_row_bills_no_tokens(r)]
    assert len(billed) == 109

    for msg, row in zip((m for m in traj["messages"]
                         if m["message"]["role"] == "assistant"), billed):
        usage = msg["message"]["usage"]
        assert usage["input"] == row["input_tokens"]
        assert usage["output"] == row["output_tokens"]
        assert usage["cacheRead"] == row["cache_read_tokens"]
        assert usage["cacheWrite"] == row["cache_write_tokens"]


# ============================================================================
# The ledger
# ============================================================================


def test_the_orphan_is_its_own_ledger_line(replay, tmp_path):
    _, report, _ = _attribute(replay, tmp_path)
    line = report["zero_token_calls"]

    assert line["request_count"] == 1
    assert [line[c] for c in _TOKEN_COLUMNS] == [0, 0, 0, 0]
    assert line["total_tokens"] == 0
    assert line["cost_usd"] == 0.0
    assert line["audio_seconds"] == 0.0


def test_the_bucket_does_not_claim_to_know_what_wrote_the_row(replay, tmp_path):
    """No heartbeat claim anywhere in the report. The row carries no evidence."""
    _, report, _ = _attribute(replay, tmp_path)

    assert set(report["zero_token_calls"]["by_purpose"]) == {"unlabelled"}
    assert "heartbeat" not in json.dumps(report)
    assert "heartbeat_turns_in_transcript" not in report
    assert _count_heartbeat_turns_in_transcript(_traj(replay)["messages"]) == 0


def test_bucketing_the_row_does_not_move_money_out_of_the_total(replay, tmp_path):
    _, _report, path = _attribute(replay, tmp_path)
    totals = extract_usage_from_litellm_log(path, 0.0, 0.0, _RUN_KEY)

    for column in (*_TOKEN_COLUMNS, "total_tokens", "request_count"):
        assert totals[column] == replay["expected_agent_totals"][column], column


def test_the_four_ledgers_add_back_up_to_sources_agent(replay, tmp_path):
    """Σ(per-message) + internal + post_agent + zero_token == sources.agent."""
    traj, report, path = _attribute(replay, tmp_path)

    per_message = {c: 0 for c in _TOKEN_COLUMNS}
    key = {"input_tokens": "input", "output_tokens": "output",
           "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    for msg in traj["messages"]:
        usage = msg["message"].get("usage")
        if usage:
            for column in _TOKEN_COLUMNS:
                per_message[column] += usage[key[column]]

    totals = extract_usage_from_litellm_log(path, 0.0, 0.0, _RUN_KEY)
    empty = {k: 0 for k in (*_TOKEN_COLUMNS, "request_count")}
    internal = report["internal_calls"]
    post = report.get("post_agent_calls") or empty
    zero = report["zero_token_calls"]

    for column in _TOKEN_COLUMNS:
        assert (per_message[column] + internal[column] + post[column]
                + zero[column]) == totals[column] \
            == replay["expected_agent_totals"][column], column
    assert (report["messages"] + internal["request_count"]
            + post["request_count"] + zero["request_count"]) \
        == totals["request_count"] == 151


def test_the_buckets_stay_disjoint(replay, tmp_path):
    """No row is billed twice: the four counts partition rows_selected."""
    _, report, _ = _attribute(replay, tmp_path)

    assert (report["messages"] + report["rows_internal"]
            + report["rows_post_agent"] + report["rows_zero_token"]) \
        == report["rows_selected"] == 151


def test_the_run_is_loud_about_the_row(replay, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="eval.run_batch"):
        _attribute(replay, tmp_path)

    warnings = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING]
    named = [m for m in warnings if "ZERO tokens" in m]
    assert len(named) == 1
    assert replay["zero_token_row_ts"] in named[0]
    assert "1 usage row(s)" in named[0]
