"""Unit tests for the GPT-5.6 rubric judge (Waves 1-3 of
docs/GPT_JUDGE_IMPLEMENTATION_PLAN.md, Option A gated default).

Two concerns, deliberately separated:

* TRANSPORT — a `"gpt"`-family member dispatches through the OpenAI Chat
  Completions transport with `max_completion_tokens` + `reasoning_effort="low"`,
  authenticated by the DEDICATED judge key, and with `temperature`/`top_p`
  ABSENT (gpt-5.6 HTTP-400s on their mere presence, same as Sonnet 5 —
  AGENTS.md invariant 18).
* SELECTION SEAM — `grade_with_rubric` runs the GPT judge first when it is
  configured and falls back to the council on a no-signal result. With no GPT
  env vars the council path must be byte-identical to before.

No network: `urllib.request.urlopen` is monkeypatched with a fake SSE stream.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading, judge_litellm  # noqa: E402


GPT_MODEL = "gpt-5.6"
JUDGE_KEY = "sk-judge-gpt-key"
AGENT_KEY = "sk-agent-openai-key"

_GPT_ENV_VARS = (
    "KENSEI_JUDGE_GPT_API_KEY",
    "JUDGE_GPT_API_KEY",
    "KENSEI_JUDGE_GPT_MODEL",
    "JUDGE_GPT_MODEL",
    "JUDGE_GPT_PRIMARY",
)

_CODEX_ENV_VARS = (
    "KENSEI_JUDGE_CODEX_BRIDGE_URL",
    "KENSEI_JUDGE_CODEX_BRIDGE_MODEL",
    "KENSEI_JUDGE_CODEX_MAX_EVIDENCE",
    "WCB_CODEX_BRIDGE_SECRET",
)

CODEX_URL = "http://127.0.0.1:54321"
CODEX_SECRET = "sk-codex-bridge-secret"


@pytest.fixture(autouse=True)
def _clean_gpt_env(monkeypatch):
    """Every test starts from an UNCONFIGURED GPT judge.

    Load-bearing: a real .env at the repo root (or an operator's shell) must not
    leak the primary-judge gate into tests asserting the council path.
    """
    for var in _GPT_ENV_VARS + _CODEX_ENV_VARS + ("KENSEI_OPENAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


class _FakeSSEResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def _sse(verdict_text: str) -> list[bytes]:
    chunks = [
        {"choices": [{"delta": {"content": verdict_text}}]},
        {"choices": [], "usage": {"prompt_tokens": 1200,
                                  "completion_tokens": 40,
                                  "prompt_tokens_details": {"cached_tokens": 200}}},
    ]
    return [b"data: " + json.dumps(c).encode() + b"\n" for c in chunks] + [b"data: [DONE]\n"]


def _sse_error(message: str = "reached max output tokens") -> list[bytes]:
    chunks = [{"error": {"message": message, "type": "server_error"}}]
    return [b"data: " + json.dumps(c).encode() + b"\n" for c in chunks] + [b"data: [DONE]\n"]


def _request_body(req: urllib.request.Request) -> dict:
    assert isinstance(req.data, bytes)
    return json.loads(req.data)


def _capture_openai(monkeypatch, verdict_text: str = "ok") -> list[urllib.request.Request]:
    """Intercept the judge's HTTP call; return the list it records requests into."""
    seen: list[urllib.request.Request] = []

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001 - urllib signature
        seen.append(req)
        return _FakeSSEResponse(_sse(verdict_text))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return seen


# ===========================================================================
# Wave 1 — family registration
# ===========================================================================


class TestGptFamilyRegistration:
    def test_gpt_is_a_known_family(self):
        assert "gpt" in grading._KNOWN_FAMILIES

    def test_gpt_env_var_dispatch_is_registered(self):
        assert ("gpt", "JUDGE_GPT_MODEL") in grading._FAMILY_ENV_VARS

    def test_gpt_rates_are_per_token_not_per_mtok(self):
        r_in, r_out, r_cached, r_cwrite = grading._FAMILY_RATES["gpt"]
        assert (r_in, r_out, r_cached, r_cwrite) == (2e-6, 1e-5, 2e-7, 2.5e-6)
        assert r_in < 1e-3, "rates must be PER-TOKEN; a per-Mtok literal overstates 1e6x"

    def test_gpt_evidence_budget_stays_under_long_context_threshold(self):
        budget, max_output = grading._FAMILY_EVIDENCE["gpt"]
        assert max_output == 128000
        worst_case_input_tokens = (budget + 5000) / 1.375
        assert worst_case_input_tokens < 272_000, (
            "budget must keep input under OpenAI's 272K long-context threshold, "
            "which re-prices the whole request ~2x (flat _FAMILY_RATES cannot model it)"
        )

    def test_gpt_does_not_advertise_anthropic_cache_points(self):
        assert grading._FAMILY_CACHE_SUPPORTED["gpt"] is False

    def test_family_for_resolves_a_non_arn_model_id(self, monkeypatch):
        monkeypatch.setenv("JUDGE_GPT_MODEL", GPT_MODEL)
        assert grading._family_for(GPT_MODEL) == "gpt"

    def test_gpt_council_member_is_priced(self):
        member = grading.CouncilMember(family="gpt", model=GPT_MODEL)  # type: ignore[arg-type]
        grading.validate_judge_pricing([member])


# ===========================================================================
# Wave 2 — transport
# ===========================================================================


class TestGptSamplingParams:
    def test_family_gpt_omits_all_sampling_params(self):
        assert judge_litellm._judge_sampling_params(GPT_MODEL, "gpt") == {}

    def test_bare_gpt_5_6_model_id_omits_sampling_params_without_family(self):
        assert judge_litellm._judge_sampling_params(GPT_MODEL) == {}

    def test_other_families_still_pin_temperature_zero(self):
        assert judge_litellm._judge_sampling_params("glm-5", "glm") == {"temperature": 0}
        assert judge_litellm._judge_sampling_params("kimi-k2.5", "kimi") == {"temperature": 0}


class TestGptTransport:
    def _dispatch(self, monkeypatch, verdict_text="ok"):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        seen = _capture_openai(monkeypatch, verdict_text)
        text, usage = grading._call_one_judge(GPT_MODEL, "sys", "user", "gpt")
        assert len(seen) == 1
        return seen[0], _request_body(seen[0]), text, usage

    def test_dispatches_to_openai_chat_completions(self, monkeypatch):
        req, body, _text, _usage = self._dispatch(monkeypatch)
        assert req.full_url == "https://api.openai.com/v1/chat/completions"
        assert body["model"] == GPT_MODEL

    def test_omits_temperature_and_top_p(self, monkeypatch):
        _req, body, _text, _usage = self._dispatch(monkeypatch)
        assert "temperature" not in body
        assert "top_p" not in body

    def test_sends_reasoning_effort_low(self, monkeypatch):
        _req, body, _text, _usage = self._dispatch(monkeypatch)
        assert body["reasoning_effort"] == "low"

    def test_sends_max_completion_tokens_not_max_tokens(self, monkeypatch):
        _req, body, _text, _usage = self._dispatch(monkeypatch)
        assert body["max_completion_tokens"] == grading._FAMILY_EVIDENCE["gpt"][1]
        assert "max_tokens" not in body

    def test_uses_the_dedicated_judge_key(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        req, _body, _text, _usage = self._dispatch(monkeypatch)
        assert req.get_header("Authorization") == f"Bearer {JUDGE_KEY}"

    def test_falls_back_to_agent_key_only_when_judge_key_is_empty(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        grading._call_one_judge(GPT_MODEL, "sys", "user", "gpt")
        assert seen[0].get_header("Authorization") == f"Bearer {AGENT_KEY}"

    def test_usage_keeps_the_eight_key_schema_and_is_priced(self, monkeypatch):
        _req, _body, _text, usage = self._dispatch(monkeypatch)
        assert set(usage) == {
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "total_tokens", "request_count",
            "cost_usd", "cost_priced_ok",
        }
        assert usage["cost_priced_ok"] is True
        assert usage["cost_usd"] > 0.0

    def test_returns_the_streamed_verdict_text(self, monkeypatch):
        _req, _body, text, _usage = self._dispatch(monkeypatch)
        assert text == "ok"


class TestExistingOpenAiCallersUnchanged:
    def test_default_kwargs_reproduce_the_legacy_request(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        grading._call_judge_openai("gpt-5.5", "sys", "user")
        body = _request_body(seen[0])
        assert body["max_completion_tokens"] == 8000
        assert "reasoning_effort" not in body
        assert "temperature" not in body
        assert seen[0].get_header("Authorization") == f"Bearer {AGENT_KEY}"


# ===========================================================================
# Wave 3 — selection seam
# ===========================================================================


SONNET_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/sonnet-1m"
GLM_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/glm-air"
KIMI_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/kimi-k2"

RUBRICS = [
    {"criterion": "wrote report.md", "weight": 5},
    {"criterion": "leaked a credential", "weight": -3},
]


def _council_roster():
    return [
        grading.CouncilMember(family="sonnet", model=SONNET_ARN),
        grading.CouncilMember(family="glm", model=GLM_ARN),
        grading.CouncilMember(family="kimi", model=KIMI_ARN),
    ]


def _member_ok(model, family, satisfied_flags):
    return {
        "model": model,
        "effective_model": model,
        "family": family,
        "ok": True,
        "verdicts": [
            {"satisfied": bool(s), "rationale": "r", "truncation_affected": False}
            for s in satisfied_flags
        ],
        "usage": dict(grading._ZERO_USAGE),
        "user_chars": 10,
    }


def _member_dead(model, family, error="call: boom"):
    return {
        "model": model,
        "effective_model": model,
        "family": family,
        "ok": False,
        "error": error,
        "usage": {**grading._ZERO_USAGE, "error": error},
        "user_chars": 10,
    }


def _install_run_council(monkeypatch, *, gpt_alive: bool):
    """Fake `_run_council` that answers per-roster.

    A GPT-primary roster is a single "gpt" member; the fallback council is the
    usual sonnet/glm/kimi trio. Keying off the roster is what lets one test
    exercise BOTH legs of the fallback in a single grade_with_rubric call.
    """
    calls: list[str] = []

    def _fake(members, system, user_for_member, n_criteria):
        families = [m.family for m in members]
        calls.append(",".join(families))
        if families == ["gpt"]:
            if not gpt_alive:
                return [_member_dead(members[0].model, "gpt")]
            return [_member_ok(members[0].model, "gpt", [True, False])]
        return [_member_ok(m.model, m.family, [True, False]) for m in members]

    monkeypatch.setattr(grading, "_run_council", _fake)
    return calls


class TestGptJudgeConfiguredGate:
    def test_unconfigured_by_default(self):
        assert grading._gpt_judge_configured() is False

    def test_key_without_model_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        assert grading._gpt_judge_configured() is False

    def test_model_without_key_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        assert grading._gpt_judge_configured() is False

    def test_key_and_model_enable_it(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        assert grading._gpt_judge_configured() is True

    def test_generic_aliases_also_enable_it(self, monkeypatch):
        monkeypatch.setenv("JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("JUDGE_GPT_MODEL", GPT_MODEL)
        assert grading._gpt_judge_configured() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
    def test_kill_switch_forces_council_only(self, monkeypatch, value):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setenv("JUDGE_GPT_PRIMARY", value)
        assert grading._gpt_judge_configured() is False

    def test_kill_switch_on_keeps_it_enabled(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setenv("JUDGE_GPT_PRIMARY", "1")
        assert grading._gpt_judge_configured() is True


class TestGradeIsSignal:
    def test_error_dict_is_not_signal(self):
        assert grading._grade_is_signal({"overall_score": 0.0, "error": "boom"}) is False

    def test_genuine_all_fail_zero_is_signal(self):
        assert grading._grade_is_signal(
            {"overall_score": 0.0, "criteria": [{"id": 0, "passed": False}]}
        ) is True

    def test_empty_criteria_is_not_signal(self):
        assert grading._grade_is_signal({"overall_score": 0.0, "criteria": []}) is False

    def test_non_dict_is_not_signal(self):
        assert grading._grade_is_signal(None) is False


class TestGptNeverEnlistedInCouncil:
    """gpt is in _FAMILY_ENV_VARS (for primary-judge dispatch), so council_members'
    fallback loop builds a family=="gpt" member when JUDGE_GPT_MODEL is set. It MUST
    be dropped from the council roster: gpt grades as the standalone primary judge,
    and the aggregator's unanimous-or-Sonnet-tiebreak assumes Bedrock members only."""

    def test_council_members_excludes_gpt_even_when_gpt_model_set(self, monkeypatch):
        monkeypatch.setenv("WCB_AUTH_PROVIDER", "bedrock")
        monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", SONNET_ARN)
        monkeypatch.setenv("JUDGE_COUNCIL_GLM_ARN", GLM_ARN)
        monkeypatch.setenv("JUDGE_COUNCIL_KIMI_ARN", KIMI_ARN)
        monkeypatch.setenv("JUDGE_GPT_MODEL", GPT_MODEL)
        families = [m.family for m in grading.council_members()]
        assert "gpt" not in families
        assert families == ["sonnet", "glm", "kimi"]


class TestSelectionSeam:
    def test_a_unconfigured_gpt_takes_the_council_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(grading, "council_members", _council_roster)
        calls = _install_run_council(monkeypatch, gpt_alive=True)

        def _must_not_run(*_a, **_kw):
            raise AssertionError("GPT primary judge ran with an empty GPT config")

        monkeypatch.setattr(grading, "_grade_gpt_primary", _must_not_run)

        scores = grading.grade_with_rubric(RUBRICS, "task", tmp_path)

        assert scores["judge_model"] == "council"
        assert calls == ["sonnet,glm,kimi"]
        assert scores["judge_council"]["aggregation"] == "unanimous_or_sonnet_tiebreak"

    def test_b_configured_and_healthy_gpt_grades_and_labels_itself(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        calls = _install_run_council(monkeypatch, gpt_alive=True)

        def _must_not_run():
            raise AssertionError("council was consulted despite a healthy GPT judge")

        monkeypatch.setattr(grading, "council_members", _must_not_run)

        scores = grading.grade_with_rubric(RUBRICS, "task", tmp_path)

        assert scores["judge_model"] == GPT_MODEL
        assert calls == ["gpt"]
        assert scores["judge_council"]["aggregation"] == "gpt_primary_single_judge"
        assert scores["criteria_total"] == 2
        assert scores["criteria_abstained"] == 0

    def test_c_no_signal_gpt_falls_back_to_the_council(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setattr(grading, "council_members", _council_roster)
        calls = _install_run_council(monkeypatch, gpt_alive=False)

        scores = grading.grade_with_rubric(RUBRICS, "task", tmp_path)

        assert calls == ["gpt", "sonnet,glm,kimi"]
        assert scores["judge_model"] == "council"
        assert "error" not in scores

    def test_gpt_primary_never_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)

        def _explode(*_a, **_kw):
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(grading, "_grade_council", _explode)

        result = grading._grade_gpt_primary(RUBRICS, "task", tmp_path, "", "sys")

        assert result["overall_score"] == 0.0
        assert "transport exploded" in result["error"]
        assert grading._grade_is_signal(result) is False

    def test_gpt_primary_failure_falls_back_when_it_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setattr(grading, "council_members", _council_roster)

        real_grade_council = grading._grade_council

        def _explode_for_gpt(rubrics, system, user_for_member, members):
            if [m.family for m in members] == ["gpt"]:
                raise RuntimeError("transport exploded")
            return real_grade_council(rubrics, system, user_for_member, members)

        _install_run_council(monkeypatch, gpt_alive=True)
        monkeypatch.setattr(grading, "_grade_council", _explode_for_gpt)

        scores = grading.grade_with_rubric(RUBRICS, "task", tmp_path)

        assert scores["judge_model"] == "council"


# ===========================================================================
# Codex-subscription judge route (GPT judge via the codex OAuth bridge)
# ===========================================================================


class TestCodexBridgeGates:
    def test_url_gate_empty_by_default(self):
        assert grading._judge_codex_bridge_url() == ""

    def test_url_gate_reads_env(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        assert grading._judge_codex_bridge_url() == CODEX_URL

    def test_secret_reads_host_env_name(self, monkeypatch):
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        assert grading._judge_codex_bridge_secret() == CODEX_SECRET

    def test_model_defaults_to_gpt_5_6_sol(self):
        assert grading._judge_codex_bridge_model() == "gpt-5.6-sol"

    def test_model_prefers_explicit_bridge_model(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_MODEL", "gpt-5.6-terra")
        assert grading._judge_codex_bridge_model() == "gpt-5.6-terra"

    def test_model_falls_back_to_metered_model_before_default(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", "gpt-5.6-luna")
        assert grading._judge_codex_bridge_model() == "gpt-5.6-luna"


class TestCodexJudgeConfiguredGate:
    def test_codex_url_plus_secret_enables_without_metered_key(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        assert grading._gpt_judge_configured() is True

    def test_codex_url_without_secret_is_not_configured(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        assert grading._gpt_judge_configured() is False

    def test_kill_switch_forces_council_only_on_codex_route(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
        assert grading._gpt_judge_configured() is False


class TestCodexTransport:
    def _dispatch(self, monkeypatch, verdict_text="ok"):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen = _capture_openai(monkeypatch, verdict_text)
        text, usage = grading._call_one_judge(GPT_MODEL, "sys", "user", "gpt")
        assert len(seen) == 1
        return seen[0], _request_body(seen[0]), text, usage

    def test_targets_the_bridge_chat_completions_endpoint(self, monkeypatch):
        req, _body, _text, _usage = self._dispatch(monkeypatch)
        assert req.full_url == f"{CODEX_URL}/v1/chat/completions"

    def test_authenticates_with_the_bridge_secret_as_bearer(self, monkeypatch):
        req, _body, _text, _usage = self._dispatch(monkeypatch)
        assert req.get_header("Authorization") == f"Bearer {CODEX_SECRET}"

    def test_emits_reasoning_dict_not_flat_reasoning_effort(self, monkeypatch):
        _req, body, _text, _usage = self._dispatch(monkeypatch)
        assert body["reasoning"] == {"effort": "low"}
        assert "reasoning_effort" not in body

    def test_still_omits_temperature_and_top_p(self, monkeypatch):
        _req, body, _text, _usage = self._dispatch(monkeypatch)
        assert "temperature" not in body
        assert "top_p" not in body

    def test_cost_is_zero_but_priced_ok_on_subscription(self, monkeypatch):
        _req, _body, _text, usage = self._dispatch(monkeypatch)
        assert usage["cost_usd"] == 0.0
        assert usage["cost_priced_ok"] is True
        assert usage["total_tokens"] > 0

    def test_prefers_bridge_over_metered_key_when_both_set(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_GPT_API_KEY", JUDGE_KEY)
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", GPT_MODEL)
        req, _body, _text, _usage = self._dispatch(monkeypatch)
        assert req.full_url == f"{CODEX_URL}/v1/chat/completions"
        assert req.get_header("Authorization") == f"Bearer {CODEX_SECRET}"


class TestCodexErrorChunkRaises:
    def test_error_only_chunk_raises_instead_of_abstaining(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)

        def _fake_urlopen(req, timeout=None):  # noqa: ANN001
            return _FakeSSEResponse(_sse_error("cap reached"))

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        with pytest.raises(RuntimeError, match="cap reached"):
            grading._call_one_judge(GPT_MODEL, "sys", "user", "gpt")

    def test_metered_path_also_raises_on_error_chunk(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)

        def _fake_urlopen(req, timeout=None):  # noqa: ANN001
            return _FakeSSEResponse(_sse_error("boom"))

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        with pytest.raises(RuntimeError, match="boom"):
            grading._call_judge_openai("gpt-5.5", "sys", "user")


class TestCodexEvidenceCap:
    def test_gpt_budget_capped_by_codex_max_evidence_when_bridge_active(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", "1000")
        assert grading._member_evidence_budget(GPT_MODEL, "gpt") == 1000

    def test_gpt_budget_unaffected_without_bridge(self, monkeypatch):
        base = min(grading._FAMILY_EVIDENCE["gpt"][0], grading._AWS_EDGE_BODY_CAP)
        assert grading._member_evidence_budget(GPT_MODEL, "gpt") == base


class TestCodexGradePrimaryModelFallback:
    def test_grade_primary_uses_bridge_model_when_no_metered_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen_models: list[str] = []

        def _fake_run_council(members, system, user_for_member, n_criteria):
            seen_models.append(members[0].model)
            return [_member_ok(members[0].model, "gpt", [True, False])]

        monkeypatch.setattr(grading, "_run_council", _fake_run_council)
        result = grading._grade_gpt_primary(RUBRICS, "task", tmp_path, "", "sys")
        assert seen_models == ["gpt-5.6-sol"]
        assert result["judge_model"] == "gpt-5.6-sol"

    def test_codex_first_when_both_bridge_and_metered_model_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_MODEL", "gpt-5.6-sol")
        monkeypatch.setenv("KENSEI_JUDGE_GPT_MODEL", "gpt-5.6")
        seen_models: list[str] = []

        def _fake_run_council(members, system, user_for_member, n_criteria):
            seen_models.append(members[0].model)
            return [_member_ok(members[0].model, "gpt", [True, False])]

        monkeypatch.setattr(grading, "_run_council", _fake_run_council)
        result = grading._grade_gpt_primary(RUBRICS, "task", tmp_path, "", "sys")
        # MUST match what preflight_judge_codex probes (bridge model), not the
        # bare metered id the codex backend would reject.
        assert seen_models == ["gpt-5.6-sol"]
        assert result["judge_model"] == "gpt-5.6-sol"


class TestPreflightJudgeCodex:
    def test_skip_when_url_unset(self):
        ok, detail = grading.preflight_judge_codex()
        assert ok == "skip"
        assert "not configured" in detail

    def test_fail_when_url_set_but_secret_empty(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert "WCB_CODEX_BRIDGE_SECRET" in detail

    def test_ok_when_bridge_answers(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        _capture_openai(monkeypatch, "OK")
        ok, detail = grading.preflight_judge_codex()
        assert ok == "ok"
        assert CODEX_URL in detail

    def test_fail_when_bridge_raises(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)

        def _boom(req, timeout=None):  # noqa: ANN001
            raise RuntimeError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert "connection refused" in detail

    def test_preflight_uses_the_bridge_endpoint_and_bearer_secret(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen = _capture_openai(monkeypatch, "OK")
        grading.preflight_judge_codex()
        assert seen[0].full_url == f"{CODEX_URL}/v1/chat/completions"
        assert seen[0].get_header("Authorization") == f"Bearer {CODEX_SECRET}"


class TestGptNeverTripsCouncilRaise:
    """AGENTS.md #12: grade_with_rubric MUST NOT raise. Registering gpt in
    _FAMILY_ENV_VARS (for primary-judge dispatch) must not let a gpt-only roster
    trip council_members' 'no usable judge remains' raise."""

    def test_council_members_does_not_raise_with_gpt_alias_only(self, monkeypatch):
        for var in (
            "JUDGE_COUNCIL_MEMBERS", "JUDGE_COUNCIL_SONNET_ARN",
            "JUDGE_COUNCIL_GLM_ARN", "JUDGE_COUNCIL_KIMI_ARN",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("WCB_AUTH_PROVIDER", "bedrock")
        monkeypatch.setenv("JUDGE_GPT_MODEL", GPT_MODEL)
        monkeypatch.setenv("JUDGE_GPT_API_KEY", JUDGE_KEY)
        assert grading.council_members() == []

    def test_council_override_rejects_gpt_family(self, monkeypatch):
        with pytest.raises(RuntimeError, match="PRIMARY-judge family"):
            grading._parse_council_member_override("gpt=whatever")

    def test_grade_with_rubric_degrades_on_gpt_override_never_raises(self, monkeypatch, tmp_path):
        # AGENTS.md #12: even a hand-written JUDGE_COUNCIL_MEMBERS=gpt=... (which
        # makes council_members() raise) must degrade to a no-signal error dict,
        # not propagate out of grade_with_rubric.
        for var in _CODEX_ENV_VARS + _GPT_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("WCB_AUTH_PROVIDER", "bedrock")
        monkeypatch.setenv("JUDGE_COUNCIL_MEMBERS", "gpt=gpt-5.6")
        scores = grading.grade_with_rubric(RUBRICS, "task", tmp_path)
        assert scores["overall_score"] == 0.0
        assert "council roster unusable" in scores["error"]

