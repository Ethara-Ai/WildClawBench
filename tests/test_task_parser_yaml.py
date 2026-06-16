"""task.yaml -> task dict: task_type (joined), task_description, system_prompt."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.task_parser import load_task  # noqa: E402


def _make_task(tmp_path: Path, yaml_body: str) -> Path:
    d = tmp_path / "MYTASK"
    d.mkdir()
    (d / "prompts.txt").write_text("--- TURN 1 ---\ndo it\n", encoding="utf-8")
    (d / "rubric.json").write_text("[]", encoding="utf-8")
    (d / "task.yaml").write_text(yaml_body, encoding="utf-8")
    return d


def test_task_yaml_fields_loaded(tmp_path: Path):
    d = _make_task(tmp_path,
        "task_type:\n  - Search & Retrieval\n  - Productivity Flow\n"
        "category: multi_turn_robustness\n"
        "task_description: An authored one-line description.\n"
        "system_prompt: |\n  You are the authored assistant for this task.\n")
    t = load_task(str(d))
    assert t["task_type"] == "Search & Retrieval, Productivity Flow"  # joined
    assert t["category"] == "multi_turn_robustness"                   # not overwritten
    assert t["task_description"] == "An authored one-line description."
    assert t["system_prompt"].startswith("You are the authored assistant")


def test_task_yaml_missing_fields_keep_defaults(tmp_path: Path):
    d = _make_task(tmp_path, "difficulty: hard\n")  # no type/desc/system_prompt
    t = load_task(str(d))
    assert t["system_prompt"] == ""          # default preserved
    assert t["task_type"] == ""              # default preserved
