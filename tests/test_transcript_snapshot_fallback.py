"""Transcript-collection resilience — regression tests for RC-2 in
docs/RCA_missing_score_json.md.

Pins the contract introduced by the missing-score root-cause fix (Fix B):

- `OpenClawAgent.run_task` freezes a /tmp snapshot of the transcript right
  after the agent finishes (best-effort, inside try/except), so a container
  that dies before teardown no longer costs the run its chat.jsonl.
- `OpenClawAgent.collect_usage` retries a failed docker cp once, then falls
  back to that snapshot; a snapshot-restored transcript counts as a valid
  usage source.
- When neither the copy nor a snapshot is available, behavior matches the
  pre-fix contract: zeroed usage dict, no chat.jsonl, no exception.

No docker, no sidecar, no network: docker cp is monkeypatched.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNNER_PATH = ROOT / "src" / "agents" / "openclaw" / "runner.py"


def _failing_docker_cp(cmd, **kwargs):  # noqa: ANN001
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such container")


def test_collect_usage_restores_chat_from_snapshot(tmp_path: Path, monkeypatch) -> None:
    from src.agents.openclaw import runner as ocr

    task_id = "snapfallback_test_abc123"
    snap = Path(tempfile.gettempdir()) / f"chat-snap-{task_id}.jsonl"
    snap.write_text('{"role": "assistant"}\n', encoding="utf-8")

    monkeypatch.setattr(ocr.subprocess, "run", _failing_docker_cp)
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    try:
        usage = agent.collect_usage(task_id=task_id, output_dir=tmp_path, elapsed_time=1.0)
    finally:
        snap.unlink(missing_ok=True)

    transcript = tmp_path / "chat.jsonl"
    assert transcript.exists(), "chat.jsonl must be restored from the /tmp snapshot"
    assert transcript.read_text(encoding="utf-8") == '{"role": "assistant"}\n'
    assert usage.get("elapsed_time") == 1.0
    # F2 provenance: recovery must be marked so output.json/score.json carry it.
    assert usage.get("__snapshot_recovered__") is True


def test_collect_usage_retries_docker_cp_once(tmp_path: Path, monkeypatch) -> None:
    from src.agents.openclaw import runner as ocr

    task_id = "retry_test_def456"
    calls: list[list[str]] = []

    def _flaky_docker_cp(cmd, **kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="transient daemon error")
        Path(cmd[-1]).write_text('{"role": "assistant"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", _flaky_docker_cp)
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    agent.collect_usage(task_id=task_id, output_dir=tmp_path, elapsed_time=1.0)

    assert len(calls) == 2, "a failed docker cp must be retried exactly once"
    assert (tmp_path / "chat.jsonl").exists()
    # A retry-succeeded direct copy is NOT a snapshot recovery — no F2 marker.
    calls.clear()
    usage2 = agent.collect_usage(task_id="retry_test_def456b", output_dir=tmp_path, elapsed_time=1.0)
    assert "__snapshot_recovered__" not in usage2


def test_collect_usage_no_snapshot_keeps_prefix_contract(tmp_path: Path, monkeypatch) -> None:
    from src.agents.openclaw import runner as ocr

    monkeypatch.setattr(ocr.subprocess, "run", _failing_docker_cp)
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    usage = agent.collect_usage(task_id="no_snap_test_xyz", output_dir=tmp_path, elapsed_time=2.0)

    assert not (tmp_path / "chat.jsonl").exists()
    assert usage.get("usage_source") == "none"
    assert usage.get("request_count") == 0
    assert usage.get("elapsed_time") == 2.0


def test_snapshot_write_is_atomic_on_interrupted_cp(monkeypatch) -> None:
    """F1: an interrupted snapshot docker cp (writes partial bytes, exits
    nonzero) must never leave anything at the final chat-snap name — only a
    complete, renamed file may ever exist there."""
    from src.agents.openclaw import runner as ocr

    task_id = "atomic_test_ghi789"
    final_snap = Path(tempfile.gettempdir()) / f"chat-snap-{task_id}.jsonl"
    tmp_snap = Path(str(final_snap) + ".tmp")
    final_snap.unlink(missing_ok=True)

    def _interrupted_docker_cp(cmd, **kwargs):  # noqa: ANN001
        Path(cmd[-1]).write_text('{"role": "assis', encoding="utf-8")  # truncated
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="killed mid-copy")

    monkeypatch.setattr(ocr.subprocess, "run", _interrupted_docker_cp)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    returned = agent.prepare_grading_transcript(task_id)

    assert not final_snap.exists(), "partial bytes must never land at the final snap name"
    assert not tmp_snap.exists(), "the .tmp staging file must be cleaned up on failure"
    assert returned == agent.transcript_container_path


def test_snapshot_success_lands_complete_file_at_final_name(monkeypatch) -> None:
    from src.agents.openclaw import runner as ocr

    task_id = "atomic_ok_test_jkl012"
    final_snap = Path(tempfile.gettempdir()) / f"chat-snap-{task_id}.jsonl"
    final_snap.unlink(missing_ok=True)

    def _ok_docker_cp(cmd, **kwargs):  # noqa: ANN001
        Path(cmd[-1]).write_text('{"role": "assistant"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", _ok_docker_cp)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    try:
        returned = agent.prepare_grading_transcript(task_id)
        assert returned == str(final_snap)
        assert final_snap.read_text(encoding="utf-8") == '{"role": "assistant"}\n'
    finally:
        final_snap.unlink(missing_ok=True)


def test_partial_chat_jsonl_removed_when_no_snapshot(tmp_path: Path, monkeypatch) -> None:
    """F1 (same truncation class, direct-cp side): a failed docker cp that
    left partial bytes in the run dir must not survive to be graded when no
    snapshot exists to overwrite it."""
    from src.agents.openclaw import runner as ocr

    def _interrupted_docker_cp(cmd, **kwargs):  # noqa: ANN001
        Path(cmd[-1]).write_text('{"role": "assis', encoding="utf-8")  # truncated
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="killed mid-copy")

    monkeypatch.setattr(ocr.subprocess, "run", _interrupted_docker_cp)
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    agent = ocr.OpenClawAgent(gateway_port=18789)
    usage = agent.collect_usage(task_id="partial_rm_test_mno345", output_dir=tmp_path, elapsed_time=1.0)

    assert not (tmp_path / "chat.jsonl").exists(), "partial transcript must be removed, not graded"
    assert usage.get("usage_source") == "none"


def test_run_task_takes_besteffort_snapshot_after_agent_finishes() -> None:
    """AST invariant (in the style of tests/test_score_json_last_resort.py):
    run_task must call prepare_grading_transcript inside a try whose handler
    swallows Exception — the snapshot is best-effort and may never fail a run.
    """
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    run_task = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_task"
    )
    for node in ast.walk(run_task):
        if not isinstance(node, ast.Try):
            continue
        calls_snapshot = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "prepare_grading_transcript"
            for stmt in node.body
            for c in ast.walk(stmt)
        )
        swallows = any(
            h.type is not None
            and isinstance(h.type, ast.Name)
            and h.type.id == "Exception"
            for h in node.handlers
        )
        if calls_snapshot and swallows:
            return
    raise AssertionError(
        "run_task must invoke self.prepare_grading_transcript(...) in a "
        "try/except Exception block after the agent finishes"
    )
