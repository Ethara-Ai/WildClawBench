"""F4b — image-bearing chunks actually deliver their pixels.

`_collect_image_attachments` collects, base64s and pays for the deliverable
images a chunk's criteria name, and `_run_council` handed them to
`_call_one_judge`. But the LiteLLM transport is the only one that builds a
multimodal user turn: `_call_judge_bedrock` and `_call_judge_openai` have no
`images` parameter at all. Under the default configuration
(KENSEI_JUDGE_USE_LITELLM off) every attached image was therefore dropped on
the floor, while F4a's note told the judge those pixels were the authoritative
evidence.

F4b routes a pixel-carrying call through LiteLLM unconditionally — not behind a
flag — and `_will_receive_pixels` is the single predicate shared with F4a, so
the note can never again disagree with the routing.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from src.utils import grading

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm"

_IMG = [{"name": "hero.png", "media_type": "image/png", "b64": "QUJD"}]
_VERDICT = "1. c [[RATIONALE: r]] [[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: No]]"


def _members():
    return [
        grading.CouncilMember(family="sonnet", model=_SONNET),
        grading.CouncilMember(family="glm", model=_GLM),
    ]


def _install_fake_judge_litellm(monkeypatch, fake: types.ModuleType) -> None:
    """`from . import judge_litellm` reads the ATTRIBUTE off the already-imported
    `src.utils` package whenever some earlier test imported the real module, so
    patching sys.modules alone silently does nothing in a full-suite run."""
    import src.utils as _su
    monkeypatch.setitem(sys.modules, "src.utils.judge_litellm", fake)
    monkeypatch.setattr(_su, "judge_litellm", fake, raising=False)


@pytest.fixture
def transports(monkeypatch):
    """Record which transport each member's call took, and with what images."""
    seen = {"litellm": [], "bedrock": [], "openai": []}

    fake = types.ModuleType("src.utils.judge_litellm")

    def _call_via_litellm(model, system, user, max_output_tokens, cost_fn,
                          family=None, images=None):
        seen["litellm"].append({"family": family, "model": model, "images": images})
        return (_VERDICT, dict(grading._ZERO_USAGE))

    fake.call_judge_via_litellm = _call_via_litellm
    _install_fake_judge_litellm(monkeypatch, fake)

    def _bedrock(model, system, user, family=None):
        seen["bedrock"].append({"family": family, "model": model})
        return (_VERDICT, dict(grading._ZERO_USAGE))

    def _openai(model, system, user):
        seen["openai"].append({"model": model})
        return (_VERDICT, dict(grading._ZERO_USAGE))

    monkeypatch.setattr(grading, "_call_judge_bedrock", _bedrock)
    monkeypatch.setattr(grading, "_call_judge_openai", _openai)
    monkeypatch.setattr(grading, "_member_max_output_tokens", lambda *a, **k: 8192)
    monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
    return seen


def test_image_bearing_chunk_routes_the_vision_member_through_litellm(
    monkeypatch, transports,
):
    grading._run_council(_members(), "sys", "user", 1, images=_IMG)
    # Sonnet took the multimodal lane, carrying the actual pixels.
    assert len(transports["litellm"]) == 1
    assert transports["litellm"][0]["family"] == "sonnet"
    assert transports["litellm"][0]["images"] == _IMG
    # The text-only member is untouched: still the plain Bedrock path.
    assert [c["family"] for c in transports["bedrock"]] == ["glm"]


def test_non_image_chunk_leaves_every_member_on_the_bedrock_path(
    monkeypatch, transports,
):
    grading._run_council(_members(), "sys", "user", 1, images=None)
    assert transports["litellm"] == []
    assert sorted(c["family"] for c in transports["bedrock"]) == ["glm", "sonnet"]


def test_litellm_flag_on_still_routes_everyone_through_litellm(
    monkeypatch, transports,
):
    """F4b adds a route; it does not remove the existing opt-in one."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    grading._run_council(_members(), "sys", "user", 1, images=None)
    assert sorted(c["family"] for c in transports["litellm"]) == ["glm", "sonnet"]
    assert transports["bedrock"] == []


def test_pixel_loss_on_the_litellm_fallback_is_logged(monkeypatch, caplog):
    """If LiteLLM dies we still grade (text-only) rather than abstain — loudly."""
    fake = types.ModuleType("src.utils.judge_litellm")

    def _boom(**kwargs):
        raise RuntimeError("litellm had a bad day")

    fake.call_judge_via_litellm = _boom
    _install_fake_judge_litellm(monkeypatch, fake)
    monkeypatch.setattr(grading, "_member_max_output_tokens", lambda *a, **k: 8192)
    monkeypatch.setattr(
        grading, "_call_judge_bedrock",
        lambda model, system, user, family=None: (_VERDICT, dict(grading._ZERO_USAGE)),
    )
    monkeypatch.setattr(grading.auth_provider, "resolve_provider", lambda: "bedrock")
    with caplog.at_level("WARNING"):
        raw, _ = grading._call_one_judge(_SONNET, "sys", "user", "sonnet", _IMG)
    assert raw == _VERDICT
    assert "loses 1 attached image" in caplog.text


# ---------------------------------------------------------------------------
# F4a note <-> F4b routing agreement (one shared predicate)
# ---------------------------------------------------------------------------


def _run_grade(monkeypatch, tmp_path: Path, *, with_image: bool):
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    if with_image:
        (results / "hero.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
            + (7).to_bytes(4, "big") + (5).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00" + b"\x00" * 16
        )
    else:
        (results / "report.md").write_text("plain text deliverable\n")

    members = _members()
    monkeypatch.setattr(grading, "council_members", lambda: members)
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)
    criterion = "hero.png shows a red circle" if with_image else "report.md exists"
    captured: dict = {}

    def _capture_council(rubrics, system, user_for_member, mem, images=None):
        captured["notes"] = {
            m.family: "ATTACHED IMAGES" in user_for_member[m.model] for m in mem
        }
        captured["routes"] = {
            m.family: grading._will_receive_pixels(m, images) for m in mem
        }
        return {
            "overall_score": 1.0, "rubric_weights_percentage": 100.0,
            "criteria_total": 1, "criteria_passed": 1, "criteria_failed": 0,
            "criteria_abstained": 0, "criteria": [], "judge_model": "council",
            "judge_council": {}, "truncation_flags": [], "abstention_flags": [],
            "usage": dict(grading._ZERO_USAGE),
        }

    monkeypatch.setattr(grading, "_grade_council", _capture_council)
    grading.grade_with_rubric(
        [{"criterion": criterion, "weight": 1}], "task", results, "t")
    return captured


def test_note_agrees_with_routing_on_an_image_chunk(monkeypatch, tmp_path):
    monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
    cap = _run_grade(monkeypatch, tmp_path, with_image=True)
    assert cap["notes"] == cap["routes"]
    assert cap["routes"] == {"sonnet": True, "glm": False}


def test_note_agrees_with_routing_on_a_text_chunk(monkeypatch, tmp_path):
    monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
    cap = _run_grade(monkeypatch, tmp_path, with_image=False)
    assert cap["notes"] == cap["routes"]
    assert cap["routes"] == {"sonnet": False, "glm": False}
