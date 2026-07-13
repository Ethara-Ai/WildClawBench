from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.base import AgentTaskSpec, BaseAgent
from src.agents.claudecode import ClaudeCodeAgent
from src.agents.codex import CodexAgent
from src.agents.openclaw import OpenClawAgent
from src.utils.cli_args import parse_run_batch_args
from src.utils.endpoint_utils import (
    normalize_openrouter_base_url_for_claudecode,
    normalize_openrouter_base_url_for_openclaw,
)
from src.utils.task_parser import parse_task_md
from src.utils.docker_utils import (
    remove_container,
    close_proc_log,
    collect_output_from_container,
    TMP_WORKSPACE,
)
from src.utils.grading import (
    run_grading,
    format_scores,
    print_summary,
    print_global_summary,
    write_error_score as write_error_score_file,
)
from src.utils.config import Config
from src.utils.task_parser import load_task
from src.utils.docker_utils import discover_services, require_image_present, DOCKER_IMAGE
from src.utils.skills_inference import (
    infer_required_apis,
    compute_distractor_skills,
    available_apis,
)
from src.utils.testgen import generate_task_tests
from src.utils.litellm_sidecar import (
    CC_BRIDGE_INTERNAL_PORT,
    build_litellm_config_yaml,
    create_network,
    ensure_litellm_headroom_image,
    pull_litellm_image,
    remove_network,
    start_bridge,
    start_litellm,
    stop_bridge,
    wait_for_bridge_healthy,
    stop_litellm,
    verify_litellm_upstream_reachable,
    wait_for_litellm_healthy,
)
from src.utils.trajectory.builder import build_trajectory_from_jsonl
from src.utils.trajectory.local_media import replace_inline_media_with_files
from src.utils.store import Task as StoreTask
from src.utils.env_overlay_snapshot import stage_environment_with_overlays

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

GATEWAY_PORT     = int(os.environ.get("GATEWAY_PORT", "18789"))

ROOT_DIR         = Path(__file__).resolve().parent.parent
TASKS_DIR        = ROOT_DIR / os.environ.get("TASKS_SUBDIR",  "tasks")
OUTPUT_DIR       = ROOT_DIR / os.environ.get("OUTPUT_SUBDIR", "output")

