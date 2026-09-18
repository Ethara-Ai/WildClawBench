"""Behavioral tests for the two LiteLLM usage-callback modules.

`litellm_usage_callback.py` is the SOLE writer of the 12-key `usage.jsonl`
schema and `litellm_usage_oauth_callback.py` is its OAuth-path sibling that
writes the separate `usage_oauth.jsonl` (11-key) audit trail. These callbacks run
inside the LiteLLM sidecar container; nothing else in the harness may reproduce
their row shape, so these tests pin:

  - the EXACT row key set + ordering-independent contract of each schema,
  - `_is_preflight_ping` classification (the only thing separating the
    startup probe cost from real agent cost),
  - the universal non-cached input recovery rule
    `non_cached = prompt - cache_read - cache_write` (clamped to 0),
  - cost preference (`litellm.completion_cost` over `kwargs['response_cost']`,
    falling back only when completion_cost <= 0),
  - append (never truncate) semantics against a tmp_path file,
  - the small numeric/coercion helpers and their edge cases.

Everything is offline: `litellm` is stubbed via monkeypatch.setitem(sys.modules,
...) and the log path is redirected into tmp_path. No docker / network / AWS.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import litellm_usage_callback as uc  # noqa: E402
from src.utils import litellm_usage_oauth_callback as oc  # noqa: E402


# ============================================================================
# Fixtures / helpers
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_warn_state():
    """The primary module carries a process-global warn-dedup set; clear it so
    a warn emitted by one test can't suppress the assertion in another."""
    uc._WARN_SEEN.clear()
    yield
    uc._WARN_SEEN.clear()


@pytest.fixture
def usage_path(tmp_path, monkeypatch):
    """Redirect the primary callback's log path into tmp_path (nested dir to
    exercise the os.makedirs branch)."""
    p = tmp_path / "var" / "litellm_usage" / "usage.jsonl"
    monkeypatch.setattr(uc, "_PATH", str(p))
    return p


@pytest.fixture
def oauth_path(tmp_path, monkeypatch):
    p = tmp_path / "var" / "litellm_usage" / "usage_oauth.jsonl"
    monkeypatch.setattr(oc, "_PATH", str(p))
    return p


@pytest.fixture
def stub_completion_cost(monkeypatch):
    """Install a fake `litellm` module whose completion_cost returns a fixed
    value. Returns a mutable holder so a test can change the value / assert
    call args."""
    holder = {"return_value": 0.123, "calls": []}

    def _completion_cost(completion_response=None, model=None):
        holder["calls"].append({"completion_response": completion_response, "model": model})
        rv = holder["return_value"]
        if isinstance(rv, Exception):
            raise rv
        return rv

    fake = SimpleNamespace(completion_cost=_completion_cost)
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return holder


def _chat_usage(prompt_tokens=1000, completion_tokens=50, cache_read=0, cache_write=0):
    """A Bedrock/Anthropic-style usage dict where prompt_tokens already folds
    in cache_read + cache_write (the documented provider shape)."""
    d = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if cache_read:
        d["cache_read_input_tokens"] = cache_read
    if cache_write:
        d["cache_creation_input_tokens"] = cache_write
    return d


def _resp(usage=None, duration=None):
    """Response object exposing `.usage` (and optionally `.duration`)."""
    ns = SimpleNamespace(usage=usage)
    if duration is not None:
        ns.duration = duration
    return ns


def _read_rows(path: Path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=2, milliseconds=500)  # 2.5s duration


# ============================================================================
# _usage_to_dict (shared logic, tested on both modules)
# ============================================================================


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_none_returns_empty(mod):
    assert mod._usage_to_dict(None) == {}


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_passthrough_dict(mod):
    d = {"prompt_tokens": 5}
    assert mod._usage_to_dict(d) is d


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_uses_model_dump(mod):
    obj = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 7})
    assert mod._usage_to_dict(obj) == {"prompt_tokens": 7}


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_uses_dict_method_when_model_dump_missing(mod):
    # object with .dict() but not .model_dump()
    class Only:
        def dict(self):
            return {"completion_tokens": 3}

    assert mod._usage_to_dict(Only()) == {"completion_tokens": 3}


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_model_dump_raises_falls_through_to_dunder_dict(mod):
    class Boom:
        # model_dump raises -> caught -> falls to __dict__ fallback
        def model_dump(self):
            raise RuntimeError("nope")

    obj = Boom()
    obj.prompt_tokens = 11  # populates __dict__
    assert mod._usage_to_dict(obj) == {"prompt_tokens": 11}


@pytest.mark.parametrize("mod", [uc, oc])
def test_usage_to_dict_model_dump_returns_non_dict_ignored(mod):
    # model_dump returns a list (not dict) -> ignored, falls to __dict__
    obj = SimpleNamespace(model_dump=lambda: [1, 2, 3])
    # SimpleNamespace __dict__ includes the model_dump entry; that's the fallback.
    out = mod._usage_to_dict(obj)
    assert isinstance(out, dict)
    assert "model_dump" in out


# ============================================================================
# _int / _float coercion helpers
# ============================================================================


@pytest.mark.parametrize("mod", [uc, oc])
@pytest.mark.parametrize("value,expected", [
    (5, 5),
    ("7", 7),
    (3.9, 3),          # float truncates toward zero
    (None, 0),         # None -> default
    ("abc", 0),        # unparseable -> default
    ([], 0),           # wrong type -> default
])
def test_int_helper(mod, value, expected):
    assert mod._int(value) == expected


def test_int_helper_custom_default():
    assert uc._int(None, default=99) == 99
    assert uc._int("bad", default=-1) == -1


def test_float_helper():
    assert uc._float(1.5) == 1.5
    assert uc._float("2.25") == 2.25
    assert uc._float(None) == 0.0
    assert uc._float("bad") == 0.0
    assert uc._float(None, default=4.0) == 4.0


# ============================================================================
# _is_preflight_ping
# ============================================================================


