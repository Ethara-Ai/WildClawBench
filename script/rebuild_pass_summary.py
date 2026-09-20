#!/usr/bin/env python3
"""Rebuild pass_summary.json from run_N/ artifacts under a trajectories/<model>/ dir.

Point it at a trajectories/<model>/ folder that already contains 1..N run_N/
sub-dirs and it emits pass_summary_new.json with the exact same shape the
harness would have written if all N reps had run in a single batch.

USAGE

    python3 script/rebuild_pass_summary.py TRAJECTORIES_DIR [-o OUTPUT]
    python3 script/rebuild_pass_summary.py TRAJECTORIES_DIR --in-place

Where TRAJECTORIES_DIR is something like:
    output/openclaw/<task>/trajectories/<model>/
or:
    /path/to/trajectories/claude/

The script auto-discovers run_1, run_2, ..., run_N and rebuilds the summary
from the per-rep score.json + ctrf.json + reward.txt artifacts. It does NOT
touch existing pass_summary.json unless you pass --in-place.

WHY

Manual merging of two pass_summary.json files (1-rep verification + N-rep
bulk) proved unreliable across schema versions -- fields end up as null when
a rich-schema field is renamed or a decimal reward hasn't been converted to
a percent. Recomputing from the per-run artifacts sidesteps every
schema-crossover problem: the artifacts are the source of truth the harness
itself reads.

RELATIONSHIP TO backfill_pass_summary.py

This was once a hand-written re-port of the harness pipeline and had drifted
from it — most visibly in the scalar-reward precedence, where it read
reward.txt BEFORE ctrf's summary.overall_score while the production repair
writer read them the other way round. That divergence was vestigial rather
than deliberate: both artifacts are written from the same in-memory
``te['reward']`` (run_batch.py writes ``reward.txt`` at 6dp and hands the same
value to ``harbor.ctrf.build_ctrf``, which rounds it to 4dp), so the two can
only ever disagree in precision, never in meaning; and nothing outside the test
suite imported this module, whereas backfill is wired into ``script/run.sh``,
``deliver.sh`` and ``script/regrade.py``. Both now call the one implementation
in ``src/utils/pass_summary.py``, so this CLI and backfill produce identical
bytes for identical inputs. What is left here is only the CLI surface: the
``-o`` / ``--in-place`` / ``--indent`` options backfill does not offer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This module's public API, under its historical names.
from src.utils.pass_summary import (  # noqa: E402,F401
    RUN_DIR_RE as _RUN_DIR_RE,
    atomic_write_text,
    ctrf_test_result as _read_ctrf_summary,
    discover_run_dirs,
    finite_float as _finite_float,
    include_incomplete_runs as _include_incomplete_runs,
    include_invalid_runs as _include_invalid_runs,
    load_json_or_none as _load_json,
    mean_or_none as _mean_or_none,
    pass_summary_doc as _pass_summary_doc,
    pass_summary_entry as _pass_summary_entry,
    rebuild_model_dir,
    run_exclusion_reason as _run_exclusion_reason,
)

__all__ = [
    "_RUN_DIR_RE", "atomic_write_text", "_finite_float",
    "_include_incomplete_runs", "_include_invalid_runs", "_load_json",
    "_mean_or_none", "_pass_summary_doc", "_pass_summary_entry",
    "_read_ctrf_summary", "_run_exclusion_reason", "discover_run_dirs", "main",
    "rebuild", "rebuild_model_dir",
]


def rebuild(model_dir: Path, model_type: str | None = None) -> dict:
    """Compute pass_summary contents from run_N/ artifacts under model_dir."""
    doc = rebuild_model_dir(model_dir, model_type)
    if doc is None:
        sys.stderr.write(f"error: no run_N/ dirs under {model_dir}\n")
        sys.exit(2)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Rebuild pass_summary.json from run_N/ artifacts under a "
            "trajectories/<model>/ dir. Byte-equivalent to what the harness "
            "would have written."
        )
    )
    ap.add_argument(
        "model_dir",
        type=Path,
        help="Path to trajectories/<model>/ containing run_1, run_2, ..., run_N/",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Override the 'model' field. Defaults to the model_dir basename.",
    )
    out = ap.add_mutually_exclusive_group()
    out.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help=(
            "Output path. Use '-' for stdout. Default: write "
            "'pass_summary_new.json' next to the existing pass_summary.json."
        ),
    )
    out.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite <model_dir>/pass_summary.json with the recomputed doc.",
    )
    ap.add_argument("--indent", type=int, default=2, help="JSON indent (default: 2)")
    args = ap.parse_args()

    if not args.model_dir.is_dir():
        ap.error(f"model_dir is not a directory: {args.model_dir}")

    doc = rebuild(args.model_dir, model_type=args.model)
    text = json.dumps(doc, indent=args.indent)

    if args.in_place:
        dst = args.model_dir / "pass_summary.json"
        atomic_write_text(dst, text)
        sys.stderr.write(f"wrote {doc['runs']} rep(s) → {dst}\n")
        return 0
    if args.output == "-":
        sys.stdout.write(text)
        return 0
    dst = Path(args.output) if args.output else (args.model_dir / "pass_summary_new.json")
    atomic_write_text(dst, text)
    sys.stderr.write(f"wrote {doc['runs']} rep(s) → {dst}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
