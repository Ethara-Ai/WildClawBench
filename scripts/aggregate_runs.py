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
#   python3 scripts/aggregate_runs.py                       # default ./output
#   python3 scripts/aggregate_runs.py --output-root output  # explicit root
#   python3 scripts/aggregate_runs.py --backend openclaw    # filter backend
#   python3 scripts/aggregate_runs.py --json-only           # no stdout table

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable


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
    # Fallback: derive from `overall_score` (always present, in [0,1]).
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


def _test_weights_pct_from_pass_summary(score_path: Path, run_idx: int) -> float | None:
    ps_path = score_path.parent.parent / "pass_summary.json"
    try:
        data = json.loads(ps_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for r in data.get("per_run", []):
        if r.get("run_index") == run_idx:
            v = r.get("test_weights_percentage")
            if isinstance(v, (int, float)):
                return float(v)
    return None


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
                        continue
                    try:
                        run_idx = int(run_dir.name.split("_", 1)[1])
                    except ValueError:
                        continue
                    yield (b_dir.name, task_dir.name, model_dir.name, run_idx, score_path)


def aggregate(output_root: Path, backend_filter: str | None = None) -> dict:
    per_task_model: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    per_model: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_model_tw: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_model_combined: dict[tuple[str, str], list[float]] = defaultdict(list)

    for backend, task_id, model, run_idx, score_path in _walk_score_files(output_root, backend_filter):
        score = _read_score(score_path)
        if score is None:
            continue
        pct = _pct_from_score(score)
        if pct is None:
            continue
        total, passed, failed = _criteria_counts(score)
        tw_raw = _test_weights_pct_from_pass_summary(score_path, run_idx)
        tw_val = tw_raw if tw_raw is not None else 0.0
        combined_score = round((pct + tw_val) / 2.0, 2)
        entry = {
            "run": run_idx,
            "rubric_weights_percentage": round(pct, 2),
            "test_weights_percentage": round(tw_val, 2),
            "combined_score": combined_score,
            "criteria_total": total,
            "criteria_passed": passed,
            "criteria_failed": failed,
            "score_path": str(score_path),
        }
        per_task_model[(backend, task_id, model)].append(entry)
        per_model[(backend, model)].append(pct)
        per_model_tw[(backend, model)].append(tw_val)
        per_model_combined[(backend, model)].append(combined_score)

    per_model_pass_at_k: dict[tuple[str, str], list[float]] = defaultdict(list)

    summary = {
        "by_task_model": [],
        "by_model": [],
    }
    for (backend, task, model), runs in sorted(per_task_model.items()):
        pcts = [r["rubric_weights_percentage"] for r in runs]
        tw_pcts = [r["test_weights_percentage"] for r in runs]
        combined_scores = [r["combined_score"] for r in runs]
        pass_at_k = max(pcts)
        per_model_pass_at_k[(backend, model)].append(pass_at_k)
        summary["by_task_model"].append({
            "backend": backend,
            "task_id": task,
            "model": model,
            "runs": sorted(runs, key=lambda r: r["run"]),
            "run_count": len(runs),
            "average_rubric_weights_percentage": round(statistics.fmean(pcts), 2),
            "stddev_rubric_weights_percentage": round(statistics.pstdev(pcts), 2) if len(pcts) > 1 else 0.0,
            "average_test_weights_percentage": round(statistics.fmean(tw_pcts), 2),
            "average_combined_score": round(statistics.fmean(combined_scores), 2),
            "pass_at_k": round(pass_at_k, 2),
            "k": len(pcts),
        })
    for (backend, model), pcts in sorted(per_model.items()):
        task_pass_at_k_values = per_model_pass_at_k[(backend, model)]
        tw_pcts = per_model_tw[(backend, model)]
        combined_scores = per_model_combined[(backend, model)]
        summary["by_model"].append({
            "backend": backend,
            "model": model,
            "run_count": len(pcts),
            "task_count": len(task_pass_at_k_values),
            "average_rubric_weights_percentage": round(statistics.fmean(pcts), 2),
            "stddev_rubric_weights_percentage": round(statistics.pstdev(pcts), 2) if len(pcts) > 1 else 0.0,
            "average_test_weights_percentage": round(statistics.fmean(tw_pcts), 2) if tw_pcts else 0.0,
            "average_combined_score": round(statistics.fmean(combined_scores), 2) if combined_scores else 0.0,
            "average_pass_at_k": round(statistics.fmean(task_pass_at_k_values), 2) if task_pass_at_k_values else 0.0,
            "stddev_pass_at_k": round(statistics.pstdev(task_pass_at_k_values), 2) if len(task_pass_at_k_values) > 1 else 0.0,
        })
    return summary


def _print_table(summary: dict) -> None:
    print("\n=== by (backend, task, model): mean and pass@K across K runs ===")
    print(f"{'backend':<12} {'task_id':<48} {'model':<24} {'runs':>5} {'avg%':>8} {'pass@K':>8}")
    print("-" * 110)
    for row in summary["by_task_model"]:
        print(
            f"{row['backend']:<12} {row['task_id'][:48]:<48} {row['model'][:24]:<24} "
            f"{row['run_count']:>5} {row['average_rubric_weights_percentage']:>8.2f} "
            f"{row['pass_at_k']:>8.2f}"
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
