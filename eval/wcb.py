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

    total_units = len(tasks) * reps

    if _tui_default:
        from src.utils.ui.tui import run_with_dashboard, textual_available  # noqa: E402
        from src.utils.ui.events import EV_LOG, EV_PROGRESS, EV_STAGE, get_bus  # noqa: E402
        from src.utils.ui.console import is_interactive as _is_tty  # noqa: E402

        if textual_available() and _is_tty():
            # wcb.py owns the dashboard. Suppress run_batch's own TUI activation
            # so it runs _run_main_body inline (plain-mode). The dashboard wraps
            # ALL tasks × reps in one screen.
            os.environ["WCB_TUI"] = "0"

            def _dashboard_work():
                import threading
                bus = get_bus()
                completed = 0
                _lock = threading.Lock()
                failures = []

                def _stage(slug, state, detail=""):
                    meta = {
                        "running": ("\u25b6", "bold cyan", "running"),
                        "ok": ("\u2713", "bold green", "completed"),
                        "failed": ("\u2717", "bold red", "failed"),
                        "queued": ("\u25cb", "dim", "queued"),
                    }
                    glyph, style, label = meta.get(state, ("\u00b7", "white", state))
                    if detail:
                        label = f"{label} \u2014 {detail}"
                    bus.emit(EV_STAGE, task_id=slug, stage=state,
                             label=label, detail="", glyph=glyph, style=style)

                def _sep(text):
                    bus.emit(EV_LOG, level="INFO",
                             message=f"\u2500\u2500\u2500 {text} \u2500\u2500\u2500")

                if parallel_tasks:
                    import concurrent.futures
                    jobs = min(len(tasks), 4)

                    for t in tasks:
                        _stage(Path(t).name, "queued")

                    def _run_one_task(idx):
                        nonlocal completed
                        task = tasks[idx]
                        slug = Path(task).name
                        argv = per_task_argv[idx]
                        task_failures = 0
                        for rep in range(1, reps + 1):
                            rep_label = f"rep {rep}/{reps}" if reps > 1 else ""
                            display = f"{slug} {rep_label}".strip()
                            _stage(slug, "running", rep_label)
                            _sep(f"{display} started")
                            try:
                                run_batch.main(parser.parse_args(list(argv)))
                            except SystemExit as exc:
                                rc = exc.code if isinstance(exc.code, int) else 1
                                if rc:
                                    task_failures += 1
                                    _stage(slug, "failed", rep_label)
                                    bus.emit(EV_LOG, level="ERROR",
                                             message=f"{display} failed (rc={rc})")
                                else:
                                    _stage(slug, "ok", rep_label)
                                    bus.emit(EV_LOG, level="INFO", message=f"{display} ok")
                            except Exception as exc:
                                task_failures += 1
                                _stage(slug, "failed", rep_label)
                                bus.emit(EV_LOG, level="ERROR",
                                         message=f"{display} error: {exc}")
                            else:
                                _stage(slug, "ok", rep_label)
                                bus.emit(EV_LOG, level="INFO", message=f"{display} ok")
                            with _lock:
                                completed += 1
                                bus.emit(EV_PROGRESS, completed=completed, total=total_units)
                        return task, task_failures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
                        futs = {pool.submit(_run_one_task, i): i for i in range(len(tasks))}
                        for fut in concurrent.futures.as_completed(futs):
                            task, fails = fut.result()
                            if fails:
                                failures.append(task)
                else:
                    for ti, task in enumerate(tasks):
                        slug = Path(task).name
                        argv = per_task_argv[ti]
                        for rep in range(1, reps + 1):
                            rep_label = f"rep {rep}/{reps}" if reps > 1 else ""
                            display = (f"({ti+1}/{len(tasks)}) {slug} {rep_label}".strip()
                                       if multi else f"{slug} {rep_label}".strip())
                            _stage(slug, "running", rep_label)
                            _sep(f"{display} started")
                            try:
                                run_batch.main(parser.parse_args(list(argv)))
                            except SystemExit as exc:
                                rc = exc.code if isinstance(exc.code, int) else 1
                                if rc:
                                    failures.append(task)
                                    _stage(slug, "failed", rep_label)
                                    bus.emit(EV_LOG, level="ERROR",
                                             message=f"{display} failed (rc={rc})")
                                else:
                                    _stage(slug, "ok", rep_label)
                                    bus.emit(EV_LOG, level="INFO", message=f"{display} ok")
                            except Exception as exc:
                                failures.append(task)
                                _stage(slug, "failed", rep_label)
                                bus.emit(EV_LOG, level="ERROR",
                                         message=f"{display} error: {exc}")
                            else:
                                _stage(slug, "ok", rep_label)
                                bus.emit(EV_LOG, level="INFO", message=f"{display} ok")
                            completed += 1
                            bus.emit(EV_PROGRESS, completed=completed, total=total_units)

                if failures:
                    bus.emit(EV_LOG, level="WARNING",
                             message=f"{len(set(failures))} task(s) had failures")
                    raise SystemExit(1)

            run_with_dashboard(_dashboard_work, total_hint=total_units)
            return

    # Fallback: no TUI (piped, WCB_TUI=0, textual absent). Plain-mode execution.
    if parallel_tasks:
        _run_tasks_parallel(tasks, per_task_argv, reps, run_batch, parser)
        return

    serial_failures: list[str] = []
    for ti, task in enumerate(tasks, start=1):
        argv = per_task_argv[ti - 1]
        prefix = f"[wcb] ({ti}/{len(tasks)}) " if multi else "[wcb] "
        print(f"{prefix}starting: run_batch {' '.join(argv)}"
              + (f"  x {reps} reps" if reps > 1 else ""))
        for rep in range(1, reps + 1):
            if reps > 1:
                print(f"[wcb] ── {task} rep {rep}/{reps} ──")
            try:
                run_batch.main(parser.parse_args(list(argv)))
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
                if rc:
                    serial_failures.append(task)
                    print(f"[wcb] {task} rep {rep}/{reps} failed (rc={rc})")
            else:
                if reps > 1:
                    print(f"[wcb] {task} rep {rep}/{reps} ok")

    if serial_failures:
        print(f"[wcb] {len(set(serial_failures))}/{len(tasks)} task(s) had failures")
        raise SystemExit(1)


