#!/usr/bin/env python3
"""Reconstruct an ``input/<task>/`` folder from a harbor ``output_bundle``.

This REVERSES the bundle writer (``src/utils/harbor/bundle.py`` +
``script/repackage_to_bundle.py``). Pass the path to an output_bundle — either a
single task bundle dir, or a root that contains several — and it rebuilds the
task input tree(s).

What it recovers
----------------
  prompts.txt         <- the bundle's prompt file (prompts.txt, prompt.txt,
                          PROMPT.md, or data/instruction.md), with its header
                          block normalised onto the five-key standard and the
                          window's day count recomputed from the dates. Written
                          to prompts.txt, never prompt.txt: the loader reads
                          prompt.txt as ONE prompt, collapsing a 20-turn task
                          to a single turn.
  TRUTH.md            <- <bundle>/TRUTH.md   (grader truth doc, if present)
  rubric.json         <- <bundle>/rubric.json
  persona/<f>         <- <bundle>/data/environment/persona/<f>
  data/<f>            <- <bundle>/data/environment/artifacts/inputs/files/<f>
  test_outputs.py     <- <bundle>/data/tests/test_outputs.py
  test_weights.json   <- <bundle>/data/tests/test_weights.json
  inject/<stage>/<f>  <- <bundle>/inject/ (staged verbatim by the repackager;
                          absent for tasks that ship no inject spec)
  mock_data/<api>/<f> <- the OVERLAY, isolated by diffing each seed file
                          (.json/.csv) under <bundle>/data/environment/<api>/
                          against a pristine baseline environment/<api>/<f>:
                          a seed that is NEW or DIFFERS from the baked default
                          is exactly what the task shipped as its overlay.

Why the baseline diff
---------------------
The bundle flattens "baked default + task overlay" into one tree
(``data/environment/<api>/``), copying the overlay LAST so it overwrites the
default at the same path. So the overlay BYTES are present, but to tell overlay
from default you must compare against the harness's pristine ``environment/``.
Use a baseline at the SAME commit the bundle was built from for best fidelity
(``--baseline-env``); otherwise unrelated default-seed drift can be misread as
an overlay (the tool flags that case).

What CANNOT be recovered (documented in RECONSTRUCTION_NOTES.md)
---------------------------------------------------------------
  * gt/ (ground truth)        — grader-only; never staged into any bundle.
  * data/ & persona/ SUBDIRS  — the bundle flattens both to a flat file list.
  * the pre-overlay DEFAULT a given overlay replaced — overwritten in the bundle
    (recover it from the harness environment/, not the bundle).
  * task_config.yaml / taxonomy.json — only partially inferable from task.toml.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from script.lib.recon import prompts as recon_prompts  # noqa: E402
from script.lib.recon import schedule as recon_schedule  # noqa: E402

SEED_EXTS = {".json", ".csv"}
ARTIFACTS_INPUTS_SUBPATH = ("artifacts", "inputs", "files")
_DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / "environment"


# ----------------------------------------------------------------------------- #
# bundle discovery
# ----------------------------------------------------------------------------- #
def _looks_like_bundle(p: Path) -> bool:
    """A task bundle has a prompt + rubric (or the data/ equivalents)."""
    has_prompt = (
        (p / "PROMPT.md").is_file()
        or (p / "prompt.txt").is_file()
        or (p / "data" / "instruction.md").is_file()
    )
    has_rubric = (p / "rubric.json").is_file()
    has_env = (p / "data" / "environment").is_dir()
    return has_prompt and (has_rubric or has_env)


def discover_bundles(root: Path) -> list[Path]:
    if _looks_like_bundle(root):
        return [root]
    # treat root as a parent of task bundles
    return [c for c in sorted(root.iterdir()) if c.is_dir() and _looks_like_bundle(c)]


# ----------------------------------------------------------------------------- #
# helpers
# ----------------------------------------------------------------------------- #
def _copy_file(src: Path, dst: Path, log: list[str], label: str) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    log.append(f"  ok   {label:<22} <- {src.name}")
    return True


def _copy_flat_dir(src: Path, dst: Path) -> list[str]:
    """Copy files (only) from src into dst, skipping junk. Bundle staging is flat."""
    names: list[str] = []
    if not src.is_dir():
        return names
    for f in sorted(src.iterdir()):
        if f.is_file() and f.name != ".DS_Store":
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.name)
            names.append(f.name)
    return names


def _read_bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


# ----------------------------------------------------------------------------- #
# mock_data overlay extraction (the core)
# ----------------------------------------------------------------------------- #
def extract_overlays(
    env_dir: Path, baseline_env: Path, out_mock: Path
) -> tuple[dict[str, list[str]], list[str]]:
    """Diff each api's seed files against the baseline; copy the overlay (diffs)."""
    recovered: dict[str, list[str]] = {}
    warnings: list[str] = []
    if not env_dir.is_dir():
        warnings.append(f"no data/environment under bundle ({env_dir}); skipped mock_data")
        return recovered, warnings

    for api_dir in sorted(env_dir.iterdir()):
        if not api_dir.is_dir() or not api_dir.name.endswith("-api"):
            continue
        base_api = baseline_env / api_dir.name
        base_present = base_api.is_dir()
        if not base_present:
            warnings.append(
                f"{api_dir.name}: not in baseline env — its seeds can't be verified "
                f"against a default; treating all .json/.csv as overlay (UNVERIFIED)"
            )
        for f in sorted(api_dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in SEED_EXTS:
                continue
            rel = f.relative_to(api_dir)
            base_f = base_api / rel
            if base_present and base_f.is_file():
                if _read_bytes(f) == _read_bytes(base_f):
                    continue  # identical to the baked default -> NOT an overlay
                reason = "differs-from-default"
            else:
                reason = "new-not-in-default"
            dest = out_mock / api_dir.name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            recovered.setdefault(api_dir.name, []).append(f"{rel} ({reason})")
    return recovered, warnings


# ----------------------------------------------------------------------------- #
# task.toml metadata (best-effort)
# ----------------------------------------------------------------------------- #
def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        try:
            import tomllib  # py3.11+
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ----------------------------------------------------------------------------- #
# per-task reconstruction
# ----------------------------------------------------------------------------- #
def recover_prompts(bundle: Path, out_dir: Path, log: list[str], timezone: str,
                    trajectory_run: str = ""):
    """Write the prompts.txt / prompts.json pair and report what changed.

    The pair is written together or not at all: task_parser hard-raises on a
    prompts.json with no companion prompts.txt.
    """
    source = recon_prompts.locate_prompt_file(bundle)
    if source is None:
        log.append("  MISS prompts.txt            <- no prompt file in the bundle")
        return None, recon_schedule.Instants(notes=["no prompt file to schedule"])
    rec = recon_prompts.normalise(source, task_id=out_dir.name, timezone=timezone)
    (out_dir / "prompts.txt").write_text(rec.text, encoding="utf-8")
    log.append(f"  ok   prompts.txt            <- {source.rel} "
               f"({len(rec.turns)} turn(s))")
    for fix in rec.fixes:
        log.append(f"  fix  prompts.txt            .. {fix}")

    window = recon_prompts.window_from_header(rec.header.get("window", ""),
                                              rec.header.get("timezone", ""))
    instants = recon_schedule.resolve(bundle, rec.turns, window,
                                      rec.header.get("timezone", ""), trajectory_run)
    if len(instants.values) == len(rec.turns) and rec.turns:
        payload = recon_schedule.build(rec.header.get("task_id", out_dir.name),
                                       rec.header.get("persona", ""),
                                       rec.header.get("timezone", ""),
                                       rec.turns, instants)
        (out_dir / "prompts.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log.append(f"  ok   prompts.json           <- {instants.source} "
                   f"({instants.fidelity})")
        if instants.jitter_ms:
            log.append(f"  note prompts.json           .. dropped up to "
                       f"{instants.jitter_ms}ms of dispatch latency per turn")
    else:
        log.append("  MISS prompts.json           <- no schedule the bundle supports")
        rec.unresolved.append(
            "prompts.json: refused — " + "; ".join(instants.notes or
            ["the bundle carries no turn instants"]))
    rec.unresolved.extend(n for n in instants.notes if instants.values)
    return rec, instants


def reconstruct(bundle: Path, out_dir: Path, baseline_env: Path, verbose: bool,
                timezone: str = "", trajectory_run: str = "") -> dict:
    env_dir = bundle / "data" / "environment"
    log: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts, instants = recover_prompts(bundle, out_dir, log, timezone,
                                        trajectory_run)

    # TRUTH.md (grader truth doc; published verbatim by the repackager)
    _copy_file(bundle / "TRUTH.md", out_dir / "TRUTH.md", log, "TRUTH.md")

    # rubric.json
    _copy_file(bundle / "rubric.json", out_dir / "rubric.json", log, "rubric.json")

    # persona/  (flattened in the bundle -> flat here)
    persona_names = _copy_flat_dir(env_dir / "persona", out_dir / "persona")
    if persona_names:
        log.append(f"  ok   persona/              <- {len(persona_names)} file(s)")

    # data/  (input attachments live under data/environment/artifacts/inputs/files/)
    data_names = _copy_flat_dir(env_dir.joinpath(*ARTIFACTS_INPUTS_SUBPATH), out_dir / "data")
    if data_names:
        log.append(f"  ok   data/                 <- {len(data_names)} file(s)")

    # tests
    _copy_file(bundle / "data" / "tests" / "test_outputs.py", out_dir / "test_outputs.py", log, "test_outputs.py")
    _copy_file(bundle / "data" / "tests" / "test_weights.json", out_dir / "test_weights.json", log, "test_weights.json")

    # inject/ (staged verbatim into the bundle root; absent for no-inject tasks)
    src_inject = bundle / "inject"
    if src_inject.is_dir():
        shutil.copytree(src_inject, out_dir / "inject", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".DS_Store"))
        n_inject = sum(1 for f in (out_dir / "inject").rglob("*") if f.is_file())
        log.append(f"  ok   inject/               <- {n_inject} file(s)")

    # mock_data/<api>/ via baseline diff
    overlays, warnings = extract_overlays(env_dir, baseline_env, out_dir / "mock_data")
    n_overlay_files = sum(len(v) for v in overlays.values())
    if overlays:
        log.append(f"  ok   mock_data/            <- {len(overlays)} api(s), {n_overlay_files} overlay file(s)")

    meta = _load_toml(bundle / "data" / "task.toml")
    if prompts is not None:
        warnings.extend(prompts.unresolved)

    _write_notes(out_dir, bundle, baseline_env, log, overlays, warnings, meta,
                 persona_names, data_names)

    summary = {
        "task": out_dir.name,
        "turns": len(prompts.turns) if prompts else 0,
        "clock": instants.fidelity if instants.values else "none",
        "prompt": (out_dir / "prompts.txt").is_file(),
        "rubric": (out_dir / "rubric.json").is_file(),
        "persona_files": len(persona_names),
        "data_files": len(data_names),
        "mock_data_apis": len(overlays),
        "mock_data_files": n_overlay_files,
        "warnings": warnings,
    }
    if verbose:
        print(f"\n[{out_dir.name}]")
        print("\n".join(log) or "  (nothing recovered)")
        for w in warnings:
            print(f"  WARN {w}")
    return summary


