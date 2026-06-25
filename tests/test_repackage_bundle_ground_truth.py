"""Ground-truth publishing in the raw-output -> published-bundle repackager.

`script/repackage_to_bundle.py` re-sources a handful of files from the ORIGINAL
input task dir (prompt.txt, persona/, staged inputs). This suite pins the
golden_steer_flow.md -> ground-truth.md feature added on top of that flow:

  1. When the input task dir ships golden_steer_flow.md, the bundle root gets a
     ground-truth.md with BYTE-IDENTICAL contents (renamed, not transformed).
  2. When it does NOT ship the file, no ground-truth.md is emitted and the
     conversion still succeeds (the file is optional, like prompt.txt/persona/).
  3. The publish is keyed off the fuzzy persona-core match: if the run-output
     task dir can't be matched back to an input dir, nothing is re-sourced.

`script/` is not an importable package, so the module is loaded by path the
same way tests/test_connector_ssrf_guard.py loads connector scripts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_repackager():
    path = _REPO_ROOT / "script" / "repackage_to_bundle.py"
    assert path.exists(), f"repackager missing: {path}"
    spec = importlib.util.spec_from_file_location("_test_repackage_to_bundle", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_repackage_to_bundle"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rp():
    return _load_repackager()


def _mk_source_run_tree(source_root: Path, task_id: str) -> Path:
    """Minimal raw run-output tree convert_task will accept and emit from."""
    task_dir = source_root / task_id
    run_dir = task_dir / "trajectories" / "openclaw" / "run_1"
    (run_dir / "task_output" / "logs" / "verifier").mkdir(parents=True)
    (task_dir / "rubric.json").write_text("[]", encoding="utf-8")
    (run_dir / "output.json").write_text("{}", encoding="utf-8")
    (run_dir / "score.json").write_text(
        json.dumps({"combined_reward": 1.0, "criteria": []}), encoding="utf-8"
    )
    return task_dir


def _mk_input_dir(input_root: Path, name: str, *, steer: str | None) -> Path:
    """Original input task dir; optionally ships golden_steer_flow.md."""
    d = input_root / name
    d.mkdir(parents=True)
    (d / "prompt.txt").write_text("do the thing", encoding="utf-8")
    if steer is not None:
        (d / "golden_steer_flow.md").write_text(steer, encoding="utf-8")
    return d


# Distinct run-output id and input id that both reduce to the same persona core
# ("darren weston"), exercising the fuzzy match the publish relies on. The run id
# carries an 8-hex instance suffix (dropped) and the input id a numeric suffix
# (dropped), so the cores match without the names being identical.
_RUN_TASK_ID = "darren_weston_2b2baa81"
_INPUT_TASK_ID = "darren_weston_01"


def test_ground_truth_emitted_byte_identical(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    # Non-ascii + trailing-newline-free content to prove a byte-exact copy, not
    # a text round-trip that could normalize encoding or line endings.
    steer_body = "# golden\n## Bruges — café déjà vu\n\n- step\tone\r\n- step two"
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=steer_body)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    gt = bundle / "ground-truth.md"
    assert gt.is_file(), "ground-truth.md not emitted at bundle root"
    src = input_root / _INPUT_TASK_ID / "golden_steer_flow.md"
    assert gt.read_bytes() == src.read_bytes(), "ground-truth.md is not byte-identical"
    # Renamed, not relocated: the original filename must not survive in the bundle.
    assert not (bundle / "golden_steer_flow.md").exists()


def test_no_ground_truth_when_source_absent(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)  # no golden_steer_flow.md

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None  # conversion still succeeds
    assert not (bundle / "ground-truth.md").exists()
    # Sanity: the rest of the re-sourcing still happened.
    assert (bundle / "prompt.txt").is_file()


def test_no_ground_truth_when_input_dir_unmatched(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    # Input dir for a DIFFERENT persona; fuzzy match must fail, so nothing is
    # re-sourced even though this dir ships a golden_steer_flow.md of its own.
    _mk_input_dir(input_root, "someone_else_99", steer="# not mine")

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    assert not (bundle / "ground-truth.md").exists()
    assert not (bundle / "prompt.txt").exists()


def test_filename_constants_wired(rp):
    # Guards against silent drift in the consumer-facing output name.
    assert rp.GOLDEN_STEER_FILENAME == "golden_steer_flow.md"
    assert rp.GROUND_TRUTH_FILENAME == "ground-truth.md"
