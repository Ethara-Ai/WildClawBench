from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.rubric_targets import FILE_TARGETS, normalize_target  # noqa: E402


def test_canonical_is_workspace_artifact():
    assert FILE_TARGETS == frozenset({"workspace_artifact"})


@pytest.mark.parametrize("alias", ["file_output", "produced_file", "workspace"])
def test_aliases_map_to_canonical(alias):
    assert normalize_target(alias) == "workspace_artifact"
    assert normalize_target(alias) in FILE_TARGETS


@pytest.mark.parametrize("alias", ["File_Output", "  PRODUCED_FILE  ", "Workspace"])
def test_aliases_are_case_and_whitespace_insensitive(alias):
    assert normalize_target(alias) == "workspace_artifact"


@pytest.mark.parametrize(
    "value", ["final_answer", "trajectory", "state_change", "user_facing_message"]
)
def test_calibrated_targets_pass_through_and_are_not_file_targets(value):
    assert normalize_target(value) == value
    assert normalize_target(value) not in FILE_TARGETS


def test_workspace_artifact_passes_through_and_is_a_file_target():
    assert normalize_target("workspace_artifact") == "workspace_artifact"
    assert normalize_target("workspace_artifact") in FILE_TARGETS


@pytest.mark.parametrize("bad", [None, "", "   ", 5, 3.0, [], {}])
def test_non_str_and_empty_inputs_return_empty_never_raise(bad):
    assert normalize_target(bad) == "" or normalize_target(bad) not in FILE_TARGETS
    assert normalize_target(bad) != "workspace_artifact"


def test_unknown_value_passes_through_lowercased():
    assert normalize_target("Some_Novel_Target") == "some_novel_target"
    assert normalize_target("Some_Novel_Target") not in FILE_TARGETS
