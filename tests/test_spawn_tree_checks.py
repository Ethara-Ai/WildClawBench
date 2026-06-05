from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.utils.spawn_tree_checks import (
    build_checker_state,
    build_state_file,
    load_spawn_rows,
)
from src.utils.test_executor import _RUNNER_SCRIPT


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_spawn_rows_missing_returns_empty(tmp_path: Path) -> None:
    assert load_spawn_rows(tmp_path / "absent.jsonl") == []


def test_load_spawn_rows_skips_blank_and_malformed(tmp_path: Path) -> None:
    p = tmp_path / "spawn_tree.jsonl"
    p.write_text(
        '{"spawn_id":"a","status":"ok","turn_index":1}\n'
        '\n'
        '{this is not json}\n'
        '{"spawn_id":"b","status":"ok","turn_index":2}\n',
        encoding="utf-8",
    )
    rows = load_spawn_rows(p)
    assert [r["spawn_id"] for r in rows] == ["a", "b"]


def test_build_checker_state_empty_config_returns_empty() -> None:
    out = build_checker_state(Path("/nonexistent"), None)
    assert out == {"checkers": {}}
    out2 = build_checker_state(Path("/nonexistent"), {})
    assert out2 == {"checkers": {}}


def test_build_checker_state_all_pass(tmp_path: Path) -> None:
    p = tmp_path / "spawn_tree.jsonl"
    _write_ndjson(p, [
        {"spawn_id": "s1", "status": "ok", "turn_index": 1},
        {"spawn_id": "s2", "status": "ok", "turn_index": 1},
        {"spawn_id": "s3", "status": "ok", "turn_index": 1},
        {"spawn_id": "s4", "status": "ok", "turn_index": 3},
        {"spawn_id": "s5", "status": "ok", "turn_index": 3},
    ])
    cfg = {
        "expected_per_turn": {
            "1": {"min_subagents": 3, "checker_id": "T1_C4"},
            "3": {"min_subagents": 2, "checker_id": "T3_C2"},
        },
        "aggregate_checker_id": "MA_C1",
    }
    out = build_checker_state(p, cfg)
    assert out == {"checkers": {"T1_C4": True, "T3_C2": True, "MA_C1": True}}


def test_build_checker_state_partial_fail_drops_aggregate(tmp_path: Path) -> None:
    p = tmp_path / "spawn_tree.jsonl"
    _write_ndjson(p, [
        {"spawn_id": "s1", "status": "ok", "turn_index": 1},
        {"spawn_id": "s2", "status": "ok", "turn_index": 1},
        {"spawn_id": "s3", "status": "ok", "turn_index": 1},
        {"spawn_id": "s4", "status": "ok", "turn_index": 3},
    ])
    cfg = {
        "expected_per_turn": {
            "1": {"min_subagents": 3, "checker_id": "T1_C4"},
            "3": {"min_subagents": 2, "checker_id": "T3_C2"},
        },
        "aggregate_checker_id": "MA_C1",
    }
    out = build_checker_state(p, cfg)
    assert out["checkers"]["T1_C4"] is True
    assert out["checkers"]["T3_C2"] is False
    assert out["checkers"]["MA_C1"] is False


def test_build_checker_state_only_counts_status_ok(tmp_path: Path) -> None:
    p = tmp_path / "spawn_tree.jsonl"
    _write_ndjson(p, [
        {"spawn_id": "s1", "status": "ok", "turn_index": 1},
        {"spawn_id": "s2", "status": "error", "turn_index": 1},
        {"spawn_id": "s3", "status": "timeout", "turn_index": 1},
        {"spawn_id": "s4", "status": "blocked", "turn_index": 1},
    ])
    cfg = {
        "expected_per_turn": {
            "1": {"min_subagents": 3, "checker_id": "T1_C4"},
        },
    }
    out = build_checker_state(p, cfg)
    assert out["checkers"]["T1_C4"] is False


