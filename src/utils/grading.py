from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import struct
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence
from typing import Literal
from dotenv import load_dotenv

# Imported as a module (not `from ... import resolve_provider`) so tests can
# monkeypatch the resolver, and to make the one-way dependency obvious:
# auth_provider must never import grading back (see its module docstring).
from src.utils import auth_provider

logger = logging.getLogger(__name__)

load_dotenv()
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE", "/tmp_workspace")


# ---------------------------------------------------------------------------
# LLM rubric judge (for native prompt.txt + rubric.json tasks)
#
# Native tasks have no `automated_checks` to exec, so without this they score a
# degenerate reward:0.0 / tests_total:0 no matter how well the agent did. This
# judge scores each rubric criterion 0..1 against the agent's deliverables +
# transcript, then weights them into an overall_score and per-criterion test
# counts. Transport selection (m1612):
#   * DEFAULT — direct urllib POST to OpenAI / Bedrock-Converse from the host.
#     The per-batch LiteLLM SIDECAR is not host-reachable (--internal bridge,
#     no published port); the host can reach both providers directly (verified).
#   * OPT-IN — when KENSEI_JUDGE_USE_LITELLM=true, the dispatcher routes through
#     LiteLLM in *library mode* (in-process `litellm.completion`) via
#     `src/utils/judge_litellm.py`, with optional Headroom user-turn compression
#     gated by KENSEI_JUDGE_HEADROOM_ENABLED. On ANY LiteLLM/Headroom error the
#     dispatcher falls through to the urllib path so grading never fails because
#     of transport choice. Both transports MUST produce the same 7-key per-judge
#     `usage` dict (input/output/cache_read/cache_write/total tokens, request_count,
#     cost_usd); LiteLLM path adds an OPTIONAL `headroom` sub-dict aggregated into
#     `score.json.judge_council.headroom_per_member`.
# Fully best-effort: any failure returns a structured error and never raises
# into the run loop.
# ---------------------------------------------------------------------------

# JUDGE_MODEL / JUDGE_MODEL_FALLBACK envs are no longer consulted (m1609):
# single-judge mode was removed and the council is the only grading path.
# Configure council members via JUDGE_COUNCIL_MEMBERS or the per-judge
# JUDGE_COUNCIL_SONNET_ARN / _GLM_ARN / _KIMI_ARN env vars (resolved live by
# council_members(); see the FAMILY decoupling block below).
# Smallest-member-governs evidence cap, derived from AWS Bedrock official
# context-window numbers (2026-06-02 web-confirmed, no longer hit-and-trial):
#   * Claude Sonnet 4.6 (is9bst5tfadh) — 1,000,000 input tokens
#   * Kimi K2.5         (p532c9fzmeed) —   256,000 input tokens
#   * GLM 5             (xx5msvho23iq) —   200,000 input tokens  <-- smallest
# Bedrock enforces input_tokens + max_tokens <= context_window (see LiteLLM
# PR #22479 / issue #22478). Our maxTokens request is 4,000. Empirical chars
# /token ratio on real WildClawBench payloads is ~2.515 (500k chars produced
# 198,753 input tokens for GLM in the m1037 probe). Budget:
#   200,000 ctx  − 4,000 output  − ~5,000 scaffold (TASK + 25-rubric criteria
#   + JSON schema + system prompt) = ~191,000 tokens for evidence
#   ÷ 2.515 chars/token = ~75,944 evidence tokens worth of chars ≈ 191,000
# Converting back: 191,000 tokens x 2.515 chars/token ~= 480,365 chars. The
# default is deliberately 450,000 chars (~180K tokens): measured on real runs
# (lena_pruitt 2026-09-01, kayla_morgan run_3 2026-09-03, 9 grading calls),
# judge inputs <=160K tokens parsed 17-40/40 verdicts cleanly on EVERY call,
# while 270K-464K-token inputs produced empty/unparseable/format-collapsed
# output on 7 of 8 calls. Fitting the context window is not the constraint;
# verdict QUALITY is. A brief raise to 1,000,000 chars (c923095) put default
# inputs at ~400K tokens and measurably increased abstentions. Operators with
# content known to tolerate more can still export JUDGE_MAX_EVIDENCE=<chars>;
# 0 restores unbounded (known to 400 every council member).
_DEFAULT_JUDGE_MAX_EVIDENCE = 450_000

# Claude via the OAuth subscription bridge. The judge on this route is now
# Sonnet 5 (claude-sonnet-5), which documents a 1,000,000-token context window
# by default on the Claude API (no beta header) — a large increase over the
# Sonnet 4.5 era, when this route capped at 200,000 tokens and this constant
# was 300,000 chars.
#
# We do NOT budget to the full 1M window: the usable context on a Claude Max
# *subscription* surface (as opposed to the plain Anthropic API) is NOT
# documented, so we hedge. Assume a conservative ~400K-token usable input and
# budget by CHARS against the worst-case (~1.8 chars/token) density seen on
# JSON-dense trajectories:
#   400,000 tokens × 1.8 chars/token ≈ 720,000 chars → round down to 700,000.
# At the Sonnet family's 1.375 chars/token floor that is ~509K tokens, still
# far under 1M even after output(8192) + ~5K scaffold. This is a 2.3x evidence
# increase over the old 300K cap while staying safe if the true Max ceiling is
# only ~550K tokens. The min() gate in _member_evidence_budget keeps this as
# the Anthropic-direct safety valve distinct from the Bedrock family budget.
# Tunable via KENSEI_JUDGE_OAUTH_MAX_EVIDENCE for denser trajectories.
_DEFAULT_JUDGE_OAUTH_MAX_EVIDENCE = 700_000


def _judge_oauth_max_evidence() -> int:
    raw = os.environ.get("KENSEI_JUDGE_OAUTH_MAX_EVIDENCE")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_OAUTH_MAX_EVIDENCE
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_OAUTH_MAX_EVIDENCE
    return n if n > 0 else _DEFAULT_JUDGE_OAUTH_MAX_EVIDENCE


# Codex-subscription judge evidence budget. On this route it REPLACES the gpt
# family base (_FAMILY_EVIDENCE['gpt'] = 350K) rather than being min()'d with it:
# that base exists only to keep METERED OpenAI input under the 272K-token
# re-pricing threshold, and the subscription route bills $0. 500K chars is
# ~150K tokens on typical judge payloads (~3.3 chars/token measured on
# benicio_aguirre_0ee7cbc9 2026-09-17), inside the <=160K-token range that parsed
# cleanly (see _DEFAULT_JUDGE_MAX_EVIDENCE note), and 505K / 1.375 worst-case
# floor = ~367K tokens + 128K output + 3K safety = ~498K, well under sol's
# 1,050,000 window. A luna model (400K window) keeps the 350K family base (see
# _member_evidence_budget). KENSEI_JUDGE_CODEX_MAX_EVIDENCE raises or lowers it;
# set it back to 350000 to restore the previous budget.
_DEFAULT_JUDGE_CODEX_MAX_EVIDENCE = 500_000


def _judge_codex_max_evidence() -> int:
    raw = os.environ.get("KENSEI_JUDGE_CODEX_MAX_EVIDENCE")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_CODEX_MAX_EVIDENCE
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_CODEX_MAX_EVIDENCE
    return n if n > 0 else _DEFAULT_JUDGE_CODEX_MAX_EVIDENCE


def _resolve_judge_max_evidence() -> int | None:
    raw = os.environ.get("JUDGE_MAX_EVIDENCE")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_MAX_EVIDENCE
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_MAX_EVIDENCE
    return n if n > 0 else None


_JUDGE_MAX_EVIDENCE = _resolve_judge_max_evidence()

# Per-member evidence budgets (chars). Council members have different context
# windows, so each gets a payload sized to its own ceiling instead of all three
# sharing the smallest-member cap. Numbers derived from the same arithmetic as
# _DEFAULT_JUDGE_MAX_EVIDENCE:
#   budget_chars = (ctx_window − 4,000 maxTokens − ~5,500 scaffold) × 2.515 chars/token
# Rounded down with safety margin for tokenizer drift and rubric-block variance.
# Match patterns are checked against the model identifier as-passed, so an
# operator override via JUDGE_COUNCIL_*_ARN still maps to the correct budget so
# long as the inference-profile ID stays in the string.
# AWS edge body cap (~25 MB) observed in (b40) probes B2/B3 caps every Bedrock
# request regardless of context window; we cap at 24,000,000 chars to leave
# headroom for JSON envelope + scaffold + base64 overhead.
_AWS_EDGE_BODY_CAP = 24_000_000

# Fallback for unrecognized models (single-judge OpenAI fallback, custom ARNs).
# OpenAI auto-caches and has its own server-side enforcement; conservative.
# Per-family (budget_chars, max_output_tokens) live in _FAMILY_EVIDENCE below.
_DEFAULT_MAX_OUTPUT_TOKENS = 4000


# ── Council member FAMILY decoupling (profile-ID rotation) ──────────────────
# Company policy rotates the three Bedrock judge inference-profile ARNs MONTHLY,
# which changes the opaque profile-id suffix (e.g. is9bst5tfadh) every rotation.
# Therefore the profile id is NOT a stable key: pricing / evidence-budget /
# cache-eligibility must be keyed by a STABLE logical *family* label instead, and
# the rotating ARN is supplied at runtime from .env. The family of a council
# member is derived from the FIXED env-var NAME that carries its ARN
# (JUDGE_COUNCIL_SONNET_ARN → "sonnet", _GLM_ARN → "glm", _KIMI_ARN → "kimi"),
# never by parsing the rotating id. See .env.example and tests/test_judge_rotation.py.
#
# The "gpt" family is the one member that is NOT a rotating Bedrock profile: its
# env var carries a plain OpenAI model id (e.g. gpt-5.6), and it authenticates
# with its own key (KENSEI_JUDGE_GPT_API_KEY) rather than the run's provider
# credentials. It is registered here so it inherits the same stable
# pricing / evidence-budget / cache-eligibility dispatch as the Bedrock families
# instead of being smuggled in under a sonnet tag (see
# docs/GPT_JUDGE_IMPLEMENTATION_PLAN.md §2 "Do NOT use the smuggle path").
JudgeFamily = Literal["sonnet", "glm", "kimi", "gpt"]

# Stable env-var → family dispatch. Order is the canonical council order.
_FAMILY_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("sonnet", "JUDGE_COUNCIL_SONNET_ARN"),
    ("glm", "JUDGE_COUNCIL_GLM_ARN"),
    ("kimi", "JUDGE_COUNCIL_KIMI_ARN"),
    # Value is a MODEL ID (gpt-5.6), not an ARN — _family_for's equality/substring
    # match resolves it the same way.
    ("gpt", "JUDGE_GPT_MODEL"),
)
_KNOWN_FAMILIES: frozenset[str] = frozenset(fam for fam, _ in _FAMILY_ENV_VARS)

# Per-family per-token rates (input, output, cache_read, cache_write) USD/token.
# Source of truth for council billing — web-verified against AWS published cards
# (see _JUDGE_RATES header). judge_litellm.register_judges_for_batch imports THIS
# table so the two transports cannot drift (was the G15 drift bug). Values must
# track real published prices, not the (rotating) profile id.
#   Sonnet 4.6: $3/$15/$3.75cw/$0.30cr   GLM-5: $1.00/$3.20(+$0.20cr)   Kimi K2.5: $0.72/$3.60
#   gpt-5.6(-sol): $2.00/$10.00/$2.50cw/$0.20cr
_FAMILY_RATES: dict[str, tuple[float, float, float, float]] = {
    "sonnet": (3e-6, 1.5e-5, 3e-7, 3.75e-6),
    "glm": (1e-6, 3.2e-6, 2e-7, 0.0),
    "kimi": (0.72e-6, 3.6e-6, 0.0, 0.0),
    "gpt": (2e-6, 1e-5, 2e-7, 2.5e-6),
}
# Per-family (evidence_char_budget, max_output_tokens). Web-verified 2026-06-04
# against AWS official model cards + the Bedrock constraint
# `input_tokens + max_tokens <= context_window` (LiteLLM PR #22479).
# Per-family published Bedrock caps:
#   Sonnet 5  : ctx 1,000,000  max_output 128,000  (Anthropic + AWS card agree)
#   Kimi K2.5 : ctx   262,144  max_output 16,384  (AWS card)
#   GLM 5     : ctx   202,752  max_output 16,384  (AWS card lists 128K, capped at 16K — verdicts never need more)
# Budget formula: budget_chars = (ctx − max_output − 3000_safety) × cpt_floor,
# then floor to nearest 25k. Honoring the AWS-published max_output (not a single
# global 4K) is what makes the math fit, because Bedrock enforces
# input + max <= ctx so the budget MUST account for the actual maxTokens sent.
# Conservative cpt floors measured on dense fixtures (amanda_hayes_01 run_3,
# which 400'd at the old over-wide budgets): Sonnet 1.375, Kimi/GLM 1.15.
#   Sonnet: (1,000,000 − 128,000 − 3000) × 1.375 − 5000 scaffold → 1_175_000 (floor 25k)
#   Kimi  : (262,144 − 16,384 − 3000) × 1.15  − 5000 scaffold → 225_000
#   GLM   : (202,752 − 16,384 − 3000) × 1.15  − 5000 scaffold → 175_000
# Don't widen without re-running probe_judge_only.py against a representative
# trajectory; tests/test_judge_budget_invariant.py guards the worst-case math.
# The gpt family is deliberately NOT sized off its context window (1,050,000 for
# sol/terra, 400,000 for luna): OpenAI re-prices the ENTIRE request ~2× once
# input exceeds 272,000 tokens, and _FAMILY_RATES is a flat 4-tuple with no
# threshold logic, so a window-sized budget would silently understate cost ~2×.
# Inverting that threshold at the measured Sonnet 1.375 cpt floor caps it instead:
#   gpt   : 272,000 × 1.375 − 5000 scaffold = 369,000 → floor 25k → 350_000
# Back-check: (350,000 + 5,000) / 1.375 ≈ 258K input (< 272K, single-rate tier)
# and 258K + 128,000 max + 3,000 safety = 389K ≤ 400,000 (luna, smallest ctx).
# This base governs the METERED route only; the codex subscription route uses
# _DEFAULT_JUDGE_CODEX_MAX_EVIDENCE (500K) instead.
_FAMILY_EVIDENCE: dict[str, tuple[int, int]] = {
    "sonnet": (1_175_000, 128000),
    "kimi": (225_000, 16384),
    "glm": (175_000, 16384),
    "gpt": (350_000, 128000),
}
# Anthropic prompt-caching eligibility by family. Only Sonnet (Anthropic) accepts
# a cachePoint block on Bedrock Converse; GLM/Kimi return 403 if one is present.
# gpt is False for a different reason: the OpenAI Chat Completions path has no
# cachePoint block at all (prompt caching there is automatic and server-side).
_FAMILY_CACHE_SUPPORTED: dict[str, bool] = {
    "sonnet": True,
    "glm": False,
    "kimi": False,
    "gpt": False,
}

# OpenAI single-judge fallback rates, keyed by model NAME (NOT a council family,
# never rotated). Used when family is None (gpt-5.4 default fallback, gpt-5.5).
_OPENAI_JUDGE_RATES: dict[str, tuple[float, float, float, float]] = {
    "gpt-5.4": (2.5e-6, 1.5e-5, 2.5e-7, 0.0),
    "gpt-5.5": (5e-6, 3e-5, 5e-7, 0.0),
}


@dataclass(frozen=True)
class CouncilMember:
    """A council judge: stable `family` label + its current (rotating) `model` ARN."""

    family: JudgeFamily
    model: str


def _family_for(model: str | None, family: str | None = None) -> str | None:
    # Threaded family wins; else dispatch by the FIXED env-var name (read live).
    # None => non-council (OpenAI fallback), caller uses name-keyed _OPENAI_JUDGE_RATES.
    if family is not None:
        return family
    if not model:
        return None
    for fam, var in _FAMILY_ENV_VARS:
        val = (os.environ.get(var) or "").strip()
        if val and (val == model or val in model or model in val):
            return fam
    return None


def _member_evidence_budget(model: str, family: str | None = None) -> int | None:
    env_raw = os.environ.get("JUDGE_MAX_EVIDENCE")
    if env_raw is not None and env_raw.strip() != "":
        return _resolve_judge_max_evidence()
    fam = _family_for(model, family)
    if fam is not None and fam in _FAMILY_EVIDENCE:
        base = min(_FAMILY_EVIDENCE[fam][0], _AWS_EDGE_BODY_CAP)
        if fam == "sonnet":
            try:
                from . import judge_litellm

                if judge_litellm._judge_oauth_bridge_url():
                    return min(base, _judge_oauth_max_evidence())
            except Exception:
                pass
        if fam == "gpt" and _judge_codex_bridge_url():
            # Subscription route: the metered-pricing base does not apply (see
            # _DEFAULT_JUDGE_CODEX_MAX_EVIDENCE), except that a luna model's
            # 400K-token window only fits the family base.
            cap = _judge_codex_max_evidence()
            if "luna" in (model or "").lower():
                cap = min(cap, _FAMILY_EVIDENCE["gpt"][0])
            return min(cap, _AWS_EDGE_BODY_CAP)
        return base
    return _DEFAULT_JUDGE_MAX_EVIDENCE


def _member_max_output_tokens(arn: str, family: str | None = None) -> int:
    fam = _family_for(arn, family)
    if fam is not None and fam in _FAMILY_EVIDENCE:
        return _FAMILY_EVIDENCE[fam][1]
    return _DEFAULT_MAX_OUTPUT_TOKENS

# LLM council (opt-in). When enabled the rubric is scored by THREE judges in
# parallel. Per-criterion aggregation is unanimous-or-Sonnet-tiebreak: a unanimous
# council verdict stands, otherwise the Sonnet member's verdict is the source of
# truth (covering both genuine Yes/No splits and partial coverage where a
# smaller-context member truncated), and only when Sonnet casts no verdict does
# the criterion route to Human Evaluation (see _grade_council). Each member's
# family is fixed by the env-var NAME carrying its ARN (see the FAMILY block
# above); the rotating ARN itself comes from .env, never hardcoded:
#   JUDGE_COUNCIL=1
#   JUDGE_COUNCIL_SONNET_ARN=bedrock/<arn>   (→ family "sonnet")
#   JUDGE_COUNCIL_GLM_ARN=bedrock/<arn>      (→ family "glm")
#   JUDGE_COUNCIL_KIMI_ARN=bedrock/<arn>     (→ family "kimi")
# Override roster with JUDGE_COUNCIL_MEMBERS using "family=arn" tag syntax:
#   JUDGE_COUNCIL_MEMBERS=sonnet=bedrock/<arn1>,glm=bedrock/<arn2>,kimi=bedrock/<arn3>
# Unset per-family vars are dropped; an unknown family tag RAISES (fail-fast,
# symmetric with validate_judge_pricing). Vars are read LIVE per call (no
# import-time caching) so a mid-process rotation is picked up immediately.


def council_enabled() -> bool:
    return os.environ.get("JUDGE_COUNCIL", "").strip() in {"1", "true", "yes", "on"}


def _truncation_abstain_enabled() -> bool:
    return os.environ.get("WCB_TRUNCATION_ABSTAIN", "1").strip() != "0"


def _parse_council_member_override(entry: str) -> CouncilMember:
    """Parse one JUDGE_COUNCIL_MEMBERS CSV entry in "family=arn" tag syntax."""
    raw = entry.strip()
    fam, sep, arn = raw.partition("=")
    fam = fam.strip().lower()
    arn = arn.strip()
    if not sep or not fam or not arn:
        raise RuntimeError(
            f"JUDGE_COUNCIL_MEMBERS entry {entry!r} must use 'family=arn' syntax "
            f"(e.g. 'sonnet=bedrock/arn:...'); known families: {sorted(_KNOWN_FAMILIES)}."
        )
    if fam not in _KNOWN_FAMILIES:
        raise RuntimeError(
            f"JUDGE_COUNCIL_MEMBERS entry {entry!r} has unknown family {fam!r}; "
            f"known families: {sorted(_KNOWN_FAMILIES)}."
        )
    if fam in auth_provider.NON_COUNCIL_JUDGE_FAMILIES:
        raise RuntimeError(
            f"JUDGE_COUNCIL_MEMBERS entry {entry!r} uses family {fam!r}, which is a "
            f"PRIMARY-judge family and may never vote in the council "
            f"(NON_COUNCIL_JUDGE_FAMILIES). Configure it via its own primary-judge "
            f"vars instead (e.g. KENSEI_JUDGE_GPT_MODEL for gpt)."
        )
    return CouncilMember(family=fam, model=arn)  # type: ignore[arg-type]


