"""The consolidated pass_summary pipeline: one implementation, crash-safe, honest.

Three interlocking defects are locked down here.

V5 — THREE divergent implementations. eval/run_batch.py (live batch),
script/backfill_pass_summary.py (production repair; wired into script/run.sh,
deliver.sh and script/regrade.py) and script/rebuild_pass_summary.py (operator
CLI) each carried a hand-synced copy of entry/doc/locked-write, and they had
drifted on the scalar-reward precedence. Because run.sh re-runs backfill over
the WHOLE backend output tree after every batch, that drift silently restated
historical summaries under whichever copy ran last. All four readers now share
src/utils/pass_summary.py.

V4 — crash-safety and the markerless zero. The write was a truncate-then-stream
Path.write_text (a kill mid-write destroys the file), a corrupt existing file was
swallowed into `{}` (destroying every prior rep's record), and
`_mean_or_none(...) or 0.0` plus an `all_runs_excluded` marker stamped only
inside `if excluded:` let an empty per_run publish `average_reward: 0.0` with no
marker at all.

D2/D3 — the ungraded predicate. run_batch checked
`grading_status == "failed" or grading_failure_reason(s)`; the three script
readers checked only the first half, so a HISTORICAL dead-judge score.json
(written before grading.write_score's failure gate existed: `error` /
all-criteria-abstained, no `grading_status` key) was folded in as a genuine 0.0
— the measured 0.1642-becomes-0.0821 deflation.

No docker / network / AWS; everything runs against pytest tmp_path.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import pass_summary as ps  # noqa: E402


def _load_script(basename: str, alias: str):
    path = _REPO_ROOT / "script" / basename
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rb():
    return importlib.import_module("eval.run_batch")


@pytest.fixture(scope="module")
def backfill():
    return _load_script("backfill_pass_summary.py", "_t_cons_backfill")


@pytest.fixture(scope="module")
def rebuild():
    return _load_script("rebuild_pass_summary.py", "_t_cons_rebuild")


@pytest.fixture(scope="module")
def agg():
    return _load_script("aggregate_runs.py", "_t_cons_aggregate")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _healthy(overall: float, total: int = 22) -> dict:
    return {
        "overall_score": overall,
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": total, "criteria_passed": total,
        "criteria_failed": 0, "criteria_abstained": 0,
    }


def _legacy_dead_judge(total: int = 22) -> dict:
    """A pre-failure-gate score.json: the dead-judge shape, no grading_status."""
    return {
        "overall_score": 0.0, "rubric_weights_percentage": 0.0,
        "criteria_total": total, "criteria_passed": 0, "criteria_failed": 0,
        "criteria_abstained": total,
        "error": "all rubric batches failed to grade",
    }


def _sentinel_dead_judge(total: int = 22) -> dict:
    """A modern score.failed.json: carries the explicit marker."""
    return {**_legacy_dead_judge(total), "grading_status": "failed"}


# =========================================================================== #
# FIX 1 — one implementation
# =========================================================================== #
class TestSingleImplementation:
    def test_all_four_readers_share_the_same_objects(self, rb, backfill, rebuild, agg):
        assert rb._pass_summary_entry is ps.pass_summary_entry
        assert rb._pass_summary_doc is ps.pass_summary_doc
        assert rb._run_exclusion_reason is ps.run_exclusion_reason
        assert rb._locked is ps.locked

        assert backfill._entry is ps.pass_summary_entry
        assert backfill._doc is ps.pass_summary_doc
        assert backfill._run_exclusion_reason is ps.run_exclusion_reason
        assert backfill.rebuild_model_dir is ps.rebuild_model_dir
        assert backfill.write_pass_summary is ps.write_pass_summary

        assert rebuild._pass_summary_entry is ps.pass_summary_entry
        assert rebuild._pass_summary_doc is ps.pass_summary_doc
        assert rebuild._read_ctrf_summary is ps.ctrf_test_result

        assert agg._run_exclusion_reason is ps.run_exclusion_reason

    def test_regrade_public_api_is_unchanged(self):
        # regrade.py:31 imports these two names FROM backfill; the consolidation
        # must not have moved them out from under it.
        import script.backfill_pass_summary as bf
        from script import regrade
        assert bf._ctrf_test_result is ps.ctrf_test_result
        assert bf.write_pass_summary is ps.write_pass_summary
        assert regrade.write_pass_summary is ps.write_pass_summary
        assert regrade._ctrf_test_result is ps.ctrf_test_result

    def test_backfill_and_rebuild_emit_identical_bytes(self, backfill, rebuild, tmp_path):
        # The bulk-rewrite hazard: run.sh runs backfill across the entire tree,
        # so backfill and rebuild disagreeing on ANY input means historical
        # summaries flip depending on which tool last touched them.
        model_dir = tmp_path / "output" / "openclaw" / "t" / "trajectories" / "opus"
        for n, overall, reward_txt, ctrf_score in (
            (1, 0.40, "0.750000", 0.1),      # both scalar sources present + disagreeing
            (2, 0.80, None, 0.9),            # ctrf only
            (3, 0.20, "0.333333", None),     # reward.txt only
        ):
            run_dir = model_dir / f"run_{n}"
            verifier = run_dir / "task_output" / "logs" / "verifier"
            _write_json(run_dir / "score.json", _healthy(overall))
            summary = {"tests": 4, "passed": 3, "failed": 1}
            if ctrf_score is not None:
                summary["overall_score"] = ctrf_score
            _write_json(verifier / "ctrf.json", {"results": {"summary": summary}})
            if reward_txt is not None:
                (verifier / "reward.txt").write_text(reward_txt, encoding="utf-8")
        _write_json(model_dir / "run_4" / "score.failed.json", _sentinel_dead_judge())

        from_backfill = backfill.rebuild_model_dir(model_dir)
        from_rebuild = rebuild.rebuild(model_dir)
        assert json.dumps(from_backfill, indent=2) == json.dumps(from_rebuild, indent=2)
        # and the consolidated precedence really is overall_score-first
        assert from_backfill["per_run"][0]["test_reward"] == 0.1
        assert from_backfill["per_run"][2]["test_reward"] == pytest.approx(0.333333)

    def test_batch_writer_and_script_writer_agree_on_the_same_dir(
            self, rb, tmp_path):
        model_dir = tmp_path / "trajectories" / "claude"
        (model_dir / "run_1").mkdir(parents=True)
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.1642))

        rb._write_pass_summary(model_dir, "claude", 1,
                               scores=_healthy(0.1642), test_result=None)
        from_batch = json.loads((model_dir / "pass_summary.json").read_text())

        (model_dir / "pass_summary.json").unlink()
        from_script = ps.write_pass_summary(model_dir)

        assert from_batch == from_script


# =========================================================================== #
# FIX 2a — atomic write
# =========================================================================== #
class TestAtomicWrite:
    def test_commit_is_a_rename_of_an_already_complete_file(self, tmp_path, monkeypatch):
        target = tmp_path / "pass_summary.json"
        target.write_text('{"runs": 1}', encoding="utf-8")

        seen = {}
        real_replace = os.replace

        def spy(src, dst):
            # At commit time the destination still holds the COMPLETE old file
            # and the source is the COMPLETE new one — never a splice of both.
            seen["dst_before"] = Path(dst).read_text(encoding="utf-8")
            seen["src"] = Path(src).read_text(encoding="utf-8")
            seen["same_dir"] = Path(src).parent == Path(dst).parent
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        ps.atomic_write_text(target, '{"runs": 2}')

        assert seen["dst_before"] == '{"runs": 1}'
        assert seen["src"] == '{"runs": 2}'
        # same filesystem, or os.replace is not atomic
        assert seen["same_dir"] is True
        assert target.read_text(encoding="utf-8") == '{"runs": 2}'

    def test_kill_mid_stream_never_becomes_the_pass_summary(self, tmp_path, monkeypatch):
        # The kill simulation: half the bytes reach the temp file, then the
        # process takes a signal. A truncate-then-stream write_text would have
        # left pass_summary.json itself half-written and unparseable.
        model_dir = tmp_path / "claude"
        model_dir.mkdir()
        target = model_dir / "pass_summary.json"
        intact = ps.pass_summary_text(ps.pass_summary_doc(
            "claude", [ps.pass_summary_entry(1, _healthy(0.8), None)]))
        target.write_text(intact, encoding="utf-8")

        class _DyingHandle:
            def __init__(self, fh):
                self._fh = fh

            def write(self, s):
                self._fh.write(s[: len(s) // 2])
                raise KeyboardInterrupt("SIGINT mid-write")

            def flush(self):
                self._fh.flush()

            def fileno(self):
                return self._fh.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()
                return False

        real_fdopen = os.fdopen
        monkeypatch.setattr(
            os, "fdopen", lambda fd, *a, **kw: _DyingHandle(real_fdopen(fd, *a, **kw)))

        with pytest.raises(KeyboardInterrupt):
            ps.upsert_pass_summary(model_dir, "claude", 2, _healthy(0.2), None)

        monkeypatch.undo()
        assert target.read_text(encoding="utf-8") == intact
        assert json.loads(target.read_text(encoding="utf-8"))["runs"] == 1
        # the half-written bytes were cleaned up and never named pass_summary.json
        assert list(model_dir.glob("pass_summary.json.tmp*")) == []

    def test_crash_at_commit_leaves_the_previous_doc_intact(self, tmp_path, monkeypatch):
        target = tmp_path / "pass_summary.json"
        target.write_text('{"runs": 1}', encoding="utf-8")

        def boom(src, dst):
            raise OSError("killed before commit")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            ps.atomic_write_text(target, '{"runs": 2}')

        monkeypatch.undo()
        assert json.loads(target.read_text(encoding="utf-8")) == {"runs": 1}

    def test_every_writer_goes_through_the_atomic_path(self, tmp_path):
        for rel in ("src/utils/pass_summary.py", "script/backfill_pass_summary.py",
                    "script/rebuild_pass_summary.py"):
            src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            assert 'pass_summary.json").write_text' not in src
            assert 'pass_summary_new.json").write_text' not in src


# =========================================================================== #
# FIX 2b — corrupt read
# =========================================================================== #
class TestCorruptRead:
    def test_history_is_preserved_aside_and_the_write_fails_loudly(self, tmp_path):
        model_dir = tmp_path / "claude"
        model_dir.mkdir()
        target = model_dir / "pass_summary.json"
        truncated = '{"model": "claude", "runs": 8, "per_run": [{"run_index": 1'
        target.write_text(truncated, encoding="utf-8")

        with pytest.raises(ps.PassSummaryCorruptError) as exc:
            ps.upsert_pass_summary(model_dir, "claude", 9, _healthy(0.5), None)

        aside = sorted(model_dir.glob("pass_summary.json.corrupt.*"))
        assert len(aside) == 1
        assert aside[0].read_text(encoding="utf-8") == truncated
        assert exc.value.quarantined == aside[0]
        assert exc.value.path == target
        # Crucially: no replacement doc was fabricated in its place.
        assert not target.exists()

    def test_non_object_json_is_also_quarantined(self, tmp_path):
        model_dir = tmp_path / "claude"
        model_dir.mkdir()
        (model_dir / "pass_summary.json").write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ps.PassSummaryCorruptError):
            ps.upsert_pass_summary(model_dir, "claude", 1, _healthy(0.5), None)
        assert len(list(model_dir.glob("pass_summary.json.corrupt.*"))) == 1

    def test_repeated_corruption_does_not_overwrite_the_first_quarantine(self, tmp_path):
        model_dir = tmp_path / "claude"
        model_dir.mkdir()
        for payload in ("{first-corrupt", "{second-corrupt"):
            (model_dir / "pass_summary.json").write_text(payload, encoding="utf-8")
            with pytest.raises(ps.PassSummaryCorruptError):
                ps.upsert_pass_summary(model_dir, "claude", 1, _healthy(0.5), None)
        kept = sorted(p.read_text(encoding="utf-8")
                      for p in model_dir.glob("pass_summary.json.corrupt.*"))
        assert kept == ["{first-corrupt", "{second-corrupt"]

    def test_missing_file_is_not_corruption(self, tmp_path):
        assert ps.load_existing_doc(tmp_path / "nope.json") == {}

    def test_rebuild_path_repairs_corruption_instead_of_failing(self, tmp_path):
        # write_pass_summary recomputes from run_N and never reads the old doc,
        # so a corrupt file there is simply overwritten with a correct rebuild.
        model_dir = tmp_path / "trajectories" / "claude"
        (model_dir / "run_1").mkdir(parents=True)
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.5))
        (model_dir / "pass_summary.json").write_text("{corrupt", encoding="utf-8")

        doc = ps.write_pass_summary(model_dir)
        assert doc["runs"] == 1
        assert json.loads((model_dir / "pass_summary.json").read_text())["runs"] == 1


# =========================================================================== #
# FIX 2c — the markerless zero
# =========================================================================== #
class TestMarkerlessZero:
    def test_empty_per_run_has_a_marker_and_no_bare_zero(self):
        doc = ps.pass_summary_doc("claude", [])
        assert doc["runs"] == 0
        assert doc["average_reward"] is None
        assert doc["all_runs_excluded"] is True
        assert doc["runs_used"] == 0

    @pytest.mark.parametrize("marker", [
        {"grading_status": "failed"},
        {"run_incomplete": True},
        {"injection_ok": False},
        {"eval_skipped": "trajectory empty: no assistant messages"},
    ])
    def test_every_all_excluded_shape_is_marked(self, marker):
        entry = {"run_index": 1, "reward": 0.0, "combined_reward": 0.0,
                 "rubric_reward": 0.0, "test_reward": None,
                 "rubric_weights_percentage": 0.0, **marker}
        doc = ps.pass_summary_doc("claude", [entry])
        assert doc["average_reward"] is None
        assert doc["all_runs_excluded"] is True
        assert doc["runs_used"] == 0
        assert len(doc["per_run"]) == 1

    def test_a_genuine_zero_still_reads_as_zero(self):
        # The fix must not make a real 0.0 measurement indistinguishable from
        # "no measurement" — that would be the same defect with the sign flipped.
        entry = ps.pass_summary_entry(1, _healthy(0.0), None)
        doc = ps.pass_summary_doc("claude", [entry])
        assert doc["average_reward"] == 0.0
        assert "all_runs_excluded" not in doc

    def test_downstream_readers_tolerate_the_null_average(self, tmp_path):
        # average_reward can now be null where it used to be a fabricated 0.0.
        # Every in-repo consumer must survive that: merge_pass_summaries
        # recomputes from per_run rather than reading the field, and regrade
        # only prints it. A consumer doing arithmetic on it would crash here.
        merge = _load_script("merge_pass_summaries.py", "_t_cons_merge")
        docs = []
        for batch in ("first", "second"):
            model_dir = tmp_path / batch / "trajectories" / "claude"
            (model_dir / "run_1").mkdir(parents=True)
            _write_json(model_dir / "run_1" / "score.failed.json",
                        _sentinel_dead_judge())
            doc = ps.write_pass_summary(model_dir)
            assert doc["average_reward"] is None
            p = tmp_path / f"{batch}.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            docs.append(p)

        merged = merge.merge_pass_summaries(docs, extended=True)
        assert merged["runs"] == 2
        assert len(merged["per_run"]) == 2

    def test_marker_reaches_disk_through_both_writers(self, tmp_path):
        model_dir = tmp_path / "trajectories" / "claude"
        (model_dir / "run_1").mkdir(parents=True)
        _write_json(model_dir / "run_1" / "score.failed.json", _sentinel_dead_judge())

        ps.write_pass_summary(model_dir)
        doc = json.loads((model_dir / "pass_summary.json").read_text())
        assert doc["average_reward"] is None
        assert doc["all_runs_excluded"] is True
        assert doc["runs_ungraded"] == 1


# =========================================================================== #
# FIX 3 (D2) — one ungraded predicate at all four sites
# =========================================================================== #
_PREDICATE_TABLE = [
    ("modern sentinel", _sentinel_dead_judge(), "ungraded"),
    ("legacy dead judge (error, no grading_status)", _legacy_dead_judge(), "ungraded"),
    ("all criteria abstained, no error",
     {"overall_score": 0.0, "criteria_total": 4, "criteria_abstained": 4}, "ungraded"),
    ("healthy", _healthy(0.5), None),
    ("healthy zero", _healthy(0.0), None),
    ("partial abstain is still a grade",
     {"overall_score": 0.5, "criteria_total": 4, "criteria_abstained": 2}, None),
    ("incomplete", {"overall_score": 0.5, "run_incomplete": True}, "incomplete"),
    ("injection failed", {"overall_score": 0.5, "injection_ok": False}, "injection_failed"),
    ("unmeasured", {"overall_score": None, "eval_skipped": "empty"}, "unmeasured"),
    ("legacy run with no markers", {"rubric_weights_percentage": 50.0}, None),
    ("injection_ok True is not an exclusion", {"injection_ok": True}, None),
]


def _reader_table(rb, backfill, rebuild, agg):
    return {
        "eval/run_batch.py": rb._run_exclusion_reason,
        "script/backfill_pass_summary.py": backfill._run_exclusion_reason,
        "script/rebuild_pass_summary.py": rebuild._run_exclusion_reason,
        "script/aggregate_runs.py": agg._run_exclusion_reason,
    }


class TestPredicateParity:
    @pytest.mark.parametrize("label,score,expected", _PREDICATE_TABLE,
                             ids=[row[0] for row in _PREDICATE_TABLE])
    def test_all_four_readers_classify_identically(
            self, rb, backfill, rebuild, agg, label, score, expected):
        for site, fn in _reader_table(rb, backfill, rebuild, agg).items():
            assert fn(dict(score)) == expected, f"{site} disagreed on {label!r}"

    @pytest.mark.parametrize("score", [_sentinel_dead_judge(), _legacy_dead_judge()])
    def test_ungraded_is_opt_out_proof_at_every_site(
            self, rb, backfill, rebuild, agg, monkeypatch, score):
        monkeypatch.setenv("WCB_INCLUDE_INVALID_RUNS", "1")
        monkeypatch.setenv("WCB_INCLUDE_INCOMPLETE_RUNS", "1")
        for site, fn in _reader_table(rb, backfill, rebuild, agg).items():
            assert fn(dict(score)) == "ungraded", f"{site} resurrected an ungraded run"

    def test_every_entry_builder_stamps_the_marker(self, rb, backfill, rebuild):
        for builder in (rb._pass_summary_entry, backfill._entry,
                        rebuild._pass_summary_entry):
            assert builder(1, _legacy_dead_judge(), None)["grading_status"] == "failed"
            assert "grading_status" not in builder(1, _healthy(0.5), None)

    def test_the_measured_deflation_is_gone(self, backfill, rebuild, agg, tmp_path):
        # The incident: run_1 graded 0.1642, run_2's judge died and left a
        # legacy-shaped score.json. Averaging the fabricated 0.0 halved the
        # result to 0.0821.
        root = tmp_path / "output"
        model_dir = root / "openclaw" / "koji" / "trajectories" / "claude"
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.1642))
        _write_json(model_dir / "run_2" / "score.json", _legacy_dead_judge())

        for doc in (backfill.rebuild_model_dir(model_dir), rebuild.rebuild(model_dir)):
            assert doc["average_rubric_reward"] == pytest.approx(0.1642)
            assert doc["average_rubric_reward"] != pytest.approx(0.0821)
            assert doc["runs_ungraded"] == 1
            assert doc["runs_used"] == 1
            assert doc["runs"] == 2

        summary = agg.aggregate(root, "openclaw")
        row = summary["by_task_model"][0]
        assert row["runs_ungraded"] == 1
        assert row["run_count"] == 1
        assert row["average_rubric_weights_percentage"] == pytest.approx(16.42)

    def test_opt_out_cannot_resurrect_the_deflation(self, backfill, agg, monkeypatch,
                                                    tmp_path):
        monkeypatch.setenv("WCB_INCLUDE_INVALID_RUNS", "1")
        root = tmp_path / "output"
        model_dir = root / "openclaw" / "koji" / "trajectories" / "claude"
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.1642))
        _write_json(model_dir / "run_2" / "score.json", _legacy_dead_judge())

        doc = backfill.rebuild_model_dir(model_dir)
        assert doc["average_rubric_reward"] == pytest.approx(0.1642)
        assert doc["runs_ungraded"] == 1

        row = agg.aggregate(root, "openclaw")["by_task_model"][0]
        assert row["average_rubric_weights_percentage"] == pytest.approx(16.42)


# =========================================================================== #
# FIX 3 (D3) — sibling stability
# =========================================================================== #
class TestSiblingStability:
    def _tree(self, tmp_path: Path) -> Path:
        model_dir = tmp_path / "output" / "openclaw" / "koji" / "trajectories" / "claude"
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.30))
        _write_json(model_dir / "run_2" / "score.json", _legacy_dead_judge())
        _write_json(model_dir / "run_3" / "score.json",
                    {"overall_score": 0.5, "injection_ok": False})
        return model_dir

    @staticmethod
    def _classification(doc: dict) -> dict:
        return {r["run_index"]: (r.get("grading_status"), r.get("injection_ok"),
                                 ps.run_exclusion_reason(r))
                for r in doc["per_run"]}

    def test_regrading_run_1_leaves_the_siblings_classified_identically(
            self, rb, tmp_path):
        model_dir = self._tree(tmp_path)

        before = self._classification(ps.write_pass_summary(model_dir))
        assert before[2] == ("failed", None, "ungraded")
        assert before[3] == (None, False, "injection_failed")

        # run_1 is re-judged to a different score; nothing else on disk moves.
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.90))

        after_script = self._classification(ps.write_pass_summary(model_dir))
        rb._write_pass_summary(model_dir, "claude", 1,
                               scores=_healthy(0.90), test_result=None)
        after_batch = self._classification(
            json.loads((model_dir / "pass_summary.json").read_text()))

        for sibling in (2, 3):
            assert after_script[sibling] == before[sibling]
            assert after_batch[sibling] == before[sibling]

    def test_the_two_writers_produce_the_same_doc_after_the_regrade(
            self, rb, tmp_path):
        model_dir = self._tree(tmp_path)
        ps.write_pass_summary(model_dir)
        _write_json(model_dir / "run_1" / "score.json", _healthy(0.90))

        rb._write_pass_summary(model_dir, "claude", 1,
                               scores=_healthy(0.90), test_result=None)
        from_batch = json.loads((model_dir / "pass_summary.json").read_text())
        from_script = ps.write_pass_summary(model_dir)

        assert from_batch == from_script
        assert from_script["runs_ungraded"] == 1
        assert from_script["runs_excluded_injection_failed"] == 1
        assert from_script["runs_used"] == 1
        assert from_script["average_rubric_reward"] == pytest.approx(0.90)
