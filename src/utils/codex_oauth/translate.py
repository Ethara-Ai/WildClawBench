"""Chat Completions <-> Responses API translation for the Codex bridge.

The ChatGPT/Codex backend only speaks the Responses API. Many OpenAI clients
(litellm without responses-mode registration, the harness preflight probe, the
openai SDK's `.chat.completions`) speak Chat Completions. This module translates
both ways so the bridge is a drop-in `/v1/chat/completions` endpoint too:

    chat request  --chat_to_responses-->  responses request  (to codex backend)
    responses SSE --responses_sse_to_chat_sse--> chat SSE      (streaming client)
    responses obj --responses_to_chat-->  chat.completion obj  (unary client)

Only what the rust/coding pipeline needs is mapped richly (system->instructions,
messages->input, text output, usage); unsupported fields are dropped rather than
forwarded (the codex backend is strict).
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional


def _content_to_text(content: Any) -> str:
    """Flatten a chat message `content` (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("text", "input_text", "output_text"):
                    out.append(part.get("text", ""))
                elif "text" in part:
                    out.append(part["text"])
        return "".join(out)
    return "" if content is None else str(content)


# Chat `image_url` part -> Responses `input_image` part.
#
# WHY THIS EXISTS: `_content_to_text` above is a text flattener and DROPS image
# parts entirely. The GPT rubric judge sends its evidence images as Chat
# Completions `{"type":"image_url","image_url":{"url":<data-uri>,"detail":...}}`
# parts (src/utils/grading.py `_call_judge_openai`), so without this mapping the
# codex-routed judge would grade image-BLIND with nothing in the output naming the
# loss — the silent no-signal failure class (AGENTS.md #18).
#
# Responses shape differs from Chat in two ways: the type is `input_image` and
# `image_url` is a BARE STRING (not an object), with `detail` as a sibling field.
_DATA_URI_PREFIX = "data:"


def _is_image_part(part: Any) -> bool:
    """True for a Chat `image_url` content part that carries an `image_url` field.

    A `{"type":"image_url","text":...}` part with NO `image_url` key (or a null
    one) is not treated as an image: it stays on the text-collapse path
    `_content_to_text` already handles, so this predicate never turns a malformed
    text part into a hard failure. A part that DOES carry `image_url` but whose
    url is missing/empty is a malformed IMAGE and is deliberately routed to
    `_image_part_to_input_image`'s fail-loud raise rather than silently dropped.
    """
    return (
        isinstance(part, dict)
        and part.get("type") == "image_url"
        and part.get("image_url") is not None
    )


def _image_part_to_input_image(part: dict) -> dict:
    """Map one Chat `image_url` part to a Responses `input_image` part.

    FAIL-LOUD on a non-inline url: the codex/ChatGPT backend rejects remote
    http(s) image references (REMOTE_IMAGE_URL_ERROR), so a silent pass-through
    would surface as an opaque upstream 4xx mid-turn. This module runs on the
    bridge/preflight surface, never inside the grade path, so raising here is the
    correct place to fail (grading itself must always degrade, never raise).
    """
    raw = part.get("image_url")
    if isinstance(raw, dict):
        url = raw.get("url")
        detail = raw.get("detail")
    else:
        url, detail = raw, part.get("detail")
    if not isinstance(url, str) or not url:
        raise ValueError(
            "chat_to_responses: image_url part carries no url string "
            f"(got {type(url).__name__})"
        )
    if not url.startswith(_DATA_URI_PREFIX):
        raise ValueError(
            "chat_to_responses: only inline data: image URIs are supported; the "
            f"codex backend rejects remote image URLs (got {url[:64]!r})"
        )
    out: dict = {"type": "input_image", "image_url": url}
    if isinstance(detail, str) and detail:
        out["detail"] = detail
    return out


def _content_to_input_parts(content: Any) -> list[dict]:
    """Build the Responses `content` parts list for a USER-role turn.

    PURE-TEXT PATH IS UNCHANGED: with no image parts this returns exactly the
    single `[{"type":"input_text","text":<flattened>}]` the caller built before,
    including the empty-text case. Images are appended AFTER the text part, in
    document order, mirroring the Chat body the judge sends.
    """
    text = _content_to_text(content)
    images = (
        [_image_part_to_input_image(p) for p in content if _is_image_part(p)]
        if isinstance(content, list)
        else []
    )
    if not images:
        return [{"type": "input_text", "text": text}]
    parts: list[dict] = []
    # An empty text part alongside images is noise the strict backend does not
    # need; the pure-text path above still emits it for byte-compatibility.
    if text:
        parts.append({"type": "input_text", "text": text})
    parts.extend(images)
    return parts


def chat_to_responses(chat: dict) -> dict:
    """Translate a Chat Completions request body into a Responses request body.

    - system/developer messages are concatenated into `instructions`.
    - user/assistant/tool messages become Responses `input` items (user & tool ->
      input_text, assistant -> output_text).
    - user-role `image_url` parts become `input_image` parts (see
      `_image_part_to_input_image`); a non-`data:` url raises.
    - max_tokens/max_completion_tokens -> max_output_tokens.
    - stream and tools pass through; sampling params the codex backend rejects are
      dropped.
    """
    out: dict = {"model": chat.get("model")}

    instructions: list[str] = []
    input_items: list[dict] = []
    for msg in chat.get("messages", []) or []:
        role = msg.get("role")
        text = _content_to_text(msg.get("content"))
        if role in ("system", "developer"):
            if text:
                instructions.append(text)
        elif role == "assistant":
            input_items.append({"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": text}]})
        elif role == "tool":
            # Represent a tool result as a user-visible text turn (the rust pipeline
            # does not use function calling; this keeps history coherent if present).
            input_items.append({"type": "message", "role": "user",
                                "content": _content_to_input_parts(msg.get("content"))})
        else:  # user (default)
            input_items.append({"type": "message", "role": "user",
                                "content": _content_to_input_parts(msg.get("content"))})

    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    out["input"] = input_items

    mt = chat.get("max_completion_tokens") or chat.get("max_tokens")
    if mt:
        out["max_output_tokens"] = mt
    if chat.get("stream"):
        out["stream"] = True
    if chat.get("tools"):
        out["tools"] = chat["tools"]
    if chat.get("tool_choice") is not None:
        out["tool_choice"] = chat["tool_choice"]
    # Pass a reasoning hint through if the caller set one (litellm uses this).
    if isinstance(chat.get("reasoning"), dict):
        out["reasoning"] = chat["reasoning"]
    return out


