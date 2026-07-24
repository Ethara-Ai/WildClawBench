"""Tests for the dashboard's HITL input bar + Conversation pane.

Covers: ``chat_markup`` (pure, no textual needed), and a headless end-to-end of
the REAL dashboard — a worker thread blocked in ``InputBridge.request()`` emits
``EV_INPUT_REQUEST`` → the #prompt Input is revealed + focused → typing + Enter
delivers the value back to the worker and echoes it to #chat. Mirrors the
threading discipline of tests/test_tui_stream_pane.py. The headless tests skip
cleanly when textual is not installed.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.ui.tui import chat_markup, textual_available  # noqa: E402
from src.utils.ui.events import EV_CHAT, get_bus  # noqa: E402


# --------------------------------------------------------------- chat_markup

def test_chat_markup_role_tints():
    assert chat_markup({"text": "── agent (turn 0) ──"}).startswith("[cyan]")
    assert chat_markup({"text": "── scripted turn 1 ──"}).startswith("[yellow]")
    assert chat_markup({"text": "you › hello"}).startswith("[green]")
    assert chat_markup({"text": "(empty — type a message)"}).startswith("[dim italic]")
    assert chat_markup({"text": "plain agent reply"}) == "plain agent reply"


def test_chat_markup_escapes_content():
    """Model/user text must never inject live Rich markup into the pane."""
    out = chat_markup({"text": "[bold red]pwned[/]"})
    assert "\\[bold red]" in out and not out.startswith("[bold red]")


def test_chat_markup_defaults_and_junk():
    assert chat_markup({}) == ""
    assert "42" in chat_markup({"text": 42})     # non-str tolerated


# ------------------------------------------------- headless dashboard e2e

@pytest.mark.skipif(not textual_available(), reason="textual not installed")
def test_input_request_reveals_prompt_and_delivers_value_headless():
    from textual.widgets import Input, RichLog
    from src.utils.ui.tui import HarnessDashboard
    from src.utils.ui import input_bridge as ib

    ib.reset()
    bridge = ib.get_input_bridge()

    async def scenario():
        done = threading.Event()
        result: dict = {}

        def work():
            # Worker blocks here (agent-idle turn boundary) until the UI submits;
            # request() emits EV_INPUT_REQUEST which the dashboard marshals.
            try:
                result["value"] = bridge.request("── you (Enter=send) ── ")
            except Exception as exc:  # pragma: no cover - surfaced via assert
                result["error"] = repr(exc)
            done.wait(timeout=5.0)

        app = HarnessDashboard(work)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            chat = app.query_one("#chat", RichLog)
            assert len(chat.lines) == 0            # Conversation pane starts empty

            # The worker emits EV_INPUT_REQUEST as soon as it runs; wait for the
            # input bar to be revealed + focused.
            for _ in range(60):
                await pilot.pause(0.05)
                if prompt.display and app.focused is prompt:
                    break
            assert prompt.display                  # revealed by EV_INPUT_REQUEST
            assert app.focused is prompt           # and focused so typing lands

            for ch in "hi there":
                await pilot.press("space" if ch == " " else ch)
            await pilot.press("enter")

            for _ in range(60):
                await pilot.pause(0.05)
                if "value" in result:
                    break
            assert result.get("error") is None
            assert result.get("value") == "hi there"   # delivered to the worker
            assert not prompt.display                   # hidden again after submit
            chat_text = "\n".join(s.text for s in chat.lines)
            assert "you › hi there" in chat_text        # echoed to Conversation

            done.set()
            for _ in range(100):
                await pilot.pause(0.05)
                if app._done:
                    break
            assert app._done

    asyncio.run(scenario())
    ib.reset()


@pytest.mark.skipif(not textual_available(), reason="textual not installed")
def test_empty_submit_returns_empty_and_chat_not_log_headless():
    """Bare Enter returns '' (→ accept scripted); EV_CHAT lands in #chat only."""
    from textual.widgets import Input, RichLog
    from src.utils.ui.tui import HarnessDashboard
    from src.utils.ui import input_bridge as ib

    ib.reset()
    bridge = ib.get_input_bridge()

    async def scenario():
        done = threading.Event()
        result: dict = {}

        def work():
            try:
                result["value"] = bridge.request("── you ── ")
            finally:
                done.wait(timeout=5.0)

        app = HarnessDashboard(work)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt", Input)
            chat = app.query_one("#chat", RichLog)
            log = app.query_one("#log", RichLog)
            log_before = len(log.lines)

            # An EV_CHAT emitted from a worker thread must reach #chat, not #log.
            def emit_chat():
                get_bus().emit(EV_CHAT, text="── scripted turn 0 ──")
            threading.Thread(target=emit_chat, daemon=True).start()

            for _ in range(60):
                await pilot.pause(0.05)
                if prompt.display:
                    break
            assert prompt.display
            await pilot.press("enter")             # bare Enter, empty value

            for _ in range(60):
                await pilot.pause(0.05)
                if "value" in result:
                    break
            assert result.get("value") == ""        # empty → accept scripted
            chat_text = "\n".join(s.text for s in chat.lines)
            assert "scripted turn 0" in chat_text
            assert len(log.lines) == log_before     # chat never leaks into #log

            done.set()
            for _ in range(100):
                await pilot.pause(0.05)
                if app._done:
                    break
            assert app._done

    asyncio.run(scenario())
    ib.reset()
