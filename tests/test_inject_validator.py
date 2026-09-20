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
from src.utils.skills_inference import catalog_apis

# Re-pointed off google-classroom / mailchimp, which left in the newreq
# convergence. kubernetes keeps the property the amara scenario was written
# for: a nested-dict field (`spec.replicas`) moving across stages, which is the
# shape `dueDate: {year, month, day}` supplied. The validator never inspects
# the REST path, only the slug, the pk and the body's field count, so the paths
# below are cosmetic fidelity rather than load-bearing.
URLS = {"kubernetes-api": "http://127.0.0.1:1", "sentry-api": "http://127.0.0.1:2"}
DEPLOYMENT = "api-gateway"


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
        "path": f"/apis/apps/v1/namespaces/prod/deployments/{pk}", "body": body,
    }


def _script(*stages):
    return InjectScript(description="test", stages=list(stages))


def test_slug_normalization_success_no_fatal(tmp_path):
    svc_dir = tmp_path / "kubernetes-api"
    svc_dir.mkdir()
    (svc_dir / "deployments.json").write_text(
        '[{"id": "api-gateway", "spec": {"replicas": 3}}]',
        encoding="utf-8",
    )
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "kubernetes-api", DEPLOYMENT,
        {"spec": {"replicas": 7}})])
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage1), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def test_bare_slug_without_api_suffix_is_fatal():
    # The injector does NOT auto-normalize a bare slug; authors must write the
    # canonical '<name>-api' slug. A bare 'kubernetes' is unresolvable.
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "kubernetes", DEPLOYMENT,
        {"spec": {"replicas": 7}})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "unresolved" for d in ei.value.defects)


def test_unresolvable_slug_is_fatal():
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "does-not-exist", DEPLOYMENT,
        {"spec": {"replicas": 7}})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "unresolved" for d in ei.value.defects)


def test_zero_field_op_is_fatal():
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "kubernetes-api", DEPLOYMENT, {})])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "empty" for d in ei.value.defects)


def test_stage1_missing_target_is_fatal():
    seed = _seed_stage()
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "kubernetes-api", "deployment-does-not-exist",
        {"spec": {"replicas": 7}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1), host_api_to_url=URLS, mock_data_root=None)
    assert any(d["status"] == "missing-target" for d in fatal)


