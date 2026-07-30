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

Every output pane is mouse-selectable at character granularity — drag for a word,
a fragment or several lines, click for a whole line, ctrl+shift+a for a whole
pane, then ctrl+c. That is not free: panes render Rich renderables straight to
Strips, which Textual cannot address by character without help. See
:class:`SelectableStrips`.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Dict, Optional

from . import lifecycle
from .events import (
    EV_CHAT, EV_INJECT, EV_INPUT_END, EV_INPUT_REQUEST, EV_LOG, EV_PROGRESS,
    EV_STAGE, EV_SUMMARY, EV_TOKEN, Event, get_bus,
)
from .input_bridge import get_input_bridge

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.strip import Strip
    from textual.widgets import (
        Footer, Header, Input, OptionList, ProgressBar, RichLog, Static,
    )
    from textual.widgets.option_list import Option
    from rich.table import Table
    from rich import box
    _TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when textual is missing
    _TEXTUAL_AVAILABLE = False


def textual_available() -> bool:
    return _TEXTUAL_AVAILABLE


def plain_of(content: Any) -> str:
    """Best-effort markup-free text for a renderable written to a panel.

    Pure and dependency-light so it is unit-testable without textual, matching
    the other helpers in this module.
    """
    if isinstance(content, str):
        try:
            from rich.markup import render as _render_markup

            return _render_markup(content).plain
        except Exception:  # noqa: BLE001 - malformed markup must still copy
            return content
    try:
        from rich.console import Console

        # Fixed generous width so copied tables are not wrapped to the pane.
        con = Console(width=200, no_color=True, record=True, legacy_windows=False)
        con.print(content)
        return con.export_text().rstrip("\n")
    except Exception:  # noqa: BLE001
        return str(content)


def char_index_at_cell(text: str, cell: int) -> int:
    """Character index in ``text`` at terminal-cell column ``cell``.

    Selections are addressed in *characters* but strips are cut in *cells*, and
    the two diverge the moment a log line contains a CJK glyph or an emoji
    (2 cells, 1 character). Pure and textual-free so it is unit-testable.
    """
    from rich.cells import cell_len

    if cell <= 0:
        return 0
    width = 0
    for index, char in enumerate(text):
        if width >= cell:
            return index
        width += cell_len(char)
    return len(text)


def cell_index_at_char(text: str, index: int) -> int:
    """Terminal-cell column of character ``index`` — inverse of the above."""
    from rich.cells import cell_len

    if index <= 0:
        return 0
    return cell_len(text[:index])


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


