"""Tests for the dashboard's Data Injection pane (EV_INJECT → RichLog#inject).

Covers three layers:
  * ``inject_markup`` (pure, no textual needed) — record → Rich markup, across
    the inject/stage/drift director record shapes, plus value rendering and
    markup escaping of injected task content;
  * ``lifecycle.emit_inject`` — the director→bus bridge: publishes EV_INJECT
    with a truncated, display-only copy of injected values and never raises;
  * a headless end-to-end of the REAL dashboard — bus emit from a worker thread
    lands in #inject and never leaks to #log — using Textual's run_test pilot
    (skipped cleanly when textual is absent).
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.ui.tui import inject_markup, textual_available  # noqa: E402
from src.utils.ui import lifecycle  # noqa: E402
from src.utils.ui.events import EV_INJECT, EV_LOG, get_bus  # noqa: E402


# ------------------------------------------------------------- inject_markup

def test_inject_markup_seed_and_counts():
    got = inject_markup({"record": {"type": "inject.seed.start", "stage": "seed",
                                    "fs": 1, "loud": 0}})
    assert "inject.seed.start" in got
    assert "seed" in got and "fs=1" in got and "loud=0" in got


def test_inject_markup_api_with_values():
    got = inject_markup({
        "record": {"type": "inject.api", "stage": "s1", "turn_index": 2,
                   "api": "classroom-api", "table": "grades", "pk": "stu_7",
                   "silent": True, "status": "applied"},
        "values": {"score": "42", "updated_at": "2026-05-01"},
    })
    assert "classroom-api" in got and "grades" in got and "pk=stu_7" in got
    assert "@turn 2" in got
    # injected values rendered as indented lines
    assert "score:" in got and "42" in got
    assert "updated_at:" in got and "2026-05-01" in got
    # status tag present; 'applied' styled green
    assert "applied" in got


def test_inject_markup_silent_flag_not_rendered_as_count():
    """`silent` on inject.api is a bool flag (status tag), not a volume count:
    bool is an int subclass, so the count loop must exclude it."""
    got = inject_markup({"record": {"type": "inject.api", "api": "a", "table": "t",
                                    "silent": True, "status": "applied"}})
    assert "silent=True" not in got          # not rendered as a count
    assert "· silent" in got                 # rendered in the status tag


def test_inject_markup_stage_director_shape():
    got = inject_markup({"record": {"type": "stage.applied", "stage_id": "s1",
                                    "applied_before_turn": 2, "ops": 3}})
    assert "stage.applied" in got and "s1" in got
    assert "@turn 2" in got and "ops=3" in got


def test_inject_markup_drift_director_shape():
    got = inject_markup({"record": {"type": "event.fired", "api": "gmail-api",
                                    "action_keys": ["inject", "table"], "status": "ok"}})
    assert "event.fired" in got and "gmail-api" in got
    assert "keys=inject,table" in got


def test_inject_markup_escapes_task_content():
    """Injected task content (email bodies, paths, values) must never open live
    Rich markup in the pane."""
    got = inject_markup({
        "record": {"type": "inject.fs", "path": "[bold red]evil"},
        "values": {"body": "[/]pwned"},
    })
    assert "\\[bold red]" in got            # path neutralized
    assert "\\[/]pwned" in got              # value neutralized
    assert "[bold red]evil" not in got.replace("\\[bold red]", "")


def test_inject_markup_unknown_shape_still_shows_type():
    got = inject_markup({"record": {"type": "some.new.event", "weird": 1}})
    assert "some.new.event" in got  # nothing silently dropped


def test_inject_markup_empty_payload():
    assert "inject" in inject_markup({})  # default type, no crash


# --------------------------------------------------------- emit_inject bridge

def test_emit_inject_publishes_event_and_truncates_values():
    seen = []
    unsub = get_bus().subscribe(lambda e: seen.append(e))
    try:
        big = "x" * 500
        lifecycle.emit_inject("task-9", {"type": "inject.api", "table": "grades"},
                              {"score": 42, "note": big})
    finally:
        unsub()
    assert len(seen) == 1
    evt = seen[0]
    assert evt.kind == EV_INJECT
    assert evt.payload["task_id"] == "task-9"
    vals = evt.payload["values"]
    assert vals["score"] == "42"                       # stringified
    assert vals["note"].endswith("…")                  # truncated
    assert len(vals["note"]) <= lifecycle._INJECT_VALUE_MAXLEN + 1


def test_emit_inject_no_values_and_blank_task_id():
    seen = []
    unsub = get_bus().subscribe(lambda e: seen.append(e))
    try:
        lifecycle.emit_inject("", {"type": "inject.seed.done", "stage": "seed"})
    finally:
        unsub()
    assert seen[0].payload["task_id"] == "?"           # blank id normalized
    assert seen[0].payload["values"] is None


def test_emit_inject_caps_value_count():
    many = {f"f{i}": i for i in range(50)}
    out = lifecycle._truncate_inject_values(many)
    assert len(out) <= lifecycle._INJECT_VALUE_MAXCOUNT + 1  # +1 for the "…" marker
    assert "…" in out


def test_emit_inject_is_fail_open(monkeypatch):
    """A broken bus must never propagate into a director's write path."""
    class _Boom:
        def emit(self, *a, **k):
            raise RuntimeError("bus down")
    monkeypatch.setattr(lifecycle, "get_bus", lambda: _Boom())
    # Must not raise.
    lifecycle.emit_inject("t", {"type": "inject.api"}, {"k": "v"})


# ------------------------------------------------- headless dashboard e2e

@pytest.mark.skipif(not textual_available(), reason="textual not installed")
def test_inject_pane_receives_events_headless():
    """Real HarnessDashboard, headless: EV_INJECT emitted from a WORKER thread
    lands in the #inject RichLog and never leaks into #log."""
    from textual.widgets import RichLog
    from src.utils.ui.tui import HarnessDashboard

    async def scenario():
        done = threading.Event()

        def work():
            done.wait(timeout=5.0)

        app = HarnessDashboard(work)
        async with app.run_test() as pilot:
            await pilot.pause()
            inject = app.query_one("#inject", RichLog)
            log = app.query_one("#log", RichLog)
            log_before = len(log.lines)
            assert len(inject.lines) == 0  # pane starts empty

            def emit_from_worker():
                lifecycle.emit_inject("t1", {"type": "inject.seed.start",
                                             "stage": "seed", "fs": 1})
                lifecycle.emit_inject(
                    "t1",
                    {"type": "inject.api", "api": "classroom-api", "table": "grades",
                     "pk": "stu_7", "silent": True, "status": "applied"},
                    {"score": "42"},
                )

            t = threading.Thread(target=emit_from_worker, daemon=True)
            t.start()
            for _ in range(40):
                await pilot.pause(0.05)
                if len(inject.lines) >= 2:
                    break
            t.join(timeout=2.0)
            assert not t.is_alive()
            pane_text = "\n".join(strip.text for strip in inject.lines)
            assert "inject.seed.start" in pane_text
            assert "classroom-api" in pane_text and "42" in pane_text
            assert len(log.lines) == log_before  # inject events never leak to #log

            done.set()
            for _ in range(100):
                await pilot.pause(0.05)
                if app._done:
                    break
            assert app._done

    asyncio.run(scenario())