def council_members() -> list[CouncilMember]:
    """Resolve the council roster as (family, ARN) pairs, reading env LIVE.

    Precedence: JUDGE_COUNCIL_MEMBERS ("family=arn" CSV) overrides the per-family
    JUDGE_COUNCIL_{SONNET,GLM,KIMI}_ARN vars. Unset per-family vars are dropped.

    The roster is then restricted to the families the *selected auth provider*
    can serve (src/utils/auth_provider.py). This is load-bearing for provider
    isolation: run_batch calls load_dotenv() at import, so on an OAuth run all
    three JUDGE_COUNCIL_*_ARN values are still present in env, and without this
    filter the Kimi and GLM members would go straight to Bedrock via
    _call_judge_bedrock -- silently billing the provider the operator opted out
    of. Only the Sonnet member has an OAuth route (judge_litellm.py sends
    `family == "sonnet"` through the cc-bridge), so OAuth necessarily grades with
    a Sonnet-only council.
    """
    raw = os.environ.get("JUDGE_COUNCIL_MEMBERS", "").strip()
    if raw:
        out = [_parse_council_member_override(m) for m in raw.split(",") if m.strip()]
    else:
        out = []
        for fam, var in _FAMILY_ENV_VARS:
            # Skip families that are registered in _FAMILY_ENV_VARS for primary-judge
            # dispatch only (currently gpt). They MUST NOT enter the council roster:
            # gpt grades as the standalone primary judge, and if it were the only
            # configured member here it would be filtered out below and trip the
            # "no usable judge remains" raise — a grade_with_rubric failure that
            # AGENTS.md #12 forbids. See NON_COUNCIL_JUDGE_FAMILIES.
            if fam in auth_provider.NON_COUNCIL_JUDGE_FAMILIES:
                continue
            val = (os.environ.get(var) or "").strip()
            if val:
                out.append(CouncilMember(family=fam, model=val))  # type: ignore[arg-type]

    provider = auth_provider.resolve_provider()
    # MUST be council_judge_families, NOT available_judge_families: available_
    # includes primary-judge-only families (gpt) that must never vote in the
    # Bedrock council. The per-family loop above already skips
    # NON_COUNCIL_JUDGE_FAMILIES, so gpt cannot reach here via the loop; this
    # filter is defence-in-depth and also constrains a JUDGE_COUNCIL_MEMBERS
    # override roster to what the active provider can actually serve.
    allowed = set(auth_provider.council_judge_families(provider))
    filtered = [m for m in out if m.family in allowed]

    if out and filtered and len(filtered) < len(out):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Judge council: %d→%d members under provider %r (dropped: %s)",
            len(out), len(filtered), provider,
            sorted({m.family for m in out} - {m.family for m in filtered}),
        )

    if out and not filtered:
        # Every configured member was filtered out. Returning [] here would make
        # grade_with_rubric report overall_score=0.0 with a generic error, which
        # reads as "the agent failed" rather than "your council is misconfigured".
        raise RuntimeError(
            f"auth provider {provider!r} supports judge families "
            f"{sorted(allowed)}, but the configured council is "
            f"{sorted({m.family for m in out})} -- no usable judge remains. "
            f"Configure {', '.join(var for fam, var in _FAMILY_ENV_VARS if fam in allowed)} "
            f"or select a different auth provider."
        )
    return filtered


def _judge_system_prompt() -> str:
    # 2026-06-02 judge rewrite — the prompt body lives in
    # system_prompts/judge_system.md (b78 walkthrough_2026_05_27 spec, b96
    # centralization). It encodes four operationally-essential rules whose
    # absence produces graders that drift: the EXACT verdict format the
    # parser regex depends on (deviation → ParseError → council quorum
    # collapse), truncation handling (do not penalize beyond visible
    # evidence), negative-rubric polarity (Satisfied=Yes means the forbidden
    # behavior OCCURRED, not that the guardrail held), and the Yes/No-only
    # emission (no N/A, no Maybe, no markdown tables). All four are
    # referenced in the matching parser at _VERDICT_RE and in the aggregator
    # at _grade_council; changing the prompt without updating both is a
    # known-bad refactor pattern.
    from src.utils.prompt_loader import load_prompt
    return load_prompt("judge_system")


# Rubric schemas in this repo store the weight under either `weight` or
# `score` (kensei2-style rubrics use `score`, with the SIGN encoding polarity
# — negative for guardrail / forbidden-behavior criteria). The judge prompt's
# polarity semantics live entirely in the weight sign, so missing this fallback
# silently flattens all guardrail criteria to positive weight=1.0 and inverts
# the pass-count for any criterion the agent CORRECTLY refrained from.
def _extract_weight(r: dict) -> float:
    w = r.get("weight")
    if w is None:
        w = r.get("score")
    if w is None:
        return 1.0
    try:
        return float(w)
    except (TypeError, ValueError):
        return 1.0


# ── Judge multimodal payload (gpt primary judge ONLY) ───────────────────────
# Agent HTML deliverables routinely inline proof screenshots as
# `data:image/<mime>;base64,<payload>`. Sending those blobs as judge prompt TEXT
# is what produced the gpt-5.6 refusal (HTTP 200, content:[], stop_reason
# "refusal"): ~440K tokens of undecodable base64 inside a 570K-token user turn
# trips a probabilistic API safety classifier, and text base64 is unreadable to
# the judge anyway. The blobs are therefore LIFTED out of the text at the
# evidence seam (_extract_inline_images) and re-attached as structured image
# content-parts, which route to the vision preprocessor instead.
#
# This payload rides the gpt member ONLY: council members never receive image
# PARTS. Their TEXT, however, carries the same placeholders as the gpt member,
# because extraction happens in _deliverable_evidence_marker — upstream of the
# per-family split — so it is NOT byte-identical to the pre-multimodal prompt.
# That is deliberate: the base64 was unreadable to them anyway and only burned
# evidence budget.
@dataclass(frozen=True)
class ImagePart:
    """One image lifted out of a text deliverable, ready for a content-part."""

    data_uri: str
    mime: str
    detail: str
    label: str


@dataclass(frozen=True)
class JudgeUserPayload:
    """Judge user turn as text + extracted images (gpt route only).

    A sidecar type, NOT a str subclass: every non-gpt transport unwraps `.text`
    (see _call_one_judge) so the Bedrock/LiteLLM request bodies are unchanged.
    """

    text: str
    images: list[ImagePart] = field(default_factory=list)


def _payload_text(user: "str | JudgeUserPayload") -> str:
    return user.text if isinstance(user, JudgeUserPayload) else user


def _payload_images(user: "str | JudgeUserPayload") -> list[ImagePart]:
    return list(user.images) if isinstance(user, JudgeUserPayload) else []


# Image attachment budget. Deliberately small: images are for image-CONTENT
# criteria ("did the agent render the chart?"), not for bulk evidence, and every
# attached part costs vision tokens. `detail="low"` is the cheap fixed-cost tier.
# Excess images keep their TEXT placeholder (so the judge still knows the image
# existed) but are not attached — see _select_judge_images.
_DEFAULT_JUDGE_MAX_IMAGES = 8
_DEFAULT_JUDGE_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_DEFAULT_JUDGE_IMAGE_DETAIL = "low"
_JUDGE_IMAGE_DETAILS = frozenset({"low", "high", "auto"})


def _judge_max_images() -> int:
    """Max image parts attached to one judge request. 0 disables attachment
    (placeholders only); a negative or unparseable value falls back to default."""
    raw = os.environ.get("KENSEI_JUDGE_MAX_IMAGES")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_MAX_IMAGES
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_MAX_IMAGES
    return n if n >= 0 else _DEFAULT_JUDGE_MAX_IMAGES


def _judge_max_image_bytes() -> int:
    """Total base64-payload bytes attachable to one judge request."""
    raw = os.environ.get("KENSEI_JUDGE_MAX_IMAGE_BYTES")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_MAX_IMAGE_BYTES
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_MAX_IMAGE_BYTES
    return n if n >= 0 else _DEFAULT_JUDGE_MAX_IMAGE_BYTES


def _judge_image_detail() -> str:
    raw = (os.environ.get("KENSEI_JUDGE_IMAGE_DETAIL") or "").strip().lower()
    return raw if raw in _JUDGE_IMAGE_DETAILS else _DEFAULT_JUDGE_IMAGE_DETAIL


# Inline base64 image data-URI, matched by a head-anchored forward SCANNER
# rather than one regex.
#
# WHY NOT A SINGLE REGEX: agents emit these blobs BOTH unwrapped
# (`base64.b64encode`) and newline-wrapped at 64/76 columns
# (`base64.encodebytes`, and coreutils `base64 file` in a bash agent). A payload
# class without whitespace lifts only the FIRST line of a wrapped blob, which is
# the worst possible outcome: the rest of the payload stays in the judge text (so
# the refusal trigger this seam exists to remove is fully intact) AND the
# attached data URI is a truncated, undecodable image. Simply adding `\s` to the
# class is not the fix either — in markdown/plaintext deliverables it swallows
# the prose that follows the blob.
#
# So: match the prefix, then consume base64 runs, crossing a newline ONLY when
# the run just consumed is at least _B64_WRAP_MIN_SEGMENT chars. Canonical wrap
# widths are 64 and 76, so a shorter run before a newline means the blob ended.
_INLINE_IMAGE_DATA_URI_HEAD_RE = re.compile(
    r"data:image/(?P<mime>[A-Za-z0-9.+-]{1,24});base64,"
)
_B64_RUN_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_B64_WRAP_RE = re.compile(r"[ \t]*\r?\n[ \t]*")
_B64_WRAP_MIN_SEGMENT = 64
# Floor that skips degenerate stubs (kept from the pre-scanner regex).
_B64_MIN_PAYLOAD = 32


def _image_placeholder_prefix(label: str) -> str:
    """Leading, label-unique slice of an inline-image placeholder.

    Single source of truth for the placeholder shape so the emitter
    (_extract_inline_images) and the survivor test (_gather_evidence, which only
    attaches images whose placeholder outlived budgeting) cannot drift. The
    trailing comma matters: a bare label would make `page.html#1` a prefix match
    of `page.html#10`.
    """
    return f"[inline image {label},"


def _scan_b64_payload(body: str, start: int) -> tuple[str, int]:
    """Consume a possibly newline-wrapped base64 payload beginning at *start*.

    Returns (payload_with_whitespace_stripped, end_index). `end_index == start`
    when no base64 run begins at *start*.
    """
    segs: list[str] = []
    ends: list[int] = []
    pos = start
    while True:
        m = _B64_RUN_RE.match(body, pos)
        if not m:
            break
        seg = m.group(0)
        segs.append(seg)
        ends.append(m.end())
        pos = m.end()
        if seg.endswith("="):
            break  # padding terminates the payload
        wrap = _B64_WRAP_RE.match(body, pos)
        if wrap is None or len(seg) < _B64_WRAP_MIN_SEGMENT:
            break
        pos = wrap.end()
    # A well-formed base64 payload length is a multiple of 4. When the assembled
    # length is not, the last short unpadded run is far more likely a prose word
    # that happened to follow a blob ending exactly on a wrap boundary than it is
    # payload — drop it instead of corrupting both the text and the image.
    payload = "".join(segs)
    if (
        len(segs) > 1
        and len(payload) % 4
        and len(segs[-1]) < _B64_WRAP_MIN_SEGMENT
    ):
        segs.pop()
        ends.pop()
        payload = "".join(segs)
    return payload, (ends[-1] if ends else start)


def _data_uri_b64_len(data_uri: str) -> int:
    """Length of the base64 payload (wire bytes), 0 when the URI is malformed."""
    _, _, payload = data_uri.partition(",")
    return len(payload)


def _extract_inline_images(body: str, label_stem: str) -> tuple[str, list[ImagePart]]:
    """Replace each inline base64 image in *body* with a compact placeholder and
    return (rewritten_text, images) in document order. NEVER raises.

    The attached `data_uri` is REBUILT from the whitespace-stripped payload, so a
    newline-wrapped blob yields one valid single-line data URI and leaves no
    base64 behind in the text.
    """
    images: list[ImagePart] = []
    try:
        detail = _judge_image_detail()
        out: list[str] = []
        pos = 0
        for head in _INLINE_IMAGE_DATA_URI_HEAD_RE.finditer(body):
            if head.start() < pos:
                continue  # inside a payload already consumed
            payload, end = _scan_b64_payload(body, head.end())
            if len(payload) < _B64_MIN_PAYLOAD:
                continue  # degenerate stub: leave it in the text verbatim
            mime = f"image/{head.group('mime').lower()}"
            label = f"{label_stem}#{len(images) + 1}"
            images.append(ImagePart(
                data_uri=f"data:{mime};base64,{payload}",
                mime=mime,
                detail=detail,
                label=label,
            ))
            approx_kb = (len(payload) * 3 / 4) / 1024
            out.append(body[pos:head.start()])
            out.append(
                f"{_image_placeholder_prefix(label)} {mime}, ~{approx_kb:.1f}KB]"
            )
            pos = end
        if not images:
            return body, []
        out.append(body[pos:])
        return "".join(out), images
    except Exception:
        return body, []


def _select_judge_images_with_reasons(
    candidates: list[ImagePart],
) -> tuple[list[ImagePart], dict[str, str]]:
    """Apply the count + byte caps, preserving evidence order. Never raises.

    Returns (selected, {label: reason}) where the dict names every candidate NOT
    selected and why, so its placeholder can disclose it.

    An image that would breach the byte cap is SKIPPED, not a stop signal: one
    oversized blob early in the evidence must not suppress every smaller image
    behind it.
    """
    max_n = _judge_max_images()
    max_bytes = _judge_max_image_bytes()
    out: list[ImagePart] = []
    rejected: dict[str, str] = {}
    used = 0
    for img in candidates:
        if max_n <= 0:
            rejected[img.label] = "judge image attachment is disabled"
            continue
        if len(out) >= max_n:
            rejected[img.label] = f"judge image count limit reached ({max_n} per request)"
            continue
        n = _data_uri_b64_len(img.data_uri)
        if used + n > max_bytes:
            rejected[img.label] = f"judge image size limit reached ({max_bytes} bytes per request)"
            continue
        out.append(img)
        used += n
    return out, rejected


def _select_judge_images(candidates: list[ImagePart]) -> list[ImagePart]:
    return _select_judge_images_with_reasons(candidates)[0]


def _disclose_unattached_images(body: str, rejected: dict[str, str]) -> str:
    """Mark each inline-image placeholder in *body* whose image will not be
    attached, so the judge knows the image exists but was not shown.
    Placeholders keep their _image_placeholder_prefix, so survivor matching is
    unaffected."""
    for label, reason in rejected.items():
        prefix = _image_placeholder_prefix(label)
        start = body.find(prefix)
        if start < 0:
            continue
        end = body.find("]", start + len(prefix))
        if end < 0:
            continue
        body = (
            body[:end]
            + f"; present — contents not included: {reason}"
            + body[end:]
        )
    return body


def _split_evidence(evidence: str) -> tuple[str, str]:
    marker = "\n----- TRANSCRIPT (condensed) -----\n"
    if marker in evidence:
        files_part, _, transcript_part = evidence.partition(marker)
        return files_part, transcript_part
    return evidence, ""


def _judge_user_prompt(
    task_description: str, rubrics: list, evidence: "str | JudgeUserPayload"
) -> "str | JudgeUserPayload":
    from src.utils.prompt_loader import load_prompt
    from src.utils.rubric_targets import FILE_TARGETS, normalize_target
    output_files, transcript = _split_evidence(_payload_text(evidence))
    crit_lines = []
    for i, r in enumerate(rubrics):
        crit = r.get("criterion") if isinstance(r, dict) else str(r)
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        # File-target criteria carry an ordinal-safe [target: ...] tag routing
        # the judge to output_files evidence (see judge_system.md legend). The
        # tag lands inside _VERDICT_RE's `\d+\.\s.*?` capture and is discarded on
        # parse. Untagged criteria (the four calibrated targets) grade by wording.
        tgt = normalize_target(r.get("evaluation_target")) if isinstance(r, dict) else ""
        tag = f"  [target: {tgt}]" if tgt in FILE_TARGETS else ""
        crit_lines.append(f"{i + 1}. {crit}  [points: {wt}]{tag}")
    rendered = load_prompt(
        "judge_user",
        task_description=task_description,
        transcript=transcript or "(no transcript captured)",
        output_files=output_files or "(no deliverable files were collected)",
        rubrics_block="\n".join(crit_lines),
        n_criteria=len(rubrics),
    )
    # Images lifted by _gather_evidence must survive this seam: the rendered text
    # is what every family sees, but returning a bare str here would strand the
    # extraction and the transport with nothing between them (image-BLIND judge,
    # the silent no-signal case). Text-only evidence still returns a bare str, so
    # every council request body is byte-identical to before.
    images = _payload_images(evidence)
    return JudgeUserPayload(text=rendered, images=images) if images else rendered


# Per real-task forensics, agents intuit several different deliverable-root
# names (results, deliverables, output, out, artifacts). Hard-coding only
# results/ silently zeros out otherwise-correct runs (see Claude run in the
# trajectory failure report — wrote to deliverables/, scored 0/18).
_DELIVERABLE_DIR_NAMES = ("results", "deliverables", "output", "out", "artifacts")

# Text-readable deliverable formats. Files matching these go straight into the
# judge evidence dump unmodified (UTF-8 read, errors='replace'). Adding a
# binary extension here regresses per-member evidence parity: a 512 KB PDF
# becomes ~512 KB of mojibake which sorts ahead of report.md and exhausts the
# smaller council members' (Kimi 225 KB, GLM 175 KB per _FAMILY_EVIDENCE)
# truncation budget, producing "no report.md found" hallucinations. Strictly
# text-only here.
_DELIVERABLE_EXTS = {
    ".csv", ".tsv", ".md", ".markdown", ".json", ".txt", ".text",
    ".yaml", ".yml", ".html", ".htm", ".xml", ".log",
}
# Binary deliverable formats. These are made VISIBLE to the grader (listed in
# the deliverables manifest, collected by `_collect_deliverable_files`) so
# rubrics that simply check "did the agent produce report.pdf?" can grade them,
# but their content does NOT go into the judge evidence dump verbatim — the
# mojibake-poisoning hazard above still applies. A host-side text-extraction
# pass (pypdf / openpyxl / python-docx / python-pptx) will be wired into
# `_is_text_deliverable` in a follow-up step; until then binaries appear as
# presence-only entries.
_BINARY_DELIVERABLE_EXTS = {
    ".pdf", ".xlsx", ".docx", ".pptx",
}
# Image deliverables: surfaced to the judge with a stdlib dimension marker
# (PNG IHDR / JPEG SOF read with `struct` — NO Pillow, preserving the stdlib-only
# extractor posture). Content pixels are not sent (that is a capability-gated
# vision path, out of scope); the marker unblocks "did the agent generate an
# image of size WxH?" criteria that previously abstained because the file was
# silently dropped.
_IMAGE_DELIVERABLE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
_ALL_DELIVERABLE_EXTS = _DELIVERABLE_EXTS | _BINARY_DELIVERABLE_EXTS | _IMAGE_DELIVERABLE_EXTS
# Raw-byte cap for TEXT files found by the root-level scan only. Text bodies are
# pasted verbatim, so raw size is their evidence cost. Documents (pdf/docx/xlsx/
# pptx) are bounded by _EXTRACT_CHAR_CAP instead and images contribute only a
# marker, so neither is ever rejected on raw byte size. A root text file over
# this cap is still collected and disclosed with a presence marker.
_ROOT_SCAN_MAX_FILE_BYTES = 100_000
# Cap on extracted-text length per document deliverable (pdf/docx/xlsx/pptx).
# Bounds the per-member evidence budget so a large extraction cannot bury
# report.md for the smaller-context council members (Kimi 225 KB / GLM 175 KB).
# Text beyond the cap is not sent, and the block header says so.
_EXTRACT_CHAR_CAP = 150_000

# Presence-marker reasons. A presence marker tells the judge a file EXISTS even
# though its contents are not in the prompt, which judge_system.md maps to
# [[SATISFIED: No]] + [[TRUNCATION_AFFECTED: Yes]] (-> Human Evaluation) for a
# content requirement, instead of "file never produced".
_REASON_ROOT_TEXT_CAP = (
    f"text file exceeds the {_ROOT_SCAN_MAX_FILE_BYTES}-byte root-scan size cap"
)
_REASON_UNREADABLE = "file could not be read"
_REASON_NOT_EXTRACTABLE = "contents not extractable (no text could be extracted from this document)"
_REASON_EVIDENCE_BUDGET = "evidence budget exceeded"
_REASON_UNSUPPORTED = "unsupported file type"


