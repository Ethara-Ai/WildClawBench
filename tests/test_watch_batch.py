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


def test_stale_score_json_next_to_score_failed_json_still_ungraded(tmp_path):
    # Regression: a leftover score.json from an earlier successful grade,
    # sitting next to a later score.failed.json, must not outrank the
    # failure — the run is ungraded, period.
    _make_run(
        tmp_path, "openclaw", "task_d", "model_x", "run_1",
        log_lines=[f"Agent turn {t}/18 starting" for t in range(1, 19)],
        score={"overall_score": 0.5, "criteria_total": 5, "criteria_abstained": 0},
        failed_score={"error": "judge transport timed out",
                      "turns_completed": 18, "turns_planned": 18},
    )
    events = watch_batch.scan(tmp_path, {})
    assert [ev["event"] for ev in events] == ["ungraded"]


def test_render_shows_header_counts_running_row_and_recent(batch_root):
    _make_run(
        batch_root, "openclaw", "task_running", "model_x", "run_1",
        log_lines=["Agent turn 2/5 starting"],
    )
    state = {}
    events = watch_batch.scan(batch_root, state)
    block = watch_batch.render(state, events)

    assert "batch progress" in block
    assert "launched 4" in block
    assert "completed 1" in block
    assert "incomplete 0" in block
    assert "ungraded 1" in block
    assert "running 2" in block
    assert "task_running" in block
    assert "2/5" in block
    assert "completed task_b 80.0%" in block


def test_incomplete_reason_found_past_a_large_tail_of_later_log_output(tmp_path):
    # Regression: the give-up line sits well before a lot of later,
    # unrelated log output (grading-adjacent lines) — 200KB+ of it — which
    # used to push it out of a fixed-size tail-only read.
    lines = [
        "2026-09-23 15:33:14 | WARNING | MainThread | src.agents.openclaw.runner "
        "| [task] Agent turn 8 stalled twice \u2014 giving up",
        "2026-09-23 15:33:15 | WARNING | MainThread | src.agents.openclaw.runner "
        "| [task] RUN INCOMPLETE: 12 of 17 scheduled turns executed "
        "(timed_out_turn=None) \u2014 score will be flagged run_incomplete",
    ]
    padding = ["filler line " + "x" * 200 for _ in range(2000)]
    _make_run(
        tmp_path, "openclaw", "task_e", "model_x", "run_1",
        log_lines=lines + padding,
        score={"overall_score": 0.0, "run_incomplete": True,
               "turns_completed": 12, "turns_planned": 17},
    )
    log_path = tmp_path / "output/openclaw/task_e/trajectories/model_x/run_1/harness_debug.log"
    assert log_path.stat().st_size > 64 * 1024

    events = watch_batch.scan(tmp_path, {})
    assert len(events) == 1
    assert events[0]["event"] == "incomplete"
    assert events[0]["reason"] == "stalled twice"
