"""task.yaml / task.json rebuilt from task.toml, including what it cannot say."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import metadata as M  # noqa: E402
from src.utils.task_parser import _load_native_api_overrides  # noqa: E402

SCOPED_TOML = """\
schema_version = "1.1"

[metadata]
category = "published_campaign_conformance_audit"
difficulty = "hard"
required_skills = ["instagram-api-connector", "trello-api-connector"]
distractor_skills = ["gmail-api-connector", "slack-api-connector"]

[multimodal]
dependency_tags = ["creative_media", "social_media_content_audit"]

[dimensions]
complex = "medium"
"""

#: The shape every batch-1, batch-rework and delivery-1 bundle ships: the whole
#: fleet listed as required, nothing listed as a distractor.
FLEET_TOML = """\
[metadata]
category = ""
required_skills = ["gmail-api-connector", "slack-api-connector", "trello-api-connector"]
distractor_skills = []

[multimodal]
dependency_tags = ["visual_learning", "homework_problem_solving"]
"""


def _bundle(tmp_path: Path, toml_text: str) -> Path:
    b = tmp_path / "bundle"
    (b / "data").mkdir(parents=True, exist_ok=True)
    (b / "data" / "task.toml").write_text(toml_text, encoding="utf-8")
    return b


def _out_with_data(tmp_path: Path, *names) -> Path:
    out = tmp_path / "out"
    (out / "data" / "home").mkdir(parents=True, exist_ok=True)
    for name in names:
        (out / "data" / "home" / name).write_bytes(b"x")
    return out


def test_apis_come_off_required_skills_without_the_connector_suffix(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path))
    assert meta.required_apis == ["instagram-api", "trello-api"]
    assert meta.distractor_apis == ["gmail-api", "slack-api"]


def test_a_fleet_wide_required_list_is_refused(tmp_path):
    """No distractors means required_skills lists the image, not the task."""
    meta = M.derive(_bundle(tmp_path, FLEET_TOML), _out_with_data(tmp_path))
    assert meta.required_apis == []
    assert meta.distractor_apis == M.AUTO
    assert any("whole shipped fleet" in n for n in meta.notes)


def test_taxonomy_comes_from_the_multimodal_dependency_tags(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path))
    assert (meta.l1, meta.l2) == ("creative_media", "social_media_content_audit")


def test_taxonomy_falls_back_to_dimensions_then_to_the_loader(tmp_path):
    meta = M.derive(_bundle(tmp_path, '[dimensions]\ndependency_tags = ["a", "b"]\n'),
                    _out_with_data(tmp_path))
    assert (meta.l1, meta.l2) == ("a", "b")
    bare = M.derive(_bundle(tmp_path, "[metadata]\n"), _out_with_data(tmp_path))
    assert bare.l1 == ""
    assert any("derives them from the rubric" in n for n in bare.notes)


def test_task_type_and_difficulty_are_carried_when_declared(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path))
    assert meta.task_type == "published_campaign_conformance_audit"
    assert meta.difficulty == "hard"


def test_an_empty_category_is_reported_not_faked(tmp_path):
    meta = M.derive(_bundle(tmp_path, FLEET_TOML), _out_with_data(tmp_path))
    assert meta.task_type == ""
    assert any("category is empty" in n for n in meta.notes)


@pytest.mark.parametrize("files,expected", [
    (("a.png",), ["text", "image"]),
    (("a.mp3",), ["text", "audio"]),
    (("a.mp4",), ["text", "video"]),
    (("a.png", "b.mp3", "c.mp4"), ["text", "audio", "image", "video"]),
    (("a.pdf",), ["text"]),
])
def test_modalities_are_scanned_off_the_recovered_attachments(tmp_path, files, expected):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path, *files))
    assert meta.modalities == expected


def test_no_attachments_declares_no_modalities(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert M.derive(_bundle(tmp_path, SCOPED_TOML), out).modalities == []


def test_only_modalities_preflight_can_verify_are_declared(tmp_path):
    """preflight warns on any modality outside image/audio/video."""
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML),
                    _out_with_data(tmp_path, "a.docx", "b.html", "c.png"))
    assert set(meta.modalities) <= set(M.MODALITY_MIME_PREFIX) | {"text"}


def test_task_yaml_writes_api_names_the_way_the_corpus_does(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path, "a.png"))
    text = M.render_task_yaml(meta)
    assert "required_apis: [instagram, trello]" in text
    assert "distractor_apis: [gmail, slack]" in text


def test_task_yaml_says_auto_when_distractors_are_unknown(tmp_path):
    meta = M.derive(_bundle(tmp_path, FLEET_TOML), _out_with_data(tmp_path))
    text = M.render_task_yaml(meta)
    assert "distractor_apis: auto" in text
    assert "required_apis:" not in text


def test_task_json_is_what_the_loader_reads_back(tmp_path):
    out = _out_with_data(tmp_path, "a.png")
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), out)
    M.write(meta, out)
    declared = _load_native_api_overrides(out)
    assert declared["required_apis"] == ["instagram-api", "trello-api"]
    assert declared["distractor_apis"] == ["gmail-api", "slack-api"]


def test_both_files_agree_so_their_precedence_never_matters(tmp_path):
    out = _out_with_data(tmp_path, "a.png")
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), out)
    M.write(meta, out)
    from_json = json.loads((out / "task.json").read_text(encoding="utf-8"))
    yaml_text = (out / "task.yaml").read_text(encoding="utf-8")
    assert M.bare(from_json["required_apis"]) == ["instagram", "trello"]
    assert f"required_apis: [{', '.join(M.bare(from_json['required_apis']))}]" in yaml_text


def test_scoped_apis_is_the_union_an_overlay_may_touch(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path))
    assert meta.scoped_apis == {"instagram-api", "trello-api", "gmail-api", "slack-api"}


def test_an_unreadable_task_toml_leaves_everything_to_the_loader(tmp_path):
    meta = M.derive(_bundle(tmp_path, "not = [valid"), _out_with_data(tmp_path))
    assert meta.required_apis == []
    assert meta.l1 == ""
    assert meta.notes


def test_system_prompt_is_carried_when_the_run_recorded_one(tmp_path):
    meta = M.derive(_bundle(tmp_path, SCOPED_TOML), _out_with_data(tmp_path),
                    system_prompt="be terse")
    assert "system_prompt: " in M.render_task_yaml(meta)
    assert meta.system_prompt == "be terse"
