"""Container / task lifecycle rendering for the harness.

Backends and the orchestrator call :func:`emit_stage` at each transition of a
task's Docker container so the user gets a clear, colored, chronological view of
what the harness is doing: creation → startup → execution → status → done/fail.

Every call does two things:
  1. Publishes a structured event on the shared :mod:`events` bus so the Textual
     dashboard (when active) can update its live status table.
  2. When the Textual dashboard is NOT driving output, prints a styled one-line
     status to the shared Rich console.

Emitting is always safe: if rich is unavailable it falls back to a plain print,
and it never raises into the caller.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .console import get_console
from .events import EV_INJECT, EV_STAGE, get_bus

# Ordered lifecycle stages with a glyph + Rich style each.
STAGE_CREATE = "CREATE"
STAGE_START = "START"
STAGE_EXEC = "EXEC"
STAGE_STATUS = "STATUS"
STAGE_DONE = "DONE"
STAGE_FAIL = "FAIL"

_STAGE_META = {
    STAGE_CREATE: ("◐", "cyan", "creating container"),
    STAGE_START: ("◑", "cyan", "starting container"),
    STAGE_EXEC: ("▶", "bold cyan", "executing task"),
    STAGE_STATUS: ("•", "blue", "status"),
    STAGE_DONE: ("✓", "bold green", "completed"),
    STAGE_FAIL: ("✗", "bold red", "failed"),
}

# When True, emit_stage suppresses direct console prints (the Textual dashboard
# owns the screen and renders stages itself from the event bus). Set by the TUI.
_dashboard_active = False


def set_dashboard_active(active: bool) -> None:
    global _dashboard_active
    _dashboard_active = bool(active)


def is_dashboard_active() -> bool:
    """True while the Textual dashboard owns the terminal (see tui.py).

    Other renderers (e.g. the execution summary) consult this to avoid printing
    to the shared console, which would corrupt the full-screen canvas.
    """
    return _dashboard_active


def emit_stage(
    task_id: str,
    stage: str,
    detail: str = "",
    *,
    status: Optional[str] = None,
) -> None:
    """Record and render a lifecycle stage transition for ``task_id``.

    ``stage`` is one of the ``STAGE_*`` constants. ``detail`` is a short
    human-readable suffix (e.g. a container id, an elapsed time, an error).
    ``status`` optionally overrides the default status word for the stage.
    """
    glyph, style, default_label = _STAGE_META.get(stage, ("·", "white", stage))
    label = status or default_label

    # 1) Always publish for the dashboard / any subscriber.
    try:
        get_bus().emit(
            EV_STAGE,
            task_id=task_id,
            stage=stage,
            label=label,
            detail=detail,
            glyph=glyph,
            style=style,
        )
    except Exception:
        pass

    # 2) Console echo (skipped when the full-screen dashboard owns the terminal).
    if _dashboard_active:
        return
    console = get_console()
    text = f"{glyph} [{task_id}] {label}"
    if detail:
        text += f" — {detail}"
    if console is None:
        print(text)
        return
    # markup=False so a bracketed task id (e.g. "[alden-croft_MB]") is printed
    # verbatim instead of being parsed as a Rich style tag.
    console.print(text, style=style, markup=False)


# Bound the size of injected values shipped over the bus + rendered in the pane:
# a single mutation can carry a whole email body, so cap value length and count.
_INJECT_VALUE_MAXLEN = 200
_INJECT_VALUE_MAXCOUNT = 16


def _truncate_inject_values(
    values: Optional[Mapping[str, Any]]
) -> Optional[Dict[str, str]]:
    """Return a display-safe copy of ``values``: each value stringified and
    truncated, at most ``_INJECT_VALUE_MAXCOUNT`` entries. ``None``/empty in →
    ``None`` out. Never raises (returns None on any surprise)."""
    if not values:
        return None
    try:
        out: Dict[str, str] = {}
        for i, (k, v) in enumerate(values.items()):
            if i >= _INJECT_VALUE_MAXCOUNT:
                out["…"] = f"(+{len(values) - _INJECT_VALUE_MAXCOUNT} more)"
                break
            s = str(v)
            if len(s) > _INJECT_VALUE_MAXLEN:
                s = s[:_INJECT_VALUE_MAXLEN] + "…"
            out[str(k)] = s
        return out or None
    except Exception:
        return None


def emit_inject(
    task_id: str,
    record: Mapping[str, Any],
    values: Optional[Mapping[str, Any]] = None,
) -> None:
    """Publish one data-injection timeline record onto the bus so the Textual
    dashboard's Data Injection pane can render it live (see events.EV_INJECT).

    ``record`` is a single ``*_timeline.jsonl`` entry (as written by the
    inject/stage/drift directors). ``values`` optionally carries the injected
    field values for display only — they are truncated here and are NOT
    persisted to the timeline file.

    Bus-only (no console echo, unlike ``emit_stage``): outside the TUI the
    directors already log a summary line, and per-op echoes would be noisy.
    Fully fail-open — a display path must never break a run.
    """
    try:
        get_bus().emit(
            EV_INJECT,
            task_id=task_id or "?",
            record=dict(record or {}),
            values=_truncate_inject_values(values),
        )
    except Exception:
        pass
