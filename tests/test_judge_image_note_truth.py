"""F4a — the "ATTACHED IMAGES ... authoritative evidence" note is a factual
claim about the message it rides on, so it may only go to members that actually
receive the pixels.

Before F4a the note was prepended to EVERY member's user prompt whenever images
had merely been COLLECTED. Pixels are gated twice — vision-capable family, and a
transport that can carry image blocks at all — so text-only council members were
told that authoritative images were attached to a message carrying none, and
then graded image-content criteria against that fiction instead of falling back
to the "(image WxH, presence only)" markers in <output_files>.

`_will_receive_pixels` is the SHARED predicate: F4b routes pixels by the same
call, so the note can never again disagree with the routing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils import grading

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm"

_IMG = [{"name": "hero.png", "media_type": "image/png", "b64": "QUJD"}]


def _sonnet():
    return grading.CouncilMember(family="sonnet", model=_SONNET)


def _glm():
    return grading.CouncilMember(family="glm", model=_GLM)


# ---------------------------------------------------------------------------
# _will_receive_pixels — the shared predicate
# ---------------------------------------------------------------------------


def test_no_images_means_nobody_receives_pixels(monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    assert grading._will_receive_pixels(_sonnet(), None) is False
    assert grading._will_receive_pixels(_sonnet(), []) is False


def test_text_only_family_never_receives_pixels(monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    assert grading._will_receive_pixels(_glm(), _IMG) is False


def test_vision_family_on_a_pixel_capable_transport_receives_pixels(monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    assert grading._will_receive_pixels(_sonnet(), _IMG) is True


def test_vision_family_receives_pixels_regardless_of_the_litellm_flag(monkeypatch):
    """F4b: an image-bearing chunk routes its vision member through the
    multimodal transport unconditionally, so the predicate has no flag term."""
    monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
    assert grading._will_receive_pixels(_sonnet(), _IMG) is True
    assert grading._will_receive_pixels(_glm(), _IMG) is False


# ---------------------------------------------------------------------------
# grade_with_rubric — which member prompts carry the note
# ---------------------------------------------------------------------------


def _capture_prompts(monkeypatch, tmp_path: Path, *, litellm: str | None):
    """Run grade_with_rubric over a rubric naming an image and return
    {family: user_prompt} exactly as _grade_council would have received it."""
    if litellm is None:
        monkeypatch.delenv("KENSEI_JUDGE_USE_LITELLM", raising=False)
    else:
        monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", litellm)

    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    png = (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
        + (7).to_bytes(4, "big") + (5).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00" + b"\x00" * 16
    )
    (results / "hero.png").write_bytes(png)

    members = [_sonnet(), _glm()]
    monkeypatch.setattr(grading, "council_members", lambda: members)
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)

    seen: dict = {}

    def _fake_council(rubrics, system, user_for_member, mem, images=None):
        seen["prompts"] = {
            m.family: user_for_member[m.model] for m in mem
        }
        seen["images"] = images
        return {
            "overall_score": 1.0, "rubric_weights_percentage": 100.0,
            "criteria_total": len(rubrics), "criteria_passed": len(rubrics),
            "criteria_failed": 0, "criteria_abstained": 0, "criteria": [],
            "judge_model": "council", "judge_council": {},
            "truncation_flags": [], "abstention_flags": [],
            "usage": dict(grading._ZERO_USAGE),
        }

    monkeypatch.setattr(grading, "_grade_council", _fake_council)
    grading.grade_with_rubric(
        [{"criterion": "hero.png shows a red circle", "weight": 1}],
        "task", results, "transcript",
    )
    return seen


def test_text_only_member_is_not_told_images_are_attached(monkeypatch, tmp_path):
    seen = _capture_prompts(monkeypatch, tmp_path, litellm="1")
    assert seen["images"], "image should have been collected"
    assert "ATTACHED IMAGES" in seen["prompts"]["sonnet"]
    assert "ATTACHED IMAGES" not in seen["prompts"]["glm"]


def test_the_note_does_not_depend_on_the_litellm_flag(monkeypatch, tmp_path):
    """F4b routes pixels either way, so the note must be told either way."""
    seen = _capture_prompts(monkeypatch, tmp_path, litellm=None)
    assert seen["images"]
    assert "ATTACHED IMAGES" in seen["prompts"]["sonnet"]
    assert "ATTACHED IMAGES" not in seen["prompts"]["glm"]


def test_presence_only_markers_are_unaffected_by_the_note_gate(monkeypatch, tmp_path):
    """Every member keeps the '(image WxH, presence only)' evidence marker —
    the note gate changes only the authoritative-pixels claim."""
    for litellm in ("1", None):
        seen = _capture_prompts(monkeypatch, tmp_path, litellm=litellm)
        for family in ("sonnet", "glm"):
            assert "hero.png" in seen["prompts"][family]
            assert "image 7x5, presence only" in seen["prompts"][family]
