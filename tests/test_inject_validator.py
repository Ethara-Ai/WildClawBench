from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.inject_director import InjectScript, InjectStage
from src.utils.inject_validator import (
    InjectAuthoringError,
    run_authoring_validation,
    validate_inject_script,
)

URLS = {"google-classroom-api": "http://127.0.0.1:1", "mailchimp-api": "http://127.0.0.1:2"}


def _seed_stage(silent=None, loud=None):
    return InjectStage(
        index=0, name="seed", from_turn=None, to_turn=0,
        filesystem=[], loud=loud or [], silent=silent or [], source="",
    )


def _stage(index, from_turn, to_turn, silent=None, loud=None, name=None):
    return InjectStage(
        index=index, name=name or f"s{index}", from_turn=from_turn, to_turn=to_turn,
        filesystem=[], loud=loud or [], silent=silent or [], source="",
    )


def _rest_patch(oid, service, pk, body):
    return {
        "id": oid, "service": service, "method": "PATCH",
        "path": f"/v1/courses/801110001/courseWork/{pk}", "body": body,
    }


def _script(*stages):
    return InjectScript(description="test", stages=list(stages))


def test_slug_normalization_success_no_fatal(tmp_path):
    svc_dir = tmp_path / "google-classroom-api"
    svc_dir.mkdir()
    (svc_dir / "coursework.json").write_text(
        '[{"id": "901110051", "dueDate": {"year": 2027, "month": 1, "day": 20}}]',
        encoding="utf-8",
    )
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "google-classroom-api", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def test_bare_slug_without_api_suffix_is_fatal():
    # The injector does NOT auto-normalize a bare slug; authors must write the
    # canonical '<name>-api' slug. A bare 'google-classroom' is unresolvable.
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "google-classroom", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "unresolved" for d in ei.value.defects)


def test_unresolvable_slug_is_fatal():
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "does-not-exist", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "unresolved" for d in ei.value.defects)


def test_zero_field_op_is_fatal():
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "google-classroom-api", "901110051", {})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "empty" for d in ei.value.defects)


def test_stage1_missing_target_is_fatal():
    seed = _seed_stage()
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "google-classroom-api", "999999999",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "missing-target" for d in fatal)


