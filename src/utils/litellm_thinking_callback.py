"""LiteLLM sidecar pre-call hook that forces visible extended-thinking TEXT.

Mounted into the LiteLLM sidecar at /app/litellm_thinking_callback.py and
referenced from the proxy YAML as:

    litellm_settings:
      callbacks:
        - "litellm_thinking_callback.thinking_callback_instance"
        - ...

WHY THIS EXISTS
---------------
Bedrock (opus-4-6/4-7) returns reasoning *text* only when the request carries
`thinking: {type: adaptive, display: "summarized"}`. The sidecar sets this shape
server-side in `litellm_params` (litellm_sidecar.py), but openclaw drives the
agent with `--thinking xhigh`, which emits a request-level `reasoning_effort:high`.
A request-level `reasoning_effort` OVERRIDES the configured thinking dict and
STRIPS `display`, so Bedrock returns a signature-only thinking block with EMPTY
text — and openclaw then persists `thinking: ""` to chat.jsonl (only the parent
agent is affected; sub-agents send no reasoning_effort and keep the text).

This pre-call hook neutralizes that: for Claude/Anthropic requests it drops
`reasoning_effort` and forces the known-good thinking shape, so the parent agent's
reasoning text is populated and flows through to output.json / delivery.json.

It runs BEFORE LiteLLM's provider transforms (same dispatch point as the headroom
hook). It mutates only the thinking-control keys, never the messages/system, and
NEVER raises into the dispatch loop (fail-open).
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)

# Known-good shape proven (litellm_sidecar.py header) to yield populated reasoning
# text (len ~289-665) instead of signature-only.
_GOOD_THINKING = {"type": "adaptive", "display": "summarized"}
_GOOD_OUTPUT_CONFIG = {"effort": "high"}

# Only force thinking for models that actually support Anthropic extended thinking.
_THINKING_MODEL_HINTS = ("claude", "opus", "sonnet", "anthropic")


def _is_thinking_model(model: Any) -> bool:
    m = str(model or "").lower()
    return any(h in m for h in _THINKING_MODEL_HINTS)


class ThinkingForcer(CustomLogger):
    """Force `thinking:{type:adaptive,display:summarized}` on Anthropic requests.

    Implements ONLY `async_pre_call_hook` (canonical 4-arg keyword signature), so
    it does not contend with the usage writer's `async_log_success_event`.
    Fail-open: any error returns `data` unchanged.
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
            if not _is_thinking_model(data.get("model")):
                return data  # leave gpt / non-thinking models untouched
            # reasoning_effort:high is what strips `display` -> empty reasoning text.
            data.pop("reasoning_effort", None)
            # Force the shape that yields visible reasoning text.
            data["thinking"] = dict(_GOOD_THINKING)
            data.setdefault("output_config", dict(_GOOD_OUTPUT_CONFIG))
        except Exception as exc:  # noqa: BLE001 - never break the dispatch loop
            logger.warning("[thinking_forcer] pre-call hook failed: %s", exc)
        return data


# Module-level instance referenced from the proxy YAML callbacks list.
thinking_callback_instance = ThinkingForcer()
