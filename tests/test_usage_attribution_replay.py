"""Offline replay of the 2026-09-18 sean_callahan run that failed the count gate.

That run shipped ``usage_attribution: {"status": "failed", "rows_unmatched": 26}``
and 129 assistant messages with no cost block, because openclaw's memory
embeddings and its image tool each put rows in the sidecar usage log that no
assistant message can claim. The fixture holds that run's REAL usage rows (the
156 the harness read) and its REAL message roles; this drives them through the
classifier and the attribution path as they now stand and requires the gate to
close.

The request shapes are reconstructed per row from the agent bundle's own call
builders — the usage log records tokens, not prompts — and the reconstruction
is what ``_classify_internal_purpose`` is handed. The token numbers, the row
count, the message count and the expected totals are all the run's own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_batch import _attribute_per_message_cost
from src.utils import litellm_usage_callback as uc

FIXTURE = Path(__file__).parent / "fixtures" / "usage_replay_sean_20260918.json"

_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
_RUN_KEY = (
    "wcb::sean_callahan_adcf8833-1280-4149-a54c-a2c565af2277_claude-opus-5"
    "_20260918_0614_321e30::9f630469563c4053aac4868334428b55"
)


@pytest.fixture(scope="module")
def replay():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _kwargs_for(row, image_ts):
    """The request that produced ``row``, in the shape its caller builds it.

    embeddings — extensions/memory-lancedb/index.ts posts {model, input} to
      /v1/embeddings; litellm reports the call under an embedding call_type.
    image      — src/agents/tools/image-tool.ts::buildImageContext sends one
      system-less user message of [text block, image block...].
    compaction — pi-coding-agent's SUMMARIZATION_SYSTEM_PROMPT.
    turn       — the conversation so far under the agent's system prompt.
    """
    if "embedding" in row.get("model", ""):
        return {"call_type": "aembedding", "model": row["model"],
                "litellm_params": {"proxy_server_request": {"body": {
                    "model": row["model"], "input": "bench 06 shoulder"}}}}
    if row["ts"] in image_ts:
        return {"litellm_params": {"proxy_server_request": {"body": {
            "system": "",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "read the bench cards in these photos"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": "QUJD"}},
            ]}]}}}}
    if row.get("purpose") == "compaction":
        return {"messages": [
            {"role": "system", "content": uc._COMPACTION_SYSTEM_HEAD},
            {"role": "user", "content": "<conversation>"},
        ]}
    return {"messages": [
        {"role": "system", "content": "You are Sean's workshop assistant."},
        {"role": "user", "content": "mark the Saturday cohort"},
        {"role": "assistant", "content": "reading the sign-in sheet"},
        {"role": "user", "content": "carry on"},
    ]}


def _classified_rows(replay):
    image_ts = set(replay["image_tool_row_ts"])
    out = []
    for row in replay["rows"]:
        fresh = {k: v for k, v in row.items() if k != "purpose"}
        purpose = uc._classify_internal_purpose(_kwargs_for(row, image_ts))
        if purpose:
            fresh["purpose"] = purpose
        out.append(fresh)
    return out


def _traj(replay):
    return {"messages": [{"message": {"role": role, "content": ""}}
                         for role in replay["message_roles"]]}


def _write_log(tmp_path, rows):
    path = tmp_path / "usage.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(path)


def test_fixture_is_the_run_that_failed(replay):
    assert replay["reported_attribution"] == {
        "status": "failed", "messages": 129,
        "rows_selected": 156, "rows_internal": 1, "rows_unmatched": 26,
    }
    assert len(replay["rows"]) == 156
    assert replay["message_roles"].count("assistant") == 129


def test_every_surplus_row_is_now_labelled(replay):
    rows = _classified_rows(replay)
    labelled = [r for r in rows if r.get("purpose")]
    assert len(labelled) == 27
    by_purpose = {}
    for r in labelled:
        by_purpose.setdefault(r["purpose"], []).append(r)
    assert {k: len(v) for k, v in by_purpose.items()} == {
        "embeddings": 22, "image": 4, "compaction": 1,
    }


def test_the_turns_are_left_unlabelled(replay):
    rows = _classified_rows(replay)
    assert len([r for r in rows if not r.get("purpose")]) == 129


def test_replay_reaches_attributed(replay, tmp_path):
    traj = _traj(replay)
    report = _attribute_per_message_cost(
        traj, _write_log(tmp_path, _classified_rows(replay)), _RUN_KEY,
        oauth_route=True, model="claude-opus-5")

    assert report["status"] == "attributed"
    assert report["rows_unmatched"] == 0
    assert report["messages"] == 129
    assert report["rows_selected"] == 156
    assert report["rows_internal"] == 27


def test_replay_fills_every_assistant_message(replay, tmp_path):
    traj = _traj(replay)
    _attribute_per_message_cost(
        traj, _write_log(tmp_path, _classified_rows(replay)), _RUN_KEY,
        oauth_route=True, model="claude-opus-5")

    assistants = [m for m in traj["messages"]
                  if m["message"]["role"] == "assistant"]
    assert len(assistants) == 129
    for msg in assistants:
        usage = msg["message"]["usage"]
        assert usage["totalTokens"] > 0
        assert usage["cost"]["total"] > 0.0


def test_replay_reconciles_with_the_run_agent_total(replay, tmp_path):
    """Every row sources.agent counted is now either on a message or in the
    internal ledger, and the two add back up to it."""
    traj = _traj(replay)
    report = _attribute_per_message_cost(
        traj, _write_log(tmp_path, _classified_rows(replay)), _RUN_KEY,
        oauth_route=True, model="claude-opus-5")

    per_message = {k: 0 for k in _TOKEN_COLUMNS}
    key = {"input_tokens": "input", "output_tokens": "output",
           "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    for msg in traj["messages"]:
        usage = msg["message"].get("usage")
        if usage:
            for col in _TOKEN_COLUMNS:
                per_message[col] += usage[key[col]]

    internal = report["internal_calls"]
    expected = replay["expected_agent_totals"]
    for col in _TOKEN_COLUMNS:
        assert per_message[col] + internal[col] == expected[col], col

    assert report["messages"] + internal["request_count"] == expected["request_count"]
