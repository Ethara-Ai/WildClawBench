from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.utils.inject_director import InjectApplier, InjectScript, InjectStage

LOG = logging.getLogger("wildclaw.inject")


class InjectAuthoringError(Exception):
    """Raised when a mutations.json op cannot possibly land at runtime.

    Blocking (fail-closed) authoring defects: an unresolvable service slug or an
    op that extracts zero fields. These never depend on live/agent state, so a
    static verdict is authoritative and the run should not proceed as if the
    injection scenario were valid.
    """

    def __init__(self, defects: List[Dict[str, Any]]):
        self.defects = defects
        summary = "; ".join(
            f"{d.get('stage')}/{d.get('id')}: {d.get('reason')}" for d in defects
        )
        super().__init__(f"inject authoring validation failed: {summary}")


def _op_service(op: Dict[str, Any]) -> Optional[str]:
    return op.get("service") or op.get("api")


def _resolve_slug(service: Optional[str], urls: Dict[str, Any]) -> Optional[str]:
    if not service:
        return None
    if service in urls:
        return service
    return None


_ADMIN_DATA_POST_RE = re.compile(r"^/admin/data/([^/]+)/?$")
_ADMIN_DATA_PATCH_RE = re.compile(r"^/admin/data/([^/]+)/([^/]+)/?$")


def _op_kind(op: Dict[str, Any]) -> str:
    admin = op.get("admin")
    if isinstance(admin, dict):
        return str(admin.get("op") or "patch").lower()
    # Admin-plane-addressed REST ops are replayed verbatim by the applier
    # (_replay_admin_rest); classify them by shape so create ops are not
    # rejected for carrying no mutation fields.
    path = str(op.get("path") or "")
    if path.startswith("/admin/"):
        method = str(op.get("method") or "POST").upper()
        body = op.get("body") if isinstance(op.get("body"), dict) else {}
        if (method == "POST" and _ADMIN_DATA_POST_RE.match(path)
                and isinstance(body.get("row"), dict)):
            return "upsert"
        if method == "PATCH" and _ADMIN_DATA_PATCH_RE.match(path):
            return "patch"
        return "admin_raw"
    return "rest"


def _admin_target_key(op: Dict[str, Any]) -> Optional[str]:
    admin = op.get("admin")
    if isinstance(admin, dict) and admin.get("pk") is not None:
        return str(admin["pk"])
    if not isinstance(admin, dict):
        m = _ADMIN_DATA_PATCH_RE.match(str(op.get("path") or ""))
        if m and str(op.get("method") or "").upper() == "PATCH":
            return m.group(2)
    return InjectApplier._extract_key_from_op(op)


def _created_ids(op: Dict[str, Any]) -> Set[str]:
    """Row/doc identifiers an op is expected to CREATE (fold into snapshot)."""
    out: Set[str] = set()
    admin = op.get("admin")
    if isinstance(admin, dict):
        kind = str(admin.get("op") or "patch").lower()
        if kind == "upsert":
            row = admin.get("row")
            if isinstance(row, dict):
                rid = row.get("id") or row.get("pk") or admin.get("pk")
                if rid is not None:
                    out.add(str(rid))
        elif kind in ("doc_set", "doc.merge", "doc_merge"):
            path = admin.get("path")
            if path is not None:
                out.add(str(path))
        return out
    # Bare admin-plane REST creates (replayed verbatim by the applier).
    body = op.get("body") if isinstance(op.get("body"), dict) else {}
    path = str(op.get("path") or "")
    method = str(op.get("method") or "POST").upper()
    if method == "POST" and path.startswith("/admin/"):
        if _ADMIN_DATA_POST_RE.match(path) and isinstance(body.get("row"), dict):
            out.update(_row_ids(body["row"]))
        for raw in (body.get("operations") or []):
            if isinstance(raw, dict) and raw.get("op") == "data.upsert" \
                    and isinstance(raw.get("row"), dict):
                out.update(_row_ids(raw["row"]))
    return out


def _row_ids(row: Dict[str, Any]) -> Set[str]:
    """All identifier-ish values on a row, case-insensitively.

    Stores key rows by heterogeneous pk columns (``Id`` for quickbooks,
    ``item_id`` for monday, ``sys_id`` for servicenow, ``objectID`` for
    algolia, ...). A static validator has no /admin/tables to consult, so it
    collects every scalar under a key that lowercases to ``pk`` or ends in
    ``id`` — over-collecting only relaxes the missing-target check, never
    breaks a valid op."""
    out: Set[str] = set()
    for k, v in row.items():
        if not isinstance(v, (str, int)):
            continue
        kl = str(k).lower()
        if kl == "pk" or kl.endswith("id"):
            out.add(str(v))
    return out


