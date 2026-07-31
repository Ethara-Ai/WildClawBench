"""Tests for src/utils/sim_clock.compute_sim_clock (agent clock-shim source)."""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.sim_clock import compute_sim_clock  # noqa: E402


def _write(task_dir, payload):
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompts.json").write_text(json.dumps(payload), encoding="utf-8")
    return {"task_dir": str(task_dir)}


def test_schema_a_iso_timestamp(tmp_path):
    task = _write(tmp_path / "t", {
        "timezone": "America/Chicago",
        "turns": [{"turn": "T0", "timestamp": "2026-11-03T07:38:00-06:00", "message": "hi"}],
    })
    sc = compute_sim_clock(task)
    assert sc is not None
    assert sc.tz == "America/Chicago"
    assert sc.epoch_ms == int(datetime.fromisoformat("2026-11-03T07:38:00-06:00").timestamp() * 1000)


def test_schema_b_day_time_with_window_timezone(tmp_path):
    # timezone nested inside window; day index anchors on window start.
    task = _write(tmp_path / "t", {
        "window": {"start": "2026-11-03", "end": "2026-11-05", "timezone": "America/Chicago"},
        "turns": [{"turn": "T0", "day": 1, "time": "08:06", "message": "hi"}],
    })
    sc = compute_sim_clock(task)
    assert sc is not None
    expected = int(datetime(2026, 11, 3, 8, 6, tzinfo=ZoneInfo("America/Chicago")).timestamp() * 1000)
    assert sc.epoch_ms == expected
    assert sc.tz == "America/Chicago"


def test_schema_b_day_offset(tmp_path):
    # day=3 => two days after the window start.
    task = _write(tmp_path / "t", {
        "window": {"start": "2026-11-03", "timezone": "America/Chicago"},
        "turns": [{"turn": "T5", "day": 3, "time": "10:00", "message": "later"}],
    })
    sc = compute_sim_clock(task)
    expected = int(datetime(2026, 11, 5, 10, 0, tzinfo=ZoneInfo("America/Chicago")).timestamp() * 1000)
    assert sc.epoch_ms == expected


def test_missing_prompts_json_returns_none(tmp_path):
    (tmp_path / "t").mkdir()
    assert compute_sim_clock({"task_dir": str(tmp_path / "t")}) is None


def test_no_task_dir_returns_none():
    assert compute_sim_clock({}) is None


def test_schema_b_without_timezone_is_none(tmp_path):
    # No resolvable timezone anywhere -> undetermined (agent stays on real time).
    task = _write(tmp_path / "t", {
        "window": {"start": "2026-11-03"},
        "turns": [{"turn": "T0", "day": 1, "time": "08:06", "message": "hi"}],
    })
    assert compute_sim_clock(task) is None


def test_malformed_json_returns_none(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "prompts.json").write_text("{not json", encoding="utf-8")
    assert compute_sim_clock({"task_dir": str(d)}) is None
