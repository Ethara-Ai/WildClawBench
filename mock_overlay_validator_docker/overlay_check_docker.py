#!/usr/bin/env python3
"""Validate per-task mock_data overlays inside the REAL mock container.

script/coerce_dryrun.py checks overlays by importing the data loaders on the
host — fast, but an imitation (host Python != container Python, no bind
mounts, no server boot). This script closes that gap: for each task it starts
a throwaway kensei3-mocks:v1 container with the task's mock_data/<api>/ files
bind-mounted read-only over the baseline — the exact mounts, image and
health gate that eval/run_batch.py:_start_task_mock_stack uses in a real run
— and reports OK/FAIL per API.

Checks, in order:
  static  - overlaid API exists in the environment/ catalog (else the runtime
            silently never serves it), no ':' in filenames (docker -v cannot
            mount those), subdirs/empty dirs flagged (the runtime mounts
            top-level files only), orphan-filename heuristic (a file neither
            present in the baseline API dir nor referenced by the API's code
            is likely never read — the "misnamed file" silent no-op).
  docker  - image present (rebuilt if stale, mirroring run_batch), container
            starts with the task's overlays + MOCK_ENABLED_APIS, every mounted
            file's sha256 inside the container matches the host file, and each
            overlaid API answers /health within --timeout (same 180s default
            as the runtime). A service whose overlay data fails coercion
            crashes at import and never turns healthy — that is the schema
            check, executed by the real loader in the real image.

On failure the per-service stderr log (/tmp/<api>.err) is dumped, which is
exactly what a real run would have died with ("overlay CSV likely malformed").

Usage:
  python3 script/overlay_check_docker.py                     # all tracked tasks
  python3 script/overlay_check_docker.py amanda_hayes_01     # one task by name
  python3 script/overlay_check_docker.py path/to/task_dir    # or by path
  Flags: --timeout N  --no-build  --keep  --strict  --env-dir DIR

Exit codes: 0 all OK, 1 any FAIL (or WARN with --strict), 2 usage/preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.utils.mock_stack import (  # noqa: E402
    MOCK_IMAGE,
    _compute_mock_content_hash,
    _image_content_hash,
    build_mock_image_if_needed,
    read_api_ports,
    start_mock_stack,
    stop_mock_stack,
)

INPUT_DIR = REPO_ROOT / "input"

_EXEC_TIMEOUT = 20  # seconds, for every docker exec/inspect probe

logger = logging.getLogger("overlay_check")


# --------------------------------------------------------------------------
# Finding model: (level, api, message). FAIL fails the run; WARN fails only
# under --strict; INFO is evidence for the operator.
# --------------------------------------------------------------------------

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"


class TaskReport:
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, api: str, message: str) -> None:
        self.rows.append((level, api, message))

    @property
    def fail_count(self) -> int:
        return sum(1 for lvl, _, _ in self.rows if lvl == FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for lvl, _, _ in self.rows if lvl == WARN)


# --------------------------------------------------------------------------
# Task discovery + overlay resolution (mirrors eval/run_batch.py
# _resolve_task_apis: top-level files of each input/<task>/mock_data/<api>/)
# --------------------------------------------------------------------------

def tracked_task_dirs() -> list[Path]:
    """Git-tracked task dirs with mock_data (dir-scan fallback without git)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "input/"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return sorted(p for p in INPUT_DIR.iterdir()
                      if p.is_dir() and (p / "mock_data").is_dir())
    names = sorted({line.split("/", 2)[1] for line in out.splitlines()
                    if line.startswith("input/") and len(line.split("/")) > 2})
    return [INPUT_DIR / n for n in names if (INPUT_DIR / n / "mock_data").is_dir()]


def resolve_overlays(task_dir: Path) -> dict[str, dict[str, str]]:
    """{api: {filename: abs_host_path}} — same shape start_mock_stack receives."""
    mock_root = task_dir / "mock_data"
    if not mock_root.is_dir():
        return {}
    return {
        api_dir.name: {
            p.name: str(p.resolve())
            for p in sorted(api_dir.iterdir()) if p.is_file()
        }
        for api_dir in sorted(mock_root.iterdir())
        if api_dir.is_dir() and any(p.is_file() for p in api_dir.iterdir())
    }


