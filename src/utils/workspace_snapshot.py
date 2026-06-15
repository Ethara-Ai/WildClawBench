"""Workspace snapshots: persona + mock-data state, captured before turn 0 and
after the last turn finishes (before container teardown).

The harness writes two sidecar files per run:

    <run_dir>/workspace_snapshot_initial.json
    <run_dir>/workspace_snapshot_final.json

Each file has the shape:

    {
        "captured_at": "<iso8601>",
        "phase": "initial" | "final",
        "task_id": "<container name>",
        "persona": { "AGENTS.md": "<full text>", ... } | null,
        "mock_data": { "<api_or_bucket>": { "<table>": [<rows>] } } | null,
    }

Both sections may be `null` — `snapshot_persona` and `snapshot_mock_data` are
designed to degrade gracefully when docker exec or the admin plane is
unreachable, so a snapshot failure NEVER breaks a run. The writer always emits
a valid JSON file; consumers must treat `null` as "not captured".

Reads are stdlib only (subprocess + urllib) so this module imports cleanly in
the host venv without `requests`.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Persona files live flat at /root/*.md by convention (see
# src/utils/docker_utils.py:inject_persona_into_workspace). Anything else
# would be a per-task override, so we keep the search path tight.
_DEFAULT_PERSONA_SEARCH_PATHS: tuple[str, ...] = ("/root",)

_DOCKER_EXEC_TIMEOUT_SECONDS = 15.0
_HTTP_DEFAULT_TIMEOUT_SECONDS = 10.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _docker_exec(task_id: str, cmd: str) -> tuple[int, str, str]:
    """Run `docker exec <task_id> /bin/sh -c <cmd>`; return (rc, stdout, stderr).

    Returns rc=-1 + empty strings on any subprocess error so the caller can
    treat docker-unreachable identically to a non-zero exit.
    """
    try:
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/sh", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=_DOCKER_EXEC_TIMEOUT_SECONDS,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("workspace_snapshot: docker exec failed (%s): %s", task_id, exc)
        return -1, "", ""


def snapshot_persona(
    task_id: str,
    *,
    search_paths: Iterable[str] = _DEFAULT_PERSONA_SEARCH_PATHS,
) -> dict[str, str]:
    """Return `{filename: text}` for every `*.md` under each search path.

    Empty dict on any docker-exec failure (caller can distinguish "no persona"
    from "snapshot failed" via `None` vs `{}`, but for simplicity this layer
    returns `{}` and lets the writer decide).
    """
    persona: dict[str, str] = {}
    for path in search_paths:
        # Single-call payload: print one record per file with a sentinel
        # separator so we can parse N files in one docker round-trip. The
        # sentinel `<<<WS_FILE>>>` is highly unlikely to appear in any .md
        # body; we'd see it surface in the output if it did.
        cmd = (
            f'for f in {path}/*.md; do '
            f'[ -f "$f" ] || continue; '
            f'printf "<<<WS_FILE>>>%s<<<WS_FILE_END>>>\\n" "$f"; '
            f'cat "$f"; '
            f'printf "<<<WS_FILE_BODY_END>>>\\n"; '
            f'done'
        )
        rc, out, _err = _docker_exec(task_id, cmd)
        if rc != 0 or not out:
            continue
        for fname, body in _parse_persona_payload(out):
            persona[fname] = body
    return persona


def _parse_persona_payload(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"<<<WS_FILE>>>(.*?)<<<WS_FILE_END>>>\n"
        r"(.*?)\n?<<<WS_FILE_BODY_END>>>\n?",
        re.DOTALL,
    )
    results: list[tuple[str, str]] = []
    for match in pattern.finditer(text):
        full_path = match.group(1).strip()
        body = match.group(2)
        fname = full_path.rsplit("/", 1)[-1]
        if fname:
            results.append((fname, body))
    return results


def snapshot_persona_from_path(persona_dir: str | Path) -> dict[str, str]:
    persona: dict[str, str] = {}
    if not persona_dir:
        return persona
    p = Path(persona_dir)
    if not p.is_dir():
        return persona
    for md_path in sorted(p.glob("*.md")):
        try:
            persona[md_path.name] = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("workspace_snapshot: read %s failed: %s", md_path, exc)
    return persona


def _http_get_json(url: str, *, headers: dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - admin plane is internal
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def snapshot_mock_data(
    *,
    host_api_to_url: dict[str, str],
    admin_token: str | None = None,
    timeout: float = _HTTP_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Return `{api_name: {table_or_doc: rows_or_doc}}` by sweeping each API's
    `/admin/*` plane.

    Inputs match what `_start_task_mock_stack` already populates into
    `drift_info`:
        host_api_to_url -- `{api_name: 'http://127.0.0.1:<published_port>'}`
        admin_token     -- value to send as `X-Admin-Token` (or `None`)

    Per-API contract (environment/admin_plane.py):
        GET /admin/tables  -> {"tables": [...], "documents": [...]}
        GET /admin/data/{table}  -> {"rows": [...]} or bare list
        GET /admin/doc/{doc}     -> arbitrary JSON

    Individual API/table/doc failures are logged and skipped; the snapshot
    keeps everything that DID respond. Returns `{}` only when there are no
    APIs to sweep.
    """
    headers: dict[str, str] = {}
    if admin_token:
        headers["X-Admin-Token"] = admin_token

    out: dict[str, dict[str, Any]] = {}
    for api_name, base_url in sorted((host_api_to_url or {}).items()):
        api_state = _sweep_one_api(api_name, base_url, headers=headers, timeout=timeout)
        if api_state:
            out[api_name] = api_state
    return out


