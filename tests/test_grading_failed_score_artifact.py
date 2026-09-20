"""Total judge failure is FATAL-by-artifact, never a silent 0.00%.

Incident (alpha, 2026-09-19): `_merge_batched_grades` stamps
`error: "all rubric batches failed to grade"` when every rubric chunk fails and
degrades each criterion to a synthetic abstain, which lands
`rubric_weights_percentage: 0.0` in the payload. That payload went out through
the normal `score.json` writer and NOTHING downstream read `error`, so three
real runs shipped 0.00% written by a dead judge — byte-indistinguishable from a
genuine zero reward.

This file pins the fix end to end:

  * `grading._write_score`   — the gate: error-scores and all-abstained scores
                               write `score.failed.json` (+ `grading_status`)
                               and NEVER `score.json`; healthy scores are
                               byte-unchanged and carry NO `grading_status`.
  * mutual exclusion         — writing either artifact retires the other, so a
                               judge-only regrade flips the verdict cleanly in
                               both directions.
  * reader behaviour         — `aggregate_runs` and the pass-summary builders
                               surface an ungraded run VISIBLY (`runs_ungraded`)
                               and never average it in as a 0.0, and the stdout
                               table does not crash on a row with no averages.
  * `run_batch` last-resort  — the invariant guard must not RESURRECT a normal
                               `score.json` over a deliberate failure sentinel.

All tests are offline/deterministic: no docker, no network, no judge call. Temp
data goes to pytest tmp_path only. Fixtures are built locally from the schema
`_merge_batched_grades` / `_grade_council` emit.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

# sys.path bootstrap: repo root before "from src..." imports (matches
# tests/test_grading_units_deep.py convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures — the real shapes _merge_batched_grades / _grade_council produce
# ---------------------------------------------------------------------------


def _healthy_scores(total: int = 3, passed: int = 2) -> dict:
    # A normal council verdict: some criteria satisfied, none abstained.
    return {
        "overall_score": 0.6667,
        "rubric_weights_percentage": 66.67,
        "criteria_total": total,
        "criteria_passed": passed,
        "criteria_failed": total - passed,
        "criteria_abstained": 0,
        "criteria": [
            {"id": i, "weight": 1.0, "satisfied": i < passed, "passed": i < passed}
            for i in range(total)
        ],
        "judge_model": "council",
        "abstention_flags": [],
        "truncation_flags": [],
    }


def _all_batches_failed_scores(total: int = 3) -> dict:
    # Exactly what _merge_batched_grades returns when EVERY chunk failed:
    # synthetic abstains for all criteria, 0.0 numerator, top-level error.
    doc = {
        "overall_score": 0.0,
        "rubric_weights_percentage": 0.0,
        "criteria_total": total,
        "criteria_passed": 0,
        "criteria_failed": 0,
        "criteria_abstained": total,
        "criteria": [
            {"id": i, "weight": 1.0, "satisfied": None, "passed": None,
             "resolved_by": "human_eval"}
            for i in range(total)
        ],
        "judge_model": "council",
        "abstention_flags": list(range(total)),
        "truncation_flags": [],
        "error": "all rubric batches failed to grade",
    }
    return doc


def _all_abstained_no_error_scores(total: int = 4) -> dict:
    # The quieter twin: every criterion abstained but no top-level `error`
    # survived the merge (chunks "succeeded" while suppressing every verdict).
    doc = _all_batches_failed_scores(total)
    doc.pop("error")
    return doc


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# the predicate — EXACTLY "any top-level error OR all-abstained (total > 0)"
# ---------------------------------------------------------------------------


def test_failure_reason_fires_on_any_top_level_error():
    assert grading._grading_failure_reason({"error": "boom"})
    assert grading._grading_failure_reason(
        {"overall_score": 0.0, "error": "no rubric criteria"})
    assert grading._grading_failure_reason(_all_batches_failed_scores())


def test_failure_reason_fires_on_all_abstained_without_error():
    reason = grading._grading_failure_reason(_all_abstained_no_error_scores(4))
    assert reason is not None
    assert "4" in reason and "abstain" in reason


def test_failure_reason_silent_on_healthy_and_partial_abstention():
    assert grading._grading_failure_reason(_healthy_scores()) is None
    # A partially-abstained run still carries real verdicts -> it IS a score.
    partial = _healthy_scores(total=5, passed=2)
    partial["criteria_abstained"] = 4
    assert grading._grading_failure_reason(partial) is None


def test_failure_reason_never_fires_on_zero_criteria_total():
    # The no-rubrics path is separate and must NOT be misread as a dead judge.
    # (In production it sets `error` anyway, which the error branch catches.)
    assert grading._grading_failure_reason(
        {"criteria_total": 0, "criteria_abstained": 0}) is None
    assert grading._grading_failure_reason(
        {"criteria_total": 0, "criteria_abstained": 0, "overall_score": 0.0}) is None


def test_failure_reason_tolerates_non_dict_and_junk_counts():
    # _write_score is reached from run_grading with whatever JSON the in-container
    # grader printed; the gate must never be the thing that raises.
    assert grading._grading_failure_reason([1, 2, 3]) is None
    assert grading._grading_failure_reason(None) is None
    assert grading._grading_failure_reason(
        {"criteria_total": "lots", "criteria_abstained": "lots"}) is None


def test_genuine_zero_still_writes_a_real_score():
    # THE distinction the incident erased: an honest 0% (judge ran, nothing
    # satisfied, nothing abstained) is signal and must stay a normal score.
    genuine = _healthy_scores(total=3, passed=0)
    genuine["overall_score"] = 0.0
    genuine["rubric_weights_percentage"] = 0.0
    assert grading._grading_failure_reason(genuine) is None


# ---------------------------------------------------------------------------
# _write_score — the gate
# ---------------------------------------------------------------------------


def test_error_scores_write_failed_artifact_and_no_score_json(tmp_path):
    scores = _all_batches_failed_scores()
    grading._write_score(tmp_path, "t1", scores)

    assert not (tmp_path / "score.json").exists()
    doc = _read(tmp_path / "score.failed.json")
    assert doc["grading_status"] == "failed"
    assert doc["error"] == "all rubric batches failed to grade"
    # The payload is preserved verbatim alongside the marker — the sentinel is
    # still the forensic record of what the dead judge managed to emit.
    assert doc["criteria_total"] == 3
    assert doc["rubric_weights_percentage"] == 0.0


def test_all_abstained_without_error_writes_failed_artifact(tmp_path):
    grading._write_score(tmp_path, "t1", _all_abstained_no_error_scores(4))

    assert not (tmp_path / "score.json").exists()
    doc = _read(tmp_path / "score.failed.json")
    assert doc["grading_status"] == "failed"
    assert "error" not in doc
    assert doc["criteria_abstained"] == doc["criteria_total"] == 4


def test_error_score_helper_routes_to_failed_artifact(tmp_path):
    # _error_score / write_error_score funnel through the same choke point.
    grading.write_error_score(tmp_path, "t1", "judge transport exploded")

    assert not (tmp_path / "score.json").exists()
    assert _read(tmp_path / "score.failed.json")["grading_status"] == "failed"


def test_healthy_scores_write_score_json_only_with_no_status_key(tmp_path):
    scores = _healthy_scores()
    grading._write_score(tmp_path, "t1", scores)

    assert not (tmp_path / "score.failed.json").exists()
    doc = _read(tmp_path / "score.json")
    assert "grading_status" not in doc
    assert doc == scores


def test_healthy_score_json_is_byte_identical_to_the_legacy_writer(tmp_path):
    # Byte-compatibility is a hard requirement: aggregation, bundles and the
    # delivery diff all read this file, and the gate must be invisible to them.
    scores = _healthy_scores()
    grading._write_score(tmp_path, "t1", scores)
    legacy = json.dumps(scores, indent=2, ensure_ascii=False)
    assert (tmp_path / "score.json").read_text(encoding="utf-8") == legacy


def test_write_score_creates_missing_output_dir(tmp_path):
    nested = tmp_path / "trajectories" / "model" / "run_1"
    grading._write_score(nested, "t1", _all_batches_failed_scores())
    assert (nested / "score.failed.json").is_file()


def test_failed_write_logs_at_error_level_with_the_reason(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger=grading.logger.name):
        grading._write_score(tmp_path, "task-xyz", _all_batches_failed_scores())
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "a totally failed grade must be logged at ERROR"
    msg = errors[-1].getMessage()
    assert "task-xyz" in msg
    assert "all rubric batches failed to grade" in msg
    assert "score.failed.json" in msg


def test_healthy_write_does_not_log_an_error(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger=grading.logger.name):
        grading._write_score(tmp_path, "t1", _healthy_scores())
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_failed_artifact_survives_unserializable_payload(tmp_path):
    # The judge-exception stub in run_batch used `default=str`; the failure
    # branch keeps that tolerance so the sentinel is never lost to a TypeError.
    scores = {"error": "boom", "exc": ValueError("nope"), "path": Path("/tmp/x")}
    grading._write_score(tmp_path, "t1", scores)
    doc = _read(tmp_path / "score.failed.json")
    assert doc["grading_status"] == "failed"
    assert "nope" in doc["exc"]


# ---------------------------------------------------------------------------
# mutual exclusion — regrade flips the verdict cleanly in BOTH directions
# ---------------------------------------------------------------------------


def test_regrade_over_failed_removes_the_failed_artifact(tmp_path):
    grading._write_score(tmp_path, "t1", _all_batches_failed_scores())
    assert (tmp_path / "score.failed.json").is_file()

    # Judge-only regrade succeeds the second time.
    grading._write_score(tmp_path, "t1", _healthy_scores())

    assert (tmp_path / "score.json").is_file()
    assert not (tmp_path / "score.failed.json").exists()
    assert "grading_status" not in _read(tmp_path / "score.json")


def test_regrade_over_healthy_removes_the_stale_score_json(tmp_path):
    grading._write_score(tmp_path, "t1", _healthy_scores())
    assert (tmp_path / "score.json").is_file()

    # The other direction: a regrade whose judge dies must not leave a file
    # still claiming this run is scored.
    grading._write_score(tmp_path, "t1", _all_batches_failed_scores())

    assert (tmp_path / "score.failed.json").is_file()
    assert not (tmp_path / "score.json").exists()


def test_the_two_artifacts_are_never_both_present(tmp_path):
    for scores in (_healthy_scores(), _all_batches_failed_scores(),
                   _all_abstained_no_error_scores(), _healthy_scores(total=1, passed=1)):
        grading._write_score(tmp_path, "t1", scores)
        present = [p.name for p in tmp_path.iterdir()]
        assert not ("score.json" in present and "score.failed.json" in present), present


def test_mutual_exclusion_is_a_noop_when_the_other_file_is_absent(tmp_path):
    # unlink(missing_ok=True) on a clean dir must not raise.
    grading._write_score(tmp_path, "t1", _healthy_scores())
    grading._write_score(tmp_path, "t1", _healthy_scores())
    assert (tmp_path / "score.json").is_file()


# ---------------------------------------------------------------------------
# readers — an ungraded run is VISIBLE, is not a 0, and does not crash
# ---------------------------------------------------------------------------


def _run_dir(root: Path, task: str, model: str, run: int) -> Path:
    d = root / "openclaw" / task / "trajectories" / model / f"run_{run}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_aggregate_skips_ungraded_run_instead_of_scoring_it_zero(tmp_path):
    from script import aggregate_runs

    good = _run_dir(tmp_path, "task_a", "opus", 1)
    grading._write_score(good, "task_a", _healthy_scores())
    dead = _run_dir(tmp_path, "task_a", "opus", 2)
    grading._write_score(dead, "task_a", _all_batches_failed_scores())

    summary = aggregate_runs.aggregate(tmp_path)
    row = summary["by_task_model"][0]

    # The ungraded run is NOT averaged in: mean stays the healthy run's value.
    # Were it folded in as 0.0 the mean would be 33.34.
    assert row["run_count"] == 1
    assert row["average_rubric_weights_percentage"] == 66.67
    assert row["pass_at_k"] == 66.67
    # ...and it is VISIBLE, not silently dropped.
    assert row["runs_ungraded"] == 1
    assert summary["by_model"][0]["average_rubric_weights_percentage"] == 66.67


def test_aggregate_keeps_a_fully_ungraded_task_visible(tmp_path):
    from script import aggregate_runs

    for i in (1, 2, 3):
        grading._write_score(_run_dir(tmp_path, "task_dead", "opus", i),
                             "task_dead", _all_batches_failed_scores())

    summary = aggregate_runs.aggregate(tmp_path)
    rows = [r for r in summary["by_task_model"] if r["task_id"] == "task_dead"]
    assert len(rows) == 1
    assert rows[0]["run_count"] == 0
    assert rows[0]["runs_ungraded"] == 3
    # A task with no gradeable run contributes nothing to the model rollup.
    assert summary["by_model"] == []


def test_aggregate_ungraded_reason_is_not_confused_with_incomplete(tmp_path):
    from script import aggregate_runs

    dead = _run_dir(tmp_path, "task_a", "opus", 1)
    grading._write_score(dead, "task_a", _all_batches_failed_scores())
    incomplete = _run_dir(tmp_path, "task_a", "opus", 2)
    (incomplete / "score.json").write_text(json.dumps(
        {**_healthy_scores(), "run_incomplete": True}), encoding="utf-8")

    row = aggregate_runs.aggregate(tmp_path)["by_task_model"][0]
    # Different operator responses: re-judge vs re-run.
    assert row["runs_ungraded"] == 1
    assert row["runs_excluded_incomplete"] == 1


def test_aggregate_ignores_a_run_dir_with_no_score_files_at_all(tmp_path):
    from script import aggregate_runs

    _run_dir(tmp_path, "task_a", "opus", 1)  # empty run dir
    summary = aggregate_runs.aggregate(tmp_path)
    assert summary["by_task_model"] == []


def test_aggregate_table_renders_ungraded_rows_without_crashing(tmp_path, capsys):
    from script import aggregate_runs

    for i in (1, 2):
        grading._write_score(_run_dir(tmp_path, "task_dead", "opus", i),
                             "task_dead", _all_batches_failed_scores())

    aggregate_runs._print_table(aggregate_runs.aggregate(tmp_path))
    out = capsys.readouterr().out
    assert "UNGRADED x2" in out
    # A dash, never a fabricated 0.00, for a row that has no measurement.
    assert "0.00" not in out.split("task_dead")[1].split("\n")[0]


def test_ungraded_run_is_excluded_from_pass_summary_averages(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
    import importlib
    rb = importlib.import_module("eval.run_batch")

    healthy = rb._pass_summary_entry(1, _healthy_scores(), {})
    dead = rb._pass_summary_entry(2, _all_batches_failed_scores(), {})

    assert dead["grading_status"] == "failed"
    assert rb._run_exclusion_reason(dead) == "ungraded"
    assert rb._run_exclusion_reason(healthy) is None

    doc = rb._pass_summary_doc("opus", [healthy, dead])
    # Averaged over the healthy run only — not deflated by the dead judge.
    assert doc["average_rubric_reward"] == pytest.approx(0.6667)
    assert doc["runs_ungraded"] == 1
    assert doc["runs_used"] == 1
    # The run itself is still present for forensics.
    assert len(doc["per_run"]) == 2


def test_ungraded_exclusion_has_no_opt_out_env(tmp_path, monkeypatch):
    import importlib
    rb = importlib.import_module("eval.run_batch")

    # The invalid/incomplete opt-outs must not fold a dead judge back in: the
    # only number it could contribute is the fabricated 0.0 placeholder.
    monkeypatch.setenv("WCB_INCLUDE_INVALID_RUNS", "1")
    monkeypatch.setenv("WCB_INCLUDE_INCOMPLETE_RUNS", "1")
    dead = rb._pass_summary_entry(1, _all_batches_failed_scores(), {})
    assert rb._run_exclusion_reason(dead) == "ungraded"


def test_rebuild_pass_summary_reads_the_failure_sentinel(tmp_path):
    from script import rebuild_pass_summary

    model_dir = tmp_path / "trajectories" / "opus"
    grading._write_score(model_dir / "run_1", "t", _healthy_scores())
    grading._write_score(model_dir / "run_2", "t", _all_batches_failed_scores())

    doc = rebuild_pass_summary.rebuild(model_dir, "opus")

    assert doc["runs_ungraded"] == 1
    assert doc["runs_used"] == 1
    assert doc["average_rubric_reward"] == pytest.approx(0.6667)


# ---------------------------------------------------------------------------
# run_batch last-resort guard — must not RESURRECT a normal score.json
# ---------------------------------------------------------------------------


def test_last_resort_guard_source_checks_the_failure_sentinel():
    # AST-free source invariant, mirroring tests/test_score_json_last_resort.py:
    # the guard's existence test must consider score.failed.json, otherwise the
    # finally-block stub silently undoes every failed grade on disk.
    src = (Path(__file__).resolve().parents[1] / "eval" / "run_batch.py").read_text(
        encoding="utf-8")
    marker = "if not score_path.exists() and not (output_dir / FAILED_SCORE_FILENAME).exists():"
    assert marker in src, (
        "the last-resort score.json guard must skip run dirs that already carry "
        "score.failed.json, or a deliberately ungraded run gets a normal "
        "score.json written back over it"
    )


def test_run_batch_judge_path_no_longer_raw_writes_score_json():
    # The rubric-judge success path is THE call site that shipped the three
    # 0.00% alpha runs; it must go through the gated writer, not write_text.
    src = (Path(__file__).resolve().parents[1] / "eval" / "run_batch.py").read_text(
        encoding="utf-8")
    assert '(output_dir / "score.json").write_text(\n                json.dumps(scores' not in src
    assert "write_score_file(output_dir, task[\"task_id\"], scores)" in src