def test_preflight_ping_str_content():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_case_and_whitespace_insensitive():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "  PiNg  "}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_max_tokens_string_one():
    kwargs = {
        "max_tokens": "1",
        "messages": [{"role": "user", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_max_tokens_from_optional_params():
    kwargs = {
        "optional_params": {"max_tokens": 1},
        "messages": [{"role": "user", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_max_tokens_from_optional_params_camelcase():
    kwargs = {
        "optional_params": {"maxTokens": 1},
        "messages": [{"role": "user", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_content_list_shape():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": [{"text": "ping"}]}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_preflight_ping_content_list_uses_content_key():
    # inner dict has 'content' rather than 'text'
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": [{"content": "ping"}]}],
    }
    assert uc._is_preflight_ping(kwargs) is True


def test_not_preflight_wrong_max_tokens():
    kwargs = {
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_missing_max_tokens_entirely():
    # no max_tokens anywhere -> max_tok is None -> not in (1,"1")
    kwargs = {"messages": [{"role": "user", "content": "ping"}]}
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_multiple_messages():
    kwargs = {
        "max_tokens": 1,
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "ping"},
        ],
    }
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_wrong_role():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "assistant", "content": "ping"}],
    }
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_wrong_content_text():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hello world"}],
    }
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_content_list_wrong_length():
    kwargs = {
        "max_tokens": 1,
        "messages": [{"role": "user", "content": [{"text": "ping"}, {"text": "extra"}]}],
    }
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_empty_messages():
    kwargs = {"max_tokens": 1, "messages": []}
    assert uc._is_preflight_ping(kwargs) is False


def test_not_preflight_message_not_dict():
    kwargs = {"max_tokens": 1, "messages": ["ping"]}
    assert uc._is_preflight_ping(kwargs) is False


def test_preflight_exception_path_returns_false():
    # messages is a non-iterable-non-list truthy value; len() will raise inside
    # -> the outer try/except returns False.
    class Weird:
        def __len__(self):
            raise RuntimeError("boom")

    kwargs = {"max_tokens": 1, "messages": Weird()}
    assert uc._is_preflight_ping(kwargs) is False


# ============================================================================
# _warn_once_per_day (rate limiting)
# ============================================================================


def test_warn_once_per_day_dedups(monkeypatch):
    writes = []
    monkeypatch.setattr(uc.sys.stderr, "write", lambda s: writes.append(s))
    uc._warn_once_per_day("model-x", "hi %d", 1)
    uc._warn_once_per_day("model-x", "hi %d", 2)  # same model+day -> suppressed
    assert len(writes) == 1
    assert "model-x" in writes[0]


def test_warn_once_per_day_distinct_models(monkeypatch):
    writes = []
    monkeypatch.setattr(uc.sys.stderr, "write", lambda s: writes.append(s))
    uc._warn_once_per_day("model-a", "x")
    uc._warn_once_per_day("model-b", "x")
    assert len(writes) == 2


# ============================================================================
# _write_row (primary callback) — the 12-key schema + math
# ============================================================================

# The canonical 12 keys of usage.jsonl. Pinning this exact set is the whole
# point of this file: nothing else may reproduce or diverge from it.
EXPECTED_KEYS = {
    "ts", "model", "kind",
    "input_tokens", "output_tokens", "total_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens",
    "audio_seconds", "cost_usd", "duration_s",
}


def test_write_row_exact_key_schema(usage_path, stub_completion_cost):
    kwargs = {"model": "claude-opus-4.7", "messages": [], "response_cost": 0.0}
    uc._write_row(kwargs, _resp(_chat_usage()), T0, T1)
    rows = _read_rows(usage_path)
    assert len(rows) == 1
    assert set(rows[0].keys()) == EXPECTED_KEYS


def test_write_row_basic_values(usage_path, stub_completion_cost):
    stub_completion_cost["return_value"] = 0.5
    kwargs = {"model": "claude-opus-4.7", "messages": []}
    uc._write_row(kwargs, _resp(_chat_usage(prompt_tokens=1000, completion_tokens=50)), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["model"] == "claude-opus-4.7"
    assert row["kind"] == "agent"
    assert row["input_tokens"] == 1000          # no cache -> non-cached == prompt
    assert row["output_tokens"] == 50
    assert row["total_tokens"] == 1050
    assert row["cache_read_tokens"] == 0
    assert row["cache_write_tokens"] == 0
    assert row["audio_seconds"] == 0.0
    assert row["cost_usd"] == 0.5
    assert row["duration_s"] == 2.5


def test_write_row_non_cached_input_subtracts_read_and_write(usage_path, stub_completion_cost):
    # prompt folds in BOTH cache_read and cache_write -> non-cached = 1500-300-50
    usage = _chat_usage(prompt_tokens=1500, completion_tokens=120, cache_read=300, cache_write=50)
    uc._write_row({"model": "m", "messages": []}, _resp(usage), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["input_tokens"] == 1500 - 300 - 50
    assert row["cache_read_tokens"] == 300
    assert row["cache_write_tokens"] == 50
    # total = non_cached + output + cache_read + cache_write
    assert row["total_tokens"] == (1500 - 300 - 50) + 120 + 300 + 50


def test_write_row_cache_read_from_prompt_tokens_details(usage_path, stub_completion_cost):
    # OpenAI shape: cached tokens live under prompt_tokens_details.cached_tokens
    usage = {
        "prompt_tokens": 800,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 200},
    }
    uc._write_row({"model": "gpt-5.5", "messages": []}, _resp(usage), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["cache_read_tokens"] == 200
    assert row["input_tokens"] == 800 - 200  # cache_write is 0 here


def test_write_row_negative_non_cached_clamped_and_warned(usage_path, stub_completion_cost, monkeypatch):
    writes = []
    monkeypatch.setattr(uc.sys.stderr, "write", lambda s: writes.append(s))
    # prompt < cache_read + cache_write -> clamp to 0 + warn
    usage = _chat_usage(prompt_tokens=100, completion_tokens=10, cache_read=200, cache_write=50)
    uc._write_row({"model": "m", "messages": []}, _resp(usage), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["input_tokens"] == 0
    assert row["total_tokens"] == 0 + 10 + 200 + 50
    # a warn was emitted
    assert any("clamping non-cached input to 0" in w for w in writes)


def test_write_row_preflight_kind(usage_path, stub_completion_cost):
    kwargs = {
        "model": "claude-sonnet",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    uc._write_row(kwargs, _resp(_chat_usage(prompt_tokens=5, completion_tokens=1)), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["kind"] == "preflight"


def test_write_row_model_missing_becomes_empty_string(usage_path, stub_completion_cost):
    uc._write_row({"messages": []}, _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["model"] == ""


def test_write_row_transcription_token_shape(usage_path, stub_completion_cost):
    # gpt-4o-transcribe token-billed shape: input_tokens/output_tokens, no
    # prompt_tokens/completion_tokens.
    usage = {
        "type": "tokens",
        "input_tokens": 300,
        "output_tokens": 25,
        "total_tokens": 325,
    }
    uc._write_row({"model": "gpt-4o-transcribe", "messages": []}, _resp(usage), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["input_tokens"] == 300
    assert row["output_tokens"] == 25


def test_write_row_audio_seconds_from_usage_seconds(usage_path, stub_completion_cost):
    usage = {"type": "duration", "seconds": 12.3456}
    uc._write_row({"model": "whisper-1", "messages": []}, _resp(usage), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["audio_seconds"] == 12.346  # rounded to 3 places
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0


def test_write_row_audio_seconds_falls_back_to_response_duration(usage_path, stub_completion_cost):
    # whisper-1 default json: no usage object, duration on the response object.
    resp = _resp(usage=None, duration=7.5)
    uc._write_row({"model": "whisper-1", "messages": []}, resp, T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["audio_seconds"] == 7.5


def test_write_row_cost_prefers_completion_cost(usage_path, stub_completion_cost):
    stub_completion_cost["return_value"] = 0.245
    uc._write_row({"model": "m", "messages": [], "response_cost": 0.0028},
                  _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    # completion_cost (0.245) wins over the wrong proxy response_cost (0.0028)
    assert row["cost_usd"] == 0.245


def test_write_row_cost_falls_back_to_response_cost_when_completion_cost_zero(usage_path, stub_completion_cost):
    stub_completion_cost["return_value"] = 0.0  # e.g. whisper duration billing
    uc._write_row({"model": "whisper-1", "messages": [], "response_cost": 0.0006},
                  _resp({"type": "duration", "seconds": 3.0}), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["cost_usd"] == 0.0006


def test_write_row_cost_completion_cost_raises_uses_response_cost(usage_path, stub_completion_cost, monkeypatch):
    monkeypatch.setattr(uc.sys.stderr, "write", lambda s: None)
    stub_completion_cost["return_value"] = RuntimeError("pricing blew up")
    uc._write_row({"model": "m", "messages": [], "response_cost": 0.09},
                  _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["cost_usd"] == 0.09


def test_write_row_response_obj_dict_usage(usage_path, stub_completion_cost):
    # response_obj is a plain dict (no .usage attr) -> reads dict["usage"]
    resp = {"usage": _chat_usage(prompt_tokens=600, completion_tokens=30)}
    uc._write_row({"model": "m", "messages": []}, resp, T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["input_tokens"] == 600
    assert row["output_tokens"] == 30


def test_write_row_appends_never_truncates(usage_path, stub_completion_cost):
    for i in range(3):
        uc._write_row({"model": f"m{i}", "messages": []},
                      _resp(_chat_usage(prompt_tokens=100 * (i + 1))), T0, T1)
    rows = _read_rows(usage_path)
    assert len(rows) == 3
    assert [r["model"] for r in rows] == ["m0", "m1", "m2"]
    assert [r["input_tokens"] for r in rows] == [100, 200, 300]


def test_write_row_creates_nested_dir(tmp_path, monkeypatch, stub_completion_cost):
    target = tmp_path / "deep" / "nested" / "dir" / "usage.jsonl"
    assert not target.parent.exists()
    monkeypatch.setattr(uc, "_PATH", str(target))
    uc._write_row({"model": "m", "messages": []}, _resp(_chat_usage()), T0, T1)
    assert target.exists()
    assert len(_read_rows(target)) == 1


def test_write_row_bad_start_end_time_duration_zero(usage_path, stub_completion_cost):
    # start/end not datetime -> subtraction raises -> duration stays 0.0
    uc._write_row({"model": "m", "messages": []}, _resp(_chat_usage()), "notatime", "alsobad")
    row = _read_rows(usage_path)[0]
    assert row["duration_s"] == 0.0


def test_write_row_ts_is_iso8601_utc(usage_path, stub_completion_cost):
    uc._write_row({"model": "m", "messages": []}, _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    parsed = datetime.fromisoformat(row["ts"])
    assert parsed.tzinfo is not None  # timezone-aware


def test_write_row_swallows_all_errors(usage_path, monkeypatch):
    # No litellm module in sys.modules AND makedirs blows up -> the outer
    # try/except must swallow it and never raise.
    monkeypatch.setitem(sys.modules, "litellm", None)  # `import litellm` -> ImportError
    monkeypatch.setattr(uc.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    # Silence the error stderr write.
    monkeypatch.setattr(uc.sys.stderr, "write", lambda s: None)
    # Must not raise.
    uc._write_row({"model": "m", "messages": []}, _resp(_chat_usage()), T0, T1)


# ============================================================================
# UsageWriter.async_log_success_event (primary)
# ============================================================================


def test_async_log_success_event_writes_row(usage_path, stub_completion_cost):
    import asyncio
    writer = uc.UsageWriter()
    asyncio.run(writer.async_log_success_event(
        {"model": "m", "messages": []}, _resp(_chat_usage()), T0, T1
    ))
    assert len(_read_rows(usage_path)) == 1


def test_proxy_handler_instance_is_usage_writer():
    assert isinstance(uc.proxy_handler_instance, uc.UsageWriter)


# ============================================================================
# OAuth callback: _is_oauth_route
# ============================================================================


@pytest.mark.parametrize("model,expected", [
    ("anthropic/claude-opus-4-5", True),
    ("bedrock/claude-OPUS-4-6", True),  # case-insensitive
    ("claude-fable-5", True),
    ("anthropic/claude-FABLE-5", True),  # case-insensitive
    ("claude-sonnet-4-5", False),
    ("gpt-5.5", False),
    ("", False),
    (None, False),
])
def test_is_oauth_route(model, expected):
    assert oc._is_oauth_route(model) is expected


def test_bedrock_equivalent_cost_uses_fable_rates_for_fable():
    # 1M uncached input tokens: $10 on fable rates vs $5 on opus rates.
    assert oc._bedrock_equivalent_cost(1_000_000, 0, 0, 0, model="claude-fable-5") == pytest.approx(10.0)
    assert oc._bedrock_equivalent_cost(0, 1_000_000, 0, 0, model="anthropic/claude-fable-5") == pytest.approx(50.0)
    # Default/opus path unchanged.
    assert oc._bedrock_equivalent_cost(1_000_000, 0, 0, 0) == pytest.approx(5.0)
    assert oc._bedrock_equivalent_cost(1_000_000, 0, 0, 0, model="claude-opus-4-8") == pytest.approx(5.0)


# ============================================================================
# OAuth callback: _bedrock_equivalent_cost
# ============================================================================


def test_bedrock_equivalent_cost_all_terms():
    # 1e6 input @ 5e-6, 1e6 output @ 25e-6, 1e6 read @ 5e-7, 1e6 write @ 6.25e-6
    cost = oc._bedrock_equivalent_cost(1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert cost == pytest.approx(5.0 + 25.0 + 0.5 + 6.25)


def test_bedrock_equivalent_cost_zero():
    assert oc._bedrock_equivalent_cost(0, 0, 0, 0) == 0.0


def test_bedrock_equivalent_cost_input_only():
    assert oc._bedrock_equivalent_cost(2000, 0, 0, 0) == pytest.approx(2000 * 5e-6)


# ============================================================================
# OAuth callback: _write_row — 11-key schema, oauth gating, cost math
# ============================================================================

EXPECTED_OAUTH_KEYS = {
    "ts", "model", "route", "kind",
    "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "cost_actual", "cost_bedrock_equivalent", "duration_s",
}


def test_oauth_write_row_skips_non_opus(oauth_path):
    oc._write_row({"model": "claude-sonnet-4-5"}, _resp(_chat_usage()), T0, T1)
    assert not oauth_path.exists()  # nothing written for non-oauth route


def test_oauth_write_row_skips_empty_model(oauth_path):
    oc._write_row({}, _resp(_chat_usage()), T0, T1)
    assert not oauth_path.exists()


def test_oauth_write_row_exact_key_schema(oauth_path):
    oc._write_row({"model": "anthropic/claude-opus-4-5"}, _resp(_chat_usage()), T0, T1)
    rows = _read_rows(oauth_path)
    assert len(rows) == 1
    assert set(rows[0].keys()) == EXPECTED_OAUTH_KEYS


def test_oauth_write_row_values_and_route(oauth_path):
    usage = _chat_usage(prompt_tokens=1500, completion_tokens=100, cache_read=300, cache_write=50)
    oc._write_row({"model": "anthropic/claude-opus-4-5"}, _resp(usage), T0, T1)
    row = _read_rows(oauth_path)[0]
    assert row["model"] == "anthropic/claude-opus-4-5"
    assert row["route"] == "claude_oauth_bridge"
    assert row["input_tokens"] == 1500 - 300 - 50
    assert row["output_tokens"] == 100
    assert row["cache_read_tokens"] == 300
    assert row["cache_write_tokens"] == 50
    assert row["cost_actual"] == 0.0  # prepaid subscription -> $0 marginal
    assert row["duration_s"] == 2.5


def test_oauth_write_row_cost_bedrock_equivalent(oauth_path):
    usage = _chat_usage(prompt_tokens=1500, completion_tokens=100, cache_read=300, cache_write=50)
    oc._write_row({"model": "opus"}, _resp(usage), T0, T1)
    row = _read_rows(oauth_path)[0]
    input_tokens = 1500 - 300 - 50
    expected = round(
        input_tokens * 5e-6 + 100 * 25e-6 + 300 * 5e-7 + 50 * 6.25e-6, 6
    )
    assert row["cost_bedrock_equivalent"] == expected


def test_oauth_write_row_negative_non_cached_clamped(oauth_path):
    # prompt < cache_read + cache_write -> clamp to 0 (no warn helper here)
    usage = _chat_usage(prompt_tokens=100, completion_tokens=5, cache_read=200, cache_write=30)
    oc._write_row({"model": "opus"}, _resp(usage), T0, T1)
    row = _read_rows(oauth_path)[0]
    assert row["input_tokens"] == 0


def test_oauth_write_row_prompt_tokens_details_cached(oauth_path):
    usage = {
        "prompt_tokens": 900,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 250},
    }
    oc._write_row({"model": "opus"}, _resp(usage), T0, T1)
    row = _read_rows(oauth_path)[0]
    assert row["cache_read_tokens"] == 250
    assert row["input_tokens"] == 900 - 250


def test_oauth_write_row_input_tokens_key_fallback(oauth_path):
    # transcription-style usage with input_tokens/output_tokens keys
    usage = {"input_tokens": 400, "output_tokens": 20}
    oc._write_row({"model": "opus"}, _resp(usage), T0, T1)
    row = _read_rows(oauth_path)[0]
    assert row["input_tokens"] == 400
    assert row["output_tokens"] == 20


def test_oauth_write_row_dict_response_obj(oauth_path):
    resp = {"usage": _chat_usage(prompt_tokens=700, completion_tokens=35)}
    oc._write_row({"model": "opus"}, resp, T0, T1)
    row = _read_rows(oauth_path)[0]
    assert row["input_tokens"] == 700
    assert row["output_tokens"] == 35


def test_oauth_write_row_appends(oauth_path):
    for i in range(3):
        oc._write_row({"model": "opus"}, _resp(_chat_usage(prompt_tokens=100 * (i + 1))), T0, T1)
    rows = _read_rows(oauth_path)
    assert len(rows) == 3
    assert [r["input_tokens"] for r in rows] == [100, 200, 300]


def test_oauth_write_row_creates_nested_dir(tmp_path, monkeypatch):
    target = tmp_path / "a" / "b" / "usage_oauth.jsonl"
    monkeypatch.setattr(oc, "_PATH", str(target))
    oc._write_row({"model": "opus"}, _resp(_chat_usage()), T0, T1)
    assert target.exists()


def test_oauth_write_row_bad_times_duration_zero(oauth_path):
    oc._write_row({"model": "opus"}, _resp(_chat_usage()), "bad", "bad")
    row = _read_rows(oauth_path)[0]
    assert row["duration_s"] == 0.0


def test_oauth_write_row_swallows_errors(oauth_path, monkeypatch):
    monkeypatch.setattr(oc.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(oc.sys.stderr, "write", lambda s: None)
    # opus route so it gets past the gate, then makedirs blows up -> swallowed.
    oc._write_row({"model": "opus"}, _resp(_chat_usage()), T0, T1)


def test_oauth_async_log_success_event(oauth_path):
    import asyncio
    writer = oc.OAuthUsageWriter()
    asyncio.run(writer.async_log_success_event(
        {"model": "opus"}, _resp(_chat_usage()), T0, T1
    ))
    assert len(_read_rows(oauth_path)) == 1


def test_oauth_callback_instance_type():
    assert isinstance(oc.oauth_usage_callback_instance, oc.OAuthUsageWriter)


# ============================================================================
# _classify_internal_purpose — naming openclaw's own calls at write time
# ============================================================================

# Verbatim heads of the prompts the agent image sends these calls with, so a
# bundle upgrade that reworded either one fails here rather than silently
# returning the log to 98-rows-for-87-messages.
_COMPACTION_SYSTEM = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI coding assistant, then produce a "
    "structured summary following the exact format specified."
)
_SUMMARIZE_USER = (
    "You are an assistant that summarizes texts concisely while keeping the "
    "most important information. Summarize the text to approximately 2000 "
    "characters."
)


def _anthropic_body(system, messages):
    return {"litellm_params": {"proxy_server_request": {
        "body": {"system": system, "messages": messages}}}}


def _turn_kwargs():
    return {"messages": [
        {"role": "user", "content": "build the invoice report"},
        {"role": "assistant", "content": "on it"},
        {"role": "user", "content": "now add VAT"},
    ]}


def test_compaction_named_from_system_message():
    kwargs = {"messages": [
        {"role": "system", "content": _COMPACTION_SYSTEM},
        {"role": "user", "content": "<conversation>\nturn 1\n</conversation>"},
    ]}
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_compaction_named_from_anthropic_top_level_system():
    # The runner registers the sidecar with api="anthropic-messages", so the
    # system prompt arrives as a body field, not a message.
    kwargs = _anthropic_body(_COMPACTION_SYSTEM, [
        {"role": "user", "content": [{"type": "text", "text": "<conversation>"}]},
    ])
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_compaction_named_when_system_is_a_content_block_list():
    kwargs = _anthropic_body(
        [{"type": "text", "text": _COMPACTION_SYSTEM}],
        [{"role": "user", "content": "<conversation>"}],
    )
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_media_summarizer_named_from_its_user_prompt():
    kwargs = {"messages": [{"role": "user", "content": _SUMMARIZE_USER}]}
    assert uc._classify_internal_purpose(kwargs) == "summarize"


def test_transcription_named_from_call_type():
    # Token-billed transcribe models carry tokens and no audio_seconds, so the
    # downstream duration test cannot see them; call_type can.
    assert uc._classify_internal_purpose(
        {"call_type": "atranscription", "messages": []}) == "transcription"


def test_agent_turn_is_not_named():
    assert uc._classify_internal_purpose(_turn_kwargs()) == ""


def test_agent_turn_quoting_the_summarizer_prompt_is_not_named():
    # A task could legitimately ask the agent about summarization. The single
    # user message requirement is what keeps that from being misread.
    kwargs = {"messages": [
        {"role": "user", "content": _SUMMARIZE_USER},
        {"role": "assistant", "content": "sure"},
        {"role": "user", "content": "go on"},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_summarizer_prompt_not_at_the_start_is_not_named():
    kwargs = {"messages": [
        {"role": "user", "content": "Explain this: " + _SUMMARIZE_USER},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_summarizer_head_under_a_system_prompt_is_not_named():
    # summarizeText sends no system prompt; a run that does is the agent.
    kwargs = {"messages": [
        {"role": "system", "content": "You are a helpful coding agent."},
        {"role": "user", "content": _SUMMARIZE_USER},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_preflight_ping_is_not_named_internal():
    assert uc._classify_internal_purpose({
        "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1,
    }) == ""


def test_classifier_never_raises_on_junk():
    for kwargs in ({"messages": "not-a-list"},
                   {"messages": [None, 3]},
                   {"litellm_params": "nope"},
                   {}):
        assert uc._classify_internal_purpose(kwargs) == ""


def test_write_row_tags_an_internal_call(usage_path, stub_completion_cost):
    kwargs = {"model": "claude-opus-4.7", "messages": [
        {"role": "system", "content": _COMPACTION_SYSTEM},
        {"role": "user", "content": "<conversation>"},
    ]}
    uc._write_row(kwargs, _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["purpose"] == "compaction"
    assert row["kind"] == "agent"
    assert set(row.keys()) == EXPECTED_KEYS | {"purpose"}


def test_write_row_leaves_a_turn_untagged(usage_path, stub_completion_cost):
    # The 12-key schema is unchanged for the rows that carry a message.
    uc._write_row({"model": "m", **_turn_kwargs()}, _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert "purpose" not in row
    assert set(row.keys()) == EXPECTED_KEYS


# ============================================================================
# image tool — buildImageContext() in the bundle sends one system-less user
# message of [text block, image block...]. Shape, not prompt text: the tool's
# prompt is whatever the agent passed it.
# ============================================================================


def _image_tool_kwargs(prompt="Describe the image."):
    return _anthropic_body("", [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image", "source": {"type": "base64",
                                     "media_type": "image/jpeg", "data": "QUJD"}},
    ]}])


def test_image_tool_named_from_its_content_blocks():
    assert uc._classify_internal_purpose(_image_tool_kwargs()) == "image"


def test_image_tool_named_with_an_agent_supplied_prompt():
    # DEFAULT_PROMPT only applies when the agent passes none, so the text
    # carries no signal and must not be required to.
    assert uc._classify_internal_purpose(
        _image_tool_kwargs("read the feeler gauge in this photo")) == "image"


def test_image_tool_named_across_multiple_images():
    kwargs = _anthropic_body("", [{"role": "user", "content": [
        {"type": "text", "text": "compare"},
        {"type": "image", "source": {"data": "QQ=="}},
        {"type": "image", "source": {"data": "Qg=="}},
    ]}])
    assert uc._classify_internal_purpose(kwargs) == "image"


def test_image_tool_named_from_openai_normalized_blocks():
    # LiteLLM hands callbacks the OpenAI-normalized messages when the raw
    # anthropic body was not captured; the image block is spelled image_url.
    kwargs = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe the image."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
    ]}]}
    assert uc._classify_internal_purpose(kwargs) == "image"


def test_agent_turn_carrying_an_image_is_not_named_image():
    # A task may hand the agent a photo in its first user message. The agent
    # always sends a system prompt; the image tool never does.
    kwargs = _anthropic_body("You are a helpful coding agent.", [
        {"role": "user", "content": [
            {"type": "text", "text": "grade the joint in this photo"},
            {"type": "image", "source": {"data": "QQ=="}},
        ]},
    ])
    assert uc._classify_internal_purpose(kwargs) == ""


def test_multi_turn_conversation_with_an_image_is_not_named_image():
    kwargs = {"messages": [
        {"role": "user", "content": [{"type": "image", "source": {"data": "QQ=="}}]},
        {"role": "assistant", "content": "that is a mortise"},
        {"role": "user", "content": "and the shoulder?"},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_text_only_single_message_is_not_named_image():
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "text", "text": "Describe the image."}]}])) == ""


# ============================================================================
# embeddings — memory-lancedb's /v1/embeddings calls, which carry no messages
# ============================================================================


def test_embeddings_named_from_call_type():
    assert uc._classify_internal_purpose(
        {"call_type": "aembedding", "model": "text-embedding-3-small"}) == "embeddings"


def test_embeddings_named_from_sync_call_type():
    assert uc._classify_internal_purpose({"call_type": "embedding"}) == "embeddings"


def test_embeddings_named_from_body_when_call_type_absent():
    # Proxy builds that do not forward call_type still post the OpenAI
    # embeddings body: `input` instead of `messages`.
    kwargs = {"litellm_params": {"proxy_server_request": {"body": {
        "model": "text-embedding-3-small", "input": "bench 06 shoulder gap",
        "dimensions": 1536}}}}
    assert uc._classify_internal_purpose(kwargs) == "embeddings"


def test_chat_request_is_not_named_embeddings():
    assert uc._classify_internal_purpose(
        {"call_type": "acompletion", **_turn_kwargs()}) == ""


def test_chat_body_with_messages_is_not_named_embeddings():
    kwargs = _anthropic_body("You are a coding agent.", [
        {"role": "user", "content": "hello"}])
    assert uc._classify_internal_purpose(kwargs) == ""


def test_compaction_still_wins_over_the_new_shape_rules():
    kwargs = {"call_type": "acompletion", "messages": [
        {"role": "system", "content": _COMPACTION_SYSTEM},
        {"role": "user", "content": "<conversation>"},
    ]}
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_write_row_tags_an_embeddings_call(usage_path, stub_completion_cost):
    uc._write_row({"model": "text-embedding-3-small", "call_type": "aembedding"},
                  _resp(_chat_usage()), T0, T1)
    assert _read_rows(usage_path)[0]["purpose"] == "embeddings"


def test_write_row_tags_an_image_tool_call(usage_path, stub_completion_cost):
    uc._write_row({"model": "claude-opus-5", **_image_tool_kwargs()},
                  _resp(_chat_usage()), T0, T1)
    assert _read_rows(usage_path)[0]["purpose"] == "image"


# ============================================================================
# pdf tool — three request paths, all of them system-less, reproduced from
# /usr/lib/node_modules/openclaw in the wildclawbench-ubuntu:v1.4 image. The
# tool is enabled in every run: the runner never writes
# agents.defaults.pdfModel but always writes imageModel, and
# resolvePdfModelConfigForTool (dist/reply-BCcP6j4h.js:22782) falls pdfModel
# -> imageModel, so a model always resolves; tools.deny lists browser tools
# only. Two of the three paths were unlabelled, and one unlabelled row is
# enough to flip usage_attribution to failed.
# ============================================================================

# anthropicAnalyzePdf, dist/reply-BCcP6j4h.js:22632, verbatim:
#     const content = [];
#     for (const pdf of params.pdfs) content.push({
#         type: "document",
#         source: { type: "base64", media_type: "application/pdf",
#                   data: pdf.base64 }
#     });
#     content.push({ type: "text", text: params.prompt });
#     ... body: JSON.stringify({ model, max_tokens, messages: [{ role: "user",
#                                                                content }] })
# There is no `system` key in that body at all — the document blocks come
# first and the prompt last.
def _pdf_native_anthropic_kwargs(prompt="Analyze this PDF document.", count=1):
    content = [
        {"type": "document", "source": {"type": "base64",
                                        "media_type": "application/pdf",
                                        "data": "JVBERi0xLjQK"}}
        for _ in range(count)
    ]
    content.append({"type": "text", "text": prompt})
    return _anthropic_body("", [{"role": "user", "content": content}])


# geminiAnalyzePdf, same file:22678, verbatim:
#     for (const pdf of params.pdfs) parts.push({ inline_data: {
#         mime_type: "application/pdf", data: pdf.base64 } });
#     parts.push({ text: params.prompt });
#     ... body: JSON.stringify({ contents: [{ role: "user", parts }] })
# Gemini parts carry no `type` tag, so the key itself is what identifies the
# block, and the media type is what separates it from a Gemini image part.
def _pdf_native_gemini_kwargs(prompt="Analyze this PDF document."):
    return {"litellm_params": {"proxy_server_request": {"body": {"messages": [
        {"role": "user", "content": [
            {"inline_data": {"mime_type": "application/pdf",
                             "data": "JVBERi0xLjQK"}},
            {"text": prompt},
        ]},
    ]}}}}


# buildPdfExtractionContext, same file:22832, verbatim:
#     const label = extractions.length > 1 ? `[PDF ${i + 1} text]\n`
#                                          : "[PDF text]\n";
#     content.push({ type: "text", text: label + extraction.text });
#     for (const img of extraction.images) content.push({ type: "image",
#         data: img.data, mimeType: img.mimeType });
#     ... content.push({ type: "text", text: prompt });
#     return { messages: [{ role: "user", content, timestamp: Date.now() }] };
# The context has no systemPrompt field, and both serializers only emit a
# system when one is set (anthropic.js:482, openai-completions.js:405), so
# this reaches the sidecar system-less. The image blocks arrive as the
# provider's own spelling — anthropic.js:559 rewrites them to
# {"type":"image","source":{...}}, openai-completions.js:436 to image_url.
def _pdf_extraction_kwargs(pages=("Invoice 4471\nSubtotal 78.00\nVAT 14.10",),
                           images=(), prompt="Analyze this PDF document."):
    content = []
    for i, text in enumerate(pages):
        label = f"[PDF {i + 1} text]\n" if len(pages) > 1 else "[PDF text]\n"
        content.append({"type": "text", "text": label + text})
        for data in images:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": data}})
    content.append({"type": "text", "text": prompt})
    return _anthropic_body("", [{"role": "user", "content": content}])


def test_pdf_tool_native_anthropic_named_from_its_document_blocks():
    assert uc._classify_internal_purpose(_pdf_native_anthropic_kwargs()) == "pdf"


def test_pdf_tool_native_anthropic_named_across_multiple_documents():
    # maxPdfs defaults to 10, so a multi-document body is an ordinary call.
    assert uc._classify_internal_purpose(
        _pdf_native_anthropic_kwargs(count=3)) == "pdf"


def test_pdf_tool_native_anthropic_named_with_an_agent_supplied_prompt():
    # DEFAULT_PROMPT ("Analyze this PDF document.", same file:22770) only
    # fills in when the agent passes none, so the text carries no signal.
    assert uc._classify_internal_purpose(
        _pdf_native_anthropic_kwargs("what is the invoice total?")) == "pdf"


def test_pdf_tool_native_gemini_named_from_its_inline_data_parts():
    assert uc._classify_internal_purpose(_pdf_native_gemini_kwargs()) == "pdf"


def test_pdf_tool_native_named_from_openai_normalized_file_blocks():
    # When the raw anthropic body was not captured LiteLLM hands callbacks the
    # OpenAI-normalized messages, where a PDF attachment is spelled `file` on
    # Chat Completions and `input_file` on Responses.
    for block_type in ("file", "input_file"):
        kwargs = {"messages": [{"role": "user", "content": [
            {"type": block_type, "file": {"filename": "invoice.pdf",
                                          "file_data": "data:application/pdf;base64,JVBER"}},
            {"type": "text", "text": "Analyze this PDF document."},
        ]}]}
        assert uc._classify_internal_purpose(kwargs) == "pdf"


def test_pdf_tool_text_extraction_named_from_its_label():
    # PDF_MIN_TEXT_CHARS = 200 (same file:22776) gates rasterization, so a
    # text-rich PDF produces zero image blocks and the label is the only
    # thing left to match on. This is the shape that was going out unlabelled.
    assert uc._classify_internal_purpose(_pdf_extraction_kwargs()) == "pdf"


def test_pdf_tool_text_extraction_named_across_multiple_pdfs():
    # Two or more extractions switch the label to its numbered form.
    kwargs = _pdf_extraction_kwargs(pages=("first doc body", "second doc body"))
    assert uc._classify_internal_purpose(kwargs) == "pdf"


def test_pdf_tool_extraction_with_images_keeps_the_image_label():
    # The third path already had a name before this change and keeps it: when
    # the PDF yields no extractable text its message is image blocks and a
    # prompt, indistinguishable from the image tool's, so the label stays
    # whole rather than splitting on the document's contents.
    assert uc._classify_internal_purpose(
        _pdf_extraction_kwargs(images=("QQ==", "Qg=="))) == "image"
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "image", "source": {"data": "QQ=="}},
            {"type": "text", "text": "Analyze this PDF document."},
        ]}])) == "image"


def test_write_row_tags_a_pdf_tool_call(usage_path, stub_completion_cost):
    uc._write_row({"model": "claude-opus-4-6", **_pdf_native_anthropic_kwargs()},
                  _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["purpose"] == "pdf"
    assert row["kind"] == "agent"
    assert set(row.keys()) == EXPECTED_KEYS | {"purpose"}


def test_write_row_tags_a_pdf_text_extraction_call(usage_path, stub_completion_cost):
    uc._write_row({"model": "claude-opus-4-6", **_pdf_extraction_kwargs()},
                  _resp(_chat_usage()), T0, T1)
    assert _read_rows(usage_path)[0]["purpose"] == "pdf"


# --- negatives: nothing an agent can send may reach the pdf label ----------


def test_agent_turn_carrying_a_pdf_is_not_named_pdf():
    # A task may hand the agent a contract in its first user message. The
    # agent always sends a system prompt; the pdf tool never does, on any of
    # its three paths.
    kwargs = _anthropic_body("You are a helpful coding agent.", [
        {"role": "user", "content": [
            {"type": "document", "source": {"type": "base64",
                                            "media_type": "application/pdf",
                                            "data": "JVBER"}},
            {"type": "text", "text": "summarise the attached contract"},
        ]},
    ])
    assert uc._classify_internal_purpose(kwargs) == ""


def test_agent_turn_whose_system_prompt_arrives_as_a_message_is_not_named_pdf():
    kwargs = {"messages": [
        {"role": "system", "content": "You are a helpful coding agent."},
        {"role": "user", "content": [
            {"type": "text", "text": "[PDF text]\nquarterly figures"},
            {"type": "text", "text": "what changed?"},
        ]},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_multi_turn_conversation_with_a_pdf_is_not_named_pdf():
    # The single-user-message requirement: an agent turn always carries the
    # conversation so far, every pdf-tool path carries exactly one message.
    kwargs = {"messages": [
        {"role": "user", "content": [
            {"type": "document", "source": {"data": "JVBER"}}]},
        {"role": "assistant", "content": "that is a lease"},
        {"role": "user", "content": "what is the break clause?"},
    ]}
    assert uc._classify_internal_purpose(kwargs) == ""


def test_text_only_single_message_without_the_pdf_label_is_not_named_pdf():
    # That shape belongs to summarize detection, which matches on its own
    # prompt head; an unlabelled one-text-block message is neither.
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "text", "text": "Invoice 4471\nSubtotal 78.00"}]}])) == ""
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "text", "text": _SUMMARIZE_USER}]}])) == "summarize"