def _extract_text_from_output(output: list) -> str:
    """Concatenate assistant text from a Responses `output` array."""
    text = ""
    for item in output or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    text += c.get("text", "")
    return text


def _usage_to_chat(usage: Optional[dict]) -> Optional[dict]:
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("input_tokens", 0) or 0
    completion = usage.get("output_tokens", 0) or 0
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": usage.get("total_tokens", prompt + completion),
    }
    # Preserve reasoning-token detail so cost/thinking capture sees it.
    otd = usage.get("output_tokens_details") or {}
    if isinstance(otd, dict) and otd.get("reasoning_tokens") is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": otd["reasoning_tokens"]}
    itd = usage.get("input_tokens_details") or {}
    if isinstance(itd, dict) and itd.get("cached_tokens") is not None:
        out["prompt_tokens_details"] = {"cached_tokens": itd["cached_tokens"]}
    return out


def _extract_tool_calls(output: list) -> list:
    """Map Responses `function_call` output items to Chat `tool_calls`.

    The rust/coding pipeline uses text edit-blocks (no function calling), but the
    /chat/completions endpoint is a general drop-in; represent tool calls if a
    client uses them rather than silently dropping them.
    """
    tool_calls = []
    for item in output or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            tool_calls.append({
                "id": item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                "type": "function",
                "function": {"name": item.get("name", ""),
                             "arguments": item.get("arguments", "") or ""},
            })
    return tool_calls


