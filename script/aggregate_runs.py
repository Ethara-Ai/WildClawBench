#!/usr/bin/env python3
# Aggregates `score.json` files produced by `eval/run_batch.py` into a
# per-(model, task) and per-model rollup.  Implements line 3 of the user's
# m1420 reward formula: average_rubric_weights_percentage = mean over runs.
# Honors the b71 deprecated-alias fallback (legacy files with only `tests_*`
# keys still aggregate correctly).  Reads `rubric_weights_percentage` when
# present, else derives `overall_score * 100`.
#
# Layout assumed (matches `eval/run_batch.py:_write_pass_summary`):
#   output/<backend>/<task_id>/trajectories/<model>/run_<N>/score.json
#
# Usage:
#   python3 script/aggregate_runs.py                       # default ./output
#   python3 script/aggregate_runs.py --output-root output  # explicit root
#   python3 script/aggregate_runs.py --backend openclaw    # filter backend
#   python3 script/aggregate_runs.py --json-only           # no stdout table

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# THE shared exclusion predicate, not a hand-synced mirror of it. This reader
# feeds raw score.json payloads straight in, so it is the site where a
# HISTORICAL dead-judge score (written before grading.write_score's failure
# gate existed: `error` / all-criteria-abstained, but no `grading_status` key)
# used to slip through as a genuine 0.0 and deflate the rollup.
from src.utils.pass_summary import (  # noqa: E402,F401
    include_incomplete_runs as _include_incomplete_runs,
    include_invalid_runs as _include_invalid_runs,
    run_exclusion_reason as _run_exclusion_reason,
)

__all__ = [
    "_criteria_counts", "_include_incomplete_runs", "_include_invalid_runs",
    "_pct_from_score", "_print_table", "_read_score", "_run_exclusion_reason",
    "_walk_score_files", "aggregate", "main",
]