def test_stage2_patch_of_stage1_upsert_not_fatal():
    seed = _seed_stage()
    upsert_op = {
        "id": "u1", "service": "google-classroom-api",
        "admin": {"op": "upsert", "table": "coursework", "row": {"id": "555000111"}},
    }
    stage1 = _stage(1, 0, 1, loud=[upsert_op])
    stage2 = _stage(2, 2, 3, silent=[_rest_patch(
        "s2", "google-classroom-api", "555000111",
        {"dueDate": {"year": 2027, "month": 2, "day": 1}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1, stage2), host_api_to_url=URLS, mock_data_root=None)
    assert fatal == []
    assert not any(d["status"] == "missing-target" for d in warnings)


def test_stage2_missing_target_is_warning_not_fatal():
    seed = _seed_stage()
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "google-classroom-api", "seedrow",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    stage2 = _stage(2, 2, 3, silent=[_rest_patch(
        "s2", "google-classroom-api", "never-seen",
        {"dueDate": {"year": 2027, "month": 2, "day": 1}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1, stage2),
        host_api_to_url=URLS,
        mock_data_root=None,
    )
    assert not any(d["id"] == "s2" and d["status"] == "missing-target" for d in fatal)
    assert any(d["id"] == "s2" and d["status"] == "missing-target" for d in warnings)


# --------------------------------------------------------------------------- #
# End-to-end amara/google-classroom scenario: the real 4-stage dueDate move,
# through the C4 validator (static pre-flight) AND the C1+C2+C3 runtime applier
# against a live store shaped like _coerce_coursework's output (nested dueDate).
# --------------------------------------------------------------------------- #
from src.utils.inject_director import InjectApplier, is_defect  # noqa: E402


def _amara_stages():
    # Canonical 'google-classroom-api' slug + bare top-level nested dueDate body,
    # matching the schema-corrected inject/stage{0..3}/mutations.json files.
    seed = _seed_stage(silent=[_rest_patch(
        "s0_due_seed", "google-classroom-api", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 20}})])
    stage1 = _stage(1, 2, 3, name="day2_hold", silent=[_rest_patch(
        "s1_due_hold", "google-classroom-api", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 20}})])
    stage2 = _stage(2, 4, 5, name="day3_registrar_move", silent=[_rest_patch(
        "s2_due_move", "google-classroom-api", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    stage3 = _stage(3, 6, 7, name="day4_hold_live", silent=[_rest_patch(
        "s3_due_live", "google-classroom-api", "901110051",
        {"dueDate": {"year": 2027, "month": 1, "day": 27}})])
    return seed, stage1, stage2, stage3


def test_amara_scenario_passes_preflight(tmp_path):
    svc_dir = tmp_path / "google-classroom-api"
    svc_dir.mkdir()
    (svc_dir / "coursework.json").write_text(
        '[{"id": "901110051", "dueDate": {"year": 2027, "month": 1, "day": 20}}]',
        encoding="utf-8",
    )
    warnings = run_authoring_validation(
        _script(*_amara_stages()), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def _live_classroom_applier(tmp_path, tables, urls):
    ap = InjectApplier(
        host_api_to_url=urls, admin_token=None,
        timeline_path=tmp_path / "inject_timeline.jsonl", task_id="amara")

    def fake_admin_get(api, suffix):
        if suffix == "/admin/tables":
            return {"tables": [{"name": t} for t in tables]}
        if suffix.startswith("/admin/data/"):
            rest = suffix[len("/admin/data/"):]
            if "/" in rest:
                table, pk = rest.split("/", 1)
                for row in tables.get(table, []):
                    if str(row.get("id") or row.get("pk")) == pk:
                        return dict(row)
                return None
            return {"rows": tables.get(rest, [])}
        return None

    def fake_admin_patch(api, table, pk, fields):
        for row in tables.get(table, []):
            if str(row.get("id") or row.get("pk")) == pk:
                row.update(fields)
                return {"ok": True, "status": 200}
        return {"ok": False, "status": 404}

    ap._admin_get = fake_admin_get      # type: ignore
    ap._admin_patch = fake_admin_patch  # type: ignore
    return ap


def test_amara_scenario_applies_nested_duedate_end_to_end(tmp_path):
    # Live store mirrors _coerce_coursework: flat seed dueDate_* lifted to nested.
    tables = {"coursework": [{
        "id": "901110051",
        "title": "Reading the Chart: Asylum Grant Rates",
        "dueDate": {"year": 2027, "month": 1, "day": 20},
    }]}
    ap = _live_classroom_applier(
        tmp_path, tables, urls={"google-classroom-api": "http://x"})
    _seed, stage1, stage2, stage3 = _amara_stages()

    # Fire each non-seed stage's silent op; the registrar move (stage2) shifts
    # the deadline to Jan 27 and the hold (stage3) keeps it there.
    for stage, expected_day in ((stage1, 20), (stage2, 27), (stage3, 27)):
        op = stage.silent[0]
        rec = ap._apply_api_mutation(op, stage, stage.to_turn, silent=True)
        assert "resolved_service" not in rec
        assert rec["service"] == "google-classroom-api"
        assert rec["ok"] is True and rec["status"] == "applied"
        assert "unmapped_fields" not in rec
        assert is_defect(rec) is False
        assert tables["coursework"][0]["dueDate"]["day"] == expected_day

    assert tables["coursework"][0]["dueDate"] == {
        "year": 2027, "month": 1, "day": 27}


# --------------------------------------------------------------------------- #
# Filesystem-op authoring validation. The runtime hook is copy-only; the
# validator must fail-closed at authoring time on the same shapes that would
# silently no-op or corrupt state at runtime (action:patch, missing src, src
# that does not resolve on disk).
# --------------------------------------------------------------------------- #

def _fs_stage_ondisk(tmp_path, fs_ops):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    return InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=list(fs_ops), loud=[], silent=[],
        source=str(stage_dir / "mutations.json"),
    ), stage_dir


def test_fs_patch_action_is_fatal():
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-p", "action": "patch",
                     "src": "note.txt", "dst": "/workspace/note.txt"}],
        loud=[], silent=[], source="")
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "fs-invalid-action" for d in ei.value.defects)


def test_fs_copy_without_src_is_fatal():
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-n", "action": "copy",
                     "src": None, "dst": "/workspace/note.txt"}],
        loud=[], silent=[], source="")
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "fs-missing-src" for d in ei.value.defects)


def test_fs_copy_with_resolvable_src_passes(tmp_path):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-ok", "action": "copy",
         "src": "note.txt", "dst": "/workspace/note.txt"}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def test_fs_copy_with_absent_src_is_fatal(tmp_path):
    stage, _ = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-miss", "action": "copy",
         "src": "note.txt", "dst": "/workspace/note.txt"}])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-src-not-found" for d in ei.value.defects)


def test_fs_mkdir_with_src_is_warning(tmp_path):
    stage, _ = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-mk", "action": "mkdir",
         "src": "note.txt", "dst": "/workspace/newdir"}])
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-mkdir-with-src" for d in warnings)
