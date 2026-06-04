from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import subprocess
import sys
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from src.utils.skills_inference import infer_required_apis, compute_distractor_skills
from src.utils.testgen import generate_task_tests
from src.utils.litellm_sidecar import (
    build_litellm_config_yaml,
    create_network,
    pull_litellm_image,
    remove_network,
    start_litellm,
    stop_litellm,
    verify_litellm_upstream_reachable,
    wait_for_litellm_healthy,
)
from src.utils.trajectory.builder import build_trajectory_from_jsonl
from src.utils.trajectory.local_media import replace_inline_media_with_files
from src.utils.store import Task as StoreTask, Store
from src.utils.harbor.bundle import write_bundle

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
        entries = []
        for sub in sorted(mock_root.iterdir()):
            if sub.is_dir():
                for child in sorted(sub.rglob("*")):
                    if child.is_file():
                        try:
                            entries.append((str(child.relative_to(mock_root)), child.stat().st_size))
                        except OSError:
                            entries.append((str(child.relative_to(mock_root)), -1))
        h.update(f"\x00mock_data:{entries}\x00".encode())
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


def save_usage(
    output_dir: Path,
    result: dict,
    usage: dict,
    task_id: str,
    *,
    testgen_usage: dict | None = None,
    judge_usage: dict | None = None,
) -> dict:
    """Write usage.json with per-source breakdown (agent + testgen + judge)."""
    agent_usage = dict(usage)
    sources: dict[str, dict] = {"agent": agent_usage}
    if testgen_usage:
        sources["testgen"] = dict(testgen_usage)
    if judge_usage:
        sources["judge"] = dict(judge_usage)

    combined: dict[str, Any] = {k: 0 for k in _USAGE_NUMERIC_KEYS}
    combined["cost_usd"] = 0.0
    for src in sources.values():
        _merge_usage_source(combined, src)

    if "total_tokens" in combined and (
        combined["total_tokens"] == 0
        or combined["total_tokens"] < combined["input_tokens"] + combined["output_tokens"]
    ):
        combined["total_tokens"] = (
            combined["input_tokens"]
            + combined["output_tokens"]
            + combined["cache_read_tokens"]
            + combined["cache_write_tokens"]
        )

    out: dict[str, Any] = dict(combined)
    out["sources"] = sources
    for k, v in agent_usage.items():
        if k not in out and k not in _USAGE_NUMERIC_KEYS and k != "cost_usd":
            out[k] = v

    result["usage"] = out
    if out["request_count"] > 0:
        breakdown_bits = []
        for name in ("agent", "testgen", "judge"):
            s = sources.get(name)
            if s and s.get("request_count", 0) > 0:
                breakdown_bits.append(
                    f"{name}(in={s.get('input_tokens',0)},out={s.get('output_tokens',0)}"
                    f",cR={s.get('cache_read_tokens',0)})"
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
    """Stage a per-task workspace dir (<work>/<task_id>/exec) from a native/yaml
    task's attachments, returning the parent dir to pass as workspace_path.
    The openclaw runner mounts <workspace_path>/exec into the container."""
    import shutil
    task_id_ori = task["task_id"]
    staging = Path(config.work_dir) / re.sub(r"[^a-zA-Z0-9._-]", "_", task_id_ori)
    exec_dir = staging / "exec"
    exec_dir.mkdir(parents=True, exist_ok=True)
    for att in task.get("attachments", []) or []:
        src = Path(att.get("path", ""))
        if not src.is_file():
            continue
        rel = att.get("storedAs") or att.get("name") or src.name
        # Reject paths that would escape exec_dir (".." or absolute).
        rel_path = Path(rel)
        if rel_path.is_absolute() or any(p == ".." for p in rel_path.parts):
            logger.warning("[%s] skipping unsafe attachment path: %s", task_id_ori, rel)
            continue
        dst = exec_dir / rel_path
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if dst.exists():
                    dst.unlink()
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        except OSError as exc:
            logger.warning("[%s] attachment copy failed (%s): %s", task_id_ori, src, exc)
    return str(staging)


def _augment_task_with_mocks(task: dict, config, mock_env_dict: dict | None) -> None:
    """Populate env_dir / required_apis / env_dict on the task dict so the
    openclaw runner injects API connectors and the shared mock-stack URLs."""
    env_dir = str(config.environment_dir) if config.environment_dir else ""
    required: set[str] = set()
    # 1) APIs inferred from the prompt text (catalog discovered from env_dir)
    try:
        required.update(infer_required_apis(
            task.get("initial_prompt") or task.get("prompt") or "",
            environment_dir=config.environment_dir,
        ))
    except Exception:
        pass
    # 2) APIs named by the task's mock_data/<api>/ subdirs (native tasks).
    #    Also build the per-task overlay map {api: {filename: host_path}} so the
    #    task's OWN mock_data CSVs get bind-mounted over the baked-in baseline
    #    (same shape as harbor/bundle.py). Without this the agent only ever sees
    #    the generic baked dataset (course_001..005 + uscg-...), never the task's.
    task_dir = task.get("task_dir", "")
    overlays: dict[str, dict[str, str]] = {}
    if task_dir:
        mock_root = Path(task_dir) / "mock_data"
        if mock_root.is_dir():
            required.update(d.name for d in mock_root.iterdir() if d.is_dir())
            overlays = {
                api_dir.name: {
                    p.name: str(p.resolve())
                    for p in api_dir.iterdir() if p.is_file()
                }
                for api_dir in sorted(mock_root.iterdir())
                if api_dir.is_dir() and any(p.is_file() for p in api_dir.iterdir())
            }
    task["env_dir"] = env_dir
    task["required_apis"] = sorted(required)
    task["mock_overlays"] = overlays
    # Distractor APIs are exposed to the agent the same way required APIs are
    # (connector skill docs + URL env var), so the agent must actively choose
    # between plausible-but-unneeded surfaces. This is the design intent of
    # distractor_skills in task.toml (b46); before 2026-06-02 only the harbor
    # bundle + testgen saw them, so the live agent could not actually be
    # tempted by them. The selection is deterministic per task_id, so reruns
    # of the same task see the same distractor set.
    try:
        task["distractor_apis"] = list(compute_distractor_skills(
            sorted(required),
            task.get("task_id") or task.get("task_id_ori") or "",
            environment_dir=config.environment_dir,
        ))
    except Exception:
        task["distractor_apis"] = []
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


def _next_run_index(model_dir: Path) -> int:
    i = 1
    while (model_dir / f"run_{i}").exists():
        i += 1
    return i


def _write_pass_summary(model_dir: Path, model_type: str, run_index: int, reward,
                        scores: dict | None = None) -> None:
    p = model_dir / "pass_summary.json"
    existing = {}
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    per_run = [r for r in existing.get("per_run", []) if r.get("run_index") != run_index]
    s = scores or {}
    # Source of truth is the rubric grader: criteria_* (canonical) with tests_*
    # fallback for legacy score.json files. reward is overall_score (0..1);
    # rubric_weights_percentage = reward * 100 (user formula m1420 line 2).
    crit_total = int(s.get("criteria_total", s.get("tests_total", 0)) or 0)
    crit_passed = int(s.get("criteria_passed", s.get("tests_passed", 0)) or 0)
    crit_failed = int(s.get("criteria_failed", s.get("tests_failed", 0)) or 0)
    reward_f = float(reward) if isinstance(reward, (int, float)) else 0.0
    pct = float(s.get("rubric_weights_percentage", reward_f * 100.0) or 0.0)
    per_run.append({
        "run_index": run_index,
        "criteria_total": crit_total,
        "criteria_passed": crit_passed,
        "criteria_failed": crit_failed,
        "tests_total": crit_total,
        "tests_passed": crit_passed,
        "tests_failed": crit_failed,
        "reward": reward_f,
        "rubric_weights_percentage": round(pct, 2),
    })
    per_run.sort(key=lambda r: r["run_index"])
    rewards = [r["reward"] for r in per_run]
    pcts = [r.get("rubric_weights_percentage", r["reward"] * 100.0) for r in per_run]
    avg_reward = (sum(rewards) / len(rewards)) if rewards else 0.0
    avg_pct = (sum(pcts) / len(pcts)) if pcts else 0.0
    p.write_text(json.dumps({
        "model": model_type, "runs": len(per_run),
        "average_reward": avg_reward,
        "average_rubric_weights_percentage": round(avg_pct, 2),
        "per_run": per_run,
    }, indent=2), encoding="utf-8")


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
        return {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "cost_usd": 0.0}
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
            logger.warning("[%s] rubric grading failed: %s", task.get("task_id"), exc)
            try:
                (output_dir / "score.json").write_text(
                    json.dumps({
                        "overall_score": None,
                        "rubric_weights_percentage": None,
                        "criteria_total": len(rubrics),
                        "criteria_passed": 0,
                        "criteria_failed": 0,
                        "criteria_abstained": len(rubrics),
                        "judge_model": None,
                        "error": f"{type(exc).__name__}: {exc}",
                        "results_dir": str(results_dir),
                    }, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass

    reward = (result.get("scores") or {}).get("overall_score")
    _write_pass_summary(task_bundle_dir / "trajectories" / model_type, model_type,
                        run_index, reward, scores=result.get("scores") or {})

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
            tr_meta = {
                "tests_total": int(src.get("tests_total", src.get("criteria_total", 0)) or 0),
                "tests_passed": int(src.get("tests_passed", src.get("criteria_passed", 0)) or 0),
                "tests_failed": int(src.get("tests_failed", src.get("criteria_failed", 0)) or 0),
                "tests_errored": int(src.get("tests_errored", 0) or 0),
                "tests_skipped": int(src.get("tests_skipped", 0) or 0),
                "test_scores": src.get("test_scores", "") or "",
                "test_output": src.get("test_output", "") or "",
                "test_code": task.get("test_code", "") or "",
            }
            entry = dict(traj)
            entry["__test_result__"] = tr_meta
            entry["__run_index__"] = run_index
            store_path = getattr(config, "state_db", None)
            store = Store(Path(store_path)) if store_path else Store(Path(":memory:"))
            manifest = write_bundle(
                task=st,
                out_dir=task_bundle_dir,
                store=store,
                config=config,
                trajectories_by_model={model_type: [entry]},
                attachments=task.get("attachments") or [],
                task_dir=Path(td) if td else None,
            )
            logger.info(
                "[%s] Harbor bundle written: %s (required=%d, distractor=%d)",
                task["task_id"], task_bundle_dir,
                len(manifest.get("required_skills") or []),
                len(manifest.get("distractor_skills") or []),
            )
        except Exception as exc:
            logger.warning("[%s] Harbor bundle write failed: %s", task["task_id"], exc)


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
    run_index = _next_run_index(model_dir)
    output_dir = model_dir / f"run_{run_index}"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {"task_id": task_id, "scores": {}, "error": None}

    gateway_proc = None
    agent_proc = None
    elapsed_time = float(timeout_seconds)
    drift_director = _start_drift_director(task, drift_info, output_dir) if drift_info else None

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
                )
                (verifier_dir / "ctrf.json").write_text(
                    json.dumps(ctrf, indent=2, ensure_ascii=False), encoding="utf-8")
                _fn_outs = te.get("test_function_outputs") or "{}"
                (verifier_dir / "test_function_outputs.json").write_text(
                    _fn_outs if isinstance(_fn_outs, str) else json.dumps(_fn_outs, indent=2),
                    encoding="utf-8")
                (verifier_dir / "test_output.log").write_text(
                    te.get("test_output", "") or "", encoding="utf-8")
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
            logger.warning("[%s] trajectory build failed: %s", task_id, exc)

        result = save_usage(
            output_dir, result, usage, task_id,
            testgen_usage=task.get("__testgen_usage__"),
            judge_usage=result.get("__judge_usage__"),
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

        if task_mock_container:
            try:
                from src.utils.mock_stack import stop_mock_stack
                stop_mock_stack(task_mock_container)
                logger.info("[%s] Per-task mock stack %s cleaned up",
                            task_id, task_mock_container)
            except Exception as exc:
                logger.warning("[%s] Per-task mock stack cleanup failed: %s", task_id, exc)

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


def _setup_litellm_and_mocks(args, config: Config, cleanups: list):
    """For the openclaw backend, optionally bring up a per-batch shared LiteLLM
    sidecar + docker network (+ mock-API stack). Returns
    (use_litellm, litellm_yaml, network, sidecar, mock_env_dict, usage_log_path).
    Registers teardown callables onto `cleanups`. Falls back gracefully to
    OpenRouter."""
    use_litellm = args.litellm if args.litellm is not None else config.litellm_enabled()
    if not use_litellm:
        return False, "", "", "", {}, ""

    import uuid as _uuid
    batch_id = _uuid.uuid4().hex[:12]
    network = f"k3net-{batch_id}"
    sidecar = f"ll-{batch_id}"

    litellm_yaml = build_litellm_config_yaml(
        bedrock_sonnet_arn=config.bedrock_sonnet_arn if config.aws_bearer_token else "",
        bedrock_arn=config.bedrock_inference_arn if config.aws_bearer_token else "",
        aws_region=config.bedrock_region,
        openai_api_key=config.openai_api_key,
        enable_usage_callback=True,
    )
    if not litellm_yaml:
        raise RuntimeError(
            "LiteLLM requested but no Bedrock/OpenAI creds resolved. "
            "Strict mode: refusing to silently downgrade to OpenRouter."
        )

    pull_litellm_image()
    create_network(network)
    cleanups.append(lambda: remove_network(network))
    config.work_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config.work_dir / f"litellm-config-{batch_id}.yaml"
    cfg_path.write_text(litellm_yaml, encoding="utf-8")

    callback_src = Path(__file__).resolve().parent.parent / "src" / "utils" / "litellm_usage_callback.py"
    usage_dir = config.work_dir / f"litellm-usage-{batch_id}"
    usage_dir.mkdir(parents=True, exist_ok=True)
    usage_log_path = usage_dir / "usage.jsonl"
    usage_log_path.touch(exist_ok=True)
    try:
        os.chmod(usage_log_path, 0o666)
        os.chmod(usage_dir, 0o777)
    except OSError:
        pass

    start_litellm(
        container_name=sidecar,
        network=network,
        host_config_path=str(cfg_path),
        master_key=config.litellm_master_key,
        aws_bearer_token=config.aws_bearer_token,
        aws_region=config.bedrock_region,
        openai_api_key=config.openai_api_key,
        usage_callback_host_path=str(callback_src),
        usage_log_host_dir=str(usage_dir),
    )
    cleanups.append(lambda: stop_litellm(sidecar))
    if not wait_for_litellm_healthy(sidecar):
        raise RuntimeError(
            f"LiteLLM sidecar {sidecar} did not become healthy in time. "
            f"Strict mode: refusing to continue with a dead sidecar. "
            f"Override budget via KENSEI_LITELLM_HEALTH_TIMEOUT env (seconds)."
        )
    probe_model = (
        "claude-opus-4.7" if config.aws_bearer_token and config.bedrock_inference_arn
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
        start_mock_stack(mock_container, network)
        cleanups.append(lambda: stop_mock_stack(mock_container))
        if not wait_for_mock_stack_healthy(mock_container, timeout=180.0):
            raise RuntimeError(
                f"Mock stack {mock_container} did not become healthy within 180s. "
                f"Strict mode: refusing to continue with a dead mock stack."
            )
        for svc in discover_services(config.environment_dir):
            if svc.get("env_var_name"):
                mock_env_dict[svc["env_var_name"]] = f"http://{mock_container}:{svc['port']}"
        logger.info("Mock stack %s ready (%d service URLs)", mock_container, len(mock_env_dict))

    return True, litellm_yaml, network, sidecar, mock_env_dict, str(usage_log_path)


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
    try:
        start_mock_stack(
            container, network,
            overlays=overlays,
            admin_env=admin_env,
            publish_ports=publish_ports,
        )
    except Exception as exc:
        logger.error("[%s] per-task mock stack failed to start: %s", task.get("task_id"), exc)
        return {}, None, {}

    # Wait only for the overlaid API(s) to answer /health inside the container.
    if not wait_for_ports_healthy(container, overlaid_ports, timeout=180.0):
        logger.error("[%s] per-task mock stack %s: overlaid ports %s not healthy; tearing down",
                     task.get("task_id"), container, overlaid_ports)
        stop_mock_stack(container)
        return {}, None, {}

    env_dict = {ev: f"http://{container}:{port}" for ev, port in env_dict_template.items()}
    logger.info("[%s] per-task mock stack %s ready (%d overlay APIs, ports %s, %d URLs)",
                task.get("task_id"), container, len(overlays), overlaid_ports, len(env_dict))

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
        try:
            use_litellm, litellm_yaml, network, sidecar, mock_env_dict, usage_log_path = (
                _setup_litellm_and_mocks(args, config, cleanups)
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
        if result.get("error") or (result.get("scores") or {}).get("error"):
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

        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        print_summary(results, category, output_root, summary_label)
        all_results.extend(results)

    if len(categories) > 1 and all_results:
        summary_label = f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        print_global_summary(all_results, output_root, summary_label)

if __name__ == "__main__":
    main()
