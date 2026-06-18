#!/usr/bin/env python3
"""Standalone repackager: raw run-output  ->  published "bundle" structure.

This script is intentionally ISOLATED from the eval pipeline. It only reads an
existing run-output tree and writes a NEW bundle tree; it never touches the
pipeline code, never runs Docker, never calls an LLM. Run it whenever you want
to publish a finished run.

WHAT IT DOES
------------
Given our raw layout (produced by eval/run_batch.py):

    <source-root>/<task_id>/
        prompt.txt, rubric.json, data/ ...
        trajectories/<harness>/            (e.g. "claude")
            pass_summary.json
            run_N/
                output.json, score.json, usage.json, chat.jsonl, ...
                task_output/
                    artifacts/<written files>        (+ _tmp/ scratch)
                    logs/verifier/{ctrf.json,reward.txt,test_weights.json}

it emits the published bundle layout (like the amanda_webb_01 reference):

    <dest-root>/<bundle_name>/
        prompt.txt, rubric.json, data/ ...           (skeleton copied as-is)
        trajectories/<Pretty Model>/                 (e.g. "Claude Opus 4.7")
            pass_summary.json                         (REBUILT schema)
            run_N/
                output.json                           (copied as-is)
                report.json                           (BUILT from ctrf+score)
                output_media/<rendered files>         (artifacts minus _tmp/)

MATCHING (requirements 2 & 3)
-----------------------------
Source task dirs and the published bundle dirs are NOT named identically
(e.g. "ben_cox_8fc24d4b-..." vs a desired "ben_cox_01", or "amanda_webb_01").
We match on the PERSONA CORE NAME by normalizing: strip emoji / non-alphanumeric,
lowercase, drop any trailing uuid / numeric / hash suffix tokens, collapse
separators. So "ben cox", "ben_cox_8fc24d4b-..." and "ben_cox_01" all reduce to
the key "ben cox". The emoji-prefixed dir ("\U0001f7e2sheila_stokes_...") is handled.

The litellm-proxy difference between trees is IGNORED entirely (requirement 4):
we never read or write data/environment/litellm-proxy.

SELECTING WHAT TO CONVERT
-------------------------
  --persona "ben cox"     convert only the task whose core name matches "ben cox"
  --all                   convert every task dir under <source-root>
You must pass exactly one of --persona / --all.

DESIGN CHOICES (documented, since some target fields are absent in our data)
---------------------------------------------------------------------------
* report.json.final_reward            <- score.json.combined_reward
  (most faithful overall reward; the reference bundle's final_reward happens to
  equal its rubric percentage only because its test score was 100%.)
* report.json.test_weights_percentage <- ctrf.json.results.summary.weighted_percentage
  (falls back to reward.txt * 100 when ctrf is missing)
* report.json.rubric_weights_percentage <- score.json.rubric_weights_percentage
* rubric[].number          <- "R" + (score.json.criteria[].id + 1)
* rubric[].score           <- int(criteria[].weight)
* rubric[].is_positive     <- criteria[].is_positive
* rubric[].passed          <- criteria[].passed   (for negative items, passed==True
                              means the agent correctly AVOIDED the bad behavior,
                              matching the reference semantics)
* rubric[].justification   <- single-judge criteria[].rationale, or, on a council
                              score.json, the first non-empty criteria[].rationales_by_judge
                              entry; emitted ONLY on failed items (empty string if none)
* rubric[].type / importance / evaluation_target are NOT in our score.json.
    - importance is DERIVED: abs(weight) >= 5 -> "critically_important" else "important"
    - type and evaluation_target are emitted as "" (unknown) unless --infer-rubric-meta
      is passed, in which case light heuristics fill them (see _infer_meta).
* pytest test name           <- "tests/test_outputs.py::" + ctrf "Class::method"
* pytest test weight         <- test_weights.json[method]  (default 1 if absent)
* include_multimodal         <- True if rubric/task mentions image/document OR any
                                input_files mime is image/* (see _detect_multimodal)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any


# Extend as new harnesses/models are published. Default direct-Anthropic path
# runs Opus 4.7, so "claude" -> "Claude Opus 4.7".
MODEL_LABELS: dict[str, str] = {
    "claude": "Claude Opus 4.7",
    "claudecode": "Claude Opus 4.7",
    "openclaw": "Claude Opus 4.7",
    "gpt": "GPT 5.5",
    "codex": "GPT 5.5",
    "hermes": "Hermes",
    "hermesagent": "Hermes",
}

# Scratch subdir inside artifacts/ that must never be published.
ARTIFACTS_SCRATCH_DIRNAME = "_tmp"

# The run-output tree strips the persona/ and staged input files from
# data/environment/. The published bundle (amanda_webb reference) keeps them, so
# we re-source them from the ORIGINAL task input dir (default root: "input").
# Where staged inputs live inside the bundle's environment dir.
ARTIFACTS_INPUTS_SUBPATH = ("artifacts", "inputs", "files")


# Tokens that look like a uuid / hex hash / pure-number suffix get dropped so the
# persona "core" survives. e.g. ben_cox_8fc24d4b-dd01-... -> "ben cox".
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{6,}$", re.I)
_NUM_RE = re.compile(r"^\d+$")


def _strip_emoji(text: str) -> str:
    """Drop emoji / symbol / non-ascii pictographs, keep letters/digits/separators."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        # So=other symbol (most emoji), Sk=modifier symbol, Cs=surrogate, Co=private.
        if cat in {"So", "Sk", "Cs", "Co"}:
            continue
        out.append(ch)
    return "".join(out)


