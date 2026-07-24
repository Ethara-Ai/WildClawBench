"""Kensei harness terminal UI layer (Rich default + opt-in Textual dashboard).

Public surface:
  * ``console`` — shared Rich console + ``install_rich_logging`` + ``is_interactive``
  * ``events``  — process-wide event bus (``get_bus``)
  * ``lifecycle`` — container/task lifecycle stage rendering (``emit_stage``, STAGE_*)
  * ``summary`` — execution summary rendering (``render_execution_summary``, ``compute_stats``)
  * ``tui``    — opt-in Textual dashboard (``run_with_dashboard``, ``textual_available``)
  * ``input_bridge`` — worker↔UI input channel for interactive Mode 2 (``get_input_bridge``)
  * ``interactive`` — ``tui_io`` adapter feeding HumanTurnSource's ``io`` seam

Importing this package is side-effect free and never requires ``textual``.
"""
from __future__ import annotations

from . import console, events, input_bridge, interactive, lifecycle, summary, tui  # noqa: F401

__all__ = [
    "console", "events", "input_bridge", "interactive", "lifecycle", "summary", "tui",
]
