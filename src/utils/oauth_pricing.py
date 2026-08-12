"""Bedrock-model-card rates used to estimate cost for OAuth-routed trajectories.

The Claude Max subscription path is prepaid, so LiteLLM prices every OAuth
request at ``$0`` and ``usage.json -> sources.agent.cost_usd`` lands at roughly
zero. That is *correct* as a record of marginal spend, but it makes a
subscription trajectory look free to a finance system.

This module supplies the missing multipliers: published Bedrock per-MTok rates
for the four models the OAuth branch actually serves, applied to token counts
the harness already records.

**This module is only ever consulted on the OAuth path.** Callers gate on the
OAuth route before asking for an estimate, so no Bedrock cost, judge-council
price, or ``usage.jsonl`` figure is affected by anything here. Nothing in this
file is imported by ``grading.py`` or any Bedrock pricing code.

Rates mirror ``litellm_usage_oauth_callback.py``'s ``_ANTHROPIC_OPUS_PRICE`` and
``_ANTHROPIC_FABLE_PRICE`` so the sidecar's ``cost_bedrock_equivalent`` and this
estimate cannot disagree.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PER_MTOK = 1_000_000.0


@dataclass(frozen=True)
class ModelRates:
    """Published Bedrock list prices in USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float

    def cost_usd(
        self,
        *,
        input_tokens: float,
        output_tokens: float,
        cache_read_tokens: float,
        cache_write_tokens: float,
    ) -> float:
        return (
            input_tokens * self.input_per_mtok
            + output_tokens * self.output_per_mtok
            + cache_read_tokens * self.cache_read_per_mtok
            + cache_write_tokens * self.cache_write_per_mtok
        ) / _PER_MTOK


# Cache read is 0.1x input and cache write 1.25x input at Anthropic's default
# 5-minute TTL; both tables below follow that ratio.
OPUS_RATES = ModelRates(
    input_per_mtok=5.0,
    output_per_mtok=25.0,
    cache_read_per_mtok=0.50,
    cache_write_per_mtok=6.25,
)

FABLE_RATES = ModelRates(
    input_per_mtok=10.0,
    output_per_mtok=50.0,
    cache_read_per_mtok=1.00,
    cache_write_per_mtok=12.50,
)

# Exactly the models auth_provider.py serves under the OAuth provider. Bedrock
# only ids (claude-opus-4.8, claude-sonnet-4-6, gpt-*) are deliberately absent:
# they are priced by the Bedrock path and must never be estimated here.
BEDROCK_MODEL_CARD_RATES: dict[str, ModelRates] = {
    "claude-opus-5": OPUS_RATES,
    "claude-opus-4.7": OPUS_RATES,
    "claude-opus-4-6": OPUS_RATES,
    "claude-fable-5": FABLE_RATES,
}

_FIELD_ENV_SUFFIX = {
    "input_per_mtok": "INPUT",
    "output_per_mtok": "OUTPUT",
    "cache_read_per_mtok": "CACHE_READ",
    "cache_write_per_mtok": "CACHE_WRITE",
}


def _normalise(model: Any) -> str:
    return str(model or "").strip().lower()


def _env_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", model).strip("_").upper()


def _env_override(model: str, rates: ModelRates) -> ModelRates:
    """Apply ``WCB_OAUTH_PRICE_<MODEL>_<FIELD>_PER_MTOK`` overrides, if any.

    Rates move without code releases, so each field is overridable per model
    (e.g. ``WCB_OAUTH_PRICE_CLAUDE_OPUS_5_OUTPUT_PER_MTOK=30``). Unparseable or
    negative values are ignored so a typo cannot zero out a whole trajectory.
    """
    slug = _env_slug(model)
    values: dict[str, float] = {}
    for field, suffix in _FIELD_ENV_SUFFIX.items():
        raw = os.environ.get(f"WCB_OAUTH_PRICE_{slug}_{suffix}_PER_MTOK", "").strip()
        if not raw:
            continue
        try:
            parsed = float(raw)
        except ValueError:
            logger.warning("[oauth-pricing] ignoring non-numeric override for %s %s: %r", model, suffix, raw)
            continue
        if parsed < 0:
            logger.warning("[oauth-pricing] ignoring negative override for %s %s: %r", model, suffix, raw)
            continue
        values[field] = parsed
    if not values:
        return rates
    logger.info("[oauth-pricing] %s rate overrides applied: %s", model, sorted(values))
    return ModelRates(
        input_per_mtok=values.get("input_per_mtok", rates.input_per_mtok),
        output_per_mtok=values.get("output_per_mtok", rates.output_per_mtok),
        cache_read_per_mtok=values.get("cache_read_per_mtok", rates.cache_read_per_mtok),
        cache_write_per_mtok=values.get("cache_write_per_mtok", rates.cache_write_per_mtok),
    )


def rates_for(model: Any) -> Optional[ModelRates]:
    """Return rates for *model*, or None when it is not an OAuth-served model."""
    name = _normalise(model)
    if not name:
        return None
    exact = BEDROCK_MODEL_CARD_RATES.get(name)
    if exact is not None:
        return _env_override(name, exact)
    # The sidecar maps every opus alias onto one upstream model, so an
    # unrecognised opus/fable id is priced by family rather than silently $0.
    if "fable" in name:
        return _env_override(name, FABLE_RATES)
    if "opus" in name:
        return _env_override(name, OPUS_RATES)
    return None


def estimate_cost_usd(
    model: Any,
    *,
    input_tokens: float = 0.0,
    output_tokens: float = 0.0,
    cache_read_tokens: float = 0.0,
    cache_write_tokens: float = 0.0,
) -> tuple[float, bool]:
    """Estimate USD cost from token counts. Returns ``(cost, priced_ok)``.

    Never raises: an unknown model yields ``(0.0, False)`` so callers can tell a
    genuinely free run from one this table could not price.
    """
    rates = rates_for(model)
    if rates is None:
        return 0.0, False
    try:
        cost = rates.cost_usd(
            input_tokens=float(input_tokens or 0.0),
            output_tokens=float(output_tokens or 0.0),
            cache_read_tokens=float(cache_read_tokens or 0.0),
            cache_write_tokens=float(cache_write_tokens or 0.0),
        )
    except (TypeError, ValueError):
        return 0.0, False
    return round(max(cost, 0.0), 6), True
