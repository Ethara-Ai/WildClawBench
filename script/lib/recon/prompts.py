"""Recover a bundle's prompt text and normalise its header block.

A bundle publishes its turns in whichever file the delivery of the day used:
``prompts.txt``, ``prompt.txt``, ``PROMPT.md``, or — when the root carries only
``prompts.json`` — ``data/instruction.md``, which the repackager writes from the
same source. All four hold the same body: ``--- TURN T<n> (Day d, HH:MM) ---``
blocks that :func:`src.utils.inject_director.parse_prompts_file` reads.

The HEADER is where the corpus diverges. Across 126 shipped bundles the same
five fields appear under four dialects — ``# turn_count:``, ``# turn count:``,
``# turns:``, and the whole block written without the ``#`` prefix — and the
window's ``(N days)`` parenthetical is written by hand, so it drifts: willie's
says ``(6 days)`` for ``2026-10-14 to 2026-10-20``, which spans 7. Everything
here funnels those dialects into the one block
:func:`src.utils.task_standard.render_prompt_header` defines, recomputing the
day count from the dates rather than carrying the written one across.

The turn BODIES are never rewritten. Output is the canonical header followed by
the source text from its first TURN line onward, byte for byte, so a
reconstruction cannot quietly reword a turn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.task_standard import (
    PROMPT_HEADER_KEYS,
    TaskWindow,
    render_prompt_header,
)

#: Root-level prompt files, most authoritative first. Mirrors the loader's own
#: order (task_parser prefers prompts.txt over prompt.txt over PROMPT.md).
PROMPT_CANDIDATES: tuple[str, ...] = ("prompts.txt", "prompt.txt", "PROMPT.md")

#: Bundles that ship only prompts.json at the root still carry the rendered
#: turns here, header included. HarnessV2 bundles place it under
#: data/solution/; older bundles at the data/ root. Newest layout first.
INSTRUCTION_FALLBACKS: tuple[tuple[str, ...], ...] = (
    ("data", "solution", "instruction.md"),
    ("data", "instruction.md"),
)
INSTRUCTION_FALLBACK: tuple[str, ...] = INSTRUCTION_FALLBACKS[-1]

#: The delimiter every shipped prompt file uses, matched exactly as the loader
#: matches it so a file that parses here parses there.
TURN_RE = re.compile(r"^---\s*TURN\s+T?(\d+)\b.*?---\s*$", re.IGNORECASE)

#: A defensive second delimiter: markdown-headed turns have been authored by
#: hand even though no shipped bundle uses them.
MD_TURN_RE = re.compile(r"^#{2,3}\s*Turn\s+T?(\d+)\b.*$", re.IGNORECASE)

#: The narrative instant a turn header labels itself with.
LABEL_RE = re.compile(r"\(\s*Day\s+(\d+)\s*,\s*(\d{1,2}:\d{2})\s*\)", re.IGNORECASE)

#: Header lines in any dialect: optional '#', key that may contain spaces.
_ANY_HEADER_RE = re.compile(r"^\s*(#\s*)?([A-Za-z_][A-Za-z0-9 _-]*?)\s*:\s*(.*?)\s*$")

#: Every spelling of the five mandated keys seen in the corpus.
_KEY_ALIASES = {
    "task_id": "task_id", "taskid": "task_id", "task id": "task_id",
    "task": "task_id",
    "persona": "persona", "user": "persona",
    "timezone": "timezone", "time zone": "timezone", "tz": "timezone",
    "window": "window",
    "turn_count": "turn_count", "turn count": "turn_count",
    "turncount": "turn_count", "turns": "turn_count",
}

_INT_RE = re.compile(r"\d+")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: The hand-written day count, in every parenthetical form the corpus uses:
#: ``(6 days)``, ``(3 simulated days)``, ``(3 days, 7 turns)``.
_WINDOW_DAYS_RE = re.compile(r"\((\d+)\s+(?:simulated\s+)?days?\b", re.IGNORECASE)


@dataclass
class Turn:
    """One ``--- TURN T<n> ---`` block: its index, its label, its body."""

    index: int
    text: str
    day: int | None = None
    time: str | None = None


@dataclass
class PromptSource:
    """The prompt file a bundle was found to publish."""

    path: Path
    text: str
    #: Path relative to the bundle root, for the provenance log.
    rel: str


@dataclass
class PromptRecovery:
    """A normalised prompt file plus the record of what had to be changed."""

    text: str
    turns: list[Turn]
    header: dict[str, str]
    source: PromptSource
    fixes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def locate_prompt_file(bundle: Path) -> PromptSource | None:
    """Find the bundle's prompt text, preferring the root over instruction.md."""
    for name in PROMPT_CANDIDATES:
        p = bundle / name
        if p.is_file():
            return PromptSource(p, p.read_text(encoding="utf-8"), name)
    for parts in INSTRUCTION_FALLBACKS:
        p = bundle.joinpath(*parts)
        if p.is_file():
            return PromptSource(p, p.read_text(encoding="utf-8"), "/".join(parts))
    return None


