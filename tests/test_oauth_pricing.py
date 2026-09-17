from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.oauth_pricing import (  # noqa: E402
    BEDROCK_MODEL_CARD_RATES,
    FABLE_RATES,
    OPUS_RATES,
    SONNET_RATES,
    ModelRates,
    cost_breakdown,
    estimate_cost_usd,
    rates_for,
    reprice_oauth_sources,
)

OAUTH_TRAJECTORY_MODELS = [
    "claude-opus-5",
    "claude-opus-4.7",
    "claude-opus-4-6",
    "claude-fable-5",
]
OAUTH_JUDGE_MODELS = [
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "sonnet",
]


class TestRateTable:
    def test_table_covers_exactly_the_oauth_served_models(self):
        assert set(BEDROCK_MODEL_CARD_RATES) == set(
            OAUTH_TRAJECTORY_MODELS + OAUTH_JUDGE_MODELS
        )

    def test_bedrock_only_models_are_absent(self):
        # These ids never reach the cc-bridge, so they must stay unpriceable:
        # a rate card here would let a caller reprice a real Bedrock charge.
        for model in ("claude-opus-4.8", "gpt-5.5", "gpt-5.6", "glassy_lagoon"):
            assert model not in BEDROCK_MODEL_CARD_RATES

    @pytest.mark.parametrize("model", OAUTH_JUDGE_MODELS)
    def test_sonnet_judge_ids_share_the_sonnet_card(self, model):
        # The bare "sonnet" family key matters: _readable_model swaps a Bedrock
        # ARN for the family, so that is the name the judge repricer looks up.
        assert BEDROCK_MODEL_CARD_RATES[model] == SONNET_RATES

    @pytest.mark.parametrize("model", ["claude-opus-5", "claude-opus-4.7", "claude-opus-4-6"])
    def test_opus_ids_share_the_opus_card(self, model):
        assert BEDROCK_MODEL_CARD_RATES[model] == OPUS_RATES

    def test_fable_has_its_own_card(self):
        assert BEDROCK_MODEL_CARD_RATES["claude-fable-5"] == FABLE_RATES

    def test_cache_rates_follow_the_anthropic_ratios(self):
        for rates in (OPUS_RATES, FABLE_RATES, SONNET_RATES):
            assert rates.cache_read_per_mtok == pytest.approx(rates.input_per_mtok * 0.1)
            assert rates.cache_write_per_mtok == pytest.approx(rates.input_per_mtok * 1.25)

    def test_published_list_prices(self):
        assert (OPUS_RATES.input_per_mtok, OPUS_RATES.output_per_mtok) == (5.0, 25.0)
        assert (FABLE_RATES.input_per_mtok, FABLE_RATES.output_per_mtok) == (10.0, 50.0)
        assert (SONNET_RATES.input_per_mtok, SONNET_RATES.output_per_mtok) == (3.0, 15.0)

    def test_the_card_covers_every_oauth_served_trajectory_model(self):
        from src.utils.auth_provider import OAUTH, served_trajectory_models

        class _Cfg:
            aws_bearer_token = ""
            anthropic_api_key = ""
            openai_api_key = ""
            meta_api_key = ""
            meta_model = ""

        for model in served_trajectory_models(OAUTH, _Cfg()):
            assert rates_for(model) is not None, model

    def test_the_card_covers_the_cc_bridge_judge_default(self):
        from src.utils import judge_litellm

        assert rates_for(judge_litellm._judge_oauth_bridge_model()) == SONNET_RATES

    def test_the_sonnet_card_matches_the_council_rate_table(self):
        # Two tables price the same sonnet judge — grading's council rates on the
        # Bedrock route, this card on the OAuth one. A divergence would make the
        # same tokens cost different dollars depending on how they were routed.
        from src.utils.grading import _FAMILY_RATES

        r_in, r_out, r_cread, r_cwrite = _FAMILY_RATES["sonnet"]
        assert r_in * 1_000_000 == pytest.approx(SONNET_RATES.input_per_mtok)
        assert r_out * 1_000_000 == pytest.approx(SONNET_RATES.output_per_mtok)
        assert r_cread * 1_000_000 == pytest.approx(SONNET_RATES.cache_read_per_mtok)
        assert r_cwrite * 1_000_000 == pytest.approx(SONNET_RATES.cache_write_per_mtok)


