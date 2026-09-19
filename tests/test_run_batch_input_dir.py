"""Unit coverage for `run_batch.py --input-dir` fan-out (_input_dir_child_argv /
_run_input_dir). Offline: children are a stub interpreter, never the real
harness — no docker / network / sidecar."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import run_batch as rb  # noqa: E402
from src.utils.cli_args import build_run_batch_parser  # noqa: E402


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--input-dir", "D", "-P", "2", "--model", "m"], ["--model", "m"]),
        (["--input-dir=D", "--parallel-tasks=2", "--parallel", "1"], ["--parallel", "1"]),
        (["-P2", "--input-dir", "D", "--judge-council"], ["--judge-council"]),
        (["--inp", "D", "--parallel-t", "2"], []),  # argparse prefix abbreviations
        (["--input-dir", "D", "--parallel", "3"], ["--parallel", "3"]),  # --parallel is forwarded
    ],
)
def test_child_argv_strips_fanout_flags(argv, expected):
    child = rb._input_dir_child_argv(argv, "D/t")
    assert child == expected + ["--task", "D/t"]
    # The child argv must parse as a valid single-task invocation.
    ns = build_run_batch_parser("m", 1).parse_args(child)
    assert ns.task == "D/t" and ns.input_dir is None


@pytest.fixture
def task_root(tmp_path):
    root = tmp_path / "input"
    for name in ("a_task", "b_task", "c_task"):
        (root / name).mkdir(parents=True)
    (root / "stray.txt").write_text("not a task")
    return root


@pytest.fixture
def stub_python(tmp_path, monkeypatch):
    """Replace the child interpreter with a script that records its argv and
    fails for b_task; also redirect per-task logs into tmp_path."""
    rec = tmp_path / "rec"
    rec.mkdir()
    stub = tmp_path / "fakepy"
    stub.write_text(
        "#!/bin/bash\n"
        't="${@: -1}"; n=$(basename "$t")\n'
        f'printf "%s\\n" "$@" > {rec}/$n.argv\n'
        'echo "out $n"; echo "err $n" >&2\n'
        '[[ "$n" == b_task ]] && exit 3; exit 0\n'
    )
    stub.chmod(0o755)
    monkeypatch.setattr(rb.sys, "executable", str(stub))
    fake_script = tmp_path / "eval" / "run_batch.py"
    fake_script.parent.mkdir()
    monkeypatch.setattr(rb, "__file__", str(fake_script))
    return rec


@pytest.mark.parametrize("par", [1, 2])
def test_runs_every_task_and_isolates_failure(task_root, stub_python, tmp_path, par):
    rc = rb._run_input_dir(str(task_root), ["--input-dir", str(task_root), "--model", "m"], par)
    assert rc == 1  # b_task failed, but a_task and c_task still ran
    ran = sorted(p.stem for p in stub_python.glob("*.argv"))
    assert ran == ["a_task", "b_task", "c_task"]
    child = (stub_python / "a_task.argv").read_text().split()
    assert child[1:] == ["--model", "m", "--task", str(task_root / "a_task")]
    logs = sorted((tmp_path / "logs").glob("*_input_dir_*.log")) if par > 1 else []
    if par > 1:
        assert len(logs) == 3
        assert "out a_task" in logs[0].read_text() and "err a_task" in logs[0].read_text()


def test_all_pass_returns_zero(task_root, stub_python):
    (task_root / "b_task").rename(task_root / "d_task")
    assert rb._run_input_dir(str(task_root), [], 2) == 0


@pytest.mark.parametrize("par", [0, -1, 9])
def test_invalid_parallelism_launches_nothing(task_root, stub_python, par):
    assert rb._run_input_dir(str(task_root), [], par) == 2
    assert not list(stub_python.glob("*.argv"))


def test_missing_and_empty_dir(tmp_path, stub_python):
    assert rb._run_input_dir(str(tmp_path / "nope"), [], 1) == 2
    (tmp_path / "empty").mkdir()
    assert rb._run_input_dir(str(tmp_path / "empty"), [], 1) == 2
