"""Bedrock-model-card rates that price every OAuth-routed figure in a run.

The Claude Max subscription path is prepaid, so what a request "cost" over the
bridge is a matter of convention: LiteLLM records marginal spend (~$0 when the
sidecar zeroes the OAuth model's per-token prices, a paid-API list price when it
does not). Neither convention is comparable with a Bedrock run, and a run
carried both at once -- batch artifacts held list-price dollars while regrades
held $0 for the same judge.

So on the OAuth route there is exactly ONE cost column and it is always derived:
every ``cost_usd`` the harness records is computed from token counts at the
published Bedrock per-MTok rates below. The card covers every model the OAuth
branch serves -- the trajectory models from ``auth_provider.served_trajectory_
models(OAUTH, ...)`` and the sonnet judge the cc-bridge fronts -- so an
OAuth-routed figure is never left unpriced.

**The Bedrock route never reaches these rates.** The pricing functions below are
pure, so the guarantee lives in every caller: each one gates on an explicit
``oauth_route``/bridge-URL flag, never on the shape of a price. A Bedrock run's
cost, judge-council price and ``usage.jsonl`` figures are byte-identical with or
without this file.

Rates mirror ``litellm_usage_oauth_callback.py``'s ``_ANTHROPIC_OPUS_PRICE`` and
``_ANTHROPIC_FABLE_PRICE``, and the sonnet card mirrors ``grading._FAMILY_RATES
["sonnet"]``, so the sidecar's ``cost_bedrock_equivalent``, the council price and
this estimate cannot disagree.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional

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

SONNET_RATES = ModelRates(
    input_per_mtok=3.0,
    output_per_mtok=15.0,
    cache_read_per_mtok=0.30,
    cache_write_per_mtok=3.75,
)

# The models reachable over the Claude Max subscription: opus/fable as
# trajectory models (auth_provider.served_trajectory_models(OAUTH) — the exact
# four), sonnet as the council judge routed through the cc-bridge.
# `claude-sonnet-5` is the cc-bridge judge default
# (judge_litellm._judge_oauth_bridge_model) and prices at the same published
# Sonnet card as 4.5/4.6; listing it explicitly means the run's actual judge id
# resolves by exact match rather than leaning on the family fallback.
# Bedrock-only ids (claude-opus-4.8, gpt-*) are deliberately absent. Note the
# opus/fable family fallback in `rates_for` still prices an unlisted opus alias;
# what keeps a Bedrock charge safe is the explicit `oauth_route` gate on every
# caller, not omission from this table.
BEDROCK_MODEL_CARD_RATES: dict[str, ModelRates] = {
    "claude-opus-5": OPUS_RATES,
    "claude-opus-4.7": OPUS_RATES,
    "claude-opus-4-6": OPUS_RATES,
    "claude-fable-5": FABLE_RATES,
    "claude-sonnet-5": SONNET_RATES,
    "claude-sonnet-4-6": SONNET_RATES,
    "claude-sonnet-4-5-20250929": SONNET_RATES,
    "sonnet": SONNET_RATES,
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
    if "sonnet" in name:
        return _env_override(name, SONNET_RATES)
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


def _tokens(entry: Mapping[str, Any], key: str) -> float:
    try:
        return float(entry.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cost_breakdown(
    model: Any,
    *,
    input_tokens: float = 0.0,
    output_tokens: float = 0.0,
    cache_read_tokens: float = 0.0,
    cache_write_tokens: float = 0.0,
) -> Optional[dict[str, float]]:
    """Per-token-class dollars for one request, or None when unpriceable.

    The per-message cost block in ``output.json`` needs the split, not just the
    total. Off this card each class has its own published rate, so the split is
    computed directly instead of apportioning a known total by weight, and
    ``total`` is the sum of the parts by construction.
    """
    rates = rates_for(model)
    if rates is None:
        return None
    try:
        counts = {
            "input": float(input_tokens or 0.0),
            "output": float(output_tokens or 0.0),
            "cacheRead": float(cache_read_tokens or 0.0),
            "cacheWrite": float(cache_write_tokens or 0.0),
        }
    except (TypeError, ValueError):
        return None
    per_mtok = {
        "input": rates.input_per_mtok,
        "output": rates.output_per_mtok,
        "cacheRead": rates.cache_read_per_mtok,
        "cacheWrite": rates.cache_write_per_mtok,
    }
    split = {
        key: round(max(counts[key], 0.0) * per_mtok[key] / _PER_MTOK, 8)
        for key in counts
    }
    split["total"] = round(sum(split.values()), 8)
    return split


def _reprice(entry: MutableMapping[str, Any], *names: Any) -> Optional[float]:
    """Set ``entry['cost_usd']`` from its own token counts. Returns the new cost.

    Tries each candidate model name in turn so a council member recorded as a
    Bedrock ARN (which matches no rate card) still prices through its stable
    family key. Returns None when no candidate is on the card, leaving the
    recorded figure in place — an unpriceable model must not be published as $0.
    """
    for candidate in names:
        name = str(candidate or "").strip()
        if not name:
            continue
        estimated, priced_ok = estimate_cost_usd(
            name,
            input_tokens=_tokens(entry, "input_tokens"),
            output_tokens=_tokens(entry, "output_tokens"),
            cache_read_tokens=_tokens(entry, "cache_read_tokens"),
            cache_write_tokens=_tokens(entry, "cache_write_tokens"),
        )
        if priced_ok and estimated:
            entry["cost_usd"] = estimated
            return estimated
    return None


def reprice_oauth_sources(
    sources: MutableMapping[str, Any], *, model: str = "", oauth_route: bool = False
) -> list[str]:
    """Recompute the OAuth-routed usage sources from token counts, in place.

    ``oauth_route`` is the only gate, and it is the run's routing flag, not a
    guess from the shape of a price. On a Bedrock run this returns immediately
    and every figure is byte-identical to what the harness recorded.

    On an OAuth run the agent and every judge member are repriced off the card
    whatever they recorded, because what they recorded is not one convention:
    LiteLLM books the prepaid subscription at rounding noise (``6e-06``) when
    the sidecar zeroes the model's per-token prices and at full paid-API list
    price when it does not. Overwriting both with the derived figure is what
    makes ``usage.json`` a single comparable cost column. The judge aggregate is
    re-summed from its members for the same reason.

    Never raises: usage bookkeeping must not be able to fail a completed run.
    """
    repriced: list[str] = []
    if not oauth_route:
        return repriced
    try:
        agent = sources.get("agent")
        if isinstance(agent, MutableMapping):
            cost = _reprice(agent, model)
            if cost is not None:
                repriced.append(f"agent({model})=${cost}")

        judge = sources.get("judge")
        if isinstance(judge, MutableMapping):
            per_member = judge.get("per_member")
            member_total = 0.0
            priced_any = False
            if isinstance(per_member, MutableMapping):
                for family, member in sorted(per_member.items()):
                    if not isinstance(member, MutableMapping):
                        continue
                    cost = _reprice(member, member.get("model"), family)
                    if cost is not None:
                        priced_any = True
                        repriced.append(f"judge.{family}=${cost}")
                    member_total += _tokens(member, "cost_usd")
            if priced_any:
                judge["cost_usd"] = round(member_total, 6)
            else:
                # Pricing the aggregate token counts at one model's rate is only
                # sound because the OAuth council is sonnet-only
                # (auth_provider.JUDGE_FAMILIES_BY_PROVIDER[OAUTH]).
                cost = _reprice(judge, judge.get("model"))
                if cost is not None:
                    repriced.append(f"judge=${cost}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[oauth-pricing] repricing skipped: %s", exc)
    return repriced