class TestCostArithmetic:
    def test_rates_are_per_million_tokens(self):
        rates = ModelRates(10.0, 20.0, 1.0, 2.0)
        cost = rates.cost_usd(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        assert cost == pytest.approx(10.0)

    def test_every_token_class_contributes(self):
        rates = ModelRates(1.0, 2.0, 4.0, 8.0)
        cost = rates.cost_usd(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        assert cost == pytest.approx(15.0)

    def test_zero_tokens_cost_nothing(self):
        cost, priced_ok = estimate_cost_usd("claude-opus-5")
        assert (cost, priced_ok) == (0.0, True)


class TestEstimate:
    def test_known_model_is_priced(self):
        cost, priced_ok = estimate_cost_usd(
            "claude-opus-5",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        assert priced_ok is True
        assert cost == pytest.approx(30.0)

    def test_unknown_model_is_not_priced(self):
        # Signals "no rate card" rather than "free", so the caller can keep the
        # recorded figure instead of publishing a fabricated zero.
        cost, priced_ok = estimate_cost_usd("some-future-model", input_tokens=1_000_000)
        assert (cost, priced_ok) == (0.0, False)

    def test_junk_tokens_decline_to_price_instead_of_raising(self):
        cost, priced_ok = estimate_cost_usd(
            "claude-opus-5",
            input_tokens="not-a-number",  # type: ignore[arg-type]
        )
        assert (cost, priced_ok) == (0.0, False)

    def test_negative_tokens_cannot_produce_a_credit(self):
        cost, _ = estimate_cost_usd("claude-opus-5", input_tokens=-1_000_000)
        assert cost >= 0.0

    def test_real_trajectory_numbers_are_plausible(self):
        # The shape of the completed run_5 trajectory: cache-read dominated.
        cost, priced_ok = estimate_cost_usd(
            "claude-opus-4.7",
            input_tokens=225,
            output_tokens=105_837,
            cache_read_tokens=8_650_486,
            cache_write_tokens=712_926,
        )
        assert priced_ok is True
        assert 10.0 < cost < 15.0


class TestFamilyFallback:
    def test_unlisted_opus_alias_falls_back_to_the_opus_card(self):
        assert rates_for("claude-opus-9-experimental") == OPUS_RATES

    def test_unlisted_fable_alias_falls_back_to_the_fable_card(self):
        assert rates_for("anthropic/claude-fable-7") == FABLE_RATES

    def test_exact_match_wins_over_family_fallback(self):
        assert rates_for("claude-fable-5") == FABLE_RATES

    def test_unrelated_model_has_no_rates(self):
        assert rates_for("gpt-5.5") is None


class TestEnvOverrides:
    def test_override_replaces_a_single_field(self, monkeypatch):
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_OUTPUT_PER_MTOK", "30")
        rates = rates_for("claude-opus-5")
        assert rates is not None
        assert rates.output_per_mtok == pytest.approx(30.0)
        assert rates.input_per_mtok == pytest.approx(OPUS_RATES.input_per_mtok)

    def test_override_changes_the_estimate(self, monkeypatch):
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK", "7.5")
        cost, priced_ok = estimate_cost_usd("claude-opus-5", input_tokens=1_000_000)
        assert priced_ok is True
        assert cost == pytest.approx(7.5)

    def test_non_numeric_override_is_ignored(self, monkeypatch):
        # A typo must not silently zero a trajectory's cost.
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK", "five dollars")
        rates = rates_for("claude-opus-5")
        assert rates is not None
        assert rates.input_per_mtok == pytest.approx(OPUS_RATES.input_per_mtok)

    def test_negative_override_is_ignored(self, monkeypatch):
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK", "-5")
        rates = rates_for("claude-opus-5")
        assert rates is not None
        assert rates.input_per_mtok == pytest.approx(OPUS_RATES.input_per_mtok)

    def test_override_is_scoped_to_one_model(self, monkeypatch):
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK", "99")
        assert rates_for("claude-fable-5").input_per_mtok == pytest.approx(
            FABLE_RATES.input_per_mtok
        )

    def test_module_defaults_are_not_mutated_by_an_override(self, monkeypatch):
        monkeypatch.setenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK", "99")
        rates_for("claude-opus-5")
        monkeypatch.delenv("WCB_OAUTH_PRICE_CLAUDE_OPUS_5_INPUT_PER_MTOK")
        assert rates_for("claude-opus-5").input_per_mtok == pytest.approx(5.0)


class TestCostBreakdown:
    def test_split_is_priced_per_class_and_sums_to_the_total(self):
        split = cost_breakdown(
            "claude-opus-5",
            input_tokens=1_000,
            output_tokens=2_000,
            cache_read_tokens=10_000,
            cache_write_tokens=4_000,
        )
        assert split == {
            "input": pytest.approx(0.005),
            "output": pytest.approx(0.05),
            "cacheRead": pytest.approx(0.005),
            "cacheWrite": pytest.approx(0.025),
            "total": pytest.approx(0.085),
        }

    def test_total_agrees_with_estimate_cost_usd(self):
        tokens = dict(
            input_tokens=544,
            output_tokens=110_161,
            cache_read_tokens=10_708_536,
            cache_write_tokens=815_388,
        )
        estimated, _ = estimate_cost_usd("claude-opus-5", **tokens)
        assert cost_breakdown("claude-opus-5", **tokens)["total"] == pytest.approx(
            estimated
        )

    def test_unpriceable_model_declines_instead_of_splitting_zero(self):
        assert cost_breakdown("gpt-5.5", input_tokens=1_000_000) is None

    def test_junk_tokens_decline_instead_of_raising(self):
        assert cost_breakdown("claude-opus-5", input_tokens="lots") is None  # type: ignore[arg-type]


class TestRepriceSources:
    @staticmethod
    def _sources(agent_cost, judge_cost, member_cost, member_model="sonnet"):
        return {
            "agent": {
                "input_tokens": 544,
                "output_tokens": 110161,
                "cache_read_tokens": 10708536,
                "cache_write_tokens": 815388,
                "cost_usd": agent_cost,
            },
            "judge": {
                "cost_usd": judge_cost,
                "per_member": {
                    "sonnet": {
                        "model": member_model,
                        "input_tokens": 101885,
                        "output_tokens": 1267,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "cost_usd": member_cost,
                    }
                },
            },
        }

    # A Bedrock run must survive this untouched. Its agent cost is small but
    # truthy, so only the oauth_route flag distinguishes it from a subscription
    # run -- the gate this asserts is the whole Bedrock-safety guarantee.
    def test_bedrock_sources_are_left_alone(self):
        sources = self._sources(0.000265, 0.421758, 0.421758)
        changed = reprice_oauth_sources(
            sources, model="claude-opus-4.7", oauth_route=False
        )
        assert changed == []
        assert sources["agent"]["cost_usd"] == pytest.approx(0.000265)
        assert sources["judge"]["cost_usd"] == pytest.approx(0.421758)

    # A prepaid subscription is booked at rounding noise (6e-06 on run_6), not a
    # clean 0.0, so a falsy-cost gate would never fire on the agent at all.
    def test_oauth_agent_is_repriced_despite_a_truthy_noise_cost(self):
        sources = self._sources(6e-06, 0.0, 0.0)
        changed = reprice_oauth_sources(
            sources, model="claude-opus-5", oauth_route=True
        )
        expected, priced_ok = estimate_cost_usd(
            "claude-opus-5",
            input_tokens=544,
            output_tokens=110161,
            cache_read_tokens=10708536,
            cache_write_tokens=815388,
        )
        assert priced_ok is True
        assert sources["agent"]["cost_usd"] == pytest.approx(expected)
        assert sources["agent"]["cost_usd"] > 13.0
        assert any("agent" in c for c in changed)

    def test_oauth_judge_member_and_aggregate_are_repriced(self):
        sources = self._sources(6e-06, 0.0, 0.0)
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        member = sources["judge"]["per_member"]["sonnet"]["cost_usd"]
        assert member == pytest.approx(0.32466)
        assert sources["judge"]["cost_usd"] == pytest.approx(member)

    def test_judge_member_recorded_as_an_arn_prices_via_the_family_key(self):
        arn = "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/x"
        sources = self._sources(6e-06, 0.0, 0.0, member_model=arn)
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        assert sources["judge"]["per_member"]["sonnet"]["cost_usd"] > 0.0

    # The figure an OAuth judge arrives with is whatever convention priced it
    # (list price on the batch path, $0 on the pre-change regrade path). Both are
    # overwritten: one route, one derivation, one number.
    def test_oauth_judge_list_price_is_overwritten_by_the_derived_figure(self):
        sources = self._sources(6e-06, 0.421758, 0.421758)
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        assert sources["judge"]["per_member"]["sonnet"]["cost_usd"] == pytest.approx(
            0.32466
        )
        assert sources["judge"]["cost_usd"] == pytest.approx(0.32466)

    def test_oauth_judge_without_per_member_reprices_off_its_own_totals(self):
        sources = {
            "agent": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            "judge": {
                "model": "claude-sonnet-5",
                "input_tokens": 20_000,
                "output_tokens": 1_000,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 4.2,
            },
        }
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        assert sources["judge"]["cost_usd"] == pytest.approx(0.075)

    def test_repricing_is_idempotent(self):
        # finance_api derives the same figure from the same card and tokens, so
        # a second pass must not compound the first.
        sources = self._sources(6e-06, 0.0, 0.0)
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        once = json.loads(json.dumps(sources))
        reprice_oauth_sources(sources, model="claude-opus-5", oauth_route=True)
        assert sources == once

    def test_unpriceable_model_leaves_the_agent_cost_alone(self):
        sources = self._sources(6e-06, 0.0, 0.0)
        reprice_oauth_sources(sources, model="glassy_lagoon", oauth_route=True)
        assert sources["agent"]["cost_usd"] == pytest.approx(6e-06)

    def test_junk_sources_never_raise(self):
        assert reprice_oauth_sources({}, model="claude-opus-5", oauth_route=True) == []
        assert reprice_oauth_sources(
            {"agent": "not-a-dict", "judge": 7}, model="claude-opus-5", oauth_route=True
        ) == []


class TestJudgeCostOnTheOAuthBridge:
    """``grading._judge_cost_usd`` is the single writer of the judge's dollars
    on both the batch and the regrade path, so the route's pricing policy has to
    live there rather than in either caller."""

    @staticmethod
    def _bridge(monkeypatch, on):
        from src.utils.auth_provider import BEDROCK, OAUTH, PROVIDER_ENV_VAR

        monkeypatch.setenv(PROVIDER_ENV_VAR, OAUTH if on else BEDROCK)
        monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:8787")

    def test_bridge_judge_is_priced_at_the_sonnet_card_not_zero(self, monkeypatch):
        from src.utils import grading

        self._bridge(monkeypatch, True)
        cost, priced_ok = grading._judge_cost_usd(
            "claude-sonnet-5", 20_000, 1_000, 0, 0, family="sonnet"
        )
        assert priced_ok is True
        assert cost == pytest.approx(0.075)

    def test_bedrock_judge_keeps_the_council_rate(self, monkeypatch):
        from src.utils import grading

        self._bridge(monkeypatch, False)
        cost, priced_ok = grading._judge_cost_usd(
            "claude-sonnet-4-6", 20_000, 1_000, 0, 0, family="sonnet"
        )
        assert priced_ok is True
        assert cost == pytest.approx(0.075)

    def test_both_routes_price_the_same_tokens_the_same(self, monkeypatch):
        from src.utils import grading

        self._bridge(monkeypatch, True)
        oauth_cost, _ = grading._judge_cost_usd(
            "claude-sonnet-5", 101_885, 1_267, 0, 0, family="sonnet"
        )
        self._bridge(monkeypatch, False)
        bedrock_cost, _ = grading._judge_cost_usd(
            "claude-sonnet-4-6", 101_885, 1_267, 0, 0, family="sonnet"
        )
        assert oauth_cost == pytest.approx(bedrock_cost) == pytest.approx(0.32466)
