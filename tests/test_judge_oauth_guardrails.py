"""Guardrails against the silent all-abstain ("rubric-zero") failure mode.

Covers the three fixes for the 2026-06-17 incident where every criterion in
score.json abstained with criteria_passed=0/criteria_failed=0:

1. `_grade_council` stamps an `error` field (and logs ERROR) when 0/N members
   survive, so an all-abstain score.json cannot masquerade as a graded run.
2. `_call_one_judge` does NOT silently fall back to the urllib/Bedrock path
   when the Sonnet member is configured to judge via the Claude Max OAuth
   bridge — the real bridge error must surface in judge_council.failed.
   Non-sonnet members keep the m0039 fallback contract.
3. `_judge_ssl_context` yields a context with a populated CA store even on
   python.org macOS builds where the default verify paths are empty.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402
from src.utils import judge_litellm  # noqa: E402


SONNET_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/sonnet-1m"
GLM_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/glm-air"
KIMI_ARN = "bedrock/arn:aws:bedrock:us-east-1:111:application-inference-profile/kimi-k2"


def _members():
    return [
        grading.CouncilMember(family="sonnet", model=SONNET_ARN),
        grading.CouncilMember(family="glm", model=GLM_ARN),
        grading.CouncilMember(family="kimi", model=KIMI_ARN),
    ]


def _ok(model, family, verdicts):
    return {
        "model": model,
        "family": family,
        "ok": True,
        "verdicts": verdicts,
        "usage": dict(grading._ZERO_USAGE),
        "user_chars": 100,
    }


def _failed(model, family, error="boom"):
    return {
        "model": model,
        "family": family,
        "ok": False,
        "error": error,
        "usage": dict(grading._ZERO_USAGE),
        "user_chars": 100,
    }


def _rubrics(n=2):
    return [{"criterion": f"c{i}", "weight": 1} for i in range(n)]


# ---------- 1. total council failure is loud ----------

def test_total_council_failure_stamps_error(monkeypatch):
    results = [
        _failed(SONNET_ARN, "sonnet", "call: ssl cert fail"),
        _failed(GLM_ARN, "glm", "call: ssl cert fail"),
        _failed(KIMI_ARN, "kimi", "call: ssl cert fail"),
    ]
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: results)
    out = grading._grade_council(_rubrics(), "sys", "user", _members())
    assert out["criteria_abstained"] == out["criteria_total"] == 2
    assert out["criteria_passed"] == 0 and out["criteria_failed"] == 0
    assert out["overall_score"] == 0.0
    assert "error" in out
    assert "total failure" in out["error"]
    assert "ssl cert fail" in out["error"]


def test_partial_council_failure_has_no_error_field(monkeypatch):
    v = [{"satisfied": True, "rationale": "r", "truncation_affected": False}] * 2
    results = [
        _ok(SONNET_ARN, "sonnet", v),
        _ok(GLM_ARN, "glm", v),
        _failed(KIMI_ARN, "kimi"),
    ]
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: results)
    out = grading._grade_council(_rubrics(), "sys", "user", _members())
    assert "error" not in out
    # sonnet voted -> criteria resolve, nothing abstains
    assert out["criteria_abstained"] == 0
    assert out["criteria_passed"] == 2


# ---------- 2. no silent Bedrock fallback for the OAuth-bridge sonnet ----------

def test_oauth_sonnet_bridge_failure_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:1")

    def _boom(**kwargs):
        raise RuntimeError("bridge 401: oauth token expired")

    monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _boom)
    called = []
    monkeypatch.setattr(
        grading, "_call_judge_bedrock",
        lambda *a, **k: called.append(a) or ("txt", {}),
    )
    with pytest.raises(RuntimeError) as exc_info:
        grading._call_one_judge(SONNET_ARN, "sys", "user", family="sonnet")
    # the REAL bridge error is carried, and the Bedrock path was never touched
    assert "oauth token expired" in str(exc_info.value)
    assert called == []


def test_non_sonnet_member_keeps_urllib_fallback(monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:1")

    def _boom(**kwargs):
        raise RuntimeError("litellm transport hiccup")

    monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _boom)
    monkeypatch.setattr(
        grading, "_call_judge_bedrock", lambda *a, **k: ("fallback-ok", {})
    )
    text, usage = grading._call_one_judge(GLM_ARN, "sys", "user", family="glm")
    assert text == "fallback-ok"


def test_sonnet_without_bridge_keeps_urllib_fallback(monkeypatch):
    """OAuth bridge unset -> the sonnet member still honors the m0039
    fall-through contract (pure-Bedrock deployments are unaffected)."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    monkeypatch.delenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", raising=False)

    def _boom(**kwargs):
        raise RuntimeError("litellm transport hiccup")

    monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _boom)
    monkeypatch.setattr(
        grading, "_call_judge_bedrock", lambda *a, **k: ("fallback-ok", {})
    )
    text, usage = grading._call_one_judge(SONNET_ARN, "sys", "user", family="sonnet")
    assert text == "fallback-ok"


# ---------- 3. urllib judge SSL context has CAs ----------

def test_judge_ssl_context_has_ca_store(monkeypatch):
    monkeypatch.setattr(grading, "_JUDGE_SSL_CTX", None)
    ctx = grading._judge_ssl_context()
    assert ctx.get_ca_certs(), (
        "judge SSL context loaded zero CA certificates — urllib judge calls "
        "would fail CERTIFICATE_VERIFY_FAILED and the council would abstain"
    )
    # cached on second call
    assert grading._judge_ssl_context() is ctx
