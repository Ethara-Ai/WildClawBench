"""Pull-based turn feeds for the openclaw runner loop.

The runner's multi-turn loop (src/agents/openclaw/runner.py) pulls each turn's
message from a TurnSource instead of iterating a precomputed tuple:

- ``StaticTurnSource`` wraps the static schedule (``spec.turns`` — the
  prompts.txt turn messages — or the single ``spec.prompt``). Single-turn
  behaviour is byte-identical to the pre-pull loop.
- ``HumanTurnSource`` (Mode 2, SFT collection) paces those turns by a human:
  each scripted message is shown as an overridable suggestion, the agent's
  prior reply is echoed, and the human accepts (Enter) / overrides (type) /
  ends (``/exit``). Wraps an inner source so environment mutation between turns
  (the inject ``before_turn`` hook) is IDENTICAL to static mode — only the
  prompt source changes.

Multi-turn is openclaw-backend-only by design; other backends ignore
``spec.turn_source`` exactly as they ignore ``spec.turns``.
"""
from __future__ import annotations

from typing import Callable, Protocol, Sequence


class TurnSource(Protocol):
    """What the runner loop pulls turns from."""

    def next_message(self, turn_index: int) -> str | None:
        """Message for turn ``turn_index``, or None to end the run.

        Called with the agent IDLE and with strictly increasing indices
        starting at 0. May raise; the runner treats that as end-of-run.
        """
        ...

    def total(self) -> int | None:
        """Planned turn count, or None when open-ended (interactive)."""
        ...

    def source_name(self) -> str: ...


class StaticTurnSource:
    """Adapter over a static message sequence.

    The runner constructs it as ``StaticTurnSource(spec.turns or (spec.prompt,))``
    preserving the historical falsy-tuple fallback: an EMPTY turns tuple means
    one turn with ``spec.prompt``, never zero turns.
    """

    def __init__(self, messages: Sequence[str]):
        self._messages = tuple(messages)

    def next_message(self, turn_index: int) -> str | None:
        if 0 <= turn_index < len(self._messages):
            return self._messages[turn_index]
        return None

    def total(self) -> int | None:
        return len(self._messages)

    def source_name(self) -> str:
        return "static"


class HumanTurnSource:
    """Mode 2 (human intervention, SFT collection): human-paced turn feed.

    Wraps an inner TurnSource (a ``StaticTurnSource`` over the prompts.txt turn
    schedule) so boundary work and environment mutation are IDENTICAL to static
    Mode 1; only the prompt source changes. At each boundary the human sees the
    agent's previous reply (``reply_fn``) and the scripted message as a
    suggestion: Enter sends the suggestion verbatim, typed text overrides it,
    ``/exit`` (or EOF/Ctrl-D) ends the session. Once the script is exhausted the
    human may keep free-chatting until ``/exit``.

    Reads/writes the controlling terminal (``/dev/tty``) directly, so run.sh's
    stdout tee and per-model backgrounding can never interleave — and the
    constructor fails fast when no TTY exists. ``total()`` is None (open-ended).
    Every delivered message is recorded verbatim via ``record_fn``
    (→ turn_timeline.jsonl) for replay / promotion to a static prompts.txt task
    (script/session_to_prompts.py).
    """

    MAX_MESSAGE_CHARS = 100_000  # bash -c argv path (ARG_MAX headroom)

    def __init__(
        self,
        inner: "TurnSource | None" = None,
        *,
        reply_fn: Callable[[], str] | None = None,
        record_fn: Callable[[dict], None] | None = None,
        io: tuple[Callable[[str], str], Callable[[str], None]] | None = None,
    ):
        self._inner = inner
        self._reply_fn = reply_fn
        self._record_fn = record_fn
        if io is not None:
            self._input, self._print = io
        else:
            try:  # fail fast: no controlling terminal → no interactive mode
                tty_in = open("/dev/tty", "r", encoding="utf-8")
                tty_out = open("/dev/tty", "w", encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(
                    "--interactive requires a controlling terminal (/dev/tty): "
                    f"{exc}. Run `python3 eval/run_batch.py --interactive` "
                    "directly — not via run.sh, nohup, or a pipe.") from exc

            def _tty_input(prompt_text: str) -> str:
                tty_out.write(prompt_text)
                tty_out.flush()
                line = tty_in.readline()
                if line == "":  # EOF (Ctrl-D)
                    raise EOFError
                return line.rstrip("\n")

            def _tty_print(text: str) -> None:
                tty_out.write(text + "\n")
                tty_out.flush()

            self._input, self._print = _tty_input, _tty_print

    def next_message(self, turn_index: int) -> str | None:
        # Inner boundary work FIRST (the scripted next message) — exactly as
        # Mode 1. The inject before_turn mutation still fires in the runner.
        suggestion: str | None = None
        if self._inner is not None:
            suggestion = self._inner.next_message(turn_index)

        # Show the agent's previous reply before prompting (ChatGPT-like).
        if turn_index > 0 and self._reply_fn is not None:
            try:
                reply = (self._reply_fn() or "").strip()
            except Exception as exc:
                reply = f"(reply unavailable: {exc})"
            if reply:
                self._print(f"\n── agent (turn {turn_index - 1}) " + "─" * 40)
                self._print(reply)

        if suggestion is not None:
            self._print(f"\n── scripted turn {turn_index} " + "─" * 40)
            self._print(suggestion)
            prompt_text = ("── you (Enter = send scripted message, type to "
                           "override, /exit to end) ── ")
        else:
            prompt_text = f"── you (turn {turn_index}; /exit to end) ── "

        while True:
            try:
                typed = self._input(prompt_text)
            except EOFError:
                self._record({"type": "human.exit", "turn_index": turn_index,
                              "reason": "eof"})
                return None
            if typed.strip() == "/exit":
                self._record({"type": "human.exit", "turn_index": turn_index,
                              "reason": "exit"})
                return None
            if not typed.strip():
                if suggestion is not None:
                    self._record({"type": "human.message",
                                  "turn_index": turn_index, "override": False,
                                  "chars": len(suggestion), "text": suggestion})
                    return suggestion
                self._print("(empty — type a message, or /exit)")
                continue
            if len(typed) > self.MAX_MESSAGE_CHARS:
                self._print(f"(message too long: {len(typed)} chars > "
                            f"{self.MAX_MESSAGE_CHARS} cap — not sent)")
                continue
            self._record({"type": "human.message", "turn_index": turn_index,
                          "override": suggestion is not None,
                          "chars": len(typed), "text": typed})
            return typed

    def total(self) -> int | None:
        return None  # open-ended: the human decides when the session ends

    def source_name(self) -> str:
        return "human"

    def _record(self, entry: dict) -> None:
        if self._record_fn is not None:
            try:
                self._record_fn(entry)
            except Exception:
                pass
