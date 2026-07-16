"""Opt-in full-screen Textual live dashboard for the harness.

Activated by ``--tui`` / ``WCB_TUI=1`` (and only on a real terminal — see
``eval/run_batch.py::main``). When active, the real batch work runs on a Textual
worker thread while the UI renders, driven entirely off the shared event bus:

  * a live scrolling **log** pane (all ``logging`` output is rerouted here so it
    never corrupts the full-screen canvas),
  * a **container lifecycle** status table (one row per task, showing its current
    stage),
  * a **progress** bar, and
  * the final **execution summary** (table + panel) rendered in-app on completion.

All Textual imports are guarded so importing this module never fails when
``textual`` is absent; :func:`run_with_dashboard` then reports unavailability and
the caller falls back to the default Rich logging path.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Dict, Optional

from . import lifecycle
from .events import (
    EV_INJECT, EV_LOG, EV_PROGRESS, EV_STAGE, EV_SUMMARY, EV_TOKEN, Event, get_bus,
)

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, ProgressBar, RichLog, Static
    from rich.table import Table
    from rich import box
    _TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when textual is missing
    _TEXTUAL_AVAILABLE = False


def textual_available() -> bool:
    return _TEXTUAL_AVAILABLE


def token_markup(payload: Dict[str, Any]) -> str:
    """Rich markup for one EV_TOKEN payload in the Live Stream pane.

    Pure function (no textual dependency) so it is unit-testable everywhere.
    The producer (stream_renderer bus mode) sends display-ready text — judge
    lines already carry their `[judge:<family>]` prefix — so this only maps
    style → markup and escapes user text so model output can never inject
    Rich markup into the pane.
    """
    text = str(payload.get("text", "")).replace("[", "\\[")
    style = payload.get("style", "status")
    if style == "thinking":
        return f"[dim]\\[thinking] {text}[/]"
    if style == "text":
        return text
    if style == "judge":
        return f"[cyan]{text}[/]"
    return f"[dim italic]{text}[/]"


def _esc(v: Any) -> str:
    """Stringify + neutralize Rich markup so injected task content (email
    bodies, field values) can never open live style tags in the pane."""
    return str(v).replace("[", "\\[")


# Compact per-record summary for the Data Injection pane. Keys are drawn from
# whatever the emitting director put in the timeline entry; unknown shapes still
# render their `type` so nothing is silently dropped.
_INJECT_TYPE_STYLE = {
    "inject.seed.start": "yellow",
    "inject.seed.done": "yellow",
    "inject.stage.applied": "bold yellow",
    "inject.api": "magenta",
    "inject.fs": "cyan",
    "inject.snapshot": "dim",
    # stage_director (stages.yaml) + drift_director record types.
    "stage.applied": "bold yellow",
    "turn": "dim",
    "event.fired": "magenta",
    "director.start": "dim",
    "director.stop": "dim",
    "director.error": "red",
    "event.error": "red",
}


def inject_markup(payload: Dict[str, Any]) -> str:
    """Rich markup for one EV_INJECT payload in the Data Injection pane.

    Pure function (no textual dependency) so it is unit-testable everywhere.
    ``payload`` = {task_id, record: dict, values: dict|None}. Renders a header
    line (type · stage · target · status) plus one indented line per injected
    value when ``values`` is present. All interpolated text is markup-escaped.
    """
    rec = payload.get("record") or {}
    values = payload.get("values") or {}
    rtype = str(rec.get("type", "inject"))
    style = _INJECT_TYPE_STYLE.get(rtype, "yellow")

    # Header: type, then whichever locating fields exist, then status.
    parts = [f"[{style}]▸ {_esc(rtype)}[/]"]
    stage = rec.get("stage") or rec.get("stage_id")
    if stage:
        parts.append(f"[dim]{_esc(stage)}[/]")
    turn = rec.get("turn_index", rec.get("turn", rec.get("applied_before_turn")))
    if turn is not None and turn != "":
        parts.append(f"[dim]@turn {_esc(turn)}[/]")
    api = rec.get("api")
    table = rec.get("table")
    if api or table:
        tgt = "·".join(_esc(x) for x in (api, table) if x)
        pk = rec.get("pk")
        parts.append(f"{tgt}" + (f" pk={_esc(pk)}" if pk not in (None, "") else ""))
    path = rec.get("path")
    if path:
        parts.append(_esc(path))
    keys = rec.get("action_keys")
    if isinstance(keys, (list, tuple)) and keys:
        parts.append(f"[dim]keys={_esc(','.join(str(k) for k in keys))}[/]")
    # Volume counts on seed/stage records. Guard against bool (a subclass of
    # int): on inject.api records ``silent`` is a flag, rendered in the status
    # tag below, not a count.
    for label in ("silent", "loud", "fs", "ops"):
        n = rec.get(label)
        if isinstance(n, int) and not isinstance(n, bool):
            parts.append(f"[dim]{label}={n}[/]")
    status = rec.get("status")
    if status:
        sstyle = "green" if status in ("applied", "ok") else (
            "red" if status in ("unresolved", "failed") else "dim")
        silent = rec.get("silent")
        tag = f"{status}" + (" · silent" if silent is True else
                             " · loud" if silent is False else "")
        parts.append(f"[{sstyle}]{_esc(tag)}[/]")

    line = "  ".join(parts)
    # Injected values (display-only; already truncated by emit_inject).
    for k, v in values.items():
        line += f"\n    [dim]{_esc(k)}:[/] {_esc(v)}"
    return line


class _BusLogHandler(logging.Handler):
    """Logging handler that forwards records to the event bus as EV_LOG events."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        get_bus().emit(EV_LOG, level=record.levelname, message=msg)


