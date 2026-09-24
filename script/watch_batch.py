#!/usr/bin/env python3
"""Poll a running batch's `output/` tree and log state-change events.

Reads the artifacts `eval/run_batch.py` / `src/agents/openclaw/runner.py`
already write (`harness_debug.log`, `score.json`, `score.failed.json`) — it
does not hook the harness in any way, so it is zero-risk to attach to a batch
that is already running (including on gama's rsynced tree, no restart
needed).

Usage:
  nohup python3 script/watch_batch.py --interval 30 > logs/watch.out 2>&1 &
  python3 script/watch_batch.py --once
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TURN_RE = re.compile(r"Agent turn (\d+)/(\d+) starting")
STALLED_RE = re.compile(r"STALLED")
GATEWAY_RESTART_RE = re.compile(r"stall-guard: gateway")
RUN_INCOMPLETE_RE = re.compile(r"RUN INCOMPLETE: (\d+) of (\d+)")
ABORTING_RE = re.compile(r"ABORTING RUN: (.*)")
GIVE_UP_RE = re.compile(r"Agent turn \d+ (timed out|stalled twice)")

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_log_text(log_path: Path) -> str:
    # Whole-file read, not a head/tail window: an aborted run keeps logging
    # (grading-adjacent output, retries) AFTER its "ABORTING RUN:"/"timed
    # out"/"stalled twice" line, which can push that line out of a
    # fixed-size tail window on a multi-MB log — a real miss seen against
    # gama's artifacts. Simplicity over a byte-offset cache; logs here are
    # MBs, not GBs.
    return log_path.read_text(errors="ignore")


def _last_incomplete_reason(text: str):
    # Union of the two "why did this run give up" markers, LAST occurrence
    # by position — a run can retry (stall) before ultimately timing out or
    # aborting, so only the final one is the true reason.
    candidates = [(m.start(), m.group(1)) for m in ABORTING_RE.finditer(text)]
    candidates += [(m.start(), m.group(1)) for m in GIVE_UP_RE.finditer(text)]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]


def _find_run_dirs(root: Path):
    # output/<backend>/<task_id>/trajectories/<model>/run_<N>/
    output_root = root / "output"
    if not output_root.is_dir():
        return
    for backend_dir in sorted(output_root.iterdir()):
        if not backend_dir.is_dir():
            continue
        for task_dir in sorted(backend_dir.iterdir()):
            traj_root = task_dir / "trajectories"
            if not traj_root.is_dir():
                continue
            for model_dir in sorted(traj_root.iterdir()):
                if not model_dir.is_dir():
                    continue
                for run_dir in sorted(model_dir.iterdir()):
                    if run_dir.is_dir() and run_dir.name.startswith("run_"):
                        yield task_dir.name, run_dir


def _grading_failure_reason(scores: dict):
    # Minimal local mirror of src.utils.grading._grading_failure_reason —
    # only the two fields this watcher needs, so it stays a stdlib-only,
    # zero-import read of an artifact the harness already wrote.
    if not isinstance(scores, dict):
        return None
    error = scores.get("error")
    if error:
        return str(error)
    total = int(scores.get("criteria_total") or 0)
    abstained = int(scores.get("criteria_abstained") or 0)
    if total > 0 and abstained == total:
        return f"all {total} criteria abstained (no judge verdict survived)"
    return None


def scan(root: Path, state: dict) -> list:
    """Scan `root` for run-dir state changes and return newly emitted events.

    `state` maps run_dir path (str) -> per-run dict, mutated in place so
    repeated calls only report deltas. Terminal states (completed/incomplete/
    ungraded) are recorded once and never re-emitted.
    """
    events = []
    for task_id, run_dir in _find_run_dirs(root):
        key = str(run_dir)
        rec = state.setdefault(key, {
            "task": task_id, "run": run_dir.name, "turn": 0,
            "turns_planned": None, "stall_count": 0, "gateway_restarts": 0,
            "launched": False, "terminal": False,
        })
        if rec["terminal"]:
            continue

        log_path = run_dir / "harness_debug.log"
        if not log_path.is_file():
            continue

        try:
            text = _read_log_text(log_path)
        except OSError:
            continue

        # Terminal checks come FIRST: a run whose harness_debug.log we are
        # seeing for the very first time can already be finished (e.g. the
        # watcher started after the batch did), and such a run should only
        # ever emit its terminal event — not a "launched" that immediately
        # contradicts it.
        #
        # score.failed.json wins outright, ahead of score.json and the log:
        # a dead judge is ungraded no matter what turns_* fields the failure
        # payload happens to carry, and no matter what a stale/leftover
        # score.json next to it might say (grading.write_score enforces
        # mutual exclusion going forward, but a watcher reading someone
        # else's tree should not assume every historical writer did).
        score_path = run_dir / "score.json"
        failed_path = run_dir / "score.failed.json"

        if failed_path.is_file():
            try:
                failed_score = json.loads(failed_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                failed_score = {}
            reason = _grading_failure_reason(failed_score) or failed_score.get("error")
            if reason:
                reason = str(reason)[:120]
            rec["terminal"] = True
            rec["terminal_kind"] = "ungraded"
            events.append({"ts": _now_iso(), "event": "ungraded",
                            "task": task_id, "run": run_dir.name,
                            "reason": reason,
                            "turns_completed": failed_score.get("turns_completed"),
                            "turns_planned": failed_score.get("turns_planned")})
            continue

        if score_path.is_file():
            try:
                score = json.loads(score_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                score = None
            if isinstance(score, dict):
                if score.get("run_incomplete"):
                    reason = _last_incomplete_reason(text)
                    if reason is None:
                        m = RUN_INCOMPLETE_RE.search(text)
                        if m:
                            reason = f"{m.group(1)} of {m.group(2)} turns"
                    rec["terminal"] = True
                    rec["terminal_kind"] = "incomplete"
                    events.append({
                        "ts": _now_iso(), "event": "incomplete",
                        "task": task_id, "run": run_dir.name,
                        "turns_completed": score.get("turns_completed"),
                        "turns_planned": score.get("turns_planned"),
                        "reason": reason,
                    })
                else:
                    rec["terminal"] = True
                    rec["terminal_kind"] = "completed"
                    events.append({
                        "ts": _now_iso(), "event": "completed",
                        "task": task_id, "run": run_dir.name,
                        "score": score.get("overall_score"),
                        "abstained": score.get("criteria_abstained"),
                        "criteria_total": score.get("criteria_total"),
                        "injection_ok": score.get("injection_ok"),
                    })
            continue

        incomplete_m = RUN_INCOMPLETE_RE.search(text)
        if incomplete_m:
            reason = _last_incomplete_reason(text) or (
                f"{incomplete_m.group(1)} of {incomplete_m.group(2)} turns")
            rec["terminal"] = True
            rec["terminal_kind"] = "incomplete"
            events.append({
                "ts": _now_iso(), "event": "incomplete",
                "task": task_id, "run": run_dir.name,
                "turns_completed": int(incomplete_m.group(1)),
                "turns_planned": int(incomplete_m.group(2)),
                "reason": reason,
            })
            continue

        if not rec["launched"]:
            rec["launched"] = True
            events.append({"ts": _now_iso(), "event": "launched",
                            "task": task_id, "run": run_dir.name})

        turn_matches = TURN_RE.findall(text)
        if turn_matches:
            turn, planned = (int(x) for x in turn_matches[-1])
            if turn != rec["turn"]:
                rec["turn"] = turn
                rec["turns_planned"] = planned
                events.append({"ts": _now_iso(), "event": "turn",
                                "task": task_id, "run": run_dir.name,
                                "turn": turn, "turns_planned": planned})

        stall_count = len(STALLED_RE.findall(text))
        if stall_count > rec["stall_count"]:
            rec["stall_count"] = stall_count
            events.append({"ts": _now_iso(), "event": "stalled",
                            "task": task_id, "run": run_dir.name})

        restart_count = len(GATEWAY_RESTART_RE.findall(text))
        if restart_count > rec["gateway_restarts"]:
            rec["gateway_restarts"] = restart_count
            events.append({"ts": _now_iso(), "event": "gateway_restart",
                            "task": task_id, "run": run_dir.name})

    return events


def _summarize(state: dict) -> str:
    counts = {"launched": 0, "running": 0, "completed": 0,
              "incomplete": 0, "ungraded": 0}
    running = []
    for rec in state.values():
        if rec["terminal"]:
            counts["launched"] += 1
            kind = rec.get("terminal_kind", "completed")
            counts[kind] = counts.get(kind, 0) + 1
        elif rec["launched"]:
            counts["launched"] += 1
            counts["running"] += 1
            running.append(f"{rec['task']}@{rec['turn']}/{rec['turns_planned'] or '?'}")
    running_str = ", ".join(running[:8])
    if len(running) > 8:
        running_str += f", +{len(running) - 8} more"
    ts = datetime.now().strftime("%H:%M")
    line = (f"{ts}  launched={counts['launched']} running={counts['running']} "
            f"completed={counts['completed']} incomplete={counts['incomplete']} "
            f"ungraded={counts['ungraded']}")
    if running_str:
        line += f"  | running: {running_str}"
    return line


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="batch root (contains output/)")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between scans")
    parser.add_argument("--log", default=None, help="jsonl log path (default <root>/logs/batch_progress.jsonl)")
    parser.add_argument("--once", action="store_true", help="scan once, print summary, exit")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    log_path = Path(args.log) if args.log else root / "logs" / "batch_progress.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    state: dict = {}

    def _tick():
        events = scan(root, state)
        if events:
            with log_path.open("a", encoding="utf-8") as fh:
                for ev in events:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print(_summarize(state))
        sys.stdout.flush()

    if args.once:
        _tick()
        return

    try:
        while True:
            _tick()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