def _turn_match(line: str) -> re.Match[str] | None:
    stripped = line.strip()
    return TURN_RE.match(stripped) or MD_TURN_RE.match(stripped)


def parse_turns(text: str) -> list[Turn]:
    """Split prompt text into turns, ordered by declared index.

    Comment lines are dropped and bodies stripped exactly as
    ``inject_director.parse_prompts_file`` does, so a turn compared against the
    loader's own reading compares equal.
    """
    bodies: dict[int, list[str]] = {}
    labels: dict[int, tuple[int, str]] = {}
    current: int | None = None
    for line in text.splitlines():
        m = _turn_match(line)
        if m:
            current = int(m.group(1))
            bodies.setdefault(current, [])
            lm = LABEL_RE.search(line)
            if lm:
                labels[current] = (int(lm.group(1)), lm.group(2))
            continue
        if current is None or line.strip().startswith("#"):
            continue
        bodies[current].append(line)
    out: list[Turn] = []
    for idx in sorted(bodies):
        day, time = labels.get(idx, (None, None))
        out.append(Turn(idx, "\n".join(bodies[idx]).strip(), day, time))
    return out


def split_header_body(text: str) -> tuple[str, str]:
    """Return ``(header region, body)`` split at the first turn delimiter.

    A file with no delimiter at all is a single-turn prompt: everything past
    its header is one turn, and it is given the delimiter it lacks. Writing it
    to prompts.txt without one would leave the loader with zero turns.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _turn_match(line):
            return "".join(lines[:i]), "".join(lines[i:])
    header, rest = _split_leading_header(lines)
    body = rest.strip("\n")
    return header, f"--- TURN T0 ---\n{body}\n" if body else ""


def _split_leading_header(lines: list[str]) -> tuple[str, str]:
    for i, line in enumerate(lines):
        if line.strip() and not _ANY_HEADER_RE.match(line):
            return "".join(lines[:i]), "".join(lines[i:])
    return "".join(lines), ""


def parse_any_header(header_text: str) -> tuple[dict[str, str], list[str]]:
    """Read a header block in any dialect into canonical keys.

    Returns the fields plus the dialect notes worth logging — which spelling
    each key arrived under, so the provenance log names what was rewritten.
    """
    found: dict[str, str] = {}
    notes: list[str] = []
    for line in header_text.splitlines():
        if not line.strip():
            continue
        m = _ANY_HEADER_RE.match(line)
        if not m:
            continue
        raw_key = m.group(2).strip()
        key = _KEY_ALIASES.get(raw_key.casefold())
        if key is None or key in found:
            continue
        found[key] = m.group(3).strip()
        written = ("# " if m.group(1) else "") + raw_key
        if written != f"# {key}":
            notes.append(f"header key {written!r} -> '# {key}'")
    return found, notes


def declared_turn_count(value: str) -> int | None:
    """The leading integer of a turn-count field (``9 across 4 days`` -> 9)."""
    m = _INT_RE.search(value or "")
    return int(m.group(0)) if m else None


def window_from_header(value: str, timezone: str = "") -> TaskWindow | None:
    """Read the two dates out of a window field in any of its written forms.

    ``to``/``through``, with or without a ``(N days)`` or ``(N simulated days)``
    parenthetical, all reduce to the same pair. The parenthetical itself is
    deliberately ignored: it is the field that drifts.
    """
    dates = _DATE_RE.findall(value or "")
    if not dates:
        return None
    start = _as_date(dates[0])
    end = _as_date(dates[1]) if len(dates) > 1 else start
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    return TaskWindow(start, end, timezone, "prompt-header")


def _as_date(text: str):
    from datetime import datetime

    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalise(source: PromptSource, *, task_id: str, persona: str = "",
              timezone: str = "", window: TaskWindow | None = None,
              ) -> PromptRecovery:
    """Rebuild ``source`` with a canonical header over untouched turn bodies.

    Caller-supplied values win over the written header only where the header is
    silent; where both speak and disagree the disagreement is logged, because a
    bundle's own header is evidence even when it is wrong.
    """
    header_text, body = split_header_body(source.text)
    found, fixes = parse_any_header(header_text)
    turns = parse_turns(body)
    unresolved: list[str] = []
    if body and not any(_turn_match(line) for line in source.text.splitlines()):
        fixes.append("plain prompt wrapped as '--- TURN T0 ---' so it loads as one turn")

    resolved_id = found.get("task_id") or task_id
    resolved_persona = found.get("persona") or persona
    resolved_tz = found.get("timezone") or timezone
    if not resolved_persona:
        unresolved.append("persona: absent from the bundle's prompt header")
    if not resolved_tz:
        unresolved.append(
            "timezone: absent from the bundle's prompt header and not "
            "recoverable from persona prose — pass --timezone to supply it")

    win = window_from_header(found.get("window", ""), resolved_tz) or window
    if win is None:
        unresolved.append("window: no dates in the header and no turn instants")
    elif "window" in found:
        m = _WINDOW_DAYS_RE.search(found["window"])
        stated = int(m.group(1)) if m else None
        if stated is None:
            fixes.append(f"window gained the '({win.days} days)' span it omitted")
        elif stated != win.days:
            fixes.append(
                f"window said ({stated} days) but {win.start.isoformat()}.."
                f"{win.end.isoformat()} spans {win.days}; recomputed")

    declared = declared_turn_count(found.get("turn_count", ""))
    if declared is not None and declared != len(turns):
        fixes.append(f"header turn_count {declared} -> {len(turns)} actual turns")

    if not found:
        fixes.append("prompt file shipped no header block; one was synthesised")

    if win is None:
        block = _partial_header(resolved_id, resolved_persona, resolved_tz,
                                found.get("window", ""), len(turns))
    else:
        block = render_prompt_header(resolved_id, resolved_persona, resolved_tz,
                                     win, len(turns))
    text = block + "\n" + body.lstrip("\n") if body else block
    header = dict(zip(PROMPT_HEADER_KEYS, _header_values(block)))
    return PromptRecovery(text, turns, header, source, fixes, unresolved)


def _partial_header(task_id: str, persona: str, timezone: str, window: str,
                    turn_count: int) -> str:
    """Render the block when no window could be resolved, keeping key order."""
    return (
        f"# task_id: {task_id}\n"
        f"# persona: {persona}\n"
        f"# timezone: {timezone}\n"
        f"# window: {window}\n"
        f"# turn_count: {turn_count}\n"
    )


def _header_values(block: str) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in block.splitlines() if ":" in line]
