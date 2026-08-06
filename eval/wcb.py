#!/usr/bin/env python3
"""One-line launcher for WildClawBench: ``wcb run [task]`` / ``wcb [task]``.

Opens the full-screen Textual config form (src/utils/ui/launcher.py) where
backend / model / OAuth account / parallel workers / advanced options are
picked from dropdowns, then hands off to eval/run_batch.py with:

  * --stream always on (no flag needed),
  * --interactive auto-detected from the task layout (multi-turn
    ``prompts.txt`` + openclaw + parallel 1 + real terminal),
  * WCB_CC_ACCOUNT_POOL / WCB_USE_CLAUDE_OAUTH exported from the OAuth
    Account dropdown instead of being typed on the command line.

Any extra ``--flags`` after the task are forwarded verbatim to run_batch's
parser as an escape hatch, e.g. ``wcb run input/foo --no-litellm``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.utils.ui.launcher import (  # noqa: E402
    config_to_argv, launcher_available, provider_env_overrides, run_launcher,
)


def _usage() -> str:
    return (
        "usage: wcb [run] [task-dir] [-- extra run_batch flags]\n"
        "       wcb run input/input1     # open the config TUI for that task\n"
        "       wcb                      # open the TUI and pick a task there"
    )


def _split_own_argv(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return (task, passthrough_flags). Tolerates a leading 'run' token."""
    args = list(argv)
    if args and args[0] == "run":
        args = args[1:]
    task: str | None = None
    passthrough: list[str] = []
    for i, a in enumerate(args):
        if a in ("-h", "--help"):
            print(_usage())
            raise SystemExit(0)
        if a.startswith("-"):
            passthrough = args[i:]
            break
        if task is None:
            task = a
        else:
            print(f"[wcb] unexpected extra argument: {a}\n{_usage()}", file=sys.stderr)
            raise SystemExit(2)
    return task, passthrough


