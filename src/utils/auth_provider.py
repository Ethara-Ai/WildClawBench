"""Explicit authentication-provider selection for a harness run.

WHY THIS EXISTS
---------------
Before this module the harness had no first-class notion of "which auth am I
running under". Auth was *inferred* from whichever env vars happened to be set:
``WCB_USE_CLAUDE_OAUTH`` + ``WCB_CC_ACCOUNT_POOL`` meant Claude Max OAuth,
otherwise whatever ``.env`` supplied. That produced three real problems:

1. The judge council read ``JUDGE_COUNCIL_{SONNET,GLM,KIMI}_ARN`` live from env
   (``grading.council_members``). Because ``run_batch`` calls ``load_dotenv()``
   at import, an OAuth run still enlisted Kimi + GLM and billed them straight to
   Bedrock -- silently spending the credentials the operator thought they had
   opted out of.
2. Unsupported ``--model`` values were silently rewritten to a fallback instead
   of failing (``run_batch``), so a typo produced a full, expensive, wrong run.
3. The sidecar's ``if oauth -> elif bedrock -> elif anthropic`` cascade meant a
   rotated Bedrock token silently downgraded the transport rather than erroring.

The contract here is deliberately blunt: **the two providers are independent and
there is NO fallback between them.** If the selected provider cannot
authenticate, the caller raises ``AuthProviderError`` and the run terminates --
it never retries against the other provider.

BACKWARD COMPATIBILITY
----------------------
``resolve_provider`` falls back to *inferring* the provider from the legacy env
vars when nothing explicit is given, so existing flows (``script/run.sh``, direct
``eval/run_batch.py`` invocations, CI) behave exactly as before this module
landed. Only an explicit ``--auth-provider`` / ``WCB_AUTH_PROVIDER`` changes
behaviour.

NOTE ON IMPORTS: this module must NOT import ``src.utils.grading`` -- grading
imports *this* module to filter the council roster, and a cycle would break
both. The judge family labels are therefore duplicated here as plain strings and
pinned to grading's table by ``tests/test_auth_provider.py``.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Optional, Sequence

# --- Providers --------------------------------------------------------------

OAUTH = "oauth"
BEDROCK = "bedrock"
PROVIDERS: tuple[str, ...] = (OAUTH, BEDROCK)

#: Env var carrying the resolved provider across process/thread boundaries.
#: ``run_batch`` exports this so ``grading.council_members`` -- which reads env
#: live, by design -- sees the same provider the CLI/TUI selected.
PROVIDER_ENV_VAR = "WCB_AUTH_PROVIDER"

#: Env var carrying the JUDGE lane's provider. UNSET means "same as the agent
#: lane", so dual-provider mode is opt-in and every gate keeps the value it had
#: before this var existed. Read LIVE (never cached) by
#: ``grading.council_members`` and ``judge_litellm._judge_oauth_bridge_url``,
#: exactly like PROVIDER_ENV_VAR, because both run on worker threads long after
#: ``main()``'s frame is gone.
JUDGE_PROVIDER_ENV_VAR = "WCB_JUDGE_AUTH_PROVIDER"

_PROVIDER_LABELS: dict[str, str] = {
    OAUTH: "OAuth (Claude Max subscription)",
    BEDROCK: "AWS Bedrock",
}

# --- Judge council rosters --------------------------------------------------

# Kimi and GLM exist only as Bedrock application-inference profiles; there is no
# OAuth/subscription equivalent, and the Sonnet judge is the only member the
# cc-bridge can serve (src/utils/judge_litellm.py routes `family == "sonnet"`
# through the bridge and leaves the others on Bedrock). So OAuth necessarily
# collapses the council to a single Sonnet member.
JUDGE_FAMILIES_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    OAUTH: ("sonnet",),
    BEDROCK: ("sonnet", "glm", "kimi"),
}

#: Env var carrying each family's ARN. Mirrors ``grading._FAMILY_ENV_VARS``;
#: kept in sync by test_auth_provider.py::test_family_env_vars_match_grading.
FAMILY_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("sonnet", "JUDGE_COUNCIL_SONNET_ARN"),
    ("glm", "JUDGE_COUNCIL_GLM_ARN"),
    ("kimi", "JUDGE_COUNCIL_KIMI_ARN"),
)


class AuthProviderError(RuntimeError):
    """Raised when the selected provider is invalid, unauthenticated, or paired
    with a model/judge it cannot serve. Always terminal -- never caught and
    retried against the other provider."""


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def provider_label(provider: str) -> str:
    """Human-readable name for display in the TUI and logs."""
    return _PROVIDER_LABELS.get(provider, provider)


def normalize_provider(value: Any) -> Optional[str]:
    """Coerce user input to a known provider id, or None when blank/unset.

    Raises AuthProviderError on a non-empty value that is not a known provider,
    so a typo surfaces immediately rather than silently inferring.
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in PROVIDERS:
        raise AuthProviderError(
            f"unknown auth provider {text!r}; expected one of {', '.join(PROVIDERS)}"
        )
    return text


