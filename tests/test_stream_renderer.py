"""Unit tests for src/utils/stream_renderer.py (display-only consumer).

Focus: lifecycle safety (R3 bounded stop; gate default-off) and the
line-buffered judge rendering / torn-line tolerance. Full visual behavior is
covered by the manual E2E matrix in docs/STREAMING_PLAN.md §8.2.
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.stream_renderer import StreamRenderer, start_renderer  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("WCB_STREAM", raising=False)
    monkeypatch.delenv("WCB_STREAM_THINKING", raising=False)
    yield


def test_start_renderer_gate_default_off(tmp_path):
    assert start_renderer(tmp_path / "s.jsonl", tmp_path / "a.log") is None


def test_start_renderer_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_STREAM", "1")
    r = start_renderer(tmp_path / "missing" / "s.jsonl", None, run_label="t/run_1")
    try:
        assert r is not None
    finally:
        if r is not None:
            r.stop(timeout=1.0)


def test_stop_is_bounded_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_STREAM", "1")
    feed = tmp_path / "stream.jsonl"
    feed.touch()
    r = start_renderer(feed, tmp_path / "agent.log")
    assert r is not None
    t0 = time.time()
    r.stop(timeout=2.0)
    assert time.time() - t0 < 3.0  # bounded join (R3)
    assert not r.is_alive()
    r.stop(timeout=1.0)  # second stop: no raise


def _fake_out_renderer() -> tuple[StreamRenderer, io.StringIO]:
    r = StreamRenderer(None, None)
    out = io.StringIO()
    r._out = out
    r._interactive = True
    r._color = False
    return r, out


def test_judge_rendering_is_line_buffered():
    r, out = _fake_out_renderer()
    r._render_event({"source": "judge:kimi", "event": "delta", "kind": "text",
                     "delta": "1. Criterion", "request_id": "j1"})
    assert "Criterion" not in out.getvalue()  # incomplete line held back
    r._render_event({"source": "judge:kimi", "event": "delta", "kind": "text",
                     "delta": " [[SATISFIED: Yes]]\n2. Next", "request_id": "j1"})
    assert "[judge:kimi] 1. Criterion [[SATISFIED: Yes]]" in out.getvalue()
    assert "2. Next" not in out.getvalue()
    r._render_event({"source": "judge:kimi", "event": "message_stop", "kind": "status",
                     "delta": "", "request_id": "j1"})
    assert "2. Next" in out.getvalue()  # flushed on stop


def test_agent_main_session_and_subagent_split():
    r, out = _fake_out_renderer()
    r._render_event({"source": "agent", "event": "message_start", "kind": "status",
                     "delta": "", "request_id": "main-1"})
    r._render_event({"source": "agent", "event": "message_start", "kind": "status",
                     "delta": "", "request_id": "sub-2"})
    r._render_event({"source": "agent", "event": "delta", "kind": "text",
                     "delta": "main tokens", "request_id": "main-1"})
    r._render_event({"source": "agent", "event": "delta", "kind": "text",
                     "delta": "SUB TOKENS", "request_id": "sub-2"})
    v = out.getvalue()
    assert "main tokens" in v
    assert "SUB TOKENS" not in v          # sub-agent deltas: status lines only (D5)
    assert "[sub-agent sub-2" in v


def test_thinking_hidden_when_disabled(monkeypatch):
    monkeypatch.setenv("WCB_STREAM_THINKING", "0")
    r, out = _fake_out_renderer()
    r._render_event({"source": "agent", "event": "message_start", "kind": "status",
                     "delta": "", "request_id": "m"})
    r._render_event({"source": "agent", "event": "delta", "kind": "thinking",
                     "delta": "secret reasoning", "request_id": "m"})
    assert "secret reasoning" not in out.getvalue()


def test_token_mode_skips_torn_lines(monkeypatch, tmp_path):
    """A torn/partial JSONL line must be skipped, never crash the thread."""
    monkeypatch.setenv("WCB_STREAM", "1")
    feed = tmp_path / "stream.jsonl"
    feed.write_text("")  # exists, renderer starts at EOF
    r = start_renderer(feed, None)
    assert r is not None
    try:
        with open(feed, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "source": "judge:glm", "event": "delta", "kind": "text",
                "delta": "ok\n", "request_id": "j",
            }) + "\n")
            fh.write('{"source": "agent", "event": "de')  # torn line, no newline
        time.sleep(0.6)  # a couple of poll cycles
        assert r.is_alive()  # torn line did not kill the thread
    finally:
        r.stop(timeout=2.0)
        assert not r.is_alive()
