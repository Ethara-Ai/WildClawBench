"""LiteLLM proxy pre-call hook that repairs malformed message shapes on the
first-party (1P / `meta_model`) relay route before LiteLLM dispatches upstream.

Mounted into the LiteLLM sidecar container at /app/litellm_sanitize_callback.py
and referenced from the proxy YAML as:

    litellm_settings:
      callbacks:
        - "litellm_sanitize_callback.sanitize_callback_instance"
        - "litellm_usage_callback.proxy_handler_instance"
        - "litellm_headroom_callback.headroom_callback_instance"

# ────────────────────────────────────────────────────────────────────────────
# WHY THIS EXISTS
# ────────────────────────────────────────────────────────────────────────────
# The 1P relay (registered as `model: openai/{meta_model}` in
# litellm_sidecar.py) 400s ("The request contains invalid parameters. Check the
# request body for any errors or inconsistencies.") when the outgoing
# `messages` array carries a malformed assistant turn:
#
#   (a) an assistant turn truncated MID-tool-call (openclaw hit finish_reason
#       "length" while streaming a tool_call) whose
#       `tool_calls[].function.arguments` is not parseable JSON, OR
#   (b) an assistant turn persisted with empty content and no tool_calls
#       (the poisoned-history row left behind by a prior 400), which replays
#       forever and turns any transient 400 into a terminal loop.
#
# LiteLLM does NO assistant-message normalization on the `openai/` provider
# path. `sanitize_messages_for_tool_calling` (litellm .../factory.py) is wired
# ONLY into the anthropic path; the openai path assumes providers "silently
# tolerate empty content" — false for this relay. This callback is the only
# place the harness can see the request AFTER openclaw builds it and BEFORE it
# leaves the sidecar, so the repair lives here.
#
# ────────────────────────────────────────────────────────────────────────────
# ROUTE-SCOPING (load-bearing — DO NOT make this global)
# ────────────────────────────────────────────────────────────────────────────
# The LiteLLM callback list is GLOBAL across every model route (opus/Bedrock,
# gpt-5.6, judge council, 1P). Dropping/altering messages on the anthropic or
# Bedrock routes is DANGEROUS: those turns carry a signed `thinkingSignature`
# computed over the original text, and mutating them invalidates the signature
# on multi-turn continuations → NEW downstream 400s (the exact class the
# headroom callback guards against at litellm_headroom_callback.py:297-304).
# Therefore this hook is a HARD no-op unless the request's `model` matches the
# configured 1P model id (KENSEI_1P_SANITIZE_MODEL). opus/judge/gpt-5.6 traffic
# passes through UNTOUCHED.
#
# ────────────────────────────────────────────────────────────────────────────
# FAIL-OPEN POLICY (production-safety, mirrors headroom callback)
# ────────────────────────────────────────────────────────────────────────────
# Any exception in this module MUST result in the original `data` dict being
# returned unmodified. Sanitization is a correctness aid, NEVER a hard
# dependency — a bug here must never break a request. It also writes NOTHING to
# any JSONL sink (usage.jsonl / headroom JSONL / usage_oauth.jsonl stay
# untouched — three-sinks-never-merged invariant).
#
# ────────────────────────────────────────────────────────────────────────────
# THINKING-SAFETY
# ────────────────────────────────────────────────────────────────────────────
# Even on the 1P route this hook NEVER touches a thinking-bearing assistant
# turn. A thinking block is legitimate content; only content-empty AND
# tool_call-empty AND thinking-empty assistant turns are dropped.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _enabled() -> bool:
    # Default-ON when mounted; the mount itself is the opt-in (the harness only
    # mounts this module for 1P runs). An explicit "0"/"false" still disables.
    raw = os.environ.get("KENSEI_1P_SANITIZE_ENABLED")
    if raw is None:
        return True
    return _truthy(raw)


def _target_models() -> set[str]:
    # Comma-separated 1P model ids this hook is allowed to sanitize. Empty ⇒ the
    # hook cannot safely scope, so it no-ops on everything (fail-safe).
    raw = os.environ.get("KENSEI_1P_SANITIZE_MODEL", "") or ""
    return {m.strip() for m in raw.split(",") if m.strip()}


def _in_scope(model: str, targets: set[str]) -> bool:
    if not model or not targets:
        return False
    if model in targets:
        return True
    # openclaw may send the raw id while the relay registers `openai/{id}`;
    # match on the trailing segment too so both spellings are covered.
    tail = model.split("/")[-1]
    return any(tail == t.split("/")[-1] for t in targets)


def _has_thinking_block(message: dict) -> bool:
    """True if the assistant turn carries a reasoning/thinking block.

    Anthropic-shaped block content: content is a list of {"type": ...} dicts.
    A `thinking`/`reasoning`/`redacted_thinking` block, or a top-level
    `reasoning_content`/`thinkingSignature`/`signature`, marks the turn as
    reasoning-bearing and therefore signature-load-bearing — never touch it.
    """
    if not isinstance(message, dict):
        return False
    for key in ("reasoning_content", "thinking", "thinkingSignature", "signature"):
        if message.get(key):
            return True
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                "thinking",
                "reasoning",
                "redacted_thinking",
            ):
                return True
    return False


def _content_is_empty(content: Any) -> bool:
    """True if the assistant content carries no usable text/blocks.

    Handles both OpenAI-shaped string content and Anthropic-shaped block lists.
    A missing key, None, empty string/whitespace, empty list, or a block list
    whose every block is an empty/whitespace text block all count as empty.
    """
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        if not content:
            return True
        for block in content:
            if not isinstance(block, dict):
                # Unknown non-dict block — treat as real content, do not drop.
                return False
            btype = block.get("type")
            if btype in (None, "text"):
                if (block.get("text") or "").strip() != "":
                    return False
            else:
                # image / tool_use / tool_result / thinking / anything else =
                # real content.
                return False
        return True
    # Unknown content shape — be conservative, treat as non-empty.
    return False


def _tool_calls_valid(message: dict) -> bool:
    """True unless the assistant turn has a tool_calls entry whose
    function.arguments is a non-empty string that fails json.loads.

    A truncated streamed tool call (finish_reason "length" mid-arguments) yields
    an unterminated JSON string here; that is the shape the relay 400s on.
    """
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return True
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if not isinstance(args, str):
            # Non-string args (already-parsed dict, or absent) — not the
            # malformed-stream shape we target.
            continue
        if args.strip() == "":
            # Empty-string args is a valid "no arguments" call.
            continue
        try:
            json.loads(args)
        except Exception:
            return False
    return True


def _should_drop(message: dict) -> bool:
    """Drop this assistant message only if it is unambiguously malformed.

    Two drop conditions, both narrow:
      1. content empty AND no tool_calls AND not thinking-bearing (poisoned
         empty-history row).
      2. tool_calls present with un-parseable JSON arguments (truncated
         streamed tool call).
    Everything else is preserved.
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "assistant":
        return False
    if _has_thinking_block(message):
        return False  # signature-load-bearing; never touch.

    has_tool_calls = bool(message.get("tool_calls"))

    # Condition 2: malformed (truncated) tool_calls arguments.
    if has_tool_calls and not _tool_calls_valid(message):
        return True

    # Condition 1: empty content and no tool_calls.
    if not has_tool_calls and _content_is_empty(message.get("content")):
        return True

    return False