def _looks_like_deliverable(path: Path, root: Path) -> bool:
    """True if a workspace-root file's contents can go to the judge: a supported
    deliverable extension and, for TEXT files only, within
    _ROOT_SCAN_MAX_FILE_BYTES. Documents and images are never rejected on raw
    size. A supported file this returns False for is still disclosed by the root
    scan with a presence marker (see _root_scan_skip_reason)."""
    if path.suffix.lower() not in _ALL_DELIVERABLE_EXTS:
        return False
    if not _is_text_deliverable(path):
        return True
    try:
        return path.stat().st_size <= _ROOT_SCAN_MAX_FILE_BYTES
    except OSError:
        return False


def _root_scan_skip_reason(path: Path) -> str:
    """Why a supported root-scan file's contents are withheld (see
    _looks_like_deliverable)."""
    try:
        path.stat()
    except OSError:
        return _REASON_UNREADABLE
    return _REASON_ROOT_TEXT_CAP


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _presence_marker(label: str, path: Path, reason: str) -> str:
    """Evidence block for a file that exists but whose contents are not in the
    prompt. The judge must be able to tell this apart from a file that was never
    produced."""
    size = _file_size(path)
    size_txt = f"{size} bytes" if size is not None else "size unknown"
    return (
        f"\n----- DELIVERABLE: {label}\n"
        f"({size_txt}, present — contents not included: {reason})\n"
        "-----\n"
    )


def _is_text_deliverable(path: Path) -> bool:
    # Binary artifacts (.jpg/.pdf/.png) read with errors='replace' inject
    # hundreds of KB of mojibake that sorts ahead of report.md and exhausts the
    # smaller council members' truncation budget, breaking per-member evidence
    # parity (Kimi/GLM hallucinating 'no report.md'). Text-only here. Supported
    # binaries (_BINARY_DELIVERABLE_EXTS) flow through _looks_like_deliverable
    # for presence, but are excluded from this verbatim-content path until
    # host-side text extraction lands.
    return path.suffix.lower() in _DELIVERABLE_EXTS


def _is_binary_deliverable(path: Path) -> bool:
    """True if the path is a recognised binary deliverable (pdf/xlsx/docx/pptx)
    whose content we cannot dump verbatim into judge evidence today, but whose
    presence still must appear in the manifest for rubrics keyed on file
    existence (e.g. 'did the agent write report.pdf?')."""
    return path.suffix.lower() in _BINARY_DELIVERABLE_EXTS


# Persona/bootstrap scaffolding the harness copies into every workspace, and
# harness-written files at the task_output/ top level. Neither is agent output,
# but both match a deliverable extension, so the root-level sweep used to hand
# them to the judge on every run (8 persona files + the gateway log, up to
# 512 KB of budget). Applied to the ROOT-LEVEL sweep only: a persona file the
# agent actually modified (e.g. MEMORY.md) is copied into artifacts/ by the
# baseline diff and still reaches the judge through the evidence dir itself.
# Names mirror s3_artifacts._TEMPLATE_FILE_NAMES; compared case-insensitively.
_PERSONA_FILE_NAMES = frozenset({
    "identity.md", "bootstrap.md", "heartbeat.md", "user.md",
    "soul.md", "agents.md", "tools.md", "agent.md", "memory.md",
})
_HARNESS_FILE_GLOBS = ("openclaw-*.log", "artifacts_excluded.json")


