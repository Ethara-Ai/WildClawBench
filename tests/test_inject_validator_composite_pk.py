"""`_row_ids` harvesting and the ``*_key`` primary-key case.

Re-pointed off figma, which left in the newreq convergence, onto confluence --
the converged fleet's live example of the class and the sole entry left in
``inject_director._SERVICE_RESOLUTION``, so the fixture and the resolver now
name the same service. ``spaces`` is keyed by a bare ``key`` and ``pages``
carries ``space_key``, which is the two-column shape figma's
``component_key``/``file_key`` used to supply.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.inject_director import InjectScript, InjectStage
from src.utils.inject_validator import _row_ids, validate_inject_script

URLS = {"confluence-api": "http://127.0.0.1:1"}

PAGES_SEED = (
    '[{"id": "100101", "type": "page", "space_key": "ENG", '
    '"title": "Engineering Home"}]'
)


def _seed_stage():
    return InjectStage(
        index=0, name="seed", from_turn=None, to_turn=0,
        filesystem=[], loud=[], silent=[], source="",
    )


def _stage(index, from_turn, to_turn, silent=None, loud=None, name=None):
    return InjectStage(
        index=index, name=name or f"s{index}", from_turn=from_turn, to_turn=to_turn,
        filesystem=[], loud=loud or [], silent=silent or [], source="",
    )


def _admin_patch(oid, service, table, pk, set_fields):
    return {
        "id": oid, "service": service,
        "admin": {"op": "patch", "table": table, "pk": pk, "set": set_fields},
    }


def _script(*stages):
    return InjectScript(description="test", stages=list(stages))


def _confluence_seed(tmp_path):
    svc_dir = tmp_path / "confluence-api"
    svc_dir.mkdir()
    (svc_dir / "pages.json").write_text(PAGES_SEED, encoding="utf-8")
    return svc_dir


def test_row_ids_harvests_space_key():
    row = {"space_key": "ENG", "id": "100101", "title": "x"}
    assert "ENG" in _row_ids(row)


def test_row_ids_harvests_every_key_column_on_the_row():
    row = {"project_key": "ENG", "key": "ENG-142", "summary": "x"}
    ids = _row_ids(row)
    assert "ENG" in ids
    assert "ENG-142" in ids


def test_row_ids_harvests_bare_key():
    row = {"key": "PROJ-123", "summary": "x"}
    assert "PROJ-123" in _row_ids(row)


def test_stage1_patch_on_key_pk_not_fatal(tmp_path):
    _confluence_seed(tmp_path)
    stage1 = _stage(1, 0, 1, silent=[_admin_patch(
        "s1", "confluence-api", "pages", "ENG",
        {"title": "updated"})])
    fatal, warnings = validate_inject_script(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert not any(d["status"] == "missing-target" for d in fatal)


def test_stage1_patch_on_absent_key_pk_still_fatal(tmp_path):
    _confluence_seed(tmp_path)
    stage1 = _stage(1, 0, 1, silent=[_admin_patch(
        "s1", "confluence-api", "pages", "SPACE-DOES-NOT-EXIST",
        {"title": "updated"})])
    fatal, warnings = validate_inject_script(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "missing-target" for d in fatal)
