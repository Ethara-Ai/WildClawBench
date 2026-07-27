"""Regression tests for script/repackage_to_bundle.py::_find_input_task_dir.

Pins the persona-name collision fix. Before the fix, persona_core() stripped a
trailing numeric token, so sibling tasks that differ only by suffix (e.g.
"jordan-mcdaniel_01" vs "jordan-mcdaniel_02") collapsed to the same key and the
resolver returned whichever iterdir() yielded first -- silently re-sourcing a
bundle's PROMPT.md / TRUTH.md / data/tests from the WRONG task. These tests lock
in:

  1. exact directory-name match wins over a persona_core collision,
  2. resolution is independent of directory order,
  3. an ambiguous persona_core match (no exact hit) fails LOUD instead of
     guessing,
  4. the legitimate lossy path (uuid-suffixed run dir -> "_NN" input dir) still
     resolves,
  5. no candidate -> None.

Static: no docker, no network, no real input/ dirs (all under tmp_path).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_repackage_module():
    spec = importlib.util.spec_from_file_location(
        "_rp_resolution_test", REPO_ROOT / "script" / "repackage_to_bundle.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rp():
    return _load_repackage_module()


def _mk(root: Path, *names: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n).mkdir(parents=True, exist_ok=True)
    return root


def test_persona_core_collapses_numeric_sibling_suffix(rp):
    assert rp.persona_core("jordan-mcdaniel_01") == rp.persona_core("jordan-mcdaniel_02")


def test_exact_match_wins_over_persona_core_collision(rp, tmp_path):
    root = _mk(tmp_path / "input", "jordan-mcdaniel_01", "jordan-mcdaniel_02")
    assert rp._find_input_task_dir(root, "jordan-mcdaniel_01").name == "jordan-mcdaniel_01"
    assert rp._find_input_task_dir(root, "jordan-mcdaniel_02").name == "jordan-mcdaniel_02"


def test_resolution_is_order_independent(rp, tmp_path):
    a = _mk(tmp_path / "a", "joseph-fields_02", "joseph-fields_01")
    b = _mk(tmp_path / "b", "joseph-fields_01", "joseph-fields_02")
    assert rp._find_input_task_dir(a, "joseph-fields_01").name == "joseph-fields_01"
    assert rp._find_input_task_dir(b, "joseph-fields_01").name == "joseph-fields_01"


def test_ambiguous_persona_core_without_exact_match_raises(rp, tmp_path):
    root = _mk(tmp_path / "input", "jordan-mcdaniel_01", "jordan-mcdaniel_02")
    with pytest.raises(ValueError):
        rp._find_input_task_dir(root, "jordan-mcdaniel")


def test_uuid_run_dir_bridges_to_single_nn_input(rp, tmp_path):
    root = _mk(tmp_path / "input", "ben-cox_01")
    got = rp._find_input_task_dir(root, "ben-cox_8fc24d4b")
    assert got is not None and got.name == "ben-cox_01"


def test_no_candidate_returns_none(rp, tmp_path):
    root = _mk(tmp_path / "input", "amanda-webb_01")
    assert rp._find_input_task_dir(root, "nate-wright_01") is None


def test_missing_input_root_returns_none(rp, tmp_path):
    assert rp._find_input_task_dir(tmp_path / "does-not-exist", "jordan-mcdaniel_01") is None
