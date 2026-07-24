"""Tests for HumanTurnSource (Mode 2) and the session→prompts promotion (P3).

IO is injected (input_fn, print_fn) — no real /dev/tty. Asserts: boundary
work still flows through the inner scripted source (mutation identical to
Mode 1), Enter accepts the scripted suggestion verbatim, typed text
overrides, /exit and EOF end the session, the oversize cap rejects, records
are written verbatim for replay, and the round-trip through
script/session_to_prompts.py reproduces the delivered turn sequence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.turn_source import (  # noqa: E402
    HumanTurnSource,
    StaticTurnSource,
)
from script.session_to_prompts import (  # noqa: E402
    render_prompts_txt,
    turns_from_timeline,
)
from src.utils.inject_director import parse_prompts_file  # noqa: E402


class _IO:
    """Scripted terminal: pops answers; records printed lines."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.printed: list[str] = []
        self.prompts: list[str] = []

    def input(self, prompt_text):
        self.prompts.append(prompt_text)
        if not self.answers:
            raise EOFError
        a = self.answers.pop(0)
        if a is EOFError:
            raise EOFError
        return a

    def print(self, text):
        self.printed.append(text)


def _human(answers, inner=None, records=None, reply_fn=None):
    io = _IO(answers)
    src = HumanTurnSource(
        inner,
        reply_fn=reply_fn,
        record_fn=(records.append if records is not None else None),
        io=(io.input, io.print),
    )
    return src, io


def test_enter_accepts_scripted_suggestion_verbatim():
    records = []
    inner = StaticTurnSource(("scripted m0", "scripted m1"))
    src, io = _human(["", "my own message"], inner=inner, records=records)

    assert src.next_message(0) == "scripted m0"
    assert src.next_message(1) == "my own message"   # typed overrides
    assert src.total() is None
    assert src.source_name() == "human"
    # suggestion was displayed before the prompt
    assert any("scripted m0" in p for p in io.printed)
    assert [ (r["type"], r["turn_index"], r["override"]) for r in records ] == [
        ("human.message", 0, False), ("human.message", 1, True)]
    assert records[0]["text"] == "scripted m0"       # verbatim for replay
    assert records[1]["text"] == "my own message"


def test_free_chat_after_script_exhausts_then_exit():
    records = []
    inner = StaticTurnSource(("only turn",))
    src, _ = _human(["", "follow-up question", "/exit"],
                    inner=inner, records=records)
    assert src.next_message(0) == "only turn"
    assert src.next_message(1) == "follow-up question"  # no suggestion left
    assert src.next_message(2) is None                  # /exit
    assert records[-1] == {**records[-1], "type": "human.exit", "reason": "exit"}


def test_eof_ends_session():
    src, _ = _human([EOFError])
    assert src.next_message(0) is None


def test_empty_without_suggestion_reprompts():
    src, io = _human(["", "", "real message"])
    assert src.next_message(0) == "real message"
    assert sum("(empty" in p for p in io.printed) == 2


def test_oversize_message_rejected_then_accepted():
    big = "x" * (HumanTurnSource.MAX_MESSAGE_CHARS + 1)
    src, io = _human([big, "small"])
    assert src.next_message(0) == "small"
    assert any("too long" in p for p in io.printed)


def test_reply_shown_before_prompt_from_turn_one():
    inner = StaticTurnSource(("m0", "m1"))
    replies = iter(["agent said hello"])  # reply_fn fires from turn 1 only
    src, io = _human(["", ""], inner=inner,
                     reply_fn=lambda: next(replies))
    src.next_message(0)
    n_before = len(io.printed)
    src.next_message(1)
    shown = io.printed[n_before:]
    assert any("agent said hello" in p for p in shown)
    assert any("agent (turn 0)" in p for p in shown)


def test_mutation_flows_through_inner_before_prompt():
    events = []

    class _Inner:
        def next_message(self, i):
            events.append(("inner_pull", i))
            return f"m{i}"

        def total(self):
            return 2

        def source_name(self):
            return "x"

    src, io = _human(["", ""], inner=_Inner())
    src.next_message(0)
    assert events == [("inner_pull", 0)]  # inner (mutation) ran before prompt
    assert io.prompts, "human was prompted after inner boundary work"


def test_no_tty_fails_fast(monkeypatch):
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if path == "/dev/tty":
            raise OSError("no tty")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(RuntimeError, match="controlling terminal"):
        HumanTurnSource()


# ── session → prompts.txt round-trip ────────────────────────────────────────

def test_session_roundtrip_to_static_prompts(tmp_path):
    records = []
    inner = StaticTurnSource(("scripted m0",))
    src, _ = _human(["", "please fix ONLY the heading", "/exit"],
                    inner=inner, records=records)
    delivered = []
    i = 0
    while (m := src.next_message(i)) is not None:
        delivered.append(m)
        i += 1

    timeline = tmp_path / "turn_timeline.jsonl"
    timeline.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    turns = turns_from_timeline(timeline)
    text = render_prompts_txt(turns)
    (tmp_path / "prompts.txt").write_text(text, encoding="utf-8")

    # the canonical parser reproduces the delivered sequence exactly
    reparsed = parse_prompts_file(tmp_path / "prompts.txt")
    assert reparsed == delivered


# ── StaticTurnSource (the schedule adapter the runner + HumanTurnSource wrap) ─

def test_static_turn_source_sequence():
    s = StaticTurnSource(("a", "b"))
    assert (s.next_message(0), s.next_message(1), s.next_message(2)) == ("a", "b", None)
    assert s.total() == 2 and s.source_name() == "static"
