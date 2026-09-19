#!/usr/bin/env python3
"""Replicate per-task overlay CSV ingestion WITHOUT containers.

For each task under input/ (or one named task), copy environment/ to a temp
tree, lay the task's mock_data/<api>/*.csv files over it exactly as the
read-only bind mount would at runtime, then import each overlaid <api>_data.py
so its _store.eager_load() runs the same coercion the live mock performs.
A CoerceError (or any import failure) is reported per api; clean tasks pass.
Exit code is non-zero if any overlay would fail to load.

With --ingest, each task's inject/stageN/mutations.json is additionally
REPLAYED against that in-process store and read back through the service's own
getter (the D17 ingest check). The previous check compared a patch's `set` keys
against the raw stored-row keys, which models none of what actually sits
between the two: the loader coercer renames and retypes columns, airtable-style
stores nest them under `fields`, and inject_director._patch_row re-wraps a
nested patch before sending it. Every one of those legitimate transforms read
as a lost write. Replaying for real and asking the getter what it serves
removes the false positives while still catching a write that truly cannot
reach the agent. See src/utils/serving_shape.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = REPO_ROOT / "environment"
INPUT_DIR = REPO_ROOT / "input"

sys.path.insert(0, str(REPO_ROOT))
from src.utils.mock_overlay import OverlayError, overlaid_data_module  # noqa: E402
from src.utils.serving_shape import (  # noqa: E402
    envelope_keys, envelope_vocabulary, orphan_keys, partition_expected,
    project_in_process, row_bag, serving_vocabulary, value_visible,
)


def _tracked_task_dirs() -> list[Path]:
    """Git-tracked task dirs only (skips untracked scratch); dir-scan fallback if no git."""
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


def _overlaid_apis(task_dir: Path) -> list[Path]:
    mock_data = task_dir / "mock_data"
    if not mock_data.is_dir():
        return []
    return sorted(p for p in mock_data.iterdir() if p.is_dir())


def _check_api(api_name: str, overlay_dir: Path,
               on_module: Optional[Callable[[Any], None]] = None) -> tuple[bool, str]:
    """Import the overlaid data module. `on_module` runs against the live module
    before teardown, which is where the ingest replay gets its store copy: the
    module was imported into a throwaway tree, so mutating it cannot touch
    environment/ or any other task."""
    try:
        with overlaid_data_module(api_name, overlay_dir, ENV_DIR) as module:
            if on_module is not None:
                on_module(module)
    except OverlayError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - a loader may raise anything
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def ingest_ops(task_dir: Path) -> dict[str, list[dict]]:
    """Explicit-admin patch ops from inject/stageN/mutations.json, by service.

    Only the `admin` form ({table, pk, set}) names a store target outright; the
    bare-REST form is resolved at apply time against live state the dry run does
    not have, so it is out of scope here (preflight warns on it separately).
    """
    by_service: dict[str, list[dict]] = {}
    inject_root = task_dir / "inject"
    if not inject_root.is_dir():
        return by_service
    for stage_dir in sorted(inject_root.iterdir()):
        path = stage_dir / "mutations.json"
        if not stage_dir.is_dir() or not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        muts = doc.get("mutations") if isinstance(doc, dict) else doc
        buckets = (list(muts.get("silent") or []) + list(muts.get("loud") or [])
                   if isinstance(muts, dict) else list(muts or []))
        for op in buckets:
            if not isinstance(op, dict) or not isinstance(op.get("admin"), dict):
                continue
            service = op.get("service") or op.get("api")
            admin = op["admin"]
            if not service or (admin.get("op") or "patch").lower() != "patch":
                continue
            by_service.setdefault(str(service), []).append(
                {"id": op.get("id") or f"{stage_dir.name}:{len(by_service.get(str(service), []))}",
                 "stage": stage_dir.name, **admin})
    return by_service


def _find_row(table: Any, pk: Any) -> tuple[Any, Optional[dict]]:
    """Store rows key on the pk's ORIGINAL type; a mutations.json pk is JSON, so
    try it verbatim then as an int (mirrors admin_plane._pk_candidates)."""
    for candidate in (pk, str(pk), int(pk) if str(pk).lstrip("-").isdigit() else None):
        if candidate is None:
            continue
        row = table.get(candidate)
        if row is not None:
            return candidate, row
    return pk, None


def replay_patch(module: Any, spec: dict) -> tuple[bool, str]:
    """Apply one admin patch to the in-process store and read it back through
    the service getter. Returns (visible_in_serving_shape, detail)."""
    store = getattr(module, "_store", None)
    table_name = str(spec.get("table") or "")
    set_ = spec.get("set") if isinstance(spec.get("set"), dict) else {}
    if store is None:
        return False, "module exposes no _store"
    if not set_:
        return False, "patch carries no `set` block"
    try:
        table = store.table(table_name)
    except Exception as exc:
        return False, f"no such store table {table_name!r}: {exc}"

    pk, before = _find_row(table, spec.get("pk"))
    if before is None:
        return False, f"row {spec.get('pk')!r} not in table {table_name!r}"

    nested = isinstance(before.get("fields"), dict)
    columns, envelope = partition_expected(set_, nested)
    written = {**columns, **envelope}
    vocabulary = serving_vocabulary(table.rows(), exclude_pk=pk,
                                    pk_field=table.primary_key,
                                    extra=row_bag(before))
    orphans = orphan_keys(columns, vocabulary)
    if envelope:
        orphans = sorted(orphans + orphan_keys(envelope, envelope_vocabulary(
            table.rows(), exclude_pk=pk, pk_field=table.primary_key,
            extra=envelope_keys(before))))

    snapshot = store.snapshot("d17-ingest")
    try:
        serving_before, mechanism = project_in_process(module, table_name, pk)
        payload = ({**envelope, "fields": {**before["fields"], **columns}} if nested
                   else dict(columns))
        table.patch(pk, payload)
        serving_after, mechanism = project_in_process(module, table_name, pk)
    finally:
        store.restore(snapshot)

    if orphans:
        return False, (f"orphan key(s) {orphans} — absent from the live column "
                       f"vocabulary, no getter can read them")
    if serving_after is None:
        return False, f"row unreadable after patch via {mechanism}"
    # Visibility is judged by whether the PROJECTION moved, not by whether the
    # written scalar reappears verbatim. The coercer legitimately retypes what
    # it is handed (';'-joined CSV -> list, "49152" -> 49152.0), so a written
    # value is routinely absent from the serving shape while the write landed
    # perfectly — the D17 `loader coercion` false positive. A projection that
    # did NOT move is the real failure: the write never reached the agent.
    if serving_after != serving_before:
        return True, f"visible via {mechanism}"
    if all(value_visible(v, serving_after) for v in written.values()):
        return True, f"no-op (already served) via {mechanism}"
    return False, f"patch left the serving shape unchanged via {mechanism}"


def _check_ingest(api_name: str, overlay_dir: Path,
                  ops: list[dict]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def _replay(module: Any) -> None:
        for spec in ops:
            try:
                ok, info = replay_patch(module, copy.deepcopy(spec))
            except Exception as exc:
                ok, info = False, f"{type(exc).__name__}: {exc}"
            results.append((f"{api_name}:{spec.get('id')}", ok, info))

    loaded, info = _check_api(api_name, overlay_dir, on_module=_replay)
    if not loaded:
        return [(f"{api_name}:<load>", False, info)]
    return results


def _check_task(task_dir: Path, ingest: bool = False) -> list[tuple[str, bool, str]]:
    out = []
    ops_by_service = ingest_ops(task_dir) if ingest else {}
    for overlay_dir in _overlaid_apis(task_dir):
        ok, info = _check_api(overlay_dir.name, overlay_dir)
        out.append((overlay_dir.name, ok, info))
        ops = ops_by_service.get(overlay_dir.name) or []
        if ok and ops:
            out.extend(_check_ingest(overlay_dir.name, overlay_dir, ops))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", help="task name under input/ (default: all)")
    ap.add_argument("--ingest", action="store_true",
                    help="also replay inject/stageN admin patches against the "
                         "loaded store and read back through the service getter")
    args = ap.parse_args()

    if args.task:
        tasks = [INPUT_DIR / args.task]
    else:
        tasks = _tracked_task_dirs()

    total_fail = 0
    for task_dir in tasks:
        if not task_dir.is_dir():
            print(f"SKIP {task_dir.name}: not a directory")
            continue
        rows = _check_task(task_dir, ingest=args.ingest)
        if not rows:
            continue
        print(f"\n{task_dir.name}")
        for api_name, ok, info in rows:
            print(f"  {'OK  ' if ok else 'FAIL'} {api_name:24s} {info}")
            if not ok:
                total_fail += 1

    print(f"\nfailures: {total_fail}")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