def test_pdf_label_not_at_the_start_of_a_block_is_not_named_pdf():
    # The extractor prepends the label; a block that merely mentions it is a
    # task talking about PDFs.
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "text", "text": "our extractor emits [PDF text]\nas a marker"}]}])) == ""


def test_pdf_label_without_its_trailing_newline_is_not_named_pdf():
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"type": "text", "text": "[PDF text] is the marker we use"}]}])) == ""


def test_gemini_image_part_is_not_named_pdf():
    # pi-ai's google provider spells images inlineData too
    # (node_modules/@mariozechner/pi-ai/dist/providers/google-shared.js:90),
    # so the document test has to prove an application/pdf media type.
    assert uc._classify_internal_purpose(
        _anthropic_body("", [{"role": "user", "content": [
            {"text": "Describe the image."},
            {"inlineData": {"mimeType": "image/png", "data": "QQ=="}},
        ]}])) != "pdf"


def test_existing_labels_still_fire_alongside_the_pdf_rules():
    # The pdf checks sit behind the same no-system-prompt guard as the image
    # check and ahead of summarize, so none of the four earlier labels moves.
    assert uc._classify_internal_purpose({"call_type": "atranscription"}) == "transcription"
    assert uc._classify_internal_purpose({"call_type": "aembedding"}) == "embeddings"
    assert uc._classify_internal_purpose(_anthropic_body(_COMPACTION_SYSTEM, [
        {"role": "user", "content": "<conversation>"}])) == "compaction"
    assert uc._classify_internal_purpose(_image_tool_kwargs()) == "image"
    assert uc._classify_internal_purpose(
        {"messages": [{"role": "user", "content": _SUMMARIZE_USER}]}) == "summarize"
    assert uc._classify_internal_purpose(_turn_kwargs()) == ""


