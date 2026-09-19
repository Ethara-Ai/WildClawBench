"""Verbatim carry-over: where each published file goes, and which are refused."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import sources as S  # noqa: E402
from src.utils.task_standard import TRUTH_FILENAMES  # noqa: E402

PERSONA_FILES = ("AGENTS.md", "HEARTBEAT.md", "IDENTITY.md", "MEMORY.md",
                 "SOUL.md", "TOOLS.md", "USER.md")
ARTIFACTS = ("home/Desktop/img_1.png", "home/Documents/data_1.tsv",
             "home/Library/xlsx_3.xlsx")


def _bundle(tmp_path: Path, *, truth_at=None, artifacts=ARTIFACTS) -> Path:
    b = tmp_path / "bundle"
    files = b.joinpath(*S.ARTIFACTS_SUBPATH)
    for rel in artifacts:
        p = files / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel, encoding="utf-8")
    persona = b.joinpath(*S.PERSONA_SUBPATH)
    persona.mkdir(parents=True, exist_ok=True)
    for name in PERSONA_FILES:
        (persona / name).write_text(name, encoding="utf-8")
    (b / "rubric.json").write_text('[{"c": 1}]', encoding="utf-8")
    tests = b / "data" / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_outputs.py").write_text("def test_x(): pass", encoding="utf-8")
    (tests / "test_weights.json").write_text("{}", encoding="utf-8")
    stage = b / "inject" / "stage1"
    stage.mkdir(parents=True)
    (stage / "mutations.json").write_text("{}", encoding="utf-8")
    if truth_at:
        p = b / truth_at
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# TRUTH\n", encoding="utf-8")
    return b


def test_attachments_keep_their_relative_paths(tmp_path):
    """storedAs is 'home/<path under data/>', so the home/ segment must stay."""
    out = tmp_path / "out"
    carried = S.recover_data(_bundle(tmp_path), out)
    assert carried.names == sorted(ARTIFACTS)
    for rel in ARTIFACTS:
        assert (out / "data" / rel).read_text(encoding="utf-8") == rel


def test_attachments_are_recovered_recursively(tmp_path):
    """A flat copy sees only the single home/ directory and recovers nothing."""
    out = tmp_path / "out"
    assert len(S.recover_data(_bundle(tmp_path), out)) == len(ARTIFACTS)


def test_persona_is_carried_whole(tmp_path):
    out = tmp_path / "out"
    carried = S.recover_persona(_bundle(tmp_path), out)
    assert sorted(carried.names) == sorted(PERSONA_FILES)
    assert len(carried) == S.PERSONA_FILE_COUNT


@pytest.mark.parametrize("location", [
    "TRUTH.md",
    "golden_steer_flow.md",
    "data/solution/TRUTH.md",
    "data/solution/golden_steer_flow.md",
])
def test_truth_is_found_in_either_place_under_either_name(tmp_path, location):
    b = _bundle(tmp_path, truth_at=location)
    assert S.find_truth(b) == b / location
    out = tmp_path / "out"
    assert S.recover_truth(b, out).names == [Path(location).name]


def test_root_truth_outranks_the_solution_copy(tmp_path):
    b = _bundle(tmp_path, truth_at="TRUTH.md")
    (b / "data" / "solution").mkdir(parents=True, exist_ok=True)
    (b / "data" / "solution" / "TRUTH.md").write_text("older", encoding="utf-8")
    assert S.find_truth(b) == b / "TRUTH.md"


def test_a_bundle_without_truth_reports_it_rather_than_inventing_one(tmp_path):
    b = _bundle(tmp_path)
    assert S.find_truth(b) is None
    assert S.recover_truth(b, tmp_path / "out").names == []


@pytest.mark.parametrize("name", TRUTH_FILENAMES)
def test_every_standard_truth_name_is_probed(tmp_path, name):
    assert S.find_truth(_bundle(tmp_path, truth_at=name)) is not None


def test_generated_tests_are_not_written_back(tmp_path):
    """The channel test_outputs.py feeds is retired; restoring it revives it."""
    out = tmp_path / "out"
    S.recover_all(_bundle(tmp_path), out)
    assert not (out / "test_outputs.py").exists()
    assert not (out / "test_weights.json").exists()
    assert not (out / "data" / "tests").exists()


def test_inject_is_staged_verbatim(tmp_path):
    out = tmp_path / "out"
    assert S.recover_inject(_bundle(tmp_path), out).names == ["stage1/mutations.json"]
    assert (out / "inject" / "stage1" / "mutations.json").is_file()


def test_junk_files_are_skipped(tmp_path):
    b = _bundle(tmp_path)
    (b.joinpath(*S.PERSONA_SUBPATH) / ".DS_Store").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    assert ".DS_Store" not in S.recover_persona(b, out).names


def test_absent_sources_carry_nothing_and_do_not_raise(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out"
    assert all(len(c) == 0 for c in S.recover_all(empty, out))


def test_recover_all_reports_every_surface(tmp_path):
    labels = [c.label for c in S.recover_all(_bundle(tmp_path), tmp_path / "out")]
    assert labels == ["rubric.json", "TRUTH.md", "persona/", "data/", "inject/"]