def _synthesize_composite_pk(row: Dict[str, Any], ids: Set[str]) -> None:
    """Synthesize composite primary keys into the snapshot set.

    Runtime loaders build _pk from multiple id fields (e.g. f"{item_id}@{column_id}").
    This mirrors that synthesis for the static validator's membership check only."""
    id_vals = [str(v) for k, v in row.items()
               if isinstance(v, (str, int)) and (str(k).lower() == "pk" or str(k).lower().endswith("id"))]
    if len(id_vals) >= 2:
        ids.add("@".join(id_vals))


def _seed_ids_for_service(mock_data_root: Path, service: str) -> Set[str]:
    """Collect row identifiers seeded on disk for a service's mock_data overlay.

    Reads the same per-task overlay files the runtime mounts, so the validator's
    seed snapshot matches what the live store will hold before turn 1.
    """
    ids: Set[str] = set()
    svc_dir = mock_data_root / service
    if not svc_dir.is_dir():
        svc_dir = mock_data_root / f"{service}-api"
        if not svc_dir.is_dir():
            return ids
    for path in svc_dir.iterdir():
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text())
                rows = data if isinstance(data, list) else data.get("rows", data)
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            ids.update(_row_ids(row))
                            _synthesize_composite_pk(row, ids)
            elif path.suffix == ".csv":
                with path.open(newline="") as fh:
                    for row in csv.DictReader(fh):
                        ids.update(_row_ids(row))
                        _synthesize_composite_pk(row, ids)
        except (OSError, ValueError):
            continue
    return ids


_FS_ALLOWED_ACTIONS = ("copy", "mkdir")


def _resolve_fs_src(stage: InjectStage, src: str) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    """Resolve a filesystem op's ``src`` the way the runtime hook does.

    Mirrors ``InjectApplier._apply_filesystem``'s 3-step chain exactly:
      (1) stage_dir/src, (2) basename rglob within stage_dir (skip
      ``_placeholders``), (3) task-root fallback stage_dir.parent.parent/src.
    Returns ``(resolved_path_or_None, footgun_warnings)``. Only usable when the
    stage carries a real on-disk ``source`` (unit stages use source="").
    """
    warnings: List[Dict[str, Any]] = []
    if not stage.source:
        return None, warnings
    stage_dir = Path(stage.source).parent
    if not stage_dir.is_dir():
        return None, warnings
    host_src = stage_dir / src
    if host_src.exists():
        return host_src.resolve(), warnings
    hits = [p for p in stage_dir.rglob(Path(src).name)
            if p.is_file() and "_placeholders" not in p.parts]
    if len(hits) > 1:
        warnings.append({"status": "fs-src-ambiguous",
                         "reason": f"{len(hits)} files match basename {Path(src).name!r} "
                                   "under the stage dir (rglob order is unstable; use an "
                                   "explicit relative path)"})
    if hits:
        return hits[0].resolve(), warnings
    task_root_src = (stage_dir.parent.parent / src)
    if task_root_src.exists():
        resolved = task_root_src.resolve()
        if stage_dir.resolve() not in resolved.parents:
            warnings.append({"status": "fs-src-outside-stage",
                             "reason": f"src {src!r} resolved outside the stage dir "
                                       f"({resolved}); prefer a file under inject/stage<N>/"})
        return resolved, warnings
    return None, warnings


