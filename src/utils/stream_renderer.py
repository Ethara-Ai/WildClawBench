"""Terminal renderer for the live-stream feed (stream.jsonl / agent.log).

Consumer half of the streaming feature (docs/STREAMING_PLAN.md §4). Runs as
a daemon thread inside eval/run_batch.py::run_single_task, strictly
display-only: it reads the observability feed and prints; nothing graded
depends on it (R1) and stopping it is bounded (stop() joins ≤5s, R3).

Output target resolution (in order):
  1. /dev/tty when openable — the primary case: under `script/run.sh` stdout
     is piped through `tee logs/<run>.log`, so isatty(stdout) is False even
     though a human is watching. Writing to the controlling terminal keeps
     the run log free of token spew while the operator still sees it live.
  2. sys.stdout when it is a tty (direct `python3 eval/run_batch.py` runs).
  3. Otherwise "summary mode": one aggregate line every ~5s via print() —
     tee'd logs stay small; full fidelity lives in stream.jsonl.

Modes:
  * token mode  — tail stream.jsonl (start offset = file EOF at start()):
      - agent text deltas of the MAIN session: printed raw as they arrive.
        Main session = first agent request seen; additional concurrent agent
        requests (openclaw sub-agents, image-tool calls) render as one
        status line each — token-interleaving them is unreadable.
      - agent thinking deltas: dim, prefixed [thinking] per block; hidden
        entirely when WCB_STREAM_THINKING is falsy.
      - judge:*/testgen: line-buffered with a [source] prefix.
  * turn mode   — tail agent.log (openclaw's real-time turn narration),
      printing whole lines. Used as the fallback when the token feed is
      absent/stale for >30s (e.g. sidecar hook unavailable), and as the
      primary mode when run_batch starts the renderer without a stream path.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional, TextIO

_TRUTHY = ("1", "true", "yes", "on")
_POLL_SECS = 0.2
_SUMMARY_EVERY_SECS = 5.0
_TOKEN_FEED_STALE_SECS = 30.0
_DIM = "\033[2m"
_RESET = "\033[0m"


def _thinking_enabled() -> bool:
    raw = os.environ.get("WCB_STREAM_THINKING", "1").strip().lower()
    return raw in _TRUTHY


class StreamRenderer(threading.Thread):
    """Display-only follower of the stream feed. Never raises out of run()."""

    def __init__(
        self,
        stream_path: Optional[Path],
        agent_log_path: Optional[Path],
        run_label: str = "",
    ) -> None:
        super().__init__(name="wcb-stream-renderer", daemon=True)
        self._stream_path = Path(stream_path) if stream_path else None
        self._agent_log_path = Path(agent_log_path) if agent_log_path else None
        self._run_label = run_label
        self._stop_event = threading.Event()
        self._out: Optional[TextIO] = None
        self._owns_out = False
        self._interactive = False
        self._color = False
        # display state
        self._main_request: Optional[str] = None
        self._open_requests: set[str] = set()
        self._cur_kind: Optional[str] = None  # last agent kind printed (text/thinking)
        self._line_buf: dict[str, str] = {}  # per-source partial lines (judges)
        self._delta_count = 0
        self._last_summary = 0.0

    # ------------------------------------------------------------------ setup

    def _resolve_out(self) -> None:
        try:
            fh = open("/dev/tty", "w", encoding="utf-8", errors="replace")
            self._out, self._owns_out, self._interactive = fh, True, True
        except OSError:
            if sys.stdout.isatty():
                self._out, self._interactive = sys.stdout, True
            else:
                self._out, self._interactive = sys.stdout, False
        self._color = self._interactive and not os.environ.get("NO_COLOR")

    def _w(self, text: str) -> None:
        try:
            assert self._out is not None
            self._out.write(text)
            self._out.flush()
        except Exception:
            # A dead terminal must never take the run down with it.
            self._stop_event.set()

    def _line(self, text: str, dim: bool = False) -> None:
        if dim and self._color:
            self._w(f"{_DIM}{text}{_RESET}\n")
        else:
            self._w(text + "\n")

    # ---------------------------------------------------------------- control

    def stop(self, timeout: float = 5.0) -> None:
        """Signal + bounded join (R3). Only teardown waits on this; grading
        never does. Never raises."""
        try:
            self._stop_event.set()
            self.join(timeout=timeout)
            self._flush_partial_lines()
            if self._owns_out and self._out is not None:
                self._out.close()
        except Exception:
            pass

    # ------------------------------------------------------------------- run

    def run(self) -> None:  # noqa: D102
        try:
            self._resolve_out()
            if self._stream_path is not None:
                self._run_token_mode()
            elif self._agent_log_path is not None:
                self._run_turn_mode()
        except Exception:
            # Display-only thread: swallow everything (R2).
            pass

    # ------------------------------------------------------------ token mode

    def _run_token_mode(self) -> None:
        assert self._stream_path is not None
        started = time.time()
        # Start at current EOF: prior reps' events in a shared batch feed are
        # behind this offset by construction (single-run foreground gate D6).
        offset = self._stream_path.stat().st_size if self._stream_path.exists() else 0
        saw_any = False
        carry = ""
        while not self._stop_event.is_set():
            if not self._stream_path.exists():
                if (not saw_any and time.time() - started > _TOKEN_FEED_STALE_SECS
                        and self._agent_log_path is not None):
                    self._line("[stream] token feed unavailable — falling back "
                               "to turn-level agent.log", dim=True)
                    self._run_turn_mode()
                    return
                self._stop_event.wait(_POLL_SECS)
                continue
            try:
                with open(self._stream_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    data = fh.read()
                    offset = fh.tell()
            except OSError:
                self._stop_event.wait(_POLL_SECS)
                continue
            if not data:
                if (not saw_any and time.time() - started > _TOKEN_FEED_STALE_SECS
                        and self._agent_log_path is not None):
                    self._line("[stream] token feed stale — falling back to "
                               "turn-level agent.log", dim=True)
                    self._run_turn_mode()
                    return
                self._stop_event.wait(_POLL_SECS)
                continue
            saw_any = True
            carry += data
            lines = carry.split("\n")
            carry = lines.pop()  # possibly-torn trailing fragment
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # torn line — skip, never error
                self._render_event(evt)
        # drain nothing further; stop() flushes partial lines

    def _render_event(self, evt: dict) -> None:
        source = str(evt.get("source") or "")
        event = str(evt.get("event") or "")
        kind = str(evt.get("kind") or "text")
        delta = evt.get("delta") or ""
        req = str(evt.get("request_id") or "")

        if not self._interactive:
            self._summary_tick(event)
            return

        if source == "agent":
            self._render_agent(event, kind, str(delta), req)
        else:
            self._render_line_buffered(source, event, str(delta))

    def _render_agent(self, event: str, kind: str, delta: str, req: str) -> None:
        if event == "message_start":
            if req in self._open_requests or req == self._main_request:
                return  # duplicate start for an already-open request
            self._open_requests.add(req)
            if self._main_request is None:
                self._main_request = req
            else:
                self._line(f"[sub-agent {req[:8]}] started", dim=True)
            return
        if event in ("message_stop", "error"):
            self._open_requests.discard(req)
            if req == self._main_request:
                if self._cur_kind is not None:
                    self._w("\n")
                    self._cur_kind = None
                self._main_request = None  # next request becomes main (next turn)
                if event == "error":
                    self._line(f"[stream error] {delta}", dim=True)
            else:
                self._line(f"[sub-agent {req[:8]}] "
                           f"{'done' if event == 'message_stop' else 'error'}", dim=True)
            return
        if event != "delta":
            return
        if req != self._main_request:
            return  # sub-agent deltas: status lines only (D5)
        if kind == "thinking":
            if not _thinking_enabled():
                return
            if self._cur_kind != "thinking":
                if self._cur_kind is not None:
                    self._w("\n")
                self._w(f"{_DIM}[thinking] " if self._color else "[thinking] ")
                self._cur_kind = "thinking"
            self._w(delta if not self._color else delta)  # dim already open
            return
        # text
        if self._cur_kind != "text":
            if self._cur_kind == "thinking":
                self._w(f"{_RESET}\n" if self._color else "\n")
            self._cur_kind = "text"
        self._w(delta)

    def _render_line_buffered(self, source: str, event: str, delta: str) -> None:
        if event == "message_start":
            self._line(f"[{source}] …", dim=True)
            return
        if event in ("message_stop", "error", "status"):
            buf = self._line_buf.pop(source, "")
            if buf:
                self._line(f"[{source}] {buf}")
            if event == "error":
                self._line(f"[{source}] ERROR {delta}", dim=True)
            elif event == "status":
                self._line(f"[{source}] {delta}", dim=True)
            return
        # delta: flush complete lines only
        buf = self._line_buf.get(source, "") + delta
        *complete, rest = buf.split("\n")
        for ln in complete:
            self._line(f"[{source}] {ln}")
        self._line_buf[source] = rest

    def _flush_partial_lines(self) -> None:
        try:
            if self._cur_kind is not None and self._interactive:
                self._w(f"{_RESET}\n" if self._color else "\n")
                self._cur_kind = None
            for source, buf in list(self._line_buf.items()):
                if buf and self._interactive:
                    self._line(f"[{source}] {buf}")
            self._line_buf.clear()
        except Exception:
            pass

    def _summary_tick(self, event: str) -> None:
        if event == "delta":
            self._delta_count += 1
        now = time.time()
        if now - self._last_summary >= _SUMMARY_EVERY_SECS and self._delta_count:
            self._last_summary = now
            print(f"[stream] {self._run_label} +{self._delta_count} deltas", flush=True)
            self._delta_count = 0

    # ------------------------------------------------------------- turn mode

    def _run_turn_mode(self) -> None:
        """Tail agent.log (openclaw's live turn narration), whole lines."""
        if self._agent_log_path is None:
            return
        offset = 0
        carry = ""
        while not self._stop_event.is_set():
            if not self._agent_log_path.exists():
                self._stop_event.wait(_POLL_SECS)
                continue
            try:
                with open(self._agent_log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    data = fh.read()
                    offset = fh.tell()
            except OSError:
                self._stop_event.wait(_POLL_SECS)
                continue
            if not data:
                self._stop_event.wait(_POLL_SECS)
                continue
            carry += data
            lines = carry.split("\n")
            carry = lines.pop()
            for ln in lines:
                if not ln.strip():
                    continue
                if self._interactive:
                    self._line(f"[agent] {ln}")
                else:
                    self._summary_tick("delta")


def start_renderer(
    stream_path: Optional[Path],
    agent_log_path: Optional[Path],
    run_label: str = "",
) -> Optional[StreamRenderer]:
    """Create+start a renderer when streaming is enabled, else None.

    Gate matches stream_events: WCB_STREAM truthy. token mode needs
    WCB_STREAM_LOG_PATH (stream_path); otherwise turn mode over agent.log.
    Never raises."""
    try:
        if os.environ.get("WCB_STREAM", "").strip().lower() not in _TRUTHY:
            return None
        r = StreamRenderer(stream_path, agent_log_path, run_label=run_label)
        r.start()
        return r
    except Exception:
        return None