def persona_core(name: str) -> str:
    """Reduce any folder/persona label to its comparable core key.

    "\U0001f7e2sheila_stokes_c74d93d8-..."  -> "sheila stokes"
    "ben_cox_8fc24d4b-dd01-44db-95b5-..."   -> "ben cox"
    "amanda_webb_01"                          -> "amanda webb"
    "alden-croft"                             -> "alden croft"
    """
    name = _strip_emoji(name)
    name = name.lower()
    # Split on separators and whitespace.
    tokens = re.split(r"[\s\-_]+", name)
    kept: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        # Drop uuid/hex/numeric suffix tokens (they are run/instance ids, not names).
        if _UUID_RE.match(tok) or _HEX_RE.match(tok) or _NUM_RE.match(tok):
            continue
        # Drop leftover non-alphanumeric debris.
        tok = re.sub(r"[^a-z0-9]+", "", tok)
        if tok:
            kept.append(tok)
    return " ".join(kept).strip()


def _load_json(path: Path) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _pretty_model(harness_dirname: str) -> str:
    key = harness_dirname.strip().lower()
    return MODEL_LABELS.get(key, harness_dirname)


def _method_of(test_name: str) -> str:
    """ctrf name is 'Class::method'; weights key is bare 'method'."""
    return test_name.split("::")[-1]


def _build_pytest_block(verifier_dir: Path) -> dict[str, Any]:
    ctrf = _load_json(verifier_dir / "ctrf.json") or {}
    weights = _load_json(verifier_dir / "test_weights.json") or {}
    results = ctrf.get("results", {}) if isinstance(ctrf, dict) else {}
    summary = results.get("summary", {}) if isinstance(results, dict) else {}
    raw_tests = results.get("tests", []) if isinstance(results, dict) else []

    tests: list[dict[str, Any]] = []
    for t in raw_tests:
        name = t.get("name", "")
        method = _method_of(name)
        weight = weights.get(method, weights.get(name, 1))
        tests.append(
            {
                "name": f"tests/test_outputs.py::{name}",
                "weight": int(weight),
                "passed": t.get("status") == "passed",
            }
        )

    # reward.txt fallback for percentage if ctrf summary missing.
    reward_txt = None
    rtxt = verifier_dir / "reward.txt"
    if rtxt.exists():
        try:
            reward_txt = float(rtxt.read_text().strip())
        except ValueError:
            reward_txt = None

    passed = int(summary.get("passed", sum(1 for t in tests if t["passed"])))
    failed = int(summary.get("failed", sum(1 for t in tests if not t["passed"])))
    reward = summary.get("overall_score")
    if reward is None:
        reward = reward_txt if reward_txt is not None else 0.0

    return {
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if failed == 0 else 1,
        "reward": float(reward),
        "tests": tests,
    }, summary, reward_txt