if _TEXTUAL_AVAILABLE:

    _LEVEL_STYLE = {
        "DEBUG": "dim",
        "INFO": "cyan",
        "WARNING": "yellow",
        "ERROR": "bold red",
        "CRITICAL": "bold white on red",
    }

    class HarnessDashboard(App):
        CSS = """
        Screen { layout: vertical; }
        #body { height: 1fr; }
        #left { width: 2fr; }
        #stream { height: 2fr; border: round $success; padding: 0 1; }
        #inject { height: 1fr; border: round $warning; padding: 0 1; }
        #log { height: 1fr; border: round $accent; padding: 0 1; }
        #status { width: 1fr; border: round $primary; padding: 0 1; }
        #progress { height: auto; padding: 0 1; }
        """

        BINDINGS = [("q", "quit", "Quit")]

        def __init__(self, work: Callable[[], Any], total_hint: int = 0) -> None:
            super().__init__()
            self._work = work
            self._total_hint = total_hint
            self._stages: Dict[str, Dict[str, str]] = {}
            self._done = False
            self._completed = 0
            self._total = total_hint
            self._unsub: Optional[Callable[[], None]] = None
            self._log_handler: Optional[_BusLogHandler] = None
            # Exit intent captured from the worker thread. The harness signals
            # failure via sys.exit(code); a SystemExit raised on the Textual
            # worker thread is swallowed and would NOT set the process exit
            # code, so we record it here and re-raise on the main thread after
            # app.run(). None => worker never set it (e.g. user quit early) =>
            # treated as success (0).
            self._exit_code: Optional[int] = None

        # --- layout -------------------------------------------------------
        def compose(self) -> "ComposeResult":
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with Vertical(id="left"):
                    # Live LLM stream (EV_TOKEN, populated by the stream
                    # renderer's bus mode) above the harness log. Empty until
                    # a --stream run emits; harmless otherwise.
                    yield RichLog(id="stream", highlight=False, markup=True, wrap=True)
                    # Mid-run data-injection feed (EV_INJECT): what the drift/
                    # inject/stage directors mutate, when, and the injected
                    # values. Empty until an inject-enabled task fires.
                    yield RichLog(id="inject", highlight=False, markup=True, wrap=True)
                    yield RichLog(id="log", highlight=False, markup=True, wrap=True)
                yield Static(self._render_status(), id="status")
            yield ProgressBar(id="progress", total=max(1, self._total_hint))
            yield Footer()

        def on_mount(self) -> None:
            self.title = "Kensei Harness"
            self.sub_title = "live dashboard"
            # Border titles label each pane (Textual RichLog supports these).
            try:
                self.query_one("#stream", RichLog).border_title = "Live Stream"
                self.query_one("#inject", RichLog).border_title = "Data Injection"
                self.query_one("#log", RichLog).border_title = "Log"
            except Exception:
                pass
            # Reroute all logging into the dashboard log pane.
            self._log_handler = _BusLogHandler()
            self._log_handler.setFormatter(logging.Formatter("%(message)s"))
            root = logging.getLogger()
            self._saved_handlers = list(root.handlers)
            self._saved_level = root.level
            for h in self._saved_handlers:
                root.removeHandler(h)
            root.addHandler(self._log_handler)
            root.setLevel(logging.INFO)
            # Subscribe to the bus; marshal every event onto our thread.
            self._unsub = get_bus().subscribe(self._on_bus_event)
            lifecycle.set_dashboard_active(True)
            # Run the real work off the UI thread.
            self.run_worker(self._run_work, thread=True, name="harness-work")

        # --- worker -------------------------------------------------------
        def _run_work(self) -> None:
            try:
                self._work()
            except SystemExit as exc:
                # The harness signals task failure with sys.exit(code). SystemExit
                # is not an Exception (so `except Exception` misses it) and, raised
                # on this worker thread, it is swallowed without setting the process
                # exit code. Record the code; run_with_dashboard re-raises it on the
                # main thread so `--tui` runs exit non-zero on failure like the
                # default path does.
                code = exc.code
                if code is None:
                    self._exit_code = 0
                elif isinstance(code, int):
                    self._exit_code = code
                else:
                    # sys.exit("message"): a non-int code means failure (exit 1);
                    # surface the message in the log pane before we exit.
                    get_bus().emit(EV_LOG, level="ERROR", message=str(code))
                    self._exit_code = 1
            except Exception as exc:  # surface, don't crash the UI
                # An unhandled harness exception is a failure too; on the default
                # (main-thread) path it would exit non-zero, so mirror that here.
                get_bus().emit(EV_LOG, level="ERROR", message=f"harness error: {exc}")
                self._exit_code = 1
            else:
                self._exit_code = 0
            finally:
                self.call_from_thread(self._on_work_done)

        def _on_work_done(self) -> None:
            self._done = True
            self.sub_title = "completed — press q to exit"
            try:
                self.query_one("#log", RichLog).write("[bold green]● run complete — press q to exit[/]")
            except Exception:
                pass

        # --- bus plumbing -------------------------------------------------
        def _on_bus_event(self, evt: Event) -> None:
            # Called from the worker thread; hop to the UI thread.
            try:
                self.call_from_thread(self._handle_event, evt)
            except Exception:
                pass

        def _handle_event(self, evt: Event) -> None:
            if evt.kind == EV_LOG:
                self._handle_log(evt.payload)
            elif evt.kind == EV_STAGE:
                self._handle_stage(evt.payload)
            elif evt.kind == EV_PROGRESS:
                self._handle_progress(evt.payload)
            elif evt.kind == EV_SUMMARY:
                self._handle_summary(evt.payload)
            elif evt.kind == EV_TOKEN:
                self._handle_token(evt.payload)
            elif evt.kind == EV_INJECT:
                self._handle_inject(evt.payload)

        def _handle_inject(self, p: Dict[str, Any]) -> None:
            # Data Injection pane: one entry per director timeline record.
            # inject_markup escapes all task content; failures are swallowed so
            # a display problem never reaches the harness worker.
            try:
                self.query_one("#inject", RichLog).write(inject_markup(p))
            except Exception:
                pass

        def _handle_token(self, p: Dict[str, Any]) -> None:
            # Live Stream pane: display-ready text from the stream renderer's
            # bus mode (docs/STREAMING_PLAN.md). Escaping happens inside
            # token_markup; failures are swallowed like every other handler —
            # a display problem must never reach the harness worker.
            try:
                self.query_one("#stream", RichLog).write(token_markup(p))
            except Exception:
                pass

        def _handle_log(self, p: Dict[str, Any]) -> None:
            level = p.get("level", "INFO")
            style = _LEVEL_STYLE.get(level, "white")
            msg = str(p.get("message", "")).replace("[", "\\[")
            try:
                self.query_one("#log", RichLog).write(f"[{style}]{level:<7}[/] {msg}")
            except Exception:
                pass

        def _handle_stage(self, p: Dict[str, Any]) -> None:
            task_id = p.get("task_id", "?")
            self._stages[task_id] = {
                "glyph": p.get("glyph", "·"),
                "style": p.get("style", "white"),
                "label": p.get("label", ""),
                "detail": p.get("detail", ""),
                "stage": p.get("stage", ""),
            }
            try:
                self.query_one("#status", Static).update(self._render_status())
                self.query_one("#log", RichLog).write(
                    f"[{p.get('style','white')}]{p.get('glyph','·')} {task_id} — {p.get('label','')}"
                    + (f" · {p.get('detail','')}" if p.get("detail") else "")
                )
            except Exception:
                pass

        def _handle_progress(self, p: Dict[str, Any]) -> None:
            self._completed = int(p.get("completed", self._completed))
            self._total = int(p.get("total", self._total) or 1)
            try:
                bar = self.query_one("#progress", ProgressBar)
                bar.update(total=max(1, self._total), progress=self._completed)
            except Exception:
                pass

        def _handle_summary(self, p: Dict[str, Any]) -> None:
            stats = p.get("stats", {})
            elapsed = p.get("elapsed_seconds")
            try:
                from .summary import (
                    build_results_table, build_summary_panel,
                    build_criteria_table, build_tests_table,
                )
                log = self.query_one("#log", RichLog)
                results = p.get("results") or []
                tbl = build_results_table(results)
                if tbl is not None:
                    log.write(tbl)
                panel = build_summary_panel(stats, elapsed)
                if panel is not None:
                    log.write(panel)
                # Per-task rubric-criteria + test-case breakdown.
                for r in results:
                    crit = build_criteria_table(r.get("scores") or {})
                    tests = build_tests_table(r.get("test_result") or {})
                    if crit is not None or tests is not None:
                        log.write(f"[bold cyan]Details — {r.get('task_id', '?')}[/]")
                    if crit is not None:
                        log.write(crit)
                    if tests is not None:
                        log.write(tests)
            except Exception:
                pass

        # --- rendering ----------------------------------------------------
        def _render_status(self):
            table = Table(title="Container Lifecycle", box=box.SIMPLE, expand=True)
            table.add_column("", justify="center", no_wrap=True)
            table.add_column("Task", overflow="fold")
            table.add_column("Stage", no_wrap=True)
            if not self._stages:
                table.add_row("·", "[dim]waiting…[/]", "")
            for task_id, s in self._stages.items():
                table.add_row(
                    f"[{s['style']}]{s['glyph']}[/]",
                    task_id,
                    f"[{s['style']}]{s['label']}[/]",
                )
            return table

        def on_unmount(self) -> None:
            # Restore logging + unsubscribe so a second run is clean.
            try:
                if self._unsub:
                    self._unsub()
                lifecycle.set_dashboard_active(False)
                root = logging.getLogger()
                if self._log_handler:
                    root.removeHandler(self._log_handler)
                for h in getattr(self, "_saved_handlers", []):
                    root.addHandler(h)
                root.setLevel(getattr(self, "_saved_level", logging.INFO))
            except Exception:
                pass


def run_with_dashboard(work: Callable[[], Any], total_hint: int = 0) -> bool:
    """Run ``work`` inside the Textual dashboard.

    Returns True if the dashboard ran, False if Textual is unavailable (caller
    should then fall back to the plain Rich logging path).
    """
    if not _TEXTUAL_AVAILABLE:
        return False
    app = HarnessDashboard(work, total_hint=total_hint)
    app.run()
    # app.run() has returned, so we are back on the main thread. Propagate the
    # harness's exit intent that the worker thread captured; without this a
    # failed run under --tui would exit 0 (the worker's SystemExit is lost).
    code = getattr(app, "_exit_code", None)
    if code:
        sys.exit(code)
    return True