def test_stage2_patch_of_stage1_upsert_not_fatal():
    seed = _seed_stage()
    upsert_op = {
        "id": "u1", "service": "kubernetes-api",
        "admin": {"op": "upsert", "table": "deployments", "row": {"id": "api-canary"}},
    }
    stage1 = _stage(1, 0, 1, loud=[upsert_op])
    stage2 = _stage(2, 2, 3, silent=[_rest_patch(
        "s2", "kubernetes-api", "api-canary",
        {"spec": {"replicas": 9}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1, stage2), host_api_to_url=URLS, mock_data_root=None)
    assert fatal == []
    assert not any(d["status"] == "missing-target" for d in warnings)


def test_stage2_missing_target_is_warning_not_fatal():
    seed = _seed_stage()
    stage1 = _stage(1, 0, 1, silent=[_rest_patch(
        "s1", "kubernetes-api", "seedrow",
        {"spec": {"replicas": 7}})])
    stage2 = _stage(2, 2, 3, silent=[_rest_patch(
        "s2", "kubernetes-api", "never-seen",
        {"spec": {"replicas": 9}})])
    fatal, warnings = validate_inject_script(
        _script(seed, stage1, stage2),
        host_api_to_url=URLS,
        mock_data_root=None,
    )
    assert not any(d["id"] == "s2" and d["status"] == "missing-target" for d in fatal)
    assert any(d["id"] == "s2" and d["status"] == "missing-target" for d in warnings)


# --------------------------------------------------------------------------- #
# End-to-end 4-stage nested-field move, through the C4 validator (static
# pre-flight) AND the C1+C2+C3 runtime applier against a live store.
#
# This is the amara/google-classroom dueDate scenario re-pointed onto
# kubernetes: hold, move, hold, with the moved field nested one level down
# (`spec.replicas` where amara had `dueDate.day`). The property under test is
# that the applier reaches INTO the nested body rather than overwriting the
# parent key, which is what the incident turned on.
# --------------------------------------------------------------------------- #
from src.utils.inject_director import InjectApplier, is_defect  # noqa: E402


def _scale_stages():
    # Canonical '<name>-api' slug + bare top-level nested body, matching the
    # shape of an authored inject/stage{0..3}/mutations.json file.
    seed = _seed_stage(silent=[_rest_patch(
        "s0_scale_seed", "kubernetes-api", DEPLOYMENT,
        {"spec": {"replicas": 3}})])
    stage1 = _stage(1, 2, 3, name="day2_hold", silent=[_rest_patch(
        "s1_scale_hold", "kubernetes-api", DEPLOYMENT,
        {"spec": {"replicas": 3}})])
    stage2 = _stage(2, 4, 5, name="day3_scale_out", silent=[_rest_patch(
        "s2_scale_move", "kubernetes-api", DEPLOYMENT,
        {"spec": {"replicas": 7}})])
    stage3 = _stage(3, 6, 7, name="day4_hold_live", silent=[_rest_patch(
        "s3_scale_live", "kubernetes-api", DEPLOYMENT,
        {"spec": {"replicas": 7}})])
    return seed, stage1, stage2, stage3


def test_scale_scenario_passes_preflight(tmp_path):
    svc_dir = tmp_path / "kubernetes-api"
    svc_dir.mkdir()
    (svc_dir / "deployments.json").write_text(
        '[{"id": "api-gateway", "spec": {"replicas": 3}}]',
        encoding="utf-8",
    )
    warnings = run_authoring_validation(
        _script(*_scale_stages()), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def _live_deployment_applier(tmp_path, tables, urls):
    ap = InjectApplier(
        host_api_to_url=urls, admin_token=None,
        timeline_path=tmp_path / "inject_timeline.jsonl", task_id="scale-move")

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


def test_scale_scenario_applies_nested_replicas_end_to_end(tmp_path):
    # Live store mirrors the served deployment shape: replicas nested under spec.
    tables = {"deployments": [{
        "id": DEPLOYMENT,
        "namespace": "prod",
        "spec": {"replicas": 3},
    }]}
    ap = _live_deployment_applier(
        tmp_path, tables, urls={"kubernetes-api": "http://x"})
    _seed, stage1, stage2, stage3 = _scale_stages()

    # Fire each non-seed stage's silent op; the scale-out (stage2) takes the
    # deployment to 7 replicas and the hold (stage3) keeps it there.
    for stage, expected_replicas in ((stage1, 3), (stage2, 7), (stage3, 7)):
        op = stage.silent[0]
        rec = ap._apply_api_mutation(op, stage, stage.to_turn, silent=True)
        assert "resolved_service" not in rec
        assert rec["service"] == "kubernetes-api"
        assert rec["ok"] is True and rec["status"] == "applied"
        assert "unmapped_fields" not in rec
        assert is_defect(rec) is False
        assert tables["deployments"][0]["spec"]["replicas"] == expected_replicas

    assert tables["deployments"][0]["spec"] == {"replicas": 7}


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


@pytest.mark.parametrize("mtime", ["2026-12-20 03:10:00", "yesterday", ""])
def test_fs_op_with_unusable_mtime_override_is_fatal(tmp_path, mtime):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-mt", "action": "copy", "src": "note.txt",
         "dst": "/workspace/note.txt", "mtime": mtime}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-invalid-mtime" for d in ei.value.defects)


@pytest.mark.parametrize("mtime", ["2026-12-20T03:10:00-05:00", 1797754200000])
def test_fs_op_with_usable_mtime_override_passes(tmp_path, mtime):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-mt", "action": "copy", "src": "note.txt",
         "dst": "/workspace/note.txt", "mtime": mtime}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert warnings == []


def test_fs_mkdir_with_src_is_warning(tmp_path):
    stage, _ = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-mk", "action": "mkdir",
         "src": "note.txt", "dst": "/workspace/newdir"}])
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-mkdir-with-src" for d in warnings)


def test_fs_dst_outside_workspace_is_fatal(tmp_path):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-abs", "action": "copy",
         "src": "note.txt", "dst": "/data/home/note.txt"}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-dst-not-workspace" for d in ei.value.defects)


@pytest.mark.parametrize("dst", [
    "/root/workspace/note.txt", "~/workspace/note.txt", "data/home/note.txt",
    "/tmp_workspace/note.txt", "relative/note.txt"])
def test_fs_non_canonical_dst_spellings_are_fatal(tmp_path, dst):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-alias", "action": "copy", "src": "note.txt", "dst": dst}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-dst-not-workspace" for d in ei.value.defects)


def test_fs_mkdir_dst_outside_workspace_is_fatal(tmp_path):
    stage, _ = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-mk", "action": "mkdir", "dst": "/data/newdir"}])
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(
            _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-dst-not-workspace" for d in ei.value.defects)


def test_fs_dst_tree_shape_mismatch_is_warning(tmp_path):
    """data/home/ staging puts inputs at /workspace/home/home/<rel>; a single-home
    dst lands beside them, not among them."""
    (tmp_path / "data" / "home" / "Pictures").mkdir(parents=True)
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-shape", "action": "copy",
         "src": "note.txt", "dst": "/workspace/home/Pictures/note.txt"}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert any(d["status"] == "fs-dst-tree-mismatch" for d in warnings)