def test_compaction_still_wins_over_a_document_carrying_body():
    # Compaction is tested before the system-prompt guard, so a summarization
    # request that happens to quote a document block stays compaction.
    kwargs = _anthropic_body(_COMPACTION_SYSTEM, [{"role": "user", "content": [
        {"type": "document", "source": {"media_type": "application/pdf",
                                        "data": "JVBER"}},
        {"type": "text", "text": "<conversation>"},
    ]}])
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_pdf_classifier_never_raises_on_junk():
    for content in ("not-a-list", [None, 3], [{"type": None}],
                    [{"inline_data": "nope"}], [{"text": None}]):
        assert uc._classify_internal_purpose(
            _anthropic_body("", [{"role": "user", "content": content}])) in ("", "pdf")


# ============================================================================
# heartbeat — the gateway's own scheduled turn. Unlike every other label here
# it is a FULL agent turn: system prompt, tools, session history. Both shape
# guards would drop it, so it is matched ahead of them on the LAST user
# message, and the two fingerprints below are reproduced verbatim from
# /usr/lib/node_modules/openclaw in the wildclawbench-ubuntu:v1.4 image.
# ============================================================================

# resolveHeartbeatPrompt's default, dist/reply-BCcP6j4h.js:9084, verbatim:
#     return (typeof raw === "string" ? raw.trim() : "") || "Read HEARTBEAT.md
#       if it exists (workspace context). Follow it strictly. Do not infer or
#       repeat old tasks from prior chats. If nothing needs attention, reply
#       HEARTBEAT_OK.";
# Sent as the user message with no rewriting (docs/gateway/heartbeat.md:51).
_HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
    "Do not infer or repeat old tasks from prior chats. If nothing needs "
    "attention, reply HEARTBEAT_OK."
)