def _validate_filesystem_op(
    stage: InjectStage, op: Dict[str, Any], mock_data_root: Optional[Path],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Statically validate one ``mutations.filesystem`` op. Returns (fatal, warnings)."""
    fatal: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    oid = op.get("id")
    action = op.get("action")
    dst = op.get("dst")

    if action not in _FS_ALLOWED_ACTIONS:
        fatal.append({
            "stage": stage.name, "id": oid, "status": "fs-invalid-action",
            "reason": f"unsupported filesystem action {action!r} (allowed: copy, "
                      "mkdir; 'patch'/in-place edit is not supported — author a full "
                      "replacement file under inject/stage<N>/ and use action:copy)",
        })
        return fatal, warnings

    if action == "mkdir":
        if not dst:
            fatal.append({"stage": stage.name, "id": oid, "status": "fs-missing-dst",
                          "reason": "mkdir op has no dst"})
        if op.get("src"):
            warnings.append({"stage": stage.name, "id": oid, "status": "fs-mkdir-with-src",
                             "reason": "mkdir op also sets src (ignored by the copy hook)"})
        return fatal, warnings

    src = op.get("src")
    if not src:
        fatal.append({
            "stage": stage.name, "id": oid, "status": "fs-missing-src",
            "reason": "filesystem copy op has no src (author a real replacement file "
                      "under inject/stage<N>/ and reference it)",
        })
    if not dst:
        fatal.append({"stage": stage.name, "id": oid, "status": "fs-missing-dst",
                      "reason": "filesystem copy op has no dst"})
    if src and dst and stage.source:
        resolved, fg = _resolve_fs_src(stage, src)
        warnings.extend({**w, "stage": stage.name, "id": oid} for w in fg)
        if resolved is None:
            fatal.append({
                "stage": stage.name, "id": oid, "status": "fs-src-not-found",
                "reason": f"src {src!r} not found under {Path(stage.source).parent}",
            })
    return fatal, warnings


def validate_inject_script(
    script: InjectScript,
    host_api_to_url: Dict[str, Any],
    mock_data_root: Optional[Path],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Statically validate every op against a cumulative seed+inject snapshot.

    Returns (fatal, warnings). Fatal defects block the run; warnings are logged.
    The snapshot starts from on-disk seed row ids and grows as earlier stages'
    upsert/doc_set targets are folded in (prior stages only, never same-stage),
    mirroring the runtime firing order (silent before loud within a stage).
    """
    urls = host_api_to_url or {}
    fatal: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    snapshot: Dict[str, Set[str]] = {}

    def _snap(service: str) -> Set[str]:
        if service not in snapshot:
            base: Set[str] = set()
            if mock_data_root is not None:
                base |= _seed_ids_for_service(mock_data_root, service)
            snapshot[service] = base
        return snapshot[service]

    non_seed = [s for s in script.stages if not s.is_seed]
    ordered = sorted(non_seed, key=lambda s: (s.from_turn if s.from_turn is not None else 0))

    seed_stage = script.seed_stage()
    if seed_stage is not None:
        for op in list(seed_stage.silent) + list(seed_stage.loud):
            svc = _resolve_slug(_op_service(op), urls) or (_op_service(op) or "")
            _snap(svc).update(_created_ids(op))
        for op in seed_stage.filesystem:
            f, w = _validate_filesystem_op(seed_stage, op, mock_data_root)
            fatal.extend(f)
            warnings.extend(w)

    for stage_pos, stage in enumerate(ordered):
        # stage_pos 0 == the first non-seed stage (validated against seed only).
        is_first_boundary = stage_pos == 0
        pending_creates: Dict[str, Set[str]] = {}

        for op in stage.filesystem:
            f, w = _validate_filesystem_op(stage, op, mock_data_root)
            fatal.extend(f)
            warnings.extend(w)

        for op in list(stage.silent) + list(stage.loud):
            oid = op.get("id")
            raw_service = _op_service(op)
            resolved = _resolve_slug(raw_service, urls)

            if resolved is None:
                fatal.append({
                    "stage": stage.name, "id": oid, "status": "unresolved",
                    "reason": f"no admin URL for {raw_service!r} (use the canonical '<name>-api' slug)",
                })
                continue

            kind = _op_kind(op)

            # Pure deletes/creates carry no fields; only mutating kinds must extract some.
            needs_fields = kind in ("rest", "patch", "update_where", "bulk", "doc_merge", "doc.merge")
            if needs_fields:
                fields = InjectApplier._extract_fields(op)
                admin = op.get("admin")
                admin_set = admin.get("set") if isinstance(admin, dict) else None
                if not fields and not (isinstance(admin_set, dict) and admin_set):
                    fatal.append({
                        "stage": stage.name, "id": oid, "status": "empty",
                        "reason": "op extracts zero fields (nothing to mutate)",
                    })
                    continue

            # Fold this op's own creations into the pending set (visible to LATER
            # stages, not to same-stage patches).
            creates = _created_ids(op)
            if creates:
                pending_creates.setdefault(resolved, set()).update(creates)

            # Target-existence check for row-addressing ops.
            targets_row = kind in ("rest", "patch")
            if targets_row:
                key = _admin_target_key(op)
                if key is not None:
                    known = _snap(resolved)
                    if key not in known:
                        entry = {
                            "stage": stage.name, "id": oid, "status": "missing-target",
                            "reason": f"target row {key!r} not found in seed/prior-inject snapshot for {resolved}",
                        }
                        if is_first_boundary:
                            fatal.append(entry)
                        else:
                            warnings.append(entry)

        # After the whole stage, prior-stage creations become visible downstream.
        for svc, ids in pending_creates.items():
            _snap(svc).update(ids)

    return fatal, warnings


def run_authoring_validation(
    script: InjectScript,
    host_api_to_url: Dict[str, Any],
    mock_data_root: Optional[Path],
    task_id: str = "",
) -> List[Dict[str, Any]]:
    """Validate and raise on fatal defects; return non-blocking warnings.

    Wired into run_batch before seeding so authoring bugs surface loudly and
    early instead of as silent mid-run no-ops at turns 3/5/7.
    """
    fatal, warnings = validate_inject_script(script, host_api_to_url, mock_data_root)
    for w in warnings:
        LOG.warning("[authoring] inject validation warning (%s): %s/%s — %s",
                    task_id, w.get("stage"), w.get("id"), w.get("reason"))
    if fatal:
        for f in fatal:
            LOG.error("[authoring] inject validation FAILED (%s): %s/%s — %s",
                      task_id, f.get("stage"), f.get("id"), f.get("reason"))
        raise InjectAuthoringError(fatal)
    return warnings
