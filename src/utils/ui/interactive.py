"""TUI-backed IO for interactive Mode-2 ``HumanTurnSource``.

``HumanTurnSource`` (``src/utils/turn_source.py``) accepts an injected
``io=(input_fn, print_fn)``. On a plain terminal it defaults to a ``/dev/tty``
REPL. When the unified Textual dashboard owns the screen, that raw REPL cannot
coexist with it, so this module supplies an alternative ``io`` that routes
through the dashboard instead:

  * ``input_fn`` → :meth:`InputBridge.request`: blocks the worker thread until
    the human submits a line into the dashboard's ``#prompt`` Input widget.
  * ``print_fn`` → ``EV_CHAT``: each line ``HumanTurnSource`` prints (the
    scripted suggestion, the agent's prior reply, harness notices) is published
    to the dashboard's Conversation pane.

``HumanTurnSource``'s turn logic is reused verbatim — Enter = accept the scripted
suggestion, typed = override, ``/exit`` = end, and verbatim timeline recording —
only the io backend changes. That is what lets static and interactive runs share
the one dashboard rather than forking a second interface.

Imports no ``textual``; safe to import anywhere (the bus and bridge are pure).
"""
from __future__ import annotations

from typing import Callable, Tuple

from .events import EV_CHAT, get_bus
from .input_bridge import get_input_bridge


def tui_io() -> Tuple[Callable[[str], str], Callable[[str], None]]:
    """Return ``(input_fn, print_fn)`` bound to the shared dashboard bridge/bus.

    ``input_fn(prompt)`` blocks on the input bridge and returns the raw submitted
    line — an empty string on a bare Enter, which ``HumanTurnSource`` maps to
    "accept the scripted suggestion", preserving its recorded ``override`` flags.
    ``EOFError`` propagates when the bridge closes (dashboard quit); the caller
    ends the session, exactly as for Ctrl-D on the /dev/tty path.

    ``print_fn(text)`` emits one Conversation-pane line; fail-open so a display
    problem never reaches the harness worker.
    """
    def _input(prompt_text: str) -> str:
        return get_input_bridge().request(prompt_text)

    def _print(text: str) -> None:
        try:
            get_bus().emit(EV_CHAT, text=str(text))
        except Exception:
            pass

    return _input, _print
