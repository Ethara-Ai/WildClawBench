"""LiteLLM + Headroom transport for the judge council (opt-in).

This module is the SECOND transport for the rubric judge council. The original
host-side urllib transport lives in `grading.py` (`_call_judge_bedrock` /
`_call_judge_openai`) and remains the default. When the env flag
`KENSEI_JUDGE_USE_LITELLM` is on, `grading._call_one_judge` routes here first
and falls back to urllib on any exception — grading is the harness's promise
to a task and MUST NEVER fail because of a transport choice.

Why this module exists
----------------------
Headroom (https://github.com/chopratejas/headroom, PyPI `headroom-ai`) is a
prompt-compression tool. Its README suggests
`litellm.callbacks = [HeadroomCallback()]`, but LiteLLM's own docs mark the
mutating `async_pre_call_hook` as **proxy-only** — in library mode (which is
what we use here, no proxy), callbacks are observe-only and CANNOT rewrite
the outgoing messages. So we use the explicit `compress()` call BEFORE
`litellm.completion()` instead. More debuggable too — each call returns a
`CompressResult` with full transform telemetry.

Env vars (read at call time, NOT cached at import)
---------------------------------------------------
  KENSEI_JUDGE_USE_LITELLM            bool  default false  — master toggle
  KENSEI_JUDGE_HEADROOM_ENABLED       bool  default true   — compression switch
  KENSEI_JUDGE_HEADROOM_TARGET_RATIO  float default 0.4    — target compress ratio
  KENSEI_JUDGE_HEADROOM_PROTECT_RECENT int default 2       — protected message count
  KENSEI_JUDGE_HEADROOM_MIN_TOKENS    int   default 2000   — skip below this size

Token tracking invariants (DO NOT BREAK)
----------------------------------------
The per-judge `usage` dict shape returned by `call_judge_via_litellm()` MUST
match the existing shape produced by `_call_judge_bedrock` / `_call_judge_openai`
exactly (7 keys: input_tokens, output_tokens, cache_read_tokens,
cache_write_tokens, total_tokens, request_count, cost_usd). The `headroom`
sub-dict is purely additive and harmless to readers that don't expect it.

The JSONL log at `litellm_usage_callback.py` (10-key schema) is for AGENT
calls through the per-batch sidecar — NOT touched by this module.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ---------- env-flag helpers (read live every call; never cached) ----------

def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def judge_use_litellm() -> bool:
    """Master toggle. False = use legacy urllib path in grading.py."""
    return _truthy(os.environ.get("KENSEI_JUDGE_USE_LITELLM"))


def judge_headroom_enabled() -> bool:
    """Compression switch. Default on when set empty/unset; allows A/B
    testing LiteLLM-without-compression by setting this to false explicitly."""
    raw = os.environ.get("KENSEI_JUDGE_HEADROOM_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return _truthy(raw)


def _headroom_target_ratio() -> float:
    raw = os.environ.get("KENSEI_JUDGE_HEADROOM_TARGET_RATIO")
    if not raw:
        return 0.4
    try:
        return float(raw)
    except ValueError:
        return 0.4


def _headroom_protect_recent() -> int:
    raw = os.environ.get("KENSEI_JUDGE_HEADROOM_PROTECT_RECENT")
    if not raw:
        return 2
    try:
        return int(raw)
    except ValueError:
        return 2


def _headroom_min_tokens() -> int:
    raw = os.environ.get("KENSEI_JUDGE_HEADROOM_MIN_TOKENS")
    if not raw:
        return 2000
    try:
        return int(raw)
    except ValueError:
        return 2000


# ---------- litellm.register_model() for judge ARNs ----------
#
# LiteLLM's Bedrock layer extracts the model id from ARNs via
# `extract_model_name_from_bedrock_arn(model.split('/')[-1])`. The
# application-inference-profile tail is an opaque ID that is NOT in
# `model_prices_and_context_window.json`, so `get_model_info()` returns defaults
# and per-model `max_input_tokens` / cost enforcement is wrong without
# explicit registration.
#
# Source of truth for cost rates is `grading._JUDGE_RATES`. ctx windows match
# `tests/test_judge_budget_invariant.py:_MEMBER_LIMITS`. Keep these in sync.

# (arn_tail, input_cost, output_cost, cache_read_cost, cache_write_cost, ctx_window, max_output)
_JUDGE_REGISTRY = (
    ("urg0zifsjiga", 3e-6,    1.5e-5, 3e-7,     3.75e-6, 1_000_000, 8192),   # Sonnet 4.6
    ("u4czm4f2p3ws", 0.6e-6,  2.4e-6, 0.0,      0.0,       202_752, 16384),  # GLM 5
    ("q6g7fi6wumk3", 0.6e-6,  2.5e-6, 0.0,      0.0,       262_144, 16384),  # Kimi K2.5
)

_registered_once = False


def register_judges_once() -> None:
    """Idempotent: register each judge ARN's tail with LiteLLM exactly once
    per Python process. Failure to register is logged and never re-raised —
    LiteLLM will fall back to defaults, calls still succeed, but per-model
    max_input_tokens enforcement will be loose. Always safe to call."""
    global _registered_once
    if _registered_once:
        return
    try:
        import litellm  # type: ignore
    except ImportError:
        logger.warning(
            "litellm not importable; judge council cannot use LiteLLM transport "
            "(falling back to urllib path inside grading.py)"
        )
        _registered_once = True  # don't spam on every call
        return

    for tail, r_in, r_out, r_cr, r_cw, ctx, max_out in _JUDGE_REGISTRY:
        try:
            litellm.register_model({
                tail: {
                    "max_tokens": ctx,
                    "max_input_tokens": ctx,
                    "max_output_tokens": max_out,
                    "input_cost_per_token": r_in,
                    "output_cost_per_token": r_out,
                    "cache_read_input_token_cost": r_cr,
                    "cache_creation_input_token_cost": r_cw,
                    "litellm_provider": "bedrock_converse",
                    "mode": "chat",
                }
            })
        except Exception as exc:  # pragma: no cover (LiteLLM schema drift)
            logger.warning(
                "litellm.register_model failed for %s: %s — call will use LiteLLM defaults",
                tail, exc,
            )
    _registered_once = True


# ---------- Headroom compression wrapper ----------

def maybe_compress(messages: list[dict], model: str) -> tuple[list[dict], dict]:
    """Best-effort Headroom compression. NEVER raises.

    Returns (messages, stats) where messages is the (possibly compressed)
    list and stats is `{}` on skip/failure OR a dict containing the keys
    {tokens_before, tokens_after, tokens_saved, compression_ratio,
     transforms_applied}.

    Critical config decisions:
      - compress_user_messages=True   — judges' evidence lives in user turn
      - compress_system_messages=False — system prompt encodes the verdict
        format that `_VERDICT_RE` regexes against; compressing it risks
        breaking the regex and silently zeroing all council votes.
      - ARN tokenizer hint: when model contains `application-inference-profile`,
        Headroom can't infer a tokenizer from the opaque ID, so we pass
        `anthropic/claude-sonnet-4-5-20250929` as the model= hint. The judges
        are all Anthropic-protocol-compatible on Bedrock; this is a safe hint
        for counting alone (does NOT change the model LiteLLM actually calls)."""
    if not judge_headroom_enabled():
        return messages, {}

    try:
        from headroom import compress, CompressConfig  # type: ignore
    except ImportError:
        # Headroom not installed; silent no-op so KENSEI_JUDGE_USE_LITELLM=true
        # alone still works without headroom-ai installed.
        return messages, {}
    except Exception as exc:  # pragma: no cover (defensive)
        logger.warning("headroom import raised %s — skipping compression", exc)
        return messages, {}

    # Tokenizer hint for opaque Bedrock ARNs.
    if "application-inference-profile" in (model or ""):
        model_hint = "anthropic/claude-sonnet-4-5-20250929"
    else:
        model_hint = model

    try:
        cfg = CompressConfig(
            compress_user_messages=True,
            compress_system_messages=False,
            protect_recent=_headroom_protect_recent(),
            min_tokens_to_compress=_headroom_min_tokens(),
            target_ratio=_headroom_target_ratio(),
        )
    except Exception as exc:  # pragma: no cover (CompressConfig schema drift)
        logger.warning("CompressConfig construction failed: %s — skipping compression", exc)
        return messages, {}

    try:
        result = compress(messages, model=model_hint, config=cfg)
    except Exception as exc:
        logger.warning("headroom.compress raised %s — sending uncompressed", exc)
        return messages, {}

    tokens_before = int(getattr(result, "tokens_before", 0) or 0)
    tokens_after = int(getattr(result, "tokens_after", 0) or 0)
    tokens_saved = int(getattr(result, "tokens_saved", 0) or 0)
    compression_ratio = float(getattr(result, "compression_ratio", 0.0) or 0.0)
    transforms = list(getattr(result, "transforms_applied", []) or [])
    new_messages = getattr(result, "messages", None) or messages

    if tokens_saved <= 0:
        # Record the no-op too — useful for telemetry to confirm we tried.
        return messages, {
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_saved": 0,
            "compression_ratio": compression_ratio,
            "transforms_applied": [],
        }

    return new_messages, {
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_saved": tokens_saved,
        "compression_ratio": compression_ratio,
        "transforms_applied": transforms,
    }


# ---------- LiteLLM-backed judge call ----------

def call_judge_via_litellm(
    model: str,
    system: str,
    user: str,
    max_output_tokens: int,
    cost_fn: Any,
) -> tuple[str, dict]:
    """LiteLLM-backed judge call with optional Headroom compression.

    Returns the same `(raw_text, usage_dict)` tuple shape as
    `grading._call_judge_bedrock` / `grading._call_judge_openai`, so
    `grading._run_council`'s `_one()` is transport-agnostic.

    `cost_fn` is `grading._judge_cost_usd` injected to avoid a circular
    import. `max_output_tokens` is `grading._member_max_output_tokens(arn)`.

    Raises on any LiteLLM error so `grading._call_one_judge` can fall back
    to the urllib transport. This is intentional: grading must NEVER fail
    because LiteLLM had a bad day."""
    import litellm  # type: ignore  (deliberate: ImportError surfaces as a fall-back trigger)

    register_judges_once()

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Headroom (best-effort, never raises)
    messages, compression_stats = maybe_compress(messages, model)

    # Sanity: judges MUST NOT receive `thinking`, `reasoning_effort`,
    # `output_config`, or `response_format` — those silently change the
    # output character and break `_VERDICT_RE` parsing. Pass nothing extra.
    response = litellm.completion(
        model=model,
        messages=messages,
        max_tokens=max_output_tokens,
        temperature=0,
        stream=False,
        # Bedrock application-inference-profiles reject `temperature` on the
        # messages/invoke route ("bedrock does not support parameters:
        # ['temperature']"), which otherwise raises UnsupportedParamsError and
        # forces a full fallback to the urllib path (losing judge Headroom
        # compression). drop_params makes LiteLLM silently drop only the params
        # a given provider rejects, so OpenAI judges keep temperature=0 while
        # Bedrock judges drop it and still run through the LiteLLM path.
        drop_params=True,
    )

    # Extract text. LiteLLM normalizes to OpenAI shape across providers.
    try:
        raw = response.choices[0].message.content or ""
    except (AttributeError, IndexError) as exc:
        raise RuntimeError(f"litellm response shape unexpected: {exc}") from exc

    # Extract usage. LiteLLM normalizes to OpenAI shape but Bedrock under
    # the hood may fold cache_read/cache_write back INTO prompt_tokens. The
    # invariant: returned `input_tokens` must be non-cached only (matches
    # both _call_judge_bedrock and _call_judge_openai semantics).
    u = getattr(response, "usage", None) or {}
    def _gi(obj: Any, name: str) -> int:
        if isinstance(obj, dict):
            v = obj.get(name)
        else:
            v = getattr(obj, name, None)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    prompt_tok = _gi(u, "prompt_tokens")
    comp_tok = _gi(u, "completion_tokens")
    cache_read = _gi(u, "cache_read_input_tokens")
    if not cache_read:
        # OpenAI shape (prompt_tokens_details.cached_tokens)
        details = u.get("prompt_tokens_details") if isinstance(u, dict) else getattr(u, "prompt_tokens_details", None)
        if details:
            cache_read = _gi(details, "cached_tokens")
    cache_write = _gi(u, "cache_creation_input_tokens")

    # Bedrock's `inputTokens` excludes cache by convention; OpenAI's
    # `prompt_tokens` includes cached_tokens. LiteLLM's normalization
    # rolls cache back into prompt_tokens on Bedrock — subtract to maintain
    # the "input_tokens = non-cached" invariant shared with the urllib path.
    input_excl = max(0, prompt_tok - cache_read - cache_write)

    usage = {
        "input_tokens": input_excl,
        "output_tokens": comp_tok,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": input_excl + comp_tok + cache_read + cache_write,
        "request_count": 1,
        "cost_usd": float(cost_fn(model, input_excl, comp_tok, cache_read, cache_write)),
        # Additive: never read by existing code paths. New aggregator in
        # `_grade_council` collects these for `score.json.judge_council`.
        "headroom": compression_stats,
    }
    return raw, usage
