"""Harbor docker-compose.yaml generator.

Walks `<env_dir>/<svc>/service.toml` and emits a compose file with a `main`
service (sleep infinity, depends_on each svc service_healthy) plus one entry
per mock service (build context, expose, healthcheck via python3 urllib).

Port of `_generate_harbor_docker_compose` from kensei2.py (line 3035).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:  # pragma: no cover
        tomllib = None  # type: ignore


def _parse_service_toml_fallback(path: Path) -> dict:
    """Minimal TOML parser when tomllib unavailable."""
    data = {
        "name": path.parent.name,
        "port": 8000,
        "env_var_name": "",
        "healthcheck_path": "/health",
        "k8s_image": "",
        "cpu_request": "25m",
        "memory_request": "128Mi",
        "memory_limit": "256Mi",
    }
    if not path.exists():
        return data
    section: Optional[str] = None
    try:
        text = path.read_text()
    except Exception:
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if section == "service":
            if k == "name":
                data["name"] = v
            elif k == "port":
                try:
                    data["port"] = int(v)
                except ValueError:
                    pass
            elif k == "env_var_name":
                data["env_var_name"] = v
            elif k == "healthcheck_path":
                data["healthcheck_path"] = v
        elif section == "k8s":
            if k == "image":
                data["k8s_image"] = v
            elif k == "cpu_request":
                data["cpu_request"] = v
            elif k == "memory_request":
                data["memory_request"] = v
            elif k == "memory_limit":
                data["memory_limit"] = v
    return data


def _parse_service_toml(path: Path) -> dict:
    if tomllib is None:
        return _parse_service_toml_fallback(path)
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except Exception:
        return _parse_service_toml_fallback(path)
    svc = doc.get("service", {}) if isinstance(doc, dict) else {}
    k8s = doc.get("k8s", {}) if isinstance(doc, dict) else {}
    return {
        "name": svc.get("name", path.parent.name),
        "port": int(svc.get("port", 8000) or 8000),
        "env_var_name": svc.get("env_var_name", ""),
        "healthcheck_path": svc.get("healthcheck_path", "/health"),
        "k8s_image": k8s.get("image", ""),
        "cpu_request": k8s.get("cpu_request", "25m"),
        "memory_request": k8s.get("memory_request", "128Mi"),
        "memory_limit": k8s.get("memory_limit", "256Mi"),
    }


def discover_services(env_dir: Path) -> List[dict]:
    """List service metadata for every `<env_dir>/<svc>/service.toml`."""
    services: List[dict] = []
    if not env_dir.is_dir():
        return services
    for entry in sorted(env_dir.iterdir()):
        if not entry.is_dir():
            continue
        toml_path = entry / "service.toml"
        if not toml_path.exists():
            continue
        services.append(_parse_service_toml(toml_path))
    return services


def _healthcheck_line(port: int, path: str) -> str:
    """skillsbench flow-sequence healthcheck: a python urllib.urlopen against /health."""
    url = f"http://localhost:{port}{path}"
    return (
        '      test: ["CMD", "python3", "-c", '
        '"import urllib.request; urllib.request.urlopen(\'' + url + '\')"]'
    )


def sanitize_task_name(name: str) -> str:
    """Lower-case, collapse non-alphanumerics to `_` (for MAIN_IMAGE_NAME default)."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def generate_harbor_compose(env_dir: Path,
                            services: Optional[Iterable[Mapping]] = None,
                            *,
                            main_image_name: str = "skillsbench-task",
                            current_date: str = "") -> str:
    """Render the Harbor `data/environment/docker-compose.yaml` in the skillsbench
    delivery format: a `main` service (env + volumes + deploy limits) that depends
    on every mock `<svc>-api` plus a built `litellm-proxy`.
    """
    if services is None:
        services = discover_services(env_dir)
    svc_list = list(services)

    L: List[str] = ["services:"]

    # ---- main ----------------------------------------------------------------
    L.append("  main:")
    L.append("    build:")
    L.append("      context: .")
    L.append("      dockerfile: Dockerfile")
    L.append(f"    image: ${{MAIN_IMAGE_NAME:-{main_image_name}}}")
    L.append('    command: ["sh", "-c", "sleep infinity"]')
    L.append("    depends_on:")
    for svc in svc_list:
        L.append("")
        L.append(f"      {svc['name']}:")
        L.append("        condition: service_healthy")
    L.append("")
    L.append("      litellm-proxy:")
    L.append("        condition: service_healthy")
    L.append("    environment:")
    L.append("")
    for svc in svc_list:
        env_var = svc.get("env_var_name") or (str(svc["name"]).upper().replace("-", "_"))
        L.append(f"      - {env_var}=http://{svc['name']}:{int(svc['port'])}")
    L.append("")
    L.append("      - LITELLM_BASE_URL=http://litellm-proxy:4000")
    L.append("      - OPENAI_API_BASE=http://litellm-proxy:4000/v1")
    L.append("      - OPENAI_API_KEY=placeholder")
    L.append("      - LLAMA_API_KEY=${LLAMA_API_KEY}")
    if current_date:
        L.append(f"      - CURRENT_DATE={current_date}")
    L.append("      - TEST_DIR=${TEST_DIR:-/tests}")
    L.append("    volumes:")
    L.append("      - ${HOST_VERIFIER_LOGS_PATH:-./logs/verifier}:${ENV_VERIFIER_LOGS_PATH:-/logs/verifier}")
    L.append("      - ${HOST_AGENT_LOGS_PATH:-./logs/agent}:${ENV_AGENT_LOGS_PATH:-/logs/agent}")
    L.append("    deploy:")
    L.append("      resources:")
    L.append("        limits:")
    L.append('          cpus: "${CPUS:-1}"')
    L.append('          memory: "${MEMORY:-4096M}"')
    L.append("")

    # ---- one entry per mock service -----------------------------------------
    for svc in svc_list:
        name = svc["name"]
        port = int(svc["port"])
        hc_path = svc.get("healthcheck_path") or "/health"
        L.append("")
        L.append(f"  {name}:")
        L.append("    build:")
        L.append(f"      context: ./{name}")
        L.append("      dockerfile: Dockerfile")
        L.append("    expose:")
        L.append(f'      - "{port}"')
        L.append("    healthcheck:")
        L.append(_healthcheck_line(port, hc_path))
        L.append("      interval: 2s")
        L.append("      timeout: 5s")
        L.append("      retries: 15")
        L.append("      start_period: 5s")

    # ---- litellm-proxy -------------------------------------------------------
    L.append("")
    L.append("")
    L.append("  litellm-proxy:")
    L.append("    build:")
    L.append("      context: ./litellm-proxy")
    L.append("      dockerfile: Dockerfile")
    L.append("    expose:")
    L.append('      - "4000"')
    L.append("    environment:")
    L.append("      - LLAMA_API_KEY=${LLAMA_API_KEY}")
    L.append("    healthcheck:")
    L.append(_healthcheck_line(4000, "/health"))
    L.append("      interval: 2s")
    L.append("      timeout: 5s")
    L.append("      retries: 15")
    L.append("      start_period: 5s")

    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Buildable litellm-proxy/ directory (Dockerfile + config) for the bundle.
