"""Mock-module integrity manifest (QC item 3).

megan's bundle was cut from a stale `notion-api/notion_data.py`: the
write-discard bug was fixed upstream and four sibling bundles shipped the
corrected module, but nothing compared what landed in a bundle against what the
harness had. It was found months later by md5-ing the same file across seven
deliveries by hand.

Pinned here: the manifest records every shipped mock module, generation FAILS on
a stale one, and `script/validate_bundle.py` re-detects both a post-packaging
edit and the megan staleness case.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from script.validate_bundle import check_mock_modules, _bundle_dirs  # noqa: E402
from src.utils.harbor.mock_manifest import (  # noqa: E402
    MANIFEST_NAME,
    DIGEST_ALGO,
    StaleMockModule,
    build_manifest,
    collect_modules,
    module_digest,
    verify_manifest,
    write_manifest,
)

FIXED = "def get_page(pid):\n    return _store.table('pages').get(pid)\n"
STALE = "def get_page(pid):\n    return deepcopy(_store.table('pages').get(pid))\n"


def _env(root: Path, notion_body: str = FIXED) -> Path:
    env = root
    env.mkdir(parents=True, exist_ok=True)
    (env / "_mutable_store.py").write_text("STORE = 1\n", encoding="utf-8")
    (env / "admin_plane.py").write_text("ADMIN = 1\n", encoding="utf-8")
    notion = env / "notion-api"
    notion.mkdir(exist_ok=True)
    (notion / "notion_data.py").write_text(notion_body, encoding="utf-8")
    (notion / "server.py").write_text("APP = 1\n", encoding="utf-8")
    (notion / "pages.json").write_text("[]", encoding="utf-8")
    skills = env / "skills"
    skills.mkdir(exist_ok=True)
    (skills / "helper.py").write_text("NOT_A_SERVICE = 1\n", encoding="utf-8")
    return env


def _bundle(root: Path, notion_body: str = FIXED, name: str = "bundle") -> Path:
    bundle = root / name
    _env(bundle / "data" / "environment", notion_body)
    (bundle / "rubric.json").write_text("[]", encoding="utf-8")
    return bundle


# --------------------------------------------------------------------------- #
# collection scope
# --------------------------------------------------------------------------- #
def test_collect_covers_service_modules_and_shared_infra(tmp_path):
    found = collect_modules(_env(tmp_path / "env"))
    assert set(found) == {
        "_mutable_store.py", "admin_plane.py",
        "notion-api/notion_data.py", "notion-api/server.py",
    }


def test_collect_ignores_seed_files_and_non_service_dirs(tmp_path):
    found = collect_modules(_env(tmp_path / "env"))
    # Seeds are per-task overlays and legitimately differ from the baseline;
    # checksumming them would be all false positives.
    assert not any(k.endswith(".json") for k in found)
    assert not any(k.startswith("skills/") for k in found)


def test_collect_on_a_missing_dir_is_empty(tmp_path):
    assert collect_modules(tmp_path / "nope") == {}


def test_module_digest_is_content_addressed(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text(FIXED, encoding="utf-8")
    b.write_text(FIXED, encoding="utf-8")
    assert module_digest(a) == module_digest(b)
    b.write_text(STALE, encoding="utf-8")
    assert module_digest(a) != module_digest(b)


# --------------------------------------------------------------------------- #
# build / write
# --------------------------------------------------------------------------- #
def test_manifest_records_every_module_with_both_digests(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    manifest = build_manifest(bundle / "data" / "environment", source)
    assert manifest["algorithm"] == DIGEST_ALGO
    assert manifest["module_count"] == 4
    assert manifest["stale"] == []
    entry = manifest["modules"]["notion-api/notion_data.py"]
    assert entry["digest"] == entry["source_digest"]


def test_build_flags_the_megan_stale_module(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    manifest = build_manifest(bundle / "data" / "environment", source)
    assert manifest["stale"] == ["notion-api/notion_data.py"]


def test_write_manifest_lands_at_the_bundle_root_not_inside_environment(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    write_manifest(bundle, bundle / "data" / "environment", source, task_id="megan")
    # Living inside data/environment/ would change the very bytes being
    # checksummed and make the bundle differ from the harness source.
    assert (bundle / MANIFEST_NAME).is_file()
    assert not (bundle / "data" / "environment" / MANIFEST_NAME).exists()
    doc = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert doc["task_id"] == "megan"


def test_write_manifest_raises_on_a_stale_module(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    with pytest.raises(StaleMockModule, match="notion_data.py"):
        write_manifest(bundle, bundle / "data" / "environment", source)
    # The manifest is still written so the failure is diagnosable.
    assert (bundle / MANIFEST_NAME).is_file()


def test_write_manifest_non_strict_reports_without_raising(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    manifest = write_manifest(bundle, bundle / "data" / "environment", source,
                              strict=False)
    assert manifest["stale"] == ["notion-api/notion_data.py"]


def test_a_bundle_only_module_records_a_null_source_digest(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    (bundle / "data" / "environment" / "notion-api" / "overlay_extra.py").write_text(
        "X = 1\n", encoding="utf-8")
    manifest = build_manifest(bundle / "data" / "environment", source)
    assert manifest["modules"]["notion-api/overlay_extra.py"]["source_digest"] is None
    assert manifest["stale"] == []


# --------------------------------------------------------------------------- #
# verification path
# --------------------------------------------------------------------------- #
def test_verify_clean_bundle_has_no_findings(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    write_manifest(bundle, bundle / "data" / "environment", source)
    assert verify_manifest(bundle, source) == ([], [])


def test_verify_detects_the_megan_staleness_against_the_harness_source(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    write_manifest(bundle, bundle / "data" / "environment", source, strict=False)
    errors, _ = verify_manifest(bundle, source)
    assert any("STALE" in e and "notion_data.py" in e for e in errors)


def test_verify_detects_an_edit_made_after_packaging(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    write_manifest(bundle, bundle / "data" / "environment", source)
    (bundle / "data" / "environment" / "notion-api" / "notion_data.py").write_text(
        STALE, encoding="utf-8")
    errors, _ = verify_manifest(bundle, None)
    assert any("does not match its manifest digest" in e for e in errors)


def test_verify_flags_a_bundle_shipping_mocks_with_no_manifest(tmp_path):
    bundle = _bundle(tmp_path)
    errors, _ = verify_manifest(bundle, None)
    assert any(MANIFEST_NAME in e and "missing" in e for e in errors)


def test_verify_is_silent_on_a_bundle_that_ships_no_mock_modules(tmp_path):
    bundle = tmp_path / "empty"
    bundle.mkdir()
    assert verify_manifest(bundle, None) == ([], [])


def test_verify_warns_on_a_module_shipped_but_not_recorded(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    write_manifest(bundle, bundle / "data" / "environment", source)
    (bundle / "data" / "environment" / "notion-api" / "late.py").write_text(
        "Y = 1\n", encoding="utf-8")
    errors, warnings = verify_manifest(bundle, None)
    assert errors == []
    assert any("late.py" in w for w in warnings)


def test_verify_errors_on_a_module_recorded_but_absent(tmp_path):
    source = _env(tmp_path / "harness")
    bundle = _bundle(tmp_path)
    write_manifest(bundle, bundle / "data" / "environment", source)
    (bundle / "data" / "environment" / "notion-api" / "server.py").unlink()
    errors, _ = verify_manifest(bundle, None)
    assert any("absent from the bundle" in e for e in errors)


def test_verify_without_a_source_skips_the_staleness_check(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    write_manifest(bundle, bundle / "data" / "environment", source, strict=False)
    errors, _ = verify_manifest(bundle, None)
    assert not any("STALE" in e for e in errors)


# --------------------------------------------------------------------------- #
# validate_bundle wiring
# --------------------------------------------------------------------------- #
def test_check_mock_modules_delegates_to_verify(tmp_path):
    source = _env(tmp_path / "harness", FIXED)
    bundle = _bundle(tmp_path, STALE)
    write_manifest(bundle, bundle / "data" / "environment", source, strict=False)
    errors, _ = check_mock_modules(bundle, source)
    assert any("STALE" in e for e in errors)


def test_bundle_dirs_finds_a_root_that_is_itself_a_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    assert list(_bundle_dirs(bundle)) == [bundle]


def test_bundle_dirs_finds_bundles_one_level_under_a_delivery_root(tmp_path):
    root = tmp_path / "output_bundle"
    root.mkdir()
    first = _bundle(root, name="megan")
    second = _bundle(root, name="eric")
    assert set(_bundle_dirs(root)) == {first, second}
