"""LiteLLM proxy pre-call hook that compresses the agent's prompt via Headroom.

Mounted into the LiteLLM sidecar container at /app/litellm_headroom_callback.py
and referenced from the proxy YAML as:

    litellm_settings:
      callbacks:
        - "litellm_usage_callback.proxy_handler_instance"
        - "litellm_headroom_callback.headroom_callback_instance"

ORDERING MATTERS: this callback's `async_pre_call_hook` runs BEFORE LiteLLM
applies provider-specific transforms (cache_control_injection_points, thinking,
reasoning_effort, Bedrock prompt format) — so headroom mutations survive
downstream provider customizations. The usage-callback in the slot above only
overrides `async_log_success_event` (it never overrides `async_pre_call_hook`),
so LiteLLM's pre-call dispatch loop SKIPS it; the two callbacks therefore do
not contend in the pre-call phase. Post-call, the usage logger sees
`kwargs["messages"]` AS COMPRESSED, so its 11-key JSONL row will record the
POST-compression `prompt_tokens` exactly as Bedrock/OpenAI billed them.

# ────────────────────────────────────────────────────────────────────────────
# TOKEN-TRACKING INVARIANT (user m0130 mandate)
# ────────────────────────────────────────────────────────────────────────────
# This module MUST NOT touch the usage-tracking pipeline:
#   - The 11-key JSONL schema in `litellm_usage_callback.py` (ts, model, kind,
#     input_tokens, output_tokens, total_tokens, cache_read_tokens,
#     cache_write_tokens, audio_seconds, cost_usd, duration_s) is canonical.
#   - The non-cached input math (`prompt_tokens - cache_read - cache_write`,
#     clamped ≥0) at `litellm_usage_callback.py:166` is load-bearing.
#   - `litellm.completion_cost()` over `kwargs["response_cost"]` cost path is
#     load-bearing.
# This file writes ONLY to its OWN separate sink (KENSEI_AGENT_HEADROOM_LOG_PATH
# defaulting to /var/litellm_headroom/headroom.jsonl) — never to
# LITELLM_USAGE_LOG_PATH. Schema disjoint by design.
#
# ────────────────────────────────────────────────────────────────────────────
# WHY we bypass Headroom's bundled `HeadroomCallback` class
# ────────────────────────────────────────────────────────────────────────────
# Headroom ships `headroom.integrations.litellm_callback.HeadroomCallback` whose
# `async_pre_call_hook` signature is:
#   async def async_pre_call_hook(self, user_api_key, data, call_type) -> dict
# This is a 3-arg SDK signature. LiteLLM's proxy invokes pre-call hooks with
# canonical 4-arg KEYWORDS:
#   user_api_key_dict=..., cache=..., data=..., call_type=...
# So `HeadroomCallback.async_pre_call_hook(...)` raises `TypeError: got an
# unexpected keyword argument 'user_api_key_dict'` on the FIRST proxy request.
# Bug exists in all 0.x versions including 0.24.0 latest.
#
# Second reason to bypass: `HeadroomCallback.__init__` does NOT accept a
# `CompressConfig`. It only takes (min_tokens, model_limit, hooks, api_key,
# api_url). To get `compress_system_messages=False` (load-bearing — system
# prompts carry tool-use schemas and openclaw's verbatim instruction format)
# we MUST call `headroom.compress.compress(messages, model, config=...)` ourself.
#
# Conclusion: this module is a thin CustomLogger subclass that calls
# `headroom.compress.compress()` directly with our own CompressConfig.
#
# ────────────────────────────────────────────────────────────────────────────
# FAIL-OPEN POLICY (production-safety)
# ────────────────────────────────────────────────────────────────────────────
# Any exception in this module — ImportError, compress() failure, telemetry
# write failure — MUST result in the original `data` dict being returned
# unmodified. The agent's request proceeds with the un-compressed prompt.
# Compression is an optimization, NEVER a correctness dependency.
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
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass


# Tri-state probe — None=not yet probed, True/False=result. One-shot import
# attempt at module load avoids repeating the cost (and avoids re-raising
# ImportError 1000 times per batch for batches where headroom isn't installed,
# which would happen if the operator forgot to switch to the custom image).
_HEADROOM_AVAILABLE: bool | None = None
_compress = None
_CompressConfig = None


def _probe_headroom() -> bool:
    global _HEADROOM_AVAILABLE, _compress, _CompressConfig
    if _HEADROOM_AVAILABLE is not None:
        return _HEADROOM_AVAILABLE
    try:
        from headroom import compress as _compress_fn, CompressConfig as _CompressConfig_cls  # type: ignore[import-not-found]
        _compress = _compress_fn
        _CompressConfig = _CompressConfig_cls
        _HEADROOM_AVAILABLE = True
    except Exception as exc:
        sys.stderr.write(
            f"[litellm_headroom_callback] headroom-ai not importable, "
            f"compression DISABLED (sidecar will run uncompressed): {exc}\n"
        )
        _HEADROOM_AVAILABLE = False
    return _HEADROOM_AVAILABLE


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _enabled() -> bool:
    return _truthy(os.environ.get("KENSEI_AGENT_HEADROOM_ENABLED"))


def _target_ratio() -> float:
    try:
        return float(os.environ.get("KENSEI_AGENT_HEADROOM_TARGET_RATIO", "0.4"))
    except (TypeError, ValueError):
        return 0.4


def _protect_recent() -> int:
    try:
        return int(os.environ.get("KENSEI_AGENT_HEADROOM_PROTECT_RECENT", "4"))
    except (TypeError, ValueError):
        return 4


def _min_tokens() -> int:
    try:
        return int(os.environ.get("KENSEI_AGENT_HEADROOM_MIN_TOKENS", "2000"))
    except (TypeError, ValueError):
        return 2000


_LOG_PATH = os.environ.get(
    "KENSEI_AGENT_HEADROOM_LOG_PATH",
    "/var/litellm_headroom/headroom.jsonl",
)
_LOG_LOCK = threading.Lock()


def _model_hint(model: str) -> str:
    # Headroom's tokenizer estimates token counts by string-matching the model
    # name against its internal model database. Bedrock ARNs like
    # `bedrock/arn:aws:bedrock:us-east-1:.../application-inference-profile/<id>`
    # won't match anything, so headroom falls back to a generic tokenizer that
    # over-estimates Anthropic models by ~30%. Substring-hint Opus 4.6 ARNs and
    # bedrock/anthropic.claude-opus-4-6-v1 routes to a known Anthropic model so
    # the budget math matches what Bedrock actually counts.
    m = (model or "").lower()
    if "application-inference-profile" in m or "anthropic.claude-opus" in m or m.startswith("bedrock/anthropic"):
        return "anthropic/claude-opus-4-20250514"
    return model


def _write_telemetry(row: dict[str, Any]) -> None:
    # Best-effort: any failure to write telemetry MUST NOT propagate; the
    # request has already been compressed (or skipped) by the time we get here,
    # so a failed write should not retroactively unwind that.
    try:
        with _LOG_LOCK:
            os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception as exc:  # pragma: no cover - sink failure
        sys.stderr.write(
            f"[litellm_headroom_callback] telemetry write failed: {exc}\n"
        )


class HeadroomPreCallCompressor(CustomLogger):
    """Compresses `data["messages"]` in-place via Headroom before LiteLLM dispatch.

    Subclasses LiteLLM's `CustomLogger`. Implements ONLY `async_pre_call_hook`
    using LiteLLM's canonical 4-arg keyword signature
    `(user_api_key_dict, cache, data, call_type)`. Does NOT implement
    `async_log_success_event` so it does not contend with
    `litellm_usage_callback.UsageWriter`.

    NEVER raises into LiteLLM's pre-call dispatch loop. Any compression failure
    returns the original `data` dict unchanged (fail-open).
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> dict | None:
        # `anthropic_messages` is the call_type LiteLLM router assigns to
        # /v1/messages (litellm/router.py: `call_type="anthropic_messages"`)
        # — the openclaw agent's path. Without it the hook silently no-ops
        # for every Anthropic-format request, leaving telemetry empty even
        # when KENSEI_AGENT_HEADROOM_ENABLED=true and the image+volume mount
        # are wired correctly.
        if call_type not in ("completion", "acompletion", "anthropic_messages"):
            return data
        if not _enabled():
            return data
        if not _probe_headroom():
            return data

        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return data
        model = data.get("model", "") or ""

        try:
            # Headroom defaults — for multi-turn tool-loops (agent path):
            #   * skip_user_messages=True (default) — preserves user turns
            #   * skip_system=True (default) — preserves system prompt
            #   * compress_assistant_text_blocks=False (default) — preserves
            #     assistant reasoning text
            # i.e. ONLY tool_result content blocks compress. This matches the
            # Headroom-recommended pattern for agent loops where tool outputs
            # (large JSON/log/diff blobs) dominate the prompt. The judge path
            # in `judge_litellm.py` sets compress_user_messages=True because
            # there it's the only big block — agent path uses defaults.
            cfg = _CompressConfig(  # type: ignore[misc]
                compress_system_messages=False,
                protect_recent=_protect_recent(),
                min_tokens_to_compress=_min_tokens(),
                target_ratio=_target_ratio(),
            )
            hint = _model_hint(model)
            result = _compress(messages, model=hint, config=cfg)  # type: ignore[misc]
        except Exception as exc:
            sys.stderr.write(
                f"[litellm_headroom_callback] compress() raised, sending "
                f"uncompressed: model={model!r} err={exc!r}\n"
            )
            return data

        saved = int(getattr(result, "tokens_saved", 0) or 0)
        if saved <= 0:
            return data

        new_messages = getattr(result, "messages", None)
        if not isinstance(new_messages, list) or not new_messages:
            return data
        data["messages"] = new_messages

        _write_telemetry({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "call_type": call_type,
            "tokens_before": int(getattr(result, "tokens_before", 0) or 0),
            "tokens_after": int(getattr(result, "tokens_after", 0) or 0),
            "tokens_saved": saved,
            "compression_ratio": float(getattr(result, "compression_ratio", 0.0) or 0.0),
            "transforms_applied": list(getattr(result, "transforms_applied", []) or []),
        })
        return data


headroom_callback_instance = HeadroomPreCallCompressor()
