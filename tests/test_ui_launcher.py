"""Tests for the launcher TUI's auth-provider wiring (src/utils/ui/launcher.py).

Covers the pure translation layer -- form dict -> run_batch argv and -> env
overrides -- plus the provider-dependent option builders. The Textual widget
tree itself is not driven here; the rules that matter (which judge models a
provider offers, what argv/env a selection produces) all live in plain functions
and static methods so they can be tested without an event loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.auth_provider import BEDROCK, OAUTH, AuthProviderError
from src.utils.ui import launcher as L

pytestmark = pytest.mark.skipif(
    not L.launcher_available(), reason="textual not installed"
)


def _form(**kw):
    """A form dict shaped like LauncherApp._collect()'s return value."""
    base = dict(
        task="input/demo",
        backend="openclaw",
        model="claude-opus-4.7",
        custom_model="",
        parallel=1,
        reps=1,
        litellm=True,
        mock_stack=True,
        rebuild_mocks=False,
        judge_council=L.ON,
        generate_tests=L.AUTO,
        interactive=L.OFF,
        thinking="default",
        auth_provider=BEDROCK,
        judge_models="sonnet,glm,kimi",
        oauth_account=None,
    )
    base.update(kw)
    return base


@pytest.fixture
def arns(monkeypatch):
    monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "arn:s")
    monkeypatch.setenv("JUDGE_COUNCIL_GLM_ARN", "arn:g")
    monkeypatch.setenv("JUDGE_COUNCIL_KIMI_ARN", "arn:k")
    return monkeypatch


# ===========================================================================
# config_to_argv
# ===========================================================================


class TestConfigToArgv:
    def test_emits_auth_provider(self):
        argv = L.config_to_argv(_form(auth_provider=BEDROCK), Path("."), isatty=False)
        assert "--auth-provider" in argv
        assert argv[argv.index("--auth-provider") + 1] == BEDROCK

    def test_oauth_emits_use_claude_oauth(self):
        argv = L.config_to_argv(
            _form(auth_provider=OAUTH, oauth_account="/pool/a.json"), Path("."), isatty=False
        )
        assert "--use-claude-oauth" in argv
        assert argv[argv.index("--auth-provider") + 1] == OAUTH

    def test_bedrock_never_emits_use_claude_oauth(self):
        """A leftover account selection must not flip the transport -- and
        --auth-provider bedrock together with --use-claude-oauth is a hard
        error in resolve_provider(), so emitting both would break the run."""
        argv = L.config_to_argv(
            _form(auth_provider=BEDROCK, oauth_account="/pool/a.json"), Path("."), isatty=False
        )
        assert "--use-claude-oauth" not in argv

    def test_argv_parses_against_the_real_parser(self):
        """End-to-end: whatever the form emits must satisfy run_batch's parser."""
        from src.utils.cli_args import build_run_batch_parser

        argv = L.config_to_argv(
            _form(auth_provider=OAUTH, oauth_account="/pool/a.json"), Path("."), isatty=False
        )
        args = build_run_batch_parser("m", 1).parse_args(argv)
        assert args.auth_provider == OAUTH

    def test_custom_model_still_overrides(self):
        argv = L.config_to_argv(_form(custom_model="  my/model  "), Path("."), isatty=False)
        assert argv[argv.index("--model") + 1] == "my/model"


# ===========================================================================
# provider_env_overrides
# ===========================================================================


