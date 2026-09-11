"""Unit tests for src/utils/litellm_sanitize_callback.py.

Pins the 1P message-shape sanitizer contract:
  * HARD no-op on non-1P routes (route-scoping is the load-bearing safety
    property — mutating anthropic/Bedrock turns would invalidate thinking
    signatures and cause new 400s).
  * Drops empty-content-no-tool_calls assistant turns (poisoned history).
  * Drops assistant turns with un-parseable tool_calls arguments (truncated
    streamed tool call).
  * NEVER touches thinking-bearing turns even on the 1P route.
  * Preserves user/system/tool turns and healthy assistant turns.
  * Fail-open: any internal error returns data unchanged.

Fully offline; no litellm/docker/network. The callback subclasses a stub
CustomLogger when litellm is absent (import guard in the module), so these run
anywhere.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import litellm_sanitize_callback as sc  # noqa: E402


ONEP = "rl-muse-spark-1-2-playground"


@pytest.fixture(autouse=True)
def _scope_to_1p(monkeypatch):
    monkeypatch.setenv("KENSEI_1P_SANITIZE_MODEL", ONEP)
    monkeypatch.setenv("KENSEI_1P_SANITIZE_ENABLED", "true")


def _run(data, call_type="acompletion", model=ONEP):
    payload = dict(data)
    payload.setdefault("model", model)
    hook = sc.OnePSanitizePreCallHook()
    return asyncio.run(
        hook.async_pre_call_hook(
            user_api_key_dict=None, cache=None, data=payload, call_type=call_type
        )
    )


def _valid_toolcall(args='{"a": 1}'):
    return {
        "role": "assistant",
        "content": [],
        "tool_calls": [{"function": {"name": "exec", "arguments": args}}],
    }


class TestRouteScoping:
    def test_noop_on_non_1p_model(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": []}]
        out = _run({"messages": list(msgs)}, model="claude-opus-4.7")
        assert out["messages"] == msgs

    def test_noop_when_target_model_env_absent(self, monkeypatch):
        monkeypatch.delenv("KENSEI_1P_SANITIZE_MODEL", raising=False)
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": []}]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == msgs

    def test_matches_openai_prefixed_model_id(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": []},
            {"role": "user", "content": "again"},
        ]
        out = _run({"messages": list(msgs)}, model=f"openai/{ONEP}")
        assert {"role": "assistant", "content": []} not in out["messages"]

    def test_noop_on_embedding_call_type(self):
        msgs = [{"role": "assistant", "content": []}, {"role": "user", "content": "x"}]
        out = _run({"messages": list(msgs)}, call_type="embedding")
        assert out["messages"] == msgs

    def test_disabled_by_env_is_noop(self, monkeypatch):
        monkeypatch.setenv("KENSEI_1P_SANITIZE_ENABLED", "0")
        msgs = [{"role": "assistant", "content": []}, {"role": "user", "content": "x"}]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == msgs


class TestDropConditions:
    def test_drops_empty_content_no_toolcalls(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": []},
            {"role": "user", "content": "landed?"},
        ]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "landed?"},
        ]

    def test_drops_whitespace_string_content(self):
        msgs = [
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "x"},
        ]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == [{"role": "user", "content": "x"}]

    def test_drops_none_content(self):
        msgs = [
            {"role": "assistant", "content": None},
            {"role": "user", "content": "x"},
        ]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == [{"role": "user", "content": "x"}]

    def test_drops_truncated_toolcall_arguments(self):
        bad = _valid_toolcall(args='{"command": "python3 << PY\\nimport os')
        msgs = [{"role": "user", "content": "go"}, bad, {"role": "user", "content": "now"}]
        out = _run({"messages": list(msgs)})
        assert bad not in out["messages"]
        assert len(out["messages"]) == 2


class TestPreservation:
    def test_preserves_healthy_toolcall(self):
        good = _valid_toolcall()
        msgs = [{"role": "user", "content": "go"}, good]
        out = _run({"messages": list(msgs)})
        assert good in out["messages"]

    def test_preserves_empty_string_toolcall_arguments(self):
        good = _valid_toolcall(args="")
        msgs = [{"role": "user", "content": "go"}, good]
        out = _run({"messages": list(msgs)})
        assert good in out["messages"]

    def test_never_drops_thinking_bearing_turn(self):
        thinking = {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "reasoning..."}],
        }
        msgs = [{"role": "user", "content": "go"}, thinking]
        out = _run({"messages": list(msgs)})
        assert thinking in out["messages"]

    def test_never_drops_turn_with_signature(self):
        signed = {"role": "assistant", "content": [], "thinkingSignature": "sig-abc"}
        msgs = [{"role": "user", "content": "go"}, signed]
        out = _run({"messages": list(msgs)})
        assert signed in out["messages"]

    def test_preserves_text_block_content(self):
        keep = {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
        msgs = [{"role": "user", "content": "go"}, keep]
        out = _run({"messages": list(msgs)})
        assert keep in out["messages"]

    def test_preserves_user_and_tool_turns(self):
        msgs = [
            {"role": "user", "content": ""},
            {"role": "tool", "content": [], "tool_call_id": "c1"},
        ]
        out = _run({"messages": list(msgs)})
        assert out["messages"] == msgs

    def test_returns_original_object_when_nothing_dropped(self):
        payload = {"model": ONEP, "messages": [{"role": "user", "content": "hi"}]}
        out = _run(payload)
        assert out["messages"] == [{"role": "user", "content": "hi"}]


class TestFailOpen:
    def test_non_list_messages_is_noop(self):
        out = _run({"messages": "not-a-list"})
        assert out["messages"] == "not-a-list"

    def test_missing_messages_is_noop(self):
        out = _run({"model": ONEP})
        assert "messages" not in out or out.get("messages") is None

    def test_internal_error_returns_data(self, monkeypatch):
        def _boom(_msg):
            raise RuntimeError("synthetic")

        monkeypatch.setattr(sc, "_should_drop", _boom)
        payload = {"model": ONEP, "messages": [{"role": "assistant", "content": []}]}
        hook = sc.OnePSanitizePreCallHook()
        out = asyncio.run(
            hook.async_pre_call_hook(
                user_api_key_dict=None, cache=None, data=payload, call_type="acompletion"
            )
        )
        assert out is payload
