"""Dual-provider mode: the judge lane's gates, keyed on WCB_JUDGE_AUTH_PROVIDER.

Six gates read the judge lane, directly or through the bridge-URL gate, and all
six are pinned here because three of them decide MONEY or ARTIFACT VALUES:

  1. judge_litellm._judge_oauth_bridge_url   the transport
  2. judge_litellm.call_judge_via_litellm    the completion kwargs
  3. grading._member_evidence_budget         the yves_quinn root cause
  4. grading._judge_cost_usd                 which rate card prices the judge
  5. grading._effective_judge_model          score.json / finance model names
  6. grading._call_one_judge                 the no-fallback guard

The load-bearing case is test_bridge_url_raw_read_is_preserved_for_regrade: the
bridge gate was a RAW read of WCB_AUTH_PROVIDER, and script/regrade.py never
exports it. Resolving the judge lane with resolve_provider()'s legacy inference
would have re-armed the 700,000-char OAuth clamp on exactly the path that
rescues an oversize transcript.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import auth_provider as ap  # noqa: E402
from src.utils import grading, judge_litellm  # noqa: E402

SONNET_ARN = (
    "bedrock/arn:aws:bedrock:ap-south-1:426628337772:"
    "application-inference-profile/is9bst5tfadh"
)
BRIDGE = "http://127.0.0.1:34567"

_ENV = (
    "WCB_AUTH_PROVIDER",
    "WCB_JUDGE_AUTH_PROVIDER",
    "WCB_USE_CLAUDE_OAUTH",
    "WCB_CC_ACCOUNT_POOL",
    "KENSEI_JUDGE_OAUTH_BRIDGE_URL",
    "KENSEI_JUDGE_OAUTH_BRIDGE_MODEL",
    "KENSEI_JUDGE_USE_LITELLM",
    "KENSEI_JUDGE_OAUTH_MAX_EVIDENCE",
    "JUDGE_MAX_EVIDENCE",
    "JUDGE_COUNCIL_MEMBERS",
    "JUDGE_COUNCIL_SONNET_ARN",
    "JUDGE_COUNCIL_GLM_ARN",
    "JUDGE_COUNCIL_KIMI_ARN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _lanes(env, agent=None, judge=None, bridge=True):
    if agent is None:
        env.delenv("WCB_AUTH_PROVIDER", raising=False)
    else:
        env.setenv("WCB_AUTH_PROVIDER", agent)
    if judge is None:
        env.delenv("WCB_JUDGE_AUTH_PROVIDER", raising=False)
    else:
        env.setenv("WCB_JUDGE_AUTH_PROVIDER", judge)
    if bridge:
        env.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", BRIDGE)
    else:
        env.delenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", raising=False)


# ===========================================================================
# Gate 1 -- the bridge URL
# ===========================================================================


class TestBridgeUrlGate:
    def test_empty_when_judge_lane_is_bedrock(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert judge_litellm._judge_oauth_bridge_url() == ""

    def test_present_when_judge_lane_is_oauth_on_a_bedrock_agent(self, clean_env):
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert judge_litellm._judge_oauth_bridge_url() == BRIDGE

    def test_cp4_preserved_when_judge_unset_on_a_bedrock_run(self, clean_env):
        _lanes(clean_env, agent=ap.BEDROCK)
        assert judge_litellm._judge_oauth_bridge_url() == ""

    def test_unset_judge_var_follows_the_agent(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH)
        assert judge_litellm._judge_oauth_bridge_url() == BRIDGE

    def test_bridge_url_raw_read_is_preserved_for_regrade(self, clean_env):
        """THE byte-identical lock (Oracle REQUIRED #3).

        The regrade .env shape: WCB_AUTH_PROVIDER unset, WCB_USE_CLAUDE_OAUTH=1,
        a pool configured, and a bridge URL in the environment. resolve_provider()
        infers OAUTH here -- but bbbcec0's gate was a raw env read and answered
        "not oauth", so the clamp never fired. That must stay true.
        """
        clean_env.setenv("WCB_USE_CLAUDE_OAUTH", "1")
        clean_env.setenv("WCB_CC_ACCOUNT_POOL", "/pool/a.json")
        clean_env.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", BRIDGE)
        assert ap.resolve_provider() == ap.OAUTH
        assert judge_litellm._judge_oauth_bridge_url() == ""
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 1_175_000

    def test_gate_never_raises_on_a_typo(self, clean_env):
        """Two callers swallow exceptions and a third is reached from inside an
        exception handler, so a raise here would fail OPEN."""
        clean_env.setenv("WCB_JUDGE_AUTH_PROVIDER", "bedrok")
        clean_env.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", BRIDGE)
        assert judge_litellm._judge_oauth_bridge_url() == ""


# ===========================================================================
# Gate 3 -- the evidence budget (the yves_quinn root cause)
# ===========================================================================


class TestEvidenceBudgetLane:
    def test_oauth_cap_applied_when_judge_lane_is_oauth(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 700_000

    def test_oauth_cap_NOT_applied_when_judge_lane_is_bedrock(self, clean_env):
        """The yves lock: the bridge URL is STILL exported (the agent needs it),
        yet a Bedrock judge grades at the sonnet family's full budget."""
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert judge_litellm._judge_oauth_bridge_url() == ""
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 1_175_000

    def test_oauth_cap_applied_when_judge_oauth_on_bedrock_agent(self, clean_env):
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 700_000

    def test_bedrock_only_run_unchanged(self, clean_env):
        _lanes(clean_env, agent=ap.BEDROCK)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 1_175_000

    def test_JUDGE_MAX_EVIDENCE_override_still_wins_on_both_lanes(self, clean_env):
        """Oracle RECOMMENDED R-d: this override is checked FIRST and silently
        bypasses the family table, so a box that has it set does not get the fix."""
        clean_env.setenv("JUDGE_MAX_EVIDENCE", "123456")
        for judge in (ap.OAUTH, ap.BEDROCK):
            _lanes(clean_env, agent=ap.OAUTH, judge=judge)
            assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 123_456

    def test_oauth_max_evidence_tunable_only_affects_the_oauth_lane(self, clean_env):
        clean_env.setenv("KENSEI_JUDGE_OAUTH_MAX_EVIDENCE", "500000")
        _lanes(clean_env, agent=ap.OAUTH)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 500_000
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") == 1_175_000

    def test_yves_sized_transcript_fits_on_bedrock_and_not_on_oauth(self, clean_env):
        yves_chars = 787_562
        _lanes(clean_env, agent=ap.OAUTH)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") < yves_chars
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert grading._member_evidence_budget(SONNET_ARN, "sonnet") > yves_chars


# ===========================================================================
# Gates 4 and 5 -- cost card and artifact model name
# ===========================================================================


class TestCostAndModelNameLane:
    def test_effective_model_is_the_arn_on_a_bedrock_judge(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert grading._effective_judge_model(SONNET_ARN, "sonnet") == SONNET_ARN

    def test_effective_model_is_the_bridge_id_on_an_oauth_judge(self, clean_env):
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert grading._effective_judge_model(SONNET_ARN, "sonnet") == "claude-sonnet-4-6"

    def test_finance_readable_model_keeps_a_stable_name_on_both_lanes(self, clean_env):
        """The artifact VALUE flips (ARN vs bridge id), but the finance payload's
        model_name stays a readable family label either way -- the external
        contract holds without lying about which endpoint answered."""
        from src.utils.finance_api import _readable_model

        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert _readable_model(grading._effective_judge_model(SONNET_ARN, "sonnet"),
                               "sonnet") == "sonnet"
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert _readable_model(grading._effective_judge_model(SONNET_ARN, "sonnet"),
                               "sonnet") == "claude-sonnet-4-6"

    def test_both_lanes_price_a_sonnet_judge_identically(self, clean_env):
        """Oracle RECOMMENDED R-a: there is no '$0 free judge' and no 'real
        billed dollars'. oauth_pricing.SONNET_RATES and grading._FAMILY_RATES
        carry the SAME published numbers, so the lane split buys purity, not a
        different figure."""
        _lanes(clean_env, agent=ap.OAUTH)
        oauth_cost, oauth_ok = grading._judge_cost_usd(SONNET_ARN, 1000, 100, 0, 0, "sonnet")
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        bedrock_cost, bedrock_ok = grading._judge_cost_usd(SONNET_ARN, 1000, 100, 0, 0, "sonnet")
        assert oauth_ok and bedrock_ok
        assert oauth_cost > 0.0
        assert round(oauth_cost, 9) == round(bedrock_cost, 9)


# ===========================================================================
# Gate 6 -- no fallback, and no silent provider crossing
# ===========================================================================


class TestNoFallbackLane:
    def _poison_litellm(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("bridge exploded")

        monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _boom)

    def test_oauth_judge_reraises_instead_of_dialing_bedrock(self, clean_env, monkeypatch):
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        clean_env.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
        self._poison_litellm(monkeypatch)
        monkeypatch.setattr(
            grading, "_call_judge_bedrock",
            lambda *a, **k: pytest.fail("OAuth judge must never reach Bedrock"),
        )
        with pytest.raises(RuntimeError, match="bridge exploded"):
            grading._call_one_judge(SONNET_ARN, "sys", "user", "sonnet")

    def test_bedrock_judge_on_an_oauth_agent_may_still_use_the_urllib_transport(
        self, clean_env, monkeypatch
    ):
        """Same provider, same bearer, different wire -- crosses no boundary, and
        grading must never die of a transport choice (m0039)."""
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        clean_env.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
        self._poison_litellm(monkeypatch)
        sentinel = ("verdicts", {"request_count": 1})
        monkeypatch.setattr(grading, "_call_judge_bedrock", lambda *a, **k: sentinel)
        assert grading._call_one_judge(SONNET_ARN, "sys", "user", "sonnet") == sentinel

    def test_oauth_judge_without_a_bridge_fails_loud(self, clean_env, monkeypatch):
        """Oracle REQUIRED #2b. With no bridge URL the override block is skipped
        and the completion would go out as bedrock/arn -- billed, unlogged, and
        invisible in every artifact. Refuse instead."""
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH, bridge=False)
        monkeypatch.setattr(
            grading, "_call_judge_bedrock",
            lambda *a, **k: pytest.fail("must not silently grade on Bedrock"),
        )
        with pytest.raises(RuntimeError, match="judge lane is OAuth but no cc-bridge"):
            grading._call_one_judge(SONNET_ARN, "sys", "user", "sonnet")

    def test_bedrock_judge_without_a_bridge_is_untouched(self, clean_env, monkeypatch):
        _lanes(clean_env, agent=ap.BEDROCK, bridge=False)
        sentinel = ("verdicts", {"request_count": 1})
        monkeypatch.setattr(grading, "_call_judge_bedrock", lambda *a, **k: sentinel)
        assert grading._call_one_judge(SONNET_ARN, "sys", "user", "sonnet") == sentinel


# ===========================================================================
# Roster
# ===========================================================================


class TestCouncilRosterLane:
    @pytest.fixture(autouse=True)
    def _arns(self, clean_env):
        clean_env.setenv("JUDGE_COUNCIL_SONNET_ARN", "arn:s")
        clean_env.setenv("JUDGE_COUNCIL_GLM_ARN", "arn:g")
        clean_env.setenv("JUDGE_COUNCIL_KIMI_ARN", "arn:k")
        return clean_env

    def test_production_shape_only_sonnet_arn_stays_sonnet_only(self, _arns):
        for k in ("JUDGE_COUNCIL_GLM_ARN", "JUDGE_COUNCIL_KIMI_ARN"):
            _arns.delenv(k, raising=False)
        _lanes(_arns, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert [m.family for m in grading.council_members()] == ["sonnet"]

    def test_bedrock_judge_with_all_three_arns_enlists_all_three(self, _arns):
        _lanes(_arns, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert [m.family for m in grading.council_members()] == ["sonnet", "glm", "kimi"]

    def test_oauth_judge_on_a_bedrock_agent_drops_glm_and_kimi(self, _arns):
        _lanes(_arns, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert [m.family for m in grading.council_members()] == ["sonnet"]

    def test_empty_judge_roster_still_raises(self, _arns):
        _lanes(_arns, agent=ap.BEDROCK, judge=ap.OAUTH)
        _arns.setenv("JUDGE_COUNCIL_MEMBERS", "glm=arn:g,kimi=arn:k")
        with pytest.raises(RuntimeError, match="no usable judge remains"):
            grading.council_members()


# ===========================================================================
# The judge_council stamp -- BOTH builders
# ===========================================================================


class TestJudgeCouncilStamp:
    def test_absent_when_the_lanes_match(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.OAUTH)
        assert grading._judge_council_lane_stamp() == {}
        _lanes(clean_env, agent=ap.BEDROCK)
        assert grading._judge_council_lane_stamp() == {}

    def test_present_when_the_lanes_differ(self, clean_env):
        _lanes(clean_env, agent=ap.OAUTH, judge=ap.BEDROCK)
        assert grading._judge_council_lane_stamp() == {"judge_auth_provider": ap.BEDROCK}
        _lanes(clean_env, agent=ap.BEDROCK, judge=ap.OAUTH)
        assert grading._judge_council_lane_stamp() == {"judge_auth_provider": ap.OAUTH}

    def test_both_builders_stamp(self):
        """Oracle REQUIRED #7: judge_council is built twice -- once per chunk and
        once on the chunk-merge path -- and a large transcript (the yves shape)
        only ever takes the merge path."""
        src = Path(__file__).resolve().parents[1] / "src" / "utils" / "grading.py"
        text = src.read_text(encoding="utf-8")
        assert text.count('"judge_council": {') == 2
        assert text.count("**_judge_council_lane_stamp(),") == 2

    def test_stamp_never_raises(self, clean_env):
        clean_env.setenv("WCB_JUDGE_AUTH_PROVIDER", "bedrok")
        assert grading._judge_council_lane_stamp() == {}


# ===========================================================================
# Static: the lane vars are assigned in exactly one place each
# ===========================================================================


def test_lane_variables_are_only_ever_assigned_by_an_entry_point():
    """B.6's hard rule, mechanically enforced: never swap a lane variable around
    a judge call. At --parallel > 1 the worker threads share one os.environ, so
    a temporary swap would corrupt a sibling task's agent lane -- which is the
    whole reason the judge lane is a SECOND variable rather than a reassignment
    of the first.

    The invariant is not a head count, it is WHERE. src/ is the library those
    worker threads run inside and must only ever READ; the two CLI entry points
    resolve once, before any worker exists, and never write again.
    """
    root = Path(__file__).resolve().parents[1]
    entry_points = {"eval/run_batch.py", "script/regrade.py"}
    sites: dict[str, list[str]] = {}
    for f in sorted(root.glob("src/**/*.py")) + sorted(root.glob("eval/*.py")) + \
            sorted(root.glob("script/*.py")):
        rel = f.relative_to(root).as_posix()
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            for var in ("PROVIDER_ENV_VAR", "JUDGE_PROVIDER_ENV_VAR"):
                if stripped.startswith(f"os.environ[{var}]"):
                    sites.setdefault(rel, []).append(f"{var}:{i}")

    library_writes = {k: v for k, v in sites.items() if k.startswith("src/")}
    assert library_writes == {}, (
        f"src/ must never WRITE a lane variable, only read it: {library_writes}"
    )
    assert set(sites) <= entry_points, f"unexpected writer: {set(sites) - entry_points}"
    run_batch = sites.get("eval/run_batch.py", [])
    assert sorted(v.split(":")[0] for v in run_batch) == [
        "JUDGE_PROVIDER_ENV_VAR", "PROVIDER_ENV_VAR"
    ], run_batch


def test_judge_is_always_non_streaming_on_both_lanes(clean_env, monkeypatch):
    """The premise of the whole heartbeat proof: a judge request never takes the
    bridge's streaming branch, so it can never bump the shared lane-bridge file
    that holds a stalled agent's stall clock open."""
    seen: list[dict] = []
    fake = SimpleNamespace(
        completion=lambda **kw: (
            seen.append(kw),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="1. x"))],
                usage={"prompt_tokens": 10, "completion_tokens": 1},
            ),
        )[1],
        register_model=lambda *a, **k: None,
    )
    monkeypatch.setitem(sys.modules, "litellm", fake)
    for agent, judge in ((ap.OAUTH, ap.BEDROCK), (ap.BEDROCK, ap.OAUTH)):
        _lanes(clean_env, agent=agent, judge=judge)
        judge_litellm.call_judge_via_litellm(
            model=SONNET_ARN, system="s", user="u", max_output_tokens=16,
            cost_fn=lambda *a, **k: (0.0, True), family="sonnet",
        )
    assert len(seen) == 2
    assert all(kw["stream"] is False for kw in seen)