# --------------------------------------------------------------------------- #

_LITELLM_PROXY_DOCKERFILE = """\
# Buildable LiteLLM proxy for the delivery bundle. Mirrors the harness sidecar
# (src/utils/litellm_sidecar.py): the stock LiteLLM image started with a config
# on :4000. The delivery pipeline supplies LLAMA_API_KEY at run time.
FROM ghcr.io/berriai/litellm:main-stable
COPY config.yaml /app/config.yaml
EXPOSE 4000
CMD ["--config", "/app/config.yaml", "--port", "4000"]
"""

_LITELLM_PROXY_CONFIG = """\
# Minimal LiteLLM proxy config. The delivery pipeline routes model calls via the
# LLAMA_API_KEY env var (OpenAI-compatible). Adjust model_list to the delivery
# team's exact routing if needed.
model_list:
  - model_name: gpt-3.5-turbo
    litellm_params:
      model: openai/gpt-3.5-turbo
      api_key: os.environ/LLAMA_API_KEY
litellm_settings:
  drop_params: true
general_settings:
  master_key: ${LITELLM_MASTER_KEY:-sk-placeholder}
"""


def write_litellm_proxy_dir(env_dir: Path) -> None:
    """Write a buildable `<env_dir>/litellm-proxy/` (Dockerfile + config.yaml) so
    the compose `litellm-proxy` service has a real build context."""
    proxy = Path(env_dir) / "litellm-proxy"
    proxy.mkdir(parents=True, exist_ok=True)
    (proxy / "Dockerfile").write_text(_LITELLM_PROXY_DOCKERFILE, encoding="utf-8")
    (proxy / "config.yaml").write_text(_LITELLM_PROXY_CONFIG, encoding="utf-8")
