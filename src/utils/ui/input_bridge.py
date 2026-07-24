"""Cross-thread input channel for interactive Mode-2 inside the Textual dashboard.

The harness runs on a Textual worker thread while the dashboard renders on the UI
thread (see ``src/utils/ui/tui.py``). In interactive Mode 2 (human-in-the-loop /
SFT collection) the worker — blocked at a turn boundary with the agent idle,
inside ``HumanTurnSource.next_message`` — needs a line of input from the human. It
cannot read the terminal directly (the Textual app owns it), so it asks the UI
for one and blocks until the UI delivers it:

    worker:  line = get_input_bridge().request(prompt_text)   # blocks
    UI:      get_input_bridge().submit(typed_value)           # unblocks worker

``request()`` emits ``EV_INPUT_REQUEST`` on the shared event bus (which the
dashboard marshals onto its own thread to reveal + focus the ``#prompt`` Input
widget) and then blocks on a single-slot queue for the response. ``submit()``
(called on the UI thread from ``Input.Submitted``) puts the value. ``close()``
(called on dashboard unmount/quit) enqueues an EOF sentinel so a blocked
``request()`` raises ``EOFError`` — ``HumanTurnSource`` treats that exactly like
Ctrl-D and ends the session, so quitting the dashboard never hangs the worker.

Process-wide singleton (mirrors ``events.get_bus()``): the dashboard and the
:func:`src.utils.ui.interactive.tui_io` adapter must share ONE instance. Nothing
here imports ``textual``; the module is safe to import anywhere. ``reset()``
exists for test isolation.
"""
from __future__ import annotations

import queue
import threading

from .events import EV_INPUT_REQUEST, get_bus


class _Closed:
    """Sentinel value delivered to a pending request() when the bridge closes."""


_CLOSED = _Closed()


class InputBridge:
    """A one-slot request/response channel between the worker and the UI thread."""

    def __init__(self) -> None:
        # maxsize=1: the runner pulls turns strictly sequentially with the agent
        # idle, so there is at most one outstanding request/response at a time.
        self._responses: "queue.Queue[object]" = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._closed = False

    def request(self, prompt: str) -> str:
        """Ask the UI for a line and BLOCK until it arrives (worker thread).

        Emits ``EV_INPUT_REQUEST`` so the dashboard reveals its input widget,
        then blocks on the response queue. Raises ``EOFError`` if the bridge is
        (or becomes) closed — the caller (``HumanTurnSource``) ends the session
        on ``EOFError``, exactly as for Ctrl-D on the /dev/tty path.
        """
        with self._lock:
            if self._closed:
                raise EOFError("input bridge closed")
            # Drop any stale response so a prior/duplicate submit can never
            # satisfy this fresh request.
            try:
                while True:
                    self._responses.get_nowait()
            except queue.Empty:
                pass
        # Ask the UI to prompt (fan-out; the dashboard reveals #prompt). Emitting
        # with no subscriber is a harmless no-op, but request() is only ever
        # called while the dashboard is active.
        try:
            get_bus().emit(EV_INPUT_REQUEST, prompt=str(prompt))
        except Exception:
            pass
        value = self._responses.get()  # blocks until submit() or close()
        if value is _CLOSED:
            raise EOFError("input bridge closed")
        return str(value)

    def submit(self, text: str) -> None:
        """Deliver a typed line to a blocked ``request()`` (UI thread).

        A no-op when there is no outstanding request (the queue is full or the
        bridge is closed) — the dashboard only submits in response to an
        ``EV_INPUT_REQUEST``, so this just guards against races.
        """
        if self._closed:
            return
        try:
            self._responses.put_nowait(str(text))
        except queue.Full:
            pass

    def close(self) -> None:
        """Unblock any pending ``request()`` with EOF (dashboard unmount/quit).

        Idempotent. After close, further ``request()`` calls raise immediately
        and ``submit()`` is a no-op.
        """
        with self._lock:
            self._closed = True
        # Best-effort: replace any queued response with the EOF sentinel so a
        # currently-blocked request() wakes up and raises EOFError.
        try:
            while True:
                self._responses.get_nowait()
        except queue.Empty:
            pass
        try:
            self._responses.put_nowait(_CLOSED)
        except queue.Full:
            pass

    def is_closed(self) -> bool:
        with self._lock:
            return self._closed


_bridge = InputBridge()


def get_input_bridge() -> InputBridge:
    """Return the process-wide input bridge (mirrors ``events.get_bus()``)."""
    return _bridge


def reset() -> None:
    """Replace the singleton with a fresh, open bridge (test isolation)."""
    global _bridge
    _bridge = InputBridge()
