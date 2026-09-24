import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "watch_batch", Path(__file__).resolve().parents[1] / "script" / "watch_batch.py")
watch_batch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(watch_batch)


def _make_run(root: Path, backend, task, model, run, log_lines=None,
              score=None, failed_score=None):
    run_dir = root / "output" / backend / task / "trajectories" / model / run
    run_dir.mkdir(parents=True)
    if log_lines is not None:
        (run_dir / "harness_debug.log").write_text("\n".join(log_lines) + "\n")
    if score is not None:
        (run_dir / "score.json").write_text(json.dumps(score))
    if failed_score is not None:
        (run_dir / "score.failed.json").write_text(json.dumps(failed_score))
    return run_dir


@pytest.fixture
def batch_root(tmp_path):
    _make_run(
        tmp_path, "openclaw", "task_a", "model_x", "run_1",
        log_lines=[
            "Agent turn 1/3 starting",
            "Agent turn 2/3 starting",
            "Agent turn 2 STALLED (no sidecar traffic for WCB_TURN_STALL_SECONDS) "
            "- breaking dead connections and retrying the turn once",
        ],
    )
    _make_run(
        tmp_path, "openclaw", "task_b", "model_x", "run_1",
        log_lines=["Agent turn 1/1 starting", "Agent turn 1 finished"],
        score={"overall_score": 0.8, "criteria_total": 5, "criteria_abstained": 0,
               "injection_ok": True},
    )
    _make_run(
        tmp_path, "openclaw", "task_c", "model_x", "run_1",
        log_lines=["Agent turn 1/1 starting"],
        failed_score={"error": "judge transport timed out after 3 retries"},
    )
    return tmp_path


def test_first_scan_emits_expected_events(batch_root):
    state = {}
    events = watch_batch.scan(batch_root, state)
    by_task = {}
    for ev in events:
        by_task.setdefault(ev["task"], []).append(ev["event"])

    assert by_task["task_a"] == ["launched", "turn", "stalled"]
    assert by_task["task_b"] == ["completed"]
    assert by_task["task_c"] == ["ungraded"]


def test_second_scan_with_no_changes_emits_nothing(batch_root):
    state = {}
    watch_batch.scan(batch_root, state)
    events = watch_batch.scan(batch_root, state)
    assert events == []
