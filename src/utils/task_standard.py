"""The three machine-checkable task-format standards, in one place.

Every new task must satisfy all three; ``script/preflight_task.py`` enforces
them and ``script/compile_declarative_task.py`` emits them, so generator and
validator cannot drift:

A. **Derived date.** A task's "today" is never hard-coded — it comes from the
   task's own declared window (see :func:`resolve_window`). A bundle whose
   ``CURRENT_DATE`` sits outside the narrated window contradicts the prompt it
   ships with, which has already cost one delivery rework.
B. **TRUTH.md has exactly three sections** — Focal Event, Canonical Solve
   Path, Value Lock — in that order, with nothing else at the top level.
C. **The prompt text opens with a five-line header block** naming the task,
   persona, timezone, window and turn count, so the file is self-describing
   and cross-checkable against prompts.json.

The module is import-light (stdlib only) because preflight runs offline with
no Docker and no LLM.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

__all__ = [
    "TRUTH_SECTIONS", "TRUTH_FILENAMES", "PROMPT_HEADER_KEYS", "PROMPT_FILENAMES",
    "TaskWindow", "resolve_window", "window_from_declaration",
    "window_from_instants", "truth_sections", "check_truth_sections",
    "render_truth_skeleton", "render_prompt_header", "parse_prompt_header",
    "check_prompt_header", "check_current_date",
]

# --------------------------------------------------------------------------- #
# Standard A — the task's own time window
# --------------------------------------------------------------------------- #


class TaskWindow:
    """A task's narrated date span, inclusive on both ends."""

    __slots__ = ("start", "end", "timezone", "source")

    def __init__(self, start: date, end: date, timezone: str = "", source: str = "") -> None:
        self.start = start
        self.end = end
        self.timezone = timezone
        self.source = source

    @property
    def days(self) -> int:
        """Inclusive day span — a one-day window is 1 day, not 0."""
        return (self.end - self.start).days + 1

    @property
    def current_date(self) -> str:
        """The derivation rule: a task's "today" is its window's first day.

        The window start is the only instant every task declares (it is where
        turn T0 lands), so it is the one choice that can never disagree with
        the narrative. Later turns advance the agent's simulated clock from
        here; they do not move the environment's baseline date.
        """
        return self.start.isoformat()

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (f"TaskWindow({self.start.isoformat()}..{self.end.isoformat()}, "
                f"{self.days}d, src={self.source!r})")


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _as_date(value: object) -> date | None:
    """Coerce a date-ish declaration to a ``date`` (ISO prefix wins)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    m = _DATE_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _window_from_field(window: object) -> tuple[date | None, date | None, str]:
    """Read a ``window`` declaration in either supported shape.

    dict form ``{"start": ..., "end": ..., "timezone": ...}`` and string form
    ``"2026-10-06 to 2026-10-11"`` both ship in the corpus.
    """
    if isinstance(window, dict):
        tz = str(window.get("timezone") or "").strip()
        return _as_date(window.get("start")), _as_date(window.get("end")), tz
    if isinstance(window, str) and window.strip():
        found = _DATE_RE.findall(window)
        start = _as_date(found[0]) if found else None
        end = _as_date(found[1]) if len(found) > 1 else None
        return start, end, ""
    return None, None, ""


def window_from_declaration(value: object, timezone: str = "",
                            source: str = "declaration") -> TaskWindow | None:
    """Build a window from a ``window`` declaration in either shape.

    A declaration naming only one date is a single-day window.
    """
    start, end, win_tz = _window_from_field(value)
    if start is None:
        return None
    return TaskWindow(start, end or start, timezone or win_tz, source)


def window_from_instants(values: object, timezone: str = "",
                         source: str = "instants") -> TaskWindow | None:
    """Build the window spanning every resolvable date in ``values``."""
    days = [d for d in (_as_date(v) for v in (values or ())) if d is not None]
    if not days:
        return None
    return TaskWindow(min(days), max(days), timezone, source)


def _turn_dates(turns: object) -> list[date]:
    """Every resolvable calendar date the turn schedule lands on."""
    out: list[date] = []
    if not isinstance(turns, list):
        return out
    for turn in turns:
        if isinstance(turn, dict):
            day = _as_date(turn.get("timestamp"))
            if day is not None:
                out.append(day)
    return out


def resolve_window(task_dir: Path) -> TaskWindow | None:
    """Resolve a task's inclusive date window, or None if it declares none.

    Precedence, most authoritative first:

    1. ``prompts.json`` ``window`` (the trajectory is the source of truth),
       with either end filled in from the turn timestamps when absent.
    2. The span of the ``prompts.json`` turn timestamps themselves.
    3. ``task.yaml``'s ``window`` key, for tasks authored without a schedule.
    """
    data: dict = {}
    pj = Path(task_dir) / "prompts.json"
    if pj.is_file():
        try:
            loaded = json.loads(pj.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}

    start, end, win_tz = _window_from_field(data.get("window"))
    tz = str(data.get("timezone") or "").strip() or win_tz
    stamps = _turn_dates(data.get("turns"))
    source = "prompts.json:window"
    if start is None and stamps:
        start, source = min(stamps), "prompts.json:turns"
    if end is None and stamps:
        end = max(stamps)
    if start is not None and end is None:
        end = start
    if start is None:
        start, end, tz, source = _window_from_task_yaml(task_dir, tz)
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    return TaskWindow(start, end, tz, source)


_YAML_WINDOW_RE = re.compile(r"^window:\s*(.+)$", re.MULTILINE)
_YAML_TZ_RE = re.compile(r"^timezone:\s*(\S+)\s*$", re.MULTILINE)


def _window_from_task_yaml(task_dir: Path, tz: str):
    """Last-resort window read straight off task.yaml (regex, no PyYAML dep)."""
    ty = Path(task_dir) / "task.yaml"
    if not ty.is_file():
        return None, None, tz, ""
    try:
        text = ty.read_text(encoding="utf-8")
    except OSError:
        return None, None, tz, ""
    m = _YAML_WINDOW_RE.search(text)
    if not m:
        return None, None, tz, ""
    start, end, _ = _window_from_field(m.group(1).strip().strip("'\""))
    tz_m = _YAML_TZ_RE.search(text)
    return start, end or start, tz or (tz_m.group(1) if tz_m else ""), "task.yaml:window"


def check_current_date(value: object, window: TaskWindow | None) -> str | None:
    """Return an error string when ``value`` is not a date inside ``window``.

    Absent, unparseable and out-of-window dates are all failures: each one is a
    bundle asserting a "today" its own prompts contradict.
    """
    if window is None:
        return "task declares no resolvable window, so its date cannot be derived"
    day = _as_date(value)
    if day is None:
        text = str(value).strip() if value not in (None, "") else "<absent>"
        return f"CURRENT_DATE {text!r} is absent or not a YYYY-MM-DD date"
    if not window.contains(day):
        return (f"CURRENT_DATE {day.isoformat()} is outside the task window "
                f"{window.start.isoformat()}..{window.end.isoformat()} — "
                f"hard-coded dates contradict the narrative")
    return None


# --------------------------------------------------------------------------- #
# Standard B — TRUTH.md's three sections
# --------------------------------------------------------------------------- #

#: The pilot-rework section set, in mandatory order.
TRUTH_SECTIONS = ("Focal Event", "Canonical Solve Path", "Value Lock")

#: ``TRUTH.md`` is the standard name; the pilot shipped under the older one.
TRUTH_FILENAMES = ("TRUTH.md", "golden_steer_flow.md")

#: Authors number the sections ("## 2. Canonical Solve Path") and may qualify
#: the first ("...and Scope"); both stay canonical once normalised.
_HEADING_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$", re.MULTILINE)
_SECTION_NUM_RE = re.compile(r"^\d+[.)]\s*")


def _canonical_section(heading: str) -> str:
    """Map a written heading onto its canonical name, or return it verbatim."""
    bare = _SECTION_NUM_RE.sub("", heading.strip()).strip()
    folded = bare.casefold()
    for name in TRUTH_SECTIONS:
        if folded == name.casefold() or folded.startswith(name.casefold() + " "):
            return name
    return bare


def truth_sections(text: str) -> list[str]:
    """The canonicalised ``##`` section names of a TRUTH.md, in order.

    Only level-2 headings count as sections: the file opens with a level-1
    document title, which is decoration rather than structure.
    """
    return [_canonical_section(h) for h in _HEADING_RE.findall(text)]


#: What each section is for, so a generated skeleton is self-explaining.
_TRUTH_PROMPTS = {
    "Focal Event": "What happens, to whom, and the in-world scope boundary that\n"
                   "separates it from the distractor material.",
    "Canonical Solve Path": "The ordered steps a correct solve takes, and the\n"
                            "observation that forces each one.",
    "Value Lock": "The exact values grading pins — figures, identifiers, file\n"
                  "paths — and where each is written.",
}


def render_truth_skeleton(task_id: str, window: TaskWindow | None = None) -> str:
    """Render a TRUTH.md carrying exactly the three mandated sections."""
    lines = [f"# TRUTH — {task_id}", ""]
    if window is not None:
        lines += [f"Window: {window.start.isoformat()} to {window.end.isoformat()} "
                  f"({window.days} days).", ""]
    for i, name in enumerate(TRUTH_SECTIONS, start=1):
        lines += [f"## {i}. {name}", "", _TRUTH_PROMPTS[name], ""]
    return "\n".join(lines).rstrip() + "\n"


def check_truth_sections(text: str) -> str | None:
    """Return an error string unless the file has exactly the 3 sections."""
    found = truth_sections(text)
    if found == list(TRUTH_SECTIONS):
        return None
    missing = [s for s in TRUTH_SECTIONS if s not in found]
    extra = [s for s in found if s not in TRUTH_SECTIONS]
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"unexpected {extra}")
        return f"TRUTH.md sections {'; '.join(parts)} (found {found})"
    return f"TRUTH.md sections out of order: {found} != {list(TRUTH_SECTIONS)}"


# --------------------------------------------------------------------------- #
# Standard C — the five-line prompt header block
# --------------------------------------------------------------------------- #

#: Mandatory keys, in mandatory order.
PROMPT_HEADER_KEYS = ("task_id", "persona", "timezone", "window", "turn_count")

#: The rendered trajectory ships as prompts.txt; prompt.txt is the older name.
PROMPT_FILENAMES = ("prompts.txt", "prompt.txt")

_HEADER_LINE_RE = re.compile(r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_WINDOW_VALUE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s+\((\d+)\s+days?\)$")


def render_prompt_header(task_id: str, persona: str, timezone: str,
                         window: TaskWindow, turn_count: int) -> str:
    """Render the canonical five-line block, trailing newline included."""
    return (
        f"# task_id: {task_id}\n"
        f"# persona: {persona}\n"
        f"# timezone: {timezone}\n"
        f"# window: {window.start.isoformat()} to {window.end.isoformat()} "
        f"({window.days} days)\n"
        f"# turn_count: {turn_count}\n"
    )


def parse_prompt_header(text: str) -> dict[str, str]:
    """Read the leading ``# key: value`` lines as a dict (order preserved)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _HEADER_LINE_RE.match(line)
        if not m:
            break
        out[m.group(1)] = m.group(2).strip()
    return out