def _run_tasks_parallel(tasks, per_task_argv, reps, run_batch, parser):
    """Plain-mode parallel execution (no TUI). Uses threads since run_batch is
    in-process and WCB_TUI=0 prevents any Textual activation."""
    import concurrent.futures
    import threading

    jobs = min(len(tasks), 4)
    print_lock = threading.Lock()

    def _log(msg):
        with print_lock:
            print(msg, flush=True)

    def _run_one(idx):
        task = tasks[idx]
        slug = Path(task).name
        argv = per_task_argv[idx]
        task_failures = 0
        for rep in range(1, reps + 1):
            label = f"[{slug}] rep {rep}/{reps}" if reps > 1 else f"[{slug}]"
            _log(f"[wcb] {label} started")
            try:
                run_batch.main(parser.parse_args(list(argv)))
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
                if rc:
                    task_failures += 1
                    _log(f"[wcb] {label} FAILED (rc={rc})")
            except Exception as exc:
                task_failures += 1
                _log(f"[wcb] {label} ERROR: {exc}")
            else:
                _log(f"[wcb] {label} ok")
        return task, task_failures

    print(f"[wcb] running {len(tasks)} tasks in parallel (jobs={jobs}"
          + (f", x {reps} reps each" if reps > 1 else "") + ")")
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(_run_one, i): i for i in range(len(tasks))}
        for fut in concurrent.futures.as_completed(futs):
            task, fails = fut.result()
            if fails:
                failures.append(task)
                _log(f"[wcb] DONE: {task} — {fails}/{reps} rep(s) failed")
            else:
                _log(f"[wcb] DONE: {task} — ok")
    if failures:
        print(f"[wcb] {len(failures)}/{len(tasks)} task(s) had failures: {', '.join(failures)}")
        raise SystemExit(1)
    print(f"[wcb] all {len(tasks)} tasks completed successfully")


if __name__ == "__main__":
    main()
