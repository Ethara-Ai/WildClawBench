#!/usr/bin/env python3
"""Promote an interactive (Mode 2) session into a static multi-turn script.

Reads a run's ``turn_timeline.jsonl`` (written by the interactive Mode-2 path)
and emits a ``prompts.txt`` wake-up script (``--- TURN n ---`` blocks, T0-based
— the canonical engine turn-index convention) from the messages actually
DELIVERED to the agent: ``human.message`` records (verbatim, including accepted
scripted suggestions).

An SFT collection session thereby becomes a reproducible static Mode-1 task:
pair the emitted prompts.txt with the source task's assets/mock_data to mint a
permanent Eval/RL task (one HITL session → SFT trajectory + static task).

Usage:
  python3 script/session_to_prompts.py --run output/openclaw/<task>/trajectories/<model>/run_N
  python3 script/session_to_prompts.py --run <run_dir> --out input/<new_task>/prompts.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def turns_from_timeline(timeline_path: Path) -> dict[int, str]:
    """turn_index → delivered message text. human.message wins over the
    scripted stage.message for the same index (the human may have overridden);
    stage.message records carry only lengths, so a scripted turn with no
    human record cannot be reconstructed — flagged by the caller."""
    turns: dict[int, str] = {}
    scripted_only: dict[int, bool] = {}
    for line in timeline_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        idx = rec.get("turn_index")
        if idx is None:
            continue
        if rec.get("type") == "human.message" and rec.get("text") is not None:
            turns[int(idx)] = rec["text"]
            scripted_only.pop(int(idx), None)
        elif rec.get("type") == "stage.message" and int(idx) not in turns:
            scripted_only[int(idx)] = True
    for idx in scripted_only:
        turns.setdefault(idx, "")  # unreconstructable scripted turn
    return turns


def render_prompts_txt(turns: dict[int, str]) -> str:
    blocks = []
    for idx in sorted(turns):
        blocks.append(f"--- TURN {idx} ---\n{turns[idx].rstrip()}\n")
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run_N directory of an interactive session")
    ap.add_argument("--out", default="", help="output prompts.txt path (default: <run>/prompts.txt)")
    args = ap.parse_args()

    run_dir = Path(args.run)
    timeline = run_dir / "turn_timeline.jsonl"
    if not timeline.is_file():
        print(f"error: {timeline} not found (was this an interactive Mode-2 run?)",
              file=sys.stderr)
        return 1

    turns = turns_from_timeline(timeline)
    if not turns:
        print("error: no delivered turns found in the timeline", file=sys.stderr)
        return 1
    missing = [i for i in sorted(turns) if not turns[i].strip()]
    if missing:
        print(f"warning: turns {missing} were scripted-only (no verbatim text "
              "recorded) — fill them in from the source task.py before use",
              file=sys.stderr)

    out = Path(args.out) if args.out else run_dir / "prompts.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_prompts_txt(turns), encoding="utf-8")
    print(f"wrote {out} ({len(turns)} turn(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