def resolve_provider(
    args: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the provider for this run.

    Precedence (first hit wins):
      1. explicit ``--auth-provider`` on *args*
      2. ``--use-claude-oauth`` on *args* (legacy alias for ``oauth``)
      3. ``WCB_AUTH_PROVIDER`` in *env*
      4. inferred: OAuth when ``WCB_USE_CLAUDE_OAUTH`` is truthy AND
         ``WCB_CC_ACCOUNT_POOL`` is non-empty; otherwise Bedrock.

    Step 4 is what preserves pre-existing behaviour for callers that never pass
    the new flag.
    """
    env = os.environ if env is None else env

    explicit = normalize_provider(getattr(args, "auth_provider", None))
    legacy_oauth = bool(getattr(args, "use_claude_oauth", None))

    if explicit:
        # A contradictory pair is a user error, not something to silently pick a
        # winner for -- rerouting is exactly what this feature exists to prevent.
        if legacy_oauth and explicit != OAUTH:
            raise AuthProviderError(
                f"conflicting auth selection: --auth-provider {explicit} was given "
                f"together with --use-claude-oauth (which means "
                f"--auth-provider {OAUTH}). Pass only one."
            )
        return explicit

    if legacy_oauth:
        return OAUTH

    from_env = normalize_provider(env.get(PROVIDER_ENV_VAR))
    if from_env:
        return from_env

    if _truthy(env.get("WCB_USE_CLAUDE_OAUTH")) and str(
        env.get("WCB_CC_ACCOUNT_POOL") or ""
    ).strip():
        return OAUTH
    return BEDROCK


def resolve_judge_provider_env_only(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Resolve the JUDGE lane from env alone, with NO legacy inference.

    Precedence: ``WCB_JUDGE_AUTH_PROVIDER`` -> ``WCB_AUTH_PROVIDER`` -> None.

    This is the exact contract ``judge_litellm._judge_oauth_bridge_url`` has
    always had (a RAW read of ``WCB_AUTH_PROVIDER``), extended by one var in
    front. ``resolve_provider``'s step-4 inference from
    ``WCB_USE_CLAUDE_OAUTH`` + ``WCB_CC_ACCOUNT_POOL`` is deliberately NOT
    applied: a process that never exported ``WCB_AUTH_PROVIDER``
    (``script/regrade.py``, the OAuth drills, any direct
    ``grading.grade_with_rubric`` import) must keep resolving to "not oauth"
    exactly as it did before this function existed. Inferring there would
    re-arm the 700,000-char OAuth evidence clamp on the regrade path.
    """
    env = os.environ if env is None else env
    from_judge = normalize_provider(env.get(JUDGE_PROVIDER_ENV_VAR))
    if from_judge:
        return from_judge
    return normalize_provider(env.get(PROVIDER_ENV_VAR))


def resolve_judge_provider(
    args: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the provider the JUDGE lane runs under.

    Precedence (first hit wins):
      1. explicit ``--judge-auth-provider`` on *args*
      2. ``WCB_JUDGE_AUTH_PROVIDER`` in *env*
      3. the AGENT provider (``resolve_provider(args, env)``)

    Step 3 is the byte-identical-when-unset contract for the gates that
    ALREADY call ``resolve_provider()`` today (the council roster filter and
    the judge's no-fallback guard). Gates that today read env RAW must use
    ``resolve_judge_provider_env_only`` instead -- see its docstring.
    """
    env = os.environ if env is None else env
    explicit = normalize_provider(getattr(args, "judge_auth_provider", None))
    if explicit:
        return explicit
    from_env = normalize_provider(env.get(JUDGE_PROVIDER_ENV_VAR))
    if from_env:
        return from_env
    return resolve_provider(args, env)


def lanes_differ(agent_provider: str, judge_provider: str) -> bool:
    """Single source of truth for "is this a mixed run"."""
    return agent_provider != judge_provider


def validate_judge_provider_auth(
    judge_provider: str, agent_provider: str, config: Any
) -> None:
    """Assert the JUDGE lane's credentials are present, before any agent spend.

    Delegates to ``validate_provider_auth`` and re-raises with the lane named,
    so an operator reading the abort knows which half of a mixed run is
    misconfigured. A run whose judge cannot authenticate produces an expensive
    trajectory and then ``score.failed.json``.
    """
    try:
        validate_provider_auth(judge_provider, config)
    except AuthProviderError as exc:
        raise AuthProviderError(
            f"JUDGE lane provider {judge_provider!r} ({provider_label(judge_provider)}) "
            f"is unauthenticated while the AGENT lane runs on {agent_provider!r}. "
            f"{exc} Refusing to spend a trajectory that cannot be graded. "
            f"Unset {JUDGE_PROVIDER_ENV_VAR} to put the judge back on the agent's "
            f"provider."
        ) from exc

    if (
        judge_provider == OAUTH
        and lanes_differ(agent_provider, judge_provider)
        and not str(getattr(config, "cc_bridge_secret", "") or "").strip()
    ):
        # The cc-bridge's co-tenant guard is a JUDGE-lane credential whenever the
        # judge alone is on OAuth: eval/bootstrap_sidecar.py runs in a subprocess,
        # so a secret it generates itself dies with that subprocess and the
        # host-side judge then sends `x-wcb-bridge-secret: ""` and is rejected.
        # Only an explicit .env value survives into this process.
        raise AuthProviderError(
            f"JUDGE lane provider 'oauth' on a {agent_provider!r} agent requires "
            f"WCB_CC_BRIDGE_SECRET to be set in .env: the cc-bridge is started by "
            f"a subprocess, so an auto-generated secret never reaches the host-side "
            f"judge and every grading call would be rejected by the bridge's "
            f"co-tenant guard. Set WCB_CC_BRIDGE_SECRET=<hex> in .env. "
            f"Not falling back to AWS Bedrock."
        )


def validate_provider_auth(provider: str, config: Any) -> None:
    """Assert the credentials for *provider* are present and self-consistent.

    Called before any container starts so a misconfiguration costs seconds
    rather than a full trajectory. Raises AuthProviderError with an actionable
    message; the caller must terminate rather than try the other provider.
    """
    if provider not in PROVIDERS:
        raise AuthProviderError(
            f"unknown auth provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )

    if provider == OAUTH:
        if not str(getattr(config, "cc_account_pool", "") or "").strip():
            raise AuthProviderError(
                "auth provider 'oauth' selected but no Claude OAuth credentials are "
                "configured: WCB_CC_ACCOUNT_POOL is empty. Run `source script/wcb setup` "
                "to copy this machine's Claude Max token into ~/.wcb/oauth_pool/, then "
                "`source script/wcb login`. Not falling back to AWS Bedrock."
            )
        return

    # Bedrock. Auth here is bearer-token only -- there is no access-key, profile,
    # or ~/.aws credential-chain path anywhere in litellm_sidecar.start_litellm,
    # so this is a two-field check and nothing more.
    missing: list[str] = []
    if not str(getattr(config, "aws_bearer_token", "") or "").strip():
        missing.append("KENSEI_AWS_BEARER_TOKEN (alias AWS_BEARER_TOKEN_BEDROCK)")
    if not str(getattr(config, "bedrock_inference_arn", "") or "").strip():
        missing.append("KENSEI_BEDROCK_MODEL_ARN (alias BEDROCK_MODEL_ARN)")
    if missing:
        raise AuthProviderError(
            "auth provider 'bedrock' selected but its credentials are incomplete; "
            "missing: " + ", ".join(missing) + ". Set them in .env. "
            "Not falling back to Claude OAuth."
        )


# --- Judge council ----------------------------------------------------------


def available_judge_families(provider: str) -> tuple[str, ...]:
    """Judge-council families the given provider is allowed to enlist."""
    if provider not in PROVIDERS:
        raise AuthProviderError(
            f"unknown auth provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )
    return JUDGE_FAMILIES_BY_PROVIDER[provider]


def validate_judge_selection(provider: str, families: Iterable[str]) -> None:
    """Reject judge families the provider cannot serve.

    Deliberately raises instead of filtering: silently dropping Kimi/GLM from an
    OAuth run would change the grading semantics (council -> single judge)
    without telling anyone.
    """
    allowed = set(available_judge_families(provider))
    selected = [str(f).strip().lower() for f in families if str(f).strip()]
    unsupported = [f for f in selected if f not in allowed]
    if unsupported:
        raise AuthProviderError(
            f"judge model(s) {', '.join(sorted(set(unsupported)))} are not available "
            f"under auth provider {provider!r}; it supports: "
            f"{', '.join(sorted(allowed))}. Select AWS Bedrock to use the full "
            f"3-judge council."
        )
    if not selected:
        raise AuthProviderError(
            f"no judge models selected for auth provider {provider!r}; at least one "
            f"of {', '.join(sorted(allowed))} is required to grade the rubric."
        )


def filter_judge_families(provider: str, families: Sequence[str]) -> list[str]:
    """Return *families* restricted to those the provider can serve, order kept.

    Used by ``grading.council_members`` to enforce isolation on a roster that was
    assembled from env. Unlike ``validate_judge_selection`` this does not raise
    on unsupported entries -- env may legitimately carry all three ARNs while the
    operator has chosen OAuth for this particular run.
    """
    allowed = set(available_judge_families(provider))
    return [f for f in families if f in allowed]


# --- Trajectory models ------------------------------------------------------


def served_trajectory_models(provider: str, config: Any) -> set[str]:
    """Model ids the LiteLLM sidecar will actually register for *provider*.

    Mirrors the branch conditions in
    ``litellm_sidecar.build_litellm_config_yaml``. Used to validate an explicit
    ``--model`` so an unsupported pick raises instead of being silently rewritten.

    ``tests/test_auth_provider.py`` asserts this stays equal to the ``model_name:``
    keys the sidecar actually emits, so the two cannot drift.
    """
    if provider not in PROVIDERS:
        raise AuthProviderError(
            f"unknown auth provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )

    models: set[str] = set()
    bearer = str(getattr(config, "aws_bearer_token", "") or "").strip()
    # The sidecar zeroes both ARNs when the bearer token is absent, so mirror that.
    bedrock_arn = str(getattr(config, "bedrock_inference_arn", "") or "").strip() if bearer else ""
    sonnet_arn = str(getattr(config, "bedrock_sonnet_arn", "") or "").strip() if bearer else ""
    anthropic_key = str(getattr(config, "anthropic_api_key", "") or "").strip()
    openai_key = str(getattr(config, "openai_api_key", "") or "").strip()
    meta_key = str(getattr(config, "meta_api_key", "") or "").strip()
    meta_model = str(getattr(config, "meta_model", "") or "").strip()

    if provider == OAUTH:
        # cc-bridge routes (litellm_sidecar.py, use_claude_oauth branch).
        models.update({"claude-opus-5", "claude-opus-4.7", "claude-opus-4-6", "claude-fable-5"})
    else:
        if bedrock_arn:
            models.update({"claude-opus-5", "claude-opus-4.8", "claude-opus-4.7", "claude-opus-4-6"})
        elif anthropic_key:
            models.update({"claude-opus-5", "claude-opus-4.7", "claude-opus-4-6"})
        if sonnet_arn:
            models.add("claude-sonnet-4-6")

    # Provider-independent: these key off their own credentials and are
    # deliberately out of scope for OAuth-vs-Bedrock isolation.
    if openai_key:
        models.add("gpt-5.5")
    if meta_key and meta_model:
        models.add(meta_model)

    return models


def validate_model_for_provider(provider: str, model: str, config: Any) -> None:
    """Raise unless *model* is served under *provider*.

    The whole point of this feature: an invalid provider/model pair produces a
    clear validation error rather than a silent reroute to some fallback.
    """
    served = served_trajectory_models(provider, config)
    if model in served:
        return
    raise AuthProviderError(
        f"model {model!r} is not served under auth provider {provider!r} "
        f"({provider_label(provider)}). Available: "
        f"{', '.join(sorted(served)) if served else '<none -- credentials missing>'}. "
        f"Refusing to silently reroute to a different model."
    )