class TestProviderEnvOverrides:
    def test_bedrock_forces_oauth_vars_off(self, arns):
        """A stale WCB_USE_CLAUDE_OAUTH=1 from `wcb login` must not win."""
        env = L.provider_env_overrides(_form(auth_provider=BEDROCK))
        assert env["WCB_AUTH_PROVIDER"] == BEDROCK
        assert env["WCB_USE_CLAUDE_OAUTH"] == "0"
        assert env["WCB_CC_ACCOUNT_POOL"] == ""

    def test_oauth_sets_pool_and_provider(self, arns):
        env = L.provider_env_overrides(
            _form(auth_provider=OAUTH, judge_models="sonnet", oauth_account="/pool/a.json")
        )
        assert env["WCB_AUTH_PROVIDER"] == OAUTH
        assert env["WCB_USE_CLAUDE_OAUTH"] == "1"
        assert env["WCB_CC_ACCOUNT_POOL"] == "/pool/a.json"

    def test_full_council_leaves_members_override_unset(self, arns):
        """Full roster => let the per-family ARN vars stay in charge."""
        env = L.provider_env_overrides(_form(auth_provider=BEDROCK, judge_models="sonnet,glm,kimi"))
        assert "JUDGE_COUNCIL_MEMBERS" not in env

    def test_subset_sets_members_override(self, arns):
        env = L.provider_env_overrides(_form(auth_provider=BEDROCK, judge_models="sonnet"))
        assert env["JUDGE_COUNCIL_MEMBERS"] == "sonnet=arn:s"

    def test_subset_with_missing_arn_is_skipped(self, monkeypatch):
        """A half-built override would grade with a silently different council."""
        monkeypatch.delenv("JUDGE_COUNCIL_GLM_ARN", raising=False)
        monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "arn:s")
        env = L.provider_env_overrides(_form(auth_provider=BEDROCK, judge_models="sonnet,glm"))
        assert "JUDGE_COUNCIL_MEMBERS" not in env

    def test_back_compat_alias_is_the_same_function(self):
        assert L.oauth_env_overrides is L.provider_env_overrides


# ===========================================================================
# Provider-dependent option builders
# ===========================================================================


class TestJudgeOptions:
    def test_oauth_offers_only_sonnet(self):
        opts = L.LauncherApp._judge_options(OAUTH)
        assert [v for _, v in opts] == ["sonnet"]

    def test_oauth_has_no_full_council_entry(self):
        """One family => no "(full council)" aggregate option."""
        assert all("full council" not in label for label, _ in L.LauncherApp._judge_options(OAUTH))

    def test_bedrock_offers_council_plus_singles(self):
        values = [v for _, v in L.LauncherApp._judge_options(BEDROCK)]
        assert values[0] == "sonnet,glm,kimi"
        assert set(values[1:]) == {"sonnet", "glm", "kimi"}

    def test_unknown_provider_raises(self):
        with pytest.raises(AuthProviderError):
            L.LauncherApp._judge_options("openrouter")

    def test_every_offered_option_validates(self):
        """The dropdown must never present a combination that _collect rejects."""
        from src.utils.auth_provider import validate_judge_selection

        for provider in (OAUTH, BEDROCK):
            for _, value in L.LauncherApp._judge_options(provider):
                validate_judge_selection(provider, value.split(","))


