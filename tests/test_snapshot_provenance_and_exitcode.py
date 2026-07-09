"""TL review follow-ups F2 (snapshot provenance) and F4 (exit-code diagnostics).

Pins:
- F4: `AgentExecution.agent_exit_code` exists, defaults to None, and never
  implies `error` (partial-credit / grade-everything design).
- F4: run_single_task's last-resort stub weaves `__agent_exit_code__` into its
  error text (AST-level, in the style of tests/test_score_json_last_resort.py).
- F2: `_build_trajectory` stamps `__snapshot_recovered__` (source check) and
  `script/regrade.py` carries it forward from output.json (source check).
- F2: `script/aggregate_runs.py::aggregate` reports per-task
  `snapshot_recovered_runs` and per-model `snapshot_recovered_rate`
  (functional, over a synthetic output tree — no docker, no LLM).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_BATCH = ROOT / "eval" / "run_batch.py"
REGRADE = ROOT / "script" / "regrade.py"
AGGREGATE = ROOT / "script" / "aggregate_runs.py"


# ------------------------------------------------------------------ F4

def test_agent_execution_exit_code_defaults_none_and_never_errors() -> None:
    from src.agents.base import AgentExecution

    ex = AgentExecution(elapsed_time=1.0)
    assert ex.agent_exit_code is None
    ex2 = AgentExecution(elapsed_time=1.0, agent_exit_code=137)
    assert ex2.error is None, "nonzero exit code must not imply error (grade-everything design)"


def test_last_resort_stub_weaves_agent_exit_code() -> None:
    tree = ast.parse(RUN_BATCH.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_single_task"
    )
    finally_consts = {
        node.value
        for outer in ast.walk(fn) if isinstance(outer, ast.Try)
        for stmt in outer.finalbody
        for node in ast.walk(ast.Module(body=[stmt], type_ignores=[]))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "__agent_exit_code__" in finally_consts, (
        "run_single_task's finally block must read __agent_exit_code__ for the "
        "last-resort stub's diagnostic error text"
    )


def test_openclaw_runner_records_exit_code_without_error() -> None:
    src = (ROOT / "src" / "agents" / "openclaw" / "runner.py").read_text(encoding="utf-8")
    assert "agent_exit_code=agent_exit_code" in src
    assert "timed_out" in src, "timeout kills must be excluded from exit-code recording"


# ------------------------------------------------------------------ F2

def test_build_trajectory_and_regrade_carry_provenance_marker() -> None:
    rb_src = RUN_BATCH.read_text(encoding="utf-8")
    assert rb_src.count("__snapshot_recovered__") >= 3, (
        "_build_trajectory must stamp the marker on traj (output.json) and on "
        "both score.json write paths"
    )
    rg_src = REGRADE.read_text(encoding="utf-8")
    assert "__snapshot_recovered__" in rg_src, (
        "regrade.py must preserve the provenance marker from output.json"
    )


def _write_score(run_dir: Path, pct: float, recovered: bool) -> None:
    run_dir.mkdir(parents=True)
    doc = {
        "overall_score": pct / 100.0,
        "rubric_weights_percentage": pct,
        "criteria_total": 4,
        "criteria_passed": 2,
        "criteria_failed": 2,
    }
    if recovered:
        doc["__snapshot_recovered__"] = True
    (run_dir / "score.json").write_text(json.dumps(doc), encoding="utf-8")


def test_aggregate_reports_snapshot_recovery_rate(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("_agg_under_test", AGGREGATE)
    agg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg)

    base = tmp_path / "openclaw" / "some_task" / "trajectories" / "claude"
    _write_score(base / "run_1", 50.0, recovered=False)
    _write_score(base / "run_2", 75.0, recovered=True)

    summary = agg.aggregate(tmp_path, backend_filter="openclaw")

    [task_row] = summary["by_task_model"]
    assert task_row["snapshot_recovered_runs"] == 1
    flagged = [r for r in task_row["runs"] if r.get("snapshot_recovered")]
    assert len(flagged) == 1 and flagged[0]["run"] == 2

    [model_row] = summary["by_model"]
    assert model_row["snapshot_recovered_runs"] == 1
    assert model_row["snapshot_recovered_rate"] == 0.5
