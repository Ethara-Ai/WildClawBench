"""Recover the turn schedule — the instants prompts.json pins each turn to.

Turn TEXT never comes from here. The published trajectory's first user message
carries the workspace-inputs footer ``task_parser._append_workspace_hint``
appends at load time, so reusing it would apply that footer twice; the text is
always read back off the prompt file. This module recovers only the WHEN.

Instants, most authoritative first:

1. **The trajectory.** ``trajectories/<model>/run_N/output.json`` timestamps
   every user message with the instant the harness actually woke the agent.
   That is the run the bundle documents, so it is the schedule the
   reconstruction should replay.
2. **The turn labels.** ``(Day 3, 08:05)`` plus the window's first date. This
   is a reconstruction, not a record, and it is measurably lossy: willie's day
   labels put T15..T19 one calendar day earlier than the run did, because the
   narrative skipped a day the labels kept counting through. Schedules built
   this way are stamped ``fidelity: degraded``.
3. **Nothing.** No prompts.json is written at all. A schedule invented from
   turn ordering alone would be a claim the bundle does not support, and
   sim_clock would serve it to the agent as fact.

Instants are truncated to the minute. Trajectory stamps carry the harness's
dispatch latency (willie T0 fired at ``08:12:04.777`` against an authored
``08:12``); the minute is the authored instant, and it is what every shipped
prompts.json writes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.utils.task_standard import TaskWindow

from script.lib.recon.prompts import Turn

TRAJECTORIES = "trajectories"
RUN_RE = re.compile(r"^run_(\d+)$")


@dataclass
class Instants:
    """A resolved turn schedule and the evidence it rests on."""

    values: list = field(default_factory=list)
    source: str = ""
    fidelity: str = "exact"
    #: Largest sub-minute offset dropped when truncating trajectory stamps.
    jitter_ms: int = 0
    notes: list = field(default_factory=list)
    system_prompt: str = ""


def find_runs(bundle: Path) -> list:
    """Every ``<model>/run_N/output.json`` the bundle publishes, in run order."""
    root = bundle / TRAJECTORIES
    if not root.is_dir():
        return []
    runs = []
    for model in sorted(root.iterdir()):
        if not model.is_dir():
            continue
        for run in model.iterdir():
            m = RUN_RE.match(run.name)
            if m and (run / "output.json").is_file():
                runs.append((int(m.group(1)), f"{model.name}/{run.name}",
                             run / "output.json"))
    return [(label, path) for _, label, path in sorted(runs)]


def user_instants(output_json: Path) -> list:
    """The UTC instant of every user message in a published trajectory."""
    try:
        data = json.loads(output_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for entry in data.get("messages") or []:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        stamp = _parse_iso(entry.get("timestamp"))
        if stamp is not None:
            out.append(stamp)
    return out


def system_prompt(output_json: Path) -> str:
    """The system prompt the documented run was given, if it recorded one."""
    try:
        data = json.loads(output_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    meta = data.get("meta_info")
    return str(meta.get("system_prompt") or "") if isinstance(meta, dict) else ""


def _parse_iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _zone(name: str):
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _truncate(stamp: datetime) -> tuple:
    minute = stamp.replace(second=0, microsecond=0)
    return minute, int((stamp - minute).total_seconds() * 1000)


def from_trajectory(bundle: Path, turn_count: int, tz_name: str,
                    run: str = "") -> Instants:
    """Read instants off the run that documents all ``turn_count`` turns.

    A named run is used as named, and its shortfall reported rather than
    papered over; otherwise the first run that woke the agent the full number
    of times wins, because a truncated run documents a truncated schedule.
    """
    runs = find_runs(bundle)
    if not runs:
        return Instants(notes=["no trajectories/ in the bundle"])
    if run:
        runs = [(label, path) for label, path in runs
                if label == run or label.endswith(f"/{run}")]
        if not runs:
            return Instants(notes=[f"no trajectory run matching {run!r}"])

    best = Instants()
    for label, path in runs:
        stamps = user_instants(path)
        if len(stamps) < len(best.values):
            continue
        best = _build(stamps, label, tz_name)
        best.system_prompt = system_prompt(path)
        if len(stamps) == turn_count:
            return best
    if best.values:
        best.notes.append(
            f"{best.source} woke the agent {len(best.values)} time(s) for "
            f"{turn_count} turn(s); no published run covers them all")
    return best


def _build(stamps: list, label: str, tz_name: str) -> Instants:
    zone = _zone(tz_name) or dt_timezone.utc
    values, jitter = [], 0
    for stamp in stamps:
        minute, drift = _truncate(stamp.astimezone(zone))
        values.append(minute)
        jitter = max(jitter, drift)
    notes = []
    if not _zone(tz_name):
        notes.append(f"timezone {tz_name!r} unknown; instants written in UTC")
    return Instants(values, f"trajectory:{label}", "exact", jitter, notes)


def from_labels(turns: list, window: TaskWindow, tz_name: str) -> Instants:
    """Rebuild instants from ``(Day N, HH:MM)`` labels against the window start.

    Day numbers count narrative days, which are not always calendar days — the
    schedule this returns is a best reading, never a record.
    """
    zone = _zone(tz_name)
    if zone is None:
        return Instants(notes=[
            f"timezone {tz_name!r} is unknown, so label instants cannot be "
            f"written offset-aware and sim_clock would reject them"])
    values = []
    for turn in turns:
        if turn.day is None or not turn.time:
            return Instants(notes=[f"turn T{turn.index} carries no (Day N, HH:MM) label"])
        hour, minute = (int(part) for part in turn.time.split(":", 1))
        day = window.start + timedelta(days=turn.day - 1)
        values.append(datetime(day.year, day.month, day.day, hour, minute,
                               tzinfo=zone))
    return Instants(values, "labels+window-start", "degraded", 0, [
        "instants reconstructed from day labels, not read off a run: a "
        "narrative day the labels count through but the calendar skips shifts "
        "every later turn"])


def resolve(bundle: Path, turns: list, window, tz_name: str,
            run: str = "") -> Instants:
    """Best available schedule for ``turns``, or an empty one to refuse on."""
    exact = from_trajectory(bundle, len(turns), tz_name, run)
    if len(exact.values) == len(turns):
        return exact
    degraded = Instants(notes=list(exact.notes), system_prompt=exact.system_prompt)
    if window is not None:
        labelled = from_labels(turns, window, tz_name)
        labelled.notes = degraded.notes + labelled.notes
        labelled.system_prompt = exact.system_prompt
        degraded = labelled
    else:
        degraded.notes.append("no window resolved, so labels cannot be dated")
    if len(degraded.values) != len(turns):
        degraded.values = []
    return degraded


def build(task_id: str, persona: str, tz_name: str, turns: list,
          instants: Instants) -> dict:
    """Render the prompts.json ``parse_prompts_json`` and sim_clock consume."""
    payload = {
        "task_id": task_id,
        "persona": persona,
        "timezone": tz_name,
        "turn_count": len(turns),
        "source": (f"reconstructed from bundle; instants from "
                   f"{instants.source} ({instants.fidelity})"),
        "turns": [
            {"turn": f"T{turn.index}",
             "timestamp": stamp.isoformat(),
             "message": turn.text}
            for turn, stamp in zip(turns, instants.values)
        ],
    }
    if instants.fidelity != "exact":
        payload["fidelity"] = instants.fidelity
    return payload
