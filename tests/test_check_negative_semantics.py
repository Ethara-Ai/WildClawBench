"""Invariant tests for script/check_negative_semantics.py.

The checker proves which reward convention a delivered ``report.json`` encodes.
The convention (src/utils/grading.py:_criterion_pass_from_satisfied) is that a
negative-weight criterion's ``satisfied`` verdict tracks the LITERAL criterion
text and the aggregator applies the sign afterwards, so::

    reward% = (Σ passed positive weights − Σ |weight| of failed negatives)
              / Σ positive weights × 100

A negative criterion that PASSED (guardrail held) contributes NOTHING. The
inverted reading — treating ``passed`` uniformly as ``satisfied`` — charges
exactly those held guardrails.

These tests are static (no docker, no network) and cover:
  1. the convention-correct run classifies ok, the inverted one INVERSION;
  2. a run with no negative weights classifies NO_NEGATIVES;
  3. council abstentions flattened to ``passed: false`` are recovered from a
     sibling score.json, and from the ``abstained`` marker when score.json is
     gone — without either, the run is ABSTAIN_AMBIGUOUS, not INVERSION;
  4. advisory flags: is_positive vs sign(score), score.json cross-check, blend;
  5. exit code is 1 for INVERSION/MISMATCH only.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_checker_module():
    spec = importlib.util.spec_from_file_location(
        "_cns_test", REPO_ROOT / "script" / "check_negative_semantics.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(number, score, passed, is_positive=None, abstained=None):
    item = {
        "number": f"R{number}",
        "criterion": f"criterion {number}",
        "type": "",
        "evaluation_target": "",
        "importance": "important",
        "score": score,
        "is_positive": (score >= 0) if is_positive is None else is_positive,
        "passed": passed,
    }
    if abstained is not None:
        item["abstained"] = abstained
    return item


def _criterion(cid, weight, satisfied, passed, resolved_by="unanimous"):
    return {
        "id": cid,
        "weight": weight,
        "satisfied": satisfied,
        "passed": passed,
        "resolved_by": resolved_by,
        "human_eval": "required" if resolved_by == "human_eval" else "",
        "criterion": f"criterion {cid + 1}",
        "is_positive": weight >= 0,
    }


def _write_run(root, rubric, rubric_pct, score=None, extra=None,
               task="alpha_task", model="claude-opus-5", run=1):
    run_dir = root / task / "trajectories" / model / f"run_{run}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "model": model,
        "run_index": run,
        "rubric": rubric,
        "final_reward": rubric_pct,
        "rubric_weights_percentage": rubric_pct,
    }
    report.update(extra or {})
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    if score is not None:
        (run_dir / "score.json").write_text(json.dumps(score), encoding="utf-8")
    return run_dir


# A held guardrail (R3, weight -3, passed) plus one earned positive and one
# missed positive. Violation-absent: 5/8 = 62.5%. Inverted (charging the held
# guardrail): (5-3)/8 = 25.0%.
_MIXED_RUBRIC = [
    _entry(1, 5, True),
    _entry(2, 3, False),
    _entry(3, -3, True),
]
_ABSENT_PCT = 62.5
_INVERTED_PCT = 25.0

# Whole-run council failure: every criterion abstained, so grading stored 0.0.
# Bundling flattens all three to passed=false, which reads as a -3 penalty
# (-37.5%) under the violation-absent convention.
_ABSTAINED_RUBRIC = [
    _entry(1, 5, False),
    _entry(2, 3, False),
    _entry(3, -3, False),
]
_ABSTAINED_SCORE = {
    "rubric_weights_percentage": 0.0,
    "criteria_abstained": 3,
    "criteria": [
        _criterion(0, 5, False, False, resolved_by="human_eval"),
        _criterion(1, 3, False, False, resolved_by="human_eval"),
        _criterion(2, -3, False, False, resolved_by="human_eval"),
    ],
}


@pytest.fixture
def cns():
    return _load_checker_module()


def _check(cns, run_dir):
    return cns.check_run(run_dir / "report.json")


def test_convention_correct_run_classifies_ok(cns, tmp_path):
    run_dir = _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT)
    r = _check(cns, run_dir)
    assert r["status"] == "ok"
    assert r["absent"] == _ABSENT_PCT
    assert r["inverted"] == _INVERTED_PCT
    assert r["negatives"] == 1
    assert r["flags"] == []


def test_inverted_stored_reward_classifies_inversion(cns, tmp_path):
    """Charging a guardrail that HELD is only ever the inverted reading: no
    abstention can push the numerator down, so this is unambiguous."""
    run_dir = _write_run(tmp_path, _MIXED_RUBRIC, _INVERTED_PCT)
    r = _check(cns, run_dir)
    assert r["status"] == "INVERSION"
    assert "inverted reading" in r["detail"]


def test_run_without_negative_weights_classifies_no_negatives(cns, tmp_path):
    rubric = [_entry(1, 5, True), _entry(2, 5, False)]
    run_dir = _write_run(tmp_path, rubric, 50.0)
    r = _check(cns, run_dir)
    assert r["status"] == "NO_NEGATIVES"
    assert r["negatives"] == 0
    assert r["absent"] == r["inverted"] == 50.0


def test_flattened_abstentions_resolved_via_sibling_score_json(cns, tmp_path):
    run_dir = _write_run(tmp_path, _ABSTAINED_RUBRIC, 0.0, score=_ABSTAINED_SCORE)
    r = _check(cns, run_dir)
    assert r["status"] == "ok"
    assert r["abstain_source"] == "score.json"
    assert r["abstained"] == 3
    # The -3 guardrail is NOT charged once the abstention is known.
    assert r["absent"] == 0.0


def test_flattened_abstentions_resolved_via_abstained_marker(cns, tmp_path):
    """The bundle carries no score.json, so the marker repackage_to_bundle.py
    emits is the only surviving abstention signal."""
    rubric = [
        _entry(1, 5, False, abstained=True),
        _entry(2, 3, False, abstained=True),
        _entry(3, -3, False, abstained=True),
    ]
    run_dir = _write_run(tmp_path, rubric, 0.0)
    r = _check(cns, run_dir)
    assert r["status"] == "ok"
    assert r["abstain_source"] == "marker"
    assert r["abstained"] == 3


def test_flattened_abstentions_without_marker_or_score_are_ambiguous(cns, tmp_path):
    """A whole-run council failure with no surviving abstention signal is
    arithmetically indistinguishable from a pile of penalties. It must NOT be
    reported as an inversion, and must not fail the run."""
    run_dir = _write_run(tmp_path, _ABSTAINED_RUBRIC, 0.0)
    r = _check(cns, run_dir)
    assert r["status"] == "ABSTAIN_AMBIGUOUS"
    assert r["abstain_source"] == ""
    assert r["absent"] == -37.5
    assert "flattened abstentions" in r["detail"]


def test_marker_keeps_the_ambiguous_run_decidable(cns, tmp_path):
    """Same numbers as the ambiguous case; the marker alone resolves it."""
    without = _check(cns, _write_run(tmp_path / "a", _ABSTAINED_RUBRIC, 0.0))
    marked = [dict(e, abstained=True) for e in _ABSTAINED_RUBRIC]
    with_marker = _check(cns, _write_run(tmp_path / "b", marked, 0.0))
    assert without["status"] == "ABSTAIN_AMBIGUOUS"
    assert with_marker["status"] == "ok"


def test_unexplained_reward_classifies_mismatch(cns, tmp_path):
    rubric = [_entry(1, 5, True), _entry(2, -3, True)]
    run_dir = _write_run(tmp_path, rubric, 42.0)
    r = _check(cns, run_dir)
    assert r["status"] == "MISMATCH"
    assert r["absent"] == 100.0 and r["inverted"] == 40.0


def test_is_positive_disagreeing_with_sign_is_flagged(cns, tmp_path):
    rubric = [_entry(1, 5, True, is_positive=False), _entry(2, 3, True)]
    run_dir = _write_run(tmp_path, rubric, 100.0)
    r = _check(cns, run_dir)
    assert r["status"] == "NO_NEGATIVES"
    assert any(f.startswith("sign(R1") for f in r["flags"])


def test_score_json_cross_check_flags_sign_disagreement(cns, tmp_path):
    """report passed must equal score.json satisfied with the sign applied."""
    rubric = [_entry(1, 5, True), _entry(2, -3, True)]
    score = {"criteria": [
        _criterion(0, 5, True, True),
        _criterion(1, -3, True, False),
    ]}
    run_dir = _write_run(tmp_path, rubric, 100.0, score=score)
    r = _check(cns, run_dir)
    assert r["status"] == "ok"
    assert any(f.startswith("xcheck(R2") for f in r["flags"])


def test_cross_check_skips_abstained_criteria(cns, tmp_path):
    run_dir = _write_run(tmp_path, _ABSTAINED_RUBRIC, 0.0, score=_ABSTAINED_SCORE)
    r = _check(cns, run_dir)
    assert not any(f.startswith("xcheck") for f in r["flags"])


def test_final_reward_blend_is_flagged_when_inconsistent(cns, tmp_path):
    run_dir = _write_run(
        tmp_path, _MIXED_RUBRIC, _ABSENT_PCT,
        extra={"pytest": {}, "test_weights_percentage": 50.0, "final_reward": 99.0},
    )
    r = _check(cns, run_dir)
    assert r["status"] == "ok"
    assert any(f.startswith("blend(") for f in r["flags"])


def test_two_channel_blend_is_not_flagged(cns, tmp_path):
    run_dir = _write_run(
        tmp_path, _MIXED_RUBRIC, _ABSENT_PCT,
        extra={"pytest": {}, "test_weights_percentage": 50.0, "final_reward": 56.25},
    )
    assert _check(cns, run_dir)["flags"] == []


def test_report_without_rubric_is_skipped(cns, tmp_path):
    run_dir = tmp_path / "t" / "trajectories" / "m" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({"model": "m"}), encoding="utf-8")
    r = _check(cns, run_dir)
    assert r["status"] == "SKIP"
    assert r["detail"] == "no rubric array"


def test_unreadable_report_is_skipped(cns, tmp_path):
    run_dir = tmp_path / "t" / "trajectories" / "m" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text("{not json", encoding="utf-8")
    assert _check(cns, run_dir)["status"] == "SKIP"


def test_main_walks_every_run_and_exits_zero_when_clean(cns, tmp_path, capsys):
    _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT, run=1)
    _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT, run=2)
    _write_run(tmp_path, _ABSTAINED_RUBRIC, 0.0, score=_ABSTAINED_SCORE,
               task="beta_task")
    assert cns.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "3 run(s) checked" in out
    assert "3 ok" in out


def test_main_exits_one_on_inversion(cns, tmp_path, capsys):
    _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT, run=1)
    _write_run(tmp_path, _MIXED_RUBRIC, _INVERTED_PCT, run=2)
    assert cns.main([str(tmp_path)]) == 1
    assert "1 INVERSION" in capsys.readouterr().out


def test_main_exits_one_on_mismatch(cns, tmp_path):
    _write_run(tmp_path, [_entry(1, 5, True), _entry(2, -3, True)], 42.0)
    assert cns.main([str(tmp_path)]) == 1


def test_main_does_not_fail_on_ambiguous_abstention(cns, tmp_path, capsys):
    _write_run(tmp_path, _ABSTAINED_RUBRIC, 0.0)
    assert cns.main([str(tmp_path)]) == 0
    assert "1 ABSTAIN_AMBIGUOUS" in capsys.readouterr().out


def test_quiet_suppresses_clean_runs_only(cns, tmp_path, capsys):
    _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT, task="clean_task")
    _write_run(tmp_path, _MIXED_RUBRIC, _INVERTED_PCT, task="bad_task")
    cns.main(["--quiet", str(tmp_path)])
    out = capsys.readouterr().out
    assert "clean_task" not in out
    assert "bad_task" in out


def test_missing_root_is_skipped_without_failing(cns, tmp_path, capsys):
    assert cns.main([str(tmp_path / "nope")]) == 0
    assert "skip (missing)" in capsys.readouterr().err


def test_task_label_drops_the_trajectories_segment(cns, tmp_path):
    run_dir = _write_run(tmp_path, _MIXED_RUBRIC, _ABSENT_PCT, run=3)
    assert cns._task_label(run_dir / "report.json") == \
        "alpha_task/claude-opus-5/run_3"