# appendHeartbeatWorkspacePathHint, dist/health-BxAgqqNt.js:390, verbatim:
#     if (!/heartbeat\.md/i.test(prompt)) return prompt;
#     const hint = `When reading HEARTBEAT.md, use workspace file
#       ${path.join(workspaceDir, DEFAULT_HEARTBEAT_FILENAME)...} (exact case).
#       Do not read docs/heartbeat.md.`;
#     return `${prompt}\n${hint}`;
# Unconditional for any prompt naming heartbeat.md and not reachable from
# config, so it survives an agents.defaults.heartbeat.prompt override.
_HEARTBEAT_HINT = (
    "When reading HEARTBEAT.md, use workspace file /workspace/HEARTBEAT.md "
    "(exact case). Do not read docs/heartbeat.md."
)

# appendCronStyleCurrentTimeLine, dist/reply-BCcP6j4h.js:36579 — the last
# thing put on the prompt before it becomes ctx.Body, and a no-op when the
# text already says "Current time:", so there is never more than one.
_HEARTBEAT_TIME = (
    "Current time: Fri, Sep 18, 2026 at 9:54 PM (UTC) / 2026-09-18 21:54 UTC"
)

_AGENT_SYSTEM = "You are openclaw, an autonomous coding agent."


def _heartbeat_body(prompt=_HEARTBEAT_PROMPT, hint=True, time_line=True):
    """The user message as runHeartbeatOnce assembles it, in order."""
    text = f"{prompt}\n{_HEARTBEAT_HINT}" if hint else prompt
    return f"{text}\n{_HEARTBEAT_TIME}" if time_line else text