# --------------------------------------------------------------------------
# Static checks (no docker needed)
# --------------------------------------------------------------------------

def _api_source_text(api_dir: Path) -> str:
    chunks: list[str] = []
    for py in sorted(api_dir.glob("*.py")):
        try:
            chunks.append(py.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


_OVERLAY_REF_MAX_BYTES = 5_000_000  # don't slurp huge blobs hunting for refs


def _quoted_in(text: str, name: str) -> bool:
    return f'"{name}"' in text or f"'{name}'" in text


def _overlay_is_reachable(fname: str, baseline_files: set[str],
                          source_text: str, sibling_texts: list[str]) -> bool:
    """Would the service ever read this overlay file?

    Reachability channels, mirroring environment/_mutable_store.py
    read_seed_with_ctx (auto-dispatch + extension swapping):
      1. exact filename exists in the baseline API dir (loader reads it there);
      2. filename appears as a string literal in the API's code;
      3. .csv/.json extension swap: read_seed_with_ctx prefers a sibling .csv
         over a requested .json (CSV-overlay-wins) and falls back .csv->.json,
         so 'customers.csv' is read whenever 'customers.json' is the baseline
         seed (the common case since the JSON migration);
      4. bare-stem string literal in code (loaders register tables and probe
         seeds by suffix-less stem);
      5. referenced by name inside ANOTHER overlay file of the same API
         (data-driven blobs, e.g. a drive files.csv row naming an .md file
         served via the flat blob fallback).
    """
    if fname in baseline_files or _quoted_in(source_text, fname):
        return True
    stem, ext = Path(fname).stem, Path(fname).suffix.lower()
    if ext in (".csv", ".json"):
        sibling = stem + (".json" if ext == ".csv" else ".csv")
        if sibling in baseline_files or _quoted_in(source_text, sibling):
            return True
    if _quoted_in(source_text, stem):
        return True
    return any(fname in text for text in sibling_texts)


def static_checks(task_dir: Path, env_dir: Path, catalog: dict[str, int],
                  overlays: dict[str, dict[str, str]], report: TaskReport,
                  ) -> dict[str, dict[str, str]]:
    """Validate overlay layout; return only the APIs safe to take into docker."""
    mock_root = task_dir / "mock_data"
    valid: dict[str, dict[str, str]] = {}

    stray = sorted(p.name for p in mock_root.iterdir() if p.is_file())
    if stray:
        report.add(WARN, "-",
                   f"files directly under mock_data/ are IGNORED by the "
                   f"runtime (they must live in mock_data/<api>/): {stray}")

    for api_dir in sorted(p for p in mock_root.iterdir() if p.is_dir()):
        api = api_dir.name

        if api not in catalog:
            report.add(FAIL, api,
                       f"not in environment/ catalog — the runtime drops it and "
                       f"never serves this data (is the folder named "
                       f"'<service>-api' and present under {env_dir.name}/?)")
            continue

        subdirs = sorted(p.name for p in api_dir.iterdir() if p.is_dir())
        if subdirs:
            report.add(WARN, api,
                       f"subdirectories {subdirs} are IGNORED — the runtime "
                       f"mounts top-level files of mock_data/{api}/ only")

        files = overlays.get(api) or {}
        if not files:
            report.add(WARN, api,
                       "empty overlay dir: enables the API but replaces no "
                       "data (baseline defaults will be served)")
            continue

        bad_names = sorted(f for f in files if ":" in f)
        if bad_names:
            report.add(FAIL, api,
                       f"filenames with ':' cannot be bind-mounted by docker: "
                       f"{bad_names}")
            continue

        baseline_files = {p.name for p in (env_dir / api).iterdir() if p.is_file()}
        source_text = _api_source_text(env_dir / api)
        overlay_texts: dict[str, str] = {}
        for other, other_path in files.items():
            try:
                p = Path(other_path)
                if p.stat().st_size <= _OVERLAY_REF_MAX_BYTES:
                    overlay_texts[other] = p.read_text(encoding="utf-8",
                                                       errors="replace")
            except OSError:
                continue
        for fname in sorted(files):
            sibling_texts = [t for n, t in overlay_texts.items() if n != fname]
            if not _overlay_is_reachable(fname, baseline_files, source_text,
                                         sibling_texts):
                report.add(WARN, api,
                           f"'{fname}' matches no baseline seed (even after "
                           f".csv/.json swap), no string literal in {api} code, "
                           f"and no reference from a sibling overlay file — it "
                           f"will mount but likely never be read (misnamed?)")

        valid[api] = files

    return valid


# --------------------------------------------------------------------------
# Docker checks
# --------------------------------------------------------------------------

def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_image(env_dir: Path, no_build: bool) -> tuple[bool, str]:
    if no_build:
        r = subprocess.run(["docker", "image", "inspect", MOCK_IMAGE],
                           capture_output=True)
        if r.returncode != 0:
            return False, f"image {MOCK_IMAGE} not present and --no-build given"
        if _image_content_hash(MOCK_IMAGE) != _compute_mock_content_hash(env_dir):
            logger.warning(
                "image %s is STALE vs %s/ — results may not reflect current "
                "baseline code/data (drop --no-build to rebuild)",
                MOCK_IMAGE, env_dir.name)
        return True, ""
    # Mirrors run_batch: content-hash cached, rebuilds only when stale.
    if not build_mock_image_if_needed(env_dir):
        return False, f"docker build of {MOCK_IMAGE} failed"
    return True, ""


def _exec(container: str, argv: list[str],
          timeout: float = _EXEC_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", container, *argv],
                          capture_output=True, text=True, timeout=timeout)


def _sha256_host(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_mounts(container: str, overlays: dict[str, dict[str, str]],
                  report: TaskReport) -> None:
    """Prove the container sees the task's bytes at the exact loader paths."""
    for api, files in sorted(overlays.items()):
        for fname, host_path in sorted(files.items()):
            target = f"/opt/mocks/{api}/{fname}"
            try:
                r = _exec(container, ["sha256sum", target])
            except subprocess.TimeoutExpired:
                report.add(FAIL, api, f"mount probe timed out for {target}")
                continue
            if r.returncode != 0:
                report.add(FAIL, api,
                           f"{target} missing inside container: "
                           f"{(r.stderr or '').strip()[:200]}")
                continue
            got = (r.stdout or "").split()[0] if (r.stdout or "").split() else ""
            try:
                want = _sha256_host(host_path)
            except OSError as exc:
                report.add(FAIL, api,
                           f"host overlay file unreadable: {host_path} ({exc})")
                continue
            if got != want:
                report.add(FAIL, api,
                           f"{target} content differs from host file "
                           f"{host_path} (mount not applied?)")


def _container_running(container: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            capture_output=True, text=True, timeout=_EXEC_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Transient inspect hiccup: assume still running so the health wait
        # keeps retrying instead of reporting a false "container died".
        return True
    return r.returncode == 0 and (r.stdout or "").strip() == "true"


def wait_ports_healthy_diag(container: str, port_to_api: dict[int, str],
                            timeout: float) -> tuple[bool, dict[int, bool]]:
    """Same pass criterion as mock_stack.wait_for_ports_healthy (curl -sf
    --max-time 2 http://localhost:<port>/health inside the container, retried
    every 2s until timeout) but tracks per-port status so a failure names the
    culprit API instead of the whole set, and bails early if the container
    itself dies."""
    ports = sorted(port_to_api)
    status: dict[int, bool] = {p: False for p in ports}
    if not ports:
        return True, status
    probe = "; ".join(
        f"if curl -sf --max-time 2 http://localhost:{p}/health >/dev/null; "
        f"then echo {p}:ok; else echo {p}:fail; fi"
        for p in ports
    )
    # Worst case the probe needs ~2s per port; keep exec timeout above that so
    # a many-API task can't time out the probe itself and fake a FAIL.
    probe_timeout = max(_EXEC_TIMEOUT, 3 * len(ports) + 10)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _container_running(container):
            logger.error("[%s] container died while waiting for health", container)
            return False, status
        try:
            r = _exec(container, ["/bin/bash", "-c", probe],
                      timeout=probe_timeout)
        except subprocess.TimeoutExpired:
            time.sleep(2)
            continue
        for line in (r.stdout or "").splitlines():
            part = line.strip().split(":")
            if len(part) == 2 and part[0].isdigit():
                status[int(part[0])] = part[1] == "ok"
        if all(status.values()):
            return True, status
        time.sleep(2)
    return False, status


def dump_service_logs(container: str, apis: list[str], report: TaskReport) -> None:
    """Read /tmp/<api>.err (uvicorn stderr — where a CoerceError traceback
    lands) before the container is removed. Mirrors run_batch._dump_mock_logs."""
    for api in sorted(apis):
        for suffix in (".err", ".log"):
            try:
                r = _exec(container, ["cat", f"/tmp/{api}{suffix}"])
            except subprocess.TimeoutExpired:
                continue
            text = (r.stdout or "").strip()
            if text:
                tail = "\n".join(text.splitlines()[-25:])
                report.add(INFO, api, f"/tmp/{api}{suffix} (last 25 lines):\n{tail}")
                break  # .err is authoritative; fall to .log only if .err empty


def read_enabled_count(container: str) -> tuple[int, int] | None:
    """Parse 'mock-stack: running X/Y APIs' printed by the entrypoint."""
    for _ in range(10):
        try:
            r = subprocess.run(["docker", "logs", container],
                               capture_output=True, text=True,
                               timeout=_EXEC_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            time.sleep(1)
            continue
        m = re.search(r"mock-stack: running (\d+)/(\d+) APIs",
                      (r.stdout or "") + (r.stderr or ""))
        if m:
            return int(m.group(1)), int(m.group(2))
        time.sleep(1)
    return None


def docker_checks(task_dir: Path, env_dir: Path, catalog: dict[str, int],
                  overlays: dict[str, dict[str, str]], report: TaskReport,
                  timeout: float, keep: bool) -> None:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", task_dir.name)[:30].lower().strip("_.-")
    container = f"mocks-ovl-{safe or 'task'}-{uuid.uuid4().hex[:6]}"
    port_to_api = {catalog[api]: api for api in overlays}
    enabled = set(overlays)

    try:
        try:
            # Same call a real run makes (eval/run_batch._start_task_mock_stack),
            # minus the drift/admin plane which needs no validation here.
            start_mock_stack(container, "bridge", overlays=overlays,
                             enabled_apis=enabled)
        except Exception as exc:
            report.add(FAIL, "-", f"container failed to start: {exc}")
            return

        running = read_enabled_count(container)
        if running is None:
            report.add(WARN, "-", "could not read service-count line from "
                                  "container logs")
        elif running[0] != len(enabled):
            report.add(WARN, "-",
                       f"container is running {running[0]}/{running[1]} services "
                       f"but {len(enabled)} were requested — MOCK_ENABLED_APIS "
                       f"filter did not match (name typo would fall back to all)")

        verify_mounts(container, overlays, report)

        ok, status = wait_ports_healthy_diag(container, port_to_api, timeout)
        if ok:
            for port, api in sorted(port_to_api.items()):
                report.add(INFO, api, f"healthy on :{port} with overlay applied")
        else:
            for port, api in sorted(port_to_api.items()):
                if status[port]:
                    report.add(INFO, api,
                               f"healthy on :{port} with overlay applied")
            sick = [api for port, api in sorted(port_to_api.items())
                    if not status[port]]
            for api in sick:
                report.add(FAIL, api,
                           f"/health not up within {timeout:.0f}s — a real run "
                           f"would abort here (overlay data likely fails the "
                           f"loader; traceback below)")
            dump_service_logs(container, sick, report)
    finally:
        if keep:
            logger.info("[%s] kept for inspection (docker exec -it %s bash; "
                        "docker rm -f %s when done)", container, container,
                        container)
        else:
            stop_mock_stack(container)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def check_task(task_dir: Path, env_dir: Path, catalog: dict[str, int],
               timeout: float, keep: bool) -> TaskReport | None:
    """Returns None when the task has no mock_data (nothing to validate)."""
    report = TaskReport(task_dir.name)
    if not (task_dir / "mock_data").is_dir():
        return None
    overlays = resolve_overlays(task_dir)
    valid = static_checks(task_dir, env_dir, catalog, overlays, report)
    if valid:
        try:
            docker_checks(task_dir, env_dir, catalog, valid, report, timeout,
                          keep)
        except Exception as exc:  # container already torn down by its finally
            report.add(FAIL, "-",
                       f"checker error during docker phase: "
                       f"{type(exc).__name__}: {exc}")
    elif not report.rows:
        report.add(WARN, "-", "mock_data/ exists but contains no API dirs")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate task mock_data overlays inside the real "
                    "kensei3-mocks container (docker-backed preflight; "
                    "see coerce_dryrun.py for the fast host-only check).")
    ap.add_argument("tasks", nargs="*",
                    help="task names under input/ or paths to task dirs "
                         "(default: all git-tracked tasks with mock_data/)")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="per-task health-wait seconds; matches the runtime's "
                         "180s gate (default: %(default)s)")
    ap.add_argument("--no-build", action="store_true",
                    help="never docker-build; use the existing image even if "
                         "stale vs environment/")
    ap.add_argument("--keep", action="store_true",
                    help="leave each container running for manual inspection")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    ap.add_argument("--env-dir", type=Path, default=REPO_ROOT / "environment",
                    help="mock environment baseline (default: %(default)s)")
    args = ap.parse_args()
    if args.timeout <= 0:
        ap.error("--timeout must be > 0")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    env_dir = args.env_dir.resolve()
    if not env_dir.is_dir():
        print(f"error: environment dir not found: {env_dir}", file=sys.stderr)
        sys.exit(2)
    catalog = read_api_ports(env_dir)
    if not catalog:
        print(f"error: no <api>/service.toml services under {env_dir}",
              file=sys.stderr)
        sys.exit(2)

    if args.tasks:
        task_dirs = []
        for t in args.tasks:
            candidate = INPUT_DIR / t
            task_dirs.append(candidate if candidate.is_dir() else Path(t))
    else:
        task_dirs = tracked_task_dirs()
    task_dirs = [p.resolve() for p in task_dirs]
    missing = [p for p in task_dirs if not p.is_dir()]
    if missing:
        for p in missing:
            print(f"error: task dir not found: {p}", file=sys.stderr)
        sys.exit(2)
    if not task_dirs:
        print("error: no tasks to check", file=sys.stderr)
        sys.exit(2)

    if not docker_available():
        print("error: docker daemon not reachable (is Docker running?)",
              file=sys.stderr)
        sys.exit(2)
    ok, err = ensure_image(env_dir, args.no_build)
    if not ok:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(2)

    total_fail = total_warn = 0
    for task_dir in task_dirs:
        report = check_task(task_dir, env_dir, catalog, args.timeout, args.keep)
        if report is None:
            print(f"\n{task_dir.name}\n  SKIP  no mock_data/ — nothing to validate")
            continue
        print(f"\n{report.task_name}")
        for level, api, message in report.rows:
            body = message if "\n" not in message else (
                message.replace("\n", "\n        "))
            print(f"  {level:4s}  {api:28s} {body}")
        total_fail += report.fail_count
        total_warn += report.warn_count

    print(f"\nfailures: {total_fail}  warnings: {total_warn}")
    sys.exit(1 if total_fail or (args.strict and total_warn) else 0)


if __name__ == "__main__":
    main()