def responses_to_chat(resp: dict, model: str, created: int) -> dict:
    """Build a Chat Completions response object from a final Responses object."""
    output = resp.get("output", []) or []
    text = _extract_text_from_output(output)
    tool_calls = _extract_tool_calls(output)
    status = resp.get("status")
    if tool_calls:
        finish = "tool_calls"
    elif status == "incomplete":
        finish = "length"
    else:
        finish = "stop"
    # content is null only for a tool-call-only message; otherwise a string
    # (empty string for an empty/refused response) so litellm parsing is happy.
    message: dict = {"role": "assistant",
                     "content": (text if text else (None if tool_calls else ""))}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": resp.get("id", "chatcmpl-codex"),
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": _usage_to_chat(resp.get("usage")) or {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _sse(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


# Responses-API terminal event types. A well-formed Responses SSE stream ends
# with exactly one of these; their ABSENCE at end-of-stream means the upstream
# was truncated (dropped mid-turn). This mirrors claude_code's `message_stop`
# terminal-event contract (agent/claude_code/bridge.py event_stream), adapted to
# the Responses-API markers this module + bridge._aggregate_sse already parse.
RESPONSES_TERMINAL_TYPES = ("response.completed", "response.incomplete", "response.failed")


def tail_has_terminal_event(tail: bytes) -> bool:
    """True if a rolling SSE byte tail contains a Responses terminal event.

    Detects the JSON ``type`` field (compact or spaced) and the SSE ``event:``
    line form. Callers keep a small rolling buffer (~256B) so a marker split
    across two upstream chunks is still matched on the next read. This is the
    byte-stream analogue of the line-level terminal check in
    ``aiter_responses_sse_as_chat``.

    The ``event:`` form MUST be line-anchored (leading ``\\n`` or tail start),
    exactly like claude_code's ``message_stop`` check
    (agent/claude_code/bridge.py): the bare substring ``event: response.completed``
    contains no JSON-escaped characters, so it appears verbatim inside a model's
    own ``output_text.delta`` text and would false-latch the terminal flag,
    bypassing the truncation guard. The ``"type":"..."`` JSON forms are safe
    because a literal ``"`` inside a delta string is always escaped to ``\\"``.
    """
    for t in RESPONSES_TERMINAL_TYPES:
        tb = t.encode()
        if b'"type":"' + tb + b'"' in tail:
            return True
        if b'"type": "' + tb + b'"' in tail:
            return True
        if (b"\nevent: " + tb) in tail or tail.startswith(b"event: " + tb):
            return True
    return False


# Message injected when the upstream stream ends WITHOUT a terminal event so the
# client raises rather than recording a truncated turn as a clean completion.
_TRUNCATION_MSG = "kaiju-bridge: upstream stream ended without response.completed (truncated)"
_FAILED_MSG = "kaiju-bridge: upstream reported a failed/error response"


def responses_truncation_error_sse(message: str = _TRUNCATION_MSG) -> bytes:
    """A synthetic Responses-API failure event to inject when the native SSE
    stream ends without a terminal event, so a Responses client (litellm
    responses-mode) raises instead of recording a truncated 'complete' turn."""
    body = json.dumps({"type": "response.failed",
                       "response": {"error": {"message": message}}})
    return b"event: response.failed\ndata: " + body.encode() + b"\n\n"


def chat_truncation_error_sse(message: str = _TRUNCATION_MSG, err_type: str = "api_error") -> bytes:
    """An OpenAI-style error chunk for the Chat-Completions SSE path. Emitting an
    ``error`` object (instead of a clean finish_reason=stop with null usage) makes
    the client (litellm) raise on a truncated stream rather than silently
    recording a short, zero-cost 'complete' turn."""
    return _sse({"error": {"message": message, "type": err_type}})


