"""Tests for the interactive-Mode-2 InputBridge (worker↔UI input channel).

The bridge is a one-slot request/response: the harness worker thread blocks in
``request()`` (emitting ``EV_INPUT_REQUEST`` on the shared bus) until the UI
thread ``submit()``s a line, or ``close()`` delivers EOF. These tests drive it
directly with threads — no textual needed. The production ordering (the UI only
submits AFTER seeing ``EV_INPUT_REQUEST``) is honoured so the tests exercise the
same path the dashboard does.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.ui import input_bridge as ib  # noqa: E402
from src.utils.ui.events import EV_INPUT_REQUEST, get_bus  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_bridge():
    ib.reset()
    yield
    ib.reset()


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_request_blocks_until_submit():
    bridge = ib.get_input_bridge()
    got = {}

    t = threading.Thread(target=lambda: got.__setitem__("v", bridge.request("p")),
                         daemon=True)
    t.start()
    time.sleep(0.05)
    assert t.is_alive() and "v" not in got     # blocked; nothing submitted yet
    bridge.submit("hello from human")
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert got["v"] == "hello from human"


def test_request_emits_input_request_event():
    bridge = ib.get_input_bridge()
    seen = []
    unsub = get_bus().subscribe(
        lambda e: seen.append(e.payload.get("prompt"))
        if e.kind == EV_INPUT_REQUEST else None)
    try:
        t = threading.Thread(target=lambda: bridge.request("prompt-X"), daemon=True)
        t.start()
        assert _wait(lambda: bool(seen))
        bridge.submit("ok")
        t.join(timeout=2.0)
    finally:
        unsub()
    assert seen == ["prompt-X"]


def test_empty_submit_returns_empty_string():
    bridge = ib.get_input_bridge()
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__("v", bridge.request("p")),
                         daemon=True)
    t.start()
    time.sleep(0.05)
    bridge.submit("")                          # a bare Enter
    t.join(timeout=2.0)
    assert out["v"] == ""                       # HumanTurnSource maps this to accept


def test_close_unblocks_pending_request_with_eof():
    bridge = ib.get_input_bridge()
    err = {}

    def worker():
        try:
            bridge.request("p")
        except EOFError:
            err["eof"] = True

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.05)
    bridge.close()
    t.join(timeout=2.0)
    assert not t.is_alive() and err.get("eof")


def test_request_after_close_raises_immediately():
    bridge = ib.get_input_bridge()
    bridge.close()
    with pytest.raises(EOFError):
        bridge.request("p")


def test_submit_after_close_is_noop():
    bridge = ib.get_input_bridge()
    bridge.close()
    bridge.submit("ignored")                    # must not raise
    with pytest.raises(EOFError):
        bridge.request("p")


def test_close_is_idempotent():
    bridge = ib.get_input_bridge()
    bridge.close()
    bridge.close()
    assert bridge.is_closed()


def test_sequential_turns_roundtrip():
    """Three turns, production-shaped: submit only after each request event."""
    bridge = ib.get_input_bridge()
    results, requests = [], []
    unsub = get_bus().subscribe(
        lambda e: requests.append(e.payload.get("prompt"))
        if e.kind == EV_INPUT_REQUEST else None)

    def worker():
        for i in range(3):
            results.append(bridge.request(f"p{i}"))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        for i, msg in enumerate(("m0", "m1", "m2")):
            assert _wait(lambda: len(requests) >= i + 1)   # worker parked in get()
            bridge.submit(msg)
            assert _wait(lambda: len(results) >= i + 1)
        t.join(timeout=2.0)
    finally:
        unsub()
    assert results == ["m0", "m1", "m2"]
    assert requests == ["p0", "p1", "p2"]
