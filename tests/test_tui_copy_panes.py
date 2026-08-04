"""Tests for getting text out of the dashboard panes.

The panes render Rich renderables straight to Strips, which Textual can neither
address by character nor extract from — so an operator could read an error in the
Live Stream pane but not copy it into a bug report, and a drag selected the whole
pane instead of the word under the pointer. Covers: ``plain_of`` (pure, no
textual needed), the plain-text mirror kept by ``CopyableRichLog``, the copy/save
key bindings, and character-level mouse selection driven through Textual's real
event path. The headless tests skip cleanly without textual.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.ui.tui import plain_of, textual_available  # noqa: E402


# ------------------------------------------------------------------ plain_of

def test_plain_of_strips_markup():
    assert plain_of("[red]ERROR[/] LLM request timed out.") == \
        "ERROR LLM request timed out."


def test_plain_of_restores_escaped_brackets():
    """Panes escape ``[`` so model output cannot inject live markup; copying
    must give back the literal text the operator sees on screen."""
    assert plain_of("[cyan]\\[judge:sonnet] verdict received[/]") == \
        "[judge:sonnet] verdict received"


def test_plain_of_passes_through_malformed_markup():
    """A stray tag must not lose the line — copy support is best-effort."""
    assert "unclosed" in plain_of("[bold]unclosed")


def test_plain_of_renders_non_string_renderables():
    pytest.importorskip("rich")
    from rich.table import Table

    t = Table("crit", "verdict")
    t.add_row("case pack corrected to 12", "Yes")
    out = plain_of(t)
    assert "case pack corrected to 12" in out and "Yes" in out


# --------------------------------------------------------------- headless UI

pytestmark_textual = pytest.mark.skipif(
    not textual_available(), reason="textual not installed"
)


@pytest.fixture(autouse=True)
def _no_real_clipboard(monkeypatch):
    """Never write to the machine's clipboard from a test.

    The copy actions shell out to pbcopy/wl-copy/xclip, so a test that presses
    ctrl+c or ctrl+y silently overwrites whatever the developer had copied —
    surprising, and unrecoverable. The app records ``last_copied`` either way,
    which is what the assertions use.
    """
    monkeypatch.setenv("WCB_TUI_NO_CLIPBOARD", "1")


@pytestmark_textual
def test_copyable_richlog_mirrors_unwrapped_text():
    """The mirror must keep ORIGINAL line structure — reading the widget's own
    strips back would return lines already hard-wrapped to the pane width."""
    from textual.app import App, ComposeResult
    from src.utils.ui.tui import CopyableRichLog

    long_line = "x" * 400

    class _T(App):
        def compose(self) -> ComposeResult:
            yield CopyableRichLog(id="s", highlight=False, markup=True, wrap=True)

    async def _run():
        app = _T()
        async with app.run_test() as pilot:
            pane = app.query_one("#s", CopyableRichLog)
            pane.write("[red]ERROR[/] boom")
            pane.write(long_line)
            await pilot.pause()
            return list(pane.plain_lines)

    lines = asyncio.run(_run())
    assert lines[0] == "ERROR boom"
    assert long_line in lines, "long line was wrapped in the copy mirror"


@pytestmark_textual
def test_panels_text_and_key_bindings(tmp_path, monkeypatch):
    """End-to-end: real dashboard, real key presses, real file on disk.

    The hidden HITL Input is kept out of the auto-focus walk during a run so a
    bare ``q`` reaches the quit binding; ctrl+o / ctrl+y stay priority bindings
    so they still fire once a turn boundary DOES focus the Input.
    """
    from textual.widgets import Input, RichLog
    from src.utils.ui import tui

    monkeypatch.chdir(tmp_path)

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#stream", RichLog).write("[red]ERROR[/] LLM request timed out.")
            app.query_one("#chat", RichLog).write("you > hello")
            await pilot.pause()

            text = app.panels_text()
            input_focused = isinstance(app.focused, Input)

            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("ctrl+y")   # must not raise even with no clipboard
            await pilot.pause()
            return text, input_focused

    text, input_focused = asyncio.run(_run())

    assert not input_focused, (
        "the hidden HITL Input must NOT hold focus during a run, or a bare q "
        "would be typed into it instead of quitting the dashboard"
    )
    assert "===== Live Stream =====" in text
    assert "ERROR LLM request timed out." in text
    assert "===== Conversation =====" in text and "you > hello" in text

    dumps = list(tmp_path.rglob("panels_latest.txt"))
    assert dumps, "ctrl+o did not write a dump file"
    assert "LLM request timed out." in dumps[0].read_text(encoding="utf-8")


@pytestmark_textual
def test_panels_text_skips_empty_panes():
    """A pane nobody wrote to contributes no heading — the dump should not be
    padded with empty sections the operator then has to scroll past."""
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test() as pilot:
            await pilot.pause()   # only the Log pane self-populates on mount
            return app.panels_text()

    text = asyncio.run(_run())
    assert "===== Log =====" in text
    for absent in ("Live Stream", "Conversation", "Data Injection"):
        assert f"===== {absent} =====" not in text


# ---------------------------------------------------- mouse text selection
#
# A stock RichLog is not selectable at all, and even once it is, Textual cannot
# resolve a mouse position to a character inside it without per-segment
# ``meta["offset"]`` tags — which is what made a drag fall back to selecting the
# whole widget. The two ingredients are ``get_selection`` (extractable) and
# ``Strip.apply_offsets`` (addressable); the tests below pin both.


@pytestmark_textual
def test_stock_richlog_is_not_selectable_but_ours_is():
    """Stock RichLog renders to Strips, so Widget.get_selection() bails and the
    pane is unselectable — dragging highlights nothing and ctrl+c has nothing to
    copy. CopyableRichLog implements get_selection, which is what makes mouse
    selection work at all."""
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog
    from textual.geometry import Offset
    from textual.selection import Selection
    from src.utils.ui.tui import CopyableRichLog

    class _App(App):
        def compose(self) -> ComposeResult:
            yield RichLog(id="stock", highlight=False, markup=True, wrap=True)
            yield CopyableRichLog(id="ours", highlight=False, markup=True, wrap=True)

    async def _run():
        app = _App()
        async with app.run_test(size=(100, 20)) as pilot:
            for wid in ("#stock", "#ours"):
                app.query_one(wid, RichLog).write("SENTINEL line one")
            await pilot.pause()
            sel = Selection(Offset(0, 0), Offset(8, 0))
            return (app.query_one("#stock", RichLog).get_selection(sel),
                    app.query_one("#ours", CopyableRichLog).get_selection(sel))

    stock, ours = asyncio.run(_run())
    assert stock is None, "stock RichLog unexpectedly became selectable"
    assert ours is not None and ours[0] == "SENTINEL"


@pytestmark_textual
def test_selection_extracts_arbitrary_spans():
    """Extraction in isolation, across span shapes a drag can produce.

    Complements the end-to-end drag tests below by pinning ``get_selection``
    against constructed offsets, so a failure says whether extraction or the
    offset plumbing broke."""
    from textual.widgets import RichLog
    from textual.geometry import Offset
    from textual.selection import Selection
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("ERROR-SENTINEL LLM request timed out at 22:33")
            pane.write("second line must not appear")
            await pilot.pause()
            ext = lambda s: pane.get_selection(s)[0]
            return (
                ext(Selection(Offset(0, 0), Offset(14, 0))),
                ext(Selection(Offset(15, 0), Offset(26, 0))),
                ext(Selection(Offset(0, 0), Offset(6, 1))),
            )

    partial, middle, across = asyncio.run(_run())
    assert partial == "ERROR-SENTINEL", "partial-word drag mis-extracted"
    assert middle == "LLM request", "mid-line drag mis-extracted"
    assert across.startswith("ERROR-SENTINEL") and across.endswith("second"), \
        "cross-row drag mis-extracted"


@pytestmark_textual
def test_line_picker_filters_and_returns_one_line():
    from textual.widgets import OptionList, RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("[red]ERROR[/] LLM request timed out.")
            pane.write("[cyan]\\[judge:sonnet] verdict received[/]")
            pane.write("some unrelated chatter")
            await pilot.pause()

            await pilot.press("ctrl+l")
            await pilot.pause()
            assert isinstance(app.screen, tui.LinePicker), "picker did not open"
            total = len(app.screen.query_one("#lines", OptionList).options)

            for ch in "judge":
                await pilot.press(ch)
            await pilot.pause()
            shown = list(app.screen._shown)

            captured = {}
            app.screen.dismiss = lambda v=None, _c=captured: _c.setdefault("v", v)
            app.screen._copy_highlighted()
            return total, shown, captured.get("v")

    total, shown, picked = asyncio.run(_run())
    assert total >= 3, "picker should list every pane line"
    assert shown == ["[judge:sonnet] verdict received"], (
        f"filter did not narrow to one line: {shown}"
    )
    assert picked == "[judge:sonnet] verdict received"


@pytestmark_textual
def test_pane_lines_newest_first_and_drops_blanks():
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("first")
            pane.write("   ")
            pane.write("second")
            await pilot.pause()
            return app.pane_lines()

    rows = asyncio.run(_run())
    texts = [line for _pane, line in rows]
    assert "second" in texts and "first" in texts
    assert texts.index("second") < texts.index("first"), "newest must sort first"
    assert all(t.strip() for t in texts), "blank lines must be dropped"


# ------------------------------------------------ visible selection highlight


@pytestmark_textual
def test_drag_highlight_is_painted_on_the_selected_span_only():
    """Dragging must LOOK like a selection, not just copy silently.

    RichLog never consults text_selection when rendering, so before this the
    drag was invisible. Two bugs this pins:
      * Strip.divide() cuts BETWEEN points and drops the tail, so a trailing
        cut at cell_length is required or the whole override no-ops;
      * the stock screen--selection style resolves to the same fg and bg on
        this theme, which would paint the text its own background colour.
    """
    from textual.widgets import RichLog
    from textual.geometry import Offset
    from textual.selection import Selection
    from src.utils.ui import tui

    LINE = "ERROR-SENTINEL LLM request timed out at 22:33"

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write(LINE)
            await pilot.pause()
            plain = pane.render_line(0)
            app.screen.selections = {pane: Selection(Offset(0, 0), Offset(14, 0))}
            await pilot.pause()
            return plain, pane.render_line(0)

    plain, hl = asyncio.run(_run())

    def segs(strip):
        return [(s.text, s.style) for s in strip._segments]

    assert not any(getattr(s, "reverse", False) or s.bgcolor != segs(plain)[0][1].bgcolor
                   for _t, s in segs(plain) if s), "unselected line should be unstyled"

    hl_segs = segs(hl)
    assert hl_segs[0][0] == "ERROR-SENTINEL", (
        f"highlight span wrong — got {hl_segs[0][0]!r}; a dropped trailing "
        f"divide() cut makes the override silently no-op"
    )
    first_style = hl_segs[0][1]
    rest_style = hl_segs[1][1]
    assert first_style != rest_style, "selected span is styled identically to the rest"
    assert getattr(first_style, "reverse", False) or (
        first_style.bgcolor is not None and first_style.bgcolor != first_style.color
    ), f"highlight would be invisible: {first_style!r}"
    assert hl_segs[1][0].startswith(" LLM request"), "tail of the line was dropped"


# --------------------------------------------------- click selects ONE line


@pytestmark_textual
def test_click_selects_only_the_clicked_line():
    """Clicking bounds the selection to one row — both for what ctrl+c copies
    and for what is visibly highlighted. It is the counterpart to dragging, for
    when you want the whole line without sweeping over it accurately.

    Offsets here are widget-relative and the pane has a border, so content row
    0 sits at widget y=1; y=0 is the border and must select nothing.
    """
    from textual.widgets import RichLog
    from src.utils.ui import tui

    LINES = ["LINE-ZERO alpha", "LINE-ONE bravo", "LINE-TWO charlie"]

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            for line in LINES:
                pane.write(line)
            await pilot.pause()

            def highlighted_rows():
                out = {}
                for r in range(len(LINES)):
                    rev = [s.text for s in pane.render_line(r)._segments
                           if getattr(s.style, "reverse", False)]
                    if rev:
                        out[r] = rev
                return out

            results = []
            for widget_y in (1, 2, 3):
                await pilot.click("#stream", offset=(5, widget_y))
                await pilot.pause()
                results.append(
                    (app.screen.get_selected_text(), highlighted_rows())
                )

            await pilot.click("#stream", offset=(5, 0))   # the border
            await pilot.pause()
            border_sel = app.screen.get_selected_text()
            return results, border_sel

    results, border_sel = asyncio.run(_run())

    for idx, (selected, rows) in enumerate(results):
        assert selected == LINES[idx], (
            f"click on content row {idx} selected {selected!r}, want {LINES[idx]!r}"
        )
        assert list(rows) == [idx], (
            f"expected only row {idx} highlighted, got rows {list(rows)} — a "
            f"whole-pane selection is exactly the bug this guards"
        )
        assert rows[idx] == [LINES[idx]]

    # Textual clears screen.selections on mouse-down; a click on the border
    # resolves to no content row, so nothing re-selects it. Net effect is a
    # deselect, which is the behaviour you want from clicking off a line.
    assert not border_sel, (
        f"clicking the border should deselect, got {border_sel!r}"
    )


# ------------------------------------------- character-level drag selection
#
# These drive the REAL event path: MouseDown / MouseMove / MouseUp are pushed
# through ``Screen._forward_event`` exactly as the driver does, so what is under
# test is Textual's own selection machinery reacting to our widget — not a
# selection we constructed ourselves. ``Pilot`` exposes no drag helper, hence
# ``_post_mouse_events``.

PAD = 2  # pane border (1) + horizontal padding (1)


def _drag(pilot, pane, start, end, to=None):
    """Sweep the pointer from ``start`` to ``end`` in widget coordinates."""
    from textual.events import MouseDown, MouseMove, MouseUp

    async def _go():
        await pilot._post_mouse_events([MouseDown], pane, offset=start)
        await pilot._post_mouse_events([MouseMove], to or pane, offset=end)
        await pilot._post_mouse_events([MouseUp], to or pane, offset=end)
        await pilot.pause()

    return _go()


def _marked(pane, rows):
    """{row: highlighted text} read off the rendered strips.

    Matches against the pane's OWN selection style rather than "has a
    background": on the real dashboard theme every segment carries the pane's
    background, so the looser test reports the whole pane as highlighted no
    matter what is selected.
    """
    want = pane._selection_style()

    def is_marked(style):
        if style is None:
            return False
        if want.reverse:
            return bool(style.reverse)
        return want.bgcolor is not None and style.bgcolor == want.bgcolor

    out = {}
    for y in range(rows):
        hit = "".join(s.text for s in pane.render_line(y) if is_marked(s.style))
        if hit.strip():
            out[y] = hit
    return out


@pytestmark_textual
def test_pane_publishes_content_offsets_so_a_drag_can_address_characters():
    """The root cause, pinned directly.

    ``Compositor.get_widget_and_offset_at`` finds the character under the mouse
    by scanning the rendered line for a segment carrying ``meta["offset"]``.
    RichLog publishes none, so the lookup returns ``None`` — and a ``None``
    content offset is what makes ``Screen._watch__select_state`` abandon its
    per-character path and mark whole widgets ``SELECT_ALL`` instead. Stock
    RichLog must fail this; ours must not.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog
    from src.utils.ui.tui import CopyableRichLog

    class _App(App):
        CSS = "#stock, #ours { height: 6; border: round green; padding: 0 1; }"

        def compose(self) -> ComposeResult:
            yield RichLog(id="stock", highlight=False, markup=True, wrap=True)
            yield CopyableRichLog(id="ours", highlight=False, markup=True,
                                  wrap=True)

    async def _run():
        app = _App()
        async with app.run_test(size=(60, 16)) as pilot:
            await pilot.pause()
            out = {}
            for wid, cls in (("#stock", RichLog), ("#ours", CopyableRichLog)):
                pane = app.query_one(wid, cls)
                pane.write("ERROR sidecar OOMKilled exit 137")
                await pilot.pause()
                # content (6, 0) -> widget (PAD + 6, 1)
                widget, offset = app.screen.get_widget_and_offset_at(
                    pane.region.x + PAD + 6, pane.region.y + 1
                )
                out[wid] = (widget is pane, offset)
            return out

    out = asyncio.run(_run())
    assert out["#stock"][0] and out["#stock"][1] is None, (
        "stock RichLog unexpectedly resolves an offset — the premise of this "
        f"fix no longer holds: {out['#stock']!r}"
    )
    assert out["#ours"][0], "hit the wrong widget"
    assert tuple(out["#ours"][1]) == (6, 0), (
        f"expected character offset (6, 0), got {out['#ours'][1]!r}"
    )


@pytestmark_textual
def test_drag_selects_a_word_a_fragment_and_several_lines():
    """The acceptance criteria, measured on real drags over the real dashboard.

    Every case asserts BOTH halves: the text ctrl+c would copy, and the cells
    actually painted as highlighted. A whole-pane selection fails either way.
    """
    from textual.widgets import RichLog
    from src.utils.ui import tui

    LINES = ["ERROR sidecar OOMKilled exit 137",
             "INFO  bridge healthy in 1.8s",
             "WARN  retrying upstream call"]

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            for line in LINES:
                pane.write(line)
            await pilot.pause()
            app.screen.clear_selection()
            await pilot.pause()

            results = {}
            cases = {
                # one word: chars 6..13 of row 0
                "word": ((PAD + 6, 1), (PAD + 13, 1)),
                # part of a line: chars 6..12 of row 1
                "fragment": ((PAD + 6, 2), (PAD + 12, 2)),
                # several consecutive lines: row 0 char 6 -> row 2 char 5
                "multiline": ((PAD + 6, 1), (PAD + 5, 3)),
                # backwards, right-to-left and bottom-to-top
                "backwards": ((PAD + 10, 3), (PAD + 2, 1)),
            }
            for name, (a, b) in cases.items():
                await _drag(pilot, pane, a, b)
                results[name] = (app.screen.get_selected_text(),
                                 _marked(pane, len(LINES)))
            return results

    got = asyncio.run(_run())

    # The cell under the pointer is included, as in a terminal — hence the
    # trailing character on each span.
    text, marked = got["word"]
    assert text == "sidecar ", f"expected one word, got {text!r}"
    assert marked == {0: "sidecar "}, f"highlight spilled: {marked!r}"

    text, marked = got["fragment"]
    assert text == "bridge ", f"expected a fragment, got {text!r}"
    assert marked == {1: "bridge "}, f"highlight spilled: {marked!r}"

    text, marked = got["multiline"]
    assert text.split("\n") == ["sidecar OOMKilled exit 137",
                                "INFO  bridge healthy in 1.8s",
                                "WARN  "], f"bad multiline span: {text!r}"
    assert set(marked) == {0, 1, 2}
    assert marked[0].startswith("sidecar") and not marked[0].startswith("ERROR")
    assert marked[2] == "WARN  ", f"last row over-selected: {marked[2]!r}"

    # Bottom-up, right-to-left: normalised to row 0 char 2 -> row 2 char 10,
    # inclusive of the cell under the pointer at both ends.
    text, _ = got["backwards"]
    assert text.split("\n") == ["ROR sidecar OOMKilled exit 137",
                                "INFO  bridge healthy in 1.8s",
                                "WARN  retry"], (
        f"a bottom-up, right-to-left drag must normalise: {text!r}"
    )


@pytestmark_textual
def test_synthesised_click_after_a_drag_does_not_widen_the_selection():
    """Textual posts a ``Click`` after every mouse-up that lands on the widget
    the mouse went down on — a drag included (``App._forward_event``). Without
    the guard in ``on_click``, releasing the button after selecting one word
    would immediately widen it back out to the whole line."""
    from textual.events import Click
    from textual.pilot import _get_mouse_message_arguments
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("ERROR sidecar OOMKilled exit 137")
            await pilot.pause()

            await _drag(pilot, pane, (PAD + 6, 1), (PAD + 13, 1))
            after_drag = app.screen.get_selected_text()

            kwargs = _get_mouse_message_arguments(pane, (PAD + 13, 1), button=1)
            app.screen._forward_event(Click(**{**kwargs, "chain": 1}))
            await pilot.pause()
            return after_drag, app.screen.get_selected_text()

    after_drag, after_click = asyncio.run(_run())
    assert after_drag == "sidecar "
    assert after_click == after_drag, (
        f"the synthesised Click widened {after_drag!r} to {after_click!r}"
    )


@pytestmark_textual
def test_drag_offsets_track_the_document_when_the_pane_is_scrolled():
    """Panes auto-scroll as the harness streams, so the row the pointer is over
    is not the row in the backing store. Selection offsets must be in document
    space or a drag on a busy pane would copy some other line entirely."""
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            for i in range(60):
                pane.write(f"row{i:02d} alpha bravo")
            await pilot.pause()
            assert pane.scroll_offset.y > 0, "pane did not scroll; test is moot"
            await _drag(pilot, pane, (PAD + 0, 1), (PAD + 5, 1))
            # Widget y=1 is the first *content* row (y=0 is the border), so the
            # document row is the scroll offset itself.
            expected_row = pane.scroll_offset.y
            sel = app.screen.selections.get(pane)
            return expected_row, sel, app.screen.get_selected_text()

    expected_row, sel, text = asyncio.run(_run())
    assert sel.start.y == expected_row == sel.end.y, (
        f"selection row {sel!r} is not document row {expected_row}"
    )
    assert text == f"row{expected_row:02d} ", f"copied the wrong line: {text!r}"


@pytestmark_textual
def test_wide_glyphs_do_not_shift_the_selection():
    """Selections are addressed in characters, strips are cut in cells, and a CJK
    glyph is one character in two cells. Mixing the two would drift the
    highlight away from the copied text on any line containing one."""
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("警告 サイドカー OOM 137")
            await pilot.pause()
            # "警告" is 2 chars / 4 cells, then a space: "サイドカー" starts at
            # character 3 but cell 5.
            await _drag(pilot, pane, (PAD + 5, 1), (PAD + 14, 1))
            return app.screen.get_selected_text(), _marked(pane, 1)

    text, marked = asyncio.run(_run())
    assert text.strip() == "サイドカー", f"copied {text!r}"
    assert marked.get(0, "").strip() == "サイドカー", (
        f"highlight drifted off the copied text: {marked!r}"
    )


@pytestmark_textual
def test_whole_pane_selection_only_via_the_explicit_action():
    """Ctrl+Shift+A is the sanctioned way to get a pane-wide selection, and the
    only one: it must yield every line, and ctrl+c must then copy all of them.

    ctrl+shift+a rather than ctrl+a because Input binds ctrl+a to "home" — the
    priority binding would otherwise take cursor movement off the HITL prompt.
    """
    from textual.widgets import Input, RichLog
    from src.utils.ui import tui

    LINES = ["first stream line", "second stream line", "third stream line"]

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            for line in LINES:
                pane.write(line)
            await pilot.pause()
            # Pointer over the stream pane, as it would be when you hit the key.
            app.mouse_position = (pane.region.x + 3, pane.region.y + 2)
            assert app.pane_under_mouse() is pane
            await pilot.press("ctrl+shift+a")
            await pilot.pause()
            return app.screen.get_selected_text(), sorted(
                str(getattr(b, "key", b)) for b in tui.HarnessDashboard.BINDINGS
            )

    text, keys = asyncio.run(_run())
    assert text.split("\n") == LINES, f"select-all missed lines: {text!r}"
    assert "ctrl+shift+a" in keys
    assert "ctrl+a" not in keys, (
        "ctrl+a would shadow Input's cursor-to-start binding"
    )
    assert "ctrl+a" in [b.key for b in Input.BINDINGS
                        if "ctrl+a" in str(getattr(b, "key", ""))][0], (
        "premise check: Input still binds ctrl+a"
    )


@pytestmark_textual
def test_cross_pane_drag_takes_partial_spans_not_whole_panes():
    """Dragging out of one pane into the next must not select both in full.

    Textual gives the first pane "from here to its end" and the second "from its
    start to here", which is what a terminal does across a split. Only a pane
    entirely swallowed by the sweep is taken whole.
    """
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            stream = app.query_one("#stream", RichLog)
            chat = app.query_one("#chat", RichLog)
            for i in range(4):
                stream.write(f"stream line {i}")
                chat.write(f"chat line {i}")
            await pilot.pause()
            app.screen.clear_selection()
            await pilot.pause()
            await _drag(pilot, stream, (PAD + 7, 2), (PAD + 4, 1), to=chat)
            return ({w.id: s for w, s in app.screen.selections.items()},
                    app.screen.get_selected_text())

    sels, text = asyncio.run(_run())
    assert set(sels) == {"stream", "chat"}, f"unexpected panes: {sels!r}"
    for pid, sel in sels.items():
        assert sel != (None, None) and (sel.start is None) != (sel.end is None), (
            f"{pid} was selected whole instead of partially: {sel!r}"
        )
    assert "stream line" in text, f"the origin pane contributed nothing: {text!r}"
    assert text.rstrip().endswith("chat"), (
        f"the drag should stop where it ended inside the second pane: {text!r}"
    )
    assert not text.startswith("stream line 0"), (
        f"the first pane should start at the drag origin, not its top: {text!r}"
    )


@pytestmark_textual
def test_status_pane_is_selectable_too():
    """The lifecycle table is a Static holding a Rich table — the same
    strips-with-no-offsets gap as RichLog, so it was equally unselectable. An
    operator copying a task id out of it must get the same behaviour as in the
    log panes: a drag yields the characters swept, nothing more."""
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            for i in range(3):
                app._stages[f"task-{i}"] = {
                    "glyph": "●", "style": "green", "label": f"stage-{i}"}
            status = app.query_one("#status")
            status.update(app._render_status())
            await pilot.pause()

            rows = status._select_rows()
            row = next(i for i, r in enumerate(rows) if "task-1" in r)
            col = rows[row].index("task-1")
            widget, offset = app.screen.get_widget_and_offset_at(
                status.region.x + PAD + col, status.region.y + 1 + row)
            resolved = (widget is status, offset)

            await _drag(pilot, status, (PAD + col, 1 + row),
                        (PAD + col + 5, 1 + row))
            return resolved, app.screen.get_selected_text()

    (hit, offset), text = asyncio.run(_run())
    assert hit and offset is not None, (
        f"status pane still reports no content offset: {offset!r}"
    )
    assert text == "task-1", f"expected the task id alone, got {text!r}"


@pytestmark_textual
def test_status_rows_are_memoised_but_never_stale():
    """``_select_rows`` renders the pane to answer for one row, so a repaint
    would be O(rows²) — 5.7 ms at 75 rows, measured, against a ~16 ms frame.
    The cache must survive repeated repaints and die on ``update()``, which is
    the only way the lifecycle table changes."""
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            status = app.query_one("#status")
            app._stages["old-task"] = {
                "glyph": "●", "style": "green", "label": "running"}
            status.update(app._render_status())
            await pilot.pause()

            before = status._select_rows()
            cached = status._select_rows() is status._select_rows()

            app._stages.clear()
            app._stages["new-task"] = {
                "glyph": "●", "style": "red", "label": "failed"}
            status.update(app._render_status())
            await pilot.pause()
            after = status._select_rows()
            return cached, "\n".join(before), "\n".join(after)

    cached, before, after = asyncio.run(_run())
    assert cached, "rows are re-rendered on every call; the memo is not working"
    assert "old-task" in before and "new-task" not in before
    assert "new-task" in after, "update() did not invalidate the row cache"
    assert "old-task" not in after, (
        "stale rows survived update() — a drag would copy text no longer shown"
    )


@pytestmark_textual
def test_both_ctrl_c_and_cmd_c_copy_the_selection():
    """Cmd+C (``super+c``) alongside Ctrl+C, because Cmd is the copy key people
    reach for on macOS. It is an addition, not a replacement: whether a terminal
    forwards Cmd+C rather than handling it itself varies (iTerm2 and Ghostty
    can, Terminal.app does not), so ctrl+c must keep working everywhere."""
    from textual.widgets import RichLog
    from src.utils.ui import tui

    LINE = "ERROR sidecar OOMKilled exit 137"

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write(LINE)
            await pilot.pause()

            copied = {}
            for key in ("ctrl+c", "super+c"):
                app.last_copied = ""
                await _drag(pilot, pane, (PAD + 6, 1), (PAD + 13, 1))
                await pilot.press(key)
                await pilot.pause()
                copied[key] = app.last_copied
            return copied

    copied = asyncio.run(_run())
    assert copied["ctrl+c"] == "sidecar ", f"ctrl+c copied {copied['ctrl+c']!r}"
    assert copied["super+c"] == "sidecar ", f"cmd+c copied {copied['super+c']!r}"


@pytestmark_textual
def test_copy_actions_do_not_touch_the_real_clipboard_when_suppressed():
    """The guard the fixture above relies on. If this regresses, every test run
    silently overwrites the developer's clipboard."""
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            return app._copy_to_clipboard("sentinel text"), app.last_copied

    mechanism, recorded = asyncio.run(_run())
    assert mechanism == "suppressed", (
        f"clipboard write was NOT suppressed (used {mechanism!r})"
    )
    assert recorded == "sentinel text", "copy was not recorded for assertions"


@pytestmark_textual
def test_a_second_drag_is_accurate_while_a_selection_is_still_painted():
    """Offsets must be published AFTER the highlight is painted, not before.

    Painting splits a segment in three with ``Strip.divide``, and every piece
    inherits the original segment's style — offset meta included. Publish first
    and all three pieces claim to start at the same character, so the moment a
    pane holds a selection, the next drag inside it resolves to the wrong
    column. Only a *second* drag exposes it, which is why it survived the
    single-drag tests above.
    """
    from textual.widgets import RichLog
    from src.utils.ui import tui

    async def _run():
        app = tui.HarnessDashboard(lambda: None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stream", RichLog)
            pane.write("ERROR sidecar OOMKilled exit 137")
            await pilot.pause()

            await _drag(pilot, pane, (PAD + 6, 1), (PAD + 13, 1))
            first = app.screen.get_selected_text()
            # Same sweep again, with the first selection still on screen.
            resolved = app.screen.get_widget_and_offset_at(
                pane.region.x + PAD + 6, pane.region.y + 1)[1]
            await _drag(pilot, pane, (PAD + 6, 1), (PAD + 13, 1))
            return first, resolved, app.screen.get_selected_text()

    first, resolved, second = asyncio.run(_run())
    assert first == "sidecar "
    assert tuple(resolved) == (6, 0), (
        f"a painted row reports the wrong character offset: {resolved!r}"
    )
    assert second == first, (
        f"repeating the same drag gave {second!r} instead of {first!r}"
    )