def test_build_checker_state_missing_spawn_tree_all_false(tmp_path: Path) -> None:
    cfg = {
        "expected_per_turn": {
            "1": {"min_subagents": 1, "checker_id": "T1_C4"},
        },
        "aggregate_checker_id": "MA_C1",
    }
    out = build_checker_state(tmp_path / "absent.jsonl", cfg)
    assert out == {"checkers": {"T1_C4": False, "MA_C1": False}}


def test_build_checker_state_ignores_malformed_entries(tmp_path: Path) -> None:
    p = tmp_path / "spawn_tree.jsonl"
    _write_ndjson(p, [{"spawn_id": "s1", "status": "ok", "turn_index": 1}])
    cfg = {
        "expected_per_turn": {
            "1": {"min_subagents": 1, "checker_id": "T1_C4"},
            "garbage": {"min_subagents": 1, "checker_id": "T_GARBAGE"},
            "2": "not a dict",
            "3": {"min_subagents": 1},
        },
    }
    out = build_checker_state(p, cfg)
    assert out["checkers"]["T1_C4"] is True
    assert "T_GARBAGE" not in out["checkers"]


def test_build_state_file_writes_json(tmp_path: Path) -> None:
    spawn = tmp_path / "spawn_tree.jsonl"
    _write_ndjson(spawn, [{"spawn_id": "s1", "status": "ok", "turn_index": 1}])
    cfg = {"expected_per_turn": {"1": {"min_subagents": 1, "checker_id": "T1_C4"}}}
    out_file = tmp_path / "out" / "state.json"
    returned = build_state_file(spawn, cfg, out_file)
    assert returned == out_file
    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded == {"checkers": {"T1_C4": True}}


def _run_runner_with(test_code: str, state_obj: dict | None) -> dict:
    """Exec _RUNNER_SCRIPT in a subprocess with patched /tests paths.

    Returns the parsed JSON payload (last `{...}` line on stdout).
    """
    tmp = Path(tempfile.mkdtemp(prefix="wcb-runner-test-"))
    try:
        (tmp / "test_outputs.py").write_text(test_code, encoding="utf-8")
        if state_obj is not None:
            (tmp / "state.json").write_text(json.dumps(state_obj), encoding="utf-8")
        script = _RUNNER_SCRIPT.replace("/tests/test_outputs.py", str(tmp / "test_outputs.py"))
        script = script.replace("/tests/state.json", str(tmp / "state.json"))
        (tmp / "runner.py").write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp / "runner.py")],
            capture_output=True, text=True, timeout=60,
        )
        for line in reversed((proc.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        raise AssertionError(
            f"no JSON payload; rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_runner_supplies_state_when_required():
    code = textwrap.dedent('''
        class TestStateUser:
            def test_uses_state(self, state):
                assert state["checkers"]["T1_C4"] is True
    ''')
    payload = _run_runner_with(code, {"checkers": {"T1_C4": True}})
    assert payload["results"]["TestStateUser::test_uses_state"]["status"] == "passed"


def test_runner_errors_when_state_missing():
    code = textwrap.dedent('''
        class TestStateUser:
            def test_uses_state(self, state):
                assert state["checkers"]["T1_C4"] is True
    ''')
    payload = _run_runner_with(code, None)
    res = payload["results"]["TestStateUser::test_uses_state"]
    assert res["status"] == "errored"
    assert "state.json missing" in res["error"]


def test_runner_unchanged_for_no_state_tests():
    code = textwrap.dedent('''
        class TestPlain:
            def test_self_contained(self):
                assert 1 + 1 == 2
    ''')
    payload = _run_runner_with(code, None)
    assert payload["results"]["TestPlain::test_self_contained"]["status"] == "passed"


def test_runner_top_level_function_state_injection():
    code = textwrap.dedent('''
        def test_top_level(state):
            assert state["x"] == 42
    ''')
    payload = _run_runner_with(code, {"x": 42})
    key = "<module>::test_top_level"
    assert payload["results"][key]["status"] == "passed"
