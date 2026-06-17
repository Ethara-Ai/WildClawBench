"""The sidecar pre-call hook that forces visible thinking text (Bug 1)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.litellm_thinking_callback import thinking_callback_instance


def _run(data, call_type="anthropic_messages"):
    return asyncio.run(
        thinking_callback_instance.async_pre_call_hook(None, None, data, call_type))


def test_forces_thinking_and_drops_reasoning_effort_on_claude():
    data = {"model": "claude-opus-4-6", "reasoning_effort": "high", "messages": []}
    out = _run(data)
    assert "reasoning_effort" not in out
    assert out["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert out["output_config"] == {"effort": "high"}
    assert out["messages"] == []  # untouched


def test_matches_opus_and_sonnet_substrings():
    for m in ("anthropic/claude-opus-4-6", "claude-sonnet-4-6", "bedrock/anthropic.claude-opus"):
        out = _run({"model": m, "reasoning_effort": "high"})
        assert out["thinking"]["display"] == "summarized" and "reasoning_effort" not in out


def test_leaves_non_claude_untouched():
    data = {"model": "gpt-5.5", "reasoning_effort": "high"}
    out = _run(data)
    assert out["reasoning_effort"] == "high"
    assert "thinking" not in out and "output_config" not in out


def test_never_raises_on_bad_input():
    assert _run("not-a-dict") == "not-a-dict"
    assert _run({}) == {}            # no model -> untouched, no crash