def _infer_meta(criterion: str, is_positive: bool) -> tuple[str, str]:
    """Light heuristic for (type, evaluation_target) when --infer-rubric-meta set.

    Kept conservative: only obvious keyword hits, else ("", "").
    """
    c = criterion.lower()
    typ = ""
    if any(w in c for w in ("must not", "should not", "no ", "without", "avoid", "distractor")):
        typ = "safety & boundaries" if not is_positive else "instruction following"
    elif any(w in c for w in ("state", "writes", "records", "generates", "file", ".docx", ".pdf")):
        typ = "task completion"
    elif any(w in c for w in ("hallucinat", "factual", "sourced", "references", "identifies")):
        typ = "factuality and hallucination"
    elif any(w in c for w in ("tool", "api", "endpoint", "queried", "fetch")):
        typ = "tool use"

    target = ""
    if any(w in c for w in ("file", "writes", "records", "generates", ".docx", ".pdf", "inside")):
        target = "state_change"
    elif any(w in c for w in ("tool", "image-processing", "trajectory", "uses an")):
        target = "trajectory"
    elif any(w in c for w in ("response states", "response presents", "final", "answer")):
        target = "final_answer"
    return typ, target


def _pick_rationale(c: dict[str, Any]) -> str:
    rationale = c.get("rationale")
    if isinstance(rationale, str) and rationale:
        return rationale
    by_judge = c.get("rationales_by_judge")
    if isinstance(by_judge, list):
        for r in by_judge:
            if isinstance(r, str) and r:
                return r
    return ""


def _build_rubric_block(score: dict[str, Any], infer_meta: bool) -> list[dict[str, Any]]:
    rubric: list[dict[str, Any]] = []
    for c in score.get("criteria", []):
        weight = c.get("weight", 0)
        is_positive = bool(c.get("is_positive", weight >= 0))
        criterion = c.get("criterion", "")
        importance = "critically_important" if abs(float(weight)) >= 5 else "important"
        typ, target = _infer_meta(criterion, is_positive) if infer_meta else ("", "")
        passed = bool(c.get("passed", False))
        item: dict[str, Any] = {
            "number": f"R{int(c.get('id', 0)) + 1}",
            "criterion": criterion,
            "type": typ,
            "evaluation_target": target,
            "importance": importance,
            "score": int(weight),
            "is_positive": is_positive,
            "passed": passed,
        }
        if not passed:
            item["justification"] = _pick_rationale(c)
        rubric.append(item)
    return rubric


def _detect_multimodal(task_dir: Path, output_json: dict[str, Any] | None) -> bool:
    # task.yaml / task.toml modalities
    for fname in ("task.yaml", "data/task.toml", "task.toml"):
        p = task_dir / fname
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore").lower()
            if "image" in txt or "document" in txt or "multimodal" in txt:
                return True
    # input_files mimes in output.json
    if isinstance(output_json, dict):
        traj = output_json.get("trajectory", {})
        files = output_json.get("input_files") or traj.get("input_files") or []
        for f in files:
            mime = (f.get("mime") or f.get("mime_type") or "") if isinstance(f, dict) else ""
            if isinstance(mime, str) and mime.startswith("image/"):
                return True
    return False


