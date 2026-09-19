"""LiteLLM proxy PRE-CALL guard that re-shapes 1P context overflow into an
error string the openclaw CLI recognizes, unlocking its built-in compaction.

Mounted into the LiteLLM sidecar container at
/app/litellm_overflow_guard_callback.py and referenced from the proxy YAML as:

    litellm_settings:
      callbacks:
        - "litellm_usage_callback.proxy_handler_instance"
        - "litellm_overflow_guard_callback.overflow_guard_instance"

# ────────────────────────────────────────────────────────────────────────────
# INCIDENT: aleksei 1P 2026-09-06 — runs die silently at the relay ceiling
# ────────────────────────────────────────────────────────────────────────────
# The 1P relay behind https://api.ai.meta.com/v1 hard-rejects any request whose
# prompt_tokens >= 262,022 (probe: 262,012 accepted, 270,012 rejected; the
# constraint is PROMPT-ONLY — output length is irrelevant). The rejection body
# is GENERIC:
#
#   {"error": {"message": "The request contains invalid parameters. Check the
#    request body for any errors or inconsistencies.",
#    "type": "invalid_request_error", "param": null}}
#
# openclaw compacts its session ONLY when a failed request's error string
# matches its overflow matcher (substrings: "context length exceeded",
# "maximum context length", "prompt is too long", "context_window_exceeded").
# The generic relay message matches NONE of them, so the agent never compacts:
# once the session crosses the ceiling every subsequent turn 400s and the run
# dies (aleksei run_4: turns 14-16 died silently). Proactive compaction driven
# by the declared `contextWindow` in the openclaw provider config is
# empirically INERT on this path — two full runs proved it, which is why the
# fix has to live on the wire, not in the client config.
#
# This guard intercepts the request BEFORE it reaches the relay and rejects it
# with an overflow-SHAPED 400 instead, so openclaw's matcher fires, the session
# compacts, and the retry succeeds. We never let the relay produce the generic
# message for an oversized prompt in the first place.
#
# ────────────────────────────────────────────────────────────────────────────
# WHY WE `raise fastapi.HTTPException` AND NOT a returned string
# ────────────────────────────────────────────────────────────────────────────
# Verified against litellm 1.88.1 (the source inside the digest-pinned image in
# litellm_sidecar.py:21):
#
#   * litellm/proxy/utils.py:1483-1502 — the proxy invokes
#     `async_pre_call_hook(user_api_key_dict=, cache=, data=, call_type=)` with
#     those four KEYWORDS, and only for callbacks where
#     `"async_pre_call_hook" in vars(type(cb))` (leaf-class __dict__ check, also
#     at utils.py:1640 for the has_pre_call_override short-circuit). So the hook
#     MUST be defined on this class directly — an inherited one is skipped.
#   * litellm/proxy/utils.py:1522-1523 — `except Exception as e: raise e`. The
#     loop does NOT swallow: anything we raise propagates to the route handler.
#   * litellm/proxy/utils.py:941-956 `process_pre_call_hook_response` — a hook
#     that RETURNS a `str` is converted to `RejectedRequestError` for call_type
#     in ("completion", "text_completion").
#   * litellm/proxy/proxy_server.py:8550-8587 — and `RejectedRequestError` on
#     /v1/chat/completions is turned into an HTTP **200** `ModelResponse` whose
#     assistant content IS the message. That is a successful assistant turn to
#     the client, NOT an error, so openclaw's matcher would never see it. THIS
#     IS THE TRAP: returning a string (or raising RejectedRequestError) would
#     silently defeat the whole mechanism.
#   * Raising `fastapi.HTTPException` instead falls through to
#     proxy_server.py:8588 `except Exception` ->
#     common_request_processing.py:1903-1918 `_handle_llm_api_exception`, which
#     runs `_serialize_http_exception_detail` (same file:76-104) — it pulls the
#     message out of a {"error": {"message": ...}} detail verbatim — and raises
#     `ProxyException(message=<our message>, code=400)`.
#   * proxy_server.py:1262-1273 `@app.exception_handler(ProxyException)` emits
#     `JSONResponse(status_code=400, content={"error": exc.to_dict()})`, and
#     `to_dict` (proxy/_types.py:3693) carries "message" verbatim.
#
# Net: our raised message reaches the client as a 400 error body, unmodified.
#
# ────────────────────────────────────────────────────────────────────────────
# FAIL-OPEN POLICY
# ────────────────────────────────────────────────────────────────────────────
# Every path other than "model matched AND estimate exceeded the limit" MUST
# return `data` untouched. Any internal error — malformed payload, unexpected
# content shape, bad env value — logs to stderr and passes the request through.
# A guard that 400s normal traffic is far worse than one that misses an
# overflow, because the relay's own ceiling is still there as a backstop.
"""

from __future__ import annotations

import os
import sys
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass

try:
    from fastapi import HTTPException  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fastapi is a hard litellm dependency
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: Any = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail


# Real content on this model tokenizes at ~3.695 chars/token (measured over
# captured 1P prompts). We deliberately divide by a SMALLER number so the
# estimate runs ~5.6% HIGH: over-estimating fires the guard early, which costs
# one avoidable compaction; under-estimating lets the request through to the
# relay and reproduces the silent-death incident. Asymmetric risk -> round up.
_CHARS_PER_TOKEN = 3.5

_DEFAULT_MODEL_SUBSTRINGS = "rl-muse"
_DEFAULT_PROMPT_TOKEN_LIMIT = 255_000


