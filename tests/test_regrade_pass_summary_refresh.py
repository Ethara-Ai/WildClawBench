"""A judge-only regrade must refresh pass_summary.json, not just score.json.

Incident (alpha, koji): `script/regrade.py` called
`rebuild_model_dir(run_dir.parent)` and THREW THE RETURN VALUE AWAY —
`rebuild_model_dir` only computes the doc, `main()` is what persists it. So the
regrade rewrote `score.json` and left `pass_summary.json` byte-for-byte stale:
pass_summary mtime 17:33 still reported run_2 = 0.0 / 0 criteria passed while
the regraded `run_2/score.json` at 18:01 reported 16.42% / 22 passed. Every
consumer reading pass_summary kept serving the superseded verdict.

This module pins the refresh end to end, including its interaction with the
total-judge-failure gate:

  * happy path      — regrade of run N updates that run's pass_summary entry
                      AND the rollup averages.
  * regrade->failed — a regrade whose judge dies flips the run to
                      score.failed.json and pass_summary drops it as `ungraded`
                      instead of averaging a fabricated 0.0.
  * failed->healthy — the reverse flip restores the run to the averages.
  * locking         — the rebuild+write happens under the SAME
                      `.pass_summary.lock` a live batch takes, and the rebuild
                      is inside the lock (it is a read-modify-write).

Collaborators are monkeypatched (grade_with_rubric); nothing touches the
network, docker or AWS, and all temp data goes under pytest tmp_path. Style and
module-loading follow tests/test_regrade_and_rerun_units.py.
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

from script import backfill_pass_summary as bps  # noqa: E402
from src.utils import pass_summary as shared_ps  # noqa: E402


def _load_script(basename: str, alias: str):
    path = _REPO_ROOT / "script" / basename
    assert path.exists(), f"script missing: {path}"
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def regrade_mod():
    return _load_script("regrade.py", "_test_regrade_pass_summary")


def _mk_run_dir(root: Path, *, backend="openclaw", task="koji",
                model="claude", run="run_1") -> Path:
    run_dir = root / backend / task / "trajectories" / model / run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.json").write_text(json.dumps({"messages": []}),
                                         encoding="utf-8")
    return run_dir


def _rubric(tmp_path: Path) -> Path:
    p = tmp_path / "rubric.json"
    p.write_text(json.dumps([{"criterion": "c", "weight": 5}]), encoding="utf-8")
    return p


def _healthy(overall: float, total: int = 22, passed: int = 22) -> dict:
    return {
        "overall_score": overall,
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": total,
        "criteria_passed": passed,
        "criteria_failed": total - passed,
        "criteria_abstained": 0,
        "judge_model": "council",
    }


def _dead_judge(total: int = 22) -> dict:
    # The _merge_batched_grades all-chunks-failed shape.
    return {
        "overall_score": 0.0,
        "rubric_weights_percentage": 0.0,
        "criteria_total": total,
        "criteria_passed": 0,
        "criteria_failed": 0,
        "criteria_abstained": total,
        "judge_model": "council",
        "error": "all rubric batches failed to grade",
    }


def _stub_judge(regrade_mod, monkeypatch, scores: dict):
    monkeypatch.setattr(regrade_mod, "grade_with_rubric",
                        lambda *a, **kw: dict(scores))


def _summary(model_dir: Path) -> dict:
    return json.loads((model_dir / "pass_summary.json").read_text(encoding="utf-8"))


def _entry(doc: dict, run_index: int) -> dict:
    return next(r for r in doc["per_run"] if r["run_index"] == run_index)


# ---------------------------------------------------------------------------
# the regression itself
# ---------------------------------------------------------------------------


def test_regrade_refreshes_pass_summary_instead_of_leaving_it_stale(
        regrade_mod, tmp_path, monkeypatch):
    run2 = _mk_run_dir(tmp_path, run="run_2")
    model_dir = run2.parent

    # The stale artifact the incident left behind: run_2 recorded as a zero.
    stale = {
        "model": "claude", "runs": 1, "average_reward": 0.0,
        "per_run": [{"run_index": 2, "criteria_total": 0, "criteria_passed": 0,
                     "criteria_failed": 0, "rubric_reward": 0.0,
                     "rubric_weights_percentage": 0.0, "tests_total": 0,
                     "tests_passed": 0, "tests_failed": 0, "tests_errored": 0,
                     "tests_skipped": 0, "test_reward": None,
                     "combined_reward": 0.0, "reward": 0.0}],
    }
    (model_dir / "pass_summary.json").write_text(json.dumps(stale), encoding="utf-8")

    _stub_judge(regrade_mod, monkeypatch, _healthy(0.1642))
    regrade_mod.regrade(run2, rubric_override=_rubric(tmp_path))

    doc = _summary(model_dir)
    e = _entry(doc, 2)
    # The exact numbers from the alpha report, now actually reaching the file.
    assert e["rubric_weights_percentage"] == 16.42
    assert e["criteria_passed"] == 22
    assert e["criteria_total"] == 22
    assert e["rubric_reward"] == pytest.approx(0.1642)
    assert doc["average_reward"] == pytest.approx(0.1642)


def test_regrade_pass_summary_reflects_sibling_runs_too(
        regrade_mod, tmp_path, monkeypatch):
    # The rebuild re-reads every run_N, so untouched siblings stay correct and
    # the rollup average spans the whole model dir, not just the regraded rep.
    run1 = _mk_run_dir(tmp_path, run="run_1")
    run2 = _mk_run_dir(tmp_path, run="run_2")
    (run1 / "score.json").write_text(json.dumps(_healthy(0.80)), encoding="utf-8")

    _stub_judge(regrade_mod, monkeypatch, _healthy(0.40))
    regrade_mod.regrade(run2, rubric_override=_rubric(tmp_path))

    doc = _summary(run2.parent)
    assert doc["runs"] == 2
    assert _entry(doc, 1)["rubric_reward"] == pytest.approx(0.80)
    assert _entry(doc, 2)["rubric_reward"] == pytest.approx(0.40)
    assert doc["average_reward"] == pytest.approx(0.60)


def test_regrade_does_not_silently_skip_the_write(regrade_mod, tmp_path, monkeypatch):
    # Guard on the precise defect shape: rebuild_model_dir returns a doc and
    # persists nothing, so a caller that ignores the return leaves no file.
    run1 = _mk_run_dir(tmp_path)
    assert not (run1.parent / "pass_summary.json").exists()

    _stub_judge(regrade_mod, monkeypatch, _healthy(0.5))
    regrade_mod.regrade(run1, rubric_override=_rubric(tmp_path))

    assert (run1.parent / "pass_summary.json").is_file()


# ---------------------------------------------------------------------------
# interaction with the total-judge-failure gate
# ---------------------------------------------------------------------------


def test_regrade_to_failed_excludes_the_run_as_ungraded(
        regrade_mod, tmp_path, monkeypatch):
    run1 = _mk_run_dir(tmp_path, run="run_1")
    run2 = _mk_run_dir(tmp_path, run="run_2")
    (run1 / "score.json").write_text(json.dumps(_healthy(0.80)), encoding="utf-8")
    (run2 / "score.json").write_text(json.dumps(_healthy(0.30)), encoding="utf-8")

    # The regrade's judge dies outright.
    _stub_judge(regrade_mod, monkeypatch, _dead_judge())
    regrade_mod.regrade(run2, rubric_override=_rubric(tmp_path))

    # Gate: the artifact flipped.
    assert (run2 / "score.failed.json").is_file()
    assert not (run2 / "score.json").exists()

    doc = _summary(run2.parent)
    assert doc["runs_ungraded"] == 1
    assert doc["runs_used"] == 1
    assert _entry(doc, 2)["grading_status"] == "failed"
    # Averaged over run_1 only. A fabricated 0.0 for run_2 would give 0.40.
    assert doc["average_reward"] == pytest.approx(0.80)
    assert doc["average_rubric_reward"] == pytest.approx(0.80)


def test_regrade_over_a_failed_run_restores_it_to_the_averages(
        regrade_mod, tmp_path, monkeypatch):
    run1 = _mk_run_dir(tmp_path, run="run_1")
    run2 = _mk_run_dir(tmp_path, run="run_2")
    (run1 / "score.json").write_text(json.dumps(_healthy(0.80)), encoding="utf-8")

    _stub_judge(regrade_mod, monkeypatch, _dead_judge())
    regrade_mod.regrade(run2, rubric_override=_rubric(tmp_path))
    assert _summary(run2.parent)["runs_ungraded"] == 1

    # Second attempt succeeds: mutual exclusion + a refreshed rollup.
    _stub_judge(regrade_mod, monkeypatch, _healthy(0.60))
    regrade_mod.regrade(run2, rubric_override=_rubric(tmp_path))

    assert (run2 / "score.json").is_file()
    assert not (run2 / "score.failed.json").exists()
    doc = _summary(run2.parent)
    assert "runs_ungraded" not in doc
    assert "grading_status" not in _entry(doc, 2)
    assert doc["average_reward"] == pytest.approx(0.70)


def test_every_run_ungraded_keeps_the_runs_visible(regrade_mod, tmp_path, monkeypatch):
    run1 = _mk_run_dir(tmp_path)
    _stub_judge(regrade_mod, monkeypatch, _dead_judge())
    regrade_mod.regrade(run1, rubric_override=_rubric(tmp_path))

    doc = _summary(run1.parent)
    assert doc["runs"] == 1
    assert doc["runs_ungraded"] == 1
    assert doc["all_runs_excluded"] is True
    # per_run keeps the record for forensics even with nothing to average.
    assert len(doc["per_run"]) == 1


# ---------------------------------------------------------------------------
# write_pass_summary — the shared, locked writer
# ---------------------------------------------------------------------------


def test_write_pass_summary_takes_the_batch_lock(tmp_path, monkeypatch):
    model_dir = tmp_path / "trajectories" / "claude"
    (model_dir / "run_1").mkdir(parents=True)
    (model_dir / "run_1" / "score.json").write_text(
        json.dumps(_healthy(0.5)), encoding="utf-8")

    seen = []
    real_locked = shared_ps.locked

    def spy(lock_path):
        seen.append(Path(lock_path))
        return real_locked(lock_path)

    monkeypatch.setattr(shared_ps, "locked", spy)
    bps.write_pass_summary(model_dir)

    assert seen == [model_dir / ".pass_summary.lock"]


def test_there_is_exactly_one_locked_write_implementation():
    # Advisory locks only exclude when every writer names the SAME file. That
    # used to be enforced by asserting the literal lock path appeared in each
    # hand-synced copy; the copies are gone, so the stronger invariant is that
    # no copy has grown back — the batch writer and the repair CLIs must all
    # route to src/utils/pass_summary rather than re-open the lock themselves.
    shared_src = (_REPO_ROOT / "src" / "utils" / "pass_summary.py").read_text(
        encoding="utf-8")
    assert "fcntl.flock" in shared_src
    assert 'PASS_SUMMARY_LOCK_FILENAME = ".pass_summary.lock"' in shared_src

    for rel in ("eval/run_batch.py", "script/backfill_pass_summary.py",
                "script/rebuild_pass_summary.py", "script/aggregate_runs.py"):
        src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "fcntl.flock" not in src, f"{rel} re-implements the pass_summary lock"
        assert "pass_summary" in src

    import eval.run_batch as rb
    assert bps._locked is shared_ps.locked
    assert bps.write_pass_summary is shared_ps.write_pass_summary
    assert rb._locked is shared_ps.locked


def test_write_pass_summary_rebuilds_inside_the_lock(tmp_path, monkeypatch):
    # Ordering invariant: the doc must be computed while holding the lock, or a
    # rep finishing mid-rebuild is dropped from the doc we then write.
    model_dir = tmp_path / "trajectories" / "claude"
    (model_dir / "run_1").mkdir(parents=True)
    (model_dir / "run_1" / "score.json").write_text(
        json.dumps(_healthy(0.5)), encoding="utf-8")

    order = []
    real_locked, real_rebuild = shared_ps.locked, shared_ps.rebuild_model_dir

    def spy_locked(lock_path):
        order.append("lock")
        return real_locked(lock_path)

    def spy_rebuild(md, model_type=None):
        order.append("rebuild")
        return real_rebuild(md, model_type)

    monkeypatch.setattr(shared_ps, "locked", spy_locked)
    monkeypatch.setattr(shared_ps, "rebuild_model_dir", spy_rebuild)
    bps.write_pass_summary(model_dir)

    assert order == ["lock", "rebuild"]


def test_write_pass_summary_returns_none_with_no_run_dirs(tmp_path):
    model_dir = tmp_path / "trajectories" / "claude"
    model_dir.mkdir(parents=True)
    assert bps.write_pass_summary(model_dir) is None
    assert not (model_dir / "pass_summary.json").exists()


def test_regrade_survives_a_pass_summary_refresh_failure(
        regrade_mod, tmp_path, monkeypatch, capsys):
    # The refresh is best-effort: a regrade must still deliver score.json even
    # if the rollup write fails, and must say so rather than fail silently.
    run1 = _mk_run_dir(tmp_path)
    _stub_judge(regrade_mod, monkeypatch, _healthy(0.5))

    def boom(_model_dir):
        raise OSError("disk full")

    monkeypatch.setattr(regrade_mod, "write_pass_summary", boom)
    out = regrade_mod.regrade(run1, rubric_override=_rubric(tmp_path))

    assert out["overall_score"] == 0.5
    assert (run1 / "score.json").is_file()
    assert "pass_summary refresh failed" in capsys.readouterr().err


def test_pass_summary_encoder_is_byte_aligned_with_the_batch_writer(tmp_path):
    # Three writers touch this file; a differing encoder shows up as spurious
    # whole-file diffs depending on who wrote last.
    doc = {"model": "claude", "runs": 1, "per_run": [], "note": "café"}
    assert bps._pass_summary_text(doc) == json.dumps(doc, indent=2)