class TestDefaultProvider:
    def _app(self, monkeypatch, prefs=None):
        monkeypatch.setattr(L, "load_prefs", lambda: prefs or {})
        return L.LauncherApp(Path("."))

    def test_defaults_to_bedrock(self, monkeypatch):
        for k in ("WCB_AUTH_PROVIDER", "WCB_USE_CLAUDE_OAUTH", "WCB_CC_ACCOUNT_POOL"):
            monkeypatch.delenv(k, raising=False)
        assert self._app(monkeypatch)._default_provider() == BEDROCK

    def test_follows_explicit_env(self, monkeypatch):
        monkeypatch.setenv("WCB_AUTH_PROVIDER", OAUTH)
        assert self._app(monkeypatch)._default_provider() == OAUTH

    def test_infers_oauth_after_wcb_login(self, monkeypatch):
        monkeypatch.delenv("WCB_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("WCB_USE_CLAUDE_OAUTH", "1")
        monkeypatch.setenv("WCB_CC_ACCOUNT_POOL", "/pool/a.json")
        assert self._app(monkeypatch)._default_provider() == OAUTH

    def test_bad_env_value_does_not_crash_the_form(self, monkeypatch):
        monkeypatch.setenv("WCB_AUTH_PROVIDER", "openrouter")
        monkeypatch.delenv("WCB_USE_CLAUDE_OAUTH", raising=False)
        assert self._app(monkeypatch)._default_provider() == BEDROCK

    def test_saved_pref_wins_over_inference(self, monkeypatch):
        monkeypatch.delenv("WCB_AUTH_PROVIDER", raising=False)
        monkeypatch.delenv("WCB_USE_CLAUDE_OAUTH", raising=False)
        app = self._app(monkeypatch, prefs={"auth_provider": OAUTH})
        assert app._initial_provider() == OAUTH

    def test_stale_pref_for_unknown_provider_falls_back(self, monkeypatch):
        monkeypatch.delenv("WCB_AUTH_PROVIDER", raising=False)
        monkeypatch.delenv("WCB_USE_CLAUDE_OAUTH", raising=False)
        app = self._app(monkeypatch, prefs={"auth_provider": "openrouter"})
        assert app._initial_provider() == BEDROCK

    def test_judge_value_resets_when_pref_unsupported(self, monkeypatch):
        """Switching a saved Bedrock council pref onto OAuth must not carry
        kimi/glm across."""
        app = self._app(monkeypatch, prefs={"judge_models": "sonnet,glm,kimi"})
        assert app._judge_value(OAUTH) == "sonnet"

    def test_summary_line_names_provider_and_judges(self, monkeypatch):
        text = self._app(monkeypatch)._summary_text(OAUTH, "claude-opus-4.7", "sonnet")
        assert "OAuth" in text and "claude-opus-4.7" in text and "Claude Sonnet" in text


# ===========================================================================
# Live widget behaviour
# ===========================================================================


class TestProviderSwitchingLive:
    """Mount the real Textual app and switch providers.

    The option builders are unit-tested above, but only a real mount exercises
    compose(), on_mount() and the on_select_changed handler together -- i.e.
    whether the judge Select is actually repopulated and locked when the user
    picks OAuth. Driven through asyncio.run() rather than pytest-asyncio, which
    is not a dependency of this repo.
    """

    @staticmethod
    def _drive(monkeypatch):
        import asyncio

        from textual.widgets import Select, Static

        monkeypatch.setattr(L, "load_prefs", lambda: {})
        monkeypatch.delenv("WCB_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("WCB_USE_CLAUDE_OAUTH", "0")

        async def run():
            app = L.LauncherApp(Path("."))
            seen = {}
            async with app.run_test() as pilot:
                provider = app.query_one("#auth_provider", Select)
                judges = app.query_one("#judge_models", Select)
                summary = app.query_one("#summary", Static)

                for target in (OAUTH, BEDROCK):
                    provider.value = target
                    await pilot.pause()
                    seen[target] = (
                        judges.value,
                        judges.disabled,
                        app.query_one("#oauth_account", Select).disabled,
                        str(summary.render()),
                    )
            return seen

        return asyncio.run(run())

    def test_switching_to_oauth_locks_judges_to_sonnet(self, monkeypatch):
        seen = self._drive(monkeypatch)
        value, disabled, _, _ = seen[OAUTH]
        assert value == "sonnet"
        assert disabled is True, "OAuth roster is fixed; the control should be locked"

    def test_switching_to_bedrock_restores_full_council(self, monkeypatch):
        seen = self._drive(monkeypatch)
        value, disabled, _, _ = seen[BEDROCK]
        assert value == "sonnet,glm,kimi"
        assert disabled is False

    def test_oauth_account_enabled_only_under_oauth(self, monkeypatch):
        seen = self._drive(monkeypatch)
        assert seen[OAUTH][2] is False, "account picker must be usable under OAuth"
        assert seen[BEDROCK][2] is True, "account picker is meaningless under Bedrock"

    def test_summary_tracks_the_selected_provider(self, monkeypatch):
        seen = self._drive(monkeypatch)
        assert "OAuth" in seen[OAUTH][3] and "Claude Sonnet" in seen[OAUTH][3]
        assert "GLM" not in seen[OAUTH][3], "OAuth summary must not advertise GLM"
        assert "AWS Bedrock" in seen[BEDROCK][3] and "GLM" in seen[BEDROCK][3]