def build_report(
    run_dir: Path,
    task_dir: Path,
    pretty_model: str,
    run_index: int,
    infer_meta: bool,
) -> dict[str, Any]:
    verifier = run_dir / "task_output" / "logs" / "verifier"
    score = _load_json(run_dir / "score.json") or {}
    output_json = _load_json(run_dir / "output.json")

    pytest_block, ctrf_summary, reward_txt = _build_pytest_block(verifier)
    rubric_block = _build_rubric_block(score, infer_meta)

    test_pct = ctrf_summary.get("weighted_percentage")
    if test_pct is None:
        test_pct = (reward_txt * 100.0) if reward_txt is not None else 0.0
    rubric_pct = score.get("rubric_weights_percentage", 0.0)
    final_reward = score.get("combined_reward")
    if final_reward is None:
        final_reward = score.get("rubric_based_reward", 0.0)

    return {
        "model": pretty_model,
        "run_index": run_index,
        "include_multimodal": _detect_multimodal(task_dir, output_json),
        "pytest": pytest_block,
        "rubric": rubric_block,
        "final_reward": round(float(final_reward), 4),
        "test_weights_percentage": round(float(test_pct), 2),
        "rubric_weights_percentage": round(float(rubric_pct), 2),
    }


def copy_output_media(run_dir: Path, dest_run: Path) -> int:
    artifacts = run_dir / "task_output" / "artifacts"
    media_dest = dest_run / "output_media"
    if not artifacts.is_dir():
        media_dest.mkdir(parents=True, exist_ok=True)
        return 0
    media_dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in artifacts.iterdir():
        if item.name == ARTIFACTS_SCRATCH_DIRNAME:
            continue  # skip scratch frames
        if item.name == ".DS_Store":
            continue
        target = media_dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
            count += sum(1 for _ in target.rglob("*") if _.is_file())
        else:
            shutil.copy2(item, target)
            count += 1
    return count


def _run_index_of(run_dirname: str) -> int:
    m = re.search(r"run_(\d+)", run_dirname)
    return int(m.group(1)) if m else 1


def _find_input_task_dir(input_root: Path, task_dir_name: str) -> Path | None:
    if not input_root.is_dir():
        return None
    want = persona_core(task_dir_name)
    candidates = [p for p in input_root.iterdir() if p.is_dir()]
    for p in candidates:
        if persona_core(p.name) == want:
            return p
    for p in candidates:
        if want and want in persona_core(p.name):
            return p
    return None


def stage_persona_and_artifacts(
    input_task_dir: Path, bundle: Path, verbose: bool
) -> tuple[int, int]:
    env_dir = bundle / "data" / "environment"
    n_persona = 0
    src_persona = input_task_dir / "persona"
    if src_persona.is_dir():
        dest_persona = env_dir / "persona"
        dest_persona.mkdir(parents=True, exist_ok=True)
        for item in src_persona.iterdir():
            if item.is_file() and item.name != ".DS_Store":
                shutil.copy2(item, dest_persona / item.name)
                n_persona += 1

    n_files = 0
    src_files = input_task_dir / "data"
    if src_files.is_dir():
        dest_files = env_dir.joinpath(*ARTIFACTS_INPUTS_SUBPATH)
        dest_files.mkdir(parents=True, exist_ok=True)
        for item in src_files.iterdir():
            if item.is_file() and item.name != ".DS_Store":
                shutil.copy2(item, dest_files / item.name)
                n_files += 1

    if verbose:
        print(f"    staged persona: {n_persona} file(s), input artifacts: {n_files} file(s)")
    return n_persona, n_files


