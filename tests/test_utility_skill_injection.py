"""inject_api_connectors: utility skills are NOT keyed by required_apis.

A task with no required/distractor APIs (a pure audio or PDF task) used to hit
an early return and get no audio-extract / pdf-extract skill and no runtime-dep
install, while the comment a few lines below said those skills "are NOT keyed by
required_apis". Only the connectors and API_DOCUMENTATION.md depend on the list.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import docker_utils as du  # noqa: E402


@pytest.fixture
def env_dir(tmp_path):
    skills = tmp_path / "skills"
    for name in ("audio-extract", "pdf-extract", "zendesk-api-connector", "jira-api-connector"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(name, encoding="utf-8")
    (tmp_path / "API_DOCUMENTATION.md").write_text("# apis", encoding="utf-8")
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(du.subprocess, "run", fake_run)
    deps: list[str] = []
    monkeypatch.setattr(du, "_install_skill_runtime_deps", lambda *a, **k: deps.append("install"))
    monkeypatch.setattr(du, "_verify_skill_runtime_deps", lambda *a, **k: deps.append("verify"))
    return SimpleNamespace(cmds=seen, deps=deps)


def _copied(calls) -> list[str]:
    return [c[2].rsplit("/", 2)[-2] for c in calls.cmds if c[:2] == ["docker", "cp"]
            and c[2].endswith("/.")]


def test_api_less_task_still_gets_utility_skills_and_deps(env_dir, calls):
    du.inject_api_connectors("t1", str(env_dir), [])
    assert _copied(calls) == ["audio-extract", "pdf-extract"]
    assert calls.deps == ["install", "verify"]


def test_api_less_task_gets_no_connector_and_no_fleet_api_doc(env_dir, calls):
    du.inject_api_connectors("t1", str(env_dir), [])
    flat = [" ".join(c) for c in calls.cmds]
    assert not any("-connector" in c for c in flat)
    assert not any("API_DOCUMENTATION.md" in c for c in flat)


def test_none_is_treated_like_an_empty_list(env_dir, calls):
    du.inject_api_connectors("t1", str(env_dir), None)
    assert _copied(calls) == ["audio-extract", "pdf-extract"]


def test_task_with_apis_is_unchanged(env_dir, calls):
    du.inject_api_connectors("t1", str(env_dir), ["zendesk-api"])
    assert _copied(calls) == ["zendesk-api-connector", "audio-extract", "pdf-extract"]
    assert any("API_DOCUMENTATION.md" in " ".join(c) for c in calls.cmds)
    assert calls.deps == ["install", "verify"]


def test_missing_env_dir_is_still_a_no_op(calls):
    du.inject_api_connectors("t1", "", ["zendesk-api"])
    assert calls.cmds == [] and calls.deps == []