def test_fs_dst_matching_staged_home_home_tree_is_clean(tmp_path):
    (tmp_path / "data" / "home" / "Pictures").mkdir(parents=True)
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-shape-ok", "action": "copy",
         "src": "note.txt", "dst": "/workspace/home/home/Pictures/note.txt"}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert not any(d["status"] == "fs-dst-tree-mismatch" for d in warnings)


def test_fs_tree_shape_warning_absent_without_staged_data_home(tmp_path):
    stage, stage_dir = _fs_stage_ondisk(tmp_path, [
        {"id": "fs-plain", "action": "copy",
         "src": "note.txt", "dst": "/workspace/home/Pictures/note.txt"}])
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    warnings = run_authoring_validation(
        _script(_seed_stage(), stage), host_api_to_url=URLS, mock_data_root=tmp_path)
    assert not any(d["status"] == "fs-dst-tree-mismatch" for d in warnings)


# --------------------------------------------------------------------------- #
# Catalog gate: an op may only name a service the fleet actually ships.
#
# Resolving against host_api_to_url is a different question. The mock image
# bakes a port manifest that outlives any fleet composition, so a service
# pruned off disk can still publish a port and hand the injector a URL; its
# admin calls then miss for the whole run. The catalog is read off disk at
# validation time so restoring a service fixes its ops with no code change.
# --------------------------------------------------------------------------- #
ABSENT = "no-such-service-api"
OFF_CATALOG_URLS = {**URLS, ABSENT: "http://127.0.0.1:3"}


def _off_catalog_stage():
    return _stage(1, 0, 1, silent=[{
        "id": "loud_partner_page_reassurance", "service": ABSENT,
        "admin": {"op": "patch", "table": "posts", "pk": "urn:li:share:c105",
                  "set": {"commentary": "corridor campaign continues"}},
    }])


def test_a_service_the_fleet_does_not_ship_is_fatal():
    assert ABSENT not in catalog_apis()
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(_seed_stage(), _off_catalog_stage()),
                                 host_api_to_url=OFF_CATALOG_URLS, mock_data_root=None)
    defect = next(d for d in ei.value.defects if d["status"] == "service-not-in-catalog")
    assert ABSENT in defect["reason"]
    assert defect["id"] == "loud_partner_page_reassurance"


def test_a_service_on_disk_clears_the_gate():
    fatal, _ = validate_inject_script(
        _script(_seed_stage(), _stage(1, 0, 1, silent=[_rest_patch(
            "s1", "kubernetes-api", DEPLOYMENT, {"spec": {"replicas": 2}})])),
        host_api_to_url=URLS, mock_data_root=None)
    assert not any(d["status"] == "service-not-in-catalog" for d in fatal)


def test_the_gate_reads_the_live_catalog_rather_than_a_fixed_list(monkeypatch):
    # A sibling restoring the service to environment/ must fix its ops without
    # anyone editing this module.
    monkeypatch.setattr("src.utils.inject_validator.catalog_apis",
                        lambda *a, **k: [*catalog_apis(), ABSENT])
    fatal, _ = validate_inject_script(
        _script(_seed_stage(), _off_catalog_stage()),
        host_api_to_url=OFF_CATALOG_URLS, mock_data_root=None)
    assert not any(d["status"] == "service-not-in-catalog" for d in fatal)


def test_an_empty_catalog_accuses_nobody(monkeypatch):
    # Stripped checkout: nothing to validate against is not "everything is wrong".
    monkeypatch.setattr("src.utils.inject_validator.catalog_apis", lambda *a, **k: [])
    fatal, _ = validate_inject_script(
        _script(_seed_stage(), _off_catalog_stage()),
        host_api_to_url=OFF_CATALOG_URLS, mock_data_root=None)
    assert not any(d["status"] == "service-not-in-catalog" for d in fatal)


def test_a_slug_in_neither_the_stack_nor_the_catalog_still_reads_as_unresolved():
    # The two checks answer different questions; the URL one keeps precedence so
    # a bare slug still gets the "use the canonical '<name>-api' slug" hint.
    with pytest.raises(InjectAuthoringError) as ei:
        run_authoring_validation(_script(_seed_stage(), _stage(1, 0, 1, silent=[
            _rest_patch("s1", "kubernetes", DEPLOYMENT, {"spec": {}})])),
            host_api_to_url=URLS, mock_data_root=None)
    assert [d["status"] for d in ei.value.defects] == ["unresolved"]
