"""Tests for src/utils/claude_oauth/bridge.py — HTTP proxy layer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claude_oauth.bridge import (
    BILLING_HEADER_PREFIX,
    OAUTH_BETA,
    SYSTEM_PREFIX,
    TOOL_NAME_PREFIX,
    apply_billing_attribution,
    iter_sse_frames,
    normalize_body_for_anthropic_direct,
    rename_tools_outbound,
    strip_tool_prefix_bytes,
    _build_forward_headers,
    _is_streaming_payload,
    _token_prefix,
    inject_system_prefix,
)


def test_inject_system_prefix_absent_creates_list():
    body = {"model": "claude-opus-4-8", "messages": []}
    out = inject_system_prefix(body)
    assert isinstance(out["system"], list)
    assert out["system"][0]["text"].startswith(SYSTEM_PREFIX)


def test_inject_system_prefix_string_prepends():
    body = {"system": "You are a helpful assistant.", "messages": []}
    out = inject_system_prefix(body)
    assert SYSTEM_PREFIX in out["system"]
    assert "You are a helpful assistant." in out["system"]


def test_inject_system_prefix_idempotent_string():
    body = {"system": f"{SYSTEM_PREFIX}\n\nExtra rules.", "messages": []}
    out = inject_system_prefix(body)
    assert out["system"].count(SYSTEM_PREFIX) == 1


def test_inject_system_prefix_list_of_blocks_prepends_new_leading_block():
    """Kaiju HEAD (297fa49) approach: prepend SYSTEM_PREFIX as new leading text block.

    Earlier port used concatenate-into-first-block to preserve cache boundaries,
    but kaiju's production bridge empirically works with prepend and treats the
    anchored startswith idempotency check on first_text as sufficient. The
    prepend approach is safer because cache_control markers on existing blocks
    are preserved verbatim (only their positional index shifts by 1).
    """
    body = {
        "system": [
            {"type": "text", "text": "You are the Claude Code CLI helper."},
            {"type": "text", "text": "Follow these rules...", "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [],
    }
    out = inject_system_prefix(body)
    assert isinstance(out["system"], list)
    assert len(out["system"]) == 3, "must prepend SYSTEM_PREFIX as new leading block"
    assert out["system"][0]["type"] == "text"
    assert out["system"][0]["text"] == SYSTEM_PREFIX
    # Original blocks preserved verbatim (index shifted, cache_control intact)
    assert out["system"][1]["text"] == "You are the Claude Code CLI helper."
    assert out["system"][2].get("cache_control", {}).get("type") == "ephemeral"


def test_inject_system_prefix_list_no_text_block_falls_back_to_prepend():
    body = {"system": [{"type": "image", "source": "url"}], "messages": []}
    out = inject_system_prefix(body)
    assert isinstance(out["system"], list)
    assert out["system"][0]["type"] == "text"
    assert out["system"][0]["text"] == SYSTEM_PREFIX


def test_inject_system_prefix_list_idempotent():
    body = {
        "system": [
            {"type": "text", "text": f"{SYSTEM_PREFIX} plus other content"},
        ],
        "messages": [],
    }
    out = inject_system_prefix(body)
    assert out["system"][0]["text"].count(SYSTEM_PREFIX) == 1


def test_is_streaming_payload_detects_stream_flag():
    assert _is_streaming_payload(b'{"stream":true,"model":"x"}')
    assert _is_streaming_payload(b'{"stream": true,"model":"x"}')
    assert not _is_streaming_payload(b'{"stream":false,"model":"x"}')
    assert not _is_streaming_payload(b'{"model":"x"}')


def test_build_forward_headers_strips_incoming_auth():
    incoming = {
        "host": "wcbsh-cc-bridge-abc:8765",
        "authorization": "Bearer old-litellm-key",
        "x-api-key": "should-be-stripped",
        "content-type": "application/json",
        "x-custom-header": "keep-me",
    }
    out = _build_forward_headers(incoming, "actual-oauth-token")
    assert out["Authorization"] == "Bearer actual-oauth-token"
    assert "x-api-key" not in {k.lower() for k in out}
    assert "host" not in {k.lower() for k in out}
    assert out.get("x-custom-header") == "keep-me"
    assert OAUTH_BETA in out.get("anthropic-beta", "")


def test_build_forward_headers_merges_existing_beta():
    incoming = {"anthropic-beta": "prompt-caching-2024-07-31"}
    out = _build_forward_headers(incoming, "tok")
    beta = out["anthropic-beta"]
    assert OAUTH_BETA in beta
    assert "prompt-caching-2024-07-31" in beta


def test_build_forward_headers_sets_version_only_if_absent():
    incoming = {"anthropic-version": "2024-10-01"}
    out = _build_forward_headers(incoming, "tok")
    assert out["anthropic-version"] == "2024-10-01"

    out2 = _build_forward_headers({}, "tok")
    assert out2["anthropic-version"] == "2023-06-01"


def test_token_prefix_returns_first_20_chars():
    assert _token_prefix("sk-oat-1234567890abcdefghijklmnop") == "sk-oat-1234567890abc"
    assert _token_prefix("") == ""


def _prep(body):
    body = inject_system_prefix(body)
    return apply_billing_attribution(body, b'{"x":1}')


def test_billing_attribution_prepends_billing_block_as_system0():
    out = _prep({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert out["system"][0]["text"].startswith(BILLING_HEADER_PREFIX)
    assert "cc_entrypoint=cli" in out["system"][0]["text"]


def test_billing_attribution_keeps_only_billing_and_identity_in_system():
    body = {
        "system": [
            {"type": "text", "text": SYSTEM_PREFIX},
            {"type": "text", "text": "A" * 30000},
            {"type": "text", "text": "More harness rules."},
        ],
        "messages": [{"role": "user", "content": "do the task"}],
    }
    out = apply_billing_attribution(body, b"body")
    assert len(out["system"]) == 2
    assert out["system"][0]["text"].startswith(BILLING_HEADER_PREFIX)
    assert out["system"][1]["text"] == SYSTEM_PREFIX


def test_billing_attribution_relocates_bulk_prompt_into_first_user_message():
    body = {
        "system": [
            {"type": "text", "text": SYSTEM_PREFIX},
            {"type": "text", "text": "BULK_HARNESS_PROMPT"},
        ],
        "messages": [{"role": "user", "content": "original task"}],
    }
    out = apply_billing_attribution(body, b"body")
    assert "BULK_HARNESS_PROMPT" in out["messages"][0]["content"]
    assert "original task" in out["messages"][0]["content"]


def test_billing_attribution_relocates_into_array_content():
    body = {
        "system": [
            {"type": "text", "text": SYSTEM_PREFIX},
            {"type": "text", "text": "BULK"},
        ],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "task"}]}],
    }
    out = apply_billing_attribution(body, b"body")
    content = out["messages"][0]["content"]
    assert content[0]["text"] == "BULK"
    assert content[1]["text"] == "task"


def test_billing_attribution_synthesizes_user_message_if_none():
    body = {
        "system": [
            {"type": "text", "text": SYSTEM_PREFIX},
            {"type": "text", "text": "BULK"},
        ],
        "messages": [],
    }
    out = apply_billing_attribution(body, b"body")
    assert out["messages"][0]["role"] == "user"
    assert "BULK" in out["messages"][0]["content"]


def test_billing_attribution_idempotent():
    out1 = _prep({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    out2 = apply_billing_attribution(out1, b'{"x":1}')
    assert [b["text"] for b in out1["system"]] == [b["text"] for b in out2["system"]]
    billing_blocks = [
        b for b in out2["system"]
        if b["text"].startswith(BILLING_HEADER_PREFIX)
    ]
    assert len(billing_blocks) == 1


def test_rename_tools_outbound_prefixes_tool_declarations():
    body = {
        "tools": [
            {"name": "read", "input_schema": {}, "description": "d"},
            {"name": "exec", "input_schema": {}},
        ],
        "messages": [],
    }
    out = rename_tools_outbound(body)
    assert out["tools"][0]["name"] == TOOL_NAME_PREFIX + "read"
    assert out["tools"][1]["name"] == TOOL_NAME_PREFIX + "exec"
    assert out["tools"][0]["description"] == "d"
    assert out["tools"][0]["input_schema"] == {}


def test_rename_tools_outbound_prefixes_assistant_tool_use_in_history():
    body = {
        "tools": [],
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "t1", "name": "cron", "input": {}},
            ]},
        ],
    }
    out = rename_tools_outbound(body)
    tu = out["messages"][1]["content"][1]
    assert tu["name"] == TOOL_NAME_PREFIX + "cron"
    assert tu["id"] == "t1"


def test_rename_tools_outbound_idempotent():
    body = {"tools": [{"name": "read", "input_schema": {}}], "messages": []}
    once = rename_tools_outbound(body)
    twice = rename_tools_outbound(once)
    assert twice["tools"][0]["name"] == TOOL_NAME_PREFIX + "read"
    assert not twice["tools"][0]["name"].startswith(TOOL_NAME_PREFIX + TOOL_NAME_PREFIX)


def test_rename_tools_outbound_15_tools_all_prefixed():
    names = ["read", "edit", "write", "exec", "process", "cron",
             "sessions_list", "sessions_history", "sessions_send",
             "sessions_spawn", "subagents", "session_status", "image",
             "memory_search", "memory_get"]
    body = {"tools": [{"name": n, "input_schema": {}} for n in names], "messages": []}
    out = rename_tools_outbound(body)
    assert all(t["name"].startswith(TOOL_NAME_PREFIX) for t in out["tools"])
    assert len(out["tools"]) == 15


def test_strip_tool_prefix_bytes_json_response():
    raw = json.dumps({
        "type": "message",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__wcb__read", "input": {}},
            {"type": "text", "text": "hi"},
        ],
    }).encode("utf-8")
    out = strip_tool_prefix_bytes(raw)
    parsed = json.loads(out)
    assert parsed["content"][0]["name"] == "read"


def test_strip_tool_prefix_bytes_sse_response():
    sse = (
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"tool_use","id":"t1","name":"mcp__wcb__exec"}}\n\n'
    )
    out = strip_tool_prefix_bytes(sse)
    assert b'"name":"exec"' in out
    assert b"mcp__wcb__" not in out


def test_strip_tool_prefix_bytes_untouched_when_no_prefix():
    raw = b'{"content":[{"type":"text","text":"just text"}]}'
    assert strip_tool_prefix_bytes(raw) == raw
    assert strip_tool_prefix_bytes(b"") == b""


# ---------------------------------------------------------------------------
# normalize_body_for_anthropic_direct — fable branch
# ---------------------------------------------------------------------------


def test_normalize_fable_rewrites_adaptive_with_display():
    body = {"model": "claude-fable-5", "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"}, "messages": []}
    out = normalize_body_for_anthropic_direct(body)
    assert out["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "output_config" not in out


def test_normalize_fable_strips_budget_tokens_shape():
    # The opus fixed-budget rewrite 400s on fable; any enabled+budget shape
    # must come out as adaptive.
    body = {"model": "anthropic/claude-fable-5",
            "thinking": {"type": "enabled", "budget_tokens": 32000}, "messages": []}
    out = normalize_body_for_anthropic_direct(body)
    assert out["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_normalize_fable_preserves_omitted_display_and_injects_when_absent():
    body = {"model": "claude-fable-5",
            "thinking": {"type": "adaptive", "display": "omitted"}, "messages": []}
    assert normalize_body_for_anthropic_direct(body)["thinking"] == {
        "type": "adaptive", "display": "omitted"}
    # Absent thinking gets adaptive+summarized injected: openclaw's allowlist
    # doesn't recognize fable so it sends no directive, and fable's upstream
    # display default ("omitted") would record EMPTY thinking text.
    body2 = {"model": "claude-fable-5", "messages": []}
    assert normalize_body_for_anthropic_direct(body2)["thinking"] == {
        "type": "adaptive", "display": "summarized"}


def test_normalize_opus_adaptive_stays_adaptive_summarized():
    body = {"model": "claude-opus-4-8",
            "thinking": {"type": "adaptive"}, "messages": []}
    out = normalize_body_for_anthropic_direct(body)
    assert out["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_normalize_opus_enabled_rewritten_to_adaptive_and_drops_budget():
    body = {"model": "claude-opus-4-8",
            "thinking": {"type": "enabled", "budget_tokens": 32000}, "messages": []}
    out = normalize_body_for_anthropic_direct(body)
    assert out["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "budget_tokens" not in out["thinking"]


def test_normalize_opus_preserves_omitted_display():
    body = {"model": "claude-opus-4-8",
            "thinking": {"type": "adaptive", "display": "omitted"}, "messages": []}
    out = normalize_body_for_anthropic_direct(body)
    assert out["thinking"] == {"type": "adaptive", "display": "omitted"}


def test_normalize_opus_idempotent_byte_equal():
    body = {"model": "claude-opus-4-8",
            "thinking": {"type": "enabled", "budget_tokens": 1000}, "messages": []}
    once = normalize_body_for_anthropic_direct(body)
    twice = normalize_body_for_anthropic_direct(dict(once))
    assert once["thinking"] == twice["thinking"] == {
        "type": "adaptive", "display": "summarized"}


def test_iter_sse_frames_empty_payload():
    assert iter_sse_frames(b"") == []


def test_iter_sse_frames_single_complete_frame_roundtrips():
    frame = b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n"
    frames = iter_sse_frames(frame)
    assert frames == [frame]
    assert b"".join(frames) == frame


def test_iter_sse_frames_splits_usage_bearing_frames_discretely():
    # The whole point of the fix: message_start (input_tokens) and
    # message_delta (output_tokens) must emerge as SEPARATE frames so
    # LiteLLM's Anthropic parser recovers both usage objects instead of
    # mis-splitting one utf-8 blob at a chunk seam.
    start = b"event: message_start\ndata: {\"usage\":{\"input_tokens\":42}}\n\n"
    delta = b"event: message_delta\ndata: {\"usage\":{\"output_tokens\":7}}\n\n"
    stop = b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
    frames = iter_sse_frames(start + delta + stop)
    assert frames == [start, delta, stop]
    assert b"".join(frames) == start + delta + stop


def test_iter_sse_frames_preserves_trailing_partial_frame():
    # A tail without the closing \n\n is kept verbatim (no fabricated
    # terminator) so nothing is silently dropped or corrupted.
    payload = b"event: a\ndata: 1\n\nevent: b\ndata: 2"
    frames = iter_sse_frames(payload)
    assert frames == [b"event: a\ndata: 1\n\n", b"event: b\ndata: 2"]
    assert b"".join(frames) == payload


def test_iter_sse_frames_is_lossless():
    payload = (b"event: message_start\ndata: {\"a\":1}\n\n"
               b"event: ping\n\n"
                b"event: message_delta\ndata: {\"b\":2}\n\n")
    assert b"".join(iter_sse_frames(payload)) == payload


def test_forward_headers_add_oauth_beta_no_interleaved():
    fwd = _build_forward_headers({}, "tok-abc")
    betas = [b.strip() for b in fwd["anthropic-beta"].split(",")]
    assert OAUTH_BETA in betas
    assert "interleaved-thinking-2025-05-14" not in betas
    assert fwd["Authorization"] == "Bearer tok-abc"


def test_forward_headers_merge_caller_beta_not_replace():
    fwd = _build_forward_headers(
        {"anthropic-beta": "prompt-caching-2024-07-31"}, "tok"
    )
    betas = [b.strip() for b in fwd["anthropic-beta"].split(",")]
    assert "prompt-caching-2024-07-31" in betas
    assert OAUTH_BETA in betas


def test_forward_headers_oauth_beta_idempotent_no_duplicates():
    fwd = _build_forward_headers({"anthropic-beta": OAUTH_BETA}, "tok")
    betas = [b.strip() for b in fwd["anthropic-beta"].split(",")]
    assert betas.count(OAUTH_BETA) == 1


def test_forward_headers_strip_x_cc_atis_and_prefix():
    incoming = {
        "x-cc-atis": "attic-parcel-meridian",
        "x-cc-experiment": "signal-quartz-banner",
        "content-type": "application/json",
    }
    out = _build_forward_headers(incoming, "tok")
    lowered = {k.lower() for k in out}
    assert "x-cc-atis" not in lowered
    assert "x-cc-experiment" not in lowered
