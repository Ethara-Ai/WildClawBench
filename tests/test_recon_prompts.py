"""The prompt-header normaliser, exercised against every shipped dialect.

Fixtures here are synthetic miniatures, not copies of delivered bundles: each
one reproduces exactly the shape a real corpus file has (the four header
dialects, the window parentheticals, the headerless file) at three turns
instead of twenty, so a failure names the dialect that broke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import prompts as P  # noqa: E402
from src.utils.inject_director import parse_prompts_file  # noqa: E402
from src.utils.task_standard import check_prompt_header  # noqa: E402

BODY = (
    "--- TURN T0 (Day 1, 08:12) ---\n"
    "morning. work out what went live.\n"
    "\n"
    "--- TURN T1 (Day 2, 09:40) ---\n"
    "quick one before standup.\n"
    "\n"
    "--- TURN T2 (Day 3, 11:05) ---\n"
    "last look before she reads it.\n"
)

CANONICAL = (
    "# task_id: t\n# persona: Willie Prince\n# timezone: Africa/Accra\n"
    "# window: 2026-10-14 to 2026-10-16 (3 days)\n# turn_count: 3\n"
)
SPACED_KEY = CANONICAL.replace("# turn_count:", "# turn count:")
OLD_KEY = CANONICAL.replace("# turn_count:", "# turns:")
BARE = CANONICAL.replace("# ", "")
WORDY_COUNT = CANONICAL.replace("# turn_count: 3", "# turns: 3 across 3 days")
DIALECTS = {
    "canonical": CANONICAL,
    "spaced-key": SPACED_KEY,
    "old-key": OLD_KEY,
    "bare": BARE,
    "wordy-count": WORDY_COUNT,
}


def _write(tmp_path: Path, name: str, header: str, body: str = BODY) -> Path:
    p = tmp_path / name
    p.write_text(header + "\n" + body, encoding="utf-8")
    return p


def _recover(tmp_path: Path, header: str, name: str = "prompt.txt"):
    _write(tmp_path, name, header)
    source = P.locate_prompt_file(tmp_path)
    return P.normalise(source, task_id="t")


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_every_header_dialect_normalises_to_the_standard(tmp_path, dialect):
    rec = _recover(tmp_path, DIALECTS[dialect])
    assert rec.text.startswith(CANONICAL)
    assert check_prompt_header(rec.text, task_id="t", turn_count=3) == []


@pytest.mark.parametrize("dialect", sorted(set(DIALECTS) - {"canonical"}))
def test_a_rewritten_key_is_logged(tmp_path, dialect):
    rec = _recover(tmp_path, DIALECTS[dialect])
    assert rec.fixes, f"{dialect} was rewritten silently"


def test_window_day_count_is_recomputed_not_copied(tmp_path):
    """willie's header claims 6 days for a span of 7; the dates win."""
    wrong = CANONICAL.replace("(3 days)", "(6 days)")
    rec = _recover(tmp_path, wrong)
    assert "# window: 2026-10-14 to 2026-10-16 (3 days)" in rec.text
    assert any("spans 3" in f for f in rec.fixes)


@pytest.mark.parametrize("written,expected", [
    ("2026-10-14 to 2026-10-16 (3 simulated days)", "(3 days)"),
    ("2026-10-14 through 2026-10-16", "(3 days)"),
    ("2026-10-14 to 2026-10-16 (3 days, 7 turns)", "(3 days)"),
    ("2026-10-14 to 2026-10-16", "(3 days)"),
])
def test_window_parentheticals_all_reduce_to_the_standard(tmp_path, written, expected):
    rec = _recover(tmp_path, CANONICAL.replace(
        "2026-10-14 to 2026-10-16 (3 days)", written))
    assert f"# window: 2026-10-14 to 2026-10-16 {expected}" in rec.text


def test_headerless_file_gets_a_header_and_names_what_is_missing(tmp_path):
    (tmp_path / "prompt.txt").write_text(BODY, encoding="utf-8")
    rec = P.normalise(P.locate_prompt_file(tmp_path), task_id="t")
    assert rec.text.startswith("# task_id: t\n")
    assert rec.header["turn_count"] == "3"
    assert any("persona" in u for u in rec.unresolved)
    assert any("timezone" in u for u in rec.unresolved)


def test_supplied_timezone_fills_a_headerless_file(tmp_path):
    (tmp_path / "prompt.txt").write_text(BODY, encoding="utf-8")
    rec = P.normalise(P.locate_prompt_file(tmp_path), task_id="t",
                      timezone="Africa/Accra")
    assert "# timezone: Africa/Accra" in rec.text
    assert not any("timezone" in u for u in rec.unresolved)


def test_turn_bodies_survive_normalisation_byte_for_byte(tmp_path):
    src = _write(tmp_path, "prompt.txt", SPACED_KEY)
    rec = P.normalise(P.locate_prompt_file(tmp_path), task_id="t")
    out = tmp_path / "prompts.txt"
    out.write_text(rec.text, encoding="utf-8")
    assert parse_prompts_file(out) == parse_prompts_file(src)


@pytest.mark.parametrize("name,expected", [
    ("prompts.txt", "prompts.txt"),
    ("prompt.txt", "prompt.txt"),
    ("PROMPT.md", "PROMPT.md"),
])
def test_prompt_file_is_found_under_each_published_name(tmp_path, name, expected):
    _write(tmp_path, name, CANONICAL)
    assert P.locate_prompt_file(tmp_path).rel == expected


def test_root_prompt_file_outranks_instruction_md(tmp_path):
    _write(tmp_path, "prompt.txt", CANONICAL)
    (tmp_path / "data").mkdir()
    _write(tmp_path / "data", "instruction.md", BARE)
    assert P.locate_prompt_file(tmp_path).rel == "prompt.txt"


def test_instruction_md_carries_a_json_only_bundle(tmp_path):
    """delivery-1 bundles publish prompts.json at the root and nothing else."""
    (tmp_path / "data").mkdir()
    _write(tmp_path / "data", "instruction.md", OLD_KEY)
    source = P.locate_prompt_file(tmp_path)
    assert source.rel == "data/instruction.md"
    assert len(P.normalise(source, task_id="t").turns) == 3


def test_turn_labels_are_read_off_the_delimiter(tmp_path):
    rec = _recover(tmp_path, CANONICAL)
    assert [(t.day, t.time) for t in rec.turns] == [
        (1, "08:12"), (2, "09:40"), (3, "11:05")]


def test_turns_are_ordered_by_index_not_file_position(tmp_path):
    scrambled = (
        "--- TURN T2 (Day 3, 11:05) ---\nthird.\n\n"
        "--- TURN T0 (Day 1, 08:12) ---\nfirst.\n\n"
        "--- TURN T1 (Day 2, 09:40) ---\nsecond.\n"
    )
    (tmp_path / "prompt.txt").write_text(scrambled, encoding="utf-8")
    rec = P.normalise(P.locate_prompt_file(tmp_path), task_id="t")
    assert [t.text for t in rec.turns] == ["first.", "second.", "third."]


def test_missing_prompt_file_is_reported_not_guessed(tmp_path):
    assert P.locate_prompt_file(tmp_path) is None
