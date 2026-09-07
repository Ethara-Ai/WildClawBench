"""Tests for src/utils/codex_oauth/recovery.py — transient-retry wrapper.

``sleep`` is always injected as a no-op so the suite never actually waits, and
the backoff schedule is asserted through the recorded sleep arguments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.utils.codex_oauth.recovery import (
    _DEFAULT_BACKOFF,
    _backoff_schedule,
    _extract_retry_after_from_error,
    _matches_rate_limit_phrase,
    is_transient,
    run_with_recovery,
)

_BACKOFF_ENV = "KAIJU_CODEX_TRANSIENT_BACKOFF"


class _Recorder:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class _HeaderResponse:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


class _RateLimited(Exception):
    def __init__(self, message: str, headers: dict) -> None:
        super().__init__(message)
        self.response = _HeaderResponse(headers)


def _flaky(failures: int, exc: BaseException):
    state = {"calls": 0}

    def fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] <= failures:
            raise exc
        return ("ok", args, kwargs, state["calls"])

    return fn


# ---------------------------------------------------------------------------
# is_transient — signal substrings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "peer closed connection",
    "httpx.ReadError: incomplete chunked read",
    "connection reset by peer",
    "connection aborted",
    "server disconnected without sending a response",
    "read timeout after 600s",
    "request timed out",
    "service temporarily unavailable",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    "httpx.RemoteProtocolError: bad chunk",
    "ECONNRESET",
    "TransientLLMError: upstream hiccup",
    "MidStreamFallbackError",
    "APIConnectionError",
    "APITimeoutError",
    "rate_limit_error",
    "litellm.RateLimitError",
    "429 Too Many Requests",
    "RESOURCE_EXHAUSTED",
])
def test_is_transient_true_for_signal_substrings(message):
    assert is_transient(RuntimeError(message))


def test_is_transient_true_for_bare_rate_limit_phrase():
    assert is_transient(RuntimeError("upstream hit a rate limit"))
    assert is_transient(RuntimeError("request was rate-limited"))
    assert is_transient(RuntimeError("we are being rate limiting right now"))


@pytest.mark.parametrize("message", [
    "this is not a rate limit problem",
    "the endpoint has no rate limit",
    "not rate limited, just slow",
    "isn't a rate limit issue",
])
def test_is_transient_false_for_negated_rate_limit_phrase(message):
    """Negation guard: log text that merely *mentions* rate limiting must not
    trigger a retry, otherwise a fatal error would be retried five times."""
    assert not is_transient(RuntimeError(message))
    assert not _matches_rate_limit_phrase(message)


def test_matches_rate_limit_phrase_true_at_message_start():
    assert _matches_rate_limit_phrase("rate limit reached")


@pytest.mark.parametrize("message", [
    "openai.InternalServerError: something broke",
    "500: Internal Server Error",
    "500 Internal Server Error",
    "error type: InternalServerError",
    "provider returned .internal_server_error",
])
def test_is_transient_true_for_internal_server_error_shapes(message):
    assert is_transient(RuntimeError(message))


@pytest.mark.parametrize("message", [
    '{"type":"overloaded_error","message":"upstream busy"}',
    "error type: overloaded",
    "Anthropic API overloaded",
])
def test_is_transient_true_for_overload_shapes(message):
    assert is_transient(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "invalid_request_error: bad model",
    "401 unauthorized",
    "model is not supported",
    "an internal server error page was mentioned in the docs",
])
def test_is_transient_false_for_fatal_errors(message):
    assert not is_transient(RuntimeError(message))


# ---------------------------------------------------------------------------
# _backoff_schedule
# ---------------------------------------------------------------------------


def test_backoff_schedule_default(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    assert _DEFAULT_BACKOFF == (5, 10, 20, 40, 60)
    assert _backoff_schedule() == _DEFAULT_BACKOFF


def test_backoff_schedule_env_override(monkeypatch):
    monkeypatch.setenv(_BACKOFF_ENV, "1,2.5, 3 ")
    assert _backoff_schedule() == (1.0, 2.5, 3.0)


def test_backoff_schedule_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_BACKOFF_ENV, "1,not-a-number")
    assert _backoff_schedule() == _DEFAULT_BACKOFF


def test_backoff_schedule_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_BACKOFF_ENV, "")
    assert _backoff_schedule() == _DEFAULT_BACKOFF


# ---------------------------------------------------------------------------
# run_with_recovery
# ---------------------------------------------------------------------------


def test_run_with_recovery_returns_immediately_on_success(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    result = run_with_recovery(lambda a, b=0: a + b, 1, b=2, sleep=sleeper)
    assert result == 3
    assert sleeper.waits == []


def test_run_with_recovery_retries_twice_then_succeeds(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    fn = _flaky(2, RuntimeError("peer closed connection"))
    kind, args, kwargs, calls = run_with_recovery(fn, "x", sleep=sleeper, flag=True)
    assert kind == "ok"
    assert args == ("x",)
    assert kwargs == {"flag": True}
    assert calls == 3
    assert sleeper.waits == [5, 10]


def test_run_with_recovery_uses_full_default_backoff_schedule(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    with pytest.raises(RuntimeError):
        run_with_recovery(_flaky(99, RuntimeError("read timeout")), sleep=sleeper)
    assert sleeper.waits == [5, 10, 20, 40, 60]


def test_run_with_recovery_honours_env_backoff_override(monkeypatch):
    monkeypatch.setenv(_BACKOFF_ENV, "1,2")
    sleeper = _Recorder()
    with pytest.raises(RuntimeError):
        run_with_recovery(_flaky(99, RuntimeError("503 service unavailable")), sleep=sleeper)
    assert sleeper.waits == [1, 2]


def test_run_with_recovery_non_transient_propagates_without_sleeping(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()

    def fn():
        raise ValueError("invalid_request_error: bad model")

    with pytest.raises(ValueError, match="invalid_request_error"):
        run_with_recovery(fn, sleep=sleeper)
    assert sleeper.waits == []


def test_run_with_recovery_respects_max_retries_cap(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    with pytest.raises(RuntimeError, match="peer closed connection"):
        run_with_recovery(_flaky(99, RuntimeError("peer closed connection")),
                          max_retries=2, sleep=sleeper)
    assert sleeper.waits == [5, 10]


def test_run_with_recovery_max_retries_zero_raises_on_first_failure(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    with pytest.raises(RuntimeError):
        run_with_recovery(_flaky(1, RuntimeError("read timeout")),
                          max_retries=0, sleep=sleeper)
    assert sleeper.waits == []


def test_run_with_recovery_keyboard_interrupt_is_never_retried(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()

    def fn():
        raise KeyboardInterrupt("peer closed connection")

    with pytest.raises(KeyboardInterrupt):
        run_with_recovery(fn, sleep=sleeper)
    assert sleeper.waits == []


def test_run_with_recovery_prefers_longer_server_retry_after_hint(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    exc = _RateLimited("429 too many requests", {"Retry-After": "42"})
    with pytest.raises(_RateLimited):
        run_with_recovery(_flaky(99, exc), max_retries=1, sleep=sleeper)
    assert sleeper.waits == [42]


def test_run_with_recovery_caps_pathological_retry_after_at_ten_minutes(monkeypatch):
    monkeypatch.delenv(_BACKOFF_ENV, raising=False)
    sleeper = _Recorder()
    exc = _RateLimited("429 too many requests", {"Retry-After": "86400"})
    with pytest.raises(_RateLimited):
        run_with_recovery(_flaky(99, exc), max_retries=1, sleep=sleeper)
    assert sleeper.waits == [600]


# ---------------------------------------------------------------------------
# _extract_retry_after_from_error
# ---------------------------------------------------------------------------


def test_extract_retry_after_from_response_headers():
    assert _extract_retry_after_from_error(
        _RateLimited("boom", {"Retry-After": "30"})) == 30


def test_extract_retry_after_from_lowercase_response_header():
    assert _extract_retry_after_from_error(
        _RateLimited("boom", {"retry-after": "17"})) == 17


def test_extract_retry_after_from_message_text():
    assert _extract_retry_after_from_error(
        RuntimeError("rate limited; retry-after: 45 seconds")) == 45


def test_extract_retry_after_message_underscore_and_space_forms():
    assert _extract_retry_after_from_error(RuntimeError("retry_after: 9")) == 9
    assert _extract_retry_after_from_error(RuntimeError("Retry After 11")) == 11


def test_extract_retry_after_none_when_no_hint():
    assert _extract_retry_after_from_error(RuntimeError("peer closed connection")) is None


def test_extract_retry_after_none_for_unparseable_header_value():
    assert _extract_retry_after_from_error(
        _RateLimited("boom", {"Retry-After": "soon"})) is None
