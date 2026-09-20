"""Tests for the cc-bridge live-stream tee (src/utils/claude_oauth/stream_tee.py)
and its integration into bridge._stream_buffered_with_retry.

Invariants (docs/STREAMING_PLAN.md):
  R5  observe-only — the client-visible bytes are IDENTICAL with the tee
      enabled, disabled, and broken (buffer-and-retry semantics untouched)
  R2  fail-open — a broken feed path never raises out of any tee method
  R6  inert without WCB_CC_STREAM_LOG_PATH (the default)
  §3.2 retry semantics — a mid-stream drop emits error("retrying") and the
      re-issued attempt re-opens the request with a fresh message_start
  frame parsing — an SSE `data:` line split across two chunks still parses
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture()
def tee_mod(monkeypatch, tmp_path):
    """stream_tee with the feed pointed at tmp (reload resets module caps)."""
    feed = tmp_path / "stream.jsonl"
    monkeypatch.setenv("WCB_CC_STREAM_LOG_PATH", str(feed))
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    mod._TEST_FEED = feed
    return mod


def _rows(feed: Path) -> list[dict]:
    if not feed.exists():
        return []
    return [json.loads(ln) for ln in feed.read_text().splitlines() if ln.strip()]


def _frame(event: str, data: dict) -> bytes:
    return (f"event: {event}\n" + "data: " + json.dumps(data) + "\n\n").encode()


F_START = _frame("message_start", {"type": "message_start"})
F_HEL = _frame("content_block_delta",
               {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}})
F_LO = _frame("content_block_delta",
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}})
F_THINK = _frame("content_block_delta",
                 {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hm"}})
F_STOP = _frame("message_stop", {"type": "message_stop"})


# ------------------------------------------------------------------ unit: tee

def test_inert_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WCB_CC_STREAM_LOG_PATH", raising=False)
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    tee = mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_START + F_HEL)
    tee.finish()
    tee.error("x")
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere


def test_frames_parse_to_rows(tee_mod):
    tee = tee_mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_START + F_HEL + F_THINK + F_LO + F_STOP)
    rows = _rows(tee_mod._TEST_FEED)
    assert [(r["event"], r["kind"], r["delta"]) for r in rows] == [
        ("message_start", "status", ""),
        ("delta", "text", "Hel"),
        ("delta", "thinking", "hm"),
        ("delta", "text", "lo"),
        ("message_stop", "status", ""),
    ]
    assert all(r["source"] == "agent" for r in rows)
    assert [r["seq"] for r in rows] == list(range(len(rows)))


def test_data_line_split_across_chunks(tee_mod):
    tee = tee_mod.StreamTee()
    tee.attempt_started()
    blob = F_HEL
    tee.feed(blob[:17])   # split mid data: line
    assert [r for r in _rows(tee_mod._TEST_FEED) if r["event"] == "delta"] == []
    tee.feed(blob[17:])   # completes the frame
    deltas = [r for r in _rows(tee_mod._TEST_FEED) if r["event"] == "delta"]
    assert deltas and deltas[0]["delta"] == "Hel"


def test_retrying_resets_and_reopens(tee_mod):
    tee = tee_mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_START + F_HEL)          # partial turn
    tee.retrying(1)
    tee.attempt_started()              # re-issued attempt
    tee.feed(F_START + F_LO + F_STOP)
    tee.finish()
    events = [r["event"] for r in _rows(tee_mod._TEST_FEED)]
    assert events == ["message_start", "delta", "error",     # partial + retry marker
                      "message_start", "delta", "message_stop"]


def test_finish_idempotent_and_stop_not_duplicated(tee_mod):
    tee = tee_mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_STOP)
    tee.finish()
    tee.finish()
    stops = [r for r in _rows(tee_mod._TEST_FEED) if r["event"] == "message_stop"]
    assert len(stops) == 1


def test_fail_open_unwritable_path(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_CC_STREAM_LOG_PATH", str(tmp_path / "no_dir" / "s.jsonl"))
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    tee = mod.StreamTee()
    tee.attempt_started()  # must not raise
    tee.feed(F_HEL)
    tee.finish()


# --------------------------------------- integration: buffered path, fake upstream

class _FakeUpstream:
    def __init__(self, chunk_lists):
        # chunk_lists: one list of chunks per attempt; a chunk of Exception
        # type is raised mid-stream (simulates upstream drop).
        self._attempts = list(chunk_lists)
        self.status_code = 200
        self.headers = {}

    def next_chunks(self):
        return self._attempts.pop(0)


def _install_fake_httpx(monkeypatch, bridge, upstream: _FakeUpstream):
    class _FakeStreamCM:
        def __init__(self, chunks):
            self._chunks = chunks

        async def __aenter__(self):
            resp = upstream

            async def aiter_bytes():
                for c in self._chunks:
                    if isinstance(c, Exception):
                        raise c
                    yield c
            resp.aiter_bytes = aiter_bytes
            return resp

        async def __aexit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def stream(self, *a, **k):
            return _FakeStreamCM(upstream.next_chunks())

        async def aclose(self):
            pass

    monkeypatch.setattr(bridge.httpx, "AsyncClient", _FakeClient)


class _FakeProvider:
    def get_access_token(self):
        return "sk-ant-oat01-test"


async def _client_bytes(bridge):
    resp = await bridge._stream_buffered_with_retry(
        _FakeProvider(), "POST", "http://upstream/v1/messages", b"{}", {}, {},
    )
    out = b""
    async for chunk in resp.body_iterator:
        out += chunk
    return out


@pytest.fixture()
def bridge_mod(monkeypatch, tmp_path):
    feed = tmp_path / "stream.jsonl"
    monkeypatch.setenv("WCB_CC_STREAM_LOG_PATH", str(feed))
    import src.utils.claude_oauth.stream_tee as tee_mod
    importlib.reload(tee_mod)
    import src.utils.claude_oauth.bridge as bridge
    bridge = importlib.reload(bridge)
    bridge._TEST_FEED = feed
    return bridge


def test_buffered_client_bytes_identical_with_and_without_tee(monkeypatch, bridge_mod, tmp_path):
    full = [F_START, F_HEL, F_LO, F_STOP]
    _install_fake_httpx(monkeypatch, bridge_mod, _FakeUpstream([list(full)]))
    with_tee = asyncio.run(_client_bytes(bridge_mod))

    monkeypatch.delenv("WCB_CC_STREAM_LOG_PATH")
    _install_fake_httpx(monkeypatch, bridge_mod, _FakeUpstream([list(full)]))
    without_tee = asyncio.run(_client_bytes(bridge_mod))

    assert with_tee == without_tee == b"".join(full)  # R5: byte-identical
    rows = _rows(bridge_mod._TEST_FEED)
    assert [r["delta"] for r in rows if r["event"] == "delta"] == ["Hel", "lo"]


def test_buffered_retry_emits_error_then_fresh_start(monkeypatch, bridge_mod):
    drop = [F_START, F_HEL, ConnectionError("mid-stream drop")]
    full = [F_START, F_HEL, F_LO, F_STOP]
    _install_fake_httpx(monkeypatch, bridge_mod, _FakeUpstream([drop, list(full)]))
    body = asyncio.run(_client_bytes(bridge_mod))
    assert body == b"".join(full)  # client only ever sees the COMPLETE response
    events = [r["event"] for r in _rows(bridge_mod._TEST_FEED)]
    assert "error" in events  # retry marker for the partial turn
    # fresh message_start after the error, then a terminal stop
    assert events.index("error") < len(events) - 1
    post = events[events.index("error") + 1:]
    assert post[0] == "message_start" and post[-1] == "message_stop"


# ------------------------------------------ stall-guard liveness heartbeat
#
# The guard's other signal is COMPLETION-time usage rows, so a healthy long
# turn is indistinguishable from a wedge (cite: alpha 2026-09-19 — 3 healthy
# 1P reps killed at 600s+poll; koji's row landed 3min post-kill). On the OAuth
# path this tee is the ONLY place chunks exist in real time, so the heartbeat
# has to be written here — and it must NOT ride the display gate.


@pytest.fixture()
def hb_mod(monkeypatch, tmp_path):
    """stream_tee with the heartbeat dir mounted and the DISPLAY FEED OFF.

    That combination is the production default (the batch only sets
    WCB_CC_STREAM_LOG_PATH under --stream), so it is the configuration the
    heartbeat has to work in."""
    monkeypatch.delenv("WCB_CC_STREAM_LOG_PATH", raising=False)
    hb_dir = tmp_path / "heartbeats"
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    mod._TEST_HB_DIR = hb_dir
    return mod


def _lane(mod) -> Path:
    return Path(mod._TEST_HB_DIR) / mod.HEARTBEAT_LANE_BRIDGE


def test_heartbeat_beats_without_the_display_feed(hb_mod):
    tee = hb_mod.StreamTee()
    assert _lane(hb_mod).exists(), "request start must beat before any chunk"
    assert tee._enabled is False, "display feed must stay off"


def test_chunk_advances_heartbeat_mtime(hb_mod):
    tee = hb_mod.StreamTee()
    tee.attempt_started()
    path = _lane(hb_mod)
    stale = path.stat().st_mtime - 100.0
    os.utime(path, (stale, stale))
    tee.feed(F_HEL)
    assert path.stat().st_mtime > stale


def test_heartbeat_keeps_ticking_across_an_internal_retry(hb_mod):
    tee = hb_mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_START + F_HEL)
    path = _lane(hb_mod)
    stale = path.stat().st_mtime - 100.0
    os.utime(path, (stale, stale))
    tee.retrying(1)
    assert path.stat().st_mtime > stale
    stale2 = path.stat().st_mtime - 100.0
    os.utime(path, (stale2, stale2))
    tee.attempt_started()  # reissued attempt re-arms the clock
    assert path.stat().st_mtime > stale2


def test_heartbeat_writes_nothing_into_the_display_sink(tee_mod, monkeypatch, tmp_path):
    """m0130: the heartbeat is a THIRD sink, never the stream feed."""
    hb_dir = tmp_path / "hb"
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
    mod = importlib.reload(tee_mod)
    tee = mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_HEL)
    assert sorted(p.name for p in hb_dir.iterdir()) == [mod.HEARTBEAT_LANE_BRIDGE]
    assert (hb_dir / mod.HEARTBEAT_LANE_BRIDGE).read_bytes() == b""
    assert [r["delta"] for r in _rows(Path(tee_mod._TEST_FEED)) if r["event"] == "delta"] == ["Hel"]


def test_heartbeat_inert_without_a_mounted_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("WCB_HEARTBEAT_DIR", raising=False)
    monkeypatch.delenv("WCB_CC_STREAM_LOG_PATH", raising=False)
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    tee = mod.StreamTee()
    tee.attempt_started()
    tee.feed(F_HEL)
    tee.retrying(1)
    assert list(tmp_path.iterdir()) == []


def test_broken_heartbeat_sink_never_raises_and_self_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(tmp_path / "hb"))
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)

    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(mod.os, "utime", _boom)
    monkeypatch.setattr(mod.os, "makedirs", _boom)
    tee = mod.StreamTee()          # must not raise
    tee.attempt_started()
    tee.feed(F_HEL)
    tee.finish()
    assert mod._hb_disabled is True


def test_heartbeat_throttle_collapses_a_chunk_storm(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(tmp_path / "hb"))
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "600")
    import src.utils.claude_oauth.stream_tee as mod
    mod = importlib.reload(mod)
    tee = mod.StreamTee()
    path = tmp_path / "hb" / mod.HEARTBEAT_LANE_BRIDGE
    stale = path.stat().st_mtime - 100.0
    os.utime(path, (stale, stale))
    for _ in range(50):
        tee.feed(F_HEL)
    assert path.stat().st_mtime == stale


def test_buffered_bridge_path_beats_per_chunk(monkeypatch, bridge_mod, tmp_path):
    """End-to-end on the real buffered loop: chunks in, heartbeat mtime out,
    client bytes untouched (R5)."""
    hb_dir = tmp_path / "hb"
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
    import src.utils.claude_oauth.stream_tee as tee_mod
    importlib.reload(tee_mod)
    bridge = importlib.reload(bridge_mod)
    full = [F_START, F_HEL, F_LO, F_STOP]
    _install_fake_httpx(monkeypatch, bridge, _FakeUpstream([list(full)]))
    body = asyncio.run(_client_bytes(bridge))
    assert body == b"".join(full)
    lane = hb_dir / tee_mod.HEARTBEAT_LANE_BRIDGE
    assert lane.exists() and lane.read_bytes() == b""