def _sweep_one_api(
    api_name: str,
    base_url: str,
    *,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    try:
        tables_resp = _http_get_json(f"{base}/admin/tables", headers=headers, timeout=timeout)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "workspace_snapshot: /admin/tables unreachable at %s (%s): %s",
            base, api_name, exc,
        )
        return {}

    if isinstance(tables_resp, dict):
        table_entries = list(tables_resp.get("tables") or [])
        # admin_plane.py also reports documents; include them under a separate
        # bucket so the snapshot is complete.
        docs = tables_resp.get("documents") or []
    elif isinstance(tables_resp, list):
        table_entries = tables_resp
        docs = []
    else:
        return {}

    api_state: dict[str, Any] = {}
    for entry in table_entries:
        name = _coerce_table_name(entry)
        if not name:
            continue
        try:
            data = _http_get_json(
                f"{base}/admin/data/{name}", headers=headers, timeout=timeout,
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("workspace_snapshot: GET /admin/data/%s on %s failed: %s",
                           name, api_name, exc)
            continue
        if isinstance(data, dict):
            rows = data.get("rows", data)
        else:
            rows = data
        api_state[name] = rows

    for doc_entry in docs:
        name = doc_entry if isinstance(doc_entry, str) else (
            doc_entry.get("name") if isinstance(doc_entry, dict) else None
        )
        if not name:
            continue
        try:
            data = _http_get_json(
                f"{base}/admin/doc/{name}", headers=headers, timeout=timeout,
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("workspace_snapshot: GET /admin/doc/%s on %s failed: %s",
                           name, api_name, exc)
            continue
        api_state[f"_doc:{name}"] = data

    return api_state


def _coerce_table_name(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("name") or entry.get("table")
    return None


def write_workspace_snapshot_json(
    run_dir: Path,
    *,
    phase: str,
    task_id: str,
    persona: dict[str, str] | None,
    mock_data: dict[str, dict[str, Any]] | None,
) -> Path:
    """Write `<run_dir>/workspace_snapshot_<phase>.json` and return its path.

    Empty-dict `persona` / `mock_data` are stored as `{}`; explicit `None`
    is stored as `null` so consumers can distinguish "section was attempted
    but empty" from "section was not captured at all".
    """
    if phase not in ("initial", "final"):
        raise ValueError(f"phase must be 'initial' or 'final', got {phase!r}")
    payload = {
        "captured_at": _now_iso(),
        "phase": phase,
        "task_id": task_id,
        "persona": persona,
        "mock_data": mock_data,
    }
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / f"workspace_snapshot_{phase}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def capture_workspace_snapshot(
    run_dir: Path,
    *,
    phase: str,
    task_id: str,
    host_api_to_url: dict[str, str] | None,
    admin_token: str | None,
    persona_dir: str | Path | None = None,
) -> Path | None:
    """Convenience entrypoint for the harness: capture + write in one call.

    Wraps both probes + the write in try/except so a snapshot failure never
    surfaces to the calling code. Returns the written path on success, `None`
    on any unexpected error (logged).

    Persona source by phase:
        phase='initial' -> read from `persona_dir` host path (agent container
                           does not exist yet at the run_batch call site).
        phase='final'   -> docker exec into the agent container `task_id`
                           (picks up any edits the agent made to /root/*.md).
    """
    try:
        if phase == "initial" and persona_dir:
            persona = snapshot_persona_from_path(persona_dir)
        else:
            persona = snapshot_persona(task_id)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("workspace_snapshot[%s]: persona probe raised: %s", phase, exc)
        persona = None
    mock_data: dict[str, dict[str, Any]] | None
    if host_api_to_url:
        try:
            mock_data = snapshot_mock_data(
                host_api_to_url=host_api_to_url, admin_token=admin_token,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("workspace_snapshot[%s]: mock_data probe raised: %s", phase, exc)
            mock_data = None
    else:
        mock_data = None
    try:
        return write_workspace_snapshot_json(
            run_dir,
            phase=phase,
            task_id=task_id,
            persona=persona,
            mock_data=mock_data,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("workspace_snapshot[%s]: write failed: %s", phase, exc)
        return None
