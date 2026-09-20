#!/usr/bin/env python3
"""Backfill / repair pass_summary.json files for already-graded output trees.

Historically `pass_summary.json` aliased its ``tests_*`` keys to the rubric
criteria counts and reported a rubric-only ``average_reward`` — so the real
pytest channel (and the combined reward) were invisible in that file. This
script rebuilds every pass_summary.json under an output root from the canonical
on-disk sources:

  * rubric (Channel B)  -> run_N/score.json
  * pytest (Channel A)  -> run_N/task_output/logs/verifier/ctrf.json
                           (reward.txt as a fallback for the scalar reward)

The rollup itself is NOT implemented here: entry, doc, exclusion and the locked
atomic write all come from ``src/utils/pass_summary.py``, the single
implementation shared with ``eval/run_batch.py`` (the live batch writer),
``script/rebuild_pass_summary.py``, ``script/aggregate_runs.py`` and
``script/regrade.py``. This module is the *bulk repair CLI* over that
implementation, plus the ``_find_model_dirs`` tree walk that only it needs.

Sharing is load-bearing rather than tidy: ``script/run.sh`` runs this script
across the ENTIRE backend output tree after every batch, so any divergence
between this and the live batch writer silently restated every historical
summary under whichever one ran last.

Usage:
    python3 script/backfill_pass_summary.py <output_root> [--backend NAME] [--dry-run]

<output_root> may be any directory; every ``trajectories/<model>/`` folder that
contains run_N subdirectories beneath it is rebuilt. Examples:

    python3 script/backfill_pass_summary.py output
    python3 script/backfill_pass_summary.py 25-JUNE-2026-Night/BATCH_7/temp/output --dry-run
    python3 script/backfill_pass_summary.py output/openclaw/some-task
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This module's public API: script/regrade.py imports _ctrf_test_result and
# write_pass_summary from here.
from src.utils.pass_summary import (  # noqa: E402,F401
    RUN_DIR_RE,
    atomic_write_text,
    ctrf_test_result as _ctrf_test_result,
    finite_float as _finite_float,
    include_incomplete_runs as _include_incomplete_runs,
    include_invalid_runs as _include_invalid_runs,
    load_json_or_none as _load_json,
    locked as _locked,
    mean_or_none as _mean_or_none,
    pass_summary_doc as _doc,
    pass_summary_entry as _entry,
    pass_summary_text as _pass_summary_text,
    rebuild_model_dir,
    run_exclusion_reason as _run_exclusion_reason,
    write_pass_summary,
)

__all__ = [
    "RUN_DIR_RE", "atomic_write_text", "_ctrf_test_result", "_doc", "_entry",
    "_finite_float", "_find_model_dirs", "_include_incomplete_runs",
    "_include_invalid_runs", "_load_json", "_locked", "_mean_or_none",
    "_pass_summary_text", "_run_exclusion_reason", "main", "rebuild_model_dir",
    "write_pass_summary",
]


def _find_model_dirs(root: Path):
    """Yield every <...>/trajectories/<model>/ dir that has run_N children."""
    seen = set()
    for run_dir in root.rglob("run_*"):
        if not run_dir.is_dir() or not RUN_DIR_RE.match(run_dir.name):
            continue
        model_dir = run_dir.parent
        if model_dir.parent.name != "trajectories":
            continue
        if model_dir not in seen:
            seen.add(model_dir)
            yield model_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild pass_summary.json files with real test data + combined reward.")
    ap.add_argument("output_root", help="Directory to scan for trajectories/<model>/run_N folders")
    ap.add_argument("--backend", default=None,
                    help="Only rebuild dirs whose path contains /<backend>/ (e.g. openclaw)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would change without writing")
    args = ap.parse_args()

    root = Path(args.output_root)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    written = 0
    scanned = 0
    for model_dir in _find_model_dirs(root):
        if args.backend and f"/{args.backend}/" not in str(model_dir) + "/":
            continue
        doc = rebuild_model_dir(model_dir)
        if doc is None:
            continue
        scanned += 1
        target = model_dir / "pass_summary.json"
        old = _load_json(target)
        old_avg = (old or {}).get("average_reward")
        new_text = _pass_summary_text(doc)
        tag = "DRY" if args.dry_run else "WROTE"
        print(f"[{tag}] {target}")
        for r in doc["per_run"]:
            print(f"        run {r['run_index']}: rubric={r['rubric_reward']} "
                  f"({r['criteria_passed']}/{r['criteria_total']} criteria)  "
                  f"tests={r['tests_passed']}/{r['tests_total']} (reward={r['test_reward']})  "
                  f"combined={r['combined_reward']}")
        if old_avg is not None and old_avg != doc["average_reward"]:
            print(f"        average_reward: {old_avg} -> {doc['average_reward']}")
        if not args.dry_run:
            atomic_write_text(target, new_text)
            written += 1

    verb = "would rebuild" if args.dry_run else "rebuilt"
    print(f"\n{verb} {scanned if args.dry_run else written} pass_summary.json file(s) under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
