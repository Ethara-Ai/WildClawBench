"""Tests for src/utils/claude_oauth/bridge.py — HTTP proxy layer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claude_oauth.bridge import (
    OAUTH_BETA,
    SYSTEM_PREFIX,
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
