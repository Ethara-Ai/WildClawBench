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


# ---------------------------------------------------------------------------
# F1c — a number in the judge's PREAMBLE must not open a verdict match
#
# _VERDICT_RE's echo group is non-greedy under DOTALL, so the FIRST "<digits>. "
# anywhere in the response used to anchor verdict 1 and swallow the real one:
# the parser bound the preamble prose as criterion 1's echo, the F1b canary saw
# a criterion that was not criterion 1, and criterion 1 abstained. Two shapes
# were observed across 930 archived judge responses — a mid-line number (285
# responses) and a markdown-numbered findings list before <judgment> (138).
# ---------------------------------------------------------------------------


_JUDGED = "\n".join(_block(i, f"criterion {i} text", "Yes") for i in range(1, 5))


def _wrapped(preamble: str) -> str:
    return f"{preamble}\n\n<judgment>\n{_JUDGED}\n</judgment>"


@pytest.mark.parametrize(
    "preamble",
    [
        # V2 re-parse phantoms, verbatim shapes: currency tail, decimal tail,
        # reference number, and an IN-RANGE value that silently re-bound
        # verdict 1's SATISFIED onto criterion 32 (a -5 penalty).
        "The invoice totals \u20bd149,500. net of VAT, which the agent copied.",
        "The spreadsheet cell reads 1,480.00. The agent then rounded it.",
        "Order reference INV-4419. was present in the packet.",
        "Across the run the agent touched 33. Files were listed below.",
    ],
    ids=["currency_149500", "decimal_00", "reference_4419", "in_range_33"],
)
def test_midline_number_in_preamble_cannot_open_a_verdict(preamble):
    v = grading._parse_verdict_text(f"{preamble}\n\n{_JUDGED}", 4)
    assert [x["index"] for x in v] == [0, 1, 2, 3]
    assert v[0]["echo"] == "criterion 1 text"
    assert v[0]["rationale"] == "r1"


def test_numbered_preamble_list_before_judgment_does_not_steal_verdict_one():
    """The koji_holder shape: a markdown findings list at LINE START, then the
    real verdicts inside <judgment>. Reproduced from a real archived response
    (avalon_pierce run_7), where criterion 1 (weight 5.0) abstained because its
    echo was the preamble, scoring 0.268 against the 0.45 canary threshold."""
    preamble = (
        "**Key findings from the output files:**\n"
        "\n"
        "1. **bench_run_packet.pdf** - The agent produced this file but its "
        "contents are not extractable.\n"
        "\n"
        "2. Earlier versions marked FE-118 ES as \"RELEASE.\"\n"
        "\n"
        "3. The agent never wrote to `item-0022`."
    )
    v = grading._parse_verdict_text(_wrapped(preamble), 4)
    assert [x["index"] for x in v] == [0, 1, 2, 3]
    # The real verdict 1, not the PDF bullet.
    assert v[0]["echo"] == "criterion 1 text"
    assert v[0]["rationale"] == "r1"
    assert "bench_run_packet" not in v[0]["echo"]
    # And the canary that fired in production now passes.
    ok, ratio = grading._echo_matches_criterion(v[0]["echo"], "criterion 1 text")
    assert ok and ratio == 1.0


def test_numbered_preamble_does_not_abstain_criterion_one(monkeypatch, caplog):
    """End to end: the preamble shape must cost no criterion a vote."""
    rubrics = [{"criterion": f"criterion {i} text", "weight": 5.0} for i in range(1, 5)]
    preamble = "1. **packet.pdf** - contents are not extractable.\n\n2. notes.md present."
    v = grading._parse_verdict_text(_wrapped(preamble), 4)
    with caplog.at_level("WARNING"):
        out = _grade(monkeypatch, rubrics, [
            _ok(_SONNET, "sonnet", v),
            _ok(_GLM, "glm", list(v)),
        ])
    assert out["abstention_flags"] == []
    assert [c["votes"] for c in out["criteria"]] == ["Yes/Yes"] * 4
    assert "echo mismatch" not in caplog.text


def test_midline_number_inside_a_rationale_does_not_split_the_verdict():
    resp = "\n".join([
        "1. criterion 1 text [[RATIONALE: the agent paid 1480. The next "
        "criterion is unrelated]] [[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: No]]",
        _block(2, "criterion 2 text", "No"),
    ])
    v = grading._parse_verdict_text(resp, 2)
    assert [x["index"] for x in v] == [0, 1]
    assert v[0]["rationale"] == "the agent paid 1480. The next criterion is unrelated"
    assert v[1]["satisfied"] is False


def test_indented_verdicts_still_match():
    resp = "\n".join("   " + _block(i, f"c{i}", "Yes") for i in range(1, 4))
    v = grading._parse_verdict_text(resp, 3)
    assert [x["index"] for x in v] == [0, 1, 2]
    assert [x["echo"] for x in v] == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# F1c part 2 — <judgment> scoping, and its non-lossy fallbacks
# ---------------------------------------------------------------------------


def test_verdicts_emitted_outside_the_tags_still_parse():
    """Fallback: an empty scoped region must never lose a verdict list."""
    resp = f"{_JUDGED}\n<judgment>\nsee above\n</judgment>"
    v = grading._parse_verdict_text(resp, 4)
    assert [x["index"] for x in v] == [0, 1, 2, 3]
    assert v[0]["echo"] == "criterion 1 text"


def test_unclosed_judgment_tag_keeps_the_partial_list():
    """A smaller-context judge truncated mid-list: the close tag never arrives,
    so the region runs to end of text and partial coverage is preserved."""
    resp = "1. **packet.pdf** - not extractable.\n\n<judgment>\n" + "\n".join(
        _block(i, f"criterion {i} text", "Yes") for i in (1, 2)
    )
    v = grading._parse_verdict_text(resp, 4)
    assert [x["index"] for x in v] == [0, 1]
    assert v[0]["echo"] == "criterion 1 text"


def test_trailing_duplicate_blocks_after_the_close_tag_are_ignored():
    """A judge that recaps some verdicts AFTER </judgment> used to have those
    recaps parsed as duplicate ordinals; scoping drops them at the source."""
    resp = _wrapped("preamble prose") + "\n\nRecap:\n" + _block(1, "recap", "No")
    v = grading._parse_verdict_text(resp, 4)
    assert [x["index"] for x in v] == [0, 1, 2, 3]
    assert v[0]["satisfied"] is True
    assert v[0]["echo"] == "criterion 1 text"


def test_judgment_region_helper_fallbacks():
    assert grading._judgment_region("no tags here") == "no tags here"
    assert grading._judgment_region("a <judgment>\nx\n</judgment> b").strip() == "x"
    assert grading._judgment_region("a <judgment>\nx").strip() == "x"
    # Empty block -> whole text, so the caller can still find a bare list.
    whole = "1. c [[RATIONALE: r]] [[SATISFIED: Yes]]\n<judgment></judgment>"
    assert grading._judgment_region(whole) == whole