def _heartbeat_kwargs(body=None, system=_AGENT_SYSTEM, history=()):
    messages = [*history, {"role": "user",
                           "content": body if body is not None else _heartbeat_body()}]
    return _anthropic_body(system, messages)


def test_heartbeat_named_from_the_full_shipped_request():
    assert uc._classify_internal_purpose(_heartbeat_kwargs()) == "heartbeat"


def test_heartbeat_named_on_the_prompt_head_alone():
    # The first fingerprint, with the hint and the time line both absent.
    assert uc._classify_internal_purpose(
        _heartbeat_kwargs(_heartbeat_body(hint=False, time_line=False))) == "heartbeat"


def test_heartbeat_named_on_the_path_hint_alone():
    # The second fingerprint carrying it: an operator override moved the head
    # out from under the first, but the hint is appended regardless.
    body = _heartbeat_body("Check HEARTBEAT.md and report anything broken.")
    assert uc._classify_internal_purpose(_heartbeat_kwargs(body)) == "heartbeat"


def test_heartbeat_named_on_the_path_hint_with_no_time_line():
    body = _heartbeat_body("Check HEARTBEAT.md and report anything broken.",
                           time_line=False)
    assert uc._classify_internal_purpose(_heartbeat_kwargs(body)) == "heartbeat"


