"""Tests for src/utils/codex_oauth/errors.py — OpenAI/Codex error classifier."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.codex_oauth.errors import (
    _CAP_SIGNALS,
    ClassifiedError,
    ErrorKind,
    classify_openai_error,
    extract_retry_after,
)


# ---------------------------------------------------------------------------
# ErrorKind
# ---------------------------------------------------------------------------


def test_error_kind_members_are_exact():
    assert {k.name for k in ErrorKind} == {
        "OK", "AUTH", "RATE_LIMIT", "CAP", "TRANSIENT",
        "BAD_REQUEST", "NOT_FOUND", "UNKNOWN",
    }


def test_error_kind_is_str_enum_with_lowercase_values():
    assert ErrorKind.RATE_LIMIT.value == "rate_limit"
    assert ErrorKind.CAP == "cap"


# ---------------------------------------------------------------------------
# classify_openai_error
# ---------------------------------------------------------------------------


def test_no_status_code_is_transient():
    result = classify_openai_error(None)
    assert result.kind is ErrorKind.TRANSIENT
    assert result.status_code is None
    assert result.message == "no response"


def test_2xx_is_ok():
    ok_body: Any = b'{"ok":true}'
    for status in (200, 201, 299):
        result = classify_openai_error(status, ok_body)
        assert result.kind is ErrorKind.OK
        assert result.message == "ok"


def test_401_without_cap_signal_is_auth():
    result = classify_openai_error(401, '{"error":{"message":"invalid token"}}')
    assert result.kind is ErrorKind.AUTH
    assert result.status_code == 401


def test_403_without_cap_signal_is_auth():
    assert classify_openai_error(403, "forbidden").kind is ErrorKind.AUTH


def test_403_with_cap_signal_is_cap():
    assert classify_openai_error(403, "You have exceeded your usage limit").kind is ErrorKind.CAP


def test_401_with_cap_signal_is_cap():
    assert classify_openai_error(401, "insufficient_quota for this plan").kind is ErrorKind.CAP


def test_429_plain_is_rate_limit():
    result = classify_openai_error(429, "slow down")
    assert result.kind is ErrorKind.RATE_LIMIT
    assert result.status_code == 429


def test_429_with_cap_signal_is_cap():
    assert classify_openai_error(429, "You reached your weekly limit").kind is ErrorKind.CAP


def test_every_cap_signal_upgrades_429_to_cap():
    for signal in _CAP_SIGNALS:
        body = f"upstream said: {signal} — try later"
        assert classify_openai_error(429, body).kind is ErrorKind.CAP, signal


def test_cap_signal_matching_is_case_insensitive():
    assert classify_openai_error(429, "QUOTA EXCEEDED").kind is ErrorKind.CAP


def test_404_is_not_found():
    result = classify_openai_error(404, "model is not supported")
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.retry_after is None


def test_400_and_422_are_bad_request():
    assert classify_openai_error(400, "Input must be a list").kind is ErrorKind.BAD_REQUEST
    assert classify_openai_error(422, "unprocessable").kind is ErrorKind.BAD_REQUEST


def test_5xx_is_transient():
    for status in (500, 502, 503, 504):
        assert classify_openai_error(status, "bad gateway").kind is ErrorKind.TRANSIENT


def test_other_4xx_is_unknown():
    assert classify_openai_error(418, "teapot").kind is ErrorKind.UNKNOWN


def test_bytes_body_is_decoded_into_message():
    body: Any = b'{"error":"bad"}'
    result = classify_openai_error(400, body)
    assert result.message == '{"error":"bad"}'


def test_non_str_body_is_json_serialized_into_message():
    body: Any = {"error": "bad"}
    result = classify_openai_error(400, body)
    assert result.message == '{"error": "bad"}'


def test_retry_after_header_is_carried_onto_the_classification():
    result = classify_openai_error(429, "slow down", {"retry-after": "12"})
    assert result.retry_after == 12.0


# ---------------------------------------------------------------------------
# ClassifiedError properties
# ---------------------------------------------------------------------------


def _err(kind: ErrorKind) -> ClassifiedError:
    return ClassifiedError(kind, 500, "msg")


def test_retryable_is_transient_and_rate_limit_only():
    retryable = {k for k in ErrorKind if _err(k).retryable}
    assert retryable == {ErrorKind.TRANSIENT, ErrorKind.RATE_LIMIT}


def test_should_rotate_account_is_cap_and_rate_limit_only():
    rotate = {k for k in ErrorKind if _err(k).should_rotate_account}
    assert rotate == {ErrorKind.CAP, ErrorKind.RATE_LIMIT}


def test_should_refresh_token_is_auth_only():
    refresh = {k for k in ErrorKind if _err(k).should_refresh_token}
    assert refresh == {ErrorKind.AUTH}


def test_classified_error_retry_after_defaults_to_none():
    assert ClassifiedError(ErrorKind.OK, 200, "ok").retry_after is None


# ---------------------------------------------------------------------------
# extract_retry_after
# ---------------------------------------------------------------------------


def test_extract_retry_after_numeric_header():
    assert extract_retry_after({"retry-after": "30"}) == 30.0


def test_extract_retry_after_capitalized_header():
    assert extract_retry_after({"Retry-After": "7.5"}) == 7.5


def test_extract_retry_after_falls_back_to_ratelimit_reset_headers():
    assert extract_retry_after({"x-ratelimit-reset-requests": "60"}) == 60.0
    assert extract_retry_after({"x-ratelimit-reset-tokens": "90"}) == 90.0


def test_extract_retry_after_parses_unit_suffixed_header():
    assert extract_retry_after({"retry-after": "500ms"}) == 0.5
    assert extract_retry_after({"retry-after": "2m"}) == 120.0
    assert extract_retry_after({"retry-after": "15s"}) == 15.0


def test_extract_retry_after_parses_seconds_from_body():
    assert extract_retry_after(None, "Rate limit reached. Please try again in 5s.") == 5.0


def test_extract_retry_after_parses_milliseconds_from_body():
    assert extract_retry_after(None, "please try again in 500ms") == 0.5


def test_extract_retry_after_parses_minutes_from_body():
    assert extract_retry_after(None, "try again in 2m") == 120.0


def test_extract_retry_after_body_without_unit_defaults_to_seconds():
    assert extract_retry_after({}, "try again in 8") == 8.0


def test_extract_retry_after_none_when_absent():
    assert extract_retry_after(None, "") is None
    assert extract_retry_after({}, "no hint here") is None
    assert extract_retry_after({"retry-after": ""}, "") is None