class OnePSanitizePreCallHook(CustomLogger):
    """Repairs malformed 1P-route messages in-place before LiteLLM dispatch.

    Subclasses LiteLLM's `CustomLogger`. Implements ONLY `async_pre_call_hook`
    using LiteLLM's canonical 4-arg keyword signature
    `(user_api_key_dict, cache, data, call_type)`. Does NOT implement
    `async_log_success_event`, so it does not contend with the usage callback
    in the pre-call OR post-call phase and touches no JSONL sink.

    NEVER raises into LiteLLM's pre-call dispatch loop. Any failure returns the
    original `data` dict unchanged (fail-open). HARD no-op on any route whose
    `model` is not the configured 1P model id.
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> dict | None:
        try:
            # openclaw drives the agent over the Anthropic Messages API
            # (call_type "anthropic_messages") as well as plain completions;
            # cover both. Anything else (embeddings, transcription) is skipped.
            if call_type not in ("completion", "acompletion", "anthropic_messages"):
                return data
            if not _enabled():
                return data

            if not isinstance(data, dict):
                return data
            model = data.get("model", "") or ""
            targets = _target_models()
            if not _in_scope(model, targets):
                return data  # opus / judge / gpt-5.6 / mis-configured → untouched

            messages = data.get("messages")
            if not isinstance(messages, list) or not messages:
                return data

            kept: list[Any] = []
            dropped = 0
            for m in messages:
                if isinstance(m, dict) and _should_drop(m):
                    dropped += 1
                    continue
                kept.append(m)

            # Never leave the request empty; if every message would be dropped
            # (should be impossible — the current user turn is not assistant),
            # bail out and send the original untouched.
            if dropped == 0 or not kept:
                return data

            data["messages"] = kept
            sys.stderr.write(
                f"[litellm_sanitize_callback] dropped {dropped} malformed "
                f"assistant message(s) on 1P route model={model!r}\n"
            )
            return data
        except Exception as exc:  # pragma: no cover - fail-open guard
            sys.stderr.write(
                f"[litellm_sanitize_callback] pre-call hook raised, sending "
                f"request unmodified: {exc!r}\n"
            )
            return data


# LiteLLM proxy loads `<module>.<attr>` from the callbacks list; this is the
# instance it binds. Name mirrors the usage/headroom callback instances.
sanitize_callback_instance = OnePSanitizePreCallHook()
