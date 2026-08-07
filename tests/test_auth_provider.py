"""Unit tests for src/utils/auth_provider.py and the provider isolation it
enforces in grading.council_members().

The load-bearing case is TestCouncilIsolation::test_oauth_drops_kimi_and_glm:
run_batch calls load_dotenv() at import, so on an OAuth run all three
JUDGE_COUNCIL_*_ARN values are present in env. Before the auth-provider work the
council enlisted all three and billed Kimi + GLM straight to Bedrock even though
the operator had selected the Claude Max subscription.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from src.utils import auth_provider as ap
from src.utils.auth_provider import (
    BEDROCK,
    OAUTH,
    AuthProviderError,
    available_judge_families,
    filter_judge_families,
    normalize_provider,
    resolve_provider,
    served_trajectory_models,
    validate_judge_selection,
    validate_model_for_provider,
    validate_provider_auth,
)

_PROVIDER_ENV = [
    "WCB_AUTH_PROVIDER",
    "WCB_USE_CLAUDE_OAUTH",
    "WCB_CC_ACCOUNT_POOL",
    "JUDGE_COUNCIL_MEMBERS",
    "JUDGE_COUNCIL_SONNET_ARN",
    "JUDGE_COUNCIL_GLM_ARN",
    "JUDGE_COUNCIL_KIMI_ARN",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Drop every env key the provider resolver / council roster consult."""
    for k in _PROVIDER_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _cfg(**kw):
    """A Config-shaped stub. Only attribute access is used by this module."""
    base = dict(
        cc_account_pool="",
        aws_bearer_token="",
        bedrock_inference_arn="",
        bedrock_sonnet_arn="",
        anthropic_api_key="",
        openai_api_key="",
        meta_api_key="",
        meta_model="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ===========================================================================
# normalize_provider
# ===========================================================================


class TestNormalizeProvider:
    def test_none_for_blank(self):
        assert normalize_provider(None) is None
        assert normalize_provider("") is None
        assert normalize_provider("   ") is None

    def test_case_and_whitespace_insensitive(self):
        assert normalize_provider("  OAuth ") == OAUTH
        assert normalize_provider("BEDROCK") == BEDROCK

    def test_unknown_raises(self):
        with pytest.raises(AuthProviderError, match="unknown auth provider"):
            normalize_provider("openrouter")


# ===========================================================================
# resolve_provider — precedence and back-compat inference
# ===========================================================================


class TestResolveProvider:
    def test_explicit_flag_wins_over_env(self, clean_env):
        clean_env.setenv("WCB_AUTH_PROVIDER", OAUTH)
        args = SimpleNamespace(auth_provider=BEDROCK, use_claude_oauth=None)
        assert resolve_provider(args) == BEDROCK

    def test_legacy_flag_maps_to_oauth(self, clean_env):
        args = SimpleNamespace(auth_provider=None, use_claude_oauth=True)
        assert resolve_provider(args) == OAUTH

    def test_env_used_when_no_flag(self, clean_env):
        clean_env.setenv("WCB_AUTH_PROVIDER", OAUTH)
        assert resolve_provider(SimpleNamespace(auth_provider=None, use_claude_oauth=None)) == OAUTH

    def test_contradictory_selection_raises(self, clean_env):
        args = SimpleNamespace(auth_provider=BEDROCK, use_claude_oauth=True)
        with pytest.raises(AuthProviderError, match="conflicting auth selection"):
            resolve_provider(args)

    def test_agreeing_flags_are_fine(self, clean_env):
        args = SimpleNamespace(auth_provider=OAUTH, use_claude_oauth=True)
        assert resolve_provider(args) == OAUTH

    def test_inference_reproduces_legacy_oauth_condition(self, clean_env):
        """Back-compat: this is the exact expression run_batch used before."""
        clean_env.setenv("WCB_USE_CLAUDE_OAUTH", "1")
        clean_env.setenv("WCB_CC_ACCOUNT_POOL", "/pool/a.json")
        assert resolve_provider() == OAUTH

    def test_inference_needs_both_vars(self, clean_env):
        clean_env.setenv("WCB_USE_CLAUDE_OAUTH", "1")  # pool missing
        assert resolve_provider() == BEDROCK

    def test_defaults_to_bedrock(self, clean_env):
        assert resolve_provider() == BEDROCK

    def test_accepts_explicit_env_mapping(self, clean_env):
        assert resolve_provider(None, {"WCB_AUTH_PROVIDER": OAUTH}) == OAUTH


# ===========================================================================
# validate_provider_auth — terminal, never falls back
# ===========================================================================


class TestValidateProviderAuth:
    def test_oauth_ok_with_pool(self):
        validate_provider_auth(OAUTH, _cfg(cc_account_pool="/pool/a.json"))

    def test_oauth_without_pool_raises(self):
        with pytest.raises(AuthProviderError, match="WCB_CC_ACCOUNT_POOL is empty"):
            validate_provider_auth(OAUTH, _cfg())

    def test_oauth_error_promises_no_bedrock_fallback(self):
        with pytest.raises(AuthProviderError, match="Not falling back to AWS Bedrock"):
            validate_provider_auth(OAUTH, _cfg())

    def test_bedrock_ok_with_token_and_arn(self):
        validate_provider_auth(BEDROCK, _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o"))

    def test_bedrock_missing_token_raises(self):
        with pytest.raises(AuthProviderError, match="KENSEI_AWS_BEARER_TOKEN"):
            validate_provider_auth(BEDROCK, _cfg(bedrock_inference_arn="arn:o"))

    def test_bedrock_missing_arn_raises(self):
        with pytest.raises(AuthProviderError, match="KENSEI_BEDROCK_MODEL_ARN"):
            validate_provider_auth(BEDROCK, _cfg(aws_bearer_token="t"))

    def test_bedrock_error_promises_no_oauth_fallback(self):
        with pytest.raises(AuthProviderError, match="Not falling back to Claude OAuth"):
            validate_provider_auth(BEDROCK, _cfg())

    def test_unknown_provider_raises(self):
        with pytest.raises(AuthProviderError, match="unknown auth provider"):
            validate_provider_auth("openrouter", _cfg())


# ===========================================================================
# Judge rosters
# ===========================================================================


class TestJudgeRosters:
    def test_oauth_is_sonnet_only(self):
        assert available_judge_families(OAUTH) == ("sonnet",)

    def test_bedrock_is_full_council(self):
        assert set(available_judge_families(BEDROCK)) == {"sonnet", "glm", "kimi"}

    def test_family_env_vars_match_grading(self):
        """Pin the duplicated family table to grading's source of truth.

        auth_provider cannot import grading (grading imports it), so the labels
        are duplicated. This test is what stops them drifting.
        """
        from src.utils.grading import _FAMILY_ENV_VARS

        assert ap.FAMILY_ENV_VARS == _FAMILY_ENV_VARS

    def test_validate_rejects_kimi_under_oauth(self):
        with pytest.raises(AuthProviderError, match="not available"):
            validate_judge_selection(OAUTH, ["kimi"])

    def test_validate_rejects_partial_unsupported(self):
        with pytest.raises(AuthProviderError, match="glm"):
            validate_judge_selection(OAUTH, ["sonnet", "glm"])

    def test_validate_accepts_full_bedrock_council(self):
        validate_judge_selection(BEDROCK, ["sonnet", "glm", "kimi"])

    def test_validate_rejects_empty_selection(self):
        with pytest.raises(AuthProviderError, match="no judge models selected"):
            validate_judge_selection(BEDROCK, [])

    def test_filter_keeps_order_and_drops_unsupported(self):
        assert filter_judge_families(OAUTH, ["sonnet", "glm", "kimi"]) == ["sonnet"]
        assert filter_judge_families(BEDROCK, ["glm", "sonnet"]) == ["glm", "sonnet"]


# ===========================================================================
# served_trajectory_models — anti-drift against the real sidecar config
# ===========================================================================


_TRAJECTORY_IDS = {
    "claude-opus-5",
    "claude-opus-4.8",
    "claude-opus-4.7",
    "claude-opus-4-6",
    "claude-fable-5",
    "claude-sonnet-4-6",
    "gpt-5.5",
}


def _sidecar_model_names(**kw) -> set[str]:
    """model_name: keys the sidecar actually emits, limited to trajectory ids
    (the config also carries mocked embedding/whisper/image-alias entries)."""
    from src.utils.litellm_sidecar import build_litellm_config_yaml

    yaml = build_litellm_config_yaml(**kw)
    found = set(re.findall(r"^\s*-\s*model_name:\s*(\S+)", yaml, re.M))
    return found & _TRAJECTORY_IDS


class TestServedTrajectoryModels:
    def test_matches_sidecar_for_bedrock(self):
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o", bedrock_sonnet_arn="arn:s")
        assert served_trajectory_models(BEDROCK, cfg) == _sidecar_model_names(
            bedrock_arn="arn:o", bedrock_sonnet_arn="arn:s", auth_provider=BEDROCK
        )

    def test_matches_sidecar_for_oauth(self):
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o", bedrock_sonnet_arn="arn:s")
        assert served_trajectory_models(OAUTH, cfg) == _sidecar_model_names(
            bedrock_arn="arn:o",
            bedrock_sonnet_arn="arn:s",
            use_claude_oauth=True,
            bridge_url="http://bridge:8765",
            auth_provider=OAUTH,
        )

    def test_oauth_excludes_bedrock_sonnet(self):
        """Isolation: the Bedrock sonnet block must not be registered on OAuth."""
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o", bedrock_sonnet_arn="arn:s")
        assert "claude-sonnet-4-6" not in served_trajectory_models(OAUTH, cfg)
        assert "claude-sonnet-4-6" in served_trajectory_models(BEDROCK, cfg)

    def test_bearer_token_gates_bedrock_models(self):
        """The sidecar zeroes both ARNs without a bearer token; mirror that."""
        cfg = _cfg(bedrock_inference_arn="arn:o", bedrock_sonnet_arn="arn:s")
        assert served_trajectory_models(BEDROCK, cfg) == set()

    def test_openai_key_adds_gpt_regardless_of_provider(self):
        cfg = _cfg(openai_api_key="sk-x")
        assert "gpt-5.5" in served_trajectory_models(OAUTH, cfg)
        assert "gpt-5.5" in served_trajectory_models(BEDROCK, cfg)


class TestValidateModelForProvider:
    def test_accepts_served_model(self):
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o")
        validate_model_for_provider(BEDROCK, "claude-opus-4.8", cfg)

    def test_rejects_unserved_model_with_clear_error(self):
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o")
        with pytest.raises(AuthProviderError, match="Refusing to silently reroute"):
            validate_model_for_provider(OAUTH, "claude-opus-4.8", cfg)

    def test_error_names_the_available_models(self):
        cfg = _cfg(aws_bearer_token="t", bedrock_inference_arn="arn:o")
        with pytest.raises(AuthProviderError, match="claude-fable-5"):
            validate_model_for_provider(OAUTH, "nope", cfg)


# ===========================================================================
# grading.council_members — the isolation regression tests
# ===========================================================================


class TestCouncilIsolation:
    @pytest.fixture(autouse=True)
    def _arns(self, clean_env):
        clean_env.setenv("JUDGE_COUNCIL_SONNET_ARN", "arn:s")
        clean_env.setenv("JUDGE_COUNCIL_GLM_ARN", "arn:g")
        clean_env.setenv("JUDGE_COUNCIL_KIMI_ARN", "arn:k")
        return clean_env

    def test_bedrock_enlists_full_council(self, _arns):
        from src.utils.grading import council_members

        _arns.setenv("WCB_AUTH_PROVIDER", BEDROCK)
        assert [m.family for m in council_members()] == ["sonnet", "glm", "kimi"]

    def test_oauth_drops_kimi_and_glm(self, _arns):
        """THE regression test: all three ARNs are set (load_dotenv puts them
        there), but an OAuth run must not bill Kimi/GLM to Bedrock."""
        from src.utils.grading import council_members

        _arns.setenv("WCB_AUTH_PROVIDER", OAUTH)
        assert [m.family for m in council_members()] == ["sonnet"]

    def test_oauth_inferred_from_legacy_vars_also_filters(self, _arns):
        from src.utils.grading import council_members

        _arns.setenv("WCB_USE_CLAUDE_OAUTH", "1")
        _arns.setenv("WCB_CC_ACCOUNT_POOL", "/pool/a.json")
        assert [m.family for m in council_members()] == ["sonnet"]

    def test_members_override_is_also_filtered(self, _arns):
        from src.utils.grading import council_members

        _arns.setenv("WCB_AUTH_PROVIDER", OAUTH)
        _arns.setenv("JUDGE_COUNCIL_MEMBERS", "sonnet=arn:s,glm=arn:g")
        assert [m.family for m in council_members()] == ["sonnet"]

    def test_empty_roster_raises_instead_of_scoring_zero(self, _arns):
        """Returning [] would surface as overall_score=0.0 — i.e. it would look
        like the agent failed rather than like the council is misconfigured."""
        from src.utils.grading import council_members

        _arns.setenv("WCB_AUTH_PROVIDER", OAUTH)
        _arns.setenv("JUDGE_COUNCIL_MEMBERS", "glm=arn:g,kimi=arn:k")
        with pytest.raises(RuntimeError, match="no usable judge remains"):
            council_members()

    def test_no_configured_members_still_returns_empty(self, _arns):
        """A genuinely unconfigured council keeps its existing behaviour."""
        from src.utils.grading import council_members

        for k in ("JUDGE_COUNCIL_SONNET_ARN", "JUDGE_COUNCIL_GLM_ARN", "JUDGE_COUNCIL_KIMI_ARN"):
            _arns.delenv(k, raising=False)
        _arns.setenv("WCB_AUTH_PROVIDER", BEDROCK)
        assert council_members() == []