def _write_notes(out_dir, bundle, baseline_env, log, overlays, warnings, meta,
                 persona_names, data_names) -> None:
    lines = [
        f"# Reconstruction notes — {out_dir.name}",
        "",
        f"Source bundle : {bundle}",
        f"Baseline env  : {baseline_env}",
        "",
        "## Recovered",
        *log,
        "",
        "## mock_data overlay (isolated by baseline diff)",
    ]
    if overlays:
        for api, files in sorted(overlays.items()):
            lines.append(f"- {api}:")
            lines.extend(f"    - {f}" for f in files)
    else:
        lines.append("- (none detected — task shipped no mock_data overlay, or baseline mismatch)")
    if meta:
        req = meta.get("environment", {}).get("required_apis") or meta.get("required_apis")
        if req:
            lines += ["", "## task.toml metadata", f"- required_apis: {req}"]
    lines += [
        "",
        "## NOT recoverable from a bundle (by construction)",
        "- gt/ (ground truth): grader-only, never staged into a bundle.",
        "- data/ & persona/ SUBDIRECTORY structure: the bundle flattens both to a flat file list.",
        "- the pre-overlay DEFAULT each overlay replaced: overwritten in the bundle "
        "(recover from the harness environment/ if needed).",
        "- task_config.yaml / taxonomy.json: only partially inferable from task.toml.",
    ]
    if warnings:
        lines += ["", "## Warnings", *[f"- {w}" for w in warnings]]
    (out_dir / "RECONSTRUCTION_NOTES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- #
# cli
# ----------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reconstruct input/<task>/ folder(s) from a harbor output_bundle."
    )
    ap.add_argument("bundle_path", type=Path,
                    help="Path to an output_bundle task dir OR a root containing several.")
    ap.add_argument("--out", type=Path, default=Path("reconstructed_input"),
                    help="Output root; each task lands in <out>/<task>/ (default: ./reconstructed_input)")
    ap.add_argument("--baseline-env", type=Path, default=_DEFAULT_BASELINE,
                    help=f"Pristine harness environment/ for overlay diffing (default: {_DEFAULT_BASELINE})")
    ap.add_argument("--timezone", default="",
                    help="IANA timezone to use when the bundle's prompt header "
                         "omits one (headerless bundles carry it only as persona prose).")
    ap.add_argument("--trajectory-run", default="", metavar="RUN",
                    help="Published run to take turn instants from, as 'run_3' or "
                         "'<model>/run_3' (default: the first run that covers every turn).")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not args.bundle_path.exists():
        print(f"error: bundle path not found: {args.bundle_path}", file=sys.stderr)
        return 2
    if not args.baseline_env.is_dir():
        print(f"WARNING: baseline env not found at {args.baseline_env}; mock_data overlay "
              f"detection will be UNVERIFIED (every .json/.csv treated as overlay).",
              file=sys.stderr)

    bundles = discover_bundles(args.bundle_path)
    if not bundles:
        print(f"error: no task bundle found under {args.bundle_path} "
              f"(need prompt.txt/rubric.json or data/environment/)", file=sys.stderr)
        return 2

    print(f"Found {len(bundles)} task bundle(s). Baseline env: {args.baseline_env}")
    summaries = [reconstruct(b, args.out / b.name, args.baseline_env, args.verbose,
                             args.timezone, args.trajectory_run) for b in bundles]

    print(f"\n{'task':<45} {'turns':>5} {'clock':>8} {'rubric':>6} {'persona':>7} "
          f"{'data':>4} {'mock(apis/files)':>16}")
    for s in summaries:
        mock = f"{s['mock_data_apis']}/{s['mock_data_files']}"
        print(f"{s['task'][:44]:<45} {s['turns']:>5} {s['clock']:>8} "
              f"{str(s['rubric']):>6} {s['persona_files']:>7} {s['data_files']:>4} "
              f"{mock:>16}")
    total_warn = sum(len(s["warnings"]) for s in summaries)
    print(f"\nReconstructed into: {args.out.resolve()}"
          f"{'  (with ' + str(total_warn) + ' warning(s) — see RECONSTRUCTION_NOTES.md)' if total_warn else ''}")
    print("Reminder: gt/ is never recoverable from a bundle (grader-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
