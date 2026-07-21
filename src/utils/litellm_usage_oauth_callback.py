"""OAuth-path sibling callback that writes `usage_oauth.jsonl` with the
Bedrock-equivalent cost of each OAuth-routed request.

Rationale
---------
`litellm_usage_callback.py` is the SOLE writer of the 11-key `usage.jsonl`
schema (per src/utils/AGENTS.md invariant + tests/test_litellm_headroom_
callback.py:351 enforcement). On the OAuth trajectory path, that row will
legitimately show `cost_usd: 0` because the OAuth block sets all `*cost_per_
token: 0` (subscription is prepaid; per-request marginal cost is $0).

This SEPARATE JSONL preserves the audit trail for leaderboard fairness by
recording what an equivalent Bedrock call would have cost at the current
Anthropic public rates. It is a strict superset (adds `cost_bedrock_
equivalent` + `route` fields alongside the same token counts), NEVER merged
back into `usage.jsonl`.

Mounted inside the sidecar via docker `-v` bind mount, referenced from the
LiteLLM YAML as:

    litellm_settings:
      callbacks: [
        "litellm_usage_callback.proxy_handler_instance",
        "litellm_usage_oauth_callback.oauth_usage_callback_instance",
      ]

Sibling reader: `extract_oauth_usage_from_litellm_log` in `src/utils/grading.py`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    class CustomLogger:  # type: ignore[no-redef]
        pass


_PATH = os.environ.get(
    "WCB_OAUTH_USAGE_LOG_PATH",
    "/var/litellm_usage/usage_oauth.jsonl",
)
_LOCK = threading.Lock()

_ANTHROPIC_OPUS_PRICE = {
    "input": 5e-6,
    "output": 25e-6,
    "cache_read": 5e-7,
    "cache_creation": 6.25e-6,
}

# Claude Fable 5 public rates ($10/$50 per MTok; cache read 0.1x input, cache
# write 1.25x input at the default 5-minute TTL).
_ANTHROPIC_FABLE_PRICE = {
    "input": 1e-5,
    "output": 5e-5,
    "cache_read": 1e-6,
    "cache_creation": 1.25e-5,
}


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    for method_name in ("model_dump", "dict"):
        meth = getattr(usage, method_name, None)
        if callable(meth):
            try:
                result = meth()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    fallback = getattr(usage, "__dict__", {}) or {}
    return fallback if isinstance(fallback, dict) else {}


def _is_oauth_route(model: str) -> bool:
    if not model:
        return False
    m = model.lower()
    return "opus" in m or "fable" in m


def _price_table(model: str) -> dict[str, float]:
    if "fable" in (model or "").lower():
        return _ANTHROPIC_FABLE_PRICE
    return _ANTHROPIC_OPUS_PRICE


def _bedrock_equivalent_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
    model: str = "",
) -> float:
    price = _price_table(model)
    return (
        input_tokens * price["input"]
        + output_tokens * price["output"]
        + cache_read * price["cache_read"]
        + cache_write * price["cache_creation"]
    )


def _write_row(kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
    try:
        model = kwargs.get("model") or ""
        if not _is_oauth_route(model):
            return

        usage_dict = _usage_to_dict(getattr(response_obj, "usage", None))
        if not usage_dict and isinstance(response_obj, dict):
            usage_dict = _usage_to_dict(response_obj.get("usage"))

        cache_read = _int((usage_dict.get("prompt_tokens_details") or {}).get("cached_tokens"))
        if not cache_read:
            cache_read = _int(usage_dict.get("cache_read_input_tokens"))
        cache_write = _int(usage_dict.get("cache_creation_input_tokens"))

        prompt_tokens_raw = _int(usage_dict.get("prompt_tokens"))
        if not prompt_tokens_raw:
            prompt_tokens_raw = _int(usage_dict.get("input_tokens"))
        output_tokens = _int(usage_dict.get("completion_tokens"))
        if not output_tokens:
            output_tokens = _int(usage_dict.get("output_tokens"))

        non_cached = prompt_tokens_raw - cache_read - cache_write
        if non_cached < 0:
            non_cached = 0
        input_tokens = non_cached

        cost_equiv = _bedrock_equivalent_cost(
            input_tokens, output_tokens, cache_read, cache_write, model=model
        )

        duration = 0.0
        try:
            duration = (end_time - start_time).total_seconds()
        except Exception:
            pass

        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "route": "claude_oauth_bridge",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "cost_actual": 0.0,
            "cost_bedrock_equivalent": round(cost_equiv, 6),
            "duration_s": round(duration, 3),
        }
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception as exc:  # pragma: no cover
        try:
            sys.stderr.write(f"[litellm_usage_oauth_callback] error: {exc}\n")
        except Exception:
            pass


class OAuthUsageWriter(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _write_row(kwargs, response_obj, start_time, end_time)


oauth_usage_callback_instance = OAuthUsageWriter()