DEFAULT_MODEL    = os.environ.get("DEFAULT_MODEL",    "openrouter/anthropic/claude-sonnet-4.6")
DEFAULT_PARALLEL = int(os.environ.get("DEFAULT_PARALLEL", "1"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL_OPENCLAW = normalize_openrouter_base_url_for_openclaw(
    os.environ.get("OPENROUTER_BASE_URL", "")
)
OPENROUTER_BASE_URL_CLAUDECODE = normalize_openrouter_base_url_for_claudecode(
    os.environ.get("OPENROUTER_BASE_URL", "")
)
MODELS_API_KEY_PLACEHOLDER = "${MY_PROXY_API_KEY}"

# Bumped whenever the cache-key input shape or testgen contract changes
# (e.g. ALLOWED_WEIGHTS rescale per b30, prompt format edits). Bump invalidates
# every on-disk testgen cache deterministically so we never silently serve a
# stale test suite generated under different semantics. See b54 Issue 3.
_TESTGEN_CACHE_VERSION = "v2-weights531"


def _compute_testgen_cache_key(task: dict) -> str:
    task_dir = task.get("task_dir")
    if not task_dir:
        return ""
    p = Path(task_dir)
    if not p.is_dir():
        return ""
    h = hashlib.sha256()
    h.update(_TESTGEN_CACHE_VERSION.encode())
    for fname in ("rubric.json", "prompt.txt", "task_config.yaml"):
        f = p / fname
        h.update(f"\x00{fname}\x00".encode())
        if f.is_file():
            try:
                h.update(f.read_bytes())
            except OSError:
                h.update(b"<unreadable>")
    mock_root = p / "mock_data"
    if mock_root.is_dir():
        # Key on each mock-data file's CONTENT, not its size. Two fixtures of
        # identical byte-length but different content (common with fixed-width
        # CSV rows or padded JSON) would otherwise collide and silently serve a
        # stale test suite. We fold relpath + a content digest into the hash.
        for sub in sorted(mock_root.iterdir()):
            if sub.is_dir():
                for child in sorted(sub.rglob("*")):
                    if child.is_file():
                        relpath = str(child.relative_to(mock_root))
                        h.update(f"\x00mock:{relpath}\x00".encode())
                        try:
                            h.update(hashlib.sha256(child.read_bytes()).digest())
                        except OSError:
                            h.update(b"<unreadable>")
    return h.hexdigest()[:32]


ALL_CATEGORIES = [
    "01_Productivity_Flow",
    "02_Code_Intelligence",
    "03_Social_Interaction",
    "04_Search_Retrieval",
    "05_Creative_Synthesis",
    "06_Safety_Alignment",
]

def grade_the_task(
    task_id: str,
    workspace_path: str,
    output_dir: Path,
    task: dict,
    result: dict,
    lobster_env: list[str] | None = None,
    transcript_container_path: str = "",
    grade_on_error: bool = False,
    write_error_score_on_failure: bool = False,
):
    gt_host = os.path.join(workspace_path, "gt")
    if os.path.isdir(gt_host):
        r_gt = subprocess.run(
            ["docker", "cp", gt_host, f"{task_id}:{TMP_WORKSPACE}/gt"],
            capture_output=True, text=True,
        )
        if r_gt.returncode != 0:
            logger.warning("[%s] gt directory copy failed: %s", task_id, r_gt.stderr)
        else:
            logger.info("[%s] gt directory copied to container %s/gt", task_id, TMP_WORKSPACE)

    should_grade = task.get("automated_checks") and (
        not result.get("error") or grade_on_error
    )
    if should_grade:
        try:
            scores = run_grading(
                task_id=task_id,
                automated_checks=task["automated_checks"],
                output_dir=output_dir,
                extra_env=task.get("env", ""),
                lobster_env=lobster_env,
                transcript_container_path=transcript_container_path,
                write_error_score=write_error_score_on_failure,
            )
            result["scores"] = scores
            print(format_scores(task_id, scores))
            logger.info("[%s] Grading complete", task_id)
        except Exception as exc:
            logger.error("[%s] Grading failed: %s", task_id, exc)
            result["scores"] = write_error_score_file(output_dir, task_id, str(exc))
    elif not task.get("automated_checks"):
        logger.info("[%s] No Automated Checks, skipping grading", task_id)
        if result.get("error"):
            result["scores"] = write_error_score_file(output_dir, task_id, result["error"])

    return result

_USAGE_NUMERIC_KEYS = (
    "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "request_count",
)


def _merge_usage_source(dst: dict, src: dict) -> None:
    if not src:
        return
    for k in _USAGE_NUMERIC_KEYS:
        dst[k] = dst.get(k, 0) + int(src.get(k, 0) or 0)
    if "cost_usd" in src:
        dst["cost_usd"] = float(dst.get("cost_usd", 0.0)) + float(src.get("cost_usd", 0.0) or 0.0)


def recompute_combined(sources: dict[str, dict], task_id: str = "") -> dict:
    """Sum per-source usage under the canonical Bedrock-native convention
    (input_tokens excludes cache; total_tokens == input+output+cR+cW). Shared
    by save_usage and script/regrade.py; warns+overwrites if a source desyncs
    the invariant. See token-accounting convention docs.
    """
    combined: dict[str, Any] = {k: 0 for k in _USAGE_NUMERIC_KEYS}
    combined["cost_usd"] = 0.0
    for src in sources.values():
        _merge_usage_source(combined, src)

    expected_total = (
        combined["input_tokens"]
        + combined["output_tokens"]
        + combined["cache_read_tokens"]
        + combined["cache_write_tokens"]
    )
    if combined["total_tokens"] != expected_total:
        logger.warning(
            "[%s] total_tokens invariant violated: stored=%d expected=%d "
            "(input=%d output=%d cache_read=%d cache_write=%d) - overwriting",
            task_id, combined["total_tokens"], expected_total,
            combined["input_tokens"], combined["output_tokens"],
            combined["cache_read_tokens"], combined["cache_write_tokens"],
        )
    combined["total_tokens"] = expected_total
    return combined


def save_usage(
    output_dir: Path,
    result: dict,
    usage: dict,
    task_id: str,
    *,
    testgen_usage: dict | None = None,
    judge_usage: dict | None = None,
    preflight_usage: dict | None = None,
) -> dict:
    """Write usage.json with per-source breakdown (agent + testgen + judge + preflight)."""
    agent_usage = dict(usage)
    agent_usage.pop("__preflight__", None)
    sources: dict[str, dict] = {"agent": agent_usage}
    if preflight_usage:
        sources["preflight"] = dict(preflight_usage)
    if testgen_usage:
        sources["testgen"] = dict(testgen_usage)
    # `is not None` (not truthiness): a failed-judge stub is request_count=0
    # but must still persist so the failure is visible; only None = no judge ran.
    if judge_usage is not None:
        sources["judge"] = dict(judge_usage)

    combined = recompute_combined(sources, task_id)

    out: dict[str, Any] = dict(combined)
    out["sources"] = sources
    for k, v in agent_usage.items():
        if k not in out and k not in _USAGE_NUMERIC_KEYS and k != "cost_usd":
            out[k] = v

    result["usage"] = out
    if out["request_count"] > 0:
        # Include preflight in the breakdown so the sidecar-startup ping is visible
        # (its cost IS already in the combined total via recompute_combined). NOTE:
        # preflight is attributed to EVERY task with no time-window filter, so the same
        # one-time ping cost is replicated across all N per-task usage.json files -- a
        # double-count trap if you naively SUM per-task usage to get a batch total.
        # Per-source cost_usd is logged too so a $0 source is distinguishable from a
        # source that simply was not priced.
        breakdown_bits = []
        for name in ("agent", "preflight", "testgen", "judge"):
            s = sources.get(name)
            if s and s.get("request_count", 0) > 0:
                breakdown_bits.append(
                    f"{name}(in={s.get('input_tokens',0)},out={s.get('output_tokens',0)}"
                    f",cR={s.get('cache_read_tokens',0)},$ {s.get('cost_usd',0.0):.4f})"
                )
        logger.info(
            "[%s] Token usage TOTAL - input:%d output:%d cache_read:%d cache_write:%d "
            "total:%d cost:$%.4f sources=[%s]",
            task_id,
            out["input_tokens"], out["output_tokens"],
            out["cache_read_tokens"], out["cache_write_tokens"],
            out["total_tokens"], out.get("cost_usd", 0.0),
            " ".join(breakdown_bits) or "none",
        )
    usage_path = output_dir / "usage.json"
    usage_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[%s] Usage written to %s", task_id, usage_path)
    return result

def collect_task_output(
    task_id: str,
    output_dir: Path,
    *,
    include_workspace_changes: bool = False,
) -> None:
    """Collect task output files from the container to output_dir/task_output/."""
    try:
        collect_output_from_container(
            task_id,
            output_dir,
            include_workspace_changes=include_workspace_changes,
        )
    except Exception as exc:
        logger.warning("[%s] Failed to collect task output: %s", task_id, exc)


def load_models_config(models_config_path: Path) -> dict:
    raw_config = models_config_path.read_text(encoding="utf-8")
    proxy_api_key = os.environ.get("MY_PROXY_API_KEY")
    if MODELS_API_KEY_PLACEHOLDER in raw_config and not proxy_api_key:
        raise ValueError(
            "MY_PROXY_API_KEY must be set to a non-empty value when models config uses ${MY_PROXY_API_KEY}"
        )

    expanded_config = raw_config.replace(
        MODELS_API_KEY_PLACEHOLDER,
        proxy_api_key or "",
    )
    parsed_models_config = json.loads(expanded_config)
    if not isinstance(parsed_models_config, dict):
        raise ValueError(f"Models config must be a JSON object: {models_config_path}")
    return parsed_models_config


def _stage_native_workspace(task: dict, config) -> str:
    """Create the per-task workspace dir (<work>/<task_id>/exec) the openclaw runner
    mounts at /app, returning the parent dir to pass as workspace_path.

    Input artifacts are NOT staged here: they ship in <task>/data/ and reach the
    container via docker_utils.inject_data_into_workspace, which copies data/. to
    /root/workspace/home. The exec dir is created empty so the /app:ro mount +
    `cp -r /app/.` workspace bootstrap in setup_workspace still succeeds."""
    task_id_ori = task["task_id"]
    staging = Path(config.work_dir) / re.sub(r"[^a-zA-Z0-9._-]", "_", task_id_ori)
    exec_dir = staging / "exec"
    exec_dir.mkdir(parents=True, exist_ok=True)
    return str(staging)


def _resolve_task_apis(task: dict, config) -> "tuple[set[str], list[str], dict]":
    """Resolve a task's (required_apis, distractor_apis, mock_overlays) without
    mutating the task. Single source of truth for both `_augment_task_with_mocks`
    (per-task) and `_collect_enabled_apis` (which limits the shared mock stack to
    only the APIs any task actually needs).

    Precedence for required_apis (highest first):
      1. Explicit `required_apis_declared` from the task file
         (yaml `required_apis:` or native `task.json`).
      2. mock_data/<api>/ subdirs.
      3. Keyword inference over the prompt.

    When (1) is present, (3) is skipped entirely so curated-keyword false
    positives can never override author intent. mock_data unions in only when
    (1) is absent, because an explicit declaration is a contract.

    Distractor policy (m0750 contract; supersedes b22):
      distractor_apis: auto      -> full catalog complement (compute_distractor_skills)
      distractor_apis: [a, b, c] -> exactly those (minus any overlap with required)
      distractor_apis: [] | null | key absent -> NO distractors at all
    """
    raw_declared_required = task.get("required_apis_declared")
    raw_declared_distractor = task.get("distractor_apis_declared")
    declared = set(raw_declared_required) if isinstance(raw_declared_required, list) else set()
    distractor_is_auto = raw_declared_distractor == "__AUTO__"
    distractor_is_absent = (
        "distractor_apis_declared" not in task
        or raw_declared_distractor == "__ABSENT__"
    )
    declared_distractors = (
        set(raw_declared_distractor)
        if isinstance(raw_declared_distractor, list)
        else set()
    )

    required: set[str] = set()
    if declared:
        required.update(declared)
    else:
        try:
            required.update(infer_required_apis(
                task.get("initial_prompt") or task.get("prompt") or "",
                environment_dir=config.environment_dir,
            ))
        except Exception:
            pass

    task_dir = task.get("task_dir", "")
    overlays: dict[str, dict[str, str]] = {}
    if task_dir:
        mock_root = Path(task_dir) / "mock_data"
        if mock_root.is_dir():
            mock_apis = {d.name for d in mock_root.iterdir() if d.is_dir()}
            if not declared:
                required.update(mock_apis)
            overlays = {
                api_dir.name: {
                    p.name: str(p.resolve())
                    for p in api_dir.iterdir() if p.is_file()
                }
                for api_dir in sorted(mock_root.iterdir())
                if api_dir.is_dir() and any(p.is_file() for p in api_dir.iterdir())
            }

    try:
        catalog = set(available_apis(config.environment_dir))
    except Exception:
        catalog = set()
    if catalog:
        unknown_required = sorted(required - catalog)
        if unknown_required:
            logger.warning(
                "[%s] declared/inferred APIs not present in catalog (dropped): %s",
                task.get("task_id"), unknown_required,
            )
            required -= set(unknown_required)
        unknown_distractor = sorted(declared_distractors - catalog)
        if unknown_distractor:
            logger.warning(
                "[%s] declared distractor APIs not present in catalog (dropped): %s",
                task.get("task_id"), unknown_distractor,
            )
            declared_distractors -= set(unknown_distractor)

    try:
        if distractor_is_auto:
            distractor = list(compute_distractor_skills(
                sorted(required),
                task.get("task_id") or task.get("task_id_ori") or "",
                environment_dir=config.environment_dir,
            ))
        elif distractor_is_absent:
            distractor = []
        else:
            distractor = sorted(declared_distractors - required)
    except Exception:
        distractor = []

    return required, distractor, overlays


def _collect_enabled_apis(args, config) -> "set[str] | None":
    """Union of (required + distractor) APIs across the task(s) this invocation
    will run. Used to start the shared mock stack with only those services up
    instead of all ~101. Returns None when the set can't be determined for every
    task (the safe fallback: run the full catalog, preserving prior behavior).

    A task that this stack serves only ever reaches APIs in its own
    required+distractor set, so the batch-wide union is sufficient for every
    task while still excluding APIs no task references.
    """
    try:
        task_files: list[Path] = []
        if getattr(args, "task", None):
            tf = Path(args.task)
            if tf.exists():
                task_files = [tf]
        else:
            cats = ALL_CATEGORIES if args.category.lower() == "all" else [args.category]
            for c in cats:
                d = TASKS_DIR / c
                if d.is_dir():
                    task_files += sorted(d.glob("*task_*.md"))
        if not task_files:
            return None
        enabled: set[str] = set()
        for tf in task_files:
            t = load_task(tf)
            required, distractor, _ = _resolve_task_apis(t, config)
            enabled |= set(required) | set(distractor)
        return enabled or None
    except Exception as exc:
        logger.warning("Could not resolve per-task API set; mock stack will run "
                       "all APIs. Reason: %s", exc)
        return None


def _augment_task_with_mocks(task: dict, config, mock_env_dict: dict | None) -> None:
    """Populate env_dir / required_apis / env_dict on the task dict so the
    openclaw runner injects API connectors and the shared mock-stack URLs.
    Required/distractor resolution is delegated to `_resolve_task_apis`.
    """
    required, distractor, overlays = _resolve_task_apis(task, config)
    task["env_dir"] = str(config.environment_dir) if config.environment_dir else ""
    task["required_apis"] = sorted(required)
    task["mock_overlays"] = overlays
    task["distractor_apis"] = distractor
    # Expose the shared mock-stack URLs to the task (the full service map; the
    # extra entries are inert env vars for APIs this task doesn't call).
    if mock_env_dict:
        task["env_dict"] = dict(mock_env_dict)
    else:
        task.setdefault("env_dict", {})

    # Skills are sourced from the environment folder's connector catalog
    # (<env>/skills). Required-API connectors are auto-injected separately by
    # inject_api_connectors; explicit/default skills resolve against this path.
    if not task.get("skills_path"):
        task["skills_path"] = str(config.wildclaw_skills_dir) if config.wildclaw_skills_dir else ""
    if config.default_skills:
        existing = [s.strip() for s in (task.get("skills") or "").splitlines() if s.strip()]
        merged = list(dict.fromkeys(existing + list(config.default_skills)))
        task["skills"] = "\n".join(merged)


def _model_type(model: str) -> str:
    """Map a model id to a kensei pod folder name (claude / gpt / sanitized)."""
    m = model.rsplit("/", 1)[-1].lower()
    if m.startswith("claude"):
        return "claude"
    if m.startswith(("gpt", "o1", "o3", "o4")):
        return "gpt"
    return re.sub(r"[^a-z0-9.\-_]", "_", m)


def _claim_run_dir(model_dir: Path) -> tuple[int, Path]:
    """Atomically reserve the next run_N directory.

    ``mkdir(exist_ok=False)`` is atomic on POSIX, so concurrent reps on the same
    model_dir each claim a distinct index instead of racing on a scan-then-mkdir
    (the old ``_next_run_index`` + ``mkdir(exist_ok=True)`` pattern let two reps
    both pick run_1 and clobber one another).
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    i = 1
    while True:
        candidate = model_dir / f"run_{i}"
        try:
            candidate.mkdir(exist_ok=False)   # raises if another rep took it
            return i, candidate
        except FileExistsError:
            i += 1


@contextmanager
def _locked(lock_path: Path):
    """Hold an exclusive advisory lock for the duration of the block.

    ``fcntl.flock`` is advisory and cross-process on the same host — enough to
    serialize the read-modify-write of shared per-model files (pass_summary.json)
    when reps for one (task, model) run in parallel.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _finite_float(v):
    """Return v as a float iff it is a finite real number, else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
        return float(v)
    return None


def _mean_or_none(vals):
    nums = [v for v in vals if v is not None]
    return (sum(nums) / len(nums)) if nums else None


def _pass_summary_entry(run_index: int, scores: dict | None, test_result: dict | None) -> dict:
    """Build one per_run record carrying BOTH scoring channels.

    Channel B (rubric): criteria_* + rubric_reward, from score.json.
    Channel A (pytest): tests_* + test_reward, from the real test_result/ctrf —
        NOT aliased to criteria_* anymore.
    combined_reward mirrors _augment_score_with_combined_rewards; `reward` is the
    authoritative run reward (combined when tests ran, else rubric).
    """
    s = scores or {}
    tr = test_result or {}
    # --- Channel B: rubric (canonical criteria_*, legacy tests_* fallback) ---
    crit_total = int(s.get("criteria_total", s.get("tests_total", 0)) or 0)
    crit_passed = int(s.get("criteria_passed", s.get("tests_passed", 0)) or 0)
    crit_failed = int(s.get("criteria_failed", s.get("tests_failed", 0)) or 0)
    rubric_reward = _finite_float(s.get("rubric_based_reward"))
    if rubric_reward is None:
        rubric_reward = _finite_float(s.get("overall_score"))
    rubric_pct = _finite_float(s.get("rubric_weights_percentage"))
    if rubric_pct is None and rubric_reward is not None:
        rubric_pct = rubric_reward * 100.0
    # --- Channel A: real pytest counts ---
    t_total = int(tr.get("tests_total", 0) or 0)
    t_passed = int(tr.get("tests_passed", 0) or 0)
    t_failed = int(tr.get("tests_failed", 0) or 0)
    t_err = int(tr.get("tests_errored", 0) or 0)
    t_skip = int(tr.get("tests_skipped", 0) or 0)
    test_reward = _finite_float(s.get("test_based_reward"))
    if test_reward is None and t_total > 0:
        test_reward = _finite_float(tr.get("reward"))
    # --- combined ---
    combined = _finite_float(s.get("combined_reward"))
    if combined is None:
        if test_reward is not None and rubric_reward is not None:
            combined = (test_reward + rubric_reward) / 2.0
        elif test_reward is not None:
            combined = test_reward
        else:
            combined = rubric_reward
    authoritative = combined if combined is not None else (rubric_reward or 0.0)
    entry = {
        "run_index": run_index,
        # Channel B — rubric judge
        "criteria_total": crit_total,
        "criteria_passed": crit_passed,
        "criteria_failed": crit_failed,
        "rubric_reward": rubric_reward,
        "rubric_weights_percentage": round(rubric_pct, 2) if rubric_pct is not None else None,
        # Channel A — real pytest
        "tests_total": t_total,
        "tests_passed": t_passed,
        "tests_failed": t_failed,
        "tests_errored": t_err,
        "tests_skipped": t_skip,
        "test_reward": test_reward,
        # authoritative run reward = combined (falls back to rubric when no tests)
        "combined_reward": combined,
        "reward": authoritative,
    }
    # Preserve the last-resort-stub marker so operators + downstream tools can
    # distinguish "grader wrote 0" from "no grader ran; stub emitted by finally".
    if s.get("__last_resort_stub__"):
        entry["__last_resort_stub__"] = True
    return entry


def _pass_summary_doc(model_type: str, per_run: list) -> dict:
    per_run = sorted(per_run, key=lambda r: r["run_index"])
    avg_reward = _mean_or_none([r.get("reward") for r in per_run]) or 0.0
    avg_combined = _mean_or_none([r.get("combined_reward") for r in per_run])
    avg_rubric = _mean_or_none([r.get("rubric_reward") for r in per_run])
    avg_test = _mean_or_none([r.get("test_reward") for r in per_run])
    avg_pct = _mean_or_none([r.get("rubric_weights_percentage") for r in per_run])
    return {
        "model": model_type,
        "runs": len(per_run),
        # average_reward is now the authoritative (combined) mean, not rubric-only
        "average_reward": avg_reward,
        "average_combined_reward": avg_combined,
        "average_rubric_reward": avg_rubric,
        "average_test_reward": avg_test,
        "average_rubric_weights_percentage": round(avg_pct, 2) if avg_pct is not None else None,
        "per_run": per_run,
    }


def _write_pass_summary(model_dir: Path, model_type: str, run_index: int,
                        scores: dict | None = None,
                        test_result: dict | None = None) -> None:
  with _locked(model_dir / ".pass_summary.lock"):
    p = model_dir / "pass_summary.json"
    existing = {}
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    per_run = [r for r in existing.get("per_run", []) if r.get("run_index") != run_index]
    per_run.append(_pass_summary_entry(run_index, scores, test_result))
    p.write_text(json.dumps(_pass_summary_doc(model_type, per_run), indent=2),
                 encoding="utf-8")


def _condense_transcript_for_judge(traj: dict, limit: int | None = None) -> str:
    """Flatten the trajectory messages into a text the judge can read.

    By user policy (2026-06-02): trajectory is NEVER truncated when fed to the
    judge. No per-call args cap, no per-tool-result cap, no overall cap. The
    `limit` kwarg is retained only for API compatibility and is ignored.
    Grading._gather_evidence is responsible for stitching this together with
    deliverables; it also no longer applies an overall cap."""
    out: list[str] = []
    for m in traj.get("messages") or []:
        msg = m.get("message", m) if isinstance(m, dict) else {}
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            if content.strip():
                out.append(f"[{role}] {content.strip()}")
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                out.append(f"[{role}] {b['text'].strip()}")
            elif t == "toolCall":
                args = json.dumps(b.get("arguments", {}))
                out.append(f"[{role}:tool] {b.get('name')} {args}")
            elif t == "toolResult" or role == "toolResult":
                txt = b.get("text") or b.get("content") or ""
                if isinstance(txt, str) and txt.strip():
                    out.append(f"[toolResult] {txt.strip()}")
    return "\n".join(out)


def _project_agent_usage_top_level(agent_usage: Mapping[str, Any] | None) -> dict[str, Any]:
    if not agent_usage:
        # return {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "cost_usd": 0.0}
        return {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0}
    def _int(k: str) -> int:
        v = agent_usage.get(k)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    try:
        cost = float(agent_usage.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cached_input_tokens": _int("cache_read_tokens"),
        "cache_read_tokens": _int("cache_read_tokens"),
        "cache_write_tokens": _int("cache_write_tokens"),
        "cost_usd": round(cost, 6),
    }


def _project_artifact_record(rich: Mapping[str, Any], *, ref_id: str, run_dir: Path) -> dict[str, Any]:
    container_path = str(rich.get("container_path", "") or "")
    path_str = container_path
    if container_path:
        try:
            abs_path = Path(container_path)
            if abs_path.is_absolute():
                try:
                    path_str = str(abs_path.relative_to(run_dir))
                except ValueError:
                    path_str = container_path
        except (OSError, ValueError):
            path_str = container_path
    try:
        sz = int(rich.get("size_bytes", 0) or 0)
    except (TypeError, ValueError):
        sz = 0
    return {
        "ref_id": ref_id,
        "path": path_str,
        "filename": str(rich.get("filename", "") or ""),
        "mime_type": str(rich.get("mime_type", "") or ""),
        "size_bytes": sz,
        "source": "agent_workspace",
    }


def _scan_verifier_artifacts(verifier_dir: Path, run_dir: Path, start_idx: int) -> list[dict[str, Any]]:
    if not verifier_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(verifier_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            sz = 0
        try:
            rel = str(f.relative_to(run_dir))
        except ValueError:
            rel = str(f)
        mime, _ = mimetypes.guess_type(f.name)
        out.append({
            "ref_id": f"artifact_{start_idx + len(out)}",
            "path": rel,
            "filename": f.name,
            "mime_type": mime or "application/octet-stream",
            "size_bytes": sz,
            "source": "agent_workspace",
        })
    return out


# Display-only relabel: the dash form "claude-opus-4-6" is load-bearing and
# MUST stay unchanged in runner.py (OpenClaw 2026.3.11 thinking allowlist) and
# litellm_sidecar.py (adaptive-thinking substring match); renaming either to
# 4.7 silently disables thinking. The agent stamps the dash id into chat.jsonl,
# which flows into the persisted trajectory. The model actually served is the
# opus-4.7 inference profile, so only the user-facing trajectory artifact is
# relabeled here.
_DISPLAY_MODEL_REWRITES = {
    "anthropic/claude-opus-4-6": "anthropic/claude-opus-4.7",
    "claude-opus-4-6": "claude-opus-4.7",
}


def _normalize_display_model(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "model" and isinstance(val, str) and val in _DISPLAY_MODEL_REWRITES:
                obj[key] = _DISPLAY_MODEL_REWRITES[val]
            else:
                _normalize_display_model(val)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_display_model(item)


def _augment_score_with_combined_rewards(scores: dict, result: dict) -> None:
    if not isinstance(scores, dict):
        return
    test_reward: float | None = None
    te = (result or {}).get("test_result") or {}
    if isinstance(te, dict):
        raw_test = te.get("reward")
        if (
            isinstance(raw_test, (int, float))
            and not isinstance(raw_test, bool)
            and math.isfinite(float(raw_test))
            and te.get("tests_total")
        ):
            test_reward = float(raw_test)
    rubric_reward: float | None = None
    raw_rubric = scores.get("overall_score")
    if (
        isinstance(raw_rubric, (int, float))
        and not isinstance(raw_rubric, bool)
        and math.isfinite(float(raw_rubric))
    ):
        rubric_reward = float(raw_rubric)
    if test_reward is not None and rubric_reward is not None:
        combined_reward: float | None = (test_reward + rubric_reward) / 2.0
    elif test_reward is not None:
        combined_reward = test_reward
    elif rubric_reward is not None:
        combined_reward = rubric_reward
    else:
        combined_reward = None
    scores["test_based_reward"] = test_reward
    scores["rubric_based_reward"] = rubric_reward
    scores["combined_reward"] = combined_reward


def _build_trajectory(task: dict, output_dir: Path, task_bundle_dir: Path,
                      model_type: str, run_index: int, result: dict,
                      config: Config | None = None,
                      agent_usage: Mapping[str, Any] | None = None) -> None:
    chat = output_dir / "chat.jsonl"
    if not chat.is_file():
        logger.warning("[%s] no chat.jsonl; skipping trajectory", task.get("task_id"))
        return
    entries: list[dict] = []
    for line in chat.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    st = StoreTask(
        id=task["task_id"], task_id=task["task_id"],
        persona=task.get("persona", "") or "",
        initial_prompt=task.get("initial_prompt") or task.get("prompt") or "",
        task_type=task.get("task_type", "") or "",
        difficulty=task.get("difficulty", "") or "",
        l1=task.get("l1", "") or "", l2=task.get("l2", "") or "",
        system_prompt=task.get("system_prompt", "") or "",
        task_description=task.get("task_description", "") or "",
        rubrics_json=json.dumps(task.get("rubrics") or []),
        test_code=task.get("test_code", "") or "",
        test_weights=task.get("test_weights", "") or "",
        golden_trajectory=task.get("golden_trajectory", "") or "",
        extra={
            "required_apis": list(task.get("required_apis") or []),
            "distractor_apis": list(task.get("distractor_apis") or []),
        },
    )
    artifacts_dir = task_bundle_dir / "artifacts"

    def _media(msgs, tid):
        return replace_inline_media_with_files(msgs, tid, artifacts_dir)

    traj = build_trajectory_from_jsonl(
        st, entries, attachments=task.get("attachments") or [], media_handler=_media,
        s3_bucket=(config.s3_bucket if config else ""),
        s3_prefix=(config.s3_prefix if config else ""),
        s3_region=(config.s3_region if config else ""),
        usage_top_level=_project_agent_usage_top_level(agent_usage),
        workspace_root=output_dir / "task_output" / "workspace_full",
    )

    # Project rich S3-upload records to the reference's trimmed schema
    # {ref_id,path,filename,mime_type,size_bytes,source:'agent_workspace'}
    # and append verifier artifacts (NOT uploaded). S3 URLs are implied by
    # bucket+prefix config — not carried in JSON. Matches kensei2 reference.
    artifacts_list: list[dict[str, Any]] = list(traj.get("output_artifacts") or [])
    seen_paths: set[str] = {r.get("path", "") for r in artifacts_list if isinstance(r, dict)}
    next_idx = len(artifacts_list)
    if config and config.s3_bucket:
        try:
            from src.utils.s3_artifacts import upload_output_artifacts
            s3_records = upload_output_artifacts(
                config, task["task_id"],
                workspace_roots=[
                    output_dir / "task_output" / "workspace",
                    output_dir / "task_output" / "workspace_full",
                ],
            )
            for rich in s3_records or []:
                proj = _project_artifact_record(rich, ref_id=f"artifact_{next_idx}", run_dir=output_dir)
                if proj["path"] in seen_paths:
                    continue
                artifacts_list.append(proj)
                seen_paths.add(proj["path"])
                next_idx += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] S3 artifact upload pipeline error: %s", task["task_id"], exc)

    verifier_records = _scan_verifier_artifacts(
        output_dir / "task_output" / "logs" / "verifier", output_dir, next_idx,
    )
    for rec in verifier_records:
        if rec["path"] in seen_paths:
            continue
        artifacts_list.append(rec)
        seen_paths.add(rec["path"])
        next_idx += 1
    traj["output_artifacts"] = artifacts_list

    _normalize_display_model(traj)

    (output_dir / "output.json").write_text(
        json.dumps(traj, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    n_thinking = sum(
        1
        for m in traj.get("messages") or []
        if isinstance(m, dict)
        for block in (m.get("message", m).get("content") or []) if isinstance(m.get("message", m), dict)
        if isinstance(block, dict) and block.get("type") == "thinking"
    )
    logger.info(
        "[%s] Trajectory written: %s (thinking_blocks=%d)",
        task["task_id"], output_dir / "output.json", n_thinking,
    )

    # Rubric grading for native tasks: they carry no `automated_checks`, so
    # without this they always score reward:0.0 / tests_total:0 regardless of
    # how well the agent did. If the task has rubrics and wasn't already scored,
    # judge the collected deliverables + transcript with the LLM judge.
    scores = result.get("scores") or {}
    rubrics = task.get("rubrics") or []
    if rubrics and "overall_score" not in scores and not result.get("error"):
        # Judge reads agent-produced files from `task_output/artifacts/` (b99
        # canonical, baseline-diff agent-touched files only). The legacy
        # `workspace/results/` path was deleted in b99 because agents never
        # wrote there. Fall back to `workspace_full/` (forensic full copy)
        # only when artifacts/ is missing or empty so older trajectories and
        # backends that don't snapshot still get judged.
        artifacts_dir = output_dir / "task_output" / "artifacts"
        results_dir = artifacts_dir
        try:
            if not artifacts_dir.exists() or not any(artifacts_dir.iterdir()):
                results_dir = output_dir / "task_output" / "workspace_full"
        except OSError:
            results_dir = output_dir / "task_output" / "workspace_full"
        try:
            from src.utils.grading import grade_with_rubric
            transcript_text = _condense_transcript_for_judge(traj)
            scores = grade_with_rubric(
                rubrics,
                task.get("task_description") or task.get("initial_prompt") or "",
                results_dir,
                transcript_text=transcript_text,
                use_council=task.get("__use_judge_council__"),
            )
            result["scores"] = scores
            if isinstance(scores, dict) and scores.get("usage"):
                result["__judge_usage__"] = dict(scores["usage"])
            _augment_score_with_combined_rewards(scores, result)
            (output_dir / "score.json").write_text(
                json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("[%s] Rubric judged: overall=%.3f (%.2f%%) — %d/%d criteria passed, model=%s",
                        task["task_id"], scores.get("overall_score", 0.0),
                        scores.get("rubric_weights_percentage",
                                   scores.get("overall_score", 0.0) * 100.0),
                        scores.get("criteria_passed", scores.get("tests_passed", 0)),
                        scores.get("criteria_total", scores.get("tests_total", 0)),
                        scores.get("judge_model", "?"))
        except Exception as exc:
            # Write a stub score.json so the failure is visible on disk.
            # Without this, a swallowed exception leaves no trace except a
            # WARNING in stdout, and the operator can't tell whether judging
            # was skipped, crashed, or quietly returned empty.
            logger.warning("[%s] rubric grading failed: %s", task.get("task_id"), exc, exc_info=True)
            try:
                stub = {
                    "overall_score": None,
                    "rubric_weights_percentage": None,
                    "criteria_total": len(rubrics),
                    "criteria_passed": 0,
                    "criteria_failed": 0,
                    "criteria_abstained": len(rubrics),
                    "judge_model": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "results_dir": str(results_dir),
                    "__last_resort_stub__": True,
                }
                # _augment_score_with_combined_rewards can raise on malformed
                # result dicts; its failure must not sink the stub write below.
                try:
                    _augment_score_with_combined_rewards(stub, result)
                except Exception as aug_exc:
                    logger.warning("[%s] _augment_score_with_combined_rewards failed on stub: %s",
                                   task.get("task_id"), aug_exc, exc_info=True)
                (output_dir / "score.json").write_text(
                    json.dumps(stub, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            except Exception as stub_exc:
                # Broadened from `except OSError`: json.dumps ValueError on NaN/Inf
                # and TypeError on non-serializable score fields must not silently
                # escape (they were the primary silent-swallow path).
                logger.error("[%s] stub score.json write failed: %s",
                             task.get("task_id"), stub_exc, exc_info=True)

    _write_pass_summary(task_bundle_dir / "trajectories" / model_type, model_type,
                        run_index, scores=result.get("scores") or {},
                        test_result=result.get("test_result") or {})

    # Copy the bundle inputs to the task root (kensei out/<task_id>/ layout).
    import shutil
    td = task.get("task_dir", "")
    if td:
        for fn in ("prompt.txt", "rubric.json"):
            src = Path(td) / fn
            if src.is_file():
                try:
                    shutil.copy2(src, task_bundle_dir / fn)
                except OSError:
                    pass

    if config is not None:
        try:
            tr = result.get("test_result") or {}
            src = tr if tr else scores
            # When src is a pytest test_result, the tests_* keys are authoritative.
            # When src is a rubric score (no real pytest ran), canonical keys are
            # criteria_*; fall back to deprecated tests_* aliases for legacy data.
            # `canonical_reward` carries the run's authoritative reward (combined,
            # else rubric overall) so the harbor bundler can use it when no real
            # pytest per-test results exist — otherwise the weighted pytest scorer
            # matches the real weight keys against zero results and emits a
            # spurious 0 (darren-weston 2026-06-15: 15/17 criteria passed = 0.8378
            # but reward.txt/ctrf showed 0).
            canonical_reward = None
            if isinstance(scores, dict):
                canonical_reward = scores.get("combined_reward")
                if canonical_reward is None:
                    canonical_reward = scores.get("overall_score")
            tr_meta = {
                "tests_total": int(src.get("tests_total", src.get("criteria_total", 0)) or 0),
                "tests_passed": int(src.get("tests_passed", src.get("criteria_passed", 0)) or 0),
                "tests_failed": int(src.get("tests_failed", src.get("criteria_failed", 0)) or 0),
                "tests_errored": int(src.get("tests_errored", 0) or 0),
                "tests_skipped": int(src.get("tests_skipped", 0) or 0),
                "test_scores": src.get("test_scores", "") or "",
                "test_output": src.get("test_output", "") or "",
                "test_code": task.get("test_code", "") or "",
                "canonical_reward": canonical_reward,
            }
            entry = dict(traj)
            entry["__test_result__"] = tr_meta
            entry["__run_index__"] = run_index
            # Route through script/repackage_to_bundle.py (stdlib-only, used by
            # run.sh and deliver.sh). Harbor's in-process write_bundle dropped
            # admin_plane.py/_mutable_store.py and sourced persona/artifacts
            # from config.environment_dir instead of input/<task>/.
            #
            # Input-root resolution: tasks may live under different input
            # collections (input/, input_v2/, input_internal/, ...). The
            # loaded task spec carries the absolute path it was loaded from
            # as task["task_dir"] (src/utils/task_parser.py:390). Use its
            # parent as --input-root and its basename as --persona so we
            # always match the on-disk dirname exactly, regardless of which
            # input collection the task was loaded from. KENSEI_INPUT_ROOT
            # remains as an override escape hatch; falls back to "input"
            # only when task_dir is unset (legacy/unparented tasks).

            # Stage the post-overlay mock environment into
            # output/<backend>/<task>/data/environment/<api>/** BEFORE the
            # bundler runs. The bundler's copytree at
            # script/repackage_to_bundle.py:783 then copies this whole
            # data/ tree into output_bundle/<task>/data/, so both output/
            # and output_bundle/ end up with identical api dirs.
            # _overlay_manifest.json is intentionally written here and
            # stripped by the bundler's ignore_patterns so it stays in
            # output/ only (user contract m0150). See
            # src/utils/env_overlay_snapshot.py for the merge semantics
            # and why this matches the running container's view of
            # /opt/mocks/<api>/ exactly.
            env_baseline = config.environment_dir if config is not None else None
            if env_baseline:
                try:
                    env_dest = task_bundle_dir / "data" / "environment"
                    stage_environment_with_overlays(
                        Path(env_baseline),
                        task.get("mock_overlays") or {},
                        env_dest,
                        write_manifest=True,
                    )
                    logger.info(
                        "[%s] Staged data/environment/ with %d overlay api(s)",
                        task["task_id"],
                        len(task.get("mock_overlays") or {}),
                    )
                except Exception as exc:
                    logger.warning(
                        "[%s] Env overlay staging failed: %s",
                        task["task_id"], exc,
                    )

            import subprocess
            source_root = task_bundle_dir.parent
            bundle_root = os.environ.get(
                "KENSEI_BUNDLE_ROOT",
                str(Path("output_bundle")),
            )
            repo_root = Path(__file__).resolve().parent.parent
            script_path = repo_root / "script" / "repackage_to_bundle.py"

            td = task.get("task_dir") or ""
            if td and Path(td).is_dir():
                input_root = str(Path(td).parent)
                persona_arg = Path(td).name
            else:
                input_root = os.environ.get("KENSEI_INPUT_ROOT", "input")
                persona_arg = task["task_id"]

            # Stage the 5 mirror artifacts (harness env .py files, persona/,
            # artifacts/inputs/files/, tests/, solution/) into the SOURCE
            # output/<task>/data/ tree FIRST so it matches bundle/data/
            # modulo the 3 by-design strips (_overlay_manifest.json,
            # _meta.json, skills/*/_meta.json). The bundler's subsequent
            # shutil.copytree(task_dir/'data', bundle/'data', ...) will then
            # propagate the staged tree into the bundle naturally. See
            # `stage_output_data` in script/repackage_to_bundle.py for the
            # parity contract. Fail-soft: a staging error must never block
            # the bundle write -- the bundler also has its own staging path.
            stage_cmd = [
                sys.executable,
                str(script_path),
                "--source-root", str(source_root),
                "--input-root", str(input_root),
                "--persona", persona_arg,
                "--stage-output-data",
                "--verbose",
            ]
            try:
                stage_completed = subprocess.run(
                    stage_cmd, cwd=str(repo_root),
                    capture_output=True, text=True, check=False,
                )
                if stage_completed.returncode == 0:
                    logger.info(
                        "[%s] Output-side data staged (parity with bundle/data/)",
                        task["task_id"],
                    )
                    if stage_completed.stderr.strip():
                        logger.warning(
                            "[%s] stage-output-data stderr:\n%s",
                            task["task_id"], stage_completed.stderr,
                        )
                else:
                    logger.warning(
                        "[%s] stage-output-data exited %d\nstdout:\n%s\nstderr:\n%s",
                        task["task_id"], stage_completed.returncode,
                        stage_completed.stdout, stage_completed.stderr,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] stage-output-data invocation failed: %s",
                    task["task_id"], exc,
                )

            cmd = [
                sys.executable,
                str(script_path),
                "--source-root", str(source_root),
                "--dest-root", str(bundle_root),
                "--input-root", str(input_root),
                "--persona", persona_arg,
                # Verbose so silent skips (no input match, missing
                # ground-truth source, missing harness env files) surface
                # in run logs instead of disappearing into capture_output.
                "--verbose",
            ]
            try:
                completed = subprocess.run(
                    cmd, cwd=str(repo_root),
                    capture_output=True, text=True, check=False,
                )
                if completed.returncode == 0:
                    logger.info(
                        "[%s] Bundle written via repackage_to_bundle: %s/%s "
                        "(input_root=%s persona=%s)",
                        task["task_id"], bundle_root, task["task_id"],
                        input_root, persona_arg,
                    )
                    # Surface stderr even on success: the bundler emits
                    # warnings for "no input dir matched", missing
                    # ground-truth source, and missing harness env files
                    # without bumping the exit code. Without this, those
                    # warnings would be swallowed by capture_output.
                    if completed.stderr.strip():
                        logger.warning(
                            "[%s] repackage_to_bundle stderr:\n%s",
                            task["task_id"], completed.stderr,
                        )
                else:
                    logger.warning(
                        "[%s] repackage_to_bundle.py exited %d\nstdout:\n%s\nstderr:\n%s",
                        task["task_id"], completed.returncode,
                        completed.stdout, completed.stderr,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] repackage_to_bundle invocation failed: %s",
                    task["task_id"], exc,
                )
        except Exception as exc:
            logger.warning("[%s] Auto-bundle preparation failed: %s", task["task_id"], exc)


def run_single_task(
    task: dict,
    model: str,
    backend: BaseAgent,
    output_root: Path,
    lobster: dict | None = None,
    thinking: str | None = None,
    models_config: dict | None = None,
    config=None,
    mock_env_dict: dict | None = None,
    network: str = "",
    enable_mock_stack: bool = False,
    generate_tests: bool = False,
    testgen_max_attempts: int = 3,
    execute_tests: bool = False,
    testexec_timeout: int = 600,
) -> dict:
    """
    Execute a single task, returning a {"task_id", "scores", "error"} dict.
    Thread-safe: each task has its own container name and log directory.

    lobster: optional dict with keys "name", "workspace", "env".
    config: optional Config (enables mock-API connector injection + env URLs).
    mock_env_dict: {ENV_VAR: http://<mock-container>:<port>} for the shared stack.
    network: docker network name the mock/agent containers share (per-task mocks).
    enable_mock_stack: when True and the task ships mock_data overlays, spin a
        per-task mock container serving THIS task's CSVs instead of the shared
        baseline (so the agent sees the task's own dataset, not course_001..005).
    """
    task_id_ori     = task["task_id"]
    timeout_seconds = task["timeout_seconds"]

    # Native/yaml tasks carry no workspace_path; stage one from their attachments
    # so task graders/solutions are never mounted into the agent container.
    workspace_path = task.get("workspace_path") or ""
    if not workspace_path and config is not None:
        workspace_path = _stage_native_workspace(task, config)
        task["workspace_path"] = workspace_path

    # Augment the task with mock-API wiring the openclaw runner consumes:
    #   env_dir       -> source of <api>-connector skill dirs + API_DOCUMENTATION.md
    #   required_apis -> which connectors to inject (prompt keywords + mock_data/)
    #   env_dict      -> {ENV_VAR: url} for the shared mock stack (container env)
    #   mock_overlays -> {api: {filename: host_path}} for per-task data isolation
    if config is not None:
        _augment_task_with_mocks(task, config, mock_env_dict)

    if (task.get("test_code") or "").strip():
        # Task ships its own test suite (input/<task>/test_outputs.py +
        # test_weights.json, loaded by task_parser._load_provided_tests) — the
        # gate below is skipped, so the LLM test generator never runs.
        logger.info(
            "[%s] using task-provided test suite (%dch code, weights=%s) — skipping LLM test generation",
            task_id_ori, len(task["test_code"]),
            "yes" if (task.get("test_weights") or "").strip() else "none",
        )

    if generate_tests and config is not None and not (task.get("test_code") or "").strip():
        force_testgen = bool(task.get("__force_testgen__"))
        cached_tests_dir = output_root / task_id_ori / "data" / "tests"
        cached_code_path = cached_tests_dir / "test_outputs.py"
        cached_weights_path = cached_tests_dir / "test_weights.json"
        cached_key_path = cached_tests_dir / "cache_key.txt"
        current_key = _compute_testgen_cache_key(task)
        cached_code = ""
        cached_weights = ""
        cache_key_match = True
        if not force_testgen and cached_code_path.is_file() and cached_weights_path.is_file():
            try:
                cached_code = cached_code_path.read_text(encoding="utf-8")
                cached_weights = cached_weights_path.read_text(encoding="utf-8")
            except OSError:
                cached_code = ""
                cached_weights = ""
            if current_key:
                try:
                    cached_key = cached_key_path.read_text(encoding="utf-8").strip() if cached_key_path.is_file() else ""
                except OSError:
                    cached_key = ""
                if cached_key != current_key:
                    cache_key_match = False
                    logger.info(
                        "[%s] testgen cache invalidated: key changed (was=%s now=%s); regenerating",
                        task_id_ori, cached_key or "<absent>", current_key,
                    )
        if cache_key_match and cached_code.strip() and cached_weights.strip() and cached_weights.strip() != "{}":
            task["test_code"] = cached_code
            task["test_weights"] = cached_weights
            task["__testgen_usage__"] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0}
            logger.info(
                "[%s] testgen reused from %s (%dch code, %dch weights, key=%s)",
                task_id_ori, cached_tests_dir, len(cached_code), len(cached_weights), current_key or "<none>",
            )
        else:
            if force_testgen:
                logger.info("[%s] testgen cache bypassed via --force-testgen", task_id_ori)
            try:
                tg = generate_task_tests(
                    task, config,
                    environment_dir=config.environment_dir,
                    max_attempts=testgen_max_attempts,
                )
                task["test_code"] = tg.test_code
                task["test_weights"] = tg.test_weights_json
                task["__testgen_usage__"] = dict(tg.usage)
                if current_key:
                    try:
                        cached_tests_dir.mkdir(parents=True, exist_ok=True)
                        cached_key_path.write_text(current_key, encoding="utf-8")
                    except OSError as exc:
                        logger.debug("[%s] testgen cache_key write failed: %s", task_id_ori, exc)
                logger.info(
                    "[%s] testgen done: %dch code, %d weights, %d attempts, fallback=%s, key=%s",
                    task_id_ori, len(tg.test_code), len(tg.test_weights),
                    tg.attempts, tg.used_fallback, current_key or "<none>",
                )
            except Exception as exc:
                logger.warning("[%s] testgen failed (continuing without): %s", task_id_ori, exc)

    # When the task ships its own mock_data, serve it from a dedicated per-task
    # mock container and point the agent at THAT instead of the shared baseline.
    # Tasks without overlays keep the shared stack URLs unchanged (no-op here).
    task_mock_container: str | None = None
    drift_info: dict = {}
    if enable_mock_stack and network and task.get("mock_overlays"):
        env_dir_for_mocks = config.environment_dir if config is not None else None
        task_mock_env, task_mock_container, drift_info = _start_task_mock_stack(
            task, network, env_dir_for_mocks,
        )
        if task_mock_env:
            merged = dict(task.get("env_dict") or {})
            merged.update(task_mock_env)
            task["env_dict"] = merged

    # Inject only the *_API_URL env vars for APIs this task actually runs
    # (required + distractor + overlays). The full ~101-entry service map is
    # otherwise handed to the agent as inert pointers to dead ports; trimming
    # keeps the container env (and the launch log) to just the live services.
    # Connectors only exist for the enabled set, so this is cosmetic — no
    # behavior change. Falls back to the full map if resolution fails.
    if task.get("env_dict") and config is not None:
        enabled_names = (
            set(task.get("required_apis") or [])
            | set(task.get("distractor_apis") or [])
            | set((task.get("mock_overlays") or {}).keys())
        )
        if enabled_names:
            try:
                env_var_by_name = {
                    s["name"]: s.get("env_var_name")
                    for s in discover_services(config.environment_dir)
                }
                keep = {env_var_by_name.get(n) for n in enabled_names}
                keep.discard(None)
                filtered = {k: v for k, v in task["env_dict"].items() if k in keep}
                if filtered:
                    task["env_dict"] = filtered
            except Exception:
                pass

    prompt          = task["prompt"]
    system_prompt = f"You are an expert in a restricted, non-interactive environment. Solve the task efficiently before the timeout ({timeout_seconds}s). Run all processes in the foreground without user input or background services. Provide a complete, functional solution in a single pass with no placeholders. \n"
    prompt = system_prompt + prompt

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    run_id = uuid.uuid4().hex[:6]
    _m = re.match(r"(\d+)_.*?(task_\d+)", task_id_ori)
    short_task_id = f"{_m.group(1)}_{_m.group(2)}" if _m else task_id_ori
    short_model = re.sub(r'[^a-zA-Z0-9.\-_]', '_', model.rsplit('/', 1)[-1])
    lobster_prefix = f"{lobster['name']}_" if lobster else ""
    suffix = f"{lobster_prefix}{short_model}_{timestamp}_{run_id}"
    task_id = f"{short_task_id}_{lobster_prefix}{short_model}_{timestamp}_{run_id}"
    # Docker container names must match [a-zA-Z0-9][a-zA-Z0-9_.-]*; task ids
    # built from on-disk directory names can contain spaces or other invalid
    # chars (e.g. macOS ' 2' duplicate suffix). Coerce here so `docker run
    # --name` cannot fail downstream and silently zero the score.
    task_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", task_id)
    if not task_id or not re.match(r"[a-zA-Z0-9]", task_id[0]):
        task_id = f"t_{task_id}"

    # kensei layout: out/<task_id>/trajectories/<model>/run_<n>/
    model_type = _model_type(model)
    task_bundle_dir = output_root / task_id_ori
    model_dir = task_bundle_dir / "trajectories" / model_type
    run_index, output_dir = _claim_run_dir(model_dir)

    result = {"task_id": task_id, "scores": {}, "error": None}

    gateway_proc = None
    agent_proc = None
    elapsed_time = float(timeout_seconds)
    drift_director = _start_drift_director(task, drift_info, output_dir) if drift_info else None
    mock_health_logger = _start_mock_health_logger(task, task_id, output_dir)

    try:
        execution = backend.run_task(
            AgentTaskSpec(
                task_id=task_id,
                task=task,
                workspace_path=workspace_path,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
                output_dir=output_dir,
                model=model,
                thinking=thinking,
                models_config=models_config,
                lobster=lobster,
            )
        )
        gateway_proc = execution.gateway_proc
        agent_proc = execution.agent_proc
        elapsed_time = execution.elapsed_time
        if execution.error:
            result["error"] = execution.error
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("[%s] Unexpected backend error: %s", task_id, exc)

    finally:
        grading_transcript_path = backend.transcript_container_path
        grade_on_error = isinstance(backend, (CodexAgent, ClaudeCodeAgent))
        should_grade = task.get("automated_checks") and (
            not result.get("error") or grade_on_error
        )
        if should_grade:
            try:
                grading_transcript_path = backend.prepare_grading_transcript(task_id)
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to prepare grading transcript, fallback to %s: %s",
                    task_id,
                    grading_transcript_path,
                    exc,
                )

        result = grade_the_task(
            task_id,
            workspace_path,
            output_dir,
            task,
            result,
            lobster.get("env") if lobster else None,
            transcript_container_path=grading_transcript_path,
            grade_on_error=grade_on_error,
            write_error_score_on_failure=grade_on_error,
        )
        usage = backend.collect_usage(
            task_id=task_id,
            output_dir=output_dir,
            elapsed_time=elapsed_time,
        )

        try:
            collect_task_output(
                task_id,
                output_dir,
                include_workspace_changes=isinstance(backend, (CodexAgent, ClaudeCodeAgent)),
            )
        except Exception as exc:
            logger.warning("[%s] Failed to collect task output: %s", task_id, exc)

        startup_failed = isinstance(result.get("error"), str) and "Container startup failed" in result["error"]
        if startup_failed:
            logger.error(
                "[%s] Agent container never started; skipping test execution to avoid grading an empty workspace. Error: %s",
                task_id, result["error"],
            )
        if execute_tests and task.get("test_code") and not startup_failed:
            try:
                from src.utils.test_executor import execute_tests as _exec_tests
                ws = output_dir / "task_output" / "workspace_full"
                testexec_env = dict(mock_env_dict or {})
                testexec_env.update(task.get("env_dict") or {})
                te = _exec_tests(
                    test_code=task["test_code"],
                    test_weights_json=task.get("test_weights") or "{}",
                    workspace_dir=ws,
                    mock_env_dict=testexec_env,
                    network=network or None,
                    image=getattr(config, "docker_image", "wildclawbench-ubuntu:v1.3") if config else "wildclawbench-ubuntu:v1.3",
                    timeout=testexec_timeout,
                )
                result["test_result"] = te
                verifier_dir = output_dir / "task_output" / "logs" / "verifier"
                verifier_dir.mkdir(parents=True, exist_ok=True)
                (verifier_dir / "reward.txt").write_text(f"{te['reward']:.6f}\n", encoding="utf-8")
                from src.utils.harbor.ctrf import build_ctrf
                ctrf = build_ctrf(
                    tests_total=te["tests_total"],
                    tests_passed=te["tests_passed"],
                    tests_failed=te["tests_failed"],
                    tests_errored=te["tests_errored"],
                    test_scores_json=te.get("test_scores", "{}"),
                    tests_skipped=te.get("tests_skipped", 0),
                    reward=te.get("reward"),
                )
                (verifier_dir / "ctrf.json").write_text(
                    json.dumps(ctrf, indent=2, ensure_ascii=False), encoding="utf-8")
                _fn_outs = te.get("test_function_outputs") or "{}"
                (verifier_dir / "test_function_outputs.json").write_text(
                    _fn_outs if isinstance(_fn_outs, str) else json.dumps(_fn_outs, indent=2),
                    encoding="utf-8")
                (verifier_dir / "test_output.log").write_text(
                    te.get("test_output", "") or "", encoding="utf-8")
                # Mirror the test sources alongside the verifier outputs so a
                # consumer reading `logs/verifier/` has BOTH the inputs (what
                # we ran) and the outputs (what we got). Symmetrical with the
                # bundle-side mirror in `script/repackage_to_bundle.py`
                # `_stage_verifier_test_sources()`. The bundle copies these
                # verbatim via `copy_verifier_logs` so the bundle inherits
                # them automatically; we still emit them on the bundle side
                # too so manual `repackage_to_bundle.py` runs against an
                # older output tree backfill cleanly.
                try:
                    (verifier_dir / "test_outputs.py").write_text(
                        task.get("test_code") or "", encoding="utf-8")
                    (verifier_dir / "test_weights.json").write_text(
                        task.get("test_weights") or "{}", encoding="utf-8")
                    from src.utils.harbor.test_sh import generate_harbor_test_sh
                    (verifier_dir / "test.sh").write_text(
                        generate_harbor_test_sh(), encoding="utf-8")
                except Exception as exc:
                    logger.warning("[%s] verifier test sources mirror failed: %s", task_id, exc)
                err_suffix = ""
                if te["tests_total"] == 0 and te.get("error"):
                    err_suffix = f" ERROR={te['error']}"
                logger.info(
                    "[%s] Tests executed: %d/%d passed reward=%.3f (%dms)%s",
                    task_id, te["tests_passed"], te["tests_total"],
                    te["reward"], te["duration_execution_ms"], err_suffix,
                )
            except Exception as exc:
                logger.warning("[%s] test execution failed: %s", task_id, exc)

        # Build the schema-1.0.0 trajectory (output.json) + pass_summary.json,
        # matching the kensei out/<task_id>/trajectories/<model>/run_<n>/ layout.
        try:
            _build_trajectory(task, output_dir, task_bundle_dir, model_type, run_index, result, config=config, agent_usage=usage)
        except Exception as exc:
            # exc_info=True is load-bearing: this branch hides missing-score.json
            # root causes (rubric-block failures, output.json write races). Do not remove.
            logger.warning("[%s] trajectory build failed: %s", task_id, exc, exc_info=True)

        result = save_usage(
            output_dir, result, usage, task_id,
            testgen_usage=task.get("__testgen_usage__"),
            judge_usage=result.get("__judge_usage__"),
            preflight_usage=usage.get("__preflight__"),
        )

        if gateway_proc is not None:
            try:
                gateway_proc.terminate()
            except Exception:
                pass
        elif backend.expects_gateway:
            logger.warning("[%s] Gateway not started, task incomplete - likely missing required result files, check %s", task_id, output_dir)

        for _proc in [gateway_proc, agent_proc]:
            if _proc is not None:
                try:
                    close_proc_log(_proc)
                except Exception:
                    pass

        remove_container(task_id)
        logger.info("[%s] Container cleaned up", task_id)

        if drift_director is not None:
            try:
                drift_director.stop()
                drift_director.join(timeout=5.0)
                logger.info("[%s] Drift director stopped", task_id)
            except Exception as exc:
                logger.warning("[%s] Drift director shutdown failed: %s", task_id, exc)

        if mock_health_logger is not None:
            try:
                mock_health_logger.stop()
                mock_health_logger.join(timeout=5.0)
            except Exception as exc:
                logger.warning("[%s] Mock health logger shutdown failed: %s", task_id, exc)

        if task_mock_container:
            try:
                from src.utils.mock_stack import stop_mock_stack
                stop_mock_stack(task_mock_container)
                logger.info("[%s] Per-task mock stack %s cleaned up",
                            task_id, task_mock_container)
            except Exception as exc:
                logger.warning("[%s] Per-task mock stack cleanup failed: %s", task_id, exc)

        # Last-resort score.json invariant guard. Every claimed run_N/ dir MUST have
        # a score.json so downstream aggregators (script/*, regrade.py) never see a
        # phantom run. If grade_the_task and the rubric block both missed, drop a
        # sentinel stub carrying `__last_resort_stub__: True` so aggregators can
        # distinguish "graded 0" from "never scored". overall_score is null (not 0.0)
        # for the same reason. Fires only when nothing else wrote — no clobber.
        try:
            score_path = output_dir / "score.json"
            if not score_path.exists():
                last_resort = {
                    "overall_score": None,
                    "rubric_weights_percentage": None,
                    "criteria_total": 0,
                    "criteria_passed": 0,
                    "criteria_failed": 0,
                    "criteria_abstained": 0,
                    "judge_model": None,
                    "error": result.get("error") or "score.json missing after finally; no grader wrote it",
                    "source": "run_single_task last-resort stub",
                    "task_id": task_id,
                    "__last_resort_stub__": True,
                }
                score_path.write_text(
                    json.dumps(last_resort, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                logger.warning(
                    "[%s] score.json was missing after finally; wrote last-resort stub at %s",
                    task_id, score_path,
                )
        except Exception as exc:
            logger.error("[%s] last-resort score.json write failed: %s", task_id, exc, exc_info=True)

    return result


LITELLM_MODEL_IDS = {"claude-opus-4.7", "gpt-5.5"}


def _run_cleanups(cleanups: list) -> None:
    """Run registered teardown callables in reverse order, swallowing errors."""
    for fn in reversed(cleanups):
        try:
            fn()
        except Exception as exc:
            logger.warning("cleanup error: %s", exc)
    cleanups.clear()


def _setup_litellm_and_mocks(args, config: Config, cleanups: list,
                             mock_enabled_apis: "set[str] | None" = None):
    """For the openclaw backend, optionally bring up a per-batch shared LiteLLM
    sidecar + docker network (+ mock-API stack). Returns
    (use_litellm, litellm_yaml, network, sidecar, mock_env_dict, usage_log_path).
    Registers teardown callables onto `cleanups`. Falls back gracefully to
    OpenRouter.

    SHARED-INFRA SHORT-CIRCUIT
    --------------------------
    When WCB_SHARED_NETWORK + WCB_SHARED_SIDECAR are set (by
    `script/run.sh::bootstrap_shared_sidecar` calling
    `eval/bootstrap_sidecar.py` once per batch), this function REUSES the
    bash-owned network + sidecar instead of creating per-rep ones. Skipped:
    `pull_litellm_image` / `ensure_litellm_headroom_image` /
    `create_network` / writing the litellm yaml / `start_litellm` /
    `wait_for_litellm_healthy` / `verify_litellm_upstream_reachable`.
    `cleanups[]` does NOT register teardown for these — bash owns the
    lifecycle via EXIT/INT/TERM trap. Per-task mock stack lifecycle is
    UNCHANGED (still per-rep at `_start_task_mock_stack`); only the
    batch-level sidecar+network become shared. See
    `docs/reps-failure-diagnosis.md` §B1-B9 for the failure modes this
    short-circuit eliminates."""
    use_litellm = args.litellm if args.litellm is not None else config.litellm_enabled()
    if not use_litellm:
        return False, "", "", "", {}, ""

    shared_network = os.environ.get("WCB_SHARED_NETWORK", "").strip()
    shared_sidecar = os.environ.get("WCB_SHARED_SIDECAR", "").strip()
    shared_usage_log = os.environ.get("WCB_SHARED_SIDECAR_USAGE_LOG", "").strip()
    shared_yaml_path = os.environ.get("WCB_SHARED_SIDECAR_YAML", "").strip()
    shared_cc_bridge = os.environ.get("WCB_SHARED_CC_BRIDGE", "").strip()
    shared_cc_bridge_url = os.environ.get("WCB_SHARED_CC_BRIDGE_URL", "").strip()
    shared_mode = bool(shared_network and shared_sidecar)

    use_oauth = bool(getattr(args, "use_claude_oauth", None)) or (
        config.use_claude_oauth and bool(config.cc_account_pool)
    )

    import uuid as _uuid
    batch_id = _uuid.uuid4().hex[:12]
    if shared_mode:
        network = shared_network
        sidecar = shared_sidecar
    else:
        network = f"k3net-{batch_id}"
        sidecar = f"ll-{batch_id}"

    cc_bridge_name = ""
    cc_bridge_url = ""
    if use_oauth:
        if shared_mode and shared_cc_bridge and shared_cc_bridge_url:
            cc_bridge_name = shared_cc_bridge
            cc_bridge_url = shared_cc_bridge_url
        else:
            cc_bridge_name = f"wcbsh-cc-bridge-{batch_id}"
            cc_bridge_url = f"http://{cc_bridge_name}:{CC_BRIDGE_INTERNAL_PORT}"

    _agent_headroom = (os.environ.get("KENSEI_AGENT_HEADROOM_ENABLED", "").strip().lower()
                       in ("1", "true", "yes", "on"))
    litellm_yaml = build_litellm_config_yaml(
        bedrock_sonnet_arn=config.bedrock_sonnet_arn if config.aws_bearer_token else "",
        bedrock_arn=config.bedrock_inference_arn if config.aws_bearer_token else "",
        aws_region=config.bedrock_region,
        openai_api_key=config.openai_api_key,
        openai_whisper_api_key=config.openai_whisper_api_key,
        enable_usage_callback=True,
        enable_headroom_callback=_agent_headroom,
        anthropic_api_key=config.anthropic_api_key,
        use_claude_oauth=use_oauth,
        bridge_url=cc_bridge_url,
        enable_oauth_usage_callback=use_oauth,
    )
    if not litellm_yaml:
        raise RuntimeError(
            "LiteLLM requested but no Bedrock/OpenAI creds resolved. "
            "Strict mode: refusing to silently downgrade to OpenRouter."
        )

    if not shared_mode:
        pull_litellm_image()
        if _agent_headroom:
            ensure_litellm_headroom_image()
        create_network(network)
        cleanups.append(lambda: remove_network(network))

    if use_oauth and not (shared_mode and shared_cc_bridge):
        pool_paths = [p.strip() for p in config.cc_account_pool.split(":") if p.strip()]
        pool_dirs = {os.path.dirname(os.path.abspath(p)) for p in pool_paths if os.path.isfile(p)}
        if not pool_dirs:
            raise RuntimeError(
                f"OAuth pool spec {config.cc_account_pool!r} resolved to zero readable files. "
                f"Point WCB_CC_ACCOUNT_POOL at existing OAuth credential JSON file(s)."
            )
        if len(pool_dirs) > 1:
            raise RuntimeError(
                f"OAuth pool files span multiple host directories {pool_dirs}. "
                f"Consolidate under one directory so a single mount can expose them."
            )
        pool_host_dir = next(iter(pool_dirs))
        bridge_secret = config.cc_bridge_secret or os.environ.get("WCB_CC_BRIDGE_SECRET", "").strip()
        if not bridge_secret:
            import secrets as _secrets
            bridge_secret = _secrets.token_hex(32)
            os.environ["WCB_CC_BRIDGE_SECRET"] = bridge_secret
        start_bridge(
            container_name=cc_bridge_name,
            network=network,
            pool_host_dir=pool_host_dir,
            bridge_secret=bridge_secret,
        )
        cleanups.append(lambda: stop_bridge(cc_bridge_name))
        if not wait_for_bridge_healthy(cc_bridge_name):
            raise RuntimeError(
                f"cc-bridge {cc_bridge_name} did not become healthy in time. "
                f"Override budget via WCB_CC_BRIDGE_HEALTH_TIMEOUT env (seconds)."
            )
    config.work_dir.mkdir(parents=True, exist_ok=True)
    if shared_mode and shared_yaml_path and Path(shared_yaml_path).is_file():
        cfg_path = Path(shared_yaml_path)
    else:
        cfg_path = config.work_dir / f"litellm-config-{batch_id}.yaml"
        cfg_path.write_text(litellm_yaml, encoding="utf-8")

    callback_src = Path(__file__).resolve().parent.parent / "src" / "utils" / "litellm_usage_callback.py"
    if shared_mode and shared_usage_log:
        usage_log_path = Path(shared_usage_log)
        usage_dir = usage_log_path.parent
    else:
        usage_dir = config.work_dir / f"litellm-usage-{batch_id}"
        usage_dir.mkdir(parents=True, exist_ok=True)
        usage_log_path = usage_dir / "usage.jsonl"
        usage_log_path.touch(exist_ok=True)
    # S-003 hardening: the LiteLLM sidecar writes back to these paths via
    # the `/var/litellm_usage` bind mount. Previously these were 0o666/0o777
    # (world-writable) to sidestep container-UID mismatch. The owner-only
    # modes below assume the sidecar runs as the same UID/GID as the host
    # batch process (cf. start_litellm). If a container-UID mismatch shows
    # up in practice the failure surfaces in the warning log below and the
    # follow-up is to add `--user "$(uid):$(gid)"` to start_litellm rather
    # than re-opening the modes.
    if not shared_mode:
        try:
            os.chmod(usage_log_path, 0o600)
            os.chmod(usage_dir, 0o700)
        except OSError as _chmod_err:
            logger.warning(
                "[%s] chmod failed on litellm usage paths (%s) — "
                "sidecar may be unable to write usage rows",
                batch_id, _chmod_err,
            )

    headroom_callback_src = ""
    headroom_log_dir_str = ""
    if _agent_headroom and not shared_mode:
        headroom_callback_src = str(
            Path(__file__).resolve().parent.parent / "src" / "utils" / "litellm_headroom_callback.py"
        )
        headroom_log_dir = config.work_dir / f"litellm-headroom-{batch_id}"
        headroom_log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(headroom_log_dir, 0o700)
        except OSError as _chmod_err:
            logger.warning(
                "[%s] chmod failed on litellm headroom log dir (%s) — "
                "sidecar may be unable to write headroom rows",
                batch_id, _chmod_err,
            )
        headroom_log_dir_str = str(headroom_log_dir)

    if shared_mode:
        logger.info(
            "LiteLLM sidecar %s reused (shared-infra mode, network=%s); "
            "skipping start/wait/verify — bash owns lifecycle",
            sidecar, network,
        )
    else:
        oauth_cb_src = ""
        if use_oauth:
            oauth_cb_src = str(
                Path(__file__).resolve().parent.parent / "src" / "utils" / "litellm_usage_oauth_callback.py"
            )
        start_litellm(
            container_name=sidecar,
            network=network,
            host_config_path=str(cfg_path),
            master_key=config.litellm_master_key,
            aws_bearer_token=config.aws_bearer_token,
            aws_region=config.bedrock_region,
            openai_api_key=config.openai_api_key,
            openai_whisper_api_key=config.openai_whisper_api_key,
            usage_callback_host_path=str(callback_src),
            usage_log_host_dir=str(usage_dir),
            headroom_callback_host_path=headroom_callback_src,
            headroom_log_host_dir=headroom_log_dir_str,
            enable_headroom=_agent_headroom,
            anthropic_api_key=config.anthropic_api_key,
            oauth_usage_callback_host_path=oauth_cb_src,
        )
        cleanups.append(lambda: stop_litellm(sidecar))
        if not wait_for_litellm_healthy(sidecar):
            raise RuntimeError(
                f"LiteLLM sidecar {sidecar} did not become healthy in time. "
                f"Strict mode: refusing to continue with a dead sidecar. "
                f"Override budget via KENSEI_LITELLM_HEALTH_TIMEOUT env (seconds)."
            )
        probe_model = (
            "claude-opus-4.7" if (config.aws_bearer_token and config.bedrock_inference_arn) or config.anthropic_api_key
            else "gpt-5.5" if config.openai_api_key
            else ""
        )
        if probe_model:
            ok, detail = verify_litellm_upstream_reachable(
                sidecar, master_key=config.litellm_master_key, model_name=probe_model,
            )
            if not ok:
                raise RuntimeError(
                    f"LiteLLM sidecar {sidecar} is up but upstream provider is unreachable "
                    f"via model={probe_model!r}: {detail}. This is the failure mode that "
                    f"produces 'LLM request timed out / Connection error.' in the agent's "
                    f"gateway.log (see openclaw.log 2026-06-02). Fix upstream egress "
                    f"(Bedrock IAM / region / network) before retrying."
                )
        logger.info("LiteLLM sidecar %s ready on network %s", sidecar, network)

    mock_env_dict: dict[str, str] = {}
    if args.mock_stack:
        from src.utils.mock_stack import (
            build_mock_image_if_needed, start_mock_stack,
            wait_for_mock_stack_healthy, stop_mock_stack,
        )
        if not build_mock_image_if_needed(
            config.environment_dir, force=getattr(args, "rebuild_mocks", False),
        ):
            raise RuntimeError(
                "Mock image build failed. Strict mode: refusing to continue "
                "without the mock stack when --mock-stack was requested."
            )
        mock_container = f"mocks-{batch_id}"
        start_mock_stack(mock_container, network, enabled_apis=mock_enabled_apis)
        cleanups.append(lambda: stop_mock_stack(mock_container))
        if not wait_for_mock_stack_healthy(mock_container, timeout=180.0):
            raise RuntimeError(
                f"Mock stack {mock_container} did not become healthy within 180s. "
                f"Strict mode: refusing to continue with a dead mock stack."
            )
        for svc in discover_services(config.environment_dir):
            if svc.get("env_var_name"):
                mock_env_dict[svc["env_var_name"]] = f"http://{mock_container}:{svc['port']}"
        _running = len(mock_enabled_apis) if mock_enabled_apis else len(mock_env_dict)
        logger.info("Mock stack %s ready (%d APIs running)", mock_container, _running)

    return True, litellm_yaml, network, sidecar, mock_env_dict, str(usage_log_path)


def _dump_mock_logs(container: str, api_names) -> str:
    """Must run BEFORE stop_mock_stack removes the container, otherwise the
    docker exec has nothing to read."""
    blobs: list[str] = []
    for api in sorted(api_names):
        try:
            r = subprocess.run(
                ["docker", "exec", container, "cat", f"/tmp/{api}.err"],
                capture_output=True, text=True, timeout=15,
            )
            err = (r.stdout or "").strip() or (r.stderr or "").strip()
        except Exception as exc:
            err = f"<failed to read /tmp/{api}.err: {exc}>"
        if err:
            logger.error("[mock-logs] %s /tmp/%s.err:\n%s", container, api, err)
            blobs.append(f"=== {api} (/tmp/{api}.err) ===\n{err}")
    return "\n\n".join(blobs)


def _start_task_mock_stack(task: dict, network: str, environment_dir) -> tuple[dict, str | None, dict]:
    """Start a per-task mock-stack container with this task's mock_data CSVs
    bind-mounted read-only over the baked-in baseline, and return
    ({ENV_VAR: http://<container>:<port>}, container_name, drift_info).

    drift_info is {} when the task has no drift.yaml; otherwise it carries
    the per-API host-side URLs the DriftDirector uses to reach /admin/* via
    127.0.0.1:<published-port>, plus the admin token (if any).

    Returns ({}, None, {}) when the task ships no overlays or startup fails —
    the caller then falls back to the shared batch stack (current behavior).

    The mock IMAGE is assumed already built by the batch-level
    build_mock_image_if_needed(); here we only spin a lightweight container.
    A unique container name (task id + short uuid) keeps this thread-safe under
    the ThreadPoolExecutor: each task addresses only its own container.
    """
    overlays = task.get("mock_overlays") or {}
    if not overlays or not network or not environment_dir:
        if task.get("drift_script_path"):
            logger.error(
                "[%s] drift.yaml requires per-task mock stack but task ships no overlays / "
                "mock-stack disabled; refusing to start director",
                task.get("task_id"),
            )
        return {}, None, {}

    from src.utils.mock_stack import (
        start_mock_stack, wait_for_ports_healthy, stop_mock_stack,
        get_published_ports, get_network_gateway,
    )

    # Resolve the services this task actually overlays, and the ENV->URL map.
    # We only gate readiness on THESE ports — not the image's all-101-port
    # HEALTHCHECK, which a fresh per-task container rarely satisfies in time.
    try:
        services = discover_services(environment_dir)
    except Exception as exc:
        logger.error("[%s] per-task mock service discovery failed: %s", task.get("task_id"), exc)
        return {}, None, {}
    overlaid_ports = [int(s["port"]) for s in services
                      if s.get("name") in overlays and s.get("port")]
    env_dict_template = {s["env_var_name"]: int(s["port"]) for s in services
                         if s.get("env_var_name") and s.get("port")}
    api_name_by_port: dict[int, str] = {
        int(s["port"]): s["name"]
        for s in services if s.get("port") and s.get("name")
    }

    # Configure admin plane + port-publishing only when the task ships a drift
    # script. Quiescent runs keep the existing zero-port-exposure surface.
    drift_path = task.get("drift_script_path")
    admin_env: dict[str, str] | None = None
    publish_ports: list[int] | None = None
    admin_token: str | None = None
    if drift_path:
        gateway = get_network_gateway(network) or "127.0.0.1"
        admin_token = uuid.uuid4().hex
        admin_env = {
            "MOCK_ADMIN_ENABLED": "1",
            "MOCK_ADMIN_ALLOWLIST": gateway,
            "MOCK_ADMIN_TOKEN": admin_token,
        }
        publish_ports = list(overlaid_ports)

    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", task.get("task_id", "task"))[:40]
    container = f"mocks-task-{safe_id}-{uuid.uuid4().hex[:6]}".lower()
    # Bring up only this task's required+distractor APIs plus whatever it overlays
    # (an overlaid API must serve even if not in required). Empty => all.
    enabled_apis = (
        set(task.get("required_apis") or [])
        | set(task.get("distractor_apis") or [])
        | set(overlays.keys())
    ) or None
    try:
        start_mock_stack(
            container, network,
            overlays=overlays,
            admin_env=admin_env,
            publish_ports=publish_ports,
            enabled_apis=enabled_apis,
        )
    except Exception as exc:
        logger.error("[%s] per-task mock stack failed to start: %s", task.get("task_id"), exc)
        logs = _dump_mock_logs(container, overlays.keys())
        stop_mock_stack(container)
        raise RuntimeError(
            f"per-task mock stack {container} failed to start for task "
            f"{task.get('task_id')}: {exc}; overlay CSV likely malformed"
            + (f"\n{logs}" if logs else "")
        )

    # Wait only for the overlaid API(s) to answer /health inside the container.
    if not wait_for_ports_healthy(container, overlaid_ports, timeout=180.0):
        logger.error("[%s] per-task mock stack %s: overlaid ports %s not healthy; tearing down",
                     task.get("task_id"), container, overlaid_ports)
        logs = _dump_mock_logs(container, overlays.keys())
        stop_mock_stack(container)
        raise RuntimeError(
            f"per-task mock stack {container} not healthy for task "
            f"{task.get('task_id')} (overlaid ports {overlaid_ports}); "
            f"overlay CSV likely malformed"
            + (f"\n{logs}" if logs else "")
        )

    env_dict = {ev: f"http://{container}:{port}" for ev, port in env_dict_template.items()}
    logger.info("[%s] per-task mock stack %s ready (%d overlay APIs, ports %s)",
                task.get("task_id"), container, len(overlays), overlaid_ports)

    drift_info: dict = {}
    if drift_path:
        host_ports = get_published_ports(container, overlaid_ports)
        host_api_to_url: dict[str, str] = {}
        for internal_port, host_port in host_ports.items():
            api_name = api_name_by_port.get(internal_port)
            if api_name:
                host_api_to_url[api_name] = f"http://127.0.0.1:{host_port}"
        if host_api_to_url:
            drift_info = {
                "script_path": drift_path,
                "host_api_to_url": host_api_to_url,
                "admin_token": admin_token,
            }
            logger.info("[%s] drift director will target %d APIs via 127.0.0.1",
                        task.get("task_id"), len(host_api_to_url))
        else:
            logger.error("[%s] drift requested but no host ports resolved; director disabled",
                         task.get("task_id"))

    return env_dict, container, drift_info


def _start_mock_health_logger(task: dict, task_id: str, output_dir):
    """Spin up the per-task mock-API health logger.

    Returns the started thread, or None when the task has no mock URLs to
    probe. Reads ``KENSEI_MOCK_HEALTH_INTERVAL`` (seconds, default 30) for
    the polling cadence so operators can tune verbosity without code edits.
    """
    env_dict = task.get("env_dict") or {}
    if not env_dict:
        return None
    # Probe only the APIs this task actually runs (required + distractor +
    # overlays). The shared env map carries all ~101 URLs, but the mock stack
    # is filtered to this task's set — so probing the rest would flag the ~96
    # intentionally-disabled distractors as "failed" every tick (pure noise).
    enabled_names = (
        set(task.get("required_apis") or [])
        | set(task.get("distractor_apis") or [])
        | set((task.get("mock_overlays") or {}).keys())
    )
    if enabled_names:
        try:
            env_var_by_name = {
                s["name"]: s.get("env_var_name")
                for s in discover_services(task.get("env_dir") or "")
            }
            keep_vars = {env_var_by_name.get(n) for n in enabled_names}
            keep_vars.discard(None)
            filtered = {k: v for k, v in env_dict.items() if k in keep_vars}
            if filtered:
                env_dict = filtered
        except Exception:
            pass  # fall back to probing the full map
    try:
        from src.utils.mock_health_logger import MockHealthLogger
        try:
            interval = float(os.environ.get("KENSEI_MOCK_HEALTH_INTERVAL", "30") or 30)
        except ValueError:
            interval = 30.0
        thread = MockHealthLogger(
            task_id=task_id,
            api_url_map=env_dict,
            output_dir=output_dir,
            agent_container=task_id,
            interval=interval,
        )
        thread.start()
        return thread
    except Exception as exc:
        logger.warning("[%s] Mock health logger failed to start: %s", task_id, exc)
        return None


def _start_drift_director(task: dict, drift_info: dict, output_dir):
    """Spin up the host-side DriftDirector thread for this task.

    Returns the started thread, or None if drift_info is empty / setup fails.
    The director writes drift_timeline.jsonl directly into output_dir; the
    audit polling reaches each mock API via 127.0.0.1:<published-port>.
    """
    script_path = drift_info.get("script_path")
    host_api_to_url = drift_info.get("host_api_to_url") or {}
    if not script_path or not host_api_to_url:
        return None
    try:
        from src.utils.drift_director import (
            DriftScript, DriftDirector, build_targets_from_env,
        )
        script = DriftScript.load(script_path)
        targets = build_targets_from_env(
            host_api_to_url, admin_token=drift_info.get("admin_token"),
        )
        timeline_path = output_dir / "drift_timeline.jsonl"
        director = DriftDirector(
            script=script,
            targets=targets,
            workspace_dir=output_dir,
            timeline_path=timeline_path,
            gateway_log_path=output_dir / "gateway.log",
        )
        director.start()
        logger.info("[%s] Drift director started (%d targets, script=%s)",
                    task.get("task_id"), len(targets), script_path)
        return director
    except Exception as exc:
        logger.error("[%s] Drift director failed to start: %s", task.get("task_id"), exc)
        return None


def main() -> None:
    args = parse_run_batch_args(
        default_model=DEFAULT_MODEL,
        default_parallel=DEFAULT_PARALLEL,
    )
    _select_ui_and_run(args)


def _select_ui_and_run(args) -> None:
    """Choose the UI mode, then run the harness body.

    Two modes (see src/utils/ui/):
      * TUI (opt-in): ``--tui`` / ``WCB_TUI=1`` AND stdout is a real terminal AND
        textual is importable -> run the whole harness inside the full-screen
        Textual dashboard. Falls through to Rich if the dashboard can't start.
      * Rich (default): attach a RichHandler to the root logger so every existing
        ``logger.*`` call is colorized. Rich auto-degrades to ANSI-free text on a
        non-tty sink, so ``run.sh``'s ``tee``'d logs stay clean.

    UI wiring is intentionally additive: the harness body (``_run_main_body``) is
    unchanged in behavior, and exit-code semantics are preserved on the default
    (main-thread) path used by ``run.sh``.
    """
    from src.utils.ui import console as ui_console, tui as ui_tui

    want_tui = bool(
        getattr(args, "tui", False)
        or os.environ.get("WCB_TUI", "").strip().lower() in ("1", "true", "yes")
    )
    if want_tui and ui_console.is_interactive() and ui_tui.textual_available():
        if ui_tui.run_with_dashboard(lambda: _run_main_body(args)):
            return
        # Dashboard declined to start (e.g. textual import failed at runtime).
    ui_console.install_rich_logging()
    _run_main_body(args)


def _run_main_body(args) -> None:
    config = Config.from_env()
    if getattr(args, "bedrock_arn", None):
        config.bedrock_inference_arn = args.bedrock_arn
    if getattr(args, "aws_region", None):
        config.bedrock_region = args.aws_region

    require_image_present(DOCKER_IMAGE)

    cleanups: list = []
    use_litellm = False
    litellm_yaml = network = sidecar = ""
    mock_env_dict: dict[str, str] = {}
    usage_log_path = ""

    if args.agent_backend == "claudecode":
        backend: BaseAgent = ClaudeCodeAgent(
            anthropic_api_key=OPENROUTER_API_KEY,
            openrouter_base_url=OPENROUTER_BASE_URL_CLAUDECODE
        )
    elif args.agent_backend == "codex":
        backend = CodexAgent()
    elif args.agent_backend == "hermesagent":
        from src.agents.hermesagent import HermesAgentAgent
        backend = HermesAgentAgent(
            openrouter_api_key=OPENROUTER_API_KEY,
            openrouter_base_url=OPENROUTER_BASE_URL_OPENCLAW,
        )
    else:
        # Resolve the required+distractor APIs for this invocation's task(s) so
        # the shared mock stack only brings up those services, not all ~101.
        # None => run the full catalog (safe fallback).
        mock_enabled_apis = _collect_enabled_apis(args, config)
        try:
            use_litellm, litellm_yaml, network, sidecar, mock_env_dict, usage_log_path = (
                _setup_litellm_and_mocks(args, config, cleanups,
                                         mock_enabled_apis=mock_enabled_apis)
            )
        except Exception:
            # Setup registers teardown callables before the main try/finally
            # below, so a failure mid-setup (e.g. start_litellm / start_mock_stack
            # raising) would otherwise orphan the docker network + containers.
            _run_cleanups(cleanups)
            raise
        if use_litellm:
            backend = OpenClawAgent(
                gateway_port=GATEWAY_PORT,
                openai_api_key=config.openai_api_key,
                litellm_master_key=config.litellm_master_key,
                litellm_port=config.litellm_port,
                litellm_config_yaml=litellm_yaml,
                litellm_container_name=sidecar,
                litellm_network=network,
                image_model=args.openclaw_image_model,
                litellm_usage_log=usage_log_path,
            )
        else:
            backend = OpenClawAgent(
                gateway_port=GATEWAY_PORT,
                openrouter_api_key=OPENROUTER_API_KEY,
                openrouter_base_url=OPENROUTER_BASE_URL_OPENCLAW,
                image_model=args.openclaw_image_model,
            )

    # In LiteLLM mode the model must be a sidecar model id (claude-opus-4.7 / gpt-5.5).
    effective_model = args.model
    if use_litellm and args.model not in LITELLM_MODEL_IDS and not args.model.startswith("litellm/"):
        effective_model = os.environ.get("LITELLM_DEFAULT_MODEL", "claude-opus-4.7")
        logger.info("LiteLLM mode: '%s' is not a sidecar model id; using '%s'", args.model, effective_model)

    # Per-task mock isolation is available only when the shared litellm/mock
    # network is up and the user asked for the mock stack.
    enable_mock_stack = bool(use_litellm and getattr(args, "mock_stack", False) and network)

    try:
        _run_dispatch(args, backend, config, mock_env_dict, effective_model,
                      network=network, enable_mock_stack=enable_mock_stack)
    finally:
        _run_cleanups(cleanups)


def _run_dispatch(args, backend, config: Config, mock_env_dict: dict, effective_model: str,
                  network: str = "", enable_mock_stack: bool = False) -> None:
    # UI layer (Rich summary + lifecycle). Imported lazily so importing this
    # module for unit tests never pulls in the UI stack. All calls are additive
    # and never change scoring/outputs.
    import time as _time
    from src.utils.ui import lifecycle as _ui_lifecycle, summary as _ui_summary
    from src.utils.ui.events import EV_PROGRESS as _EV_PROGRESS, get_bus as _get_bus
    _dispatch_start = _time.time()

    def _emit_progress(done: int, total: int) -> None:
        # Drives the Textual dashboard's progress bar. No-op without a subscriber.
        try:
            _get_bus().emit(_EV_PROGRESS, completed=done, total=max(1, total))
        except Exception:
            pass

    gen_tests = args.generate_tests
    if gen_tests is None:
        gen_tests = bool(config.bedrock_inference_arn and config.aws_bearer_token)
    testgen_max_attempts = getattr(args, "testgen_max_attempts", 3)
    exec_tests = getattr(args, "execute_tests", None)
    if exec_tests is None:
        exec_tests = bool(gen_tests and enable_mock_stack)
    testexec_timeout = getattr(args, "testexec_timeout", 600)
    use_judge_council = getattr(args, "judge_council", None)
    if use_judge_council is True:
        logger.info("Judge council enabled via --judge-council")
    elif use_judge_council is False:
        logger.info("Judge council explicitly disabled via --no-judge-council")
    if gen_tests:
        logger.info("Test generation enabled (Bedrock %s, max_attempts=%d)",
                    config.bedrock_region, testgen_max_attempts)
    if exec_tests:
        logger.info("Test execution enabled (timeout=%ds, network=%s)",
                    testexec_timeout, network or "<host>")
    elif gen_tests and not enable_mock_stack:
        logger.warning("Test execution skipped: --generate-tests is on but --mock-stack is not; "
                       "tests would target unreachable mock URLs. Pass --execute-tests to force.")
    output_root = OUTPUT_DIR / args.agent_backend
    models_config = None
    if args.models_config:
        models_config_path = Path(args.models_config).expanduser()
        if not models_config_path.is_file():
            logger.error("Models config not found: %s", models_config_path)
            sys.exit(1)
        try:
            models_config = load_models_config(models_config_path.resolve())
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error("Invalid models config: %s", exc)
            sys.exit(1)

    lobster = None
    if args.lobster_workspace:
        if not args.lobster_name:
            logger.error("--lobster-workspace requires --lobster-name")
            sys.exit(1)
        workspace = Path(args.lobster_workspace).expanduser()
        if not workspace.is_dir():
            logger.error("Lobster workspace not found: %s", workspace)
            sys.exit(1)
        env_keys = [k.strip() for k in args.lobster_env.split(",") if k.strip()] if args.lobster_env else []
        lobster = {
            "name": args.lobster_name,
            "workspace": str(workspace.resolve()),
            "env": env_keys,
        }
        logger.info("Lobster mode: %s (workspace=%s, env_keys=%s)",
                     lobster["name"], lobster["workspace"], lobster["env"])

    if args.task:
        task_file = Path(args.task)
        if not task_file.exists():
            logger.error("File not found: %s", task_file)
            sys.exit(1)
        task = load_task(task_file)
        task["__use_judge_council__"] = use_judge_council
        task["__force_testgen__"] = bool(getattr(args, "force_testgen", False))
        logger.info("Single task mode: %s (format=%s)", task["task_id"], task.get("format", "md"))
        # ISOLATION INVARIANT (b6/m0192): one task or rep failure must NEVER
        # cascade to other reps or other -P parallel tasks. The legacy code
        # called run_single_task() unguarded, so a RuntimeError from
        # _start_task_mock_stack (overlay CSV malformed, mock container
        # unhealthy, etc.) propagated as an unhandled exception, killing the
        # whole python process for this rep BEFORE `result` was even
        # constructed. From script/run.sh's POV the rep just exits with a
        # traceback; with PARALLEL_REPS=1 + -P 5 the failure window can
        # straddle other reps' setup phases. The category-mode threadpool
        # path at lines ~2197-2226 already wraps future.result() in
        # try/except and produces a soft {"task_id": tid, "scores": {},
        # "error": str(exc)} record — mirror that contract here so the
        # --task entry point converges on the same fail-soft semantics.
        # script/AGENTS.md invariant: run.sh and `python3 eval/run_batch.py`
        # must converge on identical artifacts for the same args; that
        # includes failure-mode artifacts (non-zero exit + structured error
        # log) not raw tracebacks.
        try:
            result = run_single_task(
                task,
                effective_model,
                backend=backend,
                output_root=output_root,
                lobster=lobster,
                models_config=models_config,
                thinking=args.thinking,
                config=config,
                mock_env_dict=mock_env_dict,
                network=network,
                enable_mock_stack=enable_mock_stack,
                generate_tests=gen_tests,
                testgen_max_attempts=testgen_max_attempts,
                execute_tests=exec_tests,
                testexec_timeout=testexec_timeout,
            )
        except Exception as exc:
            # Mirror the category-mode soft-failure shape so callers
            # (script/run.sh::run_one_rep, deliver.sh) see a clean rc=1
            # instead of a raw traceback. This is the only place the
            # --task entry path can leak unhandled exceptions.
            logger.error(
                "[%s] run_single_task raised (isolating rep): %s",
                task.get("task_id", "<unknown>"), exc, exc_info=True,
            )
            result = {
                "task_id": task.get("task_id", "<unknown>"),
                "scores": {},
                "error": str(exc),
            }
        # Terminal lifecycle stage + execution summary for the single-task path
        # (the flow script/run.sh always takes). Display-only; no behavior change.
        _tid = result.get("task_id", "<unknown>")
        _errored = bool(result.get("error") or (result.get("scores") or {}).get("error"))
        _ui_lifecycle.emit_stage(_tid, _ui_lifecycle.STAGE_FAIL if _errored else _ui_lifecycle.STAGE_DONE,
                                 result.get("error") or "")
        _emit_progress(1, 1)
        try:
            _ui_summary.render_execution_summary([result], _time.time() - _dispatch_start)
        except Exception as _sx:
            logger.debug("execution summary render failed: %s", _sx)
        if _errored:
            sys.exit(1)
        return
    if args.category.lower() == "all":
        categories = ALL_CATEGORIES
    else:
        categories = [args.category]

    all_results: list[dict] = []
    safe_model_name = re.sub(r'[^a-zA-Z0-9.\-_]', '_', effective_model)

    for category in categories:
        category_dir = TASKS_DIR / category
        if not category_dir.exists():
            logger.error("Category directory not found: %s", category_dir)
            continue

        task_files = sorted(category_dir.glob("*task_*.md"))
        if not task_files:
            logger.error("No task_*.md files found in: %s", category_dir)
            continue

        logger.info("Category: %s, %d tasks, parallelism: %d",
                    category, len(task_files), args.parallel)

        tasks = []
        for tf in task_files:
            try:
                t = load_task(tf)
                t["__use_judge_council__"] = use_judge_council
                t["__force_testgen__"] = bool(getattr(args, "force_testgen", False))
                tasks.append(t)
            except Exception as exc:
                logger.error("Parse failed %s: %s", tf, exc)

        if not tasks:
            continue

        results: list[dict] = []
        if args.parallel <= 1:
            for task in tasks:
                tid = task.get("task_id", "<unknown>")
                try:
                    results.append(
                        run_single_task(
                            task,
                            effective_model,
                            backend=backend,
                            output_root=output_root,
                            lobster=lobster,
                            models_config=models_config,
                            thinking=args.thinking,
                            config=config,
                            mock_env_dict=mock_env_dict,
                            network=network,
                            enable_mock_stack=enable_mock_stack,
                            generate_tests=gen_tests,
                            testgen_max_attempts=testgen_max_attempts,
                            execute_tests=exec_tests,
                            testexec_timeout=testexec_timeout,
                        )
                    )
                except Exception as exc:
                    logger.error(
                        "[%s] run_single_task raised (isolating task, continuing loop): %s",
                        tid, exc, exc_info=True,
                    )
                    results.append({"task_id": tid, "scores": {}, "error": str(exc)})
                _emit_progress(len(results), len(tasks))
        else:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = {
                    pool.submit(
                        run_single_task,
                        task,
                        effective_model,
                        backend,
                        output_root,
                        lobster,
                        args.thinking,
                        models_config,
                        config,
                        mock_env_dict,
                        network,
                        enable_mock_stack,
                        gen_tests,
                        testgen_max_attempts,
                        exec_tests,
                        testexec_timeout,
                    ): task["task_id"]
                    for task in tasks
                }
                for future in as_completed(futures):
                    tid = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        logger.error("[%s] Thread exception: %s", tid, exc)
                        results.append({"task_id": tid, "scores": {}, "error": str(exc)})
                    _emit_progress(len(results), len(tasks))

        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        # quiet=True: keep the summary_<model>.json write, but let the Rich
        # execution summary below own the console output (no duplicate report).
        print_summary(results, category, output_root, summary_label, quiet=True)
        all_results.extend(results)

    if len(categories) > 1 and all_results:
        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        print_global_summary(all_results, output_root, summary_label, quiet=True)

    if all_results:
        try:
            _ui_summary.render_execution_summary(all_results, _time.time() - _dispatch_start)
        except Exception as _sx:
            logger.debug("execution summary render failed: %s", _sx)

if __name__ == "__main__":
    main()
