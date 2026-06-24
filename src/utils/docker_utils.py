from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DOCKER_IMAGE  = os.environ.get("DOCKER_IMAGE",   "wildclawbench-ubuntu:v1.3")
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE",  "/tmp_workspace")
WORKSPACE_BASELINE_PATH = "/tmp/wildclaw_workspace_baseline.json"

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _container_running(task_id: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", task_id],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def _map_workspace_dst(container_dst: str) -> str:
    """Map an inject mutation's container path to the live agent workspace.

    Inject mutations address files as ``/workspace/<rel>`` (occasionally
    ``/app/<rel>``); the agent's writable tree lives at ``TMP_WORKSPACE``, which
    ``/root/workspace`` and ``/root/.openclaw/workspace`` symlink to. A relative
    path is taken as workspace-relative. Other absolute paths are honored as-is.
    """
    p = str(container_dst or "").strip()
    for prefix in ("/workspace/", "/app/"):
        if p.startswith(prefix):
            return str(PurePosixPath(TMP_WORKSPACE) / p[len(prefix):])
    if p in ("/workspace", "/app"):
        return TMP_WORKSPACE
    if p.startswith(TMP_WORKSPACE) or p.startswith("/"):
        return p
    return str(PurePosixPath(TMP_WORKSPACE) / p)


def copy_file_into_workspace(task_id: str, host_src: "Path | None",
                             container_dst: str, mkdir: bool = False) -> bool:
    """InjectDirector filesystem hook: place a host file (or mkdir) inside the
    running agent container's workspace via ``docker cp`` / ``docker exec``.

    Returns False (the caller logs it as a skipped fs op) when the container is
    not running — e.g. the pre-T0 seed stage, which fires before the agent
    container starts and whose drops are redundant with the mounted ``/app``
    baseline. Per-turn drops fire mid-run while the container is up.
    """
    if not _container_running(task_id):
        logger.info("[%s] inject fs: container not up; skip %s", task_id, container_dst)
        return False
    dst = _map_workspace_dst(container_dst)
    try:
        if mkdir:
            r = subprocess.run(["docker", "exec", task_id, "mkdir", "-p", dst],
                               capture_output=True, text=True)
            return r.returncode == 0
        if host_src is None:
            return False
        parent = str(PurePosixPath(dst).parent)
        subprocess.run(["docker", "exec", task_id, "mkdir", "-p", parent],
                       capture_output=True, text=True)
        r = subprocess.run(["docker", "cp", str(host_src), f"{task_id}:{dst}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            logger.warning("[%s] inject fs: docker cp failed %s -> %s: %s",
                           task_id, host_src, dst, (r.stderr or "").strip())
            return False
        # Keep agent-writable so later turns can edit (mirrors setup_workspace).
        subprocess.run(["docker", "exec", task_id, "chmod", "-R", "u+w", dst],
                       capture_output=True, text=True)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[%s] inject fs: error placing %s: %s", task_id, dst, exc)
        return False


def require_image_present(image: str) -> None:
    # Strict precheck for images that must already exist locally (the agent
    # image is loaded from the HuggingFace tar via `docker load`; we never
    # pull-on-run). Any miss raises so the harness fails fast at startup
    # instead of silently letting `docker run` try and fail mid-task with
    # `manifest unknown`.
    r = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"Required Docker image not present locally: {image}\n"
            f"Load it first (e.g. `docker load -i Images/wildclawbench-ubuntu_v1.3.tar`)\n"
            f"or set DOCKER_IMAGE to a tag that exists. docker inspect said:\n"
            f"{(r.stderr or '').strip()}"
        )
    logger.info("Agent image %s present", image)

def start_container(task_id: str, workspace_path: str, extra_env: str = "",
                    tmp_path: str = "", lobster_env: list[str] | None = None,
                    extra_env_dict: dict[str, str] | None = None,
                    network: str = "") -> None:
    workspace = Path(workspace_path).expanduser()
    if not workspace.is_dir():
        raise RuntimeError(f"Workspace path does not exist or is not a directory: {workspace}")

    proxy_http = os.environ.get('HTTP_PROXY_INNER', '')
    proxy_https = os.environ.get('HTTPS_PROXY_INNER', '')
    # The wildclawbench-ubuntu:v1.3 image bakes in
    #   http_proxy=http://100.104.40.233:7897
    #   https_proxy=http://100.104.40.233:7897
    # (from the image builder's prior corporate VPN; unreachable from Docker).
    # If we don't override them, every outbound HTTP from the agent (curl,
    # requests, the OpenAI SDK that openclaw uses to call our LiteLLM
    # sidecar) routes through that dead proxy and returns "Connection
    # error." instantly. That is the alden-croft 2026-06-02 incident:
    # rawErrorPreview="Connection error." rawErrorHash=sha256:8ec9a0b7fe5c,
    # 4 retries within 20s, surface_error, score 0. Empirically reproduced
    # against bare image (no env injection) — both intra-network sidecar
    # and public internet calls fail identically because curl tries the
    # poisoned proxy before TCP-connect to the real target.
    # Fix: when no external proxy is configured, EXPLICITLY pass `-e
    # var=""` so Docker overrides the image's baked defaults with empty
    # strings (most HTTP libs treat empty as "no proxy"; coherent with the
    # --internal-bridge sandbox from (b14)).
    env_args: list[str] = ["-e", f"BRAVE_API_KEY={BRAVE_API_KEY}"]
    if proxy_http or proxy_https:
        no_proxy_value = os.environ.get("NO_PROXY_INNER", "")
        env_args += [
            "-e", f"http_proxy={proxy_http}",
            "-e", f"https_proxy={proxy_https}",
            "-e", f"HTTP_PROXY={proxy_http}",
            "-e", f"HTTPS_PROXY={proxy_https}",
            "-e", f"no_proxy={no_proxy_value}",
            "-e", f"NO_PROXY={no_proxy_value}",
        ]
    else:
        env_args += [
            "-e", "http_proxy=",
            "-e", "https_proxy=",
            "-e", "HTTP_PROXY=",
            "-e", "HTTPS_PROXY=",
            "-e", "no_proxy=",
            "-e", "NO_PROXY=",
        ]
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith("#"):
            continue
        value = os.environ.get(key, "")
        env_args += ["-e", f"{key}={value}"]
        masked = (value[:4] + "***") if value else "(empty)"
        logger.info("[%s] Injecting env var: %s=%s", task_id, key, masked)

    for key in (lobster_env or []):
        value = os.environ.get(key, "")
        if not value:
            logger.warning("[%s] Lobster env key %s not found in environment, skipping", task_id, key)
            continue
        env_args += ["-e", f"{key}={value}"]
        masked = value[:4] + "***"
        logger.info("[%s] Injecting lobster env: %s=%s", task_id, key, masked)

    for k, v in (extra_env_dict or {}).items():
        env_args += ["-e", f"{k}={v}"]
        logger.info("[%s] Injecting extra env: %s=%s", task_id, k, v)

    image = os.environ.get("DOCKER_IMAGE", DOCKER_IMAGE)
    network_args = ["--network", network] if network else []
    cmd = [
        "docker", "run", "-d",
        "--name", task_id,
        *network_args,
        *env_args,
        "-v", f"{workspace}:/app:ro",
        image,
        "/bin/bash", "-c", "tail -f /dev/null",
    ]
    logger.info("[%s] Starting container, mounting %s → /app (ro)", task_id, workspace)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Container startup failed:\n{r.stderr}")
    logger.info("[%s] Container ID: %s", task_id, r.stdout.strip()[:12])

    if tmp_path and os.path.exists(tmp_path):
        mkdir_cmd = ["docker", "exec", task_id, "mkdir", "-p", "/tmp_workspace/tmp"]
        subprocess.run(mkdir_cmd, capture_output=True)

        cp_cmd = ["docker", "cp", f"{tmp_path}/.", f"{task_id}:/tmp_workspace/tmp/"]
        
        logger.info("[%s] Copying temp files: %s → /tmp_workspace/tmp", task_id, tmp_path)
        cp_r = subprocess.run(cp_cmd, capture_output=True, text=True)
        
        if cp_r.returncode != 0:
            logger.error("[%s] File copy failed: %s", task_id, cp_r.stderr)
        else:
            logger.info("[%s] Temp file copy complete", task_id)

def setup_workspace(task_id: str, thinking: str | None = None) -> None:
    logger.info("[%s] Copying /app → %s", task_id, TMP_WORKSPACE)
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cp -r /app/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Workspace copy failed:\n{r.stderr}")

    if thinking is not None:
        logger.info("[%s] Setting thinkingDefault to %s", task_id, thinking)
        thinking_result = subprocess.run(
            ["docker", "exec", task_id,
             "openclaw", "config", "set", "agents.defaults.thinkingDefault", thinking],
            capture_output=True, text=True,
        )
        if thinking_result.returncode != 0:
            raise RuntimeError(
                f"Failed to set thinkingDefault to {thinking}:\n{thinking_result.stderr}"
            )

    # Symlink OpenClaw workspace → TMP_WORKSPACE so the image tool's
    # media-local-roots check allows reading files under /tmp_workspace.
    subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"rm -rf /root/.openclaw/workspace && ln -s {TMP_WORKSPACE} /root/.openclaw/workspace"],
        capture_output=True, text=True,
    )

    # Expose the workspace at /root/workspace too (kensei-native flow reads
    # deliverables from here). Use a SYMLINK to TMP_WORKSPACE, not a copy: the
    # in-container grader runs against TMP_WORKSPACE, so anything an agent writes
    # to /root/workspace/... must land in TMP_WORKSPACE to be graded/collected.
    # A copy drifts after setup and silently hides those deliverables (the
    # /root/workspace/<subdir> → empty /tmp_workspace/results mismatch). Mirrors
    # the /root/.openclaw/workspace symlink above.
    subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"rm -rf /root/workspace && ln -s {TMP_WORKSPACE} /root/workspace"],
        capture_output=True, text=True,
    )

