"""Injection-integrity hardening (2026-07-30 audit): failed silent mutations
must be LOUD, not logged as "applied".

Covers:
  1. is_defect classification matrix (benign seed-fs cases vs real defects).
  2. apply_stage honest accounting: applied_ops/failed_ops in the timeline
     record + WARNING log when any op fails.
  3. seed() returns its outcomes (was None).
  4. REST-path status clobber fix: semantic string status + `http` int, plus
     post-write read-back (verified / mismatch -> defect).
  5. _admin_doc_set read-back of the live document value.
  6. run_batch stamping: injection_ok / injection_defects in scores; pass
     summary forwards injection_ok: false.

All static — no docker, no network (applier internals stubbed per-test).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.inject_director import (  # noqa: E402
    InjectApplier,
    InjectScript,
    InjectStage,
    is_defect,
)


def _applier(tmp_path, urls=None):
    return InjectApplier(
        host_api_to_url=urls or {"notion-api": "http://127.0.0.1:1"},
        admin_token=None,
        timeline_path=tmp_path / "inject_timeline.jsonl",
        task_id="t",
    )


def _stage(name="s1", silent=None, loud=None, fs=None, index=1):
    return InjectStage(index=index, name=name, from_turn=0, to_turn=1,
                       filesystem=fs or [], loud=loud or [], silent=silent or [],
                       source="")


# --------------------------------------------------------------------------- #
# 1. is_defect matrix
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rec,phase,expected", [
    ({"ok": True, "status": "applied"}, "stage", False),
    ({"ok": False, "status": "unresolved", "reason": "no admin URL for gmail"},
     "stage", True),
    ({"ok": False, "status": "failed"}, "stage", True),
    ({"ok": False, "status": "no-match"}, "stage", True),
    ({"ok": False, "status": "partial"}, "stage", True),
    # seed-time fs op, copy hook absent -> benign by design
    ({"ok": False, "status": "skipped", "action": "copy",
      "reason": "no workspace copy hook"}, "seed", False),
    # seed-time fs op, container not up yet -> benign, and says so explicitly
    ({"ok": False, "status": "skipped_container_down", "action": "copy"}, "seed", False),
    ({"ok": False, "status": "skipped_container_down", "action": "mkdir"}, "seed", False),
    # A seed op that reports "copied"/"mkdir" with ok=False now means the copy
    # really was ATTEMPTED and really FAILED -- a defect. Before the tri-state
    # copy hook these were indistinguishable from "container not up" and were
    # wrongly whitelisted, which silently swallowed dropped payloads.
    ({"ok": False, "status": "copied", "action": "copy"}, "seed", True),
    ({"ok": False, "status": "mkdir", "action": "mkdir"}, "seed", True),
    # same fs failure MID-RUN is a real defect
    ({"ok": False, "status": "copied", "action": "copy"}, "stage", True),
    ({"ok": False, "status": "skipped_container_down", "action": "copy"}, "stage", True),
    # seed fs op with a genuine authoring problem stays a defect
    ({"ok": False, "status": "missing_src", "action": "copy",
      "reason": "/x/y"}, "seed", True),
    ({"ok": False, "status": "skipped", "action": "copy",
      "reason": "missing src/dst"}, "seed", True),
])
def test_is_defect_matrix(rec, phase, expected):
    assert is_defect(rec, phase=phase) is expected


# --------------------------------------------------------------------------- #
# 2 + 3. honest stage accounting, seed returns outcomes
# --------------------------------------------------------------------------- #
def test_apply_stage_counts_failures_and_warns(tmp_path, caplog):
    ap = _applier(tmp_path)
    recs = iter([
        {"id": "op-ok", "ok": True, "status": "applied", "silent": True},
        {"id": "op-bad", "ok": False, "status": "unresolved",
         "reason": "no admin URL for gmail", "silent": True},
    ])
    ap._apply_api_mutation = lambda *a, **k: next(recs)  # type: ignore
    stage = _stage(silent=[{"id": "op-ok"}, {"id": "op-bad"}])

    with caplog.at_level(logging.INFO):
        outcomes = ap.apply_stage(stage, 1)

    assert len(outcomes) == 2
    entry = json.loads(
        (tmp_path / "inject_timeline.jsonl").read_text().strip().splitlines()[-1])
    assert entry["type"] == "inject.stage.applied"
    assert entry["applied_ops"] == 1
    assert entry["failed_ops"] == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "FAILED" in warnings[0].getMessage()
    assert "no admin URL for gmail" in warnings[0].getMessage()


def test_apply_stage_all_ok_logs_info_not_warning(tmp_path, caplog):
    ap = _applier(tmp_path)
    ap._apply_api_mutation = (  # type: ignore
        lambda *a, **k: {"id": "op", "ok": True, "status": "applied", "silent": True})
    with caplog.at_level(logging.INFO):
        ap.apply_stage(_stage(silent=[{"id": "op"}]), 1)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    entry = json.loads(
        (tmp_path / "inject_timeline.jsonl").read_text().strip().splitlines()[-1])
    assert entry["applied_ops"] == 1 and entry["failed_ops"] == 0


def test_seed_returns_outcomes(tmp_path):
    ap = _applier(tmp_path)
    seed_stage = InjectStage(index=0, name="seed", from_turn=None, to_turn=0,
                             filesystem=[{"id": "f1", "action": "copy",
                                          "src": "a", "dst": "/b"}],
                             loud=[], silent=[], source=str(tmp_path / "m.json"))
    script = InjectScript(description="test", stages=[seed_stage])
    outcomes = ap.seed(script)
    assert isinstance(outcomes, list) and len(outcomes) == 1
    # no copy hook configured -> benign skip at seed
    assert outcomes[0]["status"] == "skipped"
    assert is_defect(outcomes[0], phase="seed") is False


# --------------------------------------------------------------------------- #
# 4. REST path: status string + http int + read-back
# --------------------------------------------------------------------------- #
def _rest_applier(tmp_path, live_rows):
    """Applier with a scripted admin plane: live_rows is mutated by 'patch'."""
    ap = _applier(tmp_path)
    ap._resolve_target = lambda api, op: ("pages", "pk1", {"msrp": 15.99}, [])  # type: ignore

    def fake_admin_get(api, suffix):
        return dict(live_rows)

    def fake_admin_patch(api, table, pk, fields):
        live_rows.update(fields)
        return {"ok": True, "status": 200}

    ap._admin_get = fake_admin_get          # type: ignore
    ap._admin_patch = fake_admin_patch      # type: ignore
    return ap


def test_rest_path_string_status_http_and_verified(tmp_path):
    live = {"msrp": 14.99}
    ap = _rest_applier(tmp_path, live)
    rec = ap._apply_api_mutation(
        {"id": "sm1", "service": "notion-api", "method": "PATCH",
         "path": "/v1/pages/pk1"}, _stage(), 1, silent=True)
    assert rec["ok"] is True
    assert rec["status"] == "applied"       # string, not the int 200
    assert rec["http"] == 200
    assert rec["before"] == {"msrp": 14.99}
    assert rec["after"] == {"msrp": 15.99}  # LIVE value, read back
    assert rec["verified"] is True
    assert rec["changed"] is True
    assert is_defect(rec) is False


def test_rest_path_readback_mismatch_is_defect(tmp_path):
    live = {"msrp": 14.99}
    ap = _rest_applier(tmp_path, live)
    # Patch "succeeds" (200) but the store never takes the value.
    ap._admin_patch = lambda *a, **k: {"ok": True, "status": 200}  # type: ignore
    rec = ap._apply_api_mutation(
        {"id": "sm1", "service": "notion-api", "method": "PATCH",
         "path": "/v1/pages/pk1"}, _stage(), 1, silent=True)
    assert rec["ok"] is False
    assert rec["status"] == "failed"
    assert rec["reason"] == "write not observed on read-back"
    assert is_defect(rec) is True


def test_unresolved_service_still_unresolved(tmp_path):
    ap = _applier(tmp_path, urls={"notion-api": "http://x"})
    rec = ap._apply_api_mutation(
        {"id": "x", "service": "gmail", "method": "POST",
         "path": "/admin/messages/upsert"}, _stage(), 1, silent=True)
    assert rec["ok"] is False and rec["status"] == "unresolved"
    assert "no admin URL for gmail" in rec["reason"]
    assert is_defect(rec) is True


# --------------------------------------------------------------------------- #
# 4b. C1 slug-normalization + C2 nested-body survival + C3 strict mapping,
#     end-to-end through the REAL _resolve_target/_extract_fields/_map path.
#     Only the admin HTTP plane (_admin_get/_admin_patch) is stubbed.
# --------------------------------------------------------------------------- #
def _live_store_applier(tmp_path, tables, urls=None):
    """Applier whose admin plane serves `tables` ({table: [rows]}) and applies
    shallow top-level patches to the matching row in place."""
    ap = _applier(tmp_path, urls=urls)

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

    ap._admin_get = fake_admin_get          # type: ignore
    ap._admin_patch = fake_admin_patch      # type: ignore
    return ap


def test_v1_nested_body_reaches_live_nested_column(tmp_path):
    # Classroom V1 hazard: seed flat dueDate_* is lifted to nested dueDate{} at
    # load, so the live row carries a nested "dueDate" column. A canonical-slug op
    # with a bare top-level nested body must (C2+C3) resolve and patch it.
    tables = {"coursework": [{
        "id": "901110051",
        "title": "Reading the Chart",
        "dueDate": {"year": 2027, "month": 1, "day": 20},
    }]}
    ap = _live_store_applier(
        tmp_path, tables, urls={"google-classroom-api": "http://x"})
    rec = ap._apply_api_mutation(
        {"id": "s2_due_move", "service": "google-classroom-api", "method": "PATCH",
         "path": "/v1/courses/801110001/courseWork/901110051",
         "body": {"dueDate": {"year": 2027, "month": 1, "day": 27}}},
        _stage(), 5, silent=True)
    assert "resolved_service" not in rec
    assert rec["service"] == "google-classroom-api"
    assert rec["ok"] is True and rec["status"] == "applied"
    assert "unmapped_fields" not in rec                        # C3: clean
    assert tables["coursework"][0]["dueDate"] == {            # C2: nested landed
        "year": 2027, "month": 1, "day": 27}
    assert is_defect(rec) is False


def test_v2_rename_drop_unmapped_field_is_partial_defect(tmp_path):
    # V2 hazard: author targets a seed column name that the coercer renamed away.
    # One field maps (title), one does not (subject_line -> no live column).
    # C3 Interpretation-B: patch the mapped one, flag the op as a partial defect.
    tables = {"campaigns": [{"id": "c1", "title": "old"}]}
    ap = _live_store_applier(
        tmp_path, tables, urls={"mailchimp-api": "http://x"})
    rec = ap._apply_api_mutation(
        {"id": "s1", "service": "mailchimp-api", "method": "PATCH",
         "path": "/campaigns/c1",
         "body": {"fields": {"title": "new", "subject_line": "hello"}}},
        _stage(), 3, silent=True)
    assert rec["ok"] is False
    assert rec["status"] == "partial"
    assert rec["verified"] is False
    assert rec["unmapped_fields"] == ["subject_line"]
    assert "unmapped fields dropped" in rec["reason"]
    assert tables["campaigns"][0]["title"] == "new"   # mapped field still landed
    assert is_defect(rec) is True


def test_all_fields_unmapped_is_unresolved(tmp_path):
    # If NOTHING maps to a live column, the row can't be located -> unresolved.
    tables = {"campaigns": [{"id": "c1", "title": "old"}]}
    ap = _live_store_applier(
        tmp_path, tables, urls={"mailchimp-api": "http://x"})
    rec = ap._apply_api_mutation(
        {"id": "s1", "service": "mailchimp-api", "method": "PATCH",
         "path": "/campaigns/c1",
         "body": {"fields": {"subject_line": "hello", "from_name": "x"}}},
        _stage(), 3, silent=True)
    assert rec["ok"] is False and rec["status"] == "unresolved"
    assert is_defect(rec) is True


# --------------------------------------------------------------------------- #
# 5. doc_set read-back
# --------------------------------------------------------------------------- #
def test_doc_set_reads_back_live_value(tmp_path):
    ap = _applier(tmp_path)
    doc = {"pk1": {"msrp": {"type": "number", "value": 14.99}}}

    def fake_admin_get(api, suffix):
        return json.loads(json.dumps(doc))  # deep copy of live doc

    def fake_admin_post(api, suffix, body):
        doc.update(body["fields"])
        return {"ok": True, "status": 200}

    ap._admin_get = fake_admin_get   # type: ignore
    ap._admin_post = fake_admin_post  # type: ignore
    res = ap._admin_doc_set("notion-api", "properties",
                            ["pk1", "msrp"], {"type": "number", "value": 15.99})
    assert res["ok"] is True and res["verified"] is True
    assert res["after"] == {"type": "number", "value": 15.99}
    assert res["changed"] is True


def test_doc_set_merge_that_does_not_stick_fails(tmp_path):
    ap = _applier(tmp_path)
    doc = {"pk1": {"msrp": {"value": 14.99}}}
    ap._admin_get = lambda api, suffix: json.loads(json.dumps(doc))  # type: ignore
    ap._admin_post = lambda *a, **k: {"ok": True, "status": 200}     # type: ignore  # no-op merge
    res = ap._admin_doc_set("notion-api", "properties", ["pk1", "msrp"], {"value": 15.99})
    assert res["ok"] is False and res["verified"] is False
    assert res["reason"] == "write not observed on read-back"


# --------------------------------------------------------------------------- #
# 6. run_batch stamping + pass_summary forwarding
# --------------------------------------------------------------------------- #
def _run_batch_mod():
    import importlib
    import eval.run_batch as rb
    return rb


def test_augment_stamps_injection_flags():
    rb = _run_batch_mod()
    scores = {"overall_score": 1.0}
    result = {"test_result": {}, "injection_defects": [
        {"stage": "s1", "id": "op", "status": "unresolved", "reason": "r"}]}
    rb._augment_score_with_combined_rewards(scores, result)
    assert scores["injection_ok"] is False
    assert scores["injection_defects"][0]["status"] == "unresolved"

    clean = {"overall_score": 1.0}
    rb._augment_score_with_combined_rewards(clean, {"test_result": {}})
    assert clean["injection_ok"] is True
    assert clean["injection_defects"] == []


def test_pass_summary_entry_forwards_injection_flag():
    rb = _run_batch_mod()
    bad = rb._pass_summary_entry(1, {"overall_score": 0.5, "injection_ok": False},
                                 {"tests_total": 0})
    assert bad["injection_ok"] is False
    good = rb._pass_summary_entry(1, {"overall_score": 0.5, "injection_ok": True},
                                  {"tests_total": 0})
    assert "injection_ok" not in good


def test_pass_summary_doc_excludes_injection_failed_by_default(monkeypatch):
    rb = _run_batch_mod()
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
    per_run = [
        {"run_index": 1, "reward": 1.0, "combined_reward": 1.0,
         "rubric_reward": 1.0, "rubric_weights_percentage": 100.0},
        {"run_index": 2, "reward": 0.0, "combined_reward": 0.0,
         "rubric_reward": 0.0, "rubric_weights_percentage": 0.0,
         "injection_ok": False},
    ]
    doc = rb._pass_summary_doc("m", [dict(r) for r in per_run])
    assert doc["average_reward"] == 1.0
    assert doc["runs_used"] == 1
    assert doc["runs_excluded_injection_failed"] == 1
    assert doc["runs"] == 2


def test_pass_summary_doc_include_invalid_folds_injection_back(monkeypatch):
    rb = _run_batch_mod()
    monkeypatch.setenv("WCB_INCLUDE_INVALID_RUNS", "1")
    per_run = [
        {"run_index": 1, "reward": 1.0, "combined_reward": 1.0,
         "rubric_reward": 1.0, "rubric_weights_percentage": 100.0},
        {"run_index": 2, "reward": 0.0, "combined_reward": 0.0,
         "rubric_reward": 0.0, "rubric_weights_percentage": 0.0,
         "injection_ok": False},
    ]
    doc = rb._pass_summary_doc("m", [dict(r) for r in per_run])
    assert doc["average_reward"] == 0.5
    assert "runs_excluded_injection_failed" not in doc


def test_pass_summary_doc_excludes_eval_skipped_by_default(monkeypatch):
    rb = _run_batch_mod()
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    entry = rb._pass_summary_entry(
        2, {"overall_score": None, "eval_skipped": "trajectory empty: no assistant messages"},
        {"tests_total": 0})
    assert entry["eval_skipped"] == "trajectory empty: no assistant messages"
    per_run = [
        {"run_index": 1, "reward": 0.8, "combined_reward": 0.8,
         "rubric_reward": 0.8, "rubric_weights_percentage": 80.0},
        entry,
    ]
    doc = rb._pass_summary_doc("m", per_run)
    assert doc["average_reward"] == 0.8
    assert doc["runs_excluded_unmeasured"] == 1


def test_pass_summary_doc_legacy_entry_without_flags_not_excluded(monkeypatch):
    rb = _run_batch_mod()
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
    per_run = [
        {"run_index": 1, "reward": 0.5, "combined_reward": 0.5,
         "rubric_reward": 0.5, "rubric_weights_percentage": 50.0},
    ]
    doc = rb._pass_summary_doc("m", per_run)
    assert doc["average_reward"] == 0.5
    assert "runs_used" not in doc
    assert "runs_excluded_injection_failed" not in doc


def test_pass_summary_doc_all_runs_excluded_flag(monkeypatch):
    rb = _run_batch_mod()
    monkeypatch.delenv("WCB_INCLUDE_INVALID_RUNS", raising=False)
    per_run = [
        {"run_index": 1, "reward": 0.0, "combined_reward": 0.0,
         "rubric_reward": 0.0, "rubric_weights_percentage": 0.0,
         "injection_ok": False},
    ]
    doc = rb._pass_summary_doc("m", per_run)
    assert doc["all_runs_excluded"] is True
    assert doc["runs_used"] == 0


# --------------------------------------------------------------------------- #
# Artifacts diff must not credit harness-injected files to the agent
# --------------------------------------------------------------------------- #
def test_injected_paths_extracts_applied_fs_dsts(tmp_path):
    from src.utils.docker_utils import _injected_paths

    tl = tmp_path / "inject_timeline.jsonl"
    tl.write_text("\n".join([
        json.dumps({"type": "inject.fs", "ok": True,
                    "dst": "/workspace/home/home/Documents/order_update.txt"}),
        json.dumps({"type": "inject.fs", "ok": True,
                    "dst": "/workspace/home/home/Pictures/label.txt"}),
        # not applied -> created no file -> must NOT be excluded
        json.dumps({"type": "inject.fs", "ok": False,
                    "dst": "/workspace/home/home/Documents/never.txt"}),
        # non-fs events are irrelevant
        json.dumps({"type": "inject.api", "ok": True, "pk": "msg-1"}),
        "not json at all",
    ]), encoding="utf-8")

    assert _injected_paths(tl) == {
        "home/home/Documents/order_update.txt",
        "home/home/Pictures/label.txt",
    }


def test_injected_paths_missing_or_none_is_safe(tmp_path):
    from src.utils.docker_utils import _injected_paths

    assert _injected_paths(None) == set()
    assert _injected_paths(tmp_path / "absent.jsonl") == set()


def test_harness_bookkeeping_excludes_no_persona_markdown():
    """An agent editing MEMORY.md is real work; only unambiguous harness files
    may be withheld from artifacts/."""
    from src.utils.docker_utils import _HARNESS_BOOKKEEPING

    assert ".wildclaw_current_turn" in _HARNESS_BOOKKEEPING
    assert "spawn_tree.jsonl" in _HARNESS_BOOKKEEPING
    for persona in ("MEMORY.md", "SOUL.md", "USER.md", "IDENTITY.md"):
        assert persona not in _HARNESS_BOOKKEEPING


# --------------------------------------------------------------------------- #
# ...but an agent EDIT to an injected path is agent work and must be kept
# --------------------------------------------------------------------------- #
def _timeline_with_payloads(tmp_path: Path) -> Path:
    """Timeline whose applied copy ops carry a host `src` payload."""
    payload_dir = tmp_path / "stage1" / "files"
    payload_dir.mkdir(parents=True)
    notes = payload_dir / "order_update_v2.txt"
    notes.write_text("INJECTED v2\n", encoding="utf-8")

    tl = tmp_path / "inject_timeline.jsonl"
    tl.write_text("\n".join([
        json.dumps({"type": "inject.fs", "ok": True, "src": str(notes),
                    "dst": "/workspace/home/home/Documents/order_update.txt"}),
        json.dumps({"type": "inject.fs", "ok": False, "src": str(notes),
                    "dst": "/workspace/home/home/Documents/never.txt"}),
    ]), encoding="utf-8")
    return tl


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _stub_container(monkeypatch, changed: list[str], container_files: dict[str, str]):
    """Stub the two docker round-trips: the changed-path lister and the copier."""
    from src.utils import docker_utils as du

    monkeypatch.setattr(du.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=json.dumps(sorted(changed))))

    def _cp(task_id, src, dest):
        rel = src.split(du.TMP_WORKSPACE + "/", 1)[-1]
        if rel not in container_files:
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(container_files[rel], encoding="utf-8")
        return True

    monkeypatch.setattr(du, "_copy_file_from_container", _cp)


def test_injected_payloads_maps_dst_to_src(tmp_path):
    from src.utils.docker_utils import _injected_paths, _injected_payloads

    tl = _timeline_with_payloads(tmp_path)
    payloads = _injected_payloads(tl)

    assert set(payloads) == {"home/home/Documents/order_update.txt"}
    assert payloads["home/home/Documents/order_update.txt"].endswith("order_update_v2.txt")
    # _injected_paths stays exactly the key set -- same selection rule as before.
    assert _injected_paths(tl) == set(payloads)
    assert _injected_payloads(None) == {}
    assert _injected_payloads(tmp_path / "absent.jsonl") == {}


def test_untouched_injected_file_is_withheld(tmp_path, monkeypatch):
    """Content still identical to the payload -> the injector's file, not the agent's."""
    from src.utils.docker_utils import (
        _copy_changed_workspace_outputs_from_container,
        _injected_payloads,
    )

    tl = _timeline_with_payloads(tmp_path)
    payloads = _injected_payloads(tl)
    rel = "home/home/Documents/order_update.txt"
    dest = tmp_path / "artifacts"
    dest.mkdir()

    _stub_container(monkeypatch, [rel, "home/home/Documents/agent_made.txt"],
                    {rel: "INJECTED v2\n", "home/home/Documents/agent_made.txt": "mine\n"})

    excluded = _copy_changed_workspace_outputs_from_container(
        "t", dest, exclude=set(payloads), payloads=payloads)

    assert excluded == {rel}
    assert not (dest / rel).exists()                        # probe withdrawn
    assert not (dest / "home/home/Documents").exists() or \
        list((dest / "home/home/Documents").iterdir())      # no empty dirs left behind
    assert (dest / "home/home/Documents/agent_made.txt").read_text() == "mine\n"


def test_agent_edited_injected_file_is_kept(tmp_path, monkeypatch):
    """Content diverged from the payload -> the agent edited it after the drop."""
    from src.utils.docker_utils import (
        _copy_changed_workspace_outputs_from_container,
        _injected_payloads,
    )

    tl = _timeline_with_payloads(tmp_path)
    payloads = _injected_payloads(tl)
    rel = "home/home/Documents/order_update.txt"
    dest = tmp_path / "artifacts"
    dest.mkdir()

    _stub_container(monkeypatch, [rel], {rel: "INJECTED v2\nagent appended this\n"})

    excluded = _copy_changed_workspace_outputs_from_container(
        "t", dest, exclude=set(payloads), payloads=payloads)

    assert excluded == set()
    assert (dest / rel).read_text() == "INJECTED v2\nagent appended this\n"


def test_bookkeeping_and_missing_payload_still_withheld(tmp_path, monkeypatch):
    """No payload to compare against -> unconditional exclusion, as before."""
    from src.utils.docker_utils import _copy_changed_workspace_outputs_from_container

    dest = tmp_path / "artifacts"
    dest.mkdir()
    gone = "home/home/Documents/payload_deleted.txt"

    _stub_container(monkeypatch, [".wildclaw_current_turn", "spawn_tree.jsonl", gone],
                    {".wildclaw_current_turn": "2", "spawn_tree.jsonl": "{}",
                     gone: "whatever"})

    excluded = _copy_changed_workspace_outputs_from_container(
        "t", dest, exclude={gone},
        payloads={gone: str(tmp_path / "does_not_exist.txt")})

    assert excluded == {".wildclaw_current_turn", "spawn_tree.jsonl", gone}
    assert not (dest / gone).exists()


# --------------------------------------------------------------------------- #
# Per-turn simulated clock
# --------------------------------------------------------------------------- #
def test_compute_sim_clock_resolves_each_turn(tmp_path):
    from src.utils.sim_clock import compute_sim_clock, compute_sim_clock_for_turn

    (tmp_path / "prompts.json").write_text(json.dumps({
        "timezone": "America/Chicago",
        "turns": [
            {"turn": "T0", "timestamp": "2026-08-03T08:45:00-05:00", "message": "a"},
            {"turn": "T1", "timestamp": "2026-08-04T09:10:00-05:00", "message": "b"},
            {"turn": "T2", "timestamp": "2026-08-05T10:00:00-05:00", "message": "c"},
        ],
    }), encoding="utf-8")
    task = {"task_dir": str(tmp_path)}

    t0 = compute_sim_clock_for_turn(task, 0)
    t1 = compute_sim_clock_for_turn(task, 1)
    t2 = compute_sim_clock_for_turn(task, 2)
    assert t0.iso.startswith("2026-08-03T08:45")
    assert t1.iso.startswith("2026-08-04T09:10")
    assert t2.iso.startswith("2026-08-05T10:00")
    # each turn is a distinct instant, a full day apart
    assert t1.epoch_ms - t0.epoch_ms > 20 * 3600 * 1000
    # out-of-range turn resolves to None (caller keeps the previous anchor)
    assert compute_sim_clock_for_turn(task, 3) is None
    # back-compat: compute_sim_clock is still the turn-0 anchor
    assert compute_sim_clock(task).epoch_ms == t0.epoch_ms
