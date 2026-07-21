"""Classify OpenAI / ChatGPT-backend errors for the codex OAuth MITM proxy.

Codex 0.121 talks to ``https://chatgpt.com/backend-api/codex`` (Responses over a
WebSocket, plus a couple of HTTPS calls). The proxy inspects the upstream
response status so it can decide whether to rotate to another pooled account
(subscription cap), retry, or just pass the error through.

Mirror of ``src.utils.claude_oauth.errors`` but without the Anthropic-specific
``anthropic-ratelimit-*`` headers -- ChatGPT surfaces caps as a 429 whose
``Retry-After`` (or body ``reset_after_seconds``) is large.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Tuple

# Retry-After boundary between a short transient throttle (retry inline) and a
# hard subscription cap (rotate account / pause). ChatGPT plan caps report
# values well above this; transient throttles are single/low double digits.
TRANSIENT_RETRY_AFTER_THRESHOLD = 60


class ErrorKind(str, Enum):
    """Coarse classification of a ChatGPT-backend error."""

    OK = "ok"
    TRANSIENT_THROTTLE = "transient_throttle"
    SUBSCRIPTION_CAP = "subscription_cap"
    OAUTH_TOKEN_INVALID = "oauth_token_invalid"
    ACCOUNT_RESTRICTED = "account_restricted"
    BILLING_ERROR = "billing_error"
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_5XX = "upstream_5xx"
    UNKNOWN = "unknown"

    @property
    def is_retryable(self) -> bool:
        """Whether the proxy should retry this error class itself."""
        return self in {ErrorKind.TRANSIENT_THROTTLE, ErrorKind.UPSTREAM_5XX}

    @property
    def is_account_problem(self) -> bool:
        """Whether rotating to a different pooled account would help."""
        return self in {
            ErrorKind.SUBSCRIPTION_CAP,
            ErrorKind.OAUTH_TOKEN_INVALID,
            ErrorKind.ACCOUNT_RESTRICTED,
            ErrorKind.BILLING_ERROR,
        }


@dataclass
class ClassifiedError:
    kind: ErrorKind
    status_code: int
    retry_after_seconds: Optional[int]
    reset_at_unix: Optional[float]
    message: str


def _parse_int_header(headers: Mapping[str, str], name: str) -> Optional[int]:
    val = headers.get(name) or headers.get(name.lower())
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def extract_retry_after(headers: Mapping[str, str]) -> Optional[int]:
    """Best-effort seconds-to-retry from the ``Retry-After`` header."""
    ra = _parse_int_header(headers, "Retry-After")
    if ra is not None and ra >= 0:
        return ra
    return None


def _decode_body(body: bytes | str | None) -> Tuple[Optional[str], Optional[int]]:
    """Pull (message, reset_after_seconds) out of an OpenAI error body."""
    if not body:
        return None, None
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return (text[:200] if text else None), None
    if not isinstance(obj, dict):
        return None, None
    err = obj.get("error")
    msg = None
    if isinstance(err, dict):
        msg = err.get("message")
    elif isinstance(err, str):
        msg = err
    msg = msg or obj.get("detail")
    reset = obj.get("reset_after_seconds")
    if isinstance(err, dict):
        reset = reset or err.get("reset_after_seconds")
    try:
        reset = int(reset) if reset is not None else None
    except (TypeError, ValueError):
        reset = None
    return (str(msg)[:200] if msg else None), reset


def classify_openai_error(
    status_code: int,
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ClassifiedError:
    """Map a ChatGPT-backend upstream status into a ``ClassifiedError``.

      - 2xx / 101 (WS upgrade)                                -> OK
      - 429 with retry-after >= 60s (or body reset large)     -> SUBSCRIPTION_CAP
      - 429 otherwise                                         -> TRANSIENT_THROTTLE
      - 401                                                   -> OAUTH_TOKEN_INVALID
      - 403                                                   -> ACCOUNT_RESTRICTED
      - 402                                                   -> BILLING_ERROR
      - 400                                                   -> INVALID_REQUEST
      - 5xx                                                   -> UPSTREAM_5XX
    """
    headers = headers or {}
    message, body_reset = _decode_body(body)
    retry_after = extract_retry_after(headers)
    if retry_after is None and body_reset is not None:
        retry_after = body_reset
    reset_at = time.time() + retry_after if retry_after else None
    message = message or f"HTTP {status_code}"

    if 200 <= status_code < 300 or status_code == 101:
        return ClassifiedError(ErrorKind.OK, status_code, None, None, "ok")

    if status_code == 429:
        is_cap = retry_after is not None and retry_after >= TRANSIENT_RETRY_AFTER_THRESHOLD
        kind = ErrorKind.SUBSCRIPTION_CAP if is_cap else ErrorKind.TRANSIENT_THROTTLE
        return ClassifiedError(kind, 429, retry_after, reset_at, message)

    mapping = {
        401: ErrorKind.OAUTH_TOKEN_INVALID,
        403: ErrorKind.ACCOUNT_RESTRICTED,
        402: ErrorKind.BILLING_ERROR,
        400: ErrorKind.INVALID_REQUEST,
    }
    if status_code in mapping:
        return ClassifiedError(mapping[status_code], status_code, None, None, message)

    if 500 <= status_code < 600:
        return ClassifiedError(ErrorKind.UPSTREAM_5XX, status_code, retry_after, reset_at, message)

    return ClassifiedError(ErrorKind.UNKNOWN, status_code, None, None, message)
