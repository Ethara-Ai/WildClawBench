#!/usr/bin/env python3
"""Verify repackaged bundles match their source tasks (no Docker, no LLM).

For every `<dest-root>/<task>/` that has a matching `<input-root>/<task>/`, assert:
  1. bundle `prompts.txt` is byte-identical to the source `prompts.txt`
     (falls back to comparing against the source `prompt.txt` for single-turn tasks);
  2. the back-compat `prompt.txt` copy equals the bundle `prompts.txt`;
  3. the bundle `inject/` tree is byte-identical to the source `inject/`;
  4. no regression — `rubric.json`, `data/`, `trajectories/` are still present.

Bundles with no matching input dir (e.g. legacy reference bundles) are skipped.

Usage:
    python3 scripts/verify_bundles.py                       # defaults below
    python3 scripts/verify_bundles.py --dest-root output_bundle --input-root input
    python3 scripts/verify_bundles.py --task "Jae chandler (trj)"

Exit code 0 = all checks pass, 1 = at least one failure (CI-friendly).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(d: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under `d`."""
    return {str(p.relative_to(d)): _sha(p) for p in d.rglob("*") if p.is_file()}


def verify_bundle(bundle: Path, src: Path) -> list[str]:
    """Return a list of human-readable result lines; a line starting with '!' is a failure."""
    out: list[str] = []

    # 1) prompt content: prefer source prompts.txt, else source prompt.txt.
    src_prompt = src / "prompts.txt"
    if not src_prompt.is_file():
        src_prompt = src / "prompt.txt"
    bundle_prompts = bundle / "prompts.txt"
    if not src_prompt.is_file():
        out.append("  prompt: source has neither prompts.txt nor prompt.txt — skip")
    elif not bundle_prompts.is_file():
        out.append("! BUNDLE MISSING prompts.txt")
    elif _sha(src_prompt) != _sha(bundle_prompts):
        out.append("! prompts.txt CONTENT DIFFERS from source")
    else:
        out.append(f"  prompts.txt OK ({len(bundle_prompts.read_text(errors='ignore').splitlines())} lines)")

    # 2) back-compat prompt.txt copy.
    bp, bpp = bundle / "prompt.txt", bundle / "prompts.txt"
    if bpp.is_file():
        if not bp.is_file():
            out.append("! prompt.txt back-compat copy missing")
        elif _sha(bp) != _sha(bpp):
            out.append("! prompt.txt differs from prompts.txt")
        else:
            out.append("  prompt.txt OK")

    # 3) inject/ tree identical to source.
    si, bi = src / "inject", bundle / "inject"
    if si.is_dir():
        if not bi.is_dir():
            out.append("! BUNDLE MISSING inject/")
        else:
            st, bt = _tree(si), _tree(bi)
            if st == bt:
                out.append(f"  inject/ OK ({len(st)} files identical)")
            else:
                missing = sorted(set(st) - set(bt))
                changed = sorted(k for k in st.keys() & bt.keys() if st[k] != bt[k])
                out.append(f"! inject/ MISMATCH (missing={missing[:3]} changed={changed[:3]})")
    else:
        out.append("  inject/ — source has none, nothing to check")

    # 4) regression guard.
    for need in ("rubric.json", "data", "trajectories"):
        if not (bundle / need).exists():
            out.append(f"! REGRESSION: missing {need}")

    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest-root", default="output_bundle", help="Repackaged bundles dir (default: output_bundle)")
    ap.add_argument("--input-root", default="input", help="Source task dir (default: input)")
    ap.add_argument("--task", default=None, help="Verify only this bundle name (default: all)")
    args = ap.parse_args(argv)

    dest = (ROOT_DIR / args.dest_root) if not Path(args.dest_root).is_absolute() else Path(args.dest_root)
    inp = (ROOT_DIR / args.input_root) if not Path(args.input_root).is_absolute() else Path(args.input_root)
    if not dest.is_dir():
        print(f"dest-root not found: {dest}", file=sys.stderr)
        return 2

    bundles = [dest / args.task] if args.task else sorted(p for p in dest.iterdir() if p.is_dir())
    failures = 0
    checked = 0
    for b in bundles:
        if not b.is_dir():
            print(f"• {b.name}: not a directory — skip")
            continue
        src = inp / b.name
        if not src.is_dir():
            print(f"• {b.name}: no input source (reference bundle) — skip")
            continue
        checked += 1
        lines = verify_bundle(b, src)
        failures += sum(1 for ln in lines if ln.lstrip().startswith("!"))
        print(f"• {b.name}:")
        for ln in lines:
            print(ln)

    print(f"\n=== {checked} bundle(s) checked: " + ("ALL CHECKS PASS" if failures == 0 else f"{failures} FAILURE(S)"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
