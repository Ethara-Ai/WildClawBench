"""rubric.json export must be faithful to the authoring rubric (QC item 2).

`_transform_rubrics_for_export` `or`-defaulted `type` -> "objective",
`evaluation_target` -> "state change" and `criterion` -> "" whenever a source
key was absent or shaped differently. A fabricated default is indistinguishable
from an authored value, which is how five delivered report.json files shipped
wrong metadata: megan's targets rewritten, eric/maria/abena/willie's blanked.

Pinned here: full-fidelity roundtrip, honest-unknown + WARN on a genuinely
absent required field, and the two real corruption shapes as regressions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.harbor.bundle import _transform_rubrics_for_export  # noqa: E402
from src.utils.rubric_targets import normalize_target  # noqa: E402


# Verbatim R1 from the delivered megan bundle's authoring rubric.json.
MEGAN_R1 = {
    "number": "R1",
    "criterion": ("The response delivers bay_tool_board.html with a "
                  "photographic image embedded in the page."),
    "is_positive": True,
    "type": "task completion",
    "evaluation_target": "produced_artifact",
    "importance": "important",
    "score": 1,
}

# Verbatim R1 from the delivered eric bundle's authoring rubric.json.
ERIC_R1 = {
    "number": "R1",
    "criterion": "The response delivers case_study.html as a valid HTML document.",
    "is_positive": True,
    "type": "task completion",
    "evaluation_target": "produced_artifact",
    "importance": "important",
    "score": 1,
}


def _one(item, warnings=None):
    return _transform_rubrics_for_export(json.dumps([item]), warnings)[0]


# --------------------------------------------------------------------------- #
# full-fidelity roundtrip
# --------------------------------------------------------------------------- #
def test_authored_values_survive_verbatim():
    warnings: list[str] = []
    out = _one(MEGAN_R1, warnings)
    assert out["criterion"] == MEGAN_R1["criterion"]
    assert out["type"] == "task completion"
    assert out["evaluation_target"] == "produced_artifact"
    assert out["importance"] == "important"
    assert out["score"] == 1
    assert out["is_positive"] is True
    assert out["number"] == "R1"
    assert warnings == []


def test_full_rubric_roundtrips_every_criterion():
    source = [dict(MEGAN_R1, number=f"R{i}", score=i) for i in range(1, 6)]
    out = _transform_rubrics_for_export(json.dumps(source))
    assert len(out) == 5
    for src, got in zip(source, out):
        assert got["type"] == src["type"]
        assert got["evaluation_target"] == src["evaluation_target"]
        assert got["criterion"] == src["criterion"]
        assert got["score"] == src["score"]
        assert got["number"] == src["number"]


def test_authored_numbering_is_preserved_not_recomputed():
    # A de-anchored rubric drops a criterion; positional renumbering would slide
    # every later R-number onto the wrong criterion and break the match-by-number
    # join in backfill_bundle_meta.py.
    source = [dict(MEGAN_R1, number="R1"), dict(MEGAN_R1, number="R3")]
    out = _transform_rubrics_for_export(json.dumps(source))
    assert [r["number"] for r in out] == ["R1", "R3"]


def test_missing_number_still_falls_back_to_position():
    source = [{k: v for k, v in MEGAN_R1.items() if k != "number"}]
    assert _transform_rubrics_for_export(json.dumps(source))[0]["number"] == "R1"


def test_evaluation_target_alias_resolves_through_the_canonical_normalizer():
    out = _one(dict(MEGAN_R1, evaluation_target="produced_file"))
    assert out["evaluation_target"] == normalize_target("produced_file")
    assert out["evaluation_target"] == "workspace_artifact"


@pytest.mark.parametrize("target", ["produced_artifact", "state_change",
                                    "final_answer", "trajectory"])
def test_the_delivered_corpus_targets_pass_through_unchanged(target):
    assert _one(dict(MEGAN_R1, evaluation_target=target))["evaluation_target"] == target


def test_weight_keyed_rubrics_export_their_real_score():
    # grading._extract_weight reads weight-then-score; `item.get("score") or 0`
    # exported every weight-keyed criterion at 0.
    source = {"criterion": "c", "type": "guardrail",
              "evaluation_target": "trajectory", "weight": -5}
    out = _one(source)
    assert out["score"] == -5


def test_absent_is_positive_derives_from_the_score_sign():
    negative = _one({"criterion": "c", "type": "guardrail",
                     "evaluation_target": "trajectory", "score": -5})
    positive = _one({"criterion": "c", "type": "task completion",
                     "evaluation_target": "trajectory", "score": 3})
    assert negative["is_positive"] is False
    assert positive["is_positive"] is True


def test_explicit_is_positive_wins_over_the_score_sign():
    assert _one(dict(MEGAN_R1, is_positive=False))["is_positive"] is False


def test_authored_score_zero_is_not_swallowed():
    out = _one(dict(MEGAN_R1, score=0))
    assert out["score"] == 0


# --------------------------------------------------------------------------- #
# absent required field: honest unknown + WARN, never a plausible default
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["type", "evaluation_target", "criterion"])
def test_absent_required_field_exports_the_unknown_sentinel_and_warns(field):
    source = {k: v for k, v in MEGAN_R1.items() if k != field}
    if field == "criterion":
        source.pop("label", None)
    warnings: list[str] = []
    out = _one(source, warnings)
    assert out[field] == ""
    assert any(field in w and "R1" in w for w in warnings)


def test_absent_score_warns_and_does_not_invent_a_weight():
    source = {k: v for k, v in MEGAN_R1.items() if k != "score"}
    warnings: list[str] = []
    out = _one(source, warnings)
    assert out["score"] == 0
    assert any("'score'" in w for w in warnings)


def test_empty_string_type_is_treated_as_absent_not_as_an_authored_value():
    warnings: list[str] = []
    out = _one(dict(MEGAN_R1, type="   "), warnings)
    assert out["type"] == ""
    assert any("'type'" in w for w in warnings)


def test_a_clean_rubric_produces_no_warnings():
    warnings: list[str] = []
    _transform_rubrics_for_export(json.dumps([MEGAN_R1, ERIC_R1]), warnings)
    assert warnings == []


# --------------------------------------------------------------------------- #
# regressions: the exact delivered corruption shapes
# --------------------------------------------------------------------------- #
def test_megan_r1_is_never_rewritten_to_a_different_target():
    # Delivered megan report.json carried type "tool use" / target "trajectory"
    # for a criterion authored "task completion" / "produced_artifact".
    out = _one(MEGAN_R1)
    assert (out["type"], out["evaluation_target"]) == ("task completion", "produced_artifact")
    assert out["type"] != "tool use"
    assert out["evaluation_target"] not in ("trajectory", "state change")


def test_eric_r1_is_never_blanked():
    # Delivered eric report.json carried type "" / evaluation_target "" for a
    # criterion that authored both.
    out = _one(ERIC_R1)
    assert out["type"] == "task completion"
    assert out["evaluation_target"] == "produced_artifact"
    assert out["criterion"] == ERIC_R1["criterion"]


def test_no_criterion_is_ever_stamped_with_the_old_hardcoded_defaults():
    source = [MEGAN_R1, ERIC_R1,
              {"criterion": "c", "type": "tool use",
               "evaluation_target": "trajectory", "score": 2}]
    out = _transform_rubrics_for_export(json.dumps(source))
    assert not any(r["type"] == "objective" for r in out)
    assert not any(r["evaluation_target"] == "state change" for r in out)


def test_label_keyed_criterion_still_maps(tmp_path):
    out = _one({"label": "Posted the refund", "type": "task completion",
                "evaluation_target": "state_change", "score": 3})
    assert out["criterion"] == "Posted the refund"


def test_non_dict_rows_are_skipped_without_corrupting_the_rest():
    raw = json.dumps([MEGAN_R1, "not-a-dict", ERIC_R1])
    out = _transform_rubrics_for_export(raw)
    assert len(out) == 2
    assert [r["criterion"] for r in out] == [MEGAN_R1["criterion"], ERIC_R1["criterion"]]


@pytest.mark.parametrize("raw", [None, "", "not json", "{}", json.dumps({"a": 1})])
def test_unparseable_rubric_blobs_still_return_an_empty_list(raw):
    assert _transform_rubrics_for_export(raw) == []
