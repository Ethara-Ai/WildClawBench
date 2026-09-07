"""Tests for src/utils/codex_oauth/translate.py — pure Chat <-> Responses translation.

``translate.py`` is declared side-effect free (see src/utils/codex_oauth/AGENTS.md),
so everything here is exercised in-process with plain dicts and byte lines. No
network, no filesystem, no env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.codex_oauth.translate import (
    RESPONSES_TERMINAL_TYPES,
    _content_to_text,
    _extract_text_from_output,
    _extract_tool_calls,
    _usage_to_chat,
    aiter_responses_sse_as_chat,
    chat_to_responses,
    chat_truncation_error_sse,
    iter_responses_sse_as_chat,
    now_ts,
    responses_to_chat,
    responses_truncation_error_sse,
    tail_has_terminal_event,
)


def _sse_line(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj).encode()


def _parse_chunk(raw: bytes) -> dict:
    assert raw.startswith(b"data: ")
    return json.loads(raw[6:].strip())


# ---------------------------------------------------------------------------
# _content_to_text
# ---------------------------------------------------------------------------


def test_content_to_text_passes_string_through():
    assert _content_to_text("hello") == "hello"


def test_content_to_text_flattens_part_list():
    parts = [
        {"type": "text", "text": "a"},
        {"type": "input_text", "text": "b"},
        {"type": "output_text", "text": "c"},
        {"type": "image_url", "text": "d"},
    ]
    assert _content_to_text(parts) == "abcd"


def test_content_to_text_none_becomes_empty_string():
    assert _content_to_text(None) == ""


# ---------------------------------------------------------------------------
# chat_to_responses
# ---------------------------------------------------------------------------


def test_chat_to_responses_concatenates_system_and_developer_into_instructions():
    chat = {
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "developer", "content": "no emojis"},
            {"role": "user", "content": "hi"},
        ],
    }
    out = chat_to_responses(chat)
    assert out["instructions"] == "be terse\n\nno emojis"
    assert out["model"] == "gpt-5.6-sol"


def test_chat_to_responses_omits_instructions_when_no_system_message():
    out = chat_to_responses({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert "instructions" not in out


def test_chat_to_responses_user_message_becomes_input_text_item():
    out = chat_to_responses({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert out["input"] == [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "hi"}]},
    ]


def test_chat_to_responses_assistant_message_becomes_output_text_item():
    out = chat_to_responses({"model": "m", "messages": [{"role": "assistant", "content": "ok"}]})
    assert out["input"] == [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "ok"}]},
    ]


def test_chat_to_responses_tool_message_becomes_user_input_text_item():
    out = chat_to_responses({
        "model": "m",
        "messages": [{"role": "tool", "tool_call_id": "c1", "content": "42"}],
    })
    assert out["input"] == [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "42"}]},
    ]


def test_chat_to_responses_unknown_role_defaults_to_user():
    out = chat_to_responses({"model": "m", "messages": [{"role": "function", "content": "x"}]})
    assert out["input"][0]["role"] == "user"
    assert out["input"][0]["content"][0]["type"] == "input_text"


def test_chat_to_responses_max_tokens_maps_to_max_output_tokens():
    out = chat_to_responses({"model": "m", "messages": [], "max_tokens": 128})
    assert out["max_output_tokens"] == 128


def test_chat_to_responses_max_completion_tokens_maps_to_max_output_tokens():
    out = chat_to_responses({"model": "m", "messages": [], "max_completion_tokens": 256})
    assert out["max_output_tokens"] == 256


def test_chat_to_responses_max_completion_tokens_takes_precedence():
    out = chat_to_responses({
        "model": "m", "messages": [],
        "max_tokens": 128, "max_completion_tokens": 256,
    })
    assert out["max_output_tokens"] == 256


def test_chat_to_responses_omits_max_output_tokens_when_neither_present():
    assert "max_output_tokens" not in chat_to_responses({"model": "m", "messages": []})


def test_chat_to_responses_stream_true_passes_through():
    assert chat_to_responses({"model": "m", "messages": [], "stream": True})["stream"] is True


def test_chat_to_responses_stream_false_is_omitted():
    assert "stream" not in chat_to_responses({"model": "m", "messages": [], "stream": False})


def test_chat_to_responses_tools_and_tool_choice_pass_through():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    out = chat_to_responses({"model": "m", "messages": [],
                             "tools": tools, "tool_choice": "auto"})
    assert out["tools"] == tools
    assert out["tool_choice"] == "auto"


def test_chat_to_responses_reasoning_dict_passes_through():
    out = chat_to_responses({"model": "m", "messages": [],
                             "reasoning": {"effort": "high"}})
    assert out["reasoning"] == {"effort": "high"}


def test_chat_to_responses_reasoning_non_dict_is_dropped():
    assert "reasoning" not in chat_to_responses({"model": "m", "messages": [],
                                                 "reasoning": "high"})


def test_chat_to_responses_drops_temperature_and_top_p():
    """The codex backend 400s on sampling params for reasoning models, so
    ``chat_to_responses`` never copies them (dropped by omission)."""
    chat = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "top_p": 0.9,
    }
    out = chat_to_responses(chat)
    assert "temperature" not in out
    assert "top_p" not in out
    # And the source dict is left untouched (pure function).
    assert chat["temperature"] == 0.7


def test_chat_to_responses_emits_empty_input_for_no_messages():
    assert chat_to_responses({"model": "m"})["input"] == []


# ---------------------------------------------------------------------------
# _extract_text_from_output / _extract_tool_calls / _usage_to_chat
# ---------------------------------------------------------------------------


def test_extract_text_from_output_concatenates_message_text():
    output = [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "he"},
                                        {"type": "text", "text": "llo"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "!"}]},
        "not-a-dict",
    ]
    assert _extract_text_from_output(output) == "hello!"


def test_extract_text_from_output_empty_for_no_output():
    assert _extract_text_from_output([]) == ""


def test_extract_tool_calls_maps_function_call_items():
    output = [{"type": "function_call", "call_id": "call_9", "name": "f",
               "arguments": '{"a":1}'}]
    calls = _extract_tool_calls(output)
    assert calls == [{"id": "call_9", "type": "function",
                      "function": {"name": "f", "arguments": '{"a":1}'}}]


def test_usage_to_chat_maps_token_counts_and_details():
    usage = {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "output_tokens_details": {"reasoning_tokens": 3},
        "input_tokens_details": {"cached_tokens": 8},
    }
    out = _usage_to_chat(usage)
    assert out is not None
    assert out["prompt_tokens"] == 10
    assert out["completion_tokens"] == 4
    assert out["total_tokens"] == 14
    assert out["completion_tokens_details"] == {"reasoning_tokens": 3}
    assert out["prompt_tokens_details"] == {"cached_tokens": 8}


def test_usage_to_chat_derives_total_when_absent():
    out = _usage_to_chat({"input_tokens": 3, "output_tokens": 2})
    assert out is not None
    assert out["total_tokens"] == 5
    assert "completion_tokens_details" not in out
    assert "prompt_tokens_details" not in out


def test_usage_to_chat_none_for_non_dict():
    assert _usage_to_chat(None) is None


# ---------------------------------------------------------------------------
# responses_to_chat
# ---------------------------------------------------------------------------


def test_responses_to_chat_object_shape_and_stop_finish():
    resp = {
        "id": "resp_1",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }
    out = responses_to_chat(resp, "gpt-5.6-sol", 1700000000)
    assert out["id"] == "resp_1"
    assert out["object"] == "chat.completion"
    assert out["created"] == 1700000000
    assert out["model"] == "gpt-5.6-sol"
    assert out["choices"][0]["index"] == 0
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "hi"}
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["prompt_tokens"] == 2


def test_responses_to_chat_default_id_when_absent():
    assert responses_to_chat({}, "m", 0)["id"] == "chatcmpl-codex"


def test_responses_to_chat_incomplete_status_is_length():
    resp = {"status": "incomplete",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "tr"}]}]}
    assert responses_to_chat(resp, "m", 0)["choices"][0]["finish_reason"] == "length"


def test_responses_to_chat_function_call_is_tool_calls_with_null_content():
    resp = {"status": "completed",
            "output": [{"type": "function_call", "call_id": "c1", "name": "f",
                        "arguments": "{}"}]}
    choice = responses_to_chat(resp, "m", 0)["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["id"] == "c1"


def test_responses_to_chat_tool_calls_win_over_incomplete_status():
    resp = {"status": "incomplete",
            "output": [{"type": "function_call", "call_id": "c1", "name": "f",
                        "arguments": "{}"}]}
    assert responses_to_chat(resp, "m", 0)["choices"][0]["finish_reason"] == "tool_calls"


def test_responses_to_chat_empty_output_yields_empty_string_content():
    choice = responses_to_chat({"status": "completed", "output": []}, "m", 0)["choices"][0]
    assert choice["message"]["content"] == ""
    assert "tool_calls" not in choice["message"]


def test_responses_to_chat_missing_usage_defaults_to_zeros():
    out = responses_to_chat({"output": []}, "m", 0)
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# iter_responses_sse_as_chat
# ---------------------------------------------------------------------------


def test_iter_sse_as_chat_role_then_content_then_stop_with_usage():
    lines = [
        _sse_line({"type": "response.created"}),
        _sse_line({"type": "response.output_text.delta", "delta": "hi"}),
        _sse_line({"type": "response.completed",
                   "response": {"usage": {"input_tokens": 5, "output_tokens": 2,
                                          "total_tokens": 7}}}),
    ]
    chunks = list(iter_responses_sse_as_chat(lines, "gpt-5.6-sol", 42))

    first = _parse_chunk(chunks[0])
    assert first["object"] == "chat.completion.chunk"
    assert first["model"] == "gpt-5.6-sol"
    assert first["created"] == 42
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    assert first["choices"][0]["finish_reason"] is None

    content = _parse_chunk(chunks[1])
    assert content["choices"][0]["delta"] == {"content": "hi"}

    final = _parse_chunk(chunks[2])
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    assert chunks[-1] == b"data: [DONE]\n\n"


def test_iter_sse_as_chat_skips_empty_deltas_and_non_data_lines():
    lines = [
        b"event: response.output_text.delta",
        b"",
        b"data: [DONE]",
        b"data: not-json",
        _sse_line({"type": "response.output_text.delta", "delta": ""}),
        _sse_line({"type": "response.completed", "response": {}}),
    ]
    chunks = list(iter_responses_sse_as_chat(lines, "m", 0))
    # role chunk + final stop chunk + [DONE] only: no content chunk was emitted.
    assert len(chunks) == 3
    assert _parse_chunk(chunks[1])["choices"][0]["finish_reason"] == "stop"


def test_iter_sse_as_chat_incomplete_terminal_still_finishes_stop():
    lines = [_sse_line({"type": "response.incomplete", "response": {}})]
    chunks = list(iter_responses_sse_as_chat(lines, "m", 0))
    assert _parse_chunk(chunks[1])["choices"][0]["finish_reason"] == "stop"
    assert "usage" not in _parse_chunk(chunks[1])


def test_iter_sse_as_chat_truncated_stream_yields_error_not_clean_stop():
    """No terminal event at end-of-stream => an OpenAI ``error`` object, so the
    client raises instead of recording a truncated turn as a clean stop."""
    lines = [_sse_line({"type": "response.output_text.delta", "delta": "par"})]
    chunks = list(iter_responses_sse_as_chat(lines, "m", 0))

    err = _parse_chunk(chunks[-2])
    assert "error" in err
    assert "truncated" in err["error"]["message"]
    assert err["error"]["type"] == "api_error"
    assert chunks[-1] == b"data: [DONE]\n\n"
    # No chunk claims a clean finish.
    finishes = [c for c in chunks[:-1]
                if _parse_chunk(c).get("choices", [{}])[0].get("finish_reason") == "stop"]
    assert finishes == []


def test_iter_sse_as_chat_failed_event_yields_failed_error_message():
    lines = [
        _sse_line({"type": "response.output_text.delta", "delta": "x"}),
        _sse_line({"type": "response.failed", "response": {"error": {"message": "boom"}}}),
        _sse_line({"type": "response.output_text.delta", "delta": "never"}),
    ]
    chunks = list(iter_responses_sse_as_chat(lines, "m", 0))
    err = _parse_chunk(chunks[-2])
    assert "failed" in err["error"]["message"]
    assert chunks[-1] == b"data: [DONE]\n\n"
    # The generator broke out of the loop: the post-failure delta never emitted.
    assert not any(b"never" in c for c in chunks)


def test_iter_sse_as_chat_error_event_yields_failed_error_message():
    lines = [_sse_line({"type": "error", "error": {"message": "nope"}})]
    chunks = list(iter_responses_sse_as_chat(lines, "m", 0))
    assert "failed" in _parse_chunk(chunks[-2])["error"]["message"]


def test_chat_truncation_error_sse_shape():
    obj = _parse_chunk(chat_truncation_error_sse("msg", "custom_type"))
    assert obj == {"error": {"message": "msg", "type": "custom_type"}}


def test_responses_truncation_error_sse_is_a_failed_event():
    raw = responses_truncation_error_sse("gone")
    assert raw.startswith(b"event: response.failed\ndata: ")
    body = json.loads(raw.split(b"data: ", 1)[1])
    assert body["type"] == "response.failed"
    assert body["response"]["error"]["message"] == "gone"


# ---------------------------------------------------------------------------
# tail_has_terminal_event — truncation-guard latch
# ---------------------------------------------------------------------------


def test_terminal_types_constant_is_exact():
    assert RESPONSES_TERMINAL_TYPES == (
        "response.completed", "response.incomplete", "response.failed")


def test_tail_has_terminal_event_compact_json_type():
    assert tail_has_terminal_event(b'data: {"type":"response.completed","response":{}}')


def test_tail_has_terminal_event_spaced_json_type():
    assert tail_has_terminal_event(b'data: {"type": "response.completed"}')


def test_tail_has_terminal_event_incomplete_and_failed_json_types():
    assert tail_has_terminal_event(b'{"type":"response.incomplete"}')
    assert tail_has_terminal_event(b'{"type":"response.failed"}')


def test_tail_has_terminal_event_line_anchored_event_form():
    assert tail_has_terminal_event(b"data: {}\nevent: response.completed")


def test_tail_has_terminal_event_event_form_at_tail_start():
    assert tail_has_terminal_event(b"event: response.completed\ndata: {}")


def test_tail_has_terminal_event_false_latch_guard_for_unanchored_event_form():
    """The bare substring ``event: response.completed`` inside model output text
    must NOT latch the terminal flag — otherwise a truncated stream whose text
    merely quotes the marker would be recorded as a clean completion."""
    tail = (b'data: {"type":"response.output_text.delta","delta":'
            b'"write event: response.completed to the log"}')
    assert not tail_has_terminal_event(tail)


def test_tail_has_terminal_event_false_for_delta_mentioning_the_words():
    tail = (b'data: {"type":"response.output_text.delta",'
            b'"delta":"the response.completed event ends a stream"}')
    assert not tail_has_terminal_event(tail)


def test_tail_has_terminal_event_false_for_empty_and_unrelated_tail():
    assert not tail_has_terminal_event(b"")
    assert not tail_has_terminal_event(b'data: {"type":"response.created"}')


# ---------------------------------------------------------------------------
# now_ts
# ---------------------------------------------------------------------------


def test_now_ts_truncates_injected_clock_to_int():
    assert now_ts(lambda: 1700000000.987) == 1700000000


def test_now_ts_default_clock_returns_int():
    assert isinstance(now_ts(), int)


# ---------------------------------------------------------------------------
# aiter_responses_sse_as_chat — the ASYNC twin used in production streaming.
# Its truncation/failed guards mirror the sync generator's; without coverage a
# broken async guard would silently record a dropped stream as a clean stop.
# Driven via asyncio.run (stdlib) so no pytest-asyncio dependency is needed.
# ---------------------------------------------------------------------------


def _drain_async_sse(lines: list[bytes], model: str = "m", created: int = 0) -> list[bytes]:
    import asyncio

    async def _aiter():
        for ln in lines:
            yield ln

    async def _collect():
        return [chunk async for chunk in aiter_responses_sse_as_chat(_aiter(), model, created)]

    return asyncio.run(_collect())


def test_aiter_sse_as_chat_happy_path_emits_role_content_and_clean_stop():
    lines = [
        _sse_line({"type": "response.output_text.delta", "delta": "hi"}),
        _sse_line({"type": "response.completed",
                   "response": {"usage": {"input_tokens": 3, "output_tokens": 1}}}),
    ]
    chunks = _drain_async_sse(lines)
    assert _parse_chunk(chunks[0])["choices"][0]["delta"]["role"] == "assistant"
    assert any(_parse_chunk(c)["choices"][0]["delta"].get("content") == "hi" for c in chunks)
    final = _parse_chunk(chunks[-2])
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["prompt_tokens"] == 3
    assert chunks[-1] == b"data: [DONE]\n\n"


def test_aiter_sse_as_chat_truncated_stream_yields_error_not_clean_stop():
    """Async twin of the sync truncation guard: a stream with NO terminal event
    MUST emit an ``error`` chunk, never a clean finish=stop with null usage."""
    lines = [_sse_line({"type": "response.output_text.delta", "delta": "par"})]
    chunks = _drain_async_sse(lines)
    err = _parse_chunk(chunks[-2])
    assert "error" in err
    assert "truncated" in err["error"]["message"]
    assert chunks[-1] == b"data: [DONE]\n\n"
    finishes = [c for c in chunks[:-1]
                if _parse_chunk(c).get("choices", [{}])[0].get("finish_reason") == "stop"]
    assert finishes == []


def test_aiter_sse_as_chat_failed_event_yields_failed_error_and_breaks():
    lines = [
        _sse_line({"type": "response.output_text.delta", "delta": "x"}),
        _sse_line({"type": "response.failed", "response": {"error": {"message": "boom"}}}),
        _sse_line({"type": "response.output_text.delta", "delta": "never"}),
    ]
    chunks = _drain_async_sse(lines)
    err = _parse_chunk(chunks[-2])
    assert "failed" in err["error"]["message"]
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert not any(b"never" in c for c in chunks)


def test_aiter_sse_as_chat_decodes_str_lines_too():
    """The async iterator accepts already-decoded str lines as well as bytes."""
    lines = ['data: {"type": "response.output_text.delta", "delta": "yo"}',
             'data: {"type": "response.completed", "response": {}}']
    import asyncio

    async def _aiter():
        for ln in lines:
            yield ln

    async def _collect():
        return [c async for c in aiter_responses_sse_as_chat(_aiter(), "m", 0)]

    chunks = asyncio.run(_collect())
    assert any(_parse_chunk(c)["choices"][0]["delta"].get("content") == "yo" for c in chunks)
    assert _parse_chunk(chunks[-2])["choices"][0]["finish_reason"] == "stop"