def _normalize_task(task: str | None) -> str | None:
    if not task:
        return None
    p = Path(task)
    if not p.is_absolute():
        p = ROOT_DIR / task
    if not p.is_dir():
        print(f"[wcb] task directory not found: {task}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return str(p.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(p.resolve())


def main() -> None:
    task, passthrough = _split_own_argv(sys.argv[1:])
    task = _normalize_task(task)

    isatty = sys.stdout.isatty() and sys.stdin.isatty()
    if not launcher_available() or not isatty:
        reason = "textual is not installed" if not launcher_available() else "not a terminal"
        print(f"[wcb] launcher unavailable ({reason}); "
              f"use eval/run_batch.py with explicit flags instead.", file=sys.stderr)
        raise SystemExit(1)

    config = run_launcher(ROOT_DIR, task)
    if config is None:
        print("[wcb] cancelled — nothing was run.")
        raise SystemExit(0)

    # Docker before anything else: every backend runs the agent in a container,
    # and a stopped daemon does not fail with "start Docker" — `docker image
    # ls -q` returns empty when it cannot reach the daemon, so the harness
    # reports the agent image as missing and tells the operator to `docker load`
    # a 13 GB tar they already have. Someone who picked a task from a dropdown
    # should not have to know that. Starting it here, right after Start, means
    # the wait is visible on the plain terminal before the dashboard takes over
    # the screen.
    from src.utils.docker_daemon import ensure_daemon  # noqa: E402

    status = ensure_daemon(on_progress=lambda msg: print(f"[wcb] {msg}", flush=True))
    if not status.ok:
        print(f"[wcb] {status.detail}", file=sys.stderr)
        raise SystemExit(1)

    # Env next: the auth-provider selection must be in os.environ before
    # run_batch's import-time load_dotenv() (which never overrides existing
    # vars) and before the sidecar/bridge setup reads it. This also carries
    # WCB_AUTH_PROVIDER, which grading.council_members() reads live to restrict
    # the judge roster to the provider chosen in the form.
    os.environ.update(provider_env_overrides(config))
    os.environ["WCB_STREAM"] = "1"
    # Default launcher runs to the full-screen dashboard (logs/tests/rubrics/
    # summary render in-TUI, not on the terminal). setdefault so an explicit
    # WCB_TUI=0 still wins; run_batch's gate still safely falls back to Rich
    # logging when there is no real terminal or textual is absent.
    os.environ.setdefault("WCB_TUI", "1")
    _tui_default = os.environ.get("WCB_TUI", "").strip().lower() in ("1", "true", "yes")

    tasks = config.get("tasks") or ([config["task"]] if config.get("task") else [])

    import run_batch  # noqa: E402  (heavy import deferred until after the form)
    from src.utils.cli_args import build_run_batch_parser  # noqa: E402

    parser = build_run_batch_parser(
        default_model=run_batch.DEFAULT_MODEL,
        default_parallel=run_batch.DEFAULT_PARALLEL,
    )

    # run_batch takes ONE --task, so a multi-task selection loops one run_batch
    # invocation per task. Reps = sequential re-invocations of the same task
    # (run.sh semantics: one run_batch per rep; output dirs auto-increment
    # run_N). Re-parse per rep so one namespace's mutations never leak.
    reps = max(1, int(config.get("reps", 1)))
    multi = len(tasks) > 1

    # Run mode: "auto" = parallel when more than one task is selected, serial
    # otherwise; explicit "parallel"/"serial" force the choice. Parallel only
    # applies across DISTINCT tasks (reps of one task always stay sequential so
    # their run_N dirs increment deterministically).
    run_mode = str(config.get("run_mode", "auto")).strip().lower()
    parallel_tasks = multi and run_mode in ("auto", "parallel")

    per_task_argv = [
        config_to_argv(config, ROOT_DIR, isatty=isatty, task=task) + passthrough
        for task in tasks
    ]

    if parallel_tasks:
        _run_tasks_parallel(tasks, per_task_argv, reps)
        return

    for ti, task in enumerate(tasks, start=1):
        argv = per_task_argv[ti - 1]
        if not _tui_default:
            prefix = f"[wcb] ({ti}/{len(tasks)}) " if multi else "[wcb] "
            print(f"{prefix}starting: run_batch {' '.join(argv)}"
                  + (f"  × {reps} reps" if reps > 1 else ""))
        for rep in range(1, reps + 1):
            if reps > 1:
                print(f"[wcb] ── {task} rep {rep}/{reps} ──")
            run_batch.main(parser.parse_args(argv))


def _run_tasks_parallel(tasks, per_task_argv, reps):
    """Run each selected task concurrently as its own run_batch subprocess.

    A subprocess (not a thread) is used because run_batch.main() hosts a
    single-screen Textual dashboard and mutates process-global state; N of those
    cannot share one terminal, so parallel workers run with WCB_TUI=0 and stream
    plain logs. This matches the harness convergence invariant: each worker is a
    direct ``python3 eval/run_batch.py <argv>`` invocation.
    """
    import concurrent.futures
    import subprocess

    child_env = dict(os.environ)
    child_env["WCB_TUI"] = "0"  # dashboards cannot be multiplexed across workers

    jobs = min(len(tasks), 4)  # cap fan-out (Bedrock throttling / local docker)
    run_batch_py = str(ROOT_DIR / "eval" / "run_batch.py")

    def _run_one(idx):
        task = tasks[idx]
        argv = per_task_argv[idx]
        rc = 0
        for rep in range(1, reps + 1):
            proc = subprocess.run(
                [sys.executable, run_batch_py, *argv],
                cwd=str(ROOT_DIR), env=child_env,
            )
            if proc.returncode:
                rc = proc.returncode
        return task, rc

    print(f"[wcb] running {len(tasks)} tasks in parallel (jobs={jobs}"
          + (f", × {reps} reps each" if reps > 1 else "") + ")")
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_run_one, i): i for i in range(len(tasks))}
        for fut in concurrent.futures.as_completed(futures):
            task, rc = fut.result()
            status = "ok" if rc == 0 else f"FAILED (rc={rc})"
            print(f"[wcb] done: {task} — {status}")
            if rc != 0:
                failures.append(task)
    if failures:
        print(f"[wcb] {len(failures)} task(s) failed: {', '.join(failures)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
