from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.inject_director import InjectScript, InjectStage
from src.utils.inject_validator import _row_ids, validate_inject_script

URLS = {"figma-api": "http://127.0.0.1:1"}


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


def test_row_ids_harvests_file_key():
    row = {"file_key": "FKmenuv5final", "node_id": "42:1007", "name": "x"}
    assert "FKmenuv5final" in _row_ids(row)


def test_row_ids_harvests_component_key():
    row = {"component_key": "comp-menuinsert-trim", "file_key": "FK1", "name": "x"}
    ids = _row_ids(row)
    assert "comp-menuinsert-trim" in ids
    assert "FK1" in ids


def test_row_ids_harvests_bare_key():
    row = {"key": "PROJ-123", "summary": "x"}
    assert "PROJ-123" in _row_ids(row)


def test_stage1_patch_on_key_pk_not_fatal(tmp_path):
    svc_dir = tmp_path / "figma-api"
    svc_dir.mkdir()
    (svc_dir / "components.json").write_text(
        '[{"component_key": "comp-menuinsert-trim", "file_key": "FKmenuv5final", '
        '"name": "Menu insert"}]',
        encoding="utf-8",
    )
    stage1 = _stage(1, 0, 1, silent=[_admin_patch(
        "s1", "figma-api", "components", "comp-menuinsert-trim",
        {"description": "updated"})])
    fatal, warnings = validate_inject_script(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert not any(d["status"] == "missing-target" for d in fatal)


def test_stage1_patch_on_absent_key_pk_still_fatal(tmp_path):
    svc_dir = tmp_path / "figma-api"
    svc_dir.mkdir()
    (svc_dir / "components.json").write_text(
        '[{"component_key": "comp-menuinsert-trim", "file_key": "FKmenuv5final", '
        '"name": "Menu insert"}]',
        encoding="utf-8",
    )
    stage1 = _stage(1, 0, 1, silent=[_admin_patch(
        "s1", "figma-api", "components", "comp-does-not-exist",
        {"description": "updated"})])
    fatal, warnings = validate_inject_script(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "missing-target" for d in fatal)
