from __future__ import annotations

import csv
import json
import logging
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


def _op_kind(op: Dict[str, Any]) -> str:
    admin = op.get("admin")
    if isinstance(admin, dict):
        return str(admin.get("op") or "patch").lower()
    return "rest"


def _admin_target_key(op: Dict[str, Any]) -> Optional[str]:
    admin = op.get("admin")
    if isinstance(admin, dict) and admin.get("pk") is not None:
        return str(admin["pk"])
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
                            rid = row.get("id") or row.get("pk")
                            if rid is not None:
                                ids.add(str(rid))
            elif path.suffix == ".csv":
                with path.open(newline="") as fh:
                    for row in csv.DictReader(fh):
                        rid = row.get("id") or row.get("pk")
                        if rid is not None:
                            ids.add(str(rid))
        except (OSError, ValueError):
            continue
    return ids


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

    for stage_pos, stage in enumerate(ordered):
        # stage_pos 0 == the first non-seed stage (validated against seed only).
        is_first_boundary = stage_pos == 0
        pending_creates: Dict[str, Set[str]] = {}

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
