"""Compute a task's simulated persona start-time for the agent clock shim.

The agent otherwise runs on the real host clock, so OpenClaw's per-turn wake-up
stamp ("[Www YYYY-MM-DD HH:MM UTC] ...") shows the real run date instead of the
persona window the prompt asserts (e.g. prompt says Nov 2026, host clock says
Jul 2026). ``compute_sim_clock`` derives the first-turn simulated instant so the
harness can shift the agent's Date reads to it (see docker/agent_faketime_shim.js
and src/utils/docker_utils.py).

Source of truth is the task's ``prompts.json`` first turn:
  * schema A (ISO):   turns[0]["timestamp"] = "2026-11-03T07:38:00-06:00"
  * schema B (split): turns[0]["day"], turns[0]["time"] + a window start date

Returns None (feature no-ops, agent stays on real time) when no simulated time
can be determined — callers should log that skew loudly rather than hide it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

__all__ = ["compute_sim_clock", "compute_sim_clock_for_turn", "SimClock"]


class SimClock:
    """Resolved simulated start: epoch milliseconds + IANA timezone name."""

    __slots__ = ("epoch_ms", "tz", "iso")

    def __init__(self, epoch_ms: int, tz: str, iso: str) -> None:
        self.epoch_ms = epoch_ms
        self.tz = tz
        self.iso = iso

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SimClock(epoch_ms={self.epoch_ms}, tz={self.tz!r}, iso={self.iso!r})"


def _window_start_date(window: object) -> str | None:
    """Extract a YYYY-MM-DD start date from a window field (str or dict)."""
    if isinstance(window, dict):
        start = window.get("start")
        return str(start) if start else None
    if isinstance(window, str) and window.strip():
        # e.g. "2026-11-03 to 2026-11-06" -> "2026-11-03"
        return window.strip().split()[0]
    return None


def _from_prompts_json(data: dict, turn_index: int = 0) -> SimClock | None:
    """Resolve the simulated instant for ``turns[turn_index]``.

    ``turn_index`` defaults to 0 (the run's initial anchor). Multi-turn tasks
    declare a distinct timestamp per turn; the harness re-anchors the agent
    clock at each turn boundary so a task narrating three days does not collapse
    into three consecutive real minutes.
    """
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    if not 0 <= turn_index < len(turns):
        return None
    t0 = turns[turn_index]
    if not isinstance(t0, dict):
        return None

    # Timezone lives at top level (schema A) or inside the window dict (schema B).
    window = data.get("window")
    tz_name = (data.get("timezone") or "").strip()
    if not tz_name and isinstance(window, dict):
        tz_name = str(window.get("timezone") or "").strip()

    # Schema A: explicit ISO timestamp (may carry its own UTC offset).
    ts = t0.get("timestamp")
    if isinstance(ts, str) and ts.strip():
        try:
            dt = datetime.fromisoformat(ts.strip())
        except ValueError:
            dt = None
        if dt is not None and dt.tzinfo is not None:
            epoch_ms = int(dt.timestamp() * 1000)
            return SimClock(epoch_ms, tz_name or _offset_tz_label(dt), dt.isoformat())

    # Schema B: day index + local time, anchored on the window start date.
    day = t0.get("day")
    time_str = t0.get("time")
    start_date = _window_start_date(data.get("window"))
    if isinstance(day, int) and isinstance(time_str, str) and start_date and tz_name:
        try:
            base = datetime.strptime(start_date, "%Y-%m-%d").date()
            hh, mm = (int(x) for x in time_str.split(":")[:2])
            tz = ZoneInfo(tz_name)
        except (ValueError, ZoneInfoNotFoundError):
            return None
        local = datetime(base.year, base.month, base.day, hh, mm, tzinfo=tz) + timedelta(
            days=max(0, day - 1)
        )
        epoch_ms = int(local.timestamp() * 1000)
        return SimClock(epoch_ms, tz_name, local.isoformat())

    return None


def _offset_tz_label(dt: datetime) -> str:
    """Fallback tz label from a fixed-offset datetime (e.g. 'UTC-06:00')."""
    off = dt.utcoffset()
    if off is None:
        return "UTC"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def compute_sim_clock(task: dict) -> SimClock | None:
    """Resolve the simulated START instant for a task, or None if undetermined."""
    return compute_sim_clock_for_turn(task, 0)


def compute_sim_clock_for_turn(task: dict, turn_index: int) -> SimClock | None:
    """Resolve the simulated instant for turn ``turn_index``.

    Returns None when the turn carries no resolvable timestamp (callers keep the
    previous anchor rather than guessing). Turn 0 is the run's initial anchor,
    applied at container creation; later turns are applied at turn boundaries.
    """
    task_dir = task.get("task_dir")
    if not task_dir:
        return None
    pj = Path(task_dir) / "prompts.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[sim_clock] could not read %s: %s", pj, exc)
        return None
    return _from_prompts_json(data, turn_index)