def _is_scaffold_or_harness_file(path: Path) -> bool:
    name = path.name
    return name.lower() in _PERSONA_FILE_NAMES or any(
        fnmatch.fnmatch(name, pat) for pat in _HARNESS_FILE_GLOBS
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_deliverables_with_status(
    workspace_results: Path,
) -> list[tuple[Path, str, str | None]]:
    """Collected deliverables as (path, label, skip_reason) triples.

    `label` is the file's workspace-relative POSIX path (bare name for a
    top-level file) and is what the judge sees in the DELIVERABLE header, so two
    different files sharing a basename stay distinguishable. It never carries a
    host path.

    `skip_reason` is None when the file's contents may go to the judge, else why
    they are withheld (e.g. a root-scan text file over _ROOT_SCAN_MAX_FILE_BYTES).
    A withheld file is still returned so it can be disclosed with a presence
    marker: a collection limit must never make a produced file look absent.

    The same agent file is physically present under BOTH artifacts/<rel>
    (baseline-diff copy) and workspace_full/<rel> (full-tree copy); a path-only
    `seen` set let both through, so the judge received identical blocks twice and
    the copies burned the smaller members' evidence budget. A file is a repeat
    only when its label AND content hash both match one already collected —
    identical bytes under a different name/path are kept (a rubric may key on
    either name), as is a same-named file with different bytes."""
    found: list[tuple[Path, str, str | None]] = []
    seen: set[Path] = set()
    seen_content: set[tuple[str, str]] = set()

    def _add(f: Path, label_root: Path, skip_reason: str | None = None) -> None:
        seen.add(f)
        try:
            label = f.relative_to(label_root).as_posix()
        except ValueError:
            label = f.name
        try:
            key = (label, _file_sha256(f))
        except OSError:
            key = None  # unreadable: keep it, grading must never fail
        if key is not None:
            if key in seen_content:
                return
            seen_content.add(key)
        found.append((f, label, skip_reason))

    def _add_from(root: Path, label_root: Path | None = None) -> None:
        # Recursive sweep of an evidence/deliverable dir. No raw-byte cap here:
        # text bodies are bounded by the evidence budget, documents by
        # _EXTRACT_CHAR_CAP, and images contribute only a marker.
        if not root.is_dir():
            return
        for f in sorted(root.rglob("*")):
            if not (f.is_file() and f not in seen):
                continue
            if f.suffix.lower() in _ALL_DELIVERABLE_EXTS:
                _add(f, label_root or root)

    if workspace_results:
        results_path = Path(workspace_results)
        _add_from(results_path)
        # Sibling sweep: workspace_full/<deliverable-name>/ written by the agent
        # outside results/ — collect_output_from_container always preserves the
        # full /tmp_workspace tree under workspace_full/ for exactly this case.
        workspace_root = results_path.parent.parent if results_path.name == "results" else results_path.parent
        for sibling in (workspace_root / "workspace_full", workspace_root):
            if not sibling.is_dir():
                continue
            for name in _DELIVERABLE_DIR_NAMES:
                # Labels stay relative to the sweep root (output/x.csv), so they
                # line up with the artifacts/ copy of the same file.
                _add_from(sibling / name, label_root=sibling)
            # Some agents save deliverables at the workspace ROOT (e.g.
            # /tmp_workspace/foo.csv) rather than in a named subdir. Recover
            # text-like deliverable files sitting directly under the sweep root,
            # without recursing into input/scaffold subtrees.
            for f in sorted(sibling.glob("*")):
                if (not f.is_file() or f in seen
                        or f.suffix.lower() not in _ALL_DELIVERABLE_EXTS
                        or _is_scaffold_or_harness_file(f)):
                    continue
                if _looks_like_deliverable(f, sibling):
                    _add(f, sibling)
                else:
                    # Oversized root text (or unreadable): disclose, don't drop.
                    _add(f, sibling, skip_reason=_root_scan_skip_reason(f))
    return found


def _collect_deliverables(workspace_results: Path) -> list[tuple[Path, str]]:
    """(path, label) pairs for every collected deliverable, including ones
    whose contents are withheld (see _collect_deliverables_with_status)."""
    return [(f, label) for f, label, _ in _collect_deliverables_with_status(workspace_results)]


def _collect_deliverable_files(workspace_results: Path) -> list[Path]:
    return [f for f, _ in _collect_deliverables(workspace_results)]


def _is_image_deliverable(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_DELIVERABLE_EXTS


_DOCX_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_XLSX_SS_T = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PPTX_A_T = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    # Stdlib width/height read from the file header (NO Pillow). PNG: IHDR at a
    # fixed offset after the 8-byte signature. JPEG: scan segments for a SOF
    # marker (0xFFC0-0xFFCF except C4/C8/CC) whose payload carries height/width.
    try:
        data = path.read_bytes()
    except Exception:
        return None
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return (int(w), int(h))
        if data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (int(w), int(h))
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg
    except Exception:
        return None
    return None


def _extract_document_text(path: Path) -> str | None:
    # Stdlib-only text extraction for binary deliverables (NO python-docx/openpyxl
    # /python-pptx). OOXML formats are ZIPs of XML: .docx reads word/document.xml
    # <w:t> nodes; .xlsx reads sharedStrings + worksheet <t> nodes; .pptx reads
    # ppt/slides/slideN.xml <a:t> nodes. .pdf uses a GUARDED optional import
    # (pypdf): when installed, extracts real PDF text; when absent, degrades to None
    # (-> presence marker). pypdf is now a host requirement (requirements.txt) but the
    # import stays guarded so a missing wheel degrades rather than crashing.
    # Returns the FULL extracted text (uncapped) or None. NEVER raises.
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml")
            root = ET.fromstring(xml)
            text = "".join(n.text or "" for n in root.iter(_DOCX_W_T)).strip()
            return text or None
        if ext == ".xlsx":
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                shared: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    for si in ss.iter(f"{_XLSX_NS}si"):
                        shared.append("".join(t.text or "" for t in si.iter(_XLSX_SS_T)))
                rows_out: list[str] = []
                for name in sorted(n for n in names
                                   if n.startswith("xl/worksheets/") and n.endswith(".xml")):
                    sheet = ET.fromstring(z.read(name))
                    for row in sheet.iter(f"{_XLSX_NS}row"):
                        cells: list[str] = []
                        for c in row.iter(f"{_XLSX_NS}c"):
                            ctype = c.get("t")
                            v = c.find(f"{_XLSX_NS}v")
                            if ctype == "s" and v is not None and v.text is not None:
                                try:
                                    cells.append(shared[int(v.text)])
                                except (ValueError, IndexError):
                                    pass
                            elif ctype == "inlineStr":
                                cells.append("".join(t.text or "" for t in c.iter(_XLSX_SS_T)))
                            elif v is not None and v.text:
                                cells.append(v.text)
                        if cells:
                            rows_out.append(" | ".join(cells))
            text = "\n".join(rows_out).strip()
            return text or None
        if ext == ".pptx":
            slide_parts: list[str] = []
            with zipfile.ZipFile(path) as z:
                for name in sorted(z.namelist()):
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                        slide = ET.fromstring(z.read(name))
                        slide_parts.extend(n.text or "" for n in slide.iter(_PPTX_A_T))
            text = " ".join(p for p in slide_parts if p).strip()
            return text or None
        if ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                return None
            reader = pypdf.PdfReader(str(path))
            text = "".join((pg.extract_text() or "") for pg in reader.pages).strip()
            return text or None
    except Exception:
        return None
    return None


def _extract_text_deliverable(path: Path) -> str | None:
    """Extracted document text capped at _EXTRACT_CHAR_CAP, or None."""
    text = _extract_document_text(path)
    return text[:_EXTRACT_CHAR_CAP] if text else None


def _deliverable_evidence_marker(
    path: Path, label: str | None = None, skip_reason: str | None = None
) -> tuple[str, list[ImagePart]]:
    # Single dispatch point turning one collected deliverable into an evidence
    # block. Returns (block_text, images). Text deliverables read verbatim EXCEPT
    # for inline base64 images, which are lifted here — before any budgeting — so
    # a blob can never be bisected by a char slice; documents (pdf/docx/xlsx/pptx)
    # route through _extract_document_text; images and anything whose contents
    # cannot be sent get a presence marker (verbatim binary bytes would be
    # mojibake). A collected file ALWAYS yields a block: an unreadable or
    # withheld file is disclosed, never silently dropped. NEVER raises
    # (grading-must-never-fail).
    # `label` (workspace-relative path from _collect_deliverables) names the block
    # AND stems its inline-image labels, so same-basename files never share an
    # image label; it defaults to the bare filename.
    label = label or path.name
    if skip_reason:
        return _presence_marker(label, path, skip_reason), []
    try:
        if _is_text_deliverable(path):
            body = path.read_text(encoding="utf-8", errors="replace")
            body, images = _extract_inline_images(body, label)
            return f"\n----- DELIVERABLE: {label} -----\n{body}", images
        if _is_image_deliverable(path):
            # Standalone image files are never attached as pixels; the marker
            # carries stdlib-parsed dimensions for "did it produce an image of
            # size WxH?" criteria.
            dims = _image_dimensions(path)
            size = f"image {dims[0]}x{dims[1]}" if dims else "image"
            return _presence_marker(
                label, path,
                f"image pixels are not attached for standalone image files ({size})",
            ), []
        if _is_binary_deliverable(path):
            extracted = _extract_document_text(path)
            if not extracted:
                return _presence_marker(label, path, _REASON_NOT_EXTRACTABLE), []
            if len(extracted) <= _EXTRACT_CHAR_CAP:
                return f"\n----- DELIVERABLE: {label} (extracted text) -----\n{extracted}", []
            size = _file_size(path)
            size_txt = f"{size} bytes" if size is not None else "size unknown"
            return (
                f"\n----- DELIVERABLE: {label} (extracted text, truncated: first "
                f"{_EXTRACT_CHAR_CAP} of {len(extracted)} chars included; "
                f"{size_txt}, present — remaining contents not included: extracted "
                f"text exceeds the {_EXTRACT_CHAR_CAP}-char extraction cap) -----\n"
                f"{extracted[:_EXTRACT_CHAR_CAP]}"
            ), []
    except Exception:
        return _presence_marker(label, path, _REASON_UNREADABLE), []
    return _presence_marker(label, path, _REASON_UNSUPPORTED), []


_TRANSCRIPT_MARKER = "\n----- TRANSCRIPT (condensed) -----\n"
# Terminal-turn landmarks emitted by _condense_transcript_for_judge (see
# eval/run_batch.py) and named in system_prompts/judge_system.md. A task can end
# on an assistant message OR a submit-tool action, so a boundary-aware cut must
# recognise both to keep the final turn whole.
_TERMINAL_LANDMARKS = ("[FINAL ASSISTANT MESSAGE]", "[SUBMIT TOOL OUTPUT]")


def _budget_transcript(transcript: str, budget: int) -> str:
    # Middle-drop on physical-line boundaries: keep a head window, an explicit
    # truncation marker, and a tail window anchored on the terminal landmark so
    # the final turn survives. INVARIANT: the return is ALWAYS <= budget chars
    # (the OAuth judge 200K ceiling, AGENTS.md #18, is a hard gate — an
    # over-budget transcript makes _gather_evidence exceed the member budget and
    # every criterion abstains). When the final turn alone exceeds budget we keep
    # its END (most-recent content) clamped to budget, never the whole transcript.
    if budget <= 0:
        return ""
    if len(transcript) <= budget:
        return transcript
    lines = transcript.split("\n")
    # LAST landmark, not first: agent text can echo a landmark string earlier; a
    # first-match anchor grows the tail from that decoy back to ~whole transcript
    # (measured 7x over budget). `-1` sentinel honors a landmark at line 0.
    anchor = -1
    for i in range(len(lines) - 1, -1, -1):
        if any(mark in lines[i] for mark in _TERMINAL_LANDMARKS):
            anchor = i
            break
    tail_start = anchor if anchor >= 0 else len(lines) - 1
    tail = "\n".join(lines[tail_start:])
    marker = "\n... [truncated {n} lines] ...\n"
    # If the final turn alone can't fit, drop the head entirely and keep the END
    # of the tail so the return stays <= budget (the marker + turn-end, not the
    # whole transcript). rendered with n=tail_start (all prior lines dropped).
    rendered_marker = marker.format(n=tail_start)
    if len(tail) + len(rendered_marker) >= budget:
        room = budget - len(rendered_marker)
        if room <= 0:
            return tail[-budget:]
        return rendered_marker + tail[-room:]
    head: list[str] = []
    head_len = 0
    tail_len = len(tail) + 1
    i = 0
    while i < tail_start:
        add = len(lines[i]) + 1
        if head_len + add + tail_len + len(marker.format(n=tail_start - i)) >= budget:
            break
        head.append(lines[i])
        head_len += add
        i += 1
    dropped = tail_start - i
    if dropped <= 0:
        return "\n".join(head + [tail])[:budget]
    return "\n".join(head) + marker.format(n=dropped) + tail


# Directory names that hold agent scratch/work-product (build scripts, the
# agent's own input dumps) rather than final deliverables. Demoted to the END
# of the evidence ordering: ajax_moreno 2026-09-05 forensics — 300+ files the
# agent dumped under artifacts/extract/ sorted ahead of the two rubric-named
# deliverables by size, pushed their content past the evidence cap, and ~20
# criteria failed as "cannot verify" on a run whose deliverables were correct.
# Only components BELOW the collected deliverable root are consulted, so an
# unrelated "tmp"/"build" in the host path (e.g. pytest tmp_path) never
# triggers the demotion.
_SCRATCH_DIR_NAMES = {
    "_scratch", "build", "extract", "scratch", "tmp", "temp",
    "__pycache__", "node_modules", ".git",
}

# File tokens in rubric criterion text ("delivers report.pdf", "the summary in
# notes.md states X"). Basename-shaped only (no path separators); extension
# list mirrors _ALL_DELIVERABLE_EXTS.
_RUBRIC_FILE_RE = re.compile(
    r"[\w][\w.\-]*\.(?:pdf|html?|csv|tsv|md|markdown|json|xlsx|docx|pptx"
    r"|txt|text|xml|ya?ml|log|png|jpe?g|webp|gif)\b",
    re.IGNORECASE,
)


def _rubric_file_names(rubrics: list) -> frozenset[str]:
    """Lowercased basenames of files the rubric mentions by name.

    Evidence assembly ranks these files FIRST: a criterion that names
    report.pdf is ungradable when that file's content falls past the evidence
    cap, so rubric-named files must survive every member's budget."""
    names: set[str] = set()
    for r in rubrics or []:
        crit = r.get("criterion") if isinstance(r, dict) else str(r)
        for tok in _RUBRIC_FILE_RE.findall(str(crit or "")):
            names.add(tok.lower())
    return frozenset(names)


def _in_scratch_subdir(path: Path) -> bool:
    # Scratch check scoped to components BELOW the last deliverable-root
    # component (results/artifacts/... or workspace_full) so host-path noise
    # like /tmp/pytest-*/ never demotes a file.
    parts = [p.lower() for p in path.parts[:-1]]
    anchor = -1
    for i, p in enumerate(parts):
        if p in _DELIVERABLE_DIR_NAMES or p == "workspace_full":
            anchor = i
    if anchor < 0:
        return False
    return any(p in _SCRATCH_DIR_NAMES for p in parts[anchor + 1:])


# Evidence-budget note naming collected files whose contents were cut or not
# included. Together with per-file presence markers it lets the judge tell
# "file was never produced" (verdict No) from "file was produced but its
# contents are not in the prompt" (No + TRUNCATION_AFFECTED — which the scoring
# layer then routes to Human Evaluation instead of a graded fail).
_OMISSION_MANIFEST_MAX_NAMES = 40
_PARTIAL_KEEP_MIN_ROOM = 800
_MIN_TRANSCRIPT_TAIL = 200
_BUDGET_CUT_MARK = "\n... [truncated for evidence budget] ...\n"


def _surviving_images(text: str, candidates: list[ImagePart]) -> list[ImagePart]:
    """Images whose placeholder outlived budgeting, in order. Never raises.

    An image whose placeholder was cut (mid-block head+tail truncation, or the
    defensive final clamp) must NOT be attached: pixels in front of the judge
    with no text naming them are unattributable evidence.
    """
    return [i for i in candidates if _image_placeholder_prefix(i.label) in text]


def _is_presence_block(block: str, label: str) -> bool:
    """True for a block produced by _presence_marker (contents not included)."""
    return block.startswith(f"\n----- DELIVERABLE: {label}\n(")


def _omission_note(omitted: list[str]) -> str:
    listing = ", ".join(omitted[:_OMISSION_MANIFEST_MAX_NAMES])
    extra = len(omitted) - _OMISSION_MANIFEST_MAX_NAMES
    return (
        f"\n----- EVIDENCE BUDGET NOTE: {len(omitted)} collected file(s)"
        f" omitted or cut for budget: {listing}"
        + (f" [+{extra} more]" if extra > 0 else "")
        + " -----\n"
    )


def _omission_note_upper_bound(labels: list[str]) -> int:
    """Longest _omission_note any subset of *labels* (each possibly suffixed
    " (partial)") can produce, so the note's space is reserved up front and the
    note is never clipped."""
    n = len(labels)
    names = sorted((len(label) + len(" (partial)") for label in labels), reverse=True)
    names = names[:_OMISSION_MANIFEST_MAX_NAMES]
    return (
        len(_omission_note([]))
        + len(str(n))
        + sum(names) + 2 * max(0, len(names) - 1)
        + len(f" [+{n} more]")
    )


def _count_only_note(total: int, unlisted: int) -> str:
    return (
        f"\n----- EVIDENCE BUDGET NOTE: {total} collected file(s) present but"
        f" contents not included for budget; {unlisted} of them not listed by"
        " name for budget -----\n"
    )


def _budget_deliverables(
    blocks: list[tuple[str, str, list[ImagePart], str]],
    deliv_budget: int,
) -> tuple[str, list[ImagePart]]:
    """Fit deliverable blocks into *deliv_budget* chars without ever silently
    dropping a file. Returns (text, images of blocks kept in full or in part);
    the text is always <= deliv_budget.

    *blocks* are (label, block_text, images, presence_marker) in priority order.
    Every file not kept in full stays disclosed: a partial keep carries a
    truncation mark, a dropped block is replaced by its presence marker, and the
    EVIDENCE BUDGET NOTE names them. Before a block is kept, space is reserved
    for every LATER file's presence marker plus the note, so disclosure always
    fits. If even the markers cannot all fit, markers are emitted in priority
    order and a count-only note discloses the rest.
    """
    full = "".join(block for _, block, _, _ in blocks)
    if len(full) <= deliv_budget:
        return full, [i for _, _, imgs, _ in blocks for i in imgs]
    n = len(blocks)
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + len(blocks[i][3])
    note_reserve = _omission_note_upper_bound([label for label, _, _, _ in blocks])

    if note_reserve + suffix[0] <= deliv_budget:
        kept: list[str] = []
        disclosed: list[str] = []
        images: list[ImagePart] = []
        omitted: list[str] = []
        used = 0
        # Invariant: used + note_reserve + suffix[i] <= deliv_budget.
        for i, (label, block, block_images, presence) in enumerate(blocks):
            room = deliv_budget - used - note_reserve - suffix[i + 1]
            if len(block) <= room:
                kept.append(block)
                used += len(block)
                images.extend(block_images)
            elif (
                not omitted
                and room >= _PARTIAL_KEEP_MIN_ROOM
                and not _is_presence_block(block, label)
            ):
                # Head+tail keep (mirrors _budget_transcript): deliverable text
                # files often carry markup/data bulk up front and the
                # human-readable summary at the END, so a head-only cut drops
                # exactly the content criteria cite. _surviving_images later
                # drops images whose placeholder fell in the excised middle.
                half = (room - len(_BUDGET_CUT_MARK)) // 2
                tail = room - len(_BUDGET_CUT_MARK) - half
                kept.append(block[:half] + _BUDGET_CUT_MARK + block[-tail:])
                used += room
                images.extend(block_images)
                omitted.append(f"{label} (partial)")
            else:
                # Whole block dropped: its images are not attached (pixels with
                # no text naming them), but the file itself is disclosed.
                disclosed.append(presence)
                used += len(presence)
                omitted.append(label)
        note = _omission_note(omitted) if omitted else ""
        return "".join(kept) + note + "".join(disclosed), images

    # Too many files for one marker each: disclose as many as fit, by priority,
    # and count the rest.
    total = n
    note_max = len(_count_only_note(total, total))
    if note_max > deliv_budget:
        return "", []
    out: list[str] = []
    used = 0
    listed = 0
    for _, _, _, presence in blocks:
        if used + len(presence) + note_max > deliv_budget:
            break
        out.append(presence)
        used += len(presence)
        listed += 1
    return "".join(out) + _count_only_note(total, total - listed), []


def _gather_evidence(
    workspace_results: Path,
    transcript_text: str,
    budget: int | None = None,
    rubric_names: frozenset[str] | None = None,
    attach_images: bool = True,
) -> JudgeUserPayload:
    """Assemble one judge member's evidence text (and image attachments).

    *attach_images* is False for text-only judge transports; every inline image
    placeholder is then marked as not attached instead of silently losing the
    pixels at the transport."""
    deliverables = _collect_deliverables_with_status(workspace_results)
    # Scrub inline base64 out of the TRANSCRIPT too, discarding the images: a
    # tool result that cat'd an image-bearing deliverable would otherwise carry
    # the same blobs back into the same user turn through the other seam. Text
    # only (placeholders), so no vision cost and nothing to survivor-filter.
    transcript_text, _ = _extract_inline_images(transcript_text, "transcript")
    # Order so the files the rubric is actually ABOUT survive every member's
    # truncation budget: rubric-named files first, then report/flagged stems,
    # then other deliverables, then scratch subtrees — within each rank, by the
    # file's EVIDENCE size (its rendered block), smallest first. Raw bytes on
    # disk are the wrong measure: a 1 MB PDF whose extracted text is 14 KB, or
    # an image that contributes a one-line marker, would otherwise sort behind
    # far bulkier text and be the first thing cut.
    named = rubric_names or frozenset()
    _PRIMARY = ("report", "flagged")

    def _rank(path: Path) -> int:
        stem = path.stem.lower()
        if path.name.lower() in named:
            return 0
        if _in_scratch_subdir(path):
            return 3
        if any(k in stem for k in _PRIMARY):
            return 1
        return 2

    rendered: list[tuple[Path, str, str, list[ImagePart]]] = []
    label_uses: dict[str, int] = {}
    for f, label, skip_reason in deliverables:
        # Collection keeps different files that share a label (e.g. an
        # artifacts/ copy and a diverged workspace_full/ copy). Give each its
        # own name in the evidence so the judge can tell them apart and their
        # inline-image labels (and the attach/disclose decisions keyed on them)
        # never collide. Numbered in collection order, so the evidence-dir
        # copy keeps the plain name.
        uses = label_uses.get(label, 0) + 1
        label_uses[label] = uses
        if uses > 1:
            label = f"{label} [{uses}]"
        block, block_images = _deliverable_evidence_marker(f, label, skip_reason)
        rendered.append((f, label, block, block_images))
    rendered.sort(key=lambda r: (_rank(r[0]), len(r[2]), r[0].name, r[1]))

    # Image attachment is decided BEFORE budgeting so every placeholder of an
    # image that will not be attached is disclosed in the block text, and that
    # disclosure is counted against the budget like any other text.
    candidates = [img for _, _, _, imgs in rendered for img in imgs]
    if attach_images:
        selected, rejected = _select_judge_images_with_reasons(candidates)
    else:
        selected = []
        rejected = {
            img.label: "this judge does not receive image attachments"
            for img in candidates
        }
    selected_labels = {img.label for img in selected}

    blocks: list[tuple[str, str, list[ImagePart], str]] = []
    for f, label, block, block_images in rendered:
        own_rejected = {i.label: rejected[i.label] for i in block_images if i.label in rejected}
        if own_rejected:
            block = _disclose_unattached_images(block, own_rejected)
        presence = (
            block if _is_presence_block(block, label)
            else _presence_marker(label, f, _REASON_EVIDENCE_BUDGET)
        )
        blocks.append((
            label, block,
            [i for i in block_images if i.label in selected_labels],
            presence,
        ))

    no_deliverables = (
        "\n(no deliverable files were collected under any of: "
        + ", ".join(f"{n}/" for n in _DELIVERABLE_DIR_NAMES)
        + ")\n"
    )

    def _fit_deliverables(limit: int) -> tuple[str, list[ImagePart]]:
        if not blocks:
            return (no_deliverables if len(no_deliverables) <= limit else ""), []
        return _budget_deliverables(blocks, limit)

    effective = _JUDGE_MAX_EVIDENCE if budget is None else budget
    # Budget deliverables and transcript SEPARATELY. The transcript marker can
    # then never be sliced off (so _split_evidence never silently returns ""),
    # and the boundary-aware cut keeps the final turn whole. A tiny transcript
    # floor is reserved so the marker + final turn survive even when deliverables
    # are large; realistic per-family budgets (175K-1.35M) are the operative path.
    #
    # Image bytes are tracked SEPARATELY from this char budget: the blobs are no
    # longer in the text at all (only their placeholders are), and _judge_max_*
    # caps bound the attachment cost.
    if effective is None:
        deliv_out = "".join(block for _, block, _, _ in blocks) or no_deliverables
        kept_images = [i for _, _, imgs, _ in blocks for i in imgs]
        text = deliv_out + (
            f"{_TRANSCRIPT_MARKER}{transcript_text}" if transcript_text else ""
        )
        return JudgeUserPayload(text=text, images=_surviving_images(text, kept_images))
    if not transcript_text:
        deliv_out, kept_images = _fit_deliverables(max(0, effective))
        return JudgeUserPayload(
            text=deliv_out, images=_surviving_images(deliv_out, kept_images)
        )
    floor = min(
        len(_TRANSCRIPT_MARKER) + len(transcript_text),
        max(2000, effective // 5),
    )
    deliv_budget = max(0, effective - floor)
    # On a budget so small the transcript floor takes everything, still leave
    # room for the count-only note (if a minimal transcript tail also fits), so
    # produced files are never silently absent from the evidence.
    min_disclosure = len(_count_only_note(len(blocks), len(blocks))) if blocks else 0
    if (
        deliv_budget < min_disclosure
        and effective - len(_TRANSCRIPT_MARKER) - min_disclosure >= _MIN_TRANSCRIPT_TAIL
    ):
        deliv_budget = min_disclosure
    deliv_out, kept_images = _fit_deliverables(deliv_budget)
    t_budget = effective - len(deliv_out) - len(_TRANSCRIPT_MARKER)
    t_out = _budget_transcript(transcript_text, max(0, t_budget))
    # Defensive final clamp: the OAuth 200K ceiling (AGENTS.md #18) is a hard gate,
    # so the assembled evidence must NEVER exceed `effective` even if a component
    # budget math drifts. _split_evidence still finds the marker because deliv_out
    # + marker are budgeted to fit before the transcript tail.
    text = (deliv_out + _TRANSCRIPT_MARKER + t_out)[:effective]
    return JudgeUserPayload(text=text, images=_surviving_images(text, kept_images))


_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "total_tokens": 0,
    "request_count": 0,
    "cost_usd": 0.0,
}


# Judge per-token rate resolution. Council members resolve via their stable
# FAMILY (_FAMILY_RATES, rotation-proof); OpenAI single-judge fallbacks resolve
# by model NAME (_OPENAI_JUDGE_RATES). Both tables live in the FAMILY block above
# and MUST track real published provider list prices — the council cost line in
# usage.json is computed directly from them (NOT via litellm), so any drift
# silently mis-bills every graded task. There is no longer a profile-id-keyed
# rate table: a rotating id is never a billing key.
def _judge_rate_for(
    model: str, family: str | None = None
) -> tuple[float, float, float, float] | None:
    fam = _family_for(model, family)
    if fam is not None:
        return _FAMILY_RATES.get(fam)
    # Non-council (OpenAI) judge: substring-match the model name.
    for key, val in _OPENAI_JUDGE_RATES.items():
        if key in (model or ""):
            return val
    return None


def _judge_cost_usd(
    model: str, in_tok: int, out_tok: int, c_read: int, c_write: int,
    family: str | None = None,
) -> tuple[float, bool]:
    # Returns (cost_usd, priced_ok). An unknown model is soft-degraded to
    # (0.0, False) here rather than raising, so a long grading run never crashes
    # mid-flight on a mis-configured judge. The fail-fast guarantee lives in
    # validate_judge_pricing(), called at grade_with_rubric() startup; callers
    # that bypass grade_with_rubric must run that validator themselves first.
    # Subscription judging (sonnet via the Claude Max OAuth bridge) is not
    # metered per-token — it draws on the flat Max plan — so the per-token list
    # price would be a misleading "charge". Force cost_usd=0 (priced_ok=True) for
    # that path; real cost is reconciled separately later. Token counts are kept.
    if family == "sonnet":
        try:
            from . import judge_litellm  # local import: avoid import-time cost
            if judge_litellm._judge_oauth_bridge_url():
                return 0.0, True
        except Exception:
            pass
    rate = _judge_rate_for(model, family)
    if rate is None:
        logger.error(
            "[judge_cost] no rate for judge model=%r family=%r; cost recorded as "
            "0.0 and flagged unpriced. Add a council family to _FAMILY_RATES or an "
            "OpenAI model to _OPENAI_JUDGE_RATES in grading.py.",
            model, family,
        )
        return 0.0, False
    r_in, r_out, r_cached, r_cwrite = rate
    cost = in_tok * r_in + c_read * r_cached + c_write * r_cwrite + out_tok * r_out
    return cost, True


def validate_judge_pricing(members: Sequence[CouncilMember | str]) -> None:
    # Fail-fast at config boundary (grade_with_rubric startup), never mid-run.
    # Two distinct failure modes so the operator knows which table to edit:
    # a CouncilMember with an unpriced family vs an OpenAI judge name with no rate.
    bad_family: list[str] = []
    bad_openai: list[str] = []
    for m in members:
        if isinstance(m, CouncilMember):
            if m.family not in _FAMILY_RATES:
                bad_family.append(f"{m.family} ({m.model})")
        elif _judge_rate_for(m) is None:
            bad_openai.append(m)
    if bad_family:
        raise RuntimeError(
            "Council member(s) have no _FAMILY_RATES entry and would be billed at "
            f"$0: {bad_family}. Add the family's per-token rates before running."
        )
    if bad_openai:
        raise RuntimeError(
            "OpenAI judge model(s) have no _OPENAI_JUDGE_RATES entry and would be "
            f"billed at $0: {bad_openai}. Add per-token rates before running."
        )


def _judge_gpt_api_key() -> str:
    """Dedicated GPT-judge key, read LIVE (mirrors config.judge_gpt_api_key).

    Kept separate from KENSEI_OPENAI_API_KEY so the judge and the
    trajectory/agent can bill different accounts/quotas.
    """
    return (
        os.environ.get("KENSEI_JUDGE_GPT_API_KEY")
        or os.environ.get("JUDGE_GPT_API_KEY")
        or ""
    ).strip()


def _judge_gpt_model() -> str:
    """Configured GPT judge model id, read LIVE (mirrors config.judge_gpt_model)."""
    return (
        os.environ.get("KENSEI_JUDGE_GPT_MODEL")
        or os.environ.get("JUDGE_GPT_MODEL")
        or ""
    ).strip()


def _judge_codex_bridge_url() -> str:
    """Host origin of the codex OAuth bridge for the GPT judge, or "" when off.

    Set by eval/run_batch.py ONLY when the run used --use-codex-oauth (it picks a
    loopback port, publishes the bridge on it, and exports
    KENSEI_JUDGE_CODEX_BRIDGE_URL=http://127.0.0.1:<port>). Its presence is the
    gate: unlike the Sonnet OAuth route there is no provider check, because the
    codex bridge is an add-on to whatever provider the run already uses (Bedrock
    or OAuth) and never the run's own credential. Bare origin, no /v1 tail.
    """
    return (os.environ.get("KENSEI_JUDGE_CODEX_BRIDGE_URL") or "").strip()


def _judge_codex_bridge_secret() -> str:
    """Bridge secret presented to the codex bridge as `Authorization: Bearer`.

    Same value the sidecar injects into the container as KAIJU_CODEX_BRIDGE_SECRET
    (run_batch.py exports it on the host as WCB_CODEX_BRIDGE_SECRET).
    """
    return (os.environ.get("WCB_CODEX_BRIDGE_SECRET") or "").strip()


def _judge_codex_bridge_model() -> str:
    """GPT judge model id sent on the codex-bridge route.

    Defaults to the flagship gpt-5.6-sol (codex-accepted). Overridable via
    KENSEI_JUDGE_CODEX_BRIDGE_MODEL. NOTE: if the bridge container was started
    with KAIJU_CODEX_MODEL set (trajectory model pin), the bridge's
    _normalize_model overrides this literal and every judge request runs the
    pinned model instead — acceptable since both are gpt-5.6 family, surfaced in
    the preflight log.
    """
    return (
        os.environ.get("KENSEI_JUDGE_CODEX_BRIDGE_MODEL")
        or _judge_gpt_model()
        or "gpt-5.6-sol"
    ).strip()


# gpt-5.6 reasoning effort for verdicts. "low" is deliberate: stable, low-latency
# Yes/No verdicts. Do NOT use "none" — LiteLLM's registry marks
# supports_none_reasoning_effort=false for every gpt-5.6 variant, so a
# LiteLLM-routed judge rejects it locally before it reaches OpenAI.
_JUDGE_GPT_REASONING_EFFORT = "low"


def _call_judge_openai(
    model: str,
    system: str,
    user: "str | JudgeUserPayload",
    *,
    family: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> tuple[str, dict]:
    import urllib.request
    key = (
        api_key
        or os.environ.get("KENSEI_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not key:
        raise RuntimeError("no OpenAI key for judge")
    # `base_url` (bare origin, no path) routes the SAME OpenAI-Chat-Completions
    # wire shape through the codex OAuth bridge's /v1/chat/completions shim
    # instead of api.openai.com; `key` is then the bridge secret, presented as
    # `Authorization: Bearer` exactly as OpenAI expects (codex_oauth/bridge.py
    # _client_authorized accepts Bearer). Default (None) = metered OpenAI direct.
    _base = (base_url or "").strip().rstrip("/")
    _codex_route = bool(_base)
    _endpoint = (
        f"{_base}/v1/chat/completions"
        if _codex_route
        else "https://api.openai.com/v1/chat/completions"
    )
    # Yes/No verdict format (judge_walkthrough_2026_05_27.html §1.1 EXACT FORMAT):
    # judge emits free-form text with `[[RATIONALE:]] [[SATISFIED:Yes|No]]` blocks
    # wrapped in `<judgment>...</judgment>`. We MUST NOT pass
    # `response_format={"type":"json_object"}` here — it forces OpenAI to wrap
    # the entire response in a JSON envelope, which breaks `_VERDICT_RE` and
    # collapses every council vote into a parse error. max_completion_tokens
    # raised 4000→8000 so 25-criterion rubrics (≈1.2k verdict tokens) leave
    # ample headroom for reasoning.
    #
    # NOTE `temperature`/`top_p` are absent BY CONSTRUCTION, not by omission:
    # gpt-5.6 returns HTTP 400 on their mere presence (any value), exactly like
    # Sonnet 5 (AGENTS.md invariant 18). Never add them here.
    #
    # `user` is a bare str on every legacy path, so `content` stays a bare string
    # and a no-image request is byte-identical to before. Only when images were
    # lifted out of the deliverables does content become a parts LIST: the text
    # first, then one `image_url` part per image. That Chat-Completions shape is
    # native on metered api.openai.com AND is translated to a Responses
    # `input_image` by the codex bridge (codex_oauth/translate.py), so this body
    # stays route-agnostic.
    #
    # The image parts themselves carry no label the model can read, so the text
    # gets ONE trailing line naming the attachments in order — otherwise the
    # judge sees N labelled placeholders and N anonymous images and has to guess
    # the mapping. It is appended to the single text part rather than interleaved
    # per image on purpose: the codex bridge's _content_to_input_parts flattens
    # all text into one input_text and appends images after it, so interleaved
    # labels would be reordered on that route.
    user_text = _payload_text(user)
    user_images = _payload_images(user)
    user_content: "str | list[dict]" = user_text
    if user_images:
        manifest = (
            "\n[Attached images, in order: "
            + ", ".join(img.label for img in user_images)
            + "]"
        )
        user_content = [{"type": "text", "text": user_text + manifest}] + [
            {"type": "image_url",
             "image_url": {"url": img.data_uri, "detail": img.detail}}
            for img in user_images
        ]
    request_body: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_content}],
        "max_completion_tokens": (
            8000 if max_completion_tokens is None else max_completion_tokens
        ),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_effort:
        if _codex_route:
            # The codex bridge's chat->responses shim (codex_oauth/translate.py
            # chat_to_responses) DROPS a flat `reasoning_effort` string and only
            # forwards a dict-valued `reasoning`. Send the dict form so the effort
            # actually reaches the Responses backend; the metered-OpenAI path below
            # keeps the flat field the Chat Completions API expects.
            request_body["reasoning"] = {"effort": reasoning_effort}
        else:
            request_body["reasoning_effort"] = reasoning_effort
    body = json.dumps(request_body).encode()
    req = urllib.request.Request(
        _endpoint, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    text_parts: list[str] = []
    u: dict = {}
    # Live-stream tap (docs/STREAMING_PLAN.md §3.3), same pattern as the
    # Bedrock judge path: emit each delta as it accumulates; no-op when
    # WCB_STREAM is off; never raises.
    from src.utils import stream_events as _stream
    import uuid as _uuid
    _sid = _uuid.uuid4().hex[:12]
    _stream.emit("judge:openai", "message_start", _sid, kind="status", model=model)
    # The codex bridge holds a turn open with 15s SSE keepalives during a
    # subscription cap-wait (up to KAIJU_CODEX_CAP_WAIT_SEC x CAP_MAX_WAITS, the
    # harness default is 60s x 10 = 10min). urlopen's timeout is a per-READ
    # socket deadline, so keepalives keep it alive, but a legitimately slow
    # codex turn needs more than the 120s the metered path uses.
    _read_timeout = timeout if timeout is not None else (600 if _codex_route else 120)
    with urllib.request.urlopen(req, timeout=_read_timeout) as r:
        for raw_line in r:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # The codex bridge signals a truncation / subscription-cap failure as
            # an SSE chunk carrying only {"error": {...}} and NO "choices"
            # (codex_oauth/translate.py chat_truncation_error_sse). If ignored,
            # the judge sees empty text and every criterion abstains for a reason
            # nothing names (the silent no-signal case AGENTS.md #18 warns about).
            # Raising here surfaces the real error to the caller's fallback.
            err = obj.get("error")
            if err:
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise RuntimeError(f"judge stream error: {msg}")
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                    _stream.emit("judge:openai", "delta", _sid, kind="text",
                                 delta=content, model=model)
            usage_obj = obj.get("usage")
            if isinstance(usage_obj, dict):
                u = usage_obj
    _stream.emit("judge:openai", "message_stop", _sid, kind="status", model=model)
    text = "".join(text_parts)
    details = u.get("prompt_tokens_details", {}) or {}
    prompt_tok = int(u.get("prompt_tokens", 0) or 0)
    comp_tok = int(u.get("completion_tokens", 0) or 0)
    cached_tok = int(details.get("cached_tokens", 0) or 0)
    input_excl = max(0, prompt_tok - cached_tok)
    if _codex_route:
        # Codex subscription route: tokens are still counted for telemetry, but
        # there is no per-token dollar cost (a flat ChatGPT subscription), so the
        # metered rate table does not apply. priced_ok stays True because $0 is
        # correct here, not a missing-rate failure.
        cost_usd, priced_ok = 0.0, True
    else:
        cost_usd, priced_ok = _judge_cost_usd(
            model, input_excl, comp_tok, cached_tok, 0, family
        )
    usage = {
        "input_tokens": input_excl,
        "output_tokens": comp_tok,
        "cache_read_tokens": cached_tok,
        "cache_write_tokens": 0,
        "total_tokens": input_excl + comp_tok + cached_tok,
        "request_count": 1,
        "cost_usd": cost_usd,
        "cost_priced_ok": priced_ok,
    }
    return text, usage


# Small valid PNG (8x8 RGB, solid colour) as an inline data URI. Used by
# preflight_judge_codex to exercise the IMAGE leg of the codex judge route: the
# ChatGPT/Codex backend's handling of image content-parts is not documented, and a
# 400/refusal there would otherwise only surface at grade time as an abstain-all
# no-signal verdict (AGENTS.md #18). Not 1x1: the backend rejects a 1x1 image as
# degenerate, which failed the probe and disabled judge image attachment for the
# whole batch even though real images are accepted.
_PROBE_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR42mNwaDiAFTEMLQkAYvNgAQlPC90AAAAASUVORK5CYII="
)

# A safety-classifier refusal on this route arrives as HTTP 200 with EMPTY
# assistant content (stop_reason "refusal") — the exact failure mode the image
# content-part path was built to avoid — so empty text is the primary signal. The
# phrase list additionally catches a verbalised refusal.
_IMAGE_PROBE_REFUSAL_MARKERS = (
    "i'm sorry", "i am sorry", "i cannot", "i can't", "i won't",
    "unable to view", "unable to see", "unable to process", "can't help with",
    "cannot help with", "won't be able",
)


def _image_probe_refusal(text: str) -> str:
    """Reason string when a probe reply looks like a refusal, else ""."""
    stripped = (text or "").strip()
    if not stripped:
        return "empty response (refusal / no content)"
    low = stripped.lower()
    for marker in _IMAGE_PROBE_REFUSAL_MARKERS:
        if marker in low:
            return f"refusal marker {marker!r} in reply: {stripped[:160]!r}"
    return ""


def preflight_judge_codex(timeout_s: float = 90.0) -> tuple[str, str]:
    """Validate the GPT judge's codex-subscription grading path END-TO-END.

    wait_for_codex_bridge_healthy only probes /healthz from INSIDE the container
    (and /healthz is not secret-gated), so it passes even when (a) Docker Desktop
    dropped the host loopback publish or (b) the ChatGPT OAuth token is dead.
    Both otherwise surface at GRADE time, after the (expensive) trajectory has
    run. This issues ONE real, minimal completion through the exact host ->
    codex-bridge -> chatgpt.com route the grader uses, so a broken subscription
    is flagged up front, then a SECOND minimal completion carrying one 8x8 PNG
    image content-part so the multimodal leg is exercised before it matters.

    Returns (ok, detail) as strings-safe tuple: ok is "ok"/"fail"/"skip".

    The `"image probe: "` PREFIX on a failure detail is a CONTRACT: it is how
    eval/run_batch.py tells an image-leg failure (degrade — disable attachment
    for the batch) from a text/auth failure (abort the batch). Do not drop it.

    A trivially short prompt is used because the bridge strips max_output_tokens
    (output length is unbounded on this route), so cost is bounded by the prompt,
    not a max-token cap; a wall-clock timeout guards latency. NEVER raises
    (AGENTS.md #12: grading path must degrade, not fail).
    """
    url = _judge_codex_bridge_url()
    if not url:
        return "skip", "not configured (GPT judge uses metered OpenAI or council, not the codex bridge)"
    secret = _judge_codex_bridge_secret()
    if not secret:
        return "fail", "codex bridge url set but WCB_CODEX_BRIDGE_SECRET is empty"
    model = _judge_codex_bridge_model()
    try:
        _call_judge_openai(
            model, "You are a preflight probe.", "Reply with the word OK.",
            family="gpt",
            api_key=secret,
            reasoning_effort=_JUDGE_GPT_REASONING_EFFORT,
            base_url=url,
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — probe must never raise into caller
        return "fail", f"{type(exc).__name__}: {str(exc)[:400]}"
    # Image leg. Skipped when attachment is disabled (KENSEI_JUDGE_MAX_IMAGES=0):
    # the grader will then never send an image part, so probing one would fail the
    # batch on a capability it does not use.
    if _judge_max_images() <= 0:
        return "ok", f"ok ({url}, model={model}, image probe skipped: attachment disabled)"
    probe_payload = JudgeUserPayload(
        text="An image is attached. Reply with the single word OK.",
        images=[ImagePart(
            data_uri=_PROBE_PNG_DATA_URI,
            mime="image/png",
            detail=_judge_image_detail(),
            label="preflight-probe#1",
        )],
    )
    try:
        img_text, _ = _call_judge_openai(
            model, "You are a preflight probe.", probe_payload,
            family="gpt",
            api_key=secret,
            reasoning_effort=_JUDGE_GPT_REASONING_EFFORT,
            base_url=url,
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — probe must never raise into caller
        return "fail", f"image probe: {type(exc).__name__}: {str(exc)[:400]}"
    refusal = _image_probe_refusal(img_text)
    if refusal:
        return "fail", f"image probe: {refusal}"
    return "ok", f"ok ({url}, model={model}, image probe ok)"


_ARN_REGION_RE = re.compile(r"^arn:aws:bedrock:([a-z0-9-]+):")

# Bedrock prompt-caching support is per-model. Anthropic Claude on Bedrock
# accepts `cachePoint` blocks; Kimi and GLM do NOT and return HTTP 403 "You
# invoked an unsupported model or your request did not allow prompt caching."
# Observed in alden-croft 2026-06-02T20:20:04Z gateway.log: 2-of-3 council
# members 403'd, quorum fell back to single-judge.
#
# TWO LAYERS, because a council member's id rotates monthly but a direct-ARN
# (non-council) judge does not:
#   Layer 1 — council members resolve by stable FAMILY (_FAMILY_CACHE_SUPPORTED),
#             so caching eligibility survives ARN rotation.
#   Layer 2 — non-council / direct-ARN paths (single-judge fallback, future
#             judges) fall back to this substring allowlist of profile IDs known
#             to map to Anthropic Claude. These are NOT council ids and are not
#             rotated. Add a new ID only after confirming the model is Anthropic.
_NON_COUNCIL_CACHE_SUPPORTED_TAILS = (
    "xv71vnlzm71s",  # Sonnet 4.6 (alternate; IAM-denied per b34 but caches when permitted)
    "96j5zamnqlci",  # Opus (.env KENSEI_BEDROCK_MODEL_ARN)
)


def _supports_prompt_caching(arn: str, family: str | None = None) -> bool:
    if family is not None:
        return _FAMILY_CACHE_SUPPORTED.get(family, False)
    return any(tail in (arn or "") for tail in _NON_COUNCIL_CACHE_SUPPORTED_TAILS)


def _bedrock_region_for(arn: str) -> str:
    m = _ARN_REGION_RE.match(arn or "")
    if m:
        return m.group(1)
    return os.environ.get("KENSEI_AWS_REGION") or os.environ.get("AWS_REGION", "ap-south-1")


def _call_judge_bedrock(
    arn: str, system: str, user: str, family: str | None = None
) -> tuple[str, dict]:
    import urllib.request, urllib.parse, urllib.error
    from src.utils.bedrock_eventstream import iter_eventstream
    tok = os.environ.get("KENSEI_AWS_BEARER_TOKEN") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not tok:
        raise RuntimeError("no Bedrock bearer token for judge")
    while arn.startswith("bedrock/"):
        arn = arn[len("bedrock/"):]
    reg = _bedrock_region_for(arn)
    mid = urllib.parse.quote(arn, safe="")
    url = f"https://bedrock-runtime.{reg}.amazonaws.com/model/{mid}/converse-stream"

    def _do_post(include_temperature: bool) -> tuple[str, dict]:
        infer = {"maxTokens": _member_max_output_tokens(arn, family)}
        if include_temperature:
            infer["temperature"] = 0
        # Bedrock prompt-caching: a `cachePoint` block marks the preceding
        # blocks as cacheable for ~5 min on Anthropic models. Kimi K2.5 and
        # GLM 5 on Bedrock return 403 "your request did not allow prompt
        # caching" if cachePoint is present (see _CACHE_SUPPORTED_PROFILE_IDS
        # above). Gate emission on per-ARN allowlist; preserve b49 caching
        # win on Sonnet/Opus without re-triggering the 2-of-3 council quorum
        # collapse observed in alden-croft 2026-06-02T20:20Z gateway.log.
        if _supports_prompt_caching(arn, family):
            system_blocks = [{"text": system}, {"cachePoint": {"type": "default"}}]
        else:
            system_blocks = [{"text": system}]
        body = json.dumps({
            "system": system_blocks,
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": infer,
        }).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                     "Accept": "application/vnd.amazon.eventstream"},
        )
        return _consume(req)

    def _consume(req) -> tuple[str, dict]:
        text_parts: list[str] = []
        u: dict = {}
        # Live-stream tap (docs/STREAMING_PLAN.md §3.3): each delta is emitted
        # to the display feed AS it is accumulated — accumulate-then-parse is
        # untouched (R4), and stream_events.emit() is a guaranteed no-raise
        # no-op unless WCB_STREAM is on.
        from src.utils import stream_events as _stream
        import uuid as _uuid
        _sid = _uuid.uuid4().hex[:12]
        _src = f"judge:{family or 'bedrock'}"
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")[:2000]
            except Exception:
                pass
            _stream.emit(_src, "error", _sid, kind="status",
                         delta=f"HTTP {e.code}", model=arn)
            raise RuntimeError(f"Bedrock HTTP {e.code} at {url}: {err_body}") from None
        _stream.emit(_src, "message_start", _sid, kind="status", model=arn)
        with resp as r:
            def _chunks():
                while True:
                    chunk = r.read(8192)
                    if not chunk:
                        return
                    yield chunk
            for evt_type, evt_payload in iter_eventstream(_chunks()):
                if not isinstance(evt_payload, dict):
                    continue
                if evt_type and evt_type.endswith("Exception"):
                    err = evt_payload.get("Message") or evt_payload.get("message") or ""
                    _stream.emit(_src, "error", _sid, kind="status",
                                 delta=str(err)[:200], model=arn)
                    raise RuntimeError(f"Bedrock judge error ({evt_type}): {err}")
                if evt_type == "contentBlockDelta":
                    delta = evt_payload.get("delta") or {}
                    txt = delta.get("text")
                    if isinstance(txt, str):
                        text_parts.append(txt)
                        _stream.emit(_src, "delta", _sid, kind="text",
                                     delta=txt, model=arn)
                elif evt_type == "metadata":
                    usage_obj = evt_payload.get("usage")
                    if isinstance(usage_obj, dict):
                        u = usage_obj
        _stream.emit(_src, "message_stop", _sid, kind="status", model=arn)
        text = "".join(text_parts)
        in_tok = int(u.get("inputTokens", 0) or 0)
        out_tok = int(u.get("outputTokens", 0) or 0)
        c_read = int(
            u.get("cacheReadInputTokens")
            or u.get("cacheReadTokens")
            or u.get("cache_read_input_tokens")
            or 0
        )
        c_write = int(
            u.get("cacheWriteInputTokens")
            or u.get("cacheCreationInputTokens")
            or u.get("cache_creation_input_tokens")
            or 0
        )
        cost_usd, priced_ok = _judge_cost_usd(arn, in_tok, out_tok, c_read, c_write, family)
        usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_tokens": c_read,
            "cache_write_tokens": c_write,
            "total_tokens": in_tok + out_tok + c_read + c_write,
            "request_count": 1,
            "cost_usd": cost_usd,
            "cost_priced_ok": priced_ok,
        }
        return text, usage

    # The sonnet council leg can be pointed (via ARN swap) at Sonnet 5, which
    # HTTP-400s on ANY explicit temperature — and an application-inference-profile
    # ARN carries no model-version substring to detect it. So the whole sonnet
    # family omits temperature on the FIRST call rather than relying on the
    # error-string retry below (Sonnet 5's 400 message need not contain the
    # matched keywords, and the first 400 already abstains the criterion). Mirrors
    # judge_litellm._judge_sampling_params. Kimi/GLM keep temperature=0.
    if family == "sonnet":
        return _do_post(include_temperature=False)
    try:
        return _do_post(include_temperature=True)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "temperature" in msg and ("deprecated" in msg or "not supported" in msg or "unsupported" in msg):
            return _do_post(include_temperature=False)
        raise


# Parser for the Yes/No verdict format mandated by _judge_system_prompt. Three
# load-bearing properties: (1) DOTALL so rationale text can span newlines,
# (2) the leading 'N.' anchor disambiguates each verdict block when judges
# emit Markdown-style bold/italic markers around the criterion sentence,
# (3) TRUNCATION_AFFECTED is OPTIONAL so a judge that omits it (older models,
# rare miss-emission) does not collapse the whole verdict count and trigger
# quorum failure. Defaults to No when absent. Match this regex's structure
# against any change to the system prompt's verdict template; deviation here
# silently zeros all judge votes — see 2026-06-02 alden-croft regression
# context referenced in _judge_system_prompt.
_VERDICT_RE = re.compile(
    r"\d+\.\s.*?"
    r"\[\[\s*RATIONALE:\s*(.*?)\s*\]\]\s*"
    r"\[\[\s*SATISFIED:\s*(Yes|No)\s*\]\]"
    r"(?:\s*\[\[\s*TRUNCATION_AFFECTED:\s*(Yes|No)\s*\]\])?",
    re.DOTALL | re.IGNORECASE,
)


def _parse_verdict_text(response: str, n_criteria: int) -> list[dict]:
    if not response:
        # Most common real cause is an upstream safety refusal (HTTP 200,
        # content:[], stop_reason "refusal"), which reads as "the judge said
        # nothing". Say so, so this is not mistaken for a rubric or prompt bug.
        raise ValueError(
            f"empty judge response (expected up to {n_criteria} verdicts) — "
            f"usually an upstream safety refusal on the grading call, not a "
            f"rubric problem; check the raw judge response for stop_reason"
        )
    # Tolerate judges that wrap the entire block in <judgment>...</judgment> or
    # emit it bare; either way the verdict items themselves are what we match.
    matches = _VERDICT_RE.findall(response)
    # Partial-coverage contract per user m1543: smaller-context judges
    # (Kimi K2.5 = 256k input, GLM 5 = 200k input) truncate output before
    # reaching all rubric items on long rubrics (Sonnet 4.6 = 1M handles 69+
    # comfortably). Renata-voss 2026-06-03 produced GLM=59 / Kimi=54 / Sonnet=69
    # for a 69-criterion rubric. Returning the partial list lets the council
    # aggregator vote per-criterion using only judges that actually covered
    # that index; abstentions never invent No-votes the model did not cast.
    if not matches:
        raise ValueError(
            f"no verdicts parsed (expected up to {n_criteria}); response shape does not match _VERDICT_RE"
        )
    out: list[dict] = []
    # Cap at n_criteria in case a judge emits stray numbered items beyond the
    # rubric (defensive — verdict list is ordered, criteria N+1.. would be junk).
    for rationale, satisfied, truncation in matches[:n_criteria]:
        out.append({
            "rationale": (rationale or "").strip(),
            "satisfied": (satisfied or "No").strip().lower() == "yes",
            "truncation_affected": (truncation or "No").strip().lower() == "yes",
        })
    return out


def _arn_from_model(model: str) -> str:
    """Strip the optional 'bedrock/' prefix so `_member_max_output_tokens` and
    `_bedrock_region_for` (which do substring matches against ARN tails) work
    identically whether the caller passed `bedrock/arn:...` or the bare ARN.
    Non-Bedrock model strings pass through unchanged."""
    m = (model or "").strip()
    if m.startswith("bedrock/"):
        return m[len("bedrock/"):]
    return m


def _judge_use_litellm() -> bool:
    """Master toggle for the LiteLLM-backed judge path. Mirrors
    `judge_litellm.judge_use_litellm()` but is duplicated here so the env check
    is a cheap module-local function call — we don't want to import the
    `judge_litellm` module on every judge call when the flag is off. Default
    OFF: the urllib direct-provider path is the production-verified default;
    flipping to LiteLLM is opt-in until soak validates the new transport."""
    v = os.environ.get("KENSEI_JUDGE_USE_LITELLM", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _call_one_judge(
    model: str, system: str, user: "str | JudgeUserPayload", family: str | None = None
) -> tuple[str, dict]:
    # Provider routing is CONTENT-AWARE, not naive partition("/"): a Bedrock
    # application-inference-profile ARN itself contains slashes, so a bare ARN
    # (missing the "bedrock/" prefix) would partition to provider != "bedrock" and
    # be silently misrouted to OpenAI, which 404s on the unknown model id. Detect
    # Bedrock by shape so an ARN never reaches OpenAI. `family` (when the caller is
    # the council) carries the stable model identity so pricing/budget/cache
    # lookups never depend on the monthly-rotating ARN profile id.
    m = (model or "").strip()
    if not m:
        raise RuntimeError("empty judge model id")

    # The gpt family is routed BEFORE the LiteLLM opt-in below, deliberately: it
    # is the only family whose credential is not the run's provider credential,
    # and this direct transport is the one that reads KENSEI_JUDGE_GPT_API_KEY.
    # Routing it through LiteLLM instead would silently bill the agent's
    # KENSEI_OPENAI_API_KEY. Existing families are unaffected (no configuration
    # produced family == "gpt" before this branch existed).
    if family == "gpt":
        gpt_model = m.partition("/")[2] if m.startswith("openai/") else m
        # When the run brought up the codex OAuth bridge, route the GPT judge
        # through it (ChatGPT subscription) instead of the metered key: same
        # OpenAI-Chat-Completions wire shape, base_url pointed at the bridge, and
        # the bridge secret carried as the Bearer api_key. Falls back to the
        # metered KENSEI_JUDGE_GPT_API_KEY path when the bridge is not configured.
        # `user` is forwarded WHOLE (payload or str) — this is the only transport
        # that can carry image parts.
        _codex_url = _judge_codex_bridge_url()
        if _codex_url:
            return _call_judge_openai(
                gpt_model, system, user,
                family=family,
                api_key=_judge_codex_bridge_secret() or None,
                reasoning_effort=_JUDGE_GPT_REASONING_EFFORT,
                max_completion_tokens=_member_max_output_tokens(gpt_model, family),
                base_url=_codex_url,
            )
        return _call_judge_openai(
            gpt_model, system, user,
            family=family,
            api_key=_judge_gpt_api_key() or None,
            reasoning_effort=_JUDGE_GPT_REASONING_EFFORT,
            max_completion_tokens=_member_max_output_tokens(gpt_model, family),
        )

    # Every non-gpt transport (Bedrock Converse, LiteLLM, OpenAI fallback) is
    # TEXT-ONLY by contract: unwrap here so a council request body is
    # byte-identical to the pre-multimodal harness even if a payload ever leaks
    # into a non-gpt member's slot.
    user = _payload_text(user)
    # LiteLLM-backed path (opt-in via KENSEI_JUDGE_USE_LITELLM). On ANY exception
    # we fall through to the urllib direct-provider path below — this is the
    # explicit user m0039 contract: "If litellm is not configured for LLM
    # council account for that as well in your plan and it should follow the
    # code at all times." A hard fallback at the dispatcher (not inside the
    # LiteLLM call) means a missing dep, a misconfigured env, a network blip,
    # or a LiteLLM-internal regression all degrade gracefully to the production
    # path without losing the verdict.
    if _judge_use_litellm():
        try:
            from . import judge_litellm  # local import: avoid import-time cost
            arn_tail = _arn_from_model(m)
            return judge_litellm.call_judge_via_litellm(
                model=m,
                system=system,
                user=user,
                max_output_tokens=_member_max_output_tokens(arn_tail, family),
                cost_fn=_judge_cost_usd,
                family=family,
            )
        except Exception as exc:  # pragma: no cover - fallback path
            # Under OAuth the urllib fallback below would dial Bedrock — the
            # provider the operator explicitly opted out of. Besides the
            # billing question, it destroys the diagnostic: a broken bridge
            # surfaces as "Bedrock HTTP 403 / API Key is valid?" pointing at a
            # credential the run never intended to use, and every criterion
            # then abstains (rubric-zero) for a reason nothing in the output
            # names. Re-raise so the real error is what the operator sees.
            # Same reasoning as council_members()'s provider filter above.
            if auth_provider.resolve_provider() == auth_provider.OAUTH:
                logger.error(
                    "Judge LiteLLM path failed for %s under the OAuth provider: %s "
                    "— NOT falling back to Bedrock (opted out). Check the cc-bridge "
                    "at KENSEI_JUDGE_OAUTH_BRIDGE_URL.",
                    _short_judge_label(m), str(exc)[:200],
                )
                raise
            logger.warning(
                "Judge LiteLLM path failed for %s: %s — falling back to direct urllib path",
                _short_judge_label(m), str(exc)[:200],
            )
            # fall through to urllib routing below

    head = m.partition("/")[0]
    if head == "bedrock":
        return _call_judge_bedrock(m.partition("/")[2], system, user, family)
    if head == "openai":
        return _call_judge_openai(m.partition("/")[2] or m, system, user)
    if m.startswith("arn:aws:bedrock:") or ":application-inference-profile/" in m:
        # Bare (unprefixed) Bedrock ARN — route to Bedrock instead of OpenAI.
        return _call_judge_bedrock(m, system, user, family)
    # Unknown/unprefixed id: treat as an OpenAI model name (e.g. "gpt-5.5").
    return _call_judge_openai(m, system, user)


def _run_council(
    members: list[CouncilMember],
    system: str,
    user_for_member: "dict[str, str | JudgeUserPayload] | str | JudgeUserPayload",
    n_criteria: int,
) -> list[dict]:
    """Run every member judge in parallel and return one result dict per member:
    {model, family, ok, verdicts?, usage, error?, user_chars, raw_response?}.
    Never raises.

    `user_for_member` may be a single shared string (legacy) or a
    {model: user_prompt} dict so each member receives a payload sized to its
    own context window. A value may be a `JudgeUserPayload` (text + images) for
    the gpt member; every other family is unwrapped to text in _call_one_judge.
    `n_criteria` is the expected verdict count; parses failing this count return
    `ok=False, error='parse: ...'` rather than raise. Every result carries the
    member's stable `family` so downstream per-member dicts key by family, not by
    the monthly-rotating ARN profile id."""
    from concurrent.futures import ThreadPoolExecutor

    def _resolve_user(model: str) -> "str | JudgeUserPayload":
        if isinstance(user_for_member, dict):
            return user_for_member.get(model, "")
        return user_for_member

    def _one(member: CouncilMember) -> dict:
        import time as _time
        model = member.model
        family = member.family
        effective_model = _effective_judge_model(model, family)
        user = _resolve_user(model)
        user_chars = len(_payload_text(user))
        label = _short_judge_label(model)
        # Per-member API call telemetry: pre-call line lets operators see WHICH
        # member is being dispatched WHAT payload before any network I/O; the
        # post-call ok/fail line below carries timing + tokens + cache deltas
        # so a single grep over `Judge call` reproduces the full council
        # fan-out from run.sh logs alone. Mirrors run_batch.py:707
        # `Rubric judged:` summary line one level up.
        logger.info(
            "Judge call start: model=%s family=%s user_chars=%d system_chars=%d",
            label, family, user_chars, len(system),
        )
        t0 = _time.monotonic()
        try:
            raw, usage = _call_one_judge(model, system, user, family)
        except Exception as exc:
            elapsed = _time.monotonic() - t0
            logger.warning(
                "Judge call fail: model=%s family=%s elapsed=%.2fs stage=call error=%s",
                label, family, elapsed, str(exc)[:200],
            )
            return {
                "model": model, "effective_model": effective_model,
                "family": family, "ok": False,
                "error": f"call: {exc}",
                "usage": {**_ZERO_USAGE, "error": f"call: {exc}"},
                "user_chars": user_chars,
            }
        elapsed = _time.monotonic() - t0
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        c_read = int(usage.get("cache_read_tokens", 0) or 0)
        c_write = int(usage.get("cache_write_tokens", 0) or 0)
        # Full raw judge response, verbatim — DEBUG so it lands in harness_debug.log
        # (file) without flooding the console. This is the exact text the verdict
        # parser sees; pair it with the "parsed verdicts" line below to debug any
        # grading discrepancy.
        logger.debug(
            "Judge raw response: model=%s family=%s chars=%d\n"
            "<<<JUDGE_RAW model=%s family=%s>>>\n%s\n<<<END_JUDGE_RAW>>>",
            label, family, len(raw or "") if isinstance(raw, str) else -1,
            label, family, raw,
        )
        try:
            verdicts = _parse_verdict_text(raw, n_criteria)
        except Exception as exc:
            logger.warning(
                "Judge call fail: model=%s family=%s elapsed=%.2fs stage=parse "
                "tokens=in:%d/out:%d/cR:%d/cW:%d error=%s",
                label, family, elapsed, in_tok, out_tok, c_read, c_write, str(exc)[:200],
            )
            logger.debug(
                "Judge parse FAILED — raw response that could not be parsed "
                "(model=%s family=%s):\n<<<JUDGE_RAW_UNPARSED>>>\n%s\n<<<END>>>",
                label, family, raw,
            )
            return {
                "model": model, "effective_model": effective_model,
                "family": family, "ok": False,
                "error": f"parse: {exc}", "usage": usage,
                "user_chars": user_chars,
                "raw_response": raw[:2000] if isinstance(raw, str) else "",
            }
        logger.info(
            "Judge call ok: model=%s family=%s elapsed=%.2fs "
            "tokens=in:%d/out:%d/cR:%d/cW:%d verdicts=%d/%d",
            label, family, elapsed, in_tok, out_tok, c_read, c_write,
            len(verdicts), n_criteria,
        )
        # Per-criterion parsed verdicts (DEBUG -> harness_debug.log). Shows exactly
        # what the parser extracted from the raw response above, so a "why is this
        # criterion Yes/No" question is answerable straight from the log.
        try:
            logger.debug(
                "Judge parsed verdicts: model=%s family=%s -> %s",
                label, family,
                [
                    (str(v.get("criterion") or v.get("id") or idx)[:60],
                     v.get("verdict") if isinstance(v, dict) else v)
                    for idx, v in enumerate(verdicts)
                ] if isinstance(verdicts, list) else verdicts,
            )
        except Exception:
            logger.debug("Judge parsed verdicts (raw): model=%s -> %r", label, verdicts)
        return {
            "model": model, "effective_model": effective_model,
            "family": family, "ok": True,
            "verdicts": verdicts, "usage": usage,
            "user_chars": user_chars,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        return list(pool.map(_one, members))


def _stddev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def _criterion_pass(score: float, weight: float) -> bool:
    triggered = score >= 0.5
    return (not triggered) if weight < 0 else triggered


def _criterion_pass_from_satisfied(satisfied: bool, weight: float) -> bool:
    # Walkthrough §2 polarity rule: SATISFIED reflects the literal criterion
    # text, NOT the point sign. Aggregator applies sign here.
    #  positive weight + satisfied=True  → passed (desired thing done)
    #  positive weight + satisfied=False → failed
    #  negative weight + satisfied=False → passed (guardrail held)
    #  negative weight + satisfied=True  → failed (forbidden behavior occurred)
    return (not satisfied) if weight < 0 else satisfied


def _short_judge_label(model: str) -> str:
    """Shorten 'bedrock/arn:aws:bedrock:<region>:<acct>:application-inference-profile/<id>'
    to '<id>' for compact per-criterion arrays in score.json; preserves the
    'openai/<model>' shape verbatim."""
    if model.startswith("bedrock/"):
        return model.rsplit("/", 1)[-1]
    return model


def _effective_judge_model(model: str, family: str) -> str:
    """Model actually hit on the wire, for score.json display.

    A member's configured Bedrock ARN is only a stable label: when the sonnet
    member routes through the Claude Max OAuth bridge, judge_litellm overrides
    the request model to the anthropic model and pops the Bedrock region, so
    the endpoint is anthropic — not Bedrock. Returns that effective anthropic
    id (bare, no 'anthropic/' prefix); otherwise the raw member id."""
    if family == "sonnet":
        try:
            from . import judge_litellm  # local import: avoid import-time cost
            if judge_litellm._judge_oauth_bridge_url():
                eff = judge_litellm._judge_oauth_bridge_model()
                return eff.split("/", 1)[-1] if eff.startswith("anthropic/") else eff
        except Exception:
            pass
    return model


def _grade_council(
    rubrics: list,
    system: str,
    user_for_member: "dict[str, str | JudgeUserPayload] | str | JudgeUserPayload",
    members: list[CouncilMember],
) -> dict:
    """Council aggregation — UNANIMOUS, else SONNET source-of-truth tiebreak.

    Single-judge mode was removed (m1609): the council is the only grading path.
    Per-criterion the verdict is resolved in this order:
      * Unanimous — every member voted AND all agree on SATISFIED → use that
        verdict (Pass/Fail after polarity). `resolved_by="unanimous"`.
      * Otherwise, if the Sonnet member emitted a verdict for this criterion →
        Sonnet's verdict governs. This covers BOTH a genuine Yes/No split and
        partial coverage where a smaller-context member (Kimi/GLM) truncated
        before reaching this index. `resolved_by="sonnet"`.
      * Otherwise — no unanimity AND Sonnet itself cast no verdict (Sonnet failed
        or, rarely, truncated) → "Human Evaluation": index added to
        abstention_flags, counted in criteria_abstained, contributes 0 to the
        numerator. `resolved_by="human_eval"`, `human_eval="required"`.

    A satisfied positive criterion contributes its weight to the numerator; a
    satisfied negative criterion (forbidden behavior occurred) subtracts |weight|.
    The denominator is the sum of positive weights only.

    Always returns a scores dict; on total council failure (zero surviving members
    and therefore no Sonnet verdict) every criterion abstains and overall_score is
    0.0. No single-judge fallback exists. If the roster has no sonnet-family member,
    the tiebreak is unavailable and non-unanimous criteria abstain as before."""
    results = _run_council(members, system, user_for_member, len(rubrics))
    surviving = [r for r in results if r.get("ok") and isinstance(r.get("verdicts"), list)]
    if len(surviving) < len(members):
        failed_summary = "; ".join(
            f"{_short_judge_label(r.get('model', '?'))}={(r.get('error') or 'unknown').strip()[:160]}"
            for r in results if not r.get("ok")
        ) or "(none — all members responded)"
        # Not fatal under unanimous rule: any non-surviving member just means the
        # remaining survivors must all agree for a determinate verdict. With zero
        # survivors every criterion abstains and overall_score is 0.0.
        logger.warning(
            "Judge council partial: %d/%d members succeeded; failed: %s; "
            "criteria without full coverage will require Human Evaluation",
            len(surviving), len(members), failed_summary,
        )

    verdicts_per_member: list[list[dict]] = [r["verdicts"] for r in surviving]
    n_members = len(members)
    # survivor_lookup maps a member's (rotating) ARN → its parsed verdict list.
    # Hoisted out of the per-criterion loop below (it was rebuilt once per rubric
    # item; the surviving set is constant for the whole aggregation).
    survivor_lookup = {r["model"]: vs for r, vs in zip(surviving, verdicts_per_member)}
    # Verbatim member error keyed like survivor_lookup — surfaced as-is in the
    # per-member rationale when a member cast no verdict (no custom wording).
    error_lookup = {r["model"]: str(r.get("error") or "") for r in results if not r.get("ok")}
    # Sonnet is the tiebreaker / source of truth on any non-unanimous criterion:
    # it is the largest-context (1M) and most capable council member. Located by
    # stable FAMILY, never the rotating ARN (see the FAMILY decoupling block).
    # When the roster has no sonnet member (a custom JUDGE_COUNCIL_MEMBERS roster),
    # there is no tiebreaker and non-unanimous criteria fall back to Human
    # Evaluation as before.
    sonnet_idx = next((j for j, m in enumerate(members) if m.family == "sonnet"), None)
    if sonnet_idx is None:
        logger.warning(
            "Judge council has no 'sonnet' member; non-unanimous criteria will "
            "fall back to Human Evaluation (no source-of-truth tiebreaker)."
        )

    crit_out: list[dict] = []
    truncation_flags: list[int] = []
    abstention_flags: list[int] = []
    weighted = 0.0
    passed = 0
    # Denominator is the sum of POSITIVE weights only — walkthrough §4 verbatim:
    # 'Always use sum(positive_points). Do NOT use sum(all_points) — that
    # overshoots 1 whenever penalties exist.' Mirrors test_executor._compute_reward
    # pos_total. See alden-croft 2026-06-02 (23/23 passed → overall 0.4986 was
    # bug; positive-only denom gives 0.983 correctly).
    total_w = sum(_extract_weight(r) for r in rubrics
                  if isinstance(r, dict) and _extract_weight(r) > 0) or 1.0

    for i, r in enumerate(rubrics):
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        # Per-criterion resolution (Sonnet source-of-truth tiebreak): a criterion
        # is determined by unanimous council agreement when every member voted and
        # agreed; otherwise Sonnet's verdict governs (genuine split OR a smaller
        # member truncating before this index); only when Sonnet itself cast no
        # verdict does the criterion route to Human Evaluation.
        per_satisfied: list[bool] = []
        per_rationale: list[str] = []
        per_truncation: list[bool] = []
        per_label: list[str] = [m.family for m in members]
        per_voted: list[bool] = []

        # Build per-member vote state aligned to the full `members` list so a
        # member that failed entirely (not in `surviving`) shows up as Abstain
        # in the votes string, matching the truncated-mid-rubric semantics.
        for m in members:
            vs = survivor_lookup.get(m.model)
            if vs is None:
                per_voted.append(False)
                per_satisfied.append(False)
                per_rationale.append(error_lookup.get(m.model, ""))
                per_truncation.append(False)
                continue
            if i < len(vs):
                v = vs[i]
                per_voted.append(True)
                per_satisfied.append(bool(v.get("satisfied", False)))
                per_rationale.append(str(v.get("rationale", "") or ""))
                per_truncation.append(bool(v.get("truncation_affected", False)))
            else:
                per_voted.append(False)
                per_satisfied.append(False)
                per_rationale.append("(abstained — output truncated before this criterion)")
                per_truncation.append(False)

        voters = sum(1 for vd in per_voted if vd)
        full_coverage = (voters == n_members)
        if full_coverage:
            yes_votes = sum(1 for s in per_satisfied if s)
            unanimous_yes = (yes_votes == n_members)
            unanimous_no = (yes_votes == 0)
        else:
            unanimous_yes = False
            unanimous_no = False

        sonnet_voted = sonnet_idx is not None and per_voted[sonnet_idx]

        # Resolution policy (Sonnet source-of-truth tiebreak):
        #   1. Unanimous — all members voted AND agree → use that verdict.
        #   2. Otherwise, if Sonnet emitted a verdict → Sonnet IS the verdict.
        #      This covers BOTH a genuine Yes/No split AND partial coverage where
        #      a smaller-context member (Kimi/GLM) truncated before this index.
        #   3. Otherwise (no unanimity AND Sonnet itself cast no verdict) →
        #      Human Evaluation (abstention): no source of truth exists.
        if full_coverage and (unanimous_yes or unanimous_no):
            verdict_satisfied = unanimous_yes
            resolved_by = "unanimous"
            human_eval = ""
        elif sonnet_voted:
            verdict_satisfied = bool(per_satisfied[sonnet_idx])
            resolved_by = "sonnet"
            human_eval = ""
        else:
            abstention_flags.append(i)
            verdict_satisfied = False
            resolved_by = "human_eval"
            human_eval = "required"

        # Truncation-abstain (ajax_moreno 2026-09-05): a No verdict the judge
        # itself flagged as truncation-affected means the evidence pipeline —
        # not the agent — withheld what was needed, so recording a confident
        # fail destroys score signal (0.407 run shipped as 0.03). Route those
        # to Human Evaluation. Yes verdicts stand: judge_system.md requires
        # positive visible evidence for a Yes, so absence cannot produce one.
        # WCB_TRUNCATION_ABSTAIN=0 restores fail-on-truncation.
        if (
            _truncation_abstain_enabled()
            and human_eval == ""
            and not verdict_satisfied
        ):
            if resolved_by == "sonnet":
                flagged = per_truncation[sonnet_idx]
            else:
                flagged = any(
                    t for t, vd in zip(per_truncation, per_voted) if vd
                )
            if flagged:
                abstention_flags.append(i)
                verdict_satisfied = False
                resolved_by = "human_eval"
                human_eval = "required"

        if resolved_by == "human_eval":
            criterion_passed = False
        else:
            criterion_passed = _criterion_pass_from_satisfied(verdict_satisfied, wt)
            if criterion_passed:
                passed += 1
            # Reward (walkthrough §4): a positive-weight criterion contributes its
            # weight only when the resolved verdict is satisfied; a negative-weight
            # criterion that is satisfied (forbidden behavior occurred) subtracts
            # its |weight|. No fractions. b51 leak impossible.
            if verdict_satisfied:
                weighted += wt

        if any(per_truncation):
            truncation_flags.append(i)
        crit_out.append({
            "id": i,
            "weight": wt,
            "satisfied": verdict_satisfied,
            "passed": criterion_passed,
            "resolved_by": resolved_by,
            "human_eval": human_eval,
            "voters": voters,
            "criterion": (r.get("criterion") if isinstance(r, dict) else str(r)),
            "votes": "/".join(
                ("Yes" if s else "No") if vd else "Abstain"
                for s, vd in zip(per_satisfied, per_voted)
            ),
            "satisfied_by_judge": per_satisfied,
            "voted_by_judge": per_voted,
            "rationales_by_judge": per_rationale,
            "truncation_affected_by_judge": per_truncation,
            "judges": per_label,
            "is_positive": wt >= 0,
        })

    # User formula (verbatim, no clamp):
    #   final_reward = (Σ passed_positive_w − Σ |triggered_negative_w|) / Σ positive_w
    # Negative-weight violation checkers must be able to pull the reward below
    # zero; clamping here silently erases their signal.
    overall = weighted / total_w
    council_usage = _ZERO_USAGE.copy()
    # Per-member usage breakdown: the flat sum below collapses all members into
    # one cost line, hiding which model spent what. Preserve each member's own
    # tokens/cost keyed by stable FAMILY ('sonnet'/'glm'/'kimi'), NOT the monthly-
    # rotating ARN profile id — so a tool reading sources.judge.per_member finds
    # the same keys before and after an ARN rotation. Rides on council_usage as a
    # non-numeric passthrough, so recompute_combined leaves it alone and the
    # total=in+out+cR+cW invariant is unaffected.
    per_member: dict[str, dict] = {}
    for r in results:
        u = r.get("usage") or {}
        for k in council_usage.keys():
            if k == "cost_usd":
                council_usage[k] = float(council_usage.get(k, 0.0)) + float(u.get(k, 0.0) or 0.0)
            else:
                council_usage[k] = int(council_usage.get(k, 0)) + int(u.get(k, 0) or 0)
        in_tok = int(u.get("input_tokens", 0) or 0)
        out_tok = int(u.get("output_tokens", 0) or 0)
        cr_tok = int(u.get("cache_read_tokens", 0) or 0)
        cw_tok = int(u.get("cache_write_tokens", 0) or 0)
        # per_member.model must match judge_council.members/surviving (the
        # OAuth-bridge effective label, not the rotating Bedrock ARN).
        _member_model = r.get("effective_model") or r.get("model", "")
        member_entry: dict = {
            "model": _member_model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_tokens": cr_tok,
            "cache_write_tokens": cw_tok,
            "total_tokens": in_tok + out_tok + cr_tok + cw_tok,
            "request_count": int(u.get("request_count", 0) or 0),
            "cost_usd": float(u.get("cost_usd", 0.0) or 0.0),
            "ok": bool(r.get("ok")),
        }
        if "cost_priced_ok" in u:
            member_entry["cost_priced_ok"] = bool(u.get("cost_priced_ok"))
        if r.get("error"):
            member_entry["error"] = str(r.get("error"))[:200]
        # Key by family (unique per council: one sonnet/glm/kimi each); fall
        # back to the full model string only if family is somehow absent.
        key = r.get("family") or str(r.get("model", "") or "")
        if key in per_member:
            key = str(r.get("model", "") or key)
        per_member[key] = member_entry
    council_usage["total_tokens"] = (
        council_usage["input_tokens"] + council_usage["output_tokens"]
        + council_usage["cache_read_tokens"] + council_usage["cache_write_tokens"]
    )
    council_usage["per_member"] = per_member

    headroom_per_member: dict[str, dict] = {}
    headroom_tokens_saved_total = 0
    headroom_enabled = False
    for r in results:
        h = (r.get("usage") or {}).get("headroom") or {}
        if not isinstance(h, dict) or not h:
            continue
        headroom_enabled = True
        headroom_per_member[r.get("family") or _short_judge_label(r.get("model", ""))] = h
        headroom_tokens_saved_total += int(h.get("tokens_saved", 0) or 0)

    n = len(rubrics)
    n_abstained = len(abstention_flags)
    failed = n - passed - n_abstained
    # Schema contract — score.json scores RUBRIC CRITERIA, not pytest tests.
    # Canonical keys: criteria_total/_passed/_failed/_abstained and
    # rubric_weights_percentage (= overall_score * 100, per user formula m1420).
    # criteria_total = passed + failed + abstained (b82 invariant). The deprecated
    # tests_* aliases were dropped here; the harbor pytest channel (test_result /
    # SQLite store / ctrf.json) derives its tests_* counts from criteria_* via the
    # tr_meta adapter at eval/run_batch.py:962-968, which already falls back to
    # criteria_* when no real pytest ran. See NOMENCLATURE.md for the channel boundary.
    return {
        "overall_score": round(overall, 4),
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": n,
        "criteria_passed": passed,
        "criteria_failed": failed,
        "criteria_abstained": n_abstained,
        "criteria": crit_out,
        "judge_model": "council",
        "judge_council": {
            "members": [r.get("effective_model", r["model"]) for r in results],
            "surviving": [r.get("effective_model", r["model"]) for r in surviving],
            "failed": [
                {"model": r.get("effective_model", r["model"]), "error": r.get("error", "")}
                for r in results if not r.get("ok")
            ],
            "aggregation": "unanimous_or_sonnet_tiebreak",
            "per_member_user_chars": {
                r["family"]: int(r.get("user_chars", 0) or 0) for r in results
            },
            "per_member_verdict_count": {
                r["family"]: len(r["verdicts"]) for r in surviving
            },
            "headroom_enabled": headroom_enabled,
            "headroom_tokens_saved_total": headroom_tokens_saved_total,
            "headroom_per_member": headroom_per_member,
        },
        "truncation_flags": truncation_flags,
        "abstention_flags": abstention_flags,
        "usage": council_usage,
    }


_DEFAULT_RUBRIC_BATCH_SIZE = 40


def _rubric_batch_size() -> int:
    # Max criteria per judge call. Chosen so a full verdict list fits under the
    # smallest family output cap (8192 tokens). Guarded: a non-int or <=0 value
    # falls back to the default (never 0 -> infinite loop, never raises).
    raw = os.environ.get("WCB_JUDGE_RUBRIC_BATCH_SIZE", "").strip()
    if not raw:
        return _DEFAULT_RUBRIC_BATCH_SIZE
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RUBRIC_BATCH_SIZE
    return n if n > 0 else _DEFAULT_RUBRIC_BATCH_SIZE


def _synthetic_abstain_criterion(global_id: int, rubric, members: list) -> dict:
    # Faithful crit_out shape (see _grade_council) for a criterion in a chunk
    # whose council call errored: abstain, contribute 0 to the numerator, keep
    # the positive weight in the denominator so the score is honestly depressed.
    # `judges` MUST mirror _grade_council's [m.family for m in members] (NOT [])
    # so consumers zipping judges with the per-judge arrays stay aligned.
    n_members = len(members)
    wt = _extract_weight(rubric) if isinstance(rubric, dict) else 1.0
    return {
        "id": global_id,
        "weight": wt,
        "satisfied": False,
        "passed": False,
        "resolved_by": "human_eval",
        "human_eval": "required",
        "voters": 0,
        "criterion": (rubric.get("criterion") if isinstance(rubric, dict) else str(rubric)),
        "votes": "/".join(["Abstain"] * n_members),
        "satisfied_by_judge": [False] * n_members,
        "voted_by_judge": [False] * n_members,
        "rationales_by_judge": ["(chunk grading failed)"] * n_members,
        "truncation_affected_by_judge": [False] * n_members,
        "judges": [m.family for m in members],
        "is_positive": wt >= 0,
    }


def _merge_batched_grades(rubrics: list, members: list, chunk_results: list) -> dict:
    # Merge per-chunk _grade_council outputs into one aggregate for the full
    # rubric (Oracle design ses_f992b8cb9): remap positional ids by a running
    # offset; RECOMPUTE numerator from merged `satisfied`; denominator from the
    # ORIGINAL full rubrics (never criteria[].weight — string-rubric + `or 1.0`
    # non-additivity trap); re-derive counts; sum usage but recompute total_tokens;
    # surviving=intersection, failed=union, per_member_user_chars=max. A failed
    # chunk degrades to synthetic abstains; top-level error ONLY if ALL failed.
    n_members = len(members)
    merged_criteria: list[dict] = []
    abstention_flags: list[int] = []
    truncation_flags: list[int] = []
    offset = 0
    all_failed = True
    surviving_sets: list[set] = []
    failed_by_model: dict[str, str] = {}
    per_member_agg: dict[str, dict] = {}
    per_member_user_chars: dict[str, int] = {}
    per_member_verdict_count: dict[str, int] = {}
    headroom_per_member: dict[str, dict] = {}
    headroom_saved = 0
    headroom_enabled = False
    usage_sum = _ZERO_USAGE.copy()

    for chunk, res in chunk_results:
        if res.get("error"):
            for j, rb in enumerate(chunk):
                gid = offset + j
                merged_criteria.append(_synthetic_abstain_criterion(gid, rb, members))
                abstention_flags.append(gid)
            offset += len(chunk)
            continue
        all_failed = False
        for c in res.get("criteria", []):
            gid = offset + int(c.get("id", 0))
            merged_criteria.append({**c, "id": gid})
        for idx in res.get("abstention_flags", []):
            abstention_flags.append(offset + int(idx))
        for idx in res.get("truncation_flags", []):
            truncation_flags.append(offset + int(idx))
        council = res.get("judge_council", {}) or {}
        surviving_sets.append(set(council.get("surviving", []) or []))
        for f in council.get("failed", []) or []:
            failed_by_model.setdefault(f.get("model", ""), str(f.get("error", ""))[:200])
        for fam, cnt in (council.get("per_member_verdict_count", {}) or {}).items():
            per_member_verdict_count[fam] = per_member_verdict_count.get(fam, 0) + int(cnt or 0)
        for fam, chars in (council.get("per_member_user_chars", {}) or {}).items():
            per_member_user_chars[fam] = max(per_member_user_chars.get(fam, 0), int(chars or 0))
        if council.get("headroom_enabled"):
            headroom_enabled = True
            headroom_saved += int(council.get("headroom_tokens_saved_total", 0) or 0)
            for fam, h in (council.get("headroom_per_member", {}) or {}).items():
                if h:
                    headroom_per_member[fam] = h
        u = res.get("usage", {}) or {}
        for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_write_tokens", "request_count", "cost_usd"):
            usage_sum[k] = usage_sum.get(k, 0) + (u.get(k, 0) or 0)
        for fam, pm in (u.get("per_member", {}) or {}).items():
            agg = per_member_agg.setdefault(fam, {
                "model": pm.get("model", ""), "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": 0,
                "request_count": 0, "cost_usd": 0.0, "ok": True,
            })
            for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                      "cache_write_tokens", "request_count", "cost_usd"):
                agg[k] = agg.get(k, 0) + (pm.get(k, 0) or 0)
            agg["ok"] = bool(agg["ok"]) and bool(pm.get("ok"))
            if "cost_priced_ok" in pm:
                agg["cost_priced_ok"] = bool(agg.get("cost_priced_ok", True)) and bool(pm.get("cost_priced_ok"))
            if pm.get("error") and "error" not in agg:
                agg["error"] = str(pm.get("error"))[:200]
        offset += len(chunk)

    for fam, agg in per_member_agg.items():
        agg["total_tokens"] = (agg["input_tokens"] + agg["output_tokens"]
                               + agg["cache_read_tokens"] + agg["cache_write_tokens"])
    usage_sum["total_tokens"] = (usage_sum["input_tokens"] + usage_sum["output_tokens"]
                                 + usage_sum["cache_read_tokens"] + usage_sum["cache_write_tokens"])
    usage_sum["per_member"] = per_member_agg

    total_w = sum(_extract_weight(r) for r in rubrics
                  if isinstance(r, dict) and _extract_weight(r) > 0) or 1.0
    weighted = sum(c["weight"] for c in merged_criteria if c.get("satisfied") is True)
    overall = weighted / total_w
    n = len(rubrics)
    passed = sum(1 for c in merged_criteria if c.get("passed") is True)
    n_abstained = len(abstention_flags)
    failed = n - passed - n_abstained

    first_council = next((r.get("judge_council", {}) for _c, r in chunk_results
                          if not r.get("error")), {}) or {}
    surviving = sorted(set.intersection(*surviving_sets)) if surviving_sets else []
    merged: dict = {
        "overall_score": round(overall, 4),
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": n,
        "criteria_passed": passed,
        "criteria_failed": failed,
        "criteria_abstained": n_abstained,
        "criteria": merged_criteria,
        "judge_model": "council",
        "judge_council": {
            "members": first_council.get("members", []),
            "surviving": surviving,
            "failed": [{"model": m, "error": e} for m, e in failed_by_model.items()],
            "aggregation": first_council.get("aggregation", "unanimous_or_sonnet_tiebreak"),
            "per_member_user_chars": per_member_user_chars,
            "per_member_verdict_count": per_member_verdict_count,
            "headroom_enabled": headroom_enabled,
            "headroom_tokens_saved_total": headroom_saved,
            "headroom_per_member": headroom_per_member,
            "rubric_batch_size": _rubric_batch_size(),
            "rubric_batches": len(chunk_results),
        },
        "truncation_flags": sorted(truncation_flags),
        "abstention_flags": sorted(abstention_flags),
        "usage": usage_sum,
    }
    if all_failed:
        merged["error"] = "all rubric batches failed to grade"
    return merged


_JUDGE_GPT_PRIMARY_OFF = ("0", "false", "no", "off")


def _gpt_judge_configured() -> bool:
    """True when the GPT-5.6 primary judge should grade ahead of the council.

    Two ways to be configured: (a) the metered path needs BOTH the dedicated key
    and the model id; (b) the codex-subscription path needs the published bridge
    url plus its secret (no metered key required — the ChatGPT subscription is the
    credential). `JUDGE_GPT_PRIMARY=0` (or false/no/off) is the operational kill
    switch that forces council-only even when configured. Unset JUDGE_GPT_PRIMARY
    = enabled, so an operator who supplies either credential set gets the GPT
    judge by default and an empty GPT config is byte-for-byte today's council-only
    behavior.
    """
    metered = bool(_judge_gpt_api_key() and _judge_gpt_model())
    codex = bool(_judge_codex_bridge_url() and _judge_codex_bridge_secret())
    if not (metered or codex):
        return False
    return (os.environ.get("JUDGE_GPT_PRIMARY") or "").strip().lower() \
        not in _JUDGE_GPT_PRIMARY_OFF


def _grade_is_signal(result: object) -> bool:
    """True when *result* is a real verdict rather than the no-signal sentinel.

    Deliberately structural, NOT numeric: `overall_score == 0.0` is ambiguous
    (a genuine all-fail rubric scores 0.0 and MUST be honored), so the test is
    the presence of an `error` key plus a non-empty per-criterion list.
    """
    if not isinstance(result, dict):
        return False
    if "error" in result:
        return False
    criteria = result.get("criteria")
    return isinstance(criteria, list) and len(criteria) > 0


def _grade_gpt_primary(
    rubrics: list,
    task_description: str,
    workspace_results: Path,
    transcript_text: str,
    system: str,
) -> dict:
    """Grade `rubrics` with a SINGLE gpt-5.6 judge, reusing the council machinery.

    A one-member roster makes the aggregator degenerate in a well-defined way:
    `full_coverage` holds exactly when that member voted, so every criterion is
    "unanimous" and no Sonnet tiebreak is ever consulted. If the member fails or
    truncates, its criteria abstain — which is a NO-SIGNAL result, not a real
    0.0, so it is converted to an explicit error dict for the caller's fallback.

    NEVER raises (AGENTS.md invariant 12: grading must degrade, not fail).
    """
    # Codex-first when the bridge is active so this resolves the SAME model id
    # preflight_judge_codex probes (_judge_codex_bridge_model, default gpt-5.6-sol)
    # — otherwise a bare metered id like "gpt-5.6" would be graded on the codex
    # route, which the codex backend rejects (bridge only strips date suffixes,
    # it does not map gpt-5.6 -> gpt-5.6-sol), diverging a green preflight from a
    # dead grade. _judge_codex_bridge_model already falls back to _judge_gpt_model,
    # so the metered-only path is unchanged.
    model = (
        _judge_codex_bridge_model() if _judge_codex_bridge_url()
        else _judge_gpt_model()
    )
    try:
        roster = [CouncilMember(family="gpt", model=model)]  # type: ignore[arg-type]
        validate_judge_pricing(roster)
        budget = _member_evidence_budget(model, "gpt")
        # Same ranking as the council path: files the rubric names go first so
        # they survive this member's evidence budget.
        evidence = _gather_evidence(
            workspace_results, transcript_text, budget=budget,
            rubric_names=_rubric_file_names(rubrics),
        )

        def _grade_chunk(chunk: list) -> dict:
            user_for_member = {
                model: _judge_user_prompt(task_description, chunk, evidence)
            }
            return _grade_council(chunk, system, user_for_member, roster)

        batch_size = _rubric_batch_size()
        if len(rubrics) <= batch_size:
            result = _grade_chunk(rubrics)
        else:
            chunk_results = [
                (chunk, _grade_chunk(chunk))
                for chunk in (
                    rubrics[start:start + batch_size]
                    for start in range(0, len(rubrics), batch_size)
                )
            ]
            result = _merge_batched_grades(rubrics, roster, chunk_results)
    except Exception as exc:  # noqa: BLE001 — grading must never raise
        logger.error(
            "[grading] GPT primary judge (%s) raised: %s", model or "<unset>", exc
        )
        return {
            "overall_score": 0.0,
            "error": f"gpt primary judge failed: {exc}",
            "usage": dict(_ZERO_USAGE),
        }

    total = int(result.get("criteria_total", 0) or 0)
    if total and int(result.get("criteria_abstained", 0) or 0) >= total:
        return {
            "overall_score": 0.0,
            "error": (
                f"gpt primary judge ({model}) cast no verdicts "
                f"({total}/{total} criteria abstained)"
            ),
            "usage": result.get("usage") or dict(_ZERO_USAGE),
        }

    result["judge_model"] = model
    council = result.get("judge_council")
    if isinstance(council, dict):
        council["aggregation"] = "gpt_primary_single_judge"
    return result


def grade_with_rubric(
    rubrics: list,
    task_description: str,
    workspace_results: Path,
    transcript_text: str = "",
    judge_model: str | None = None,
    use_council: bool | None = None,
) -> dict:
    """Score `rubrics` with the GPT-5.6 primary judge, else the LLM judge COUNCIL.

    Legacy single-judge mode was removed (m1609); the council remained the only
    path until the gated GPT primary judge landed. When
    KENSEI_JUDGE_GPT_API_KEY + KENSEI_JUDGE_GPT_MODEL are both set (and
    JUDGE_GPT_PRIMARY is not switched off) a single gpt-5.6 judge grades first
    and the council is the fallback for a no-signal result; with an empty GPT
    config the council path below runs verbatim. The `judge_model` and
    `use_council` parameters are retained for backward call-site compatibility
    but are ignored.

    Returns a scores dict:
    {overall_score, rubric_weights_percentage,
     criteria_total, criteria_passed, criteria_failed, criteria_abstained,
     criteria:[...], judge_model:'council'|<gpt model id>, judge_council:{...},
     truncation_flags, abstention_flags, usage}
    or {overall_score:0.0, error:...} when no rubrics or no council members
    are configured (never raises)."""
    if not rubrics:
        logger.warning(
            "[grading] no rubric criteria -> overall_score=0.0 "
            "(rubric.json missing/empty/malformed, or task_parser produced rubrics=[])"
        )
        return {"overall_score": 0.0, "error": "no rubric criteria"}
    system = _judge_system_prompt()

    if _gpt_judge_configured():
        gpt_result = _grade_gpt_primary(
            rubrics, task_description, workspace_results, transcript_text, system
        )
        if _grade_is_signal(gpt_result):
            return gpt_result
        logger.warning(
            "[grading] GPT primary judge produced no signal (%s) -> falling back "
            "to council",
            str(gpt_result.get("error") or "unknown")[:200],
        )

    try:
        members = council_members()
    except Exception as exc:  # noqa: BLE001 — AGENTS.md #12: grading must degrade, not raise
        # council_members()/_parse_council_member_override raise on a misconfigured
        # roster (unknown or non-council family tag in JUDGE_COUNCIL_MEMBERS, or a
        # roster fully filtered out by the provider). Fail-fast is fine at the CLI,
        # but grade_with_rubric must never raise (#12) — degrade to the no-signal
        # error dict so the run records a diagnosable result instead of crashing.
        logger.error("[grading] council roster unusable: %s", exc)
        return {
            "overall_score": 0.0,
            "error": f"council roster unusable: {exc}",
            "usage": dict(_ZERO_USAGE),
        }
    if not members:
        logger.error(
            "[grading] no judge council members configured -> overall_score=0.0; "
            "set JUDGE_COUNCIL_SONNET_ARN / _GLM_ARN / _KIMI_ARN (or JUDGE_COUNCIL_MEMBERS) in .env"
        )
        return {
            "overall_score": 0.0,
            "error": "no judge council members configured (set JUDGE_COUNCIL_SONNET_ARN / _GLM_ARN / _KIMI_ARN, or JUDGE_COUNCIL_MEMBERS) in .env",
            "usage": dict(_ZERO_USAGE),
        }

    validate_judge_pricing(members)

    # Rubric batching (Issue 1): the judge output-token cap (_FAMILY_EVIDENCE, e.g.
    # Sonnet 8192) truncates verdict lists on large rubrics, so tail criteria
    # abstain. Splitting into <=batch_size chunks keeps each verdict list under the
    # cap. Threshold read ONCE (env WCB_JUDGE_RUBRIC_BATCH_SIZE, guarded); rubrics
    # at/under it take the single-call path (byte-identical to pre-batching).
    batch_size = _rubric_batch_size()
    # Hoist per-member evidence out of the chunk loop: _gather_evidence walks the
    # filesystem + extracts binaries once per member; only the rubric block varies
    # per chunk (system prompt is identical, so Sonnet cachePoint still reused).
    evidence_for_member: "dict[str, str | JudgeUserPayload]" = {}
    rubric_names = _rubric_file_names(rubrics)
    for m in members:
        budget = _member_evidence_budget(m.model, m.family)
        evidence_for_member[m.model] = _gather_evidence(
            workspace_results, transcript_text, budget=budget,
            rubric_names=rubric_names,
            # Only the gpt transport carries image parts; the rest are text-only
            # by contract, so their placeholders must say the image is not shown.
            attach_images=(m.family == "gpt"),
        )

    def _grade_chunk(chunk: list) -> dict:
        user_for_member = {
            m.model: _judge_user_prompt(task_description, chunk, evidence_for_member[m.model])
            for m in members
        }
        return _grade_council(chunk, system, user_for_member, members)

    def _graded_chunks(chunk: list, depth: int = 0) -> list:
        # Refusal re-split (ajax_moreno 2026-09-04, empirically validated on
        # gama): safety refusals are prompt-COMPOSITION dependent — the same 31
        # criteria that refused as one block graded cleanly as 16-criterion
        # chunks. On a refused chunk, halve and retry each side (depth<=2);
        # halves that still fail degrade to synthetic abstains as before, so
        # the blast radius shrinks from the whole chunk to the poisoned core.
        res = _grade_chunk(chunk)
        err = str(res.get("error") or "").lower()
        if (res.get("error") and depth < 2 and len(chunk) > 4
                and ("refus" in err or "safety filter" in err)):
            logger.warning(
                "judge chunk of %d criteria refused upstream — re-splitting "
                "and retrying halves (depth %d)", len(chunk), depth + 1)
            mid = (len(chunk) + 1) // 2
            return (_graded_chunks(chunk[:mid], depth + 1)
                    + _graded_chunks(chunk[mid:], depth + 1))
        if not res.get("error") and (res.get("criteria_abstained") or 0) > 0:
            logger.warning(
                "judge chunk returned OK but %d of %d verdicts are missing — "
                "possible upstream verdict suppression; affected criteria "
                "abstain", res.get("criteria_abstained"), len(chunk))
        return [(chunk, res)]

    if len(rubrics) <= batch_size:
        parts = _graded_chunks(rubrics)
        if len(parts) == 1:
            return parts[0][1]
        return _merge_batched_grades(rubrics, members, parts)

    chunk_results: list[tuple[list, dict]] = []
    for start in range(0, len(rubrics), batch_size):
        chunk = rubrics[start:start + batch_size]
        chunk_results.extend(_graded_chunks(chunk))
    return _merge_batched_grades(rubrics, members, chunk_results)


def _write_score(output_dir: Path, task_id: str, scores: dict) -> None:
    score_path = output_dir / "score.json"
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(
        json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[%s] Grading results written to → %s", task_id, score_path)


def _error_score(output_dir: Path, task_id: str, message: str) -> dict:
    scores = {"overall_score": 0.0, "error": message}
    _write_score(output_dir, task_id, scores)
    return scores


def _grading_error(
    output_dir: Path,
    task_id: str,
    message: str,
    write_error_score: bool,
) -> dict:
    if write_error_score:
        return _error_score(output_dir, task_id, message)
    return {"error": message}


def write_error_score(output_dir: Path, task_id: str, message: str) -> dict:
    return _error_score(output_dir, task_id, message)


def run_grading(
    task_id: str,
    automated_checks: str,
    output_dir: Path,
    extra_env: str = "",
    lobster_env: list[str] | None = None,
    transcript_container_path: str = "",
    write_error_score: bool = False,
) -> dict:
    logger.info("[%s] Starting in-container grading...", task_id)

    loader_src = Path(__file__).with_name("transcript_loader.py")
    if not loader_src.exists():
        logger.error("[%s] transcript loader module not found: %s", task_id, loader_src)
        return _grading_error(
            output_dir,
            task_id,
            f"transcript loader module not found: {loader_src}",
            write_error_score,
        )

    runner_code = "\n".join([
        "import json",
        "from _transcript_loader import load_transcript",
        f"_transcript = load_transcript({json.dumps(transcript_container_path)})",
        "",
        automated_checks,
        "",
        f'result = grade(transcript=_transcript, workspace_path="{TMP_WORKSPACE}")',
        "print(json.dumps(result))",
    ]) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(runner_code)
        runner_host = f.name

    try:
        r_loader = subprocess.run(
            ["docker", "cp", str(loader_src), f"{task_id}:/tmp/_transcript_loader.py"],
            capture_output=True, text=True,
        )
        if r_loader.returncode != 0:
            logger.error("[%s] docker cp transcript loader failed: %s", task_id, r_loader.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"docker cp transcript loader failed: {r_loader.stderr}",
                write_error_score,
            )

        r = subprocess.run(
            ["docker", "cp", runner_host, f"{task_id}:/tmp/_grade_runner.py"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("[%s] docker cp failed: %s", task_id, r.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"docker cp failed: {r.stderr}",
                write_error_score,
            )

        env_args: list[str] = []
        for line in extra_env.splitlines():
            key = line.strip()
            if not key or key.startswith("#"):
                continue
            value = os.environ.get(key, "")
            env_args += ["-e", f"{key}={value}"]
            masked = (value[:4] + "***") if value else "(empty)"
            logger.info("[%s] Injecting grading env: %s=%s", task_id, key, masked)

        for key in (lobster_env or []):
            value = os.environ.get(key, "")
            if not value:
                logger.warning("[%s] Grading lobster env key %s not found, skipping", task_id, key)
                continue
            env_args += ["-e", f"{key}={value}"]
            masked = value[:4] + "***"
            logger.info("[%s] Injecting grading lobster env: %s=%s", task_id, key, masked)

        r = subprocess.run(
            ["docker", "exec", *env_args, task_id, "python3", "/tmp/_grade_runner.py"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            logger.error("[%s] Grading script execution failed: %s", task_id, r.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"grade script failed: {r.stderr}",
                write_error_score,
            )

        try:
            scores = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            scores = None
            for line in reversed(r.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        scores = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if scores is None:
                logger.error("[%s] Failed to parse grading result, no valid JSON found in stdout\nstdout: %s", task_id, r.stdout[:500])
                return _grading_error(
                    output_dir,
                    task_id,
                    "json parse failed: no valid JSON in stdout",
                    write_error_score,
                )

    finally:
        Path(runner_host).unlink(missing_ok=True)

    _write_score(output_dir, task_id, scores)
    return scores


def format_scores(task_id: str, scores: dict) -> str:
    if "error" in scores and not any(
        isinstance(v, (int, float)) for v in scores.values()
    ):
        return f"[{task_id}] Grading error: {scores['error']}"
    lines = [f"\n{'='*60}", f"  {task_id}", f"{'='*60}"]

    for k, v in scores.items():
        if isinstance(v, (int, float)):
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            lines.append(f"  {bar} {v:.2f}  {k}")

    lines.append("=" * 60)
    return "\n".join(lines)

def print_summary(results: list[dict], category: str, output_dir: Path, model_name: str,
                  quiet: bool = False) -> None:
    # quiet=True suppresses the ASCII console report (the Rich execution summary
    # in eval/run_batch.py renders it instead) while preserving the JSON write
    # below. Shadowing `print` for the whole function keeps every line unchanged.
    import builtins as _b
    print = _b.print if not quiet else (lambda *a, **k: None)  # noqa: A001
    print(f"\n{'#'*60}")
    print(f"  Summary Report — {category}")
    print(f"{'#'*60}")

    all_scores: dict[str, float] = {}
    for r in results:
        task_id = r["task_id"]
        scores = r['scores']
        if not scores:
            if r.get("error"):
                print(f"  ✗ {task_id}: {r['error']}")
            else:
                print(f"  - {task_id}: No scores")
            continue
        numeric_dict = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        
        if not numeric_dict:
            if "error" in scores:
                print(f"  ✗ {task_id}: Grading error {scores['error']}")
            else:
                print(f"  - {task_id}: No valid numeric scores")
            continue

        avg = sum(numeric_dict.values()) / len(numeric_dict)
        status = "!" if r.get("error") or scores.get("error") else "✓"
        note = ""
        if r.get("error"):
            note = f" agent_error={r['error']}"
        elif scores.get("error"):
            note = f" grading_error={scores['error']}"
        print(f"  {status} {task_id}: avg {avg:.2f}  ({len(numeric_dict)} items){note}")

        final_score_val = numeric_dict.get('overall_score', avg)
        all_scores[task_id] = final_score_val

    if all_scores:
        print(f"\n  Final scores per task:")
        for k, score in sorted(all_scores.items()):
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            print(f"    {bar} {score:.2f}  {k}")

    print(f"\n  Token usage and cost per task:")
    print(f"    {'Task ID':<55} {'Output Tokens':>12} {'Cost(USD)':>12}")
    print(f"    {'-'*55} {'-'*12} {'-'*12}")
    total_output_tokens = 0
    total_cost_usd = 0.0
    for r in sorted(results, key=lambda x: x["task_id"]):
        usage = r.get("usage", {})
        out_tok = usage.get("output_tokens", 0)
        cost = usage.get("cost_usd", 0.0)
        total_output_tokens += out_tok
        total_cost_usd += cost
        print(f"    {r['task_id']:<55} {out_tok:>12} {cost:>11.4f}$")
    print(f"    {'Total':<55} {total_output_tokens:>12} {total_cost_usd:>11.4f}$")

    summary_path = output_dir / category / f"summary_{model_name}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n  Summary written to → {summary_path}")
    print("#" * 60)

    if quiet:
        # The verbose ASCII report above is suppressed under quiet mode (the Rich
        # execution summary renders it instead). Keep a compact, greppable marker
        # in the log so downstream tooling that keys on the old "Summary Report —
        # <category>" header still finds the report. Routed through the logger,
        # never raw print, so it reaches logs/*.log on the default path without
        # corrupting the Textual dashboard's full-screen canvas.
        logger.info("Summary Report — %s | %d task(s) | written to %s",
                    category, len(results), summary_path)

_MODEL_COST_PER_TOKEN: dict[str, tuple[float, float]] = {
    "gpt-5.5":            (0.000005,  0.00003),
    "gpt-4o":             (0.0000025, 0.00001),
    "claude-opus-4.7":    (0.000005,  0.000025),
    "claude-sonnet-4.6":  (0.000003,  0.000015),
    "claude-fable-5":     (0.00001,   0.00005),
}


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                parts.append(str(c.get("text") or c.get("content") or ""))
        return "\n".join(parts)
    return ""


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def extract_usage_from_litellm_log(
    log_path: Path, window_start: float, window_end: float, run_key: str = ""
) -> dict:
    """Sum agent usage rows for one run.

    Attribution order:
      1. ``run_key`` exact match — rows the usage callback tagged with this
         run's per-attempt key. Immune to concurrent runs on a shared sidecar.
      2. Time-window fallback (legacy) — ONLY when no tagged row matches.
         Unsafe under parallelism: the ±2s-padded window sweeps in every
         concurrent run's traffic (measured 1.4x-62.7x inflation on the
         2026-08 deliveries). Retained for old logs and master-key
         deployments where the bearer cannot carry the run key.
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "audio_seconds": 0.0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_source": "litellm",
    }
    if not log_path or not log_path.exists():
        return totals

    from datetime import datetime as _dt

    pad = 2.0
    lo = window_start - pad
    hi = window_end + pad

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals

    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") in ("preflight", "failure"):
            continue
        rows.append(row)

    tagged = [r for r in rows if run_key and r.get("run_key") == run_key]
    if tagged:
        selected = tagged
        totals["usage_source"] = "litellm_run_key"
    else:
        selected = []
        for row in rows:
            ts_str = row.get("ts", "")
            try:
                ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError):
                continue
            if lo <= ts <= hi:
                selected.append(row)
        if run_key:
            logger.warning(
                "usage extraction: no rows tagged with run_key %s in %s — "
                "falling back to the time window, which OVER-ATTRIBUTES under "
                "parallel runs", run_key, log_path)

    for row in selected:
        totals["request_count"] += 1
        totals["input_tokens"]       += int(row.get("input_tokens", 0) or 0)
        totals["output_tokens"]      += int(row.get("output_tokens", 0) or 0)
        totals["cache_read_tokens"]  += int(row.get("cache_read_tokens", 0) or 0)
        totals["cache_write_tokens"] += int(row.get("cache_write_tokens", 0) or 0)
        totals["total_tokens"]       += int(row.get("total_tokens", 0) or 0)
        totals["audio_seconds"]      += float(row.get("audio_seconds", 0.0) or 0.0)
        totals["cost_usd"]           += float(row.get("cost_usd", 0.0) or 0.0)

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["audio_seconds"] = round(totals["audio_seconds"], 3)
    return totals


def extract_preflight_usage_from_litellm_log(log_path: Path) -> dict:
    # Aggregates every row tagged kind="preflight" in the LiteLLM callback log,
    # with no time-window filter. Preflight runs once per sidecar startup
    # (eval/run_batch.py::verify_litellm_upstream_reachable), BEFORE any task's
    # run window, so the in-window agent extractor skips it. Per user policy
    # (m1402, "All tasks" attribution), every task in the batch picks up the
    # same preflight cost so each task's usage.json reflects the true LLM
    # traffic that occurred during its execution. Returns the agent-shaped
    # totals dict (zero values when no preflight ran) so save_usage can drop
    # it straight into sources["preflight"].
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "audio_seconds": 0.0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_source": "litellm",
    }
    if not log_path or not log_path.exists():
        return totals
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") != "preflight":
            continue
        totals["request_count"] += 1
        totals["input_tokens"]       += int(row.get("input_tokens", 0) or 0)
        totals["output_tokens"]      += int(row.get("output_tokens", 0) or 0)
        totals["cache_read_tokens"]  += int(row.get("cache_read_tokens", 0) or 0)
        totals["cache_write_tokens"] += int(row.get("cache_write_tokens", 0) or 0)
        totals["total_tokens"]       += int(row.get("total_tokens", 0) or 0)
        totals["audio_seconds"]      += float(row.get("audio_seconds", 0.0) or 0.0)
        totals["cost_usd"]           += float(row.get("cost_usd", 0.0) or 0.0)
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["audio_seconds"] = round(totals["audio_seconds"], 3)
    return totals


def extract_oauth_usage_from_litellm_log(
    log_path: Path,
    window_start_ts: str = "",
    window_end_ts: str = "",
) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_actual": 0.0,
        "cost_bedrock_equivalent": 0.0,
        "request_count": 0,
        "usage_source": "litellm_oauth",
        "route": "claude_oauth_bridge",
    }
    try:
        if not log_path or not Path(log_path).exists():
            return totals
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if window_start_ts and row.get("ts", "") < window_start_ts:
            continue
        if window_end_ts and row.get("ts", "") > window_end_ts:
            continue
        if row.get("kind") == "failure":
            continue
        totals["request_count"] += 1
        totals["input_tokens"]       += int(row.get("input_tokens", 0) or 0)
        totals["output_tokens"]      += int(row.get("output_tokens", 0) or 0)
        totals["cache_read_tokens"]  += int(row.get("cache_read_tokens", 0) or 0)
        totals["cache_write_tokens"] += int(row.get("cache_write_tokens", 0) or 0)
        totals["cost_actual"]        += float(row.get("cost_actual", 0.0) or 0.0)
        totals["cost_bedrock_equivalent"] += float(row.get("cost_bedrock_equivalent", 0.0) or 0.0)
    totals["total_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"]
        + totals["cache_read_tokens"] + totals["cache_write_tokens"]
    )
    totals["cost_actual"] = round(totals["cost_actual"], 6)
    totals["cost_bedrock_equivalent"] = round(totals["cost_bedrock_equivalent"], 6)
    return totals


def extract_usage_from_jsonl(jsonl_path: Path) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "audio_seconds": 0.0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_source": "openclaw",
    }
    if not jsonl_path.exists():
        return totals

    entries: list[dict] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    openclaw_total = 0
    last_model = ""
    for entry in entries:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        totals["request_count"] += 1
        if msg.get("model"):
            last_model = msg["model"]
        usage = msg.get("usage", {})
        totals["input_tokens"]       += usage.get("input",       0)
        totals["output_tokens"]      += usage.get("output",      0)
        totals["cache_read_tokens"]  += usage.get("cacheRead",   0)
        totals["cache_write_tokens"] += usage.get("cacheWrite",  0)
        totals["total_tokens"]       += usage.get("totalTokens", 0)
        cost = usage.get("cost", {})
        totals["cost_usd"] += cost.get("total", 0.0)
        openclaw_total += usage.get("input", 0) + usage.get("output", 0)

    # Fallback: openclaw reported no usage but there were requests. Estimate
    # tokens (~len/4) with a running-context model and apply per-model rates.
    if openclaw_total == 0 and totals["request_count"] > 0:
        totals["usage_source"] = "estimated"
        running_context_tokens = 0
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            text = _extract_text(msg.get("content", ""))
            tokens = _estimate_tokens(text)
            role = msg.get("role")
            if role in ("user", "system", "toolResult"):
                running_context_tokens += tokens
            elif role == "assistant":
                totals["input_tokens"]  += running_context_tokens
                totals["output_tokens"] += tokens
                running_context_tokens += tokens
                if msg.get("model"):
                    last_model = msg["model"]
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]

        model_id = last_model.split("/")[-1] if last_model else ""
        rates = _MODEL_COST_PER_TOKEN.get(model_id, (0.0, 0.0))
        totals["cost_usd"] = (
            totals["input_tokens"]  * rates[0]
            + totals["output_tokens"] * rates[1]
        )

    # Mark missing-price $0 (e.g. OpenRouter-only models) so it is not read as "free".
    if totals["cost_usd"] == 0.0 and totals["request_count"] > 0:
        model_id = last_model.split("/")[-1] if last_model else ""
        if model_id not in _MODEL_COST_PER_TOKEN:
            totals["cost_unpriced"] = True

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals

def print_global_summary(results: list[dict], output_dir: Path, model_name: str,
                         quiet: bool = False) -> None:
    # quiet=True suppresses the ASCII console report (Rich renders it) while
    # preserving any JSON side effects below. See print_summary for rationale.
    import builtins as _b
    print = _b.print if not quiet else (lambda *a, **k: None)  # noqa: A001
    print(f"\n{'#'*60}")
    print(f"  Global Summary Report — ALL CATEGORIES")
    print(f"{'#'*60}")

    total_tasks = len(results)
    scored_tasks = 0
    missing_score_tasks = 0
    total_score = 0.0
    for r in results:
        scores = r.get("scores", {})
        numeric = {
            k: v
            for k, v in scores.items()
            if isinstance(v, (int, float))
        } if scores else {}
        if not numeric:
            missing_score_tasks += 1
            continue
        final = numeric.get("overall_score", sum(numeric.values()) / len(numeric))
        total_score += final
        scored_tasks += 1

    global_avg = 0.0
    if total_tasks > 0:
        global_avg = total_score / total_tasks
        bar = "█" * int(global_avg * 10) + "░" * (10 - int(global_avg * 10))
        print(f"\n  Completed tasks: {scored_tasks} / {total_tasks}")
        print(f"  Tasks without a valid score.json: {missing_score_tasks}")
        if missing_score_tasks > 0:
            print("  Possible causes: task execution failed, such as OOM, or grading failed.")
        print(f"  Global average: {bar} {global_avg:.4f}")
    else:
        print("  No tasks found")

    total_out_tok = sum(r.get("usage", {}).get("output_tokens", 0) for r in results)
    total_cost    = sum(r.get("usage", {}).get("cost_usd",      0.0) for r in results)
    print(f"  Total output tokens: {total_out_tok}   Total cost: ${total_cost:.4f}")

    summary_path = output_dir / f"summary_all_{model_name}.json"
    summary_path.write_text(
        json.dumps(
            {"global_avg": global_avg if total_tasks else None,
             "task_count": total_tasks,
             "scored_task_count": scored_tasks,
             "missing_score_task_count": missing_score_tasks,
             "results": results},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n  Global summary written to → {summary_path}")
    print("#" * 60)

    if quiet:
        # See print_summary: keep a compact, greppable marker in the log for
        # scrapers keying on the old "Global Summary Report — ALL CATEGORIES"
        # header when the ASCII report is suppressed. Logger, not raw print.
        logger.info("Global Summary Report — ALL CATEGORIES | %d task(s) | written to %s",
                    total_tasks, summary_path)
