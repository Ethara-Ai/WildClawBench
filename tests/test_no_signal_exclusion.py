"""No-signal judge results must never be averaged in as a genuine 0%.

Covers the two halves of the fix:

  1.1  grading._grade_council marks a council that cast no verdicts at all with
       an `error` key (the same no-signal sentinel the GPT-primary path emits),
       instead of returning a bare overall_score 0.0.
  1.2  every rollup excludes an errored run from averages — run_batch's
       pass_summary, the backfill/rebuild/merge mirrors, aggregate_runs, and the
       delivery bundle's pass_summary — while a real all-fail 0.0 still counts.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import grading  # noqa: E402
from eval.run_batch import (  # noqa: E402
    _augment_score_with_combined_rewards,
    _pass_summary_doc,
    _pass_summary_entry,
    _run_exclusion_reason,
)

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet-x"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm-x"


def _load_script(basename: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, _REPO_ROOT / "script" / basename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _fail_closed(monkeypatch):
    # The exclusion is fail-closed by default; make sure an operator env that
    # folds invalid runs back in does not leak into these tests.
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)


# --------------------------------------------------------------------------- #
# 1.1  council no-signal sentinel
# --------------------------------------------------------------------------- #
def _member_result(model, family, ok, verdicts=None, error=""):
    r = {"model": model, "effective_model": model, "family": family, "ok": ok,
         "usage": dict(grading._ZERO_USAGE), "user_chars": 10}
    if ok:
        r["verdicts"] = verdicts or []
    else:
        r["error"] = error
    return r


def _members():
    return [grading.CouncilMember(family="sonnet", model=_SONNET),
            grading.CouncilMember(family="glm", model=_GLM)]


def test_council_all_members_failed_is_marked_no_signal(monkeypatch):
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: [
        _member_result(_SONNET, "sonnet", False, error="call: refused upstream"),
        _member_result(_GLM, "glm", False, error="call: timeout"),
    ])
    out = grading._grade_council([{"criterion": "c", "weight": 5}], "sys", "user", _members())
    assert out["overall_score"] == 0.0
    assert out["criteria_abstained"] == 1
    assert "judge council cast no verdicts" in out["error"]
    assert "refused upstream" in out["error"]  # member reasons survive for the retry logic
    assert not grading._grade_is_signal(out)


def test_council_survivors_with_empty_verdict_lists_is_no_signal(monkeypatch):
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: [
        _member_result(_SONNET, "sonnet", True, verdicts=[]),
        _member_result(_GLM, "glm", True, verdicts=[]),
    ])
    out = grading._grade_council([{"criterion": "c", "weight": 5}], "sys", "user", _members())
    assert "cast no verdicts" in out["error"]


def test_council_with_any_verdict_is_a_real_grade(monkeypatch):
    # Sonnet voted No, GLM failed: a genuine (tiebroken) 0.0, NOT no-signal.
    no = {"satisfied": False, "rationale": "r", "truncation_affected": False}
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: [
        _member_result(_SONNET, "sonnet", True, verdicts=[no]),
        _member_result(_GLM, "glm", False, error="call: boom"),
    ])
    out = grading._grade_council([{"criterion": "c", "weight": 5}], "sys", "user", _members())
    assert "error" not in out
    assert out["criteria_failed"] == 1


def test_grade_with_rubric_surfaces_council_no_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "council_members", _members)
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda members: None)
    monkeypatch.setattr(grading, "_run_council", lambda *a, **k: [
        _member_result(_SONNET, "sonnet", False, error="call: boom"),
        _member_result(_GLM, "glm", False, error="call: boom"),
    ])
    ws = tmp_path / "task_output" / "results"
    ws.mkdir(parents=True)
    out = grading.grade_with_rubric([{"criterion": "c", "weight": 5}], "task", ws, "t")
    assert "cast no verdicts" in out["error"]


# --------------------------------------------------------------------------- #
# 1.2  run_batch: reward fields and pass_summary exclusion
# --------------------------------------------------------------------------- #
_ERR = {"overall_score": 0.0, "error": "judge council cast no verdicts (0/3 ...)",
        "criteria_total": 4, "criteria_passed": 0, "criteria_failed": 0,
        "criteria_abstained": 4, "rubric_weights_percentage": 0.0}
_REAL_ZERO = {"overall_score": 0.0, "criteria_total": 4, "criteria_passed": 0,
              "criteria_failed": 4, "criteria_abstained": 0,
              "rubric_weights_percentage": 0.0}
_GOOD = {"overall_score": 0.8, "criteria_total": 4, "criteria_passed": 3,
         "criteria_failed": 1, "criteria_abstained": 0,
         "rubric_weights_percentage": 80.0}


def test_errored_judge_has_no_rubric_reward():
    s = dict(_ERR)
    _augment_score_with_combined_rewards(s, {})
    assert s["rubric_based_reward"] is None
    assert s["combined_reward"] is None


def test_errored_judge_with_tests_keeps_only_the_test_channel():
    s = dict(_ERR)
    _augment_score_with_combined_rewards(
        s, {"test_result": {"reward": 0.5, "tests_total": 4}})
    assert s["rubric_based_reward"] is None
    assert s["combined_reward"] == 0.5


def test_real_all_fail_zero_still_counts():
    s = dict(_REAL_ZERO)
    _augment_score_with_combined_rewards(s, {})
    assert s["rubric_based_reward"] == 0.0
    assert s["combined_reward"] == 0.0


def _scored(base):
    s = dict(base)
    _augment_score_with_combined_rewards(s, {})
    return s


def test_pass_summary_excludes_no_signal_but_keeps_real_zero():
    per_run = [
        _pass_summary_entry(1, _scored(_GOOD), None),
        _pass_summary_entry(2, _scored(_ERR), None),
        _pass_summary_entry(3, _scored(_REAL_ZERO), None),
    ]
    assert per_run[1]["no_signal"].startswith("judge council cast no verdicts")
    assert per_run[1]["rubric_reward"] is None
    assert per_run[1]["rubric_weights_percentage"] is None
    doc = _pass_summary_doc("claude", per_run)
    assert doc["runs"] == 3
    assert doc["runs_used"] == 2
    assert doc["runs_excluded_no_signal"] == 1
    assert doc["average_reward"] == pytest.approx(0.4)  # (0.8 + 0.0) / 2
    assert doc["average_rubric_weights_percentage"] == pytest.approx(40.0)


def test_last_resort_stub_is_excluded():
    stub = {"overall_score": None, "error": "RuntimeError: boom",
            "__last_resort_stub__": True}
    entry = _pass_summary_entry(1, _scored(stub), None)
    assert _run_exclusion_reason(entry) == "no_signal"


def test_opt_out_env_folds_no_signal_back_in(monkeypatch):
    monkeypatch.setenv("WCB_INCLUDE_INVALID_RUNS", "1")
    assert _run_exclusion_reason({"no_signal": "x"}) is None


# --------------------------------------------------------------------------- #
# 1.2  script mirrors agree with run_batch
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def backfill():
    return _load_script("backfill_pass_summary.py", "_t_ns_backfill")


@pytest.fixture(scope="module")
def rebuild():
    return _load_script("rebuild_pass_summary.py", "_t_ns_rebuild")


@pytest.fixture(scope="module")
def merge():
    return _load_script("merge_pass_summaries.py", "_t_ns_merge")


@pytest.fixture(scope="module")
def agg():
    return _load_script("aggregate_runs.py", "_t_ns_agg")


def test_backfill_mirror_excludes_no_signal(backfill):
    per_run = [backfill._entry(1, _GOOD, {}), backfill._entry(2, _ERR, {})]
    assert per_run[1]["no_signal"]
    doc = backfill._doc("claude", per_run)
    assert doc["runs_used"] == 1 and doc["runs_excluded_no_signal"] == 1
    assert doc["average_reward"] == pytest.approx(0.8)


def test_rebuild_mirror_excludes_no_signal(rebuild):
    per_run = [rebuild._pass_summary_entry(1, _GOOD, {}),
               rebuild._pass_summary_entry(2, _ERR, {})]
    doc = rebuild._pass_summary_doc("claude", per_run)
    assert doc["runs_used"] == 1 and doc["runs_excluded_no_signal"] == 1
    assert doc["average_reward"] == pytest.approx(0.8)


def test_merge_mirror_excludes_no_signal(merge):
    assert merge._run_exclusion_reason({"no_signal": "x"}) == "no_signal"
    assert merge._run_exclusion_reason({"reward": 0.0}) is None


def test_aggregate_runs_excludes_errored_score_json(agg):
    assert agg._run_exclusion_reason(dict(_ERR)) == "no_signal"
    assert agg._run_exclusion_reason(dict(_REAL_ZERO)) is None


# --------------------------------------------------------------------------- #
# 1.2  delivery bundle (repackage_to_bundle)
# --------------------------------------------------------------------------- #
def test_bundle_report_carries_no_signal_marker(tmp_path):
    rp = _load_script("repackage_to_bundle.py", "_t_ns_repackage")
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    (run_dir / "score.json").write_text(json.dumps(_scored(_ERR)), encoding="utf-8")
    report = rp.build_report(run_dir, tmp_path, "Claude", 1, False)
    assert report["no_signal"].startswith("judge council cast no verdicts")
    good_dir = tmp_path / "run_2"
    good_dir.mkdir()
    (good_dir / "score.json").write_text(json.dumps(_scored(_GOOD)), encoding="utf-8")
    assert "no_signal" not in rp.build_report(good_dir, tmp_path, "Claude", 2, False)
