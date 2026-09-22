"""Cost is priced per lane: agent by the agent's provider, judge by the judge's.

The invariants, each asserted below:

  I1  lane purity      no agent token is priced by the judge's route, and none
                       of the judge's by the agent's
  I2  no double count  recompute_combined sums each source exactly once and
                       repricing mutates in place, never adds a source
  I3  no silent $0     an OAuth-routed source carries an imputed cost, a
                       Bedrock-routed one its recorded cost
  I4  stamp truth      every artifact that names a provider names the provider
                       of the tokens it describes
  I5  bundle shape     report.json gains nothing; the judge stamp rides
                       usage.json, which the bundler copies verbatim

Note on what the two-gate split is FOR. Both rate cards carry the same
published sonnet numbers -- oauth_pricing.SONNET_RATES mirrors
grading._FAMILY_RATES["sonnet"] and the module says so -- so a sonnet judge
prices identically on either lane today. The split buys lane purity and
survives a card divergence; it is not a dollar correction, and
test_both_lanes_price_a_sonnet_judge_identically in
test_dual_provider_judge_lane.py pins that fact so nobody hunts a phantom.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run_batch import _lane_routes, recompute_combined, save_usage  # noqa: E402
from src.utils import auth_provider as ap  # noqa: E402
from src.utils.oauth_pricing import reprice_oauth_sources  # noqa: E402

_ENV = ("WCB_AUTH_PROVIDER", "WCB_JUDGE_AUTH_PROVIDER",
        "WCB_USE_CLAUDE_OAUTH", "WCB_CC_ACCOUNT_POOL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _sources(agent_cost=0.0, judge_cost=0.0):
    return {
        "agent": {
            "input_tokens": 100_000, "output_tokens": 5_000,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "total_tokens": 105_000, "request_count": 20, "cost_usd": agent_cost,
        },
        "judge": {
            "input_tokens": 40_000, "output_tokens": 2_000,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "total_tokens": 42_000, "request_count": 3, "cost_usd": judge_cost,
            "per_member": {
                "sonnet": {
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 40_000, "output_tokens": 2_000,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_tokens": 42_000, "request_count": 3,
                    "cost_usd": judge_cost, "cost_priced_ok": True, "ok": True,
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# reprice_oauth_sources -- the two gates
# ---------------------------------------------------------------------------


class TestRepriceTwoGates:
    def test_none_means_same_as_the_agent(self):
        """The byte-identical default for every existing caller."""
        for route in (True, False):
            a, b = _sources(), _sources()
            single = reprice_oauth_sources(a, model="claude-opus-4-6", oauth_route=route)
            paired = reprice_oauth_sources(
                b, model="claude-opus-4-6", oauth_route=route, judge_oauth_route=None,
            )
            assert single == paired
            assert a == b

    def test_agent_oauth_judge_bedrock_leaves_the_judge_alone(self):
        s = _sources(agent_cost=0.0, judge_cost=1.2345)
        reprice_oauth_sources(s, model="claude-opus-4-6",
                              oauth_route=True, judge_oauth_route=False)
        assert s["agent"]["cost_usd"] > 0.0
        assert s["judge"]["cost_usd"] == 1.2345
        assert s["judge"]["per_member"]["sonnet"]["cost_usd"] == 1.2345

    def test_agent_bedrock_judge_oauth_reprices_only_the_judge(self):
        s = _sources(agent_cost=7.5, judge_cost=0.0)
        reprice_oauth_sources(s, model="claude-opus-4-6",
                              oauth_route=False, judge_oauth_route=True)
        assert s["agent"]["cost_usd"] == 7.5
        assert s["judge"]["cost_usd"] > 0.0, "a prepaid judge must not stay at $0"
        assert s["judge"]["per_member"]["sonnet"]["cost_usd"] > 0.0

    def test_both_bedrock_touches_nothing(self):
        s = _sources(agent_cost=7.5, judge_cost=1.25)
        assert reprice_oauth_sources(s, model="claude-opus-4-6",
                                     oauth_route=False, judge_oauth_route=False) == []
        assert s["agent"]["cost_usd"] == 7.5
        assert s["judge"]["cost_usd"] == 1.25

    def test_repricing_never_adds_a_source(self):
        s = _sources()
        before = set(s)
        reprice_oauth_sources(s, model="claude-opus-4-6",
                              oauth_route=True, judge_oauth_route=True)
        assert set(s) == before

    def test_never_raises_on_garbage(self):
        assert reprice_oauth_sources({"agent": "nonsense", "judge": 7},
                                     oauth_route=True, judge_oauth_route=True) == []


# ---------------------------------------------------------------------------
# _lane_routes -- derived from the resolved providers (M0-a)
# ---------------------------------------------------------------------------


class TestLaneRoutes:
    def test_auth_provider_flag_and_legacy_flag_now_agree(self, clean_env):
        """M0-a. `--auth-provider oauth` with no WCB_USE_CLAUDE_OAUTH in .env
        used to stamp auth_provider:"bedrock" and skip OAuth imputation, because
        the route boolean read config.use_claude_oauth rather than the resolved
        provider. script/run.sh always passes --use-claude-oauth, which is why
        this never bit in production. Intentional cost-correcting delta."""
        flagged = SimpleNamespace(auth_provider=ap.OAUTH, use_claude_oauth=None,
                                  judge_auth_provider=None)
        legacy = SimpleNamespace(auth_provider=None, use_claude_oauth=True,
                                 judge_auth_provider=None)
        assert _lane_routes(flagged) == _lane_routes(legacy) == (True, True)

    def test_mixed_lanes(self, clean_env):
        args = SimpleNamespace(auth_provider=ap.OAUTH, use_claude_oauth=None,
                               judge_auth_provider=ap.BEDROCK)
        assert _lane_routes(args) == (True, False)
        args = SimpleNamespace(auth_provider=ap.BEDROCK, use_claude_oauth=None,
                               judge_auth_provider=ap.OAUTH)
        assert _lane_routes(args) == (False, True)

    def test_reads_the_exported_env_with_no_args(self, clean_env):
        clean_env.setenv("WCB_AUTH_PROVIDER", ap.OAUTH)
        clean_env.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.BEDROCK)
        assert _lane_routes() == (True, False)


# ---------------------------------------------------------------------------
# save_usage -- the conditional stamp (I4 / I5)
# ---------------------------------------------------------------------------


def _save(tmp_path, **kw):
    out_dir = tmp_path / "run_1"
    out_dir.mkdir(exist_ok=True)
    result = save_usage(
        out_dir, {}, _sources()["agent"], "task",
        judge_usage=_sources()["judge"], model="claude-opus-4-6", **kw,
    )
    return result["usage"] if "usage" in result else result


class TestUsageStamp:
    def test_no_judge_stamp_when_the_lanes_match(self, tmp_path):
        for route in (True, False):
            usage = _save(tmp_path, oauth_route=route)
            assert "judge_auth_provider" not in usage
            assert usage["auth_provider"] == (ap.OAUTH if route else ap.BEDROCK)

    def test_no_judge_stamp_when_judge_route_is_none(self, tmp_path):
        usage = _save(tmp_path, oauth_route=True, judge_oauth_route=None)
        assert "judge_auth_provider" not in usage

    def test_stamp_on_a_mixed_run(self, tmp_path):
        usage = _save(tmp_path, oauth_route=True, judge_oauth_route=False)
        assert usage["auth_provider"] == ap.OAUTH
        assert usage["judge_auth_provider"] == ap.BEDROCK

        usage = _save(tmp_path, oauth_route=False, judge_oauth_route=True)
        assert usage["auth_provider"] == ap.BEDROCK
        assert usage["judge_auth_provider"] == ap.OAUTH

    def test_auth_provider_still_names_the_agent_lane(self, tmp_path):
        """script/regrade.py and external audit already read it that way."""
        usage = _save(tmp_path, oauth_route=False, judge_oauth_route=True)
        assert usage["auth_provider"] == ap.BEDROCK

    def test_combined_counts_each_source_exactly_once(self, tmp_path):
        usage = _save(tmp_path, oauth_route=True, judge_oauth_route=False)
        srcs = usage["sources"]
        assert usage["total_tokens"] == sum(s["total_tokens"] for s in srcs.values())
        assert round(usage["cost_usd"], 6) == round(
            sum(float(s["cost_usd"]) for s in srcs.values()), 6)

    def test_judge_cost_is_recorded_verbatim_on_a_bedrock_judge(self, tmp_path):
        out_dir = tmp_path / "run_2"
        out_dir.mkdir()
        judge = _sources(judge_cost=1.2345)["judge"]
        result = save_usage(out_dir, {}, _sources()["agent"], "task",
                            judge_usage=judge, model="claude-opus-4-6",
                            oauth_route=True, judge_oauth_route=False)
        assert result["usage"]["sources"]["judge"]["cost_usd"] == 1.2345
        assert result["usage"]["sources"]["agent"]["cost_usd"] > 0.0


def test_report_json_key_set_is_unchanged_by_a_mixed_run():
    """I5: the client bundle's report.json is a closed dict literal and gains no
    key. The judge stamp rides usage.json, which the bundler copies verbatim."""
    src = (Path(__file__).resolve().parents[1]
           / "script" / "repackage_to_bundle.py").read_text(encoding="utf-8")
    assert "judge_auth_provider" not in src


def test_finance_payload_keys_are_unchanged():
    """Oracle RECOMMENDED R-b: no judge_oauth_route passthrough was threaded
    through finance. _judge_cost_usd returns a truthy recorded cost verbatim and
    only estimates when it is falsy, which is already correct on both lanes by
    the time finance runs -- save_usage repriced (or deliberately did not)
    first. An unused parameter would have cost two signature changes and a test
    for nothing."""
    import inspect

    from src.utils import finance_api
    from src.utils.finance_api import FinanceSettings, build_trajectory_usage_payload

    settings = FinanceSettings(base_url="http://x", api_token="k", project_id="p")

    payload = build_trajectory_usage_payload(
        settings, task_id="t", trajectory_id="tr", model_name="claude-opus-4-6",
        usage={"sources": _sources()}, oauth_route=True,
    )
    assert "judge_auth_provider" not in payload
    assert "judge_oauth_route" not in payload
    for fn in (finance_api.build_trajectory_usage_payload,
               finance_api.record_trajectory_usage):
        assert "judge_oauth_route" not in inspect.signature(fn).parameters


def test_recompute_combined_is_a_plain_sum():
    s = _sources(agent_cost=1.0, judge_cost=2.0)
    combined = recompute_combined(s, task_id="t")
    assert combined["total_tokens"] == 105_000 + 42_000
    assert round(combined["cost_usd"], 6) == 3.0