def chat_markup(payload: Dict[str, Any]) -> str:
    """Rich markup for one EV_CHAT payload in the Conversation pane (HITL).

    Pure function (no textual dependency) so it is unit-testable everywhere.
    ``payload`` = {text: str}. The text is already a readable, role-labelled
    line from HumanTurnSource (the agent's prior reply, the scripted suggestion,
    a harness notice) or the dashboard's own "you ›" echo; this only tints by a
    known prefix and escapes the text so model/user content can never inject
    live Rich markup into the pane.
    """
    text = str(payload.get("text", "")).replace("[", "\\[")
    stripped = text.lstrip()
    if stripped.startswith("── agent"):
        return f"[cyan]{text}[/]"
    if stripped.startswith("── scripted"):
        return f"[yellow]{text}[/]"
    if stripped.startswith("you ›"):
        return f"[green]{text}[/]"
    if stripped.startswith("("):  # (empty …) / (message too long …) notices
        return f"[dim italic]{text}[/]"
    return text


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
            "red" if status in ("unresolved", "failed", "error",
                                "no-match", "missing_src") else "dim")
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

    class LinePicker(ModalScreen):
        """Filterable list of every pane line; Enter copies the highlighted one.

        A keyboard-only path to one line, kept alongside mouse selection because
        it can *search*: the line you want is nearly always the error that just
        scrolled past, and filtering to it beats scrolling back to drag over it.
        Newest lines sort first for the same reason.
        """

        DEFAULT_CSS = """
        LinePicker { align: center middle; }
        LinePicker > Vertical {
            width: 90%; height: 80%;
            border: round $accent; background: $surface;
        }
        LinePicker Input { dock: top; }
        """
        BINDINGS = [("escape", "dismiss_picker", "Cancel")]

        def __init__(self, lines: "list[tuple[str, str]]") -> None:
            super().__init__()
            self._lines = lines

        def compose(self) -> "ComposeResult":
            with Vertical():
                yield Input(placeholder="filter… (Enter copies, Esc cancels)",
                            id="filter")
                yield OptionList(id="lines")

        def on_mount(self) -> None:
            self._repopulate("")
            self.query_one("#filter", Input).focus()

        def _repopulate(self, needle: str) -> None:
            opts = self.query_one("#lines", OptionList)
            opts.clear_options()
            n = needle.lower()
            self._shown: list[str] = []
            for pane, line in self._lines:
                if n and n not in line.lower():
                    continue
                self._shown.append(line)
                opts.add_option(Option(f"[dim]{pane}[/] {line.replace('[', '\\[')}"))
            if self._shown:
                opts.highlighted = 0

        def on_input_changed(self, event) -> None:
            self._repopulate(event.value)

        def on_input_submitted(self, event) -> None:
            self._copy_highlighted()

        def on_option_list_option_selected(self, event) -> None:
            self._copy_highlighted(event.option_index)

        def _copy_highlighted(self, index: "int | None" = None) -> None:
            opts = self.query_one("#lines", OptionList)
            idx = index if index is not None else opts.highlighted
            if idx is None or not self._shown or idx >= len(self._shown):
                self.dismiss(None)
                return
            self.dismiss(self._shown[idx])

        def action_dismiss_picker(self) -> None:
            self.dismiss(None)

    class SelectableStrips:
        """Mouse text-selection for widgets that render to Strips.

        Textual's selection machinery is built for widgets whose content is a
        ``Content``/``Text``: those publish a per-segment ``meta["offset"]`` (via
        ``Style.rich_style_with_offset``) that lets the compositor map a mouse
        position to a character, and their ``get_selection`` can slice that
        content back out. Widgets that hand Rich renderables straight to Strips —
        ``RichLog``, or a ``Static`` holding a ``Table`` — do neither, so they are
        wholly unselectable: a drag over one produces no character offsets at
        all, and Textual falls back to marking whole widgets ``SELECT_ALL``.

        This mixin supplies both halves for any such widget. Subclasses provide
        the document rows (:meth:`_select_rows`) and the scroll offset is taken
        from the widget, so the coordinate space is *(character index, visual
        row)* throughout — the same space the highlight paints in and
        ``get_selection`` extracts from.
        """

        #: Rows of the selectable document, in visual (already wrapped) order.
        def _select_rows(self) -> "list[str]":
            raise NotImplementedError

        def _row_text(self, row: int) -> "str | None":
            """Text of visual row ``row``, or None when there is no such row."""
            try:
                rows = self._select_rows()
                if 0 <= row < len(rows):
                    return rows[row]
            except Exception:  # noqa: BLE001
                pass
            return None

        def _selection_style(self):
            """Highlight style that stays legible on any theme.

            The stock ``screen--selection`` component sets BOTH background and
            foreground from theme vars, and on this dashboard's theme they
            resolve to the same colour (``#094472 on #094472``) — applying it
            wholesale paints the text the colour of its own background, i.e. an
            invisible "highlight". Taking only the background keeps each line's
            existing colour (red errors stay red) and just tints behind it;
            reverse video is the fallback when no background is themed at all.
            """
            from rich.style import Style

            try:
                comp = self.selection_style
                if comp.bgcolor is not None and comp.bgcolor != comp.color:
                    return Style(bgcolor=comp.bgcolor)
            except Exception:  # noqa: BLE001
                pass
            return Style(reverse=True)

        def _publish_offsets(self, strip, row: int, scroll_x: int):
            """Tag every segment with its document offset, enabling drag-select.

            This is the crux of character-level selection.
            ``Compositor.get_widget_and_offset_at`` resolves a mouse position by
            scanning the rendered line for a segment whose style carries
            ``meta["offset"]``, then walking that segment's characters to the
            exact column. With no such meta the lookup returns ``None`` — and a
            ``None`` content offset is precisely what caused the whole-pane
            highlight: ``Screen._forward_event`` records the drag with
            ``content_widget=None``, so ``_watch__select_state`` cannot take its
            ``is_single_content_widget`` fast path and instead marks every
            spanned widget ``SELECT_ALL``.

            Publishing the offsets makes that fast path fire, and Textual emits a
            real ``Selection.from_offsets(start, end)`` span — a word, a
            fragment, a line, or several — with no further help from us.
            ``Strip.apply_offsets`` is the same call Textual's own ``Log`` widget
            makes here, and memoises per strip, so repainting an idle pane costs
            nothing after the first pass.
            """
            text = self._row_text(row)
            if text is None:
                return strip
            try:
                # The strip is already cropped to the horizontal scroll, so its
                # cell 0 is document cell scroll_x — which is not character
                # scroll_x once a wide glyph has gone by.
                base = char_index_at_cell(text, scroll_x) if scroll_x else 0
                return strip.apply_offsets(base, row)
            except Exception:  # noqa: BLE001 - never break rendering over this
                return strip

        def _paint_selection(self, strip, row: int, scroll_x: int):
            """Restyle the selected cells of ``row`` so the drag is visible.

            Strip-rendering widgets never consult ``text_selection``, so a drag
            was invisible even once the text became extractable — you could copy,
            but saw nothing highlighted.
            """
            selection = self.text_selection
            if selection is None:
                return strip
            try:
                span = selection.get_span(row)
                if span is None:
                    return strip
                start, end = span
                # Spans are character indexes; cuts are cell columns, and the
                # two differ for wide glyphs — convert before cutting.
                text = self._row_text(row) or ""
                start = cell_index_at_char(text, start)
                if end == -1:
                    end = strip.cell_length + scroll_x
                else:
                    end = cell_index_at_char(text, end)
                # Selection offsets are document columns; strips are already
                # cropped to the horizontal scroll, so rebase onto the viewport.
                start = max(0, start - scroll_x)
                end = min(max(0, end - scroll_x), strip.cell_length)
                if end <= start:
                    return strip
                # divide() cuts BETWEEN the given points and drops the tail, so
                # the trailing cut at cell_length is required to keep the rest
                # of the line: [0:start], [start:end], [end:len].
                pieces = list(strip.divide([start, end, strip.cell_length]))
                if len(pieces) != 3:
                    return strip
                before, middle, after = pieces
                return Strip.join(
                    [before, middle.apply_style(self._selection_style()), after]
                )
            except Exception:  # noqa: BLE001 - never break rendering over a highlight
                return strip

        def _selectable_line(self, y: int):
            """``render_line`` body: paint the selection, THEN publish offsets.

            Order is load-bearing. Painting splits a segment in three with
            ``Strip.divide``, and every piece inherits the *original* segment's
            style — including its offset meta. Publish first and all three
            pieces then claim to start at the same character, so the next drag
            in a pane that already has a selection resolves to the wrong column.
            Publishing last walks the final segments and numbers them correctly.
            """
            strip = super().render_line(y)  # type: ignore[misc]
            scroll_x, scroll_y = self.scroll_offset
            row = scroll_y + y
            strip = self._paint_selection(strip, row, scroll_x)
            return self._publish_offsets(strip, row, scroll_x)

        def get_selection(self, selection):  # type: ignore[override]
            """Extract the text under a selection.

            ``Widget.get_selection`` extracts from ``self._render()`` and gives
            up unless that is a ``Text``/``Content``, so the base method returns
            None and Textual treats the widget as unselectable — dragging
            highlights nothing and ctrl+c has nothing to copy. Rebuilding the
            text from the same rows the offsets were published from puts us in
            exactly the coordinate space the Selection refers to, so
            partial-line, multi-line and select-all spans all extract correctly.
            """
            try:
                text = "\n".join(self._select_rows())
            except Exception:  # noqa: BLE001 - fall back to "not selectable"
                return None
            return selection.extract(text), "\n"

        def select_clicked_line(self, event) -> None:
            """Select exactly the line under the pointer.

            The counterpart to dragging rather than a substitute for it: a drag
            selects the characters you sweep, a click takes the whole line under
            the pointer without asking you to sweep it accurately.

            ``get_content_offset`` is used rather than ``event.y`` because the
            panes have a border, so the raw event offset is one row off.

            Only acts on a *real* click. Textual synthesises a ``Click`` after
            any mouse-up that lands on the same widget as the mouse-down
            (``App._forward_event``), drag included — so without this guard a
            drag-selected word would be widened back out to its whole line the
            instant the button came up. ``text_selection`` is the exact
            discriminator: ``Screen`` clears the selection on mouse-up when the
            pointer never moved, so a non-None selection here means a drag just
            produced one and must be left alone.
            """
            try:
                if self.text_selection is not None:
                    return
                offset = event.get_content_offset(self)
                if offset is None:
                    return
                row = self.scroll_offset.y + offset.y
                text = self._row_text(row)
                if text is None or not text.strip():
                    return
                self.screen.selections = {
                    self: Selection(Offset(0, row), Offset(len(text.rstrip()), row))
                }
                self.refresh()
            except Exception:  # noqa: BLE001 - a click must never crash the UI
                pass

    class SelectableStatic(SelectableStrips, Static):
        """The status pane: a Static holding a Rich table, made selectable.

        Same gap as RichLog — a Rich renderable goes straight to Strips, so the
        lifecycle table could be read but not copied. Rows come from the rendered
        strips because a Static keeps no line store of its own; the widget does
        not scroll, so visual row == document row.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._rows_key: Any = None
            self._rows_cache: "list[str] | None" = None

        def update(self, *args: Any, **kwargs: Any):  # type: ignore[override]
            self._rows_cache = None
            return super().update(*args, **kwargs)

        def on_resize(self, event) -> None:
            self._rows_cache = None

        def _select_rows(self) -> "list[str]":
            """Rows read back off the rendered strips, memoised.

            Rendering the whole pane to answer for one row would make a repaint
            O(rows²) — 5.7 ms at 75 rows, measured, against a ~16 ms frame. The
            cache is dropped on ``update()`` (the only way the table changes) and
            on resize (the only way it re-wraps).
            """
            try:
                size = self.content_region.size
            except Exception:  # noqa: BLE001 - not laid out yet
                return []
            if self._rows_cache is not None and self._rows_key == size:
                return self._rows_cache
            rows: list[str] = []
            for y in range(size.height):
                try:
                    rows.append(Static.render_line(self, y).text.rstrip())
                except Exception:  # noqa: BLE001
                    rows.append("")
            self._rows_key, self._rows_cache = size, rows
            return rows

        def render_line(self, y: int):  # type: ignore[override]
            return self._selectable_line(y)

        def on_click(self, event) -> None:
            self.select_clicked_line(event)

    class CopyableRichLog(SelectableStrips, RichLog):
        """RichLog that also keeps an unwrapped, markup-free mirror of its text.

        Reading the widget's own ``lines`` back would give strips already hard-
        wrapped to the pane width, which is unpleasant to paste into an issue or
        a chat. Mirroring at write time costs one string per line and keeps the
        original line structure intact. Subclassing (rather than editing every
        ``.write()`` call site) also means new call sites are covered for free —
        ``query_one(..., RichLog)`` still matches, since this is a RichLog.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.plain_lines: list[str] = []

        def write(self, content: Any, *args: Any, **kwargs: Any):  # type: ignore[override]
            try:
                text = plain_of(content)
                if text:
                    self.plain_lines.extend(text.split("\n"))
            except Exception:  # noqa: BLE001 - copy support must never break the UI
                pass
            return super().write(content, *args, **kwargs)

        def _select_rows(self) -> "list[str]":
            """Visual rows of the pane, as already wrapped for display.

            The widget's own strip store, which is the coordinate space Textual
            addresses selections in — deliberately NOT ``plain_lines``, which
            keeps the pre-wrap structure for whole-pane copies.
            """
            return [strip.text for strip in self.lines]

        def render_line(self, y: int):  # type: ignore[override]
            return self._selectable_line(y)

        def on_click(self, event) -> None:
            self.select_clicked_line(event)

    class HarnessDashboard(App):
        CSS = """
        Screen { layout: vertical; }
        #body { height: 1fr; }
        #left { width: 2fr; }
        #stream { height: 2fr; border: round $success; padding: 0 1; }
        #chat { height: 2fr; border: round $secondary; padding: 0 1; }
        #inject { height: 1fr; border: round $warning; padding: 0 1; }
        #log { height: 1fr; border: round $accent; padding: 0 1; }
        #status { width: 1fr; border: round $primary; padding: 0 1; }
        #progress { height: auto; padding: 0 1; }
        /* HITL input bar: hidden until a turn boundary requests a human line. */
        #prompt { display: none; border: round $primary; margin: 0 1; }
        """

        # ctrl-combos with priority=True: in interactive mode the HITL Input
        # holds focus, so a bare letter would be typed into the prompt instead
        # of firing the action. priority routes the key to the app first.
        # ctrl+s is deliberately avoided — it is XOFF on many terminals and can
        # appear to freeze the UI.
        BINDINGS = [
            ("q", "quit", "Quit"),
            # Drag the mouse over any pane to select, then ctrl+c. Textual
            # binds ctrl+c to help_quit by default, so without this override a
            # selection can be made but never copied. Quit stays on q / ctrl+q.
            # super+c is macOS's Cmd+C. Whether the terminal forwards it rather
            # than handling it itself varies (iTerm2 and Ghostty can, Terminal.app
            # does not), so it is an addition to ctrl+c, never a replacement —
            # ctrl+c reaches the app everywhere.
            Binding("ctrl+c", "copy_selection", "Copy selection", priority=True),
            Binding("super+c", "copy_selection", "Copy selection",
                    priority=True, show=False),
            # ctrl+shift+a, not ctrl+a: Input binds ctrl+a to "home" and
            # ctrl+shift+a to its own select-all, so this matches the convention
            # instead of stealing cursor movement from the HITL prompt.
            Binding("ctrl+shift+a", "select_all_pane", "Select whole pane",
                    priority=True),
            Binding("ctrl+l", "copy_line", "Copy a line", priority=True),
            Binding("ctrl+y", "copy_panels", "Copy all", priority=True),
            Binding("ctrl+o", "save_panels", "Save to file", priority=True),
        ]

        #: (widget id, heading) for the panes copy/save walk, in reading order.
        PANELS = (
            ("stream", "Live Stream"),
            ("chat", "Conversation"),
            ("inject", "Data Injection"),
            ("log", "Log"),
        )

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
            #: Text handed to the last copy action, whether or not the OS
            #: clipboard write was suppressed. Lets tests assert on copying
            #: without clobbering the developer's real clipboard.
            self.last_copied: str = ""
            # Exit intent captured from the worker thread. The harness signals
            # failure via sys.exit(code); a SystemExit raised on the Textual
            # worker thread is swallowed and would NOT set the process exit
            # code, so we record it here and re-raise on the main thread after
            # app.run(). None => worker never set it (e.g. user quit early) =>
            # treated as success (0).
            self._exit_code: Optional[int] = None

        # --- layout -------------------------------------------------------
        def panels_text(self) -> str:
            """Every pane's contents as one plain-text, paste-ready document."""
            blocks: list[str] = []
            for pid, heading in self.PANELS:
                try:
                    pane = self.query_one(f"#{pid}", RichLog)
                except Exception:  # noqa: BLE001 - pane may not exist yet
                    continue
                lines = getattr(pane, "plain_lines", None)
                if lines is None:  # plain RichLog fallback: wrapped strips
                    lines = [s.text.rstrip() for s in getattr(pane, "lines", [])]
                body = "\n".join(lines).strip("\n")
                if body:
                    blocks.append(f"===== {heading} =====\n{body}")
            return "\n\n".join(blocks) if blocks else "(all panes are empty)"

        def _copy_to_clipboard(self, text: str) -> str:
            """Copy via the OS clipboard, falling back to terminal OSC 52.

            pbcopy/xclip/wl-copy work even when the terminal eats OSC 52 (tmux,
            some SSH setups); Textual's own call is the portable fallback.

            ``WCB_TUI_NO_CLIPBOARD=1`` records the text without touching the
            real clipboard. Tests set it: pressing a copy key otherwise
            overwrites whatever the developer had copied, which is both a
            surprising side effect and irrecoverable.
            """
            import os
            import shutil
            import subprocess

            self.last_copied = text
            if os.environ.get("WCB_TUI_NO_CLIPBOARD") == "1":
                return "suppressed"

            for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, input=text.encode("utf-8"),
                                       check=True, timeout=10)
                        return cmd[0]
                    except Exception:  # noqa: BLE001 - try the next mechanism
                        pass
            try:
                self.copy_to_clipboard(text)
                return "terminal"
            except Exception:  # noqa: BLE001
                return ""

        def _panels_dump_path(self):
            from pathlib import Path

            out = Path.cwd() / "output"
            base = out if out.is_dir() else Path.cwd()
            return base / "panels_latest.txt"

        def pane_under_mouse(self) -> "SelectableStrips | None":
            """The pane the pointer is over, else the first pane that exists.

            Any selectable pane counts, including the status table — the whole
            point is that selection behaves the same everywhere.
            """
            try:
                widget, _ = self.screen.get_widget_at(*self.mouse_position)
                if isinstance(widget, SelectableStrips):
                    return widget
            except Exception:  # noqa: BLE001 - pointer may be off-screen
                pass
            for pid, _heading in self.PANELS:
                try:
                    return self.query_one(f"#{pid}", CopyableRichLog)
                except Exception:  # noqa: BLE001
                    continue
            return None

        def action_select_all_pane(self) -> None:
            """Select a whole pane — the only way to get a pane-wide selection.

            Dragging can no longer produce one by accident, so this is the
            explicit opt-in. Targets the pane under the pointer, since the panes
            are not focusable and there is no other "current" pane.
            """
            pane = self.pane_under_mouse()
            if pane is None:
                return
            pane.text_select_all()
            heading = dict(self.PANELS).get(pane.id or "", pane.id or "pane")
            self.notify(f"Selected all of {heading} — Ctrl+C to copy.",
                        title="Select all", timeout=3)

        def action_copy_selection(self) -> None:
            """Copy just the mouse-selected text (a line, or part of one)."""
            try:
                text = self.screen.get_selected_text()
            except Exception:  # noqa: BLE001 - never let copy break the UI
                text = None
            if not text:
                self.notify(
                    "Drag over the text first, then Ctrl+C. Click selects a "
                    "line, Ctrl+Shift+A a whole pane; Ctrl+Y copies every pane.",
                    title="Nothing selected", timeout=5,
                )
                return
            via = self._copy_to_clipboard(text)
            if via:
                n = len(text.splitlines())
                self.notify(f"Copied {n} line(s) ({via}).",
                            title="Copied", timeout=3)
            else:
                self.notify("No clipboard available — use Ctrl+O to save panes.",
                            title="Copy failed", severity="warning", timeout=6)

        def pane_lines(self) -> "list[tuple[str, str]]":
            """(pane heading, line) for every non-blank line, newest first."""
            out: list[tuple[str, str]] = []
            for pid, heading in self.PANELS:
                try:
                    pane = self.query_one(f"#{pid}", RichLog)
                except Exception:  # noqa: BLE001
                    continue
                lines = getattr(pane, "plain_lines", None)
                if lines is None:
                    lines = [s.text.rstrip() for s in getattr(pane, "lines", [])]
                for line in lines:
                    if line.strip():
                        out.append((heading, line))
            out.reverse()
            return out

        def action_copy_line(self) -> None:
            lines = self.pane_lines()
            if not lines:
                self.notify("Nothing in the panes yet.", title="Copy a line",
                            timeout=3)
                return

            def _copied(line: "str | None") -> None:
                if not line:
                    return
                via = self._copy_to_clipboard(line)
                if via:
                    self.notify(f"Copied ({via}): {line[:60]}",
                                title="Copied line", timeout=4)
                else:
                    self.notify("No clipboard available — Ctrl+O saves panes.",
                                title="Copy failed", severity="warning", timeout=6)

            self.push_screen(LinePicker(lines), _copied)

        def action_copy_panels(self) -> None:
            text = self.panels_text()
            via = self._copy_to_clipboard(text)
            n = len(text.splitlines())
            if via:
                self.notify(f"Copied {n} lines from all panes ({via}).",
                            title="Copied", timeout=4)
            else:
                # No clipboard route: still give them something to paste from.
                self.action_save_panels()

        def action_save_panels(self) -> None:
            text = self.panels_text()
            try:
                path = self._panels_dump_path()
                path.write_text(text, encoding="utf-8")
                self.notify(f"{path}", title="Saved panes", timeout=8)
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Save failed: {exc}", title="Error",
                            severity="error", timeout=8)

        def compose(self) -> "ComposeResult":
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with Vertical(id="left"):
                    # Live LLM stream (EV_TOKEN, populated by the stream
                    # renderer's bus mode) above the harness log. Empty until
                    # a --stream run emits; harmless otherwise.
                    yield CopyableRichLog(id="stream", highlight=False, markup=True, wrap=True)
                    # Human-in-the-loop conversation (EV_CHAT): the scripted
                    # suggestion, the agent's prior reply, and the human's own
                    # turns. Empty during static runs — consistent with the
                    # other panes that stay empty until their mode fires.
                    yield CopyableRichLog(id="chat", highlight=False, markup=True, wrap=True)
                    # Mid-run data-injection feed (EV_INJECT): what the drift/
                    # inject/stage directors mutate, when, and the injected
                    # values. Empty until an inject-enabled task fires.
                    yield CopyableRichLog(id="inject", highlight=False, markup=True, wrap=True)
                    yield CopyableRichLog(id="log", highlight=False, markup=True, wrap=True)
                yield SelectableStatic(self._render_status(), id="status")
            # HITL input bar: hidden (display:none) until an EV_INPUT_REQUEST
            # reveals it at a turn boundary. Present in every run so the layout
            # is identical across modes; it simply never appears in static runs.
            yield Input(id="prompt", placeholder="type your message — Enter sends")
            yield ProgressBar(id="progress", total=max(1, self._total_hint))
            yield Footer()

        def on_mount(self) -> None:
            self.title = "Kensei Harness"
            self.sub_title = "live dashboard"
            # Border titles label each pane (Textual RichLog supports these).
            try:
                self.query_one("#stream", RichLog).border_title = "Live Stream"
                self.query_one("#chat", RichLog).border_title = "Conversation"
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
            elif evt.kind == EV_CHAT:
                self._handle_chat(evt.payload)
            elif evt.kind == EV_INPUT_REQUEST:
                self._handle_input_request(evt.payload)
            elif evt.kind == EV_INPUT_END:
                self._handle_input_end(evt.payload)

        def _handle_inject(self, p: Dict[str, Any]) -> None:
            # Data Injection pane: one entry per director timeline record.
            # inject_markup escapes all task content; failures are swallowed so
            # a display problem never reaches the harness worker.
            try:
                self.query_one("#inject", RichLog).write(inject_markup(p))
            except Exception:
                pass

        def _handle_chat(self, p: Dict[str, Any]) -> None:
            # Conversation pane (HITL): one role-labelled line. chat_markup
            # escapes all text; failures swallowed so a display problem never
            # reaches the harness worker.
            try:
                self.query_one("#chat", RichLog).write(chat_markup(p))
            except Exception:
                pass

        def _handle_input_request(self, p: Dict[str, Any]) -> None:
            # A turn boundary needs a human line: reveal + focus the input bar
            # and label it with the prompt HumanTurnSource passed. The worker
            # thread is parked in InputBridge.request() until on_input_submitted.
            prompt = str(p.get("prompt", "")).strip()
            try:
                inp = self.query_one("#prompt", Input)
                inp.border_title = prompt or "your message"
                inp.value = ""
                inp.display = True
                self.set_focus(inp)
            except Exception:
                pass

        def _handle_input_end(self, _p: Dict[str, Any]) -> None:
            try:
                inp = self.query_one("#prompt", Input)
                inp.value = ""
                inp.display = False
            except Exception:
                pass

        def on_input_submitted(self, event: "Input.Submitted") -> None:
            # Human hit Enter in the #prompt bar. Echo their turn into the
            # Conversation pane (the input clears, so this is the record of what
            # they sent), hide the bar, then hand the RAW value to the bridge —
            # an empty value is a bare Enter, which HumanTurnSource maps to
            # "accept the scripted suggestion" (semantics live there, not here).
            if getattr(event, "input", None) is not None and event.input.id != "prompt":
                return
            value = event.value
            echo = f"you › {value}" if value.strip() else "you › (sent scripted message)"
            try:
                self.query_one("#chat", RichLog).write(chat_markup({"text": echo}))
            except Exception:
                pass
            try:
                inp = self.query_one("#prompt", Input)
                inp.value = ""
                inp.display = False
            except Exception:
                pass
            get_input_bridge().submit(value)

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
            # Unblock any worker parked in InputBridge.request() (interactive
            # Mode 2) so quitting the dashboard mid-input can never hang the
            # harness worker thread — request() raises EOFError, which
            # HumanTurnSource treats as end-of-session.
            try:
                get_input_bridge().close()
            except Exception:
                pass
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