def setup_skills(
    task_id: str,
    skills: str,
    skills_path: str,
    container_skills_root: str = "/root/skills",
) -> None:
    container_skills_root = container_skills_root.rstrip("/")
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", container_skills_root],
        capture_output=True,
        text=True,
    )
    seen_dest_names: set[str] = set()
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        src_rel = line.replace("\\", "/").strip("/")
        dest_name = PurePosixPath(src_rel).name
        if not dest_name:
            logger.warning("[%s] Invalid skill path %r, skipping", task_id, line)
            continue
        if dest_name in seen_dest_names:
            logger.warning(
                "[%s] Duplicate flattened skill target %s from %s, skipping",
                task_id,
                dest_name,
                line,
            )
            continue
        seen_dest_names.add(dest_name)
        subprocess.run(
            ["docker", "exec", task_id,
             "mkdir", "-p", f"{container_skills_root}/{dest_name}"],
            capture_output=True, text=True,
        )
        r = subprocess.run(
            ["docker", "cp",
             f"{skills_path}/{src_rel}/.", f"{task_id}:{container_skills_root}/{dest_name}/"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.warning(
                "[%s] Failed to copy skill %s to %s/%s: %s",
                task_id,
                line,
                container_skills_root,
                dest_name,
                r.stderr.strip(),
            )


def inject_api_connectors(
    task_id: str,
    env_dir: str,
    required_apis: list[str],
    container_skills_root: str = "/root/skills",
) -> None:
    """Copy <env_dir>/skills/<api>-connector dirs into the container's skills
    root, plus API_DOCUMENTATION.md into /root/. No-op if env_dir or required
    APIs are empty."""
    if not env_dir or not required_apis:
        return
    env_root = Path(env_dir)
    if not env_root.is_dir():
        return
    container_skills_root = container_skills_root.rstrip("/")
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", container_skills_root],
        capture_output=True, text=True,
    )
    # openclaw's skill loader calls realpath() on every entry in its
    # bundled root and rejects anything resolving outside that root
    # ('Skipping skill path that resolves outside its configured root').
    # Symlinks from /usr/lib/.../skills -> /root/skills are rejected.
    # Copy connector files directly into the bundled root so realpath
    # returns the same root and the loader accepts them. Observed in
    # 2026-06-02 gpt trajectory (gateway.log lines 7-14, 17 skill-skip
    # warnings for etsy/google-classroom/google-drive/instagram).
    openclaw_skills_root = "/usr/lib/node_modules/openclaw/skills"
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", openclaw_skills_root],
        capture_output=True, text=True,
    )
    injected: list[str] = []
    missing_sources: list[str] = []
    for api in required_apis:
        connector = env_root / "skills" / f"{api}-connector"
        if not connector.is_dir():
            missing_sources.append(api)
            continue
        dest = f"{openclaw_skills_root}/{api}-connector"
        subprocess.run(
            ["docker", "exec", task_id, "rm", "-rf", dest],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["docker", "exec", task_id, "mkdir", "-p", dest],
            capture_output=True, text=True,
        )
        r = subprocess.run(
            ["docker", "cp", f"{connector}/.", f"{task_id}:{dest}/"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.warning("[%s] Failed to inject connector %s: %s", task_id, api, r.stderr.strip())
            continue
        injected.append(api)
    if missing_sources:
        logger.warning(
            "[%s] Connector source missing for required API(s) (no env_dir/skills/<api>-connector): %s",
            task_id, missing_sources,
        )
    # Non-connector utility skills (pdf-extract, audio-extract, video-frames,
    # self-improving-agent-*) ship in environment/skills/ alongside the 101
    # <api>-connector dirs. They are NOT keyed by required_apis. Without
    # injection, agent attempts to read /usr/lib/.../skills/pdf-extract/SKILL.md
    # and ENOENTs. Observed in alden-croft 2026-06-02T17:05:11 gateway.log line
    # 3: '[tools] read failed: ENOENT ... /skills/pdf-extract/SKILL.md'. The
    # agent image's bundled openclaw package does not ship these built-ins; the
    # harness owns them. Copy every non-connector top-level skill dir.
    skills_src = env_root / "skills"
    utility_injected: list[str] = []
    if skills_src.is_dir():
        for entry in sorted(skills_src.iterdir()):
            if not entry.is_dir() or entry.name.endswith("-connector"):
                continue
            dest = f"{openclaw_skills_root}/{entry.name}"
            subprocess.run(
                ["docker", "exec", task_id, "rm", "-rf", dest],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["docker", "exec", task_id, "mkdir", "-p", dest],
                capture_output=True, text=True,
            )
            r = subprocess.run(
                ["docker", "cp", f"{entry}/.", f"{task_id}:{dest}/"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                logger.warning("[%s] Failed to inject utility skill %s: %s", task_id, entry.name, r.stderr.strip())
                continue
            utility_injected.append(entry.name)
    api_doc = env_root / "API_DOCUMENTATION.md"
    if api_doc.is_file():
        r = subprocess.run(
            ["docker", "cp", str(api_doc), f"{task_id}:/root/API_DOCUMENTATION.md"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            logger.info("[%s] Injected API_DOCUMENTATION.md → /root/API_DOCUMENTATION.md", task_id)
        else:
            logger.warning("[%s] Failed to inject API_DOCUMENTATION.md: %s", task_id, r.stderr.strip())
    else:
        logger.info("[%s] No API_DOCUMENTATION.md at %s (skipped)", task_id, api_doc)
    if injected:
        logger.info("[%s] Injected API connectors (%d): %s", task_id, len(injected), ",".join(injected))
    if utility_injected:
        logger.info("[%s] Injected utility skills (%d): %s", task_id, len(utility_injected), ",".join(utility_injected))


def _parse_service_toml(path: Path) -> dict:
    result: dict = {}
    in_service = False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[service]":
            in_service = True
            continue
        if line.startswith("["):
            in_service = False
            continue
        if in_service and "=" in line:
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            result[key] = int(val) if key == "port" and val.isdigit() else val
    return result


def discover_services(environment_dir: Path) -> list[dict]:
    """Return [{name, port, env_var_name}] for every <env>/<svc>/service.toml."""
    services: list[dict] = []
    environment_dir = Path(environment_dir)
    if not environment_dir.is_dir():
        return services
    for entry in sorted(environment_dir.iterdir()):
        if not entry.is_dir():
            continue
        toml_path = entry / "service.toml"
        if not toml_path.is_file():
            continue
        try:
            cfg = _parse_service_toml(toml_path)
        except Exception:
            continue
        port = cfg.get("port")
        if not port:
            continue
        services.append({
            "name": entry.name,
            "port": port,
            "env_var_name": cfg.get("env_var_name", ""),
        })
    return services


def setup_mock_apis(task_id: str, environment_dir: Path,
                    required_apis: list[str]) -> dict[str, str]:
    """Per-task (non-stack) mode: copy each required mock API into the agent
    container under /opt/mock_apis/<name>, returning a {env_var_name: url} map
    for localhost ports. Use warmup_for_mock_apis() to start them."""
    env_vars: dict[str, str] = {}
    for api_name in required_apis:
        api_dir = Path(environment_dir) / api_name
        if not api_dir.is_dir():
            logger.warning("[%s] Mock API dir not found: %s", task_id, api_dir)
            continue
        service_toml = api_dir / "service.toml"
        if not service_toml.is_file():
            logger.warning("[%s] service.toml missing for %s", task_id, api_name)
            continue
        try:
            cfg = _parse_service_toml(service_toml)
        except Exception as exc:
            logger.warning("[%s] Failed to parse service.toml for %s: %s", task_id, api_name, exc)
            continue
        port = cfg.get("port")
        env_var_name = cfg.get("env_var_name")
        if not port or not env_var_name:
            logger.warning("[%s] Missing port/env_var_name for %s", task_id, api_name)
            continue
        dest = f"/opt/mock_apis/{api_name}"
        subprocess.run(["docker", "exec", task_id, "mkdir", "-p", dest], capture_output=True)
        r = subprocess.run(
            ["docker", "cp", f"{api_dir}/.", f"{task_id}:{dest}/"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.warning("[%s] Failed to copy mock API %s: %s", task_id, api_name, r.stderr)
            continue
        env_vars[env_var_name] = f"http://localhost:{port}"
        logger.info("[%s] Mock API %s copied → %s (port %s)", task_id, api_name, dest, port)
    # Stage the shared tracking middleware alongside the copied APIs so that
    # `from tracking_middleware import install_tracker` resolves when each server
    # runs with PYTHONPATH=/opt/mock_apis (see warmup_for_mock_apis).
    tracking_mw = Path(environment_dir) / "tracking_middleware.py"
    if env_vars and tracking_mw.is_file():
        subprocess.run(["docker", "exec", task_id, "mkdir", "-p", "/opt/mock_apis"],
                       capture_output=True)
        subprocess.run(["docker", "cp", str(tracking_mw),
                        f"{task_id}:/opt/mock_apis/tracking_middleware.py"],
                       capture_output=True)
    return env_vars


def warmup_for_mock_apis(required_apis: list[str], environment_dir: Path) -> str:
    lines: list[str] = []
    for api_name in required_apis:
        api_dir = Path(environment_dir) / api_name
        toml_path = api_dir / "service.toml"
        if not toml_path.is_file():
            continue
        try:
            port = _parse_service_toml(toml_path).get("port")
        except Exception:
            port = None
        if not port:
            continue
        dest = f"/opt/mock_apis/{api_name}"
        lines.append(f"pip install -q -r {dest}/requirements.txt 2>/dev/null || true")
        # server.py exposes `app` but has no __main__ block, so it must be served
        # via uvicorn (not `python server.py`, which would import and exit without
        # binding). PYTHONPATH=/opt/mock_apis lets the shared tracking_middleware
        # module import (staged by setup_mock_apis).
        lines.append(
            f"cd {dest} && PYTHONPATH=/opt/mock_apis "
            f"uvicorn server:app --host 0.0.0.0 --port {port} "
            f"> /tmp/mock_{api_name}.log 2>&1 &"
        )
    return "\n".join(lines)


def inject_openclaw_models(task_id: str, models_config: dict) -> None:
    """Inject custom models into ~/.openclaw/openclaw.json."""
    container_tmp_path = "/tmp/openclaw_models.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp_file:
        json.dump(models_config, tmp_file, indent=2)
        tmp_file_path = tmp_file.name

    try:
        cp_r = subprocess.run(
            ["docker", "cp", tmp_file_path, f"{task_id}:{container_tmp_path}"],
            capture_output=True, text=True,
        )
        if cp_r.returncode != 0:
            raise RuntimeError(f"Failed to copy models config into container:\n{cp_r.stderr}")

        inject_cmd = f"""python3 - <<'PY'
import json
import pathlib

config_path = pathlib.Path('/root/.openclaw/openclaw.json')
models_path = pathlib.Path('{container_tmp_path}')

config = json.loads(config_path.read_text()) if config_path.exists() else {{}}
models = json.loads(models_path.read_text())
config['models'] = models

config_path.write_text(json.dumps(config, indent=2))
PY"""
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Failed to inject models config:\n{r.stderr}")
    finally:
        Path(tmp_file_path).unlink(missing_ok=True)

    logger.info("[%s] Injected custom models config", task_id)



# ---- spawn-subagent skill injection (ported from june-7) ----
_SUBAGENT_SKILL_NAME = "spawn-subagent-connector"
_SUBAGENT_CONTAINER_ROOT = f"/usr/lib/node_modules/openclaw/skills/{_SUBAGENT_SKILL_NAME}"
_SUBAGENT_TURN_MARKER = f"{TMP_WORKSPACE}/.wildclaw_current_turn"
_SUBAGENT_SPAWN_TREE = f"{TMP_WORKSPACE}/spawn_tree.jsonl"


def _render_subagent_skill_md(multi_agent_config: dict | None) -> str:
    cfg = multi_agent_config or {}
    default_tools = cfg.get("default_allowed_tools") or [
        "Read", "Write", "Edit", "Grep", "Glob", "Bash",
    ]
    tools_line = ", ".join(f"`{t}`" for t in default_tools)
    tools_json = ", ".join(f'"{t}"' for t in default_tools)
    return (
        "---\n"
        f"name: {_SUBAGENT_SKILL_NAME}\n"
        "description: \"Spawn one or more bounded sub-agents to fan a single turn out "
        "across independent angles in parallel. Each sub-agent runs a focused task using "
        "its own tools and returns plain text you synthesize. Let the turn's own structure "
        "decide: fan out when (1) the user asks you to handle several independent "
        "workstreams at once or to 'run the whole thing' / 'take care of all of it', (2) "
        "the work spans 2+ named sources or angles (e.g. workbook AND guide AND photo), or "
        "(3) the turn asks for a synthesized multi-section brief or summary that pulls "
        "together several separate areas. Each independent workstream, source, or section "
        "= one sub-agent. Do NOT use for a single tight question or one sequential task. "
        "Sub-agents cannot spawn further sub-agents.\"\n"
        "metadata: {\"clawdbot\":{\"emoji\":\"🪺\"}}\n"
        "---\n"
        "\n"
        "# Spawn Sub-Agent\n"
        "\n"
        "Run one or more short, bounded sub-agents in parallel and synthesize their final "
        "text answers. **Each sub-agent has its own tools and MUST fetch its own data** — "
        "do not paste raw data into `context`; point the sub-agent at a path or query and "
        "let it Read/Grep/Bash to gather what it needs.\n"
        "\n"
        "## When to invoke (trigger checklist)\n"
        "\n"
        "Let the turn's own structure decide. Invoke this skill at the start of any turn "
        "where ANY of these is true:\n"
        "\n"
        "1. **The turn bundles several independent workstreams** — one message that asks "
        "you to *'run the whole thing'*, *'sweep every surface and put it together'*, or to "
        "(say) draft quotes AND stand up a tracker AND reconcile the books AND stage a "
        "launch. Spawn one sub-agent per independent workstream.\n"
        "2. **The prompt asks you to consider 2+ independent sources or angles** — e.g. "
        "*'look at the workbook, the guide, AND the photo'*, *'extract budget then draft "
        "narrative'*, *'reconcile imagery against the log AND build the argument'*. Each "
        "named angle = one sub-agent.\n"
        "3. **The turn asks for a synthesized multi-section deliverable** over several "
        "separate areas — e.g. *'a launch-week brief, at least four sections'* or *'a "
        "verification summary, at least five sections'*. Fan out to gather each area in "
        "parallel, then synthesize.\n"
        "\n"
        "Do NOT invoke when the turn is a single tight question or one sequential task "
        "with no independent parts.\n"
        "\n"
        "## How to invoke\n"
        "\n"
        "Pipe a JSON spec to the script on stdin and read the sub-agent's final text "
        "from stdout. **Always pass `allowed_tools` and a non-zero `max_tool_calls`** so "
        "the sub-agent can fetch its own data; otherwise it can only hallucinate from the "
        "instructions string.\n"
        "\n"
        "```bash\n"
        "echo '{\n"
        "  \"role\": \"budget-extractor\",\n"
        "  \"instructions\": \"Open /tmp_workspace/deep_roots_grant_draft.docx (use Bash + python-docx or pdftotext as needed), extract the five Deep Roots budget line items with amounts in dollars, and report them as a plain-text table plus the total.\",\n"
        f"  \"allowed_tools\": [{tools_json}],\n"
        "  \"max_tool_calls\": 12,\n"
        "  \"max_tokens\": 8000\n"
        "}' | python3 {baseDir}/scripts/spawn_subagent.py\n"
        "```\n"
        "\n"
        "Available tool names (exact strings — case sensitive):\n"
        "\n"
        "* `Read` — read a file. Input: `{\"path\": \"/tmp_workspace/foo.csv\"}`.\n"
        "* `Write` — overwrite a file. Input: `{\"path\": ..., \"content\": ...}`.\n"
        "* `Edit` — single-match string replace. Input: `{\"path\": ..., \"old_string\": ..., \"new_string\": ...}`.\n"
        "* `Grep` — regex search in a file or directory. Input: `{\"pattern\": ..., \"path\": ...}`.\n"
        "* `Glob` — filename pattern search. Input: `{\"pattern\": \"**/*.xlsx\", \"path\": \"/tmp_workspace\"}`.\n"
        "* `Bash` — run a shell command. Input: `{\"command\": ..., \"timeout\": 30}`. Use this for API calls (curl), parsing (python3 -c), pdftotext, etc.\n"
        "\n"
        "Paths must live under `/tmp_workspace`, `/root`, or `/tmp` — anything else is rejected.\n"
        "\n"
        "## Spec fields\n"
        "\n"
        "| Field | Required | Notes |\n"
        "|-------|----------|-------|\n"
        "| `role` | yes | Short name describing the sub-agent's role. |\n"
        "| `instructions` | yes | One concrete task. Name the file paths / API endpoints the child should hit; do NOT paraphrase data already in your context — let the child fetch it fresh. |\n"
        "| `allowed_tools` | **strongly recommended** | List of tool names the child may use. Default: " + tools_line + ". Pass `[]` only for pure-synthesis sub-agents that need no data access. |\n"
        "| `context` | no | Short orienting prose (objective + constraints). Do NOT dump raw file contents here — the child should Read them itself. |\n"
        "| `model` | no | Override model. Default: inherit parent's model. |\n"
        "| `max_tool_calls` | no | Default 20, ceiling 50. Set to 0 only for text-only sub-agents. |\n"
        "| `max_tokens` | no | Default 32000. No upper cap. |\n"
        "| `timeout_seconds` | no | Default 300, ceiling 600. |\n"
        "\n"
        "## Anti-patterns (do NOT do these)\n"
        "\n"
        "* **Pasting raw data into `context` and passing `allowed_tools: []`** — the child "
        "will just rewrite what you handed it, often hallucinating fields. Give it tools "
        "and a path/query instead.\n"
        "* **`max_tool_calls: 0`** — same outcome: child cannot fetch anything, can only "
        "produce prose from `instructions`. Use 8-20 for normal fan-out.\n"
        "* **One sub-agent doing everything** — defeats the purpose. One angle per "
        "sub-agent.\n"
        "* **Sub-agents calling each other** — `spawn_subagent` is blocked inside the "
        "child; do not put it in `allowed_tools`.\n"
        "\n"
        "## Rules\n"
        "\n"
        "* Each spawn is independent — children do NOT see each other.\n"
        "* Children may NOT spawn further sub-agents.\n"
        "* Issue multiple spawns in parallel by calling this skill multiple times; results land in the order they complete.\n"
        "* The sub-agent's full transcript is at "
        f"`{TMP_WORKSPACE}/subagents/{{spawn_id}}.jsonl` if you need to audit it.\n"
    )


def inject_subagent_tool(
    task_id: str,
    multi_agent_config: dict | None = None,
    *,
    subagent_director_src: str | None = None,
) -> None:
    """Install the spawn_subagent skill into the openclaw skill root.

    Mirrors the inject_api_connectors pattern: copies into the bundled root
    (symlinks rejected by openclaw's realpath check). Also initializes the
    empty spawn_tree.jsonl ledger and the turn-marker file used by the
    sub-agent script to correlate spawns to turns. No-op for backends other
    than openclaw; callers gate on AgentTaskSpec.multi_agent_enabled.
    """
    utils_dir = Path(__file__).resolve().parent
    if subagent_director_src is None:
        subagent_director_src = str(utils_dir / "subagent_director.py")
    src_path = Path(subagent_director_src)
    if not src_path.is_file():
        raise RuntimeError(
            f"inject_subagent_tool: subagent_director.py not found at {src_path}"
        )
    tools_src = utils_dir / "subagent_tools.py"
    if not tools_src.is_file():
        raise RuntimeError(
            f"inject_subagent_tool: subagent_tools.py not found at {tools_src}"
        )

    subprocess.run(
        ["docker", "exec", task_id, "rm", "-rf", _SUBAGENT_CONTAINER_ROOT],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["docker", "exec", task_id, "mkdir", "-p", f"{_SUBAGENT_CONTAINER_ROOT}/scripts"],
        capture_output=True, text=True,
    )

    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        skill_md = staging_path / "SKILL.md"
        skill_md.write_text(_render_subagent_skill_md(multi_agent_config), encoding="utf-8")
        scripts_dir = staging_path / "scripts"
        scripts_dir.mkdir()
        entry = scripts_dir / "spawn_subagent.py"
        entry.write_bytes(src_path.read_bytes())
        tools_entry = scripts_dir / "subagent_tools.py"
        tools_entry.write_bytes(tools_src.read_bytes())

        for src_file, container_dst in (
            (skill_md, f"{_SUBAGENT_CONTAINER_ROOT}/SKILL.md"),
            (entry, f"{_SUBAGENT_CONTAINER_ROOT}/scripts/spawn_subagent.py"),
            (tools_entry, f"{_SUBAGENT_CONTAINER_ROOT}/scripts/subagent_tools.py"),
        ):
            r = subprocess.run(
                ["docker", "cp", str(src_file), f"{task_id}:{container_dst}"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"inject_subagent_tool: docker cp failed for {container_dst}: {r.stderr.strip()}"
                )

    subprocess.run(
        ["docker", "exec", task_id, "chmod", "+x",
         f"{_SUBAGENT_CONTAINER_ROOT}/scripts/spawn_subagent.py"],
        capture_output=True, text=True,
    )

    init_cmd = (
        f"mkdir -p {TMP_WORKSPACE}/subagents && "
        f"touch {_SUBAGENT_SPAWN_TREE} && "
        f"printf 0 > {_SUBAGENT_TURN_MARKER}"
    )
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", init_cmd],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.warning(
            "[%s] subagent ledger init failed: %s", task_id, r.stderr.strip()
        )

    logger.info("[%s] Injected spawn_subagent skill into %s", task_id, _SUBAGENT_CONTAINER_ROOT)


def write_turn_marker(task_id: str, turn_index: int) -> None:
    """Write the current 0-indexed turn into the in-container marker file.

    Called between turns by the openclaw runner so that sub-agent spawns
    landing in the next turn carry the right turn_index in spawn_tree.jsonl.
    """
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"printf {int(turn_index)} > {_SUBAGENT_TURN_MARKER}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.warning(
            "[%s] turn marker write failed for turn %s: %s",
            task_id, turn_index, r.stderr.strip(),
        )


def run_warmup(
    task_id: str,
    warmup: str,
    *,
    detach_background: bool = False,
) -> None:
    """Execute warmup bash commands line by line inside the container (skip blank lines and comments)."""
    if not warmup.strip():
        return
    commands = [
        line.strip()
        for line in warmup.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not commands:
        return

    logger.info("[%s] Running warmup (%d commands)", task_id, len(commands))
    for idx, cmd in enumerate(commands, start=1):
        logger.info("[%s] warmup: %s", task_id, cmd)
        stripped_cmd = cmd.rstrip()
        if detach_background and stripped_cmd.endswith("&"):
            background_cmd = stripped_cmd[:-1].strip()
            log_path = f"/tmp/wildclaw_warmup_{idx}.log"
            wrapped = (
                f"cd {TMP_WORKSPACE} && "
                f"nohup /bin/bash -lc {shlex.quote(background_cmd)} "
                f"> {shlex.quote(log_path)} 2>&1 < /dev/null &"
            )
            r = subprocess.run(
                ["docker", "exec", task_id, "/bin/bash", "-lc", wrapped],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"Warmup background command failed: {cmd!r}\n{r.stderr}"
                )
            continue

        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Warmup command failed: {cmd!r}\n{r.stderr}")


def run_background(task_id: str, bash_cmd: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         f"cd {TMP_WORKSPACE} && {bash_cmd}"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    proc._log_file = log_file
    logger.info("[%s] Started process PID=%s → %s", task_id, proc.pid, log_path)
    return proc


def close_proc_log(proc: subprocess.Popen) -> None:
    """Close the log file handle created by run_background."""
    log_file = getattr(proc, "_log_file", None)
    if log_file and not log_file.closed:
        log_file.close()


_ROOT_DELIVERABLE_EXTENSIONS = (
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".md", ".txt", ".jsonl",
    ".html", ".xml", ".pdf", ".png", ".jpg", ".jpeg", ".svg",
    ".py", ".js", ".ts", ".sql", ".sh",
)


def _sweep_root_deliverables_to_workspace(task_id: str) -> None:
    sweep_cmd = "python3 - <<'PY'\n" + "\n".join([
        "import os, shutil",
        f"exts = {_ROOT_DELIVERABLE_EXTENSIONS!r}",
        f"workspace = {TMP_WORKSPACE!r}",
        "os.makedirs(workspace, exist_ok=True)",
        "moved = []",
        "skipped = {'AGENT.md','AGENTS.md','MEMORY.md','SOUL.md','USER.md',",
        "           'IDENTITY.md','HEARTBEAT.md','TOOLS.md','API_DOCUMENTATION.md'}",
        "for name in os.listdir('/root'):",
        "    src = os.path.join('/root', name)",
        "    if not os.path.isfile(src):",
        "        continue",
        "    if name.startswith('.') or name in skipped:",
        "        continue",
        "    if not name.lower().endswith(exts):",
        "        continue",
        "    dst = os.path.join(workspace, name)",
        "    if os.path.exists(dst):",
        "        continue",
        "    try:",
        "        shutil.copy2(src, dst)",
        "        moved.append(name)",
        "    except OSError:",
        "        pass",
        "if moved: print('swept:', ','.join(moved))",
        "PY",
    ])
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", sweep_cmd],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0 and r.stdout.strip().startswith("swept:"):
        logger.info("[%s] Recovered /root deliverables: %s", task_id, r.stdout.strip())


def collect_output_from_container(
    task_id: str,
    output_dir: Path,
    *,
    include_workspace_changes: bool = False,
) -> None:
    """Collect task output files from the container to output_dir/task_output/.

    Collection strategy:
      0. Sweep deliverable-shaped files an agent left at /root/ (top-level)
         into /tmp_workspace/.
      1. All files under /tmp/openclaw/ (agent session logs, etc.)
      2. The full /tmp_workspace/ tree (into workspace_full/) — forensic copy
         including staged inputs, persona files, agent scratch, and deliverables.
      3. artifacts/ — agent-produced files only, computed by diffing the
         current workspace against the baseline snapshot taken right before
         the agent ran. This is the canonical place to look for what the
         model produced. If no baseline was taken (legacy backends), this
         dir is silently skipped and workspace_full/ remains the source.
    """
    task_output_dir = output_dir / "task_output"
    task_output_dir.mkdir(parents=True, exist_ok=True)

    _copy_dir_from_container(task_id, "/tmp/openclaw/.", str(task_output_dir))

    _sweep_root_deliverables_to_workspace(task_id)

    workspace_out = task_output_dir / "workspace"
    workspace_out.mkdir(parents=True, exist_ok=True)

    if include_workspace_changes:
        ok = _copy_dir_from_container(task_id, f"{TMP_WORKSPACE}/.", str(workspace_out))
        if not ok:
            logger.warning("[%s] workspace directory does not exist or is empty", task_id)
        return

    full_out = task_output_dir / "workspace_full"
    full_out.mkdir(parents=True, exist_ok=True)
    if not _copy_dir_from_container(task_id, f"{TMP_WORKSPACE}/.", str(full_out)):
        logger.debug("[%s] full workspace sweep found nothing to collect", task_id)

    artifacts_out = task_output_dir / "artifacts"
    artifacts_out.mkdir(parents=True, exist_ok=True)
    _copy_changed_workspace_outputs_from_container(task_id, artifacts_out)


def snapshot_workspace_state(task_id: str) -> None:
    snapshot_cmd = "python3 - <<'PY'\n" + "\n".join([
        "import json",
        "from pathlib import Path",
        f"root = Path({TMP_WORKSPACE!r})",
        f"snapshot_path = Path({WORKSPACE_BASELINE_PATH!r})",
        "files = {}",
        "if root.exists():",
        "    for path in root.rglob('*'):",
        "        if not (path.is_file() or path.is_symlink()):",
        "            continue",
        "        try:",
        "            stat = path.lstat()",
        "        except OSError:",
        "            continue",
        "        rel = path.relative_to(root).as_posix()",
        "        files[rel] = {",
        "            'size': stat.st_size,",
        "            'mtime_ns': stat.st_mtime_ns,",
        "            'is_symlink': path.is_symlink(),",
        "        }",
        "snapshot_path.write_text(json.dumps(files, sort_keys=True), encoding='utf-8')",
        "PY",
    ])
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", snapshot_cmd],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Workspace baseline snapshot failed:\n{r.stderr}")
    logger.info("[%s] Workspace baseline saved at %s", task_id, WORKSPACE_BASELINE_PATH)


def _copy_changed_workspace_outputs_from_container(task_id: str, dest: Path) -> None:
    list_cmd = "python3 - <<'PY'\n" + "\n".join([
        "import json",
        "from pathlib import Path",
        f"root = Path({TMP_WORKSPACE!r})",
        f"snapshot_path = Path({WORKSPACE_BASELINE_PATH!r})",
        "if not snapshot_path.exists():",
        "    print(json.dumps([]))",
        "    raise SystemExit(0)",
        "before = json.loads(snapshot_path.read_text(encoding='utf-8'))",
        "excluded_prefixes = (",
        "    'results/', 'gt/', 'tmp/', '.git/',",
        "    'node_modules/', '.venv/', 'venv/', '__pycache__/', '.cache/',",
        ")",
        "excluded_names = {'results', 'gt', 'tmp', '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache'}",
        "changed = []",
        "if root.exists():",
        "    for path in root.rglob('*'):",
        "        if not (path.is_file() or path.is_symlink()):",
        "            continue",
        "        rel = path.relative_to(root).as_posix()",
        "        if rel in excluded_names or rel.startswith(excluded_prefixes):",
        "            continue",
        "        try:",
        "            stat = path.lstat()",
        "        except OSError:",
        "            continue",
        "        current = {",
        "            'size': stat.st_size,",
        "            'mtime_ns': stat.st_mtime_ns,",
        "            'is_symlink': path.is_symlink(),",
        "        }",
        "        if before.get(rel) != current:",
        "            changed.append(rel)",
        "print(json.dumps(sorted(changed)))",
        "PY",
    ])
    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", list_cmd],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        logger.warning("[%s] Failed to list changed workspace outputs: %s", task_id, r.stderr.strip())
        return

    try:
        changed_paths = json.loads(r.stdout.strip() or "[]")
    except json.JSONDecodeError:
        logger.warning("[%s] Failed to parse changed workspace output list: %s", task_id, r.stdout[:200])
        return

    for rel_path in changed_paths:
        if not isinstance(rel_path, str) or rel_path.startswith("/") or ".." in Path(rel_path).parts:
            continue
        dest_path = dest / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_from_container(task_id, f"{TMP_WORKSPACE}/{rel_path}", dest_path)


def inject_lobster_workspace(task_id: str, workspace_path: str) -> None:
    """Copy the entire lobster workspace into /root/ (the OpenClaw workspace in the image).

    This brings in everything: SOUL.md, USER.md, MEMORY.md, memory/, skills/, etc.
    """
    r = subprocess.run(
        ["docker", "cp", f"{workspace_path}/.", f"{task_id}:/root/"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        logger.error("[%s] Lobster workspace copy failed: %s", task_id, r.stderr)
        return
    logger.info("[%s] Lobster workspace copied: %s → /root/", task_id, workspace_path)

    ls_r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c",
         "ls -1 /root/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null | sort"],
        capture_output=True, text=True,
    )
    if ls_r.returncode == 0 and ls_r.stdout.strip():
        md_files = [name for name in ls_r.stdout.strip().splitlines() if name]
        logger.info("[%s] Persona MDs landed at /root/: %s", task_id, md_files)
    else:
        logger.warning("[%s] Persona MD copy succeeded but /root/ contains no *.md", task_id)


def _copy_dir_from_container(task_id: str, src: str, dest: str) -> bool:
    r = subprocess.run(
        ["docker", "cp", f"{task_id}:{src}", dest],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        logger.info("[%s] Collected container directory %s → %s", task_id, src, dest)
        return True
    return False


def _copy_file_from_container(task_id: str, src: str, dest: Path) -> bool:
    r = subprocess.run(
        ["docker", "cp", f"{task_id}:{src}", str(dest)],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logger.info("[%s] Collected container file %s → %s", task_id, src, dest)
        return True
    logger.warning("[%s] Container file copy failed (%s): %s", task_id, src, r.stderr.strip())
    return False


def snapshot_persona_and_data_from_container(
    task_id: str,
    persona_entries: "list[str]",
    data_rel_paths: "list[str]",
    dest_dir: Path,
) -> dict:
    """Copy the FINAL state of the persona/ and data/ folders out of the running
    agent container into ``dest_dir/{persona,data}/``.

    Used to build the post-run ``workspace_after`` snapshot. ``persona_entries``
    are the top-level names the task's persona dir shipped (copied into ``/root/``
    by ``inject_lobster_workspace``); ``data_rel_paths`` are the staged data
    attachment ``storedAs`` rel paths (under ``TMP_WORKSPACE``). Best-effort: a
    missing/edited/deleted file is skipped, not fatal. Must be called BEFORE the
    agent container is removed.
    """
    persona_dest = dest_dir / "persona"
    data_dest = dest_dir / "data"
    persona_dest.mkdir(parents=True, exist_ok=True)
    data_dest.mkdir(parents=True, exist_ok=True)
    n_persona = 0
    for name in persona_entries:
        if not name or name.startswith("/") or ".." in Path(name).parts:
            continue
        # Persona files live at /root/ in the container (some are dirs: memory/, skills/).
        if _copy_dir_from_container(task_id, f"/root/{name}", str(persona_dest)):
            n_persona += 1
    # Capture the FULL final workspace tree — not just the originally-staged
    # attachment manifest. The agent writes its deliverables (draft .eml, logs,
    # agendas, updated sheets) under /root/workspace -> TMP_WORKSPACE; replaying
    # only the input manifest produced an after-snapshot byte-identical to
    # before, losing every agent artifact (IAN report Pointer 4). `docker cp
    # <c>:/tmp_workspace/. dest` copies the whole tree (original + produced).
    n_data = 0
    if _copy_dir_from_container(task_id, f"{TMP_WORKSPACE}/.", str(data_dest)):
        try:
            n_data = sum(1 for p in data_dest.rglob("*") if p.is_file())
        except OSError:
            n_data = -1
    else:
        # Fallback: per-file copy of the declared attachments if the full-tree
        # copy failed (e.g. workspace is a symlink the daemon won't follow).
        for rel in data_rel_paths:
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                continue
            dst = data_dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if _copy_file_from_container(task_id, f"{TMP_WORKSPACE}/{rel}", dst):
                n_data += 1
    logger.info("[%s] workspace_after: collected %d persona entr(ies), %d data file(s)",
                task_id, n_persona, n_data)
    return {"persona": n_persona, "data": n_data}