def convert_task(
    task_dir: Path,
    dest_root: Path,
    input_root: Path,
    infer_meta: bool,
    verbose: bool,
) -> Path | None:
    trajectories = task_dir / "trajectories"
    if not trajectories.is_dir():
        print(f"  ! no trajectories/ under {task_dir.name}; skipping", file=sys.stderr)
        return None

    bundle = dest_root / task_dir.name
    # rubric.json + data/ come from the run-output task dir; prompt.txt is
    # re-sourced from the original input task dir (data/ minus litellm-proxy).
    if (task_dir / "rubric.json").exists():
        bundle.mkdir(parents=True, exist_ok=True)
        shutil.copy2(task_dir / "rubric.json", bundle / "rubric.json")
    if (task_dir / "data").is_dir():
        shutil.copytree(
            task_dir / "data",
            bundle / "data",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "litellm-proxy",
                "API_DOCUMENTATION.md",
                "sqlite_mcp_server.db",
                "_meta.json",
            ),
        )
        # Shared mock-server infra imported by every <api>/server.py
        # (`from _mutable_store import ...`, `admin_plane`, `tracking_middleware`).
        # The run-output snapshot only carries tracking_middleware.py; the other
        # two live at the repo environment/ root. Copy all three into the bundle's
        # data/environment/ so the bundled mocks are self-contained and runnable.
        _repo_env = Path(__file__).resolve().parents[1] / "environment"
        _bundle_env = bundle / "data" / "environment"
        if _bundle_env.is_dir():
            for _infra in ("_mutable_store.py", "admin_plane.py", "tracking_middleware.py"):
                _src = _repo_env / _infra
                if _src.exists():
                    shutil.copy2(_src, _bundle_env / _infra)

    # Re-source prompt.txt, persona/ and staged input files (stripped/altered in
    # run output) from the original input task dir, fuzzy-matched by persona core.
    input_task_dir = _find_input_task_dir(input_root, task_dir.name)
    if input_task_dir is not None:
        stage_persona_and_artifacts(input_task_dir, bundle, verbose)
    elif verbose:
        print(f"    (no input dir matched under {input_root}; persona/artifacts skipped)")

    # Prompt: multi-turn tasks author the full conversation in `prompts.txt`;
    # single-turn tasks use `prompt.txt`. Prefer the source `prompts.txt`, then a
    # source `prompt.txt`, then the harbor-written `prompt.txt` in the run-output
    # dir (so the prompt is never dropped — the old code looked only for a source
    # `prompt.txt`, which multi-turn tasks lack, shipping bundles with no prompt).
    # Publish as BOTH `prompts.txt` (current spec) and `prompt.txt` (back-compat).
    prompt_candidates = []
    if input_task_dir is not None:
        prompt_candidates += [input_task_dir / "prompts.txt", input_task_dir / "prompt.txt"]
    prompt_candidates.append(task_dir / "prompt.txt")
    prompt_src = next((p for p in prompt_candidates if p.is_file()), None)
    if prompt_src is not None:
        bundle.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prompt_src, bundle / "prompts.txt")
        shutil.copy2(prompt_src, bundle / "prompt.txt")
    elif verbose:
        print(f"    (no prompt found for {task_dir.name}; prompts.txt/prompt.txt skipped)")

    # Inject: the silent-mutation spec lives only in the source task dir (harbor
    # does not stage it into run output). Copy it verbatim so the bundle is
    # reproducible and gradable.
    if input_task_dir is not None:
        inject_src = input_task_dir / "inject"
        if inject_src.is_dir():
            bundle.mkdir(parents=True, exist_ok=True)
            shutil.copytree(inject_src, bundle / "inject", dirs_exist_ok=True)
            if verbose:
                n_inj = sum(1 for _ in inject_src.rglob("*") if _.is_file())
                print(f"    staged inject/ ({n_inj} file(s))")

    produced_any = False
    for harness_dir in sorted(p for p in trajectories.iterdir() if p.is_dir()):
        pretty = _pretty_model(harness_dir.name)
        dest_model = bundle / "trajectories" / pretty
        run_dirs = sorted(
            (p for p in harness_dir.iterdir() if p.is_dir() and re.match(r"run_\d+", p.name)),
            key=lambda p: _run_index_of(p.name),
        )
        per_run_summ: list[dict[str, Any]] = []
        for run_dir in run_dirs:
            ridx = _run_index_of(run_dir.name)
            dest_run = dest_model / f"run_{ridx}"
            dest_run.mkdir(parents=True, exist_ok=True)

            # 1) output.json copied as-is
            src_out = run_dir / "output.json"
            if src_out.exists():
                shutil.copy2(src_out, dest_run / "output.json")

            # 2) report.json built
            report = build_report(run_dir, task_dir, pretty, ridx, infer_meta)
            _write_json(dest_run / "report.json", report)

            # 3) output_media
            n_media = copy_output_media(run_dir, dest_run)

            per_run_summ.append(
                {
                    "run_index": ridx,
                    "include_multimodal": report["include_multimodal"],
                    "test_weights_percentage": report["test_weights_percentage"],
                    "rubric_weights_percentage": report["rubric_weights_percentage"],
                }
            )
            produced_any = True
            if verbose:
                print(
                    f"    {pretty}/run_{ridx}: report.json + output.json "
                    f"+ {n_media} media file(s)"
                )

        # pass_summary.json REBUILT from the runs we actually emitted.
        if per_run_summ:
            n = len(per_run_summ)
            avg_test = sum(r["test_weights_percentage"] for r in per_run_summ) / n
            avg_rub = sum(r["rubric_weights_percentage"] for r in per_run_summ) / n
            _write_json(
                dest_model / "pass_summary.json",
                {
                    "model": pretty,
                    "runs": n,
                    "average_test_weights_percentage": round(avg_test, 2),
                    "average_rubric_weights_percentage": round(avg_rub, 2),
                    "per_run": per_run_summ,
                },
            )

    if produced_any:
        print(f"  + {task_dir.name}  ->  {bundle}")
        return bundle
    print(f"  ! {task_dir.name}: no run_N dirs found; nothing emitted", file=sys.stderr)
    return None