def check_prompt_header(text: str, *, task_id: str = "", persona: str = "",
                        timezone: str = "", window: TaskWindow | None = None,
                        turn_count: int | None = None) -> list[str]:
    """Return every way the prompt file's header block breaks the standard.

    Cross-checks are skipped for whichever expected values the caller could not
    determine, so a task that declares less still gets its format checked.
    """
    errors: list[str] = []
    found = parse_prompt_header(text)
    keys = list(found)[:len(PROMPT_HEADER_KEYS)]
    if keys != list(PROMPT_HEADER_KEYS):
        return [f"header must open with exactly {list(PROMPT_HEADER_KEYS)} as "
                f"'# key: value' lines, found {keys or '<none>'}"]

    for key, expected in (("task_id", task_id), ("persona", persona),
                          ("timezone", timezone)):
        actual = found[key]
        if not actual:
            errors.append(f"header {key} is empty")
        elif expected and actual != expected:
            errors.append(f"header {key} {actual!r} != task's {expected!r}")

    errors.extend(_check_window_line(found["window"], window))

    declared = found["turn_count"]
    if not declared.isdigit():
        errors.append(f"header turn_count {declared!r} is not a bare integer")
    elif turn_count is not None and int(declared) != turn_count:
        errors.append(f"header turn_count {declared} != {turn_count} actual turns")
    return errors


def _check_window_line(value: str, window: TaskWindow | None) -> list[str]:
    m = _WINDOW_VALUE_RE.match(value)
    if not m:
        return [f"header window {value!r} must read "
                f"'<YYYY-MM-DD> to <YYYY-MM-DD> (<N> days)'"]
    start, end, days = _as_date(m.group(1)), _as_date(m.group(2)), int(m.group(3))
    errors: list[str] = []
    if start is None or end is None or end < start:
        return [f"header window {value!r} does not span forwards in time"]
    span = (end - start).days + 1
    if days != span:
        errors.append(f"header window says {days} days but "
                      f"{start.isoformat()}..{end.isoformat()} spans {span} "
                      f"(inclusive of both ends)")
    if window is not None and (start, end) != (window.start, window.end):
        errors.append(f"header window {start.isoformat()}..{end.isoformat()} != "
                      f"task's {window.start.isoformat()}..{window.end.isoformat()}")
    return errors