def iter_responses_sse_as_chat(lines: list[bytes], model: str, created: int):
    """Translate Responses SSE lines into Chat Completions SSE chunks.

    Emits an initial role chunk, a content chunk per `response.output_text.delta`,
    then a final chunk with finish_reason + usage and the `[DONE]` sentinel. If
    the stream ends WITHOUT a terminal event (truncated) or reports a failure, an
    error chunk is emitted instead of a clean finish=stop — see
    ``chat_truncation_error_sse`` and the async twin below.
    """
    cid = "chatcmpl-codex"
    base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
    yield _sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})

    usage_chat: Optional[dict] = None
    saw_terminal = False
    failed = False
    for line in lines:
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload in (b"", b"[DONE]"):
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type", "")
        if etype == "response.output_text.delta":
            delta = evt.get("delta", "")
            if delta:
                yield _sse({**base, "choices": [{"index": 0, "delta": {"content": delta},
                                                 "finish_reason": None}]})
        elif etype in ("response.completed", "response.incomplete"):
            usage_chat = _usage_to_chat((evt.get("response") or {}).get("usage"))
            saw_terminal = True
        elif etype in ("response.failed", "error"):
            saw_terminal = True
            failed = True
            break

    if not saw_terminal:
        # Truncated: never label a dropped stream as a clean stop with null usage.
        yield chat_truncation_error_sse()
        yield b"data: [DONE]\n\n"
        return
    if failed:
        yield chat_truncation_error_sse(_FAILED_MSG)
        yield b"data: [DONE]\n\n"
        return
    final = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    if usage_chat is not None:
        final["usage"] = usage_chat
    yield _sse(final)
    yield b"data: [DONE]\n\n"


def _sse_event_from_line(line: str):
    """Parse one SSE `data:` line into an event dict, or None."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload in ("", "[DONE]"):
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _chat_chunk_bytes(base: dict, delta: dict, finish=None, usage=None) -> bytes:
    choice = {"index": 0, "delta": delta, "finish_reason": finish}
    obj = {**base, "choices": [choice]}
    if usage is not None:
        obj["usage"] = usage
    return _sse(obj)


async def aiter_responses_sse_as_chat(aline_iter, model: str, created: int):
    """Async, INCREMENTAL translation of a Responses SSE line-stream into
    Chat-Completions SSE chunks — yields as each upstream event arrives so a
    log/liveness watchdog keeps seeing progress (no whole-turn buffering)."""
    cid = "chatcmpl-codex"
    base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
    yield _chat_chunk_bytes(base, {"role": "assistant"})

    usage_chat = None
    saw_terminal = False
    failed = False
    async for raw in aline_iter:
        line = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
        evt = _sse_event_from_line(line)
        if evt is None:
            continue
        etype = evt.get("type", "")
        if etype == "response.output_text.delta":
            delta = evt.get("delta", "")
            if delta:
                yield _chat_chunk_bytes(base, {"content": delta})
        elif etype in ("response.completed", "response.incomplete"):
            usage_chat = _usage_to_chat((evt.get("response") or {}).get("usage"))
            saw_terminal = True
        elif etype in ("response.failed", "error"):
            saw_terminal = True
            failed = True
            break

    if not saw_terminal:
        # The stream ended without response.completed/incomplete/failed — the
        # upstream turn was truncated. Emit an error (never a clean finish=stop
        # with null usage) so the client raises instead of recording a short,
        # zero-cost 'complete' turn. Mirrors claude_code's event_stream guard.
        yield chat_truncation_error_sse()
        yield b"data: [DONE]\n\n"
        return
    if failed:
        yield chat_truncation_error_sse(_FAILED_MSG)
        yield b"data: [DONE]\n\n"
        return
    yield _chat_chunk_bytes(base, {}, finish="stop", usage=usage_chat)
    yield b"data: [DONE]\n\n"


def now_ts(clock: Any = time.time) -> int:
    return int(clock())
