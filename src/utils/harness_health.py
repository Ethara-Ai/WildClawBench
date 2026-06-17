"""Post-run harness instrumentation summary.

Reads ``inject_timeline.jsonl`` and emits a small JSON describing how many
injection mutations actually succeeded vs. failed silently. Dropped beside
``reward.txt`` in the verifier-logs directory so a glance at
``harness_health.json`` answers "did the harness setup actually fire?" without
grepping a 700-line timeline.

LEE_002 motivation: SM-01/SM-02/SM-03/CM-01 all failed (envelope mismatch +
missing admin URL for filesystem), but the only trace was the JSONL. With this
summary, ``inject.unresolved > 0`` or ``inject.unsupported > 0`` surfaces the
breakage at run-completion time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _zero_summary() -> Dict[str, Any]:
    return {
        "schema": 1,
        "inject": {
            "total": 0,
            "ok": 0,
            "unresolved": 0,
            "skipped": 0,
            "unsupported": 0,
            "other_failed": 0,
        },
        "fs": {"total": 0, "ok": 0, "failed": 0},
        "api": {"total": 0, "ok": 0, "failed": 0},
        "failed_ids": [],
    }


def summarize_inject_timeline(path: Path) -> Dict[str, Any]:
    """Read ``inject_timeline.jsonl`` and return a harness_health dict.

    Records counted: ``type in {"inject.fs", "inject.api"}``. Lifecycle records
    (``inject.seed.*``, ``inject.stage.applied``) are ignored. Malformed lines
    are skipped silently — this is a triage tool, not a verifier.
    """
    summary = _zero_summary()
    if not path.is_file():
        return summary
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return summary

    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        if rtype not in {"inject.fs", "inject.api"}:
            continue

        bucket = "fs" if rtype == "inject.fs" else "api"
        summary[bucket]["total"] += 1
        summary["inject"]["total"] += 1

        if bool(rec.get("ok")):
            summary[bucket]["ok"] += 1
            summary["inject"]["ok"] += 1
            continue

        summary[bucket]["failed"] += 1
        status = rec.get("status") or "other_failed"
        bucket_key = status if status in {"unresolved", "skipped", "unsupported"} else "other_failed"
        summary["inject"][bucket_key] += 1
        summary["failed_ids"].append({
            "id": rec.get("id"),
            "type": rtype,
            "status": status,
            "reason": rec.get("reason"),
        })

    return summary