def _read_score(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _pct_from_score(score: dict) -> float | None:
    # Canonical (b71): `rubric_weights_percentage` is the user m1420 ×100 value.
    pct = score.get("rubric_weights_percentage")
    if isinstance(pct, (int, float)):
        return float(pct)
    # Fallback: derive from `overall_score` (always present; signed/unclamped
    # since 2026-07 — may be negative when triggered negative-weight rubric
    # criteria outweigh positives, byte-aligned with Channel A pytest reward).
    overall = score.get("overall_score")
    if isinstance(overall, (int, float)):
        return float(overall) * 100.0
    return None


def _criteria_counts(score: dict) -> tuple[int, int, int]:
    # Canonical (b71) wins; `tests_*` is deprecated alias kept for back-compat.
    total = score.get("criteria_total", score.get("tests_total", 0)) or 0
    passed = score.get("criteria_passed", score.get("tests_passed", 0)) or 0
    failed = score.get("criteria_failed", score.get("tests_failed", 0)) or 0
    return int(total), int(passed), int(failed)


def _walk_score_files(output_root: Path, backend_filter: str | None) -> Iterable[tuple[str, str, str, int, Path]]:
    # Yields (backend, task_id, model, run_index, score_path).  Path layout per
    # `_write_pass_summary` in eval/run_batch.py.
    if not output_root.is_dir():
        return
    backends = [d for d in output_root.iterdir() if d.is_dir()]
    if backend_filter:
        backends = [d for d in backends if d.name == backend_filter]
    for b_dir in backends:
        for task_dir in b_dir.iterdir():
            if not task_dir.is_dir():
                continue
            traj_root = task_dir / "trajectories"
            if not traj_root.is_dir():
                continue
            for model_dir in traj_root.iterdir():
                if not model_dir.is_dir():
                    continue
                for run_dir in model_dir.iterdir():
                    if not run_dir.is_dir() or not run_dir.name.startswith("run_"):
                        continue
                    score_path = run_dir / "score.json"
                    if not score_path.is_file():
                        # A run whose judge died wholly has score.failed.json
                        # and no score.json. Yield it anyway: `continue` here
                        # made the run VANISH from the rollup with no counter,
                        # so a batch could lose runs and still look complete.
                        # _run_exclusion_reason routes it to `runs_ungraded`.
                        failed_path = run_dir / "score.failed.json"
                        if not failed_path.is_file():
                            continue
                        score_path = failed_path
                    try:
                        run_idx = int(run_dir.name.split("_", 1)[1])
                    except ValueError:
                        continue
                    yield (b_dir.name, task_dir.name, model_dir.name, run_idx, score_path)


def aggregate(output_root: Path, backend_filter: str | None = None) -> dict:
    # per_task_model[(backend, task, model)] = list of {run, pct, total, passed, failed}
    per_task_model: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    per_model: dict[tuple[str, str], list[float]] = defaultdict(list)

    excluded_incomplete: dict[tuple[str, str, str], int] = defaultdict(int)
    # Tracked apart from excluded_incomplete so "the judge never produced a
    # number" is not silently filed under "the run was invalid" — they need
    # different operator responses (re-judge vs re-run).
    ungraded: dict[tuple[str, str, str], int] = defaultdict(int)
    for backend, task_id, model, run_idx, score_path in _walk_score_files(output_root, backend_filter):
        score = _read_score(score_path)
        if score is None:
            continue
        _reason = _run_exclusion_reason(score)
        if _reason == "ungraded":
            ungraded[(backend, task_id, model)] += 1
            continue
        if _reason is not None:
            excluded_incomplete[(backend, task_id, model)] += 1
            continue
        pct = _pct_from_score(score)
        if pct is None:
            continue
        total, passed, failed = _criteria_counts(score)
        entry = {
            "run": run_idx,
            "rubric_weights_percentage": round(pct, 2),
            "criteria_total": total,
            "criteria_passed": passed,
            "criteria_failed": failed,
            "score_path": str(score_path),
        }
        if score.get("turns_duplicated"):
            entry["turns_duplicated"] = list(score["turns_duplicated"])
        per_task_model[(backend, task_id, model)].append(entry)
        per_model[(backend, model)].append(pct)

    # Per (backend, model): collect per-task pass@K values for eval-aggregate
    # rollup per walkthrough §4: 'eval-aggregate = mean of per-task pass@K values'.
    # Distinct from per_model[(backend, model)] which is mean of ALL runs (user
    # m1420 line 3). pass@K rewards the model for ever having succeeded; mean
    # rewards consistency. Both are reported; neither replaces the other.
    per_model_pass_at_k: dict[tuple[str, str], list[float]] = defaultdict(list)

    summary = {
        "by_task_model": [],
        "by_model": [],
    }
    for (backend, task, model), runs in sorted(per_task_model.items()):
        pcts = [r["rubric_weights_percentage"] for r in runs]
        pass_at_k = max(pcts)
        per_model_pass_at_k[(backend, model)].append(pass_at_k)
        task_entry = {
            "backend": backend,
            "task_id": task,
            "model": model,
            "runs": sorted(runs, key=lambda r: r["run"]),
            "run_count": len(runs),
            "average_rubric_weights_percentage": round(statistics.fmean(pcts), 2),
            "stddev_rubric_weights_percentage": round(statistics.pstdev(pcts), 2) if len(pcts) > 1 else 0.0,
            # Walkthrough §4 pass@K: best-of-K rollout per task. K = run_count.
            "pass_at_k": round(pass_at_k, 2),
            "k": len(pcts),
        }
        n_excl = excluded_incomplete.get((backend, task, model), 0)
        if n_excl:
            task_entry["runs_excluded_incomplete"] = n_excl
        n_ungraded = ungraded.get((backend, task, model), 0)
        if n_ungraded:
            task_entry["runs_ungraded"] = n_ungraded
        summary["by_task_model"].append(task_entry)
    # A task whose every run was excluded must not silently disappear.
    for key in sorted(set(excluded_incomplete) | set(ungraded)):
        backend, task, model = key
        if key not in per_task_model:
            entry = {
                "backend": backend,
                "task_id": task,
                "model": model,
                "runs": [],
                "run_count": 0,
            }
            if excluded_incomplete.get(key):
                entry["runs_excluded_incomplete"] = excluded_incomplete[key]
            if ungraded.get(key):
                entry["runs_ungraded"] = ungraded[key]
            summary["by_task_model"].append(entry)
    for (backend, model), pcts in sorted(per_model.items()):
        task_pass_at_k_values = per_model_pass_at_k[(backend, model)]
        summary["by_model"].append({
            "backend": backend,
            "model": model,
            "run_count": len(pcts),
            "task_count": len(task_pass_at_k_values),
            # User formula m1420 line 3: mean over ALL runs of this model.
            "average_rubric_weights_percentage": round(statistics.fmean(pcts), 2),
            "stddev_rubric_weights_percentage": round(statistics.pstdev(pcts), 2) if len(pcts) > 1 else 0.0,
            # Walkthrough §4 eval-aggregate: mean of per-task pass@K values.
            # Each task contributes its best run; this is the headline 'how good
            # is this model when it tries' number, complementary to the typical
            # 'how good on average' average_rubric_weights_percentage.
            "average_pass_at_k": round(statistics.fmean(task_pass_at_k_values), 2) if task_pass_at_k_values else 0.0,
            "stddev_pass_at_k": round(statistics.pstdev(task_pass_at_k_values), 2) if len(task_pass_at_k_values) > 1 else 0.0,
        })
    return summary


def _print_table(summary: dict) -> None:
    print("\n=== by (backend, task, model): mean and pass@K across K runs ===")
    print(f"{'backend':<12} {'task_id':<48} {'model':<24} {'runs':>5} {'avg%':>8} {'pass@K':>8}")
    print("-" * 110)
    for row in summary["by_task_model"]:
        # A row whose every run was excluded or ungraded carries no averages at
        # all — indexing them raised KeyError and took the whole rollup down
        # with it. Render the state instead: a dash is honest, 0.00 is a lie.
        avg = row.get("average_rubric_weights_percentage")
        patk = row.get("pass_at_k")
        avg_s = f"{avg:>8.2f}" if isinstance(avg, (int, float)) else f"{'—':>8}"
        patk_s = f"{patk:>8.2f}" if isinstance(patk, (int, float)) else f"{'—':>8}"
        note = ""
        if row.get("runs_ungraded"):
            note = f"  UNGRADED x{row['runs_ungraded']} (judge failed)"
        elif row.get("runs_excluded_incomplete"):
            note = f"  excluded x{row['runs_excluded_incomplete']}"
        print(
            f"{row['backend']:<12} {row['task_id'][:48]:<48} {row['model'][:24]:<24} "
            f"{row['run_count']:>5} {avg_s} {patk_s}{note}"
        )

    print("\n=== by (backend, model): mean of runs and mean of per-task pass@K ===")
    print(f"{'backend':<12} {'model':<32} {'runs':>5} {'tasks':>6} {'avg_runs%':>10} {'avg_pass@K':>11}")
    print("-" * 92)
    for row in summary["by_model"]:
        print(
            f"{row['backend']:<12} {row['model'][:32]:<32} {row['run_count']:>5} "
            f"{row['task_count']:>6} {row['average_rubric_weights_percentage']:>10.2f} "
            f"{row['average_pass_at_k']:>11.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate run_batch.py score.json files by (model, task).")
    parser.add_argument("--output-root", default="output", help="Path to harness output directory (default: ./output)")
    parser.add_argument("--backend", default=None, help="Filter to a single backend dir name (e.g. openclaw)")
    parser.add_argument("--write", default=None, help="Write summary JSON to this path (default: <output-root>/<backend|all>_aggregate_summary.json)")
    parser.add_argument("--json-only", action="store_true", help="Suppress stdout table, only write JSON")
    args = parser.parse_args()

    output_root = Path(args.output_root).resolve()
    summary = aggregate(output_root, backend_filter=args.backend)

    if args.write:
        out_path = Path(args.write).resolve()
    else:
        tag = args.backend if args.backend else "all"
        out_path = output_root / f"{tag}_aggregate_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if not args.json_only:
        _print_table(summary)
        print(f"\nWrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
