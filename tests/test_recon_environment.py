"""Overlay isolation: scoped to the task, and refused when it cannot be proven."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import environment as E  # noqa: E402

SCOPE = {"gmail-api", "slack-api"}


def _tree(root: Path, spec: dict) -> Path:
    for rel, body in spec.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def _env(tmp_path: Path) -> Path:
    return _tree(tmp_path / "bundle" / "data" / "environment", {
        "gmail-api/messages.json": '{"m": "task"}',
        "gmail-api/labels.json": '{"l": "default"}',
        "gmail-api/new_thread.json": '{"n": 1}',
        "slack-api/channels.json": '{"c": "task"}',
        "stripe-api/charges.json": '{"s": "fleet"}',
        "gmail-api/notes.md": "not a seed",
    })


def _baseline(tmp_path: Path, include_slack: bool = True) -> Path:
    spec = {
        "gmail-api/messages.json": '{"m": "default"}',
        "gmail-api/labels.json": '{"l": "default"}',
        "stripe-api/charges.json": '{"s": "fleet"}',
    }
    if include_slack:
        spec["slack-api/channels.json"] = '{"c": "default"}'
    return _tree(tmp_path / "baseline", spec)


def test_only_declared_apis_are_searched(tmp_path):
    """The old loop walked the whole fleet, so a 2-API task reported stripe too."""
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", SCOPE)
    assert set(got.overlays) == SCOPE
    assert got.out_of_scope == ["stripe-api"]
    assert not (tmp_path / "mock" / "stripe-api").exists()


def test_a_seed_equal_to_the_default_is_not_an_overlay(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", SCOPE)
    rels = [o.rel for o in got.overlays["gmail-api"]]
    assert "labels.json" not in rels
    assert sorted(rels) == ["messages.json", "new_thread.json"]


def test_overlays_are_classified_and_carry_their_confidence(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", SCOPE)
    by_rel = {o.rel: o for o in got.overlays["gmail-api"]}
    assert by_rel["messages.json"].reason == E.DIFFERS
    assert by_rel["new_thread.json"].reason == E.NEW
    assert all(o.confidence == E.VERIFIED for o in by_rel.values())


def test_non_seed_files_are_left_alone(tmp_path):
    E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", SCOPE)
    assert not (tmp_path / "mock" / "gmail-api" / "notes.md").exists()


def test_an_api_with_no_baseline_is_an_error_not_a_warning(tmp_path):
    """Unverifiable seeds all look new; a warning here produced a 50-API result."""
    got = E.extract(_env(tmp_path), _baseline(tmp_path, include_slack=False),
                    tmp_path / "mock", SCOPE)
    assert got.unverified_apis == ["slack-api"]
    assert got.errors
    assert got.warnings == []


def test_unverified_overlays_downgrades_the_refusal(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path, include_slack=False),
                    tmp_path / "mock", SCOPE, allow_unverified=True)
    assert got.errors == []
    assert any("UNVERIFIED" in w for w in got.warnings)
    assert got.overlays["slack-api"][0].confidence == E.UNVERIFIED


def test_pruning_removes_what_could_not_be_verified(tmp_path):
    out = tmp_path / "mock"
    got = E.extract(_env(tmp_path), _baseline(tmp_path, include_slack=False),
                    out, SCOPE)
    E.prune_unverified(got, out)
    assert "slack-api" not in got.overlays
    assert not (out / "slack-api").exists()
    assert (out / "gmail-api" / "messages.json").is_file()


def test_no_declared_scope_falls_back_loudly(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", set())
    assert got.scope_proven is False
    assert got.out_of_scope == []
    assert any("not a proven task surface" in e for e in got.errors)


def test_a_declared_api_the_bundle_never_staged_is_reported(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock",
                    SCOPE | {"notion-api"})
    assert any("not staged in the bundle" in w for w in got.warnings)


def test_a_bundle_with_no_environment_isolates_nothing(tmp_path):
    got = E.extract(tmp_path / "absent", _baseline(tmp_path), tmp_path / "mock", SCOPE)
    assert got.overlays == {}
    assert got.warnings


def test_file_count_sums_every_api(tmp_path):
    got = E.extract(_env(tmp_path), _baseline(tmp_path), tmp_path / "mock", SCOPE)
    assert got.file_count == 3


def test_recovered_bytes_are_the_bundle_bytes(tmp_path):
    out = tmp_path / "mock"
    E.extract(_env(tmp_path), _baseline(tmp_path), out, SCOPE)
    assert (out / "gmail-api" / "messages.json").read_text(encoding="utf-8") == '{"m": "task"}'


# --------------------------------------------------------------------------- #
# baseline_from_ref
# --------------------------------------------------------------------------- #
def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _tree(repo, {"environment/gmail-api/messages.json": '{"m": "old"}'})
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"]):
        subprocess.run(["git"] + cmd, cwd=str(repo), check=True,
                       stdout=subprocess.DEVNULL)
    return repo


def test_a_baseline_can_be_read_out_of_a_git_ref(tmp_path):
    """Today's environment/ covers only 24 of the 50 APIs a pilot bundle ships."""
    repo = _git_repo(tmp_path)
    (repo / "environment" / "gmail-api" / "messages.json").write_text(
        '{"m": "new"}', encoding="utf-8")
    tree = E.baseline_from_ref("HEAD", repo, tmp_path / "extract")
    assert (tree / "gmail-api" / "messages.json").read_text(encoding="utf-8") == '{"m": "old"}'


def test_reading_a_baseline_never_disturbs_the_working_tree(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "environment" / "gmail-api" / "messages.json").write_text(
        '{"m": "dirty"}', encoding="utf-8")
    E.baseline_from_ref("HEAD", repo, tmp_path / "extract")
    assert (repo / "environment" / "gmail-api" / "messages.json").read_text(
        encoding="utf-8") == '{"m": "dirty"}'


def test_an_unknown_ref_is_refused_with_its_reason(tmp_path):
    with pytest.raises(ValueError, match="could not read environment"):
        E.baseline_from_ref("no-such-ref", _git_repo(tmp_path), tmp_path / "extract")
