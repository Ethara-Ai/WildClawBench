"""F1 — ordinal-bound verdict parsing (`grading._parse_verdict_text` +
`grading._grade_council`).

Before F1 the parser returned a DENSE POSITIONAL list and `_grade_council` bound
`verdicts[i]` to rubric criterion `i`. A judge that emitted verdicts 1,2,4..N
(one skipped criterion — a routine smaller-context miss) therefore shifted every
later verdict one criterion to the LEFT: criterion 3 received criterion 4's
verdict, criterion 4 received 5's, and so on to the end of the chunk. The votes
looked complete and nothing in score.json recorded the scramble.

After F1 each verdict binds to the ordinal the judge itself wrote, so a skipped
verdict abstains exactly one criterion and every other criterion still receives
its own verdict.
"""
from __future__ import annotations

import pytest

from src.utils import grading

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm"


def _members():
    return [
        grading.CouncilMember(family="sonnet", model=_SONNET),
        grading.CouncilMember(family="glm", model=_GLM),
    ]


def _ok(model: str, family: str, verdicts: list[dict]) -> dict:
    return {
        "model": model, "effective_model": model, "family": family,
        "ok": True, "verdicts": verdicts,
        "usage": dict(grading._ZERO_USAGE), "user_chars": 10,
    }


def _grade(monkeypatch, rubrics, results):
    monkeypatch.setattr(
        grading, "_run_council",
        lambda members, system, user, n, images=None: list(results),
    )
    return grading._grade_council(rubrics, "sys", "user", _members())


def _block(ordinal: int, criterion: str, satisfied: str) -> str:
    return (
        f"{ordinal}. {criterion} [[RATIONALE: r{ordinal}]] "
        f"[[SATISFIED: {satisfied}]] [[TRUNCATION_AFFECTED: No]]"
    )


# ---------------------------------------------------------------------------
# _parse_verdict_text — ordinal binding
# ---------------------------------------------------------------------------


def test_skipped_middle_ordinal_does_not_shift_later_verdicts():
    """THE regression test. Judge emits 1,2,4,5 for a 5-criterion chunk."""
    resp = "\n".join([
        _block(1, "c1", "Yes"),
        _block(2, "c2", "No"),
        _block(4, "c4", "Yes"),
        _block(5, "c5", "No"),
    ])
    v = grading._parse_verdict_text(resp, 5)
    assert [x["index"] for x in v] == [0, 1, 3, 4]
    by_idx = {x["index"]: x for x in v}
    assert by_idx[0]["satisfied"] is True
    assert by_idx[1]["satisfied"] is False
    assert by_idx[3]["satisfied"] is True
    assert by_idx[4]["satisfied"] is False
    assert 2 not in by_idx
    # Pre-F1 this list was dense, so rationale "r4" landed on index 2.
    assert by_idx[3]["rationale"] == "r4"


def test_duplicate_ordinal_keeps_first_and_drops_the_rest(caplog):
    resp = "\n".join([
        _block(1, "c1", "Yes"),
        _block(1, "c1 again", "No"),
        _block(2, "c2", "Yes"),
    ])
    with caplog.at_level("WARNING"):
        v = grading._parse_verdict_text(resp, 2)
    assert [x["index"] for x in v] == [0, 1]
    assert v[0]["satisfied"] is True
    assert v[0]["rationale"] == "r1"
    assert "duplicate=['1']" in caplog.text


def test_out_of_range_ordinal_is_dropped_not_folded_into_the_tail(caplog):
    resp = "\n".join([
        _block(1, "c1", "Yes"),
        _block(9, "phantom", "Yes"),
        _block(2, "c2", "No"),
    ])
    with caplog.at_level("WARNING"):
        v = grading._parse_verdict_text(resp, 2)
    assert [x["index"] for x in v] == [0, 1]
    assert v[1]["satisfied"] is False
    assert "out-of-range=['9']" in caplog.text


def test_zero_ordinal_is_out_of_range():
    resp = "\n".join([_block(0, "c0", "Yes"), _block(1, "c1", "Yes")])
    v = grading._parse_verdict_text(resp, 2)
    assert [x["index"] for x in v] == [0]
    assert v[0]["rationale"] == "r1"


def test_every_ordinal_unusable_raises_into_the_existing_parse_retry_path():
    resp = "\n".join([_block(41, "g41", "Yes"), _block(42, "g42", "No")])
    with pytest.raises(ValueError, match="no verdicts bound"):
        grading._parse_verdict_text(resp, 3)


def test_well_formed_list_binds_exactly_as_before():
    resp = "\n".join(_block(i, f"c{i}", "Yes") for i in range(1, 7))
    v = grading._parse_verdict_text(resp, 6)
    assert [x["index"] for x in v] == list(range(6))
    assert [x["satisfied"] for x in v] == [True] * 6
    assert [x["rationale"] for x in v] == [f"r{i}" for i in range(1, 7)]


# ---------------------------------------------------------------------------
# _grade_council — binding end to end
# ---------------------------------------------------------------------------


def test_grade_council_skipped_ordinal_abstains_only_that_criterion(monkeypatch):
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(5)]
    resp = "\n".join([
        _block(1, "c0", "Yes"),
        _block(2, "c1", "Yes"),
        _block(4, "c3", "Yes"),
        _block(5, "c4", "Yes"),
    ])
    skipped = grading._parse_verdict_text(resp, 5)
    full = grading._parse_verdict_text(
        "\n".join(_block(i, f"c{i - 1}", "Yes") for i in range(1, 6)), 5
    )
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", skipped),
        _ok(_GLM, "glm", full),
    ])
    votes = [c["votes"] for c in out["criteria"]]
    assert votes == ["Yes/Yes", "Yes/Yes", "Abstain/Yes", "Yes/Yes", "Yes/Yes"]
    # Criterion 2 lost sonnet (its source of truth) but GLM's Yes is not
    # unanimous on its own -> Human Evaluation, exactly one criterion.
    assert out["abstention_flags"] == [2]
    assert out["criteria"][2]["resolved_by"] == "human_eval"
    for i in (0, 1, 3, 4):
        assert out["criteria"][i]["resolved_by"] == "unanimous"
        assert out["criteria"][i]["passed"] is True
    # Pre-F1 the sonnet rationale on criterion 2 was "r4" (criterion 3's).
    assert out["criteria"][3]["rationales_by_judge"][0] == "r4"


def test_grade_council_positional_fallback_for_index_less_verdicts(monkeypatch):
    """Verdict dicts with no `index` (synthetic fixtures) keep the old binding."""
    rubrics = [{"criterion": "c0", "weight": 1}, {"criterion": "c1", "weight": 1}]
    dense = [
        {"satisfied": True, "rationale": "a", "truncation_affected": False},
        {"satisfied": False, "rationale": "b", "truncation_affected": False},
    ]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", dense),
        _ok(_GLM, "glm", list(dense)),
    ])
    assert [c["satisfied"] for c in out["criteria"]] == [True, False]
    assert [c["resolved_by"] for c in out["criteria"]] == ["unanimous"] * 2
