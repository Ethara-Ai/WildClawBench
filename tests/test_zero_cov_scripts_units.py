"""Unit coverage for the previously zero-coverage utility scripts (tranche 1a):

  * script/grade_golden.py          — council-grade a golden trajectory (the
    LLM judge grade_with_rubric is monkeypatched; path roots are redirected to
    tmp via the module's REPO_ROOT global).

Everything runs OFFLINE and deterministically: no network, no child processes.
__main__ guards run via runpy.run_path(run_name="__main__").
"""
from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "script"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_script(filename: str, mod_alias: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(mod_alias, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ======================================================================
# script/grade_golden.py
# ======================================================================


@pytest.fixture(scope="module")
def gg():
    return _load_script("grade_golden.py", "_t_grade_golden")


def _mk_golden_task(root: Path, task: str, *, rubric, with_prompt=True):
    g = root / "golden_trajectories" / task
    g.mkdir(parents=True)
    (g / "golden_trajectory.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    inp = root / "input" / task
    inp.mkdir(parents=True)
    (inp / "rubric.json").write_text(json.dumps(rubric), encoding="utf-8")
    if with_prompt:
        (inp / "prompt.txt").write_text("do the thing\n", encoding="utf-8")
    return g


FULL_SCORES = {
    "overall_score": 0.9,
    "rubric_weights_percentage": 90.0,
    "criteria_total": 3, "criteria_passed": 2, "criteria_failed": 1,
    "criteria_abstained": 0,
    "judge_council": {
        "surviving": [{"model": "sonnet"}],
        "failed": [{"model": "kimi", "error": "boom"}],
    },
    "criteria": [
        {"passed": True, "criterion": "ok"},
        {"passed": False, "is_positive": True, "weight": 3,
         "criterion": "posts invoice", "rationale": "missing"},
        {"passed": False, "is_positive": False, "weight": -5,
         "criterion": "no distractor", "rationale": "fired"},
    ],
}


def test_gg_success_path_with_council_and_misses(gg, tmp_path, capsys, monkeypatch):
    _mk_golden_task(tmp_path, "T1", rubric=[{"criterion": "a"}])
    monkeypatch.setattr(gg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gg, "_condense_transcript_for_judge", lambda traj: "TRANSCRIPT")
    monkeypatch.setattr(gg, "grade_with_rubric", lambda *a, **k: dict(FULL_SCORES))
    monkeypatch.setattr(sys, "argv", ["grade_golden.py", "T1"])
    assert gg.main() == 0
    out = capsys.readouterr().out
    assert "GOLDEN GRADE" in out and "council surviving = 1/2" in out
    assert "FAILED member: kimi" in out
    assert "non-passing criteria" in out and "[NEG w=-5]" in out and "[POS w=3]" in out
    written = json.loads(
        (tmp_path / "golden_trajectories" / "T1" / "score_golden.json").read_text())
    assert written["overall_score"] == 0.9


def test_gg_error_result_dict_rubric_and_default_task(gg, tmp_path, capsys, monkeypatch):
    # Default argv task + dict-shaped rubric.json + missing prompt.txt.
    _mk_golden_task(tmp_path, "ALDEN_002_haul_out_week",
                    rubric={"rubrics": [{"criterion": "a"}]}, with_prompt=False)
    monkeypatch.setattr(gg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gg, "_condense_transcript_for_judge", lambda traj: "")
    monkeypatch.setattr(gg, "grade_with_rubric", lambda *a, **k: {"error": "no creds"})
    monkeypatch.setattr(sys, "argv", ["grade_golden.py"])
    assert gg.main() == 1
    assert "ERROR: no creds" in capsys.readouterr().out


def test_gg_syspath_guard_inserts_repo_root(monkeypatch):
    """Fresh load with REPO_ROOT scrubbed from sys.path takes the guard's True
    branch (the module re-adds the repo root before its harness imports)."""
    scrubbed = [p for p in sys.path if p != str(REPO_ROOT)]
    monkeypatch.setattr(sys, "path", scrubbed)
    mod = _load_script("grade_golden.py", "_t_grade_golden_syspath")
    assert str(REPO_ROOT) in sys.path
    assert mod.REPO_ROOT == REPO_ROOT


def test_gg_dunder_main_missing_task_raises(gg, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["grade_golden.py", "___no_such_task___"])
    with pytest.raises(FileNotFoundError):
        runpy.run_path(str(SCRIPT_DIR / "grade_golden.py"), run_name="__main__")
