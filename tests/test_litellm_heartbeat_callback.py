"""Unit tests for src/utils/litellm_heartbeat_callback.py (sidecar liveness).

Invariants under test:
  liveness   a streamed chunk moves the run's heartbeat mtime forward, and the
             request-start hook writes one before any chunk arrives
  R5         pass-the-original-object — every chunk yielded IS the received
             object (identity, not equality), on healthy AND broken-writer paths
  fail-silent a broken heartbeat sink never raises out of any hook and never
             stops the stream; the pre-call hook stays a request no-op
  m0130      sink separation — writes ONLY under WCB_HEARTBEAT_DIR; configured
             LITELLM_USAGE_LOG_PATH / WCB_STREAM_LOG_PATH files stay untouched
  keying     only `wcb::` run keys ever become filenames (a real credential
             must never reach the filesystem), read from the run-key auth
             token first and the attribution headers second
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RUN_KEY = "wcb::task_alpha::" + "a" * 32


class _Token:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key


@pytest.fixture()
def hb(monkeypatch, tmp_path):
    """Reload the callback with WCB_HEARTBEAT_DIR pointed at tmp.

    The module captures nothing at import time except its defaults, but it DOES
    carry per-process throttle/disable state, so a reload per test is the
    honest reset — same technique as tests/test_litellm_stream_callback.py."""
    hb_dir = tmp_path / "heartbeats"
    usage = tmp_path / "usage.jsonl"
    stream = tmp_path / "stream.jsonl"
    usage.write_text("")
    stream.write_text("")
    monkeypatch.setenv("WCB_HEARTBEAT_DIR", str(hb_dir))
    monkeypatch.setenv("LITELLM_USAGE_LOG_PATH", str(usage))
    monkeypatch.setenv("WCB_STREAM_LOG_PATH", str(stream))
    # Per-chunk throttling would otherwise collapse a 3-chunk test into one write.
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "0")
    import src.utils.litellm_heartbeat_callback as mod
    mod = importlib.reload(mod)
    mod._TEST_DIR = hb_dir
    mod._TEST_USAGE = usage
    mod._TEST_STREAM = stream
    return mod


def _beat_path(mod, run_key: str = RUN_KEY) -> Path:
    return Path(mod._TEST_DIR) / mod.safe_name(run_key)


def _drain(mod, chunks, token=None, request_data=None):
    async def _source():
        for c in chunks:
            yield c

    async def _go():
        out = []
        async for c in mod.heartbeat_instance.async_post_call_streaming_iterator_hook(
            user_api_key_dict=token if token is not None else _Token(RUN_KEY),
            response=_source(),
            request_data=request_data if request_data is not None else {},
        ):
            out.append(c)
        return out

    return asyncio.run(_go())


# ---------------------------------------------------------------- liveness


def test_chunk_advances_heartbeat_mtime(hb):
    path = _beat_path(hb)
    _drain(hb, ["a"])
    assert path.exists()
    first = path.stat().st_mtime
    os.utime(path, (first - 100.0, first - 100.0))
    _drain(hb, ["b"])
    assert path.stat().st_mtime > first - 100.0


def test_request_start_hook_beats_before_any_chunk(hb):
    asyncio.run(hb.heartbeat_instance.async_pre_call_hook(
        user_api_key_dict=_Token(RUN_KEY), cache=None, data={}, call_type="acompletion"))
    assert _beat_path(hb).exists()


def test_pre_call_hook_is_a_request_no_op(hb):
    # Returning a dict would REPLACE the request litellm forwards; the
    # heartbeat must never shape a request, so None is load-bearing.
    data = {"messages": [{"role": "user", "content": "hi"}]}
    out = asyncio.run(hb.heartbeat_instance.async_pre_call_hook(
        user_api_key_dict=_Token(RUN_KEY), cache=None, data=data,
        call_type="acompletion"))
    assert out is None
    assert data == {"messages": [{"role": "user", "content": "hi"}]}


def test_throttle_collapses_chunk_storm_to_one_write(hb, monkeypatch):
    monkeypatch.setenv("WCB_HEARTBEAT_MIN_INTERVAL_S", "600")
    hb._last_touch.clear()
    _drain(hb, ["a", "b", "c"])
    path = _beat_path(hb)
    mtime = path.stat().st_mtime
    os.utime(path, (mtime - 100.0, mtime - 100.0))
    _drain(hb, ["d", "e"])
    assert path.stat().st_mtime == pytest.approx(mtime - 100.0), (
        "throttled window must not re-touch")


# ----------------------------------------------------------------- R5 / R2


def test_chunks_are_forwarded_unchanged(hb):
    sentinels = [object(), object(), object()]
    out = _drain(hb, sentinels)
    assert len(out) == 3
    for got, want in zip(out, sentinels):
        assert got is want


def test_broken_sink_never_breaks_the_stream(hb, monkeypatch):
    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(hb.os, "utime", _boom)
    monkeypatch.setattr(hb.os, "makedirs", _boom)
    sentinels = [object(), object()]
    out = _drain(hb, sentinels)
    assert [id(o) for o in out] == [id(s) for s in sentinels]
    assert hb._disabled is True


def test_broken_sink_never_raises_out_of_pre_call(hb, monkeypatch):
    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(hb.os, "utime", _boom)
    monkeypatch.setattr(hb.os, "makedirs", _boom)
    assert asyncio.run(hb.heartbeat_instance.async_pre_call_hook(
        user_api_key_dict=_Token(RUN_KEY), cache=None, data={},
        call_type="acompletion")) is None


def test_touch_is_silent_after_self_disable(hb, monkeypatch):
    hb._disabled = True
    hb.touch(RUN_KEY)
    assert not _beat_path(hb).exists()


# ------------------------------------------------------- m0130 separation


def test_never_writes_to_usage_or_stream_sinks(hb):
    _drain(hb, ["a", "b"])
    asyncio.run(hb.heartbeat_instance.async_pre_call_hook(
        user_api_key_dict=_Token(RUN_KEY), cache=None, data={},
        call_type="acompletion"))
    assert Path(hb._TEST_USAGE).read_text() == "", (
        "LITELLM_USAGE_LOG_PATH must remain UNTOUCHED")
    assert Path(hb._TEST_STREAM).read_text() == "", (
        "WCB_STREAM_LOG_PATH must remain UNTOUCHED")
    assert sorted(p.name for p in Path(hb._TEST_DIR).iterdir()) == [
        hb.safe_name(RUN_KEY)]


def test_heartbeat_file_carries_no_payload(hb):
    # Zero-byte by construction: mtime IS the signal, so nothing about a
    # request (prompt, credential, model) can leak into this sink.
    _drain(hb, ["a"])
    assert _beat_path(hb).read_bytes() == b""


# --------------------------------------------------------------- keying


def test_non_run_key_bearers_write_nothing(hb):
    _drain(hb, ["a"], token=_Token("sk-real-secret-credential"))
    assert not Path(hb._TEST_DIR).exists() or list(Path(hb._TEST_DIR).iterdir()) == []


def test_run_key_read_from_attribution_header(hb):
    _drain(hb, ["a"], token=_Token("sk-master"),
           request_data={"proxy_server_request": {"headers": {"x-wcb-run-key": RUN_KEY}}})
    assert _beat_path(hb).exists()


def test_run_key_read_from_bearer_authorization_header(hb):
    _drain(hb, ["a"], token=_Token(""),
           request_data={"metadata": {"headers": {"authorization": f"Bearer {RUN_KEY}"}}})
    assert _beat_path(hb).exists()


def test_safe_name_cannot_escape_the_heartbeat_dir(hb):
    assert "/" not in hb.safe_name("wcb::../../etc/passwd::" + "b" * 32)
    assert hb.safe_name("wcb::a/b::c") == "wcb__a_b__c"
