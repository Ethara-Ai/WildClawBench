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
import base64
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

# Image-attachment budget knobs. Cleared alongside the GPT/codex gates so an
# operator's shell (or a real .env) cannot change how many images a test sees.
_IMAGE_ENV_VARS = (
    "KENSEI_JUDGE_MAX_IMAGES",
    "KENSEI_JUDGE_MAX_IMAGE_BYTES",
    "KENSEI_JUDGE_IMAGE_DETAIL",
)


@pytest.fixture(autouse=True)
def _clean_gpt_env(monkeypatch):
    """Every test starts from an UNCONFIGURED GPT judge.

    Load-bearing: a real .env at the repo root (or an operator's shell) must not
    leak the primary-judge gate into tests asserting the council path.
    """
    for var in (
        _GPT_ENV_VARS + _CODEX_ENV_VARS + _IMAGE_ENV_VARS
        + ("KENSEI_OPENAI_API_KEY", "OPENAI_API_KEY")
    ):
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

    def test_metered_gpt_budget_stays_350k(self):
        assert grading._member_evidence_budget(GPT_MODEL, "gpt") == 350_000

    def test_codex_route_default_budget_is_500k(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        assert grading._DEFAULT_JUDGE_CODEX_MAX_EVIDENCE == 500_000
        assert grading._member_evidence_budget("gpt-5.6-sol", "gpt") == 500_000

    def test_codex_env_can_raise_above_the_metered_base(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", "600000")
        assert grading._member_evidence_budget("gpt-5.6-sol", "gpt") == 600_000

    @pytest.mark.parametrize("raw", ["", "junk", "0", "-5"])
    def test_codex_env_invalid_falls_back_to_500k(self, monkeypatch, raw):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", raw)
        assert grading._member_evidence_budget("gpt-5.6-sol", "gpt") == 500_000

    def test_codex_luna_model_keeps_the_350k_family_base(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        assert grading._member_evidence_budget("gpt-5.6-luna", "gpt") == 350_000
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", "1000")
        assert grading._member_evidence_budget("gpt-5.6-luna", "gpt") == 1000

    def test_gpt_primary_on_codex_builds_evidence_with_500k_budget(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen_budgets = []
        real = grading._gather_evidence

        def spy(*a, **kw):
            seen_budgets.append(kw.get("budget"))
            return real(*a, **kw)

        monkeypatch.setattr(grading, "_gather_evidence", spy)
        _capture_openai(monkeypatch, "ok")
        root = _write_deliverables(tmp_path, {"report.md": "body"})
        grading._grade_gpt_primary(RUBRICS, "task", root, "", "sys")
        assert seen_budgets == [500_000]


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


# ===========================================================================
# Multimodal judge payload — inline base64 images are LIFTED out of the judge
# text prompt and re-attached as structured image content-parts. Sending them as
# prompt text produced the gpt-5.6 refusal (HTTP 200, empty content) and was
# unreadable to the judge anyway. This rides the gpt route ONLY.
# ===========================================================================


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_DATA_URI = f"data:image/png;base64,{PNG_B64}"


def _approx_kb(b64: str) -> str:
    return f"{(len(b64) * 3 / 4) / 1024:.1f}"


def _write_deliverables(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create an `artifacts/` deliverable root and return it.

    `artifacts` (not `results`) is deliberate: `_collect_deliverable_files`
    sweeps the PARENT of a dir named `results`, which under pytest would be the
    shared session tmp root and could pick up another test's files.
    """
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


class TestExtractInlineImages:
    def test_lifts_a_data_uri_into_a_placeholder_and_an_image_part(self):
        text, images = grading._extract_inline_images(
            f'<p>before</p><img src="{PNG_DATA_URI}"><p>after</p>', "page.html"
        )
        assert "data:image" not in text
        assert "before" in text and "after" in text
        assert f"[inline image page.html#1, image/png, ~{_approx_kb(PNG_B64)}KB]" in text
        assert len(images) == 1
        assert images[0].data_uri == PNG_DATA_URI
        assert images[0].mime == "image/png"
        assert images[0].label == "page.html#1"

    def test_placeholder_kb_approximates_the_decoded_payload_size(self):
        blob = "A" * 4096
        text, images = grading._extract_inline_images(
            f"data:image/jpeg;base64,{blob}", "shot.md"
        )
        assert "~3.0KB" in text
        assert images[0].mime == "image/jpeg"

    def test_text_without_images_is_returned_untouched(self):
        body = "just a report about data:image handling, no base64 here"
        text, images = grading._extract_inline_images(body, "notes.md")
        assert text == body
        assert images == []

    def test_multiple_images_are_labelled_in_document_order(self):
        body = f"a{PNG_DATA_URI}b{PNG_DATA_URI}c"
        text, images = grading._extract_inline_images(body, "two.html")
        assert [i.label for i in images] == ["two.html#1", "two.html#2"]
        assert text.count("[inline image") == 2
        assert "data:image" not in text

    def test_detail_tier_follows_the_env_override(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_IMAGE_DETAIL", "high")
        _text, images = grading._extract_inline_images(PNG_DATA_URI, "x.md")
        assert images[0].detail == "high"

    def test_detail_defaults_to_low(self):
        _text, images = grading._extract_inline_images(PNG_DATA_URI, "x.md")
        assert images[0].detail == "low"


class TestWrappedBase64IsLiftedWhole:
    """A newline-wrapped blob must be lifted WHOLE, not one line of it.

    Agents produce wrapped payloads routinely: `base64.encodebytes` and coreutils
    `base64 <file>` both wrap at 76 columns. A whitespace-free payload class lifts
    only the FIRST line, which is the worst outcome available -- the rest of the
    base64 stays in the judge text (so the safety-classifier refusal this whole
    seam exists to prevent is fully intact) AND the attached data URI is a
    truncated, undecodable image.
    """

    def _wrapped_png(self) -> tuple[str, str]:
        """Return (wrapped_b64_with_newlines, flat_b64) for a real PNG."""
        raw = base64.b64decode(PNG_B64)
        # A single 1x1 PNG is shorter than one wrap line; repeat the bytes so the
        # encoder actually emits multiple 76-column lines.
        raw = raw * 40
        wrapped = base64.encodebytes(raw).decode("ascii")
        assert wrapped.count("\n") > 1, "fixture must span multiple wrap lines"
        return wrapped, wrapped.replace("\n", "")

    def test_wrapped_blob_is_lifted_whole_and_leaves_no_base64_behind(self):
        wrapped, flat = self._wrapped_png()
        body = f'<p>before</p><img src="data:image/png;base64,{wrapped}"><p>after</p>'
        text, images = grading._extract_inline_images(body, "wrapped.html")

        assert len(images) == 1
        # Single-line, complete, decodable data URI rebuilt from the stripped payload.
        assert images[0].data_uri == f"data:image/png;base64,{flat}"
        assert "\n" not in images[0].data_uri
        assert base64.b64decode(images[0].data_uri.partition(",")[2], validate=True)

        # ZERO residue: no data-URI head, and no run of the payload left in text.
        assert "data:image" not in text
        assert "base64," not in text
        assert flat[:64] not in text
        assert flat[-64:] not in text
        assert "[inline image wrapped.html#1, image/png," in text
        assert "before" in text and "after" in text

    def test_wrapped_placeholder_reports_the_full_payload_size(self):
        wrapped, flat = self._wrapped_png()
        text, _images = grading._extract_inline_images(
            f"data:image/png;base64,{wrapped}", "w.md"
        )
        assert f"~{_approx_kb(flat)}KB" in text

    def test_prose_after_an_unwrapped_blob_is_not_swallowed(self):
        """The wrap-continuation rule requires a >=64-char segment before the
        newline, so ordinary prose on the next line stays in the text."""
        body = f"{PNG_DATA_URI}\nThe report shows a chart."
        text, images = grading._extract_inline_images(body, "notes.md")
        assert len(images) == 1
        assert images[0].data_uri == PNG_DATA_URI
        assert "The report shows a chart." in text


class TestGatherEvidencePayload:
    def test_transcript_base64_is_scrubbed_to_placeholders_without_attaching(
        self, tmp_path
    ):
        """A tool result that cat'd an image-bearing file carries the same blobs
        into the SAME user turn through the transcript seam. Scrub them to text
        placeholders (no vision cost, no attachment) so the refusal trigger cannot
        come back the long way round."""
        root = _write_deliverables(tmp_path, {"notes.md": "no images here"})
        transcript = f"tool: cat page.html\n{PNG_DATA_URI}\ndone"
        payload = grading._gather_evidence(root, transcript)
        assert "data:image" not in payload.text
        assert "base64," not in payload.text
        assert "[inline image transcript#1," in payload.text
        assert payload.images == []

    def test_text_carries_no_data_uri_and_images_carry_the_blob(self, tmp_path):
        root = _write_deliverables(
            tmp_path, {"report.html": f'<h1>Q3</h1><img src="{PNG_DATA_URI}">'}
        )
        payload = grading._gather_evidence(root, "turn 1")
        assert isinstance(payload, grading.JudgeUserPayload)
        assert "data:image" not in payload.text
        assert "base64," not in payload.text
        assert "Q3" in payload.text
        assert [i.data_uri for i in payload.images] == [PNG_DATA_URI]

    def test_a_data_uri_is_never_bisected_by_the_char_budget(self, tmp_path):
        """Extraction runs at the deliverable seam, BEFORE budgeting, so no char
        slice can ever land inside a base64 blob. The blob here is far larger than
        the whole evidence budget: inline it would have been cut mid-payload (or
        dropped the block outright), while the placeholder it leaves behind fits
        comfortably and the pixels ride the image parts intact."""
        big_b64 = "Q" * 2000
        big_uri = f"data:image/png;base64,{big_b64}"
        filler = "x" * 200
        root = _write_deliverables(
            tmp_path, {"page.html": f'{filler}<img src="{big_uri}">{filler}'}
        )
        budget = 600
        payload = grading._gather_evidence(root, "turn 1", budget=budget)
        assert len(big_uri) > budget
        assert len(payload.text) <= budget
        assert "base64," not in payload.text
        assert big_b64[:40] not in payload.text
        assert "[inline image page.html#1, image/png, ~1.5KB]" in payload.text
        assert [i.data_uri for i in payload.images] == [big_uri]

    def test_count_cap_attaches_eight_and_leaves_placeholders_for_the_rest(self, tmp_path):
        body = "".join(f'<img src="{PNG_DATA_URI}">' for _ in range(10))
        root = _write_deliverables(tmp_path, {"gallery.html": body})
        payload = grading._gather_evidence(root, "turn 1")
        assert grading._judge_max_images() == 8
        assert len(payload.images) == 8
        assert payload.text.count("[inline image") == 10

    def test_count_cap_zero_disables_attachment_but_keeps_placeholders(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "0")
        root = _write_deliverables(tmp_path, {"page.html": f'<img src="{PNG_DATA_URI}">'})
        payload = grading._gather_evidence(root, "turn 1")
        assert payload.images == []
        assert payload.text.count("[inline image") == 1

    def test_byte_cap_stops_attachment_before_the_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGE_BYTES", str(len(PNG_B64) * 2))
        body = "".join(f'<img src="{PNG_DATA_URI}">' for _ in range(5))
        root = _write_deliverables(tmp_path, {"gallery.html": body})
        payload = grading._gather_evidence(root, "turn 1")
        assert len(payload.images) == 2
        assert payload.text.count("[inline image") == 5

    def test_byte_cap_skips_an_oversized_image_and_keeps_packing(self, tmp_path, monkeypatch):
        """The byte cap SKIPS an over-budget image rather than stopping: one large
        blob early in the evidence must not suppress every smaller one behind
        it."""
        big = "Q" * 4000
        body = (
            f'<img src="data:image/png;base64,{big}">'
            + f'<img src="{PNG_DATA_URI}">'
        )
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGE_BYTES", str(len(PNG_B64) + 10))
        root = _write_deliverables(tmp_path, {"mixed.html": body})
        payload = grading._gather_evidence(root, "turn 1")
        assert [i.label for i in payload.images] == ["mixed.html#2"]
        assert payload.text.count("[inline image") == 2

    def test_a_partially_kept_block_drops_images_whose_placeholder_was_excised(
        self, tmp_path
    ):
        """The head+tail keep excises the MIDDLE of an over-budget block. An image
        whose placeholder lived there must not be attached — same orphan-pixel
        rule as a wholly dropped block, and the reason the survivor filter runs
        against the FINAL text rather than at collection time."""
        body = ("A" * 2500) + f'<img src="{PNG_DATA_URI}">' + ("B" * 2500)
        root = _write_deliverables(tmp_path, {"page.html": body})
        payload = grading._gather_evidence(root, "turn 1", budget=4000)
        assert "truncated for evidence budget" in payload.text  # partial keep ran
        assert "[inline image page.html#1," not in payload.text
        assert payload.images == []

    def test_a_wholly_dropped_block_attaches_no_orphan_pixels(self, tmp_path):
        """A block cut for budget loses its placeholders too; attaching its images
        would put pixels in front of the judge with nothing in the text naming
        them."""
        body = ("y" * 3000) + f'<img src="{PNG_DATA_URI}">'
        root = _write_deliverables(tmp_path, {"big.html": body})
        # Too small for a head+tail keep, so the whole block is dropped.
        payload = grading._gather_evidence(root, "t", budget=600)
        assert payload.images == []
        assert "omitted or cut for budget" in payload.text
        # The dropped file is still disclosed by name.
        assert "----- DELIVERABLE: big.html\n(" in payload.text
        assert "present — contents not included: evidence budget exceeded" in payload.text


class TestJudgeOpenAiImageParts:
    def test_no_images_keeps_content_a_bare_string(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        grading._call_judge_openai("gpt-5.5", "sys", "plain user text")
        assert _request_body(seen[0])["messages"][1]["content"] == "plain user text"

    def test_an_imageless_payload_is_byte_identical_to_a_bare_string(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        grading._call_judge_openai("gpt-5.5", "sys", "plain user text")
        grading._call_judge_openai(
            "gpt-5.5", "sys", grading.JudgeUserPayload(text="plain user text")
        )
        assert seen[0].data == seen[1].data

    def test_images_become_a_parts_list_with_text_first(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        payload = grading.JudgeUserPayload(
            text="evidence",
            images=[grading.ImagePart(
                data_uri=PNG_DATA_URI, mime="image/png", detail="low", label="a#1")],
        )
        grading._call_judge_openai("gpt-5.5", "sys", payload)
        content = _request_body(seen[0])["messages"][1]["content"]
        # The image parts carry no model-readable label, so the text part gets a
        # trailing manifest naming the attachments in order (see MINOR 4).
        assert content == [
            {"type": "text",
             "text": "evidence\n[Attached images, in order: a#1]"},
            {"type": "image_url",
             "image_url": {"url": PNG_DATA_URI, "detail": "low"}},
        ]

    def test_multiple_images_preserve_order_and_per_image_detail(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        payload = grading.JudgeUserPayload(
            text="evidence",
            images=[
                grading.ImagePart(PNG_DATA_URI, "image/png", "low", "a#1"),
                grading.ImagePart(PNG_DATA_URI + "AA", "image/png", "high", "a#2"),
            ],
        )
        grading._call_judge_openai("gpt-5.5", "sys", payload)
        content = _request_body(seen[0])["messages"][1]["content"]
        assert [p["type"] for p in content] == ["text", "image_url", "image_url"]
        assert content[0]["text"].endswith("[Attached images, in order: a#1, a#2]")
        assert content[1]["image_url"]["detail"] == "low"
        assert content[2]["image_url"] == {"url": PNG_DATA_URI + "AA", "detail": "high"}

    def test_system_message_is_never_turned_into_parts(self, monkeypatch):
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        payload = grading.JudgeUserPayload(
            text="evidence",
            images=[grading.ImagePart(PNG_DATA_URI, "image/png", "low", "a#1")],
        )
        grading._call_judge_openai("gpt-5.5", "sys", payload)
        assert _request_body(seen[0])["messages"][0]["content"] == "sys"

    def test_codex_route_emits_the_same_chat_image_shape(self, monkeypatch):
        """The codex bridge is fed the SAME Chat-Completions image_url shape; the
        Responses translation happens bridge-side (codex_oauth/translate.py), so
        this body stays route-agnostic."""
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen = _capture_openai(monkeypatch)
        payload = grading.JudgeUserPayload(
            text="evidence",
            images=[grading.ImagePart(PNG_DATA_URI, "image/png", "low", "a#1")],
        )
        grading._call_one_judge(GPT_MODEL, "sys", payload, "gpt")
        content = _request_body(seen[0])["messages"][1]["content"]
        assert content[1]["image_url"]["url"] == PNG_DATA_URI


class TestCouncilNeverSeesImageParts:
    """Every non-gpt transport is TEXT-ONLY by contract: `_call_one_judge`
    unwraps `.text` so a Bedrock/LiteLLM request body is byte-identical to the
    pre-multimodal harness even if a payload lands in a council slot."""

    def _payload(self):
        return grading.JudgeUserPayload(
            text="council evidence",
            images=[grading.ImagePart(PNG_DATA_URI, "image/png", "low", "a#1")],
        )

    def test_openai_fallback_family_gets_a_bare_string(self, monkeypatch):
        monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
        monkeypatch.setenv("KENSEI_OPENAI_API_KEY", AGENT_KEY)
        seen = _capture_openai(monkeypatch)
        grading._call_one_judge("gpt-5.5", "sys", self._payload(), "glm")
        content = _request_body(seen[0])["messages"][1]["content"]
        assert content == "council evidence"
        assert isinstance(content, str)

    def test_litellm_path_receives_text_not_a_payload(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
        captured: list[object] = []

        def _fake_call(*, model, system, user, max_output_tokens, cost_fn, family):
            captured.append(user)
            return "ok", dict(grading._ZERO_USAGE)

        monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _fake_call)
        grading._call_one_judge(SONNET_ARN, "sys", self._payload(), "sonnet")
        assert captured == ["council evidence"]
        assert isinstance(captured[0], str)


class TestPreflightCodexImageProbe:
    def _configure(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)

    def _replies(self, monkeypatch, replies: list) -> list[urllib.request.Request]:
        """Fake urlopen answering one scripted reply per call.

        A reply may be an Exception (raised) or verdict text; the last reply is
        reused once the script runs out.
        """
        seen: list[urllib.request.Request] = []

        def _fake_urlopen(req, timeout=None):  # noqa: ANN001 - urllib signature
            reply = replies[len(seen)] if len(seen) < len(replies) else replies[-1]
            seen.append(req)
            if isinstance(reply, Exception):
                raise reply
            return _FakeSSEResponse(_sse(reply))

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        return seen

    def test_probes_text_then_an_image_part(self, monkeypatch):
        self._configure(monkeypatch)
        seen = self._replies(monkeypatch, ["OK", "OK"])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "ok"
        assert "image probe ok" in detail
        assert len(seen) == 2
        assert isinstance(_request_body(seen[0])["messages"][1]["content"], str)
        parts = _request_body(seen[1])["messages"][1]["content"]
        assert [p["type"] for p in parts] == ["text", "image_url"]
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_image_probe_uses_the_bridge_endpoint_and_bearer_secret(self, monkeypatch):
        self._configure(monkeypatch)
        seen = self._replies(monkeypatch, ["OK", "OK"])
        grading.preflight_judge_codex()
        assert seen[1].full_url == f"{CODEX_URL}/v1/chat/completions"
        assert seen[1].get_header("Authorization") == f"Bearer {CODEX_SECRET}"

    def test_empty_image_reply_is_a_refusal_and_fails(self, monkeypatch):
        """The gpt-5.6 refusal this path exists to avoid is HTTP 200 with EMPTY
        content — preflight MUST call that a failure, not an ok.

        The `image probe: ` PREFIX is a contract with eval/run_batch.py, which
        branches on it to DEGRADE (disable image attachment for the batch) rather
        than abort the way a text/auth failure does.
        """
        self._configure(monkeypatch)
        self._replies(monkeypatch, ["OK", ""])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert detail.startswith("image probe:")
        assert "refusal" in detail

    def test_verbalised_refusal_fails(self, monkeypatch):
        self._configure(monkeypatch)
        self._replies(monkeypatch, ["OK", "I'm sorry, I can't help with that image."])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert detail.startswith("image probe:")
        assert "refusal marker" in detail

    def test_image_probe_transport_error_fails(self, monkeypatch):
        self._configure(monkeypatch)
        self._replies(monkeypatch, ["OK", RuntimeError("image parts unsupported")])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert detail.startswith("image probe:")
        assert "image parts unsupported" in detail

    def test_text_probe_failure_still_reports_before_the_image_probe(self, monkeypatch):
        self._configure(monkeypatch)
        seen = self._replies(monkeypatch, [RuntimeError("connection refused")])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail"
        assert "connection refused" in detail
        assert "image probe" not in detail
        assert not detail.startswith("image probe:")  # run_batch must ABORT on this
        assert len(seen) == 1

    def test_image_probe_skipped_when_attachment_is_disabled(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "0")
        seen = self._replies(monkeypatch, ["OK"])
        ok, detail = grading.preflight_judge_codex()
        assert ok == "ok"
        assert "attachment disabled" in detail
        assert len(seen) == 1

    def test_skip_and_secret_gates_are_unchanged(self, monkeypatch):
        assert grading.preflight_judge_codex()[0] == "skip"
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        assert grading.preflight_judge_codex()[0] == "fail"


class TestPromptSeamCarriesImages:
    """`_judge_user_prompt` renders the evidence into the judge_user template. If
    it returned a bare str, the images `_gather_evidence` lifted would be stranded
    between the extractor and the transport and the judge would grade image-blind
    with nothing naming the loss."""

    def _payload(self):
        return grading.JudgeUserPayload(
            text="files\n----- TRANSCRIPT (condensed) -----\nturn 1",
            images=[grading.ImagePart(PNG_DATA_URI, "image/png", "low", "a#1")],
        )

    def test_images_survive_prompt_rendering(self):
        out = grading._judge_user_prompt("task", RUBRICS, self._payload())
        assert isinstance(out, grading.JudgeUserPayload)
        assert [i.data_uri for i in out.images] == [PNG_DATA_URI]
        assert "data:image" not in out.text
        assert "wrote report.md" in out.text

    def test_text_only_evidence_still_returns_a_bare_string(self):
        out = grading._judge_user_prompt("task", RUBRICS, "files only")
        assert isinstance(out, str)

    def test_an_imageless_payload_also_returns_a_bare_string(self):
        out = grading._judge_user_prompt(
            "task", RUBRICS, grading.JudgeUserPayload(text="files only")
        )
        assert isinstance(out, str)

    def test_gpt_primary_grade_puts_image_parts_on_the_wire(self, monkeypatch, tmp_path):
        """End-to-end: a deliverable with an inline screenshot reaches the GPT
        judge's HTTP body as an image_url content part, not as base64 prompt text."""
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        root = _write_deliverables(
            tmp_path, {"report.md": f"# Q3\n\n![shot]({PNG_DATA_URI})\n"}
        )
        seen = _capture_openai(monkeypatch, "ok")
        grading._grade_gpt_primary(RUBRICS, "task", root, "", "sys")
        assert len(seen) == 1
        content = _request_body(seen[0])["messages"][1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert PNG_B64 not in content[0]["text"]
        assert content[1]["image_url"]["url"] == PNG_DATA_URI


class TestImageBudgetEnvParsing:
    def test_defaults(self):
        assert grading._judge_max_images() == 8
        assert grading._judge_max_image_bytes() == 4 * 1024 * 1024
        assert grading._judge_image_detail() == "low"

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "-1"])
    def test_unparseable_or_negative_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", raw)
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGE_BYTES", raw)
        assert grading._judge_max_images() == 8
        assert grading._judge_max_image_bytes() == 4 * 1024 * 1024

    @pytest.mark.parametrize("raw", ["low", "high", "auto", "HIGH"])
    def test_valid_detail_tiers_are_accepted_case_insensitively(self, monkeypatch, raw):
        monkeypatch.setenv("KENSEI_JUDGE_IMAGE_DETAIL", raw)
        assert grading._judge_image_detail() == raw.strip().lower()

    def test_unknown_detail_tier_falls_back_to_low(self, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_IMAGE_DETAIL", "ultra")
        assert grading._judge_image_detail() == "low"



def test_image_probe_png_is_a_valid_non_degenerate_image():
    import base64
    import struct
    import zlib
    head, _, b64 = grading._PROBE_PNG_DATA_URI.partition(",")
    assert head == "data:image/png;base64"
    data = base64.b64decode(b64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (8, 8)
    pos, idat = 8, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(ctype + body) & 0xFFFFFFFF, ctype
        if ctype == b"IDAT":
            idat += body
        pos += 12 + length
    bit_depth, colour_type = data[24], data[25]
    assert (bit_depth, colour_type) == (8, 2)  # 8-bit RGB
    assert len(zlib.decompress(idat)) == h * (1 + 3 * w)


class TestGptPrimaryRubricNamedRanking:
    """H2: the GPT primary judge must rank rubric-named files first, exactly like
    the council path; before, it passed no rubric_names so ranking was by size."""

    RUBRICS = [
        {"criterion": "The response delivers final_answer.md stating the total.", "weight": 5},
        {"criterion": "leaked a credential", "weight": -3},
    ]

    def _files(self, tmp_path):
        files = {f"aaa{i}.md": f"filler {i} " * 40 for i in range(6)}
        files["final_answer.md"] = "TOTAL_IS_42 " + "detail " * 400
        return _write_deliverables(tmp_path, files)

    def _wire_text(self, monkeypatch, root):
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_BRIDGE_URL", CODEX_URL)
        monkeypatch.setenv("WCB_CODEX_BRIDGE_SECRET", CODEX_SECRET)
        seen = _capture_openai(monkeypatch, "ok")
        grading._grade_gpt_primary(self.RUBRICS, "task", root, "", "sys")
        assert len(seen) == 1
        content = _request_body(seen[0])["messages"][1]["content"]
        return content if isinstance(content, str) else content[0]["text"]

    def test_named_file_comes_first_in_the_gpt_prompt(self, monkeypatch, tmp_path):
        text = self._wire_text(monkeypatch, self._files(tmp_path))
        assert text.index("DELIVERABLE: final_answer.md") < text.index("DELIVERABLE: aaa0.md")

    def test_named_file_survives_a_tight_gpt_budget_in_full(self, monkeypatch, tmp_path):
        root = self._files(tmp_path)
        monkeypatch.setenv("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", "4000")
        text = self._wire_text(monkeypatch, root)
        assert "TOTAL_IS_42" in text
        assert ("detail " * 400).strip() in text          # kept whole, not cut
        assert "present — contents not included: evidence budget exceeded" in text