def test_heartbeat_named_from_openai_normalized_messages():
    kwargs = {"messages": [{"role": "system", "content": _AGENT_SYSTEM},
                           {"role": "user", "content": _heartbeat_body()}]}
    assert uc._classify_internal_purpose(kwargs) == "heartbeat"


def test_heartbeat_named_from_content_blocks():
    kwargs = _heartbeat_kwargs([{"type": "text", "text": _heartbeat_body()}])
    assert uc._classify_internal_purpose(kwargs) == "heartbeat"


def test_heartbeat_named_under_a_full_system_prompt():
    # The system-prompt guard is what hid this row: the heartbeat is a real
    # agent turn and carries the agent's own system prompt.
    assert uc._classify_internal_purpose(
        _heartbeat_kwargs(system="You are openclaw. " + "tools. " * 200)
    ) == "heartbeat"


def test_heartbeat_named_on_a_session_that_already_has_history():
    # The arity guard is the other one: a heartbeat fires into whatever
    # session the agent is on, so it can arrive behind that run's turns.
    history = [
        {"role": "user", "content": "walk the corridor release"},
        {"role": "assistant", "content": "reading the changelog"},
        {"role": "user", "content": "and the rulebook version?"},
        {"role": "assistant", "content": "v3, in force since March"},
    ]
    assert uc._classify_internal_purpose(
        _heartbeat_kwargs(history=history)) == "heartbeat"