def _model_substrings() -> list[str]:
    raw = os.environ.get("WCB_OVERFLOW_GUARD_MODELS")
    if raw is None or not raw.strip():
        raw = _DEFAULT_MODEL_SUBSTRINGS
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _prompt_token_limit() -> int:
    try:
        limit = int(os.environ.get("WCB_1P_PROMPT_TOKEN_LIMIT", "").strip()
                    or _DEFAULT_PROMPT_TOKEN_LIMIT)
    except (TypeError, ValueError):
        return _DEFAULT_PROMPT_TOKEN_LIMIT
    # A non-positive limit would reject every request; treat it as misconfig
    # and fall back rather than hard-failing the batch.
    return limit if limit > 0 else _DEFAULT_PROMPT_TOKEN_LIMIT


def _model_matches(model: Any) -> bool:
    if not isinstance(model, str) or not model:
        return False
    lowered = model.lower()
    return any(sub in lowered for sub in _model_substrings())


def _content_chars(value: Any, _depth: int = 0) -> int:
    """Character count of any OpenAI/Anthropic content payload shape.

    Handles the shapes that actually appear on the 1P chat-completions path:
      * bare string content
      * list-of-parts content: [{"type":"text","text":...}] and the Anthropic
        block variants (tool_use.input, tool_result.content — itself possibly a
        nested list of blocks, image blocks carrying base64 in source.data)
      * anything else (numbers, None) -> 0

    Depth-bounded so a self-referential payload cannot spin the hot path.
    """
    if _depth > 8:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return 0
    if isinstance(value, (list, tuple)):
        return sum(_content_chars(v, _depth + 1) for v in value)
    if isinstance(value, dict):
        # Sum every string-bearing leaf EXCEPT the structural discriminators,
        # which are fixed-size bookkeeping the model does not pay for at any
        # meaningful scale. Everything else (text, input, arguments, nested
        # tool_result content, base64 image data) is prompt payload.
        total = 0
        for key, sub in value.items():
            if key in ("type", "role", "id", "tool_use_id", "tool_call_id",
                       "cache_control", "index"):
                continue
            total += _content_chars(sub, _depth + 1)
        return total
    return 0


def estimate_prompt_tokens(data: dict) -> int:
    """Estimate the prompt tokens the relay will count for `data`.

    Counts message content (string AND list-of-parts), assistant tool_calls
    function name+arguments, tool-role results, the Anthropic-style top-level
    `system` block, and the `tools` schema — the last two are genuine prompt
    tokens on the wire, so omitting them would eat the entire ~7K-token margin
    between our 255K default limit and the relay's measured 262,022 ceiling.
    """
    chars = 0

    messages = data.get("messages")
    if isinstance(messages, (list, tuple)):
        for msg in messages:
            if isinstance(msg, str):
                chars += len(msg)
                continue
            if not isinstance(msg, dict):
                continue
            chars += _content_chars(msg.get("content"))
            # OpenAI assistant tool calls: the function arguments JSON is the
            # bulk of the payload and is NOT under "content".
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, (list, tuple)):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function")
                    if isinstance(fn, dict):
                        chars += _content_chars(fn.get("name"))
                        chars += _content_chars(fn.get("arguments"))
                    else:
                        chars += _content_chars(fn)
            # Legacy single function_call shape.
            fn_call = msg.get("function_call")
            if isinstance(fn_call, dict):
                chars += _content_chars(fn_call.get("name"))
                chars += _content_chars(fn_call.get("arguments"))

    # Anthropic-messages shape keeps the system prompt out of `messages`.
    chars += _content_chars(data.get("system"))
    # Tool schemas are re-sent on every turn and are far from negligible.
    chars += _content_chars(data.get("tools"))

    return int(chars / _CHARS_PER_TOKEN)


def _overflow_message(model: str, estimated: int, limit: int) -> str:
    # Every substring in openclaw's overflow matcher appears here verbatim:
    # "context length exceeded", "maximum context length", "prompt is too
    # long", "context_window_exceeded". ASCII only — this string crosses the
    # wire and is substring-matched by the client; no typographic dashes.
    return (
        f"context length exceeded: the prompt is too long. "
        f"This request's estimated {estimated} prompt tokens exceed the "
        f"maximum context length of {limit} tokens for model {model} "
        f"(context_window_exceeded). Reduce the conversation length - "
        f"compact or drop earlier turns - and retry."
    )


class OverflowGuard(CustomLogger):
    """Rejects oversized 1P prompts with an openclaw-recognizable 400.

    `async_pre_call_hook` is defined on THIS class (not inherited): litellm
    1.88.1 only dispatches pre-call hooks for callbacks whose leaf class
    __dict__ contains the name (proxy/utils.py:1486,1640).
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> dict | None:
        try:
            if not isinstance(data, dict):
                return data
            model = data.get("model")
            if not _model_matches(model):
                return data
            limit = _prompt_token_limit()
            estimated = estimate_prompt_tokens(data)
        except Exception as exc:  # noqa: BLE001 - fail OPEN, never block traffic
            sys.stderr.write(
                f"[litellm_overflow_guard_callback] estimation failed, passing "
                f"request through untouched: {exc!r}\n"
            )
            return data

        if estimated <= limit:
            return data

        sys.stderr.write(
            f"[litellm_overflow_guard_callback] rejecting oversized prompt: "
            f"model={model!r} call_type={call_type!r} estimated_tokens={estimated} "
            f"limit={limit} - returning context-overflow 400 so the client compacts\n"
        )
        # Shape verified against litellm 1.88.1: a dict detail of
        # {"error": {"message": ...}} is unwrapped verbatim by
        # _serialize_http_exception_detail (proxy/common_request_processing.py:76)
        # into ProxyException.message, which the ProxyException handler emits as
        # {"error": {"message": <ours>, ...}} with HTTP 400.
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": _overflow_message(str(model), estimated, limit),
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": "context_length_exceeded",
                }
            },
        )


overflow_guard_instance = OverflowGuard()