def discover_tasks(source_root: Path) -> list[Path]:
    return sorted(
        p for p in source_root.iterdir()
        if p.is_dir() and (p / "trajectories").is_dir()
    )


def select_tasks(source_root: Path, persona: str | None, do_all: bool) -> list[Path]:
    tasks = discover_tasks(source_root)
    if do_all:
        return tasks
    want = persona_core(persona or "")
    matches = [t for t in tasks if persona_core(t.name) == want]
    if not matches:
        # Looser fallback: substring on core key, so "ben" matches "ben cox".
        matches = [t for t in tasks if want and want in persona_core(t.name)]
    return matches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Repackage raw run output into the published bundle structure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--source-root",
        default="output/openclaw",
        help="Root containing raw <task_id>/ dirs (default: output/openclaw)",
    )
    ap.add_argument(
        "--dest-root",
        required=True,
        help="Destination root for the published bundle tree (created if absent).",
    )
    ap.add_argument(
        "--input-root",
        default="input",
        help="Root of original task input dirs, used to re-source persona/ and "
        "staged input files (default: input).",
    )
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--persona",
        help='Persona core name to convert, fuzzy-matched (e.g. "ben cox").',
    )
    grp.add_argument(
        "--all", action="store_true", help="Convert every task under --source-root."
    )
    ap.add_argument(
        "--infer-rubric-meta",
        action="store_true",
        help="Heuristically fill rubric type/evaluation_target (else left empty).",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="Per-run detail.")
    args = ap.parse_args(argv)

    source_root = Path(args.source_root).resolve()
    dest_root = Path(args.dest_root).resolve()
    input_root = Path(args.input_root).resolve()
    if not source_root.is_dir():
        print(f"error: --source-root not a directory: {source_root}", file=sys.stderr)
        return 2

    tasks = select_tasks(source_root, args.persona, args.all)
    if not tasks:
        avail = ", ".join(sorted(persona_core(t.name) for t in discover_tasks(source_root))) or "(none)"
        print(
            f"error: no task matched persona {args.persona!r}. "
            f"Available persona cores: {avail}",
            file=sys.stderr,
        )
        return 1

    print(f"source : {source_root}")
    print(f"dest   : {dest_root}")
    print(f"input  : {input_root}")
    print(f"tasks  : {len(tasks)} selected")
    dest_root.mkdir(parents=True, exist_ok=True)
    produced = 0
    for task_dir in tasks:
        if convert_task(task_dir, dest_root, input_root, args.infer_rubric_meta, args.verbose):
            produced += 1
    print(f"done: {produced}/{len(tasks)} task bundle(s) written under {dest_root}")
    return 0 if produced else 1


if __name__ == "__main__":
    raise SystemExit(main())