def test_heartbeat_matched_on_the_last_user_message_not_the_first():
    # others[0] would be the run's opening human turn, which is a task prompt.
    history = [{"role": "user", "content": "audit the release"},
               {"role": "assistant", "content": "on it"}]
    kwargs = _heartbeat_kwargs(history=history)
    assert uc._classify_internal_purpose(kwargs) == "heartbeat"
    body = kwargs["litellm_params"]["proxy_server_request"]["body"]
    assert body["messages"][0]["content"] == "audit the release"


def test_a_task_turn_mentioning_heartbeat_md_mid_text_is_not_named():
    # The whole reason both anchors are ends rather than substrings. A task
    # can legitimately quote the gateway's own prompt at the agent.
    body = (
        "the ops runbook we inherited says the agent should "
        f"\"{_HEARTBEAT_PROMPT}\" and then page whoever is on call. "
        f"it also says \"{_HEARTBEAT_HINT}\" which contradicts section 4. "
        "work out which of those two is actually current and tell me."
    )
    assert uc._classify_internal_purpose(_heartbeat_kwargs(body)) == ""


def test_a_task_turn_merely_naming_the_heartbeat_file_is_not_named():
    assert uc._classify_internal_purpose(_heartbeat_kwargs(
        "read HEARTBEAT.md and fold it into the onboarding doc")) == ""


def test_the_time_line_tolerance_does_not_swallow_a_humans_closing_sentence():
    # The tolerated trailing line is matched to its whole shape, not its
    # opening: resolveCronStyleNow (dist/reply-BCcP6j4h.js:36570) always ends
    # it on " UTC". Without that the strip would eat a human's last sentence
    # and hand the tail anchor a hint the message did not end on.
    # Quoting the hint, not opening with the prompt, so only the tail anchor
    # is in play — which is the anchor the tolerance can mislead.
    body = (_heartbeat_body("our runbook says to check HEARTBEAT.md hourly.")
            + " -- is that still what we send? check section 4.")
    assert body.endswith("section 4.")
    assert uc._classify_internal_purpose(_heartbeat_kwargs(body)) == ""


def test_the_time_line_is_still_tolerated_when_it_is_genuinely_last():
    assert uc._classify_internal_purpose(_heartbeat_kwargs(
        _heartbeat_body("Check HEARTBEAT.md now."))) == "heartbeat"


def test_heartbeat_does_not_shadow_compaction():
    """A compacted transcript containing a heartbeat turn stays compaction.

    Compaction is checked AFTER heartbeat, so this is the ordering's one real
    risk. It cannot happen: all four compaction paths wrap the serialized
    conversation in <conversation> tags and put the summarization prompt after
    it (pi-coding-agent/dist/core/compaction/compaction.js:434 and :593,
    branch-summarization.js:210), so nothing quoted inside can be first or
    last.
    """
    kwargs = {"messages": [
        {"role": "system", "content": _COMPACTION_SYSTEM},
        {"role": "user", "content":
            f"<conversation>\nuser: {_heartbeat_body()}\n"
            f"assistant: HEARTBEAT_OK\n</conversation>\n\n"
            "Produce a structured summary following the exact format."},
    ]}
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_heartbeat_does_not_shadow_a_compaction_that_ends_on_the_hint_text():
    # Even the adversarial shape: the hint is the last thing inside the tags.
    kwargs = {"messages": [
        {"role": "system", "content": _COMPACTION_SYSTEM},
        {"role": "user", "content":
            f"<conversation>\nuser: {_HEARTBEAT_HINT}\n</conversation>\n\n"
            "Produce a structured summary following the exact format."},
    ]}
    assert uc._classify_internal_purpose(kwargs) == "compaction"


def test_heartbeat_does_not_shadow_the_media_summarizer():
    assert uc._classify_internal_purpose(
        {"messages": [{"role": "user", "content": _SUMMARIZE_USER}]}) == "summarize"


def test_heartbeat_does_not_shadow_embeddings():
    kwargs = {"call_type": "aembedding", "litellm_params": {
        "proxy_server_request": {"body": {"model": "text-embedding-3-small",
                                          "input": _heartbeat_body()}}}}
    assert uc._classify_internal_purpose(kwargs) == "embeddings"


def test_heartbeat_does_not_shadow_transcription():
    assert uc._classify_internal_purpose(
        {"call_type": "atranscription",
         "messages": [{"role": "user", "content": _heartbeat_body()}]}) == "transcription"


def test_heartbeat_does_not_shadow_the_image_tool():
    kwargs = _anthropic_body("", [{"role": "user", "content": [
        {"type": "text", "text": "Describe the image."},
        {"type": "image", "source": {"data": "QQ=="}},
    ]}])
    assert uc._classify_internal_purpose(kwargs) == "image"


def test_heartbeat_does_not_shadow_the_pdf_tool():
    assert uc._classify_internal_purpose(
        _pdf_native_anthropic_kwargs()) == "pdf"


def test_a_system_less_heartbeat_is_heartbeat_not_summarize():
    # summarizeText sends no system prompt either, so without the heartbeat
    # test placed ahead of it the labels would be decided by the fall-through.
    assert uc._classify_internal_purpose(
        _heartbeat_kwargs(system="")) == "heartbeat"


def test_heartbeat_classifier_never_raises_on_junk():
    for messages in ([{"role": "user", "content": None}],
                     [{"role": "user"}],
                     [{"role": None, "content": _heartbeat_body()}],
                     [None],
                     []):
        assert uc._classify_internal_purpose(
            _anthropic_body(_AGENT_SYSTEM, messages)) in ("", "heartbeat")


def test_write_row_tags_a_heartbeat_call(usage_path, stub_completion_cost):
    uc._write_row({"model": "claude-opus-5", **_heartbeat_kwargs()},
                  _resp(_chat_usage()), T0, T1)
    row = _read_rows(usage_path)[0]
    assert row["purpose"] == "heartbeat"
    assert row["kind"] == "agent"
    assert set(row.keys()) == EXPECTED_KEYS | {"purpose"}


# ============================================================================
# Module-level _PATH env override (both modules read env at import)
# ============================================================================


def test_primary_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LITELLM_USAGE_LOG_PATH", str(tmp_path / "custom.jsonl"))
    reloaded = importlib.reload(uc)
    try:
        assert reloaded._PATH == str(tmp_path / "custom.jsonl")
    finally:
        monkeypatch.delenv("LITELLM_USAGE_LOG_PATH", raising=False)
        importlib.reload(uc)


def test_oauth_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_OAUTH_USAGE_LOG_PATH", str(tmp_path / "oauth_custom.jsonl"))
    reloaded = importlib.reload(oc)
    try:
        assert reloaded._PATH == str(tmp_path / "oauth_custom.jsonl")
    finally:
        monkeypatch.delenv("WCB_OAUTH_USAGE_LOG_PATH", raising=False)
        importlib.reload(oc)
