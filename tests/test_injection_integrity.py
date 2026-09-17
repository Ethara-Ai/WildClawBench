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
    MtimeStamp,
    NarrativeClock,
    is_defect,
    resolve_stage_mtime,
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


# --------------------------------------------------------------------------- #
# 7. Workspace dst mapping + copy verification (docker_utils inject fs hook).
#    Every authored spelling of the workspace must land on the SAME tree, an
#    absolute dst that would escape it is refused rather than written, and a
#    green copy means the bytes were read back from the container.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dst,expected", [
    ("/workspace/a/b.txt", "/tmp_workspace/a/b.txt"),
    ("/app/a/b.txt", "/tmp_workspace/a/b.txt"),
    ("/root/workspace/a/b.txt", "/tmp_workspace/a/b.txt"),
    ("/root/.openclaw/workspace/a/b.txt", "/tmp_workspace/a/b.txt"),
    ("~/workspace/a/b.txt", "/tmp_workspace/a/b.txt"),
    ("/data/home/Pictures/x.png", "/tmp_workspace/home/Pictures/x.png"),
    ("data/home/Pictures/x.png", "/tmp_workspace/home/Pictures/x.png"),
    ("notes/x.txt", "/tmp_workspace/notes/x.txt"),
    ("/workspace", "/tmp_workspace"),
    ("/app", "/tmp_workspace"),
    ("/tmp_workspace/already/mapped.txt", "/tmp_workspace/already/mapped.txt"),
])
def test_map_workspace_dst_normalizes_every_alias(dst, expected):
    from src.utils.docker_utils import _map_workspace_dst

    assert _map_workspace_dst(dst) == expected


@pytest.mark.parametrize("dst", [
    "/etc/passwd", "/var/tmp/drop.txt", "/tmp_workspace_evil/x", "/home/user/x", ""])
def test_map_workspace_dst_refuses_paths_outside_workspace(dst):
    from src.utils.docker_utils import _map_workspace_dst

    assert _map_workspace_dst(dst) is None


def test_map_workspace_dst_escape_hatch_honors_absolute(monkeypatch):
    from src.utils.docker_utils import _map_workspace_dst

    monkeypatch.setenv("WCB_INJECT_ALLOW_ABS", "1")
    assert _map_workspace_dst("/var/tmp/drop.txt") == "/var/tmp/drop.txt"


def test_map_workspace_dst_warns_on_alias_rewrite(caplog):
    from src.utils.docker_utils import _map_workspace_dst

    with caplog.at_level(logging.WARNING, logger="src.utils.docker_utils"):
        _map_workspace_dst("/data/home/x.png")
    assert any("rewrote non-canonical dst" in r.getMessage()
               for r in caplog.records)


class _FakeRun:
    """Scripted subprocess.run stand-in keyed by a substring of the argv.

    ``touch -d @N`` is modelled as actually working: the epoch is remembered per
    dst and served back to the ``stat -c %Y`` read-back, so the post-copy mtime
    invariant passes by default. Pass ``mtimes={dst: epoch_or_None}`` to
    override that and simulate a stamp that did not stick.
    """

    def __init__(self, sizes=None, fail=(), mtimes=None):
        self.sizes = sizes or {}
        self.fail = set(fail)
        self.mtimes = mtimes or {}
        self.stamped = {}
        self.calls = []

    def __call__(self, cmd, *a, **kw):
        self.calls.append(list(cmd))
        joined = " ".join(str(c) for c in cmd)
        for token in self.fail:
            if token in joined:
                return _Completed(1, "", f"boom: {token}")
        if "inspect" in joined:
            return _Completed(0, "true", "")
        if "touch" in cmd:
            stamp = next((str(c)[1:] for c in cmd if str(c).startswith("@")), None)
            if stamp is not None:
                self.stamped[str(cmd[-1])] = int(stamp)
            return _Completed(0, "", "")
        if "wc -c" in joined:
            for path, size in self.sizes.items():
                if path in joined:
                    return _Completed(0, "" if size is None else str(size), "")
            return _Completed(0, "", "")
        if "stat -c %Y" in joined:
            for path, mtime in self.mtimes.items():
                if path in joined:
                    return _Completed(0, "" if mtime is None else str(mtime), "")
            for path, mtime in self.stamped.items():
                if path in joined:
                    return _Completed(0, str(mtime), "")
            return _Completed(0, "", "")
        return _Completed(0, "", "")


class _Completed:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _host_file(tmp_path, body="payload-bytes"):
    p = tmp_path / "payload.txt"
    p.write_text(body, encoding="utf-8")
    return p


def test_copy_into_workspace_verified_copy_returns_mapped_dst(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/home/x.txt": src.stat().st_size})
    monkeypatch.setattr(du.subprocess, "run", runner)

    res = du.copy_file_into_workspace("t", src, "/data/home/x.txt")

    assert res.ok is True and bool(res) is True
    assert res.mapped_dst == "/tmp_workspace/home/x.txt"


def test_copy_into_workspace_size_mismatch_is_failure(tmp_path, monkeypatch, caplog):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": 3})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.ERROR, logger="src.utils.docker_utils"):
        res = du.copy_file_into_workspace("t", src, "/workspace/x.txt")

    assert res.ok is False and res.reason == "size_mismatch"
    assert res.mapped_dst == "/tmp_workspace/x.txt"
    assert any("INJECT FS NOT PLACED" in r.getMessage() for r in caplog.records)


def test_copy_into_workspace_missing_file_is_failure(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    monkeypatch.setattr(du.subprocess, "run",
                        _FakeRun(sizes={"/tmp_workspace/x.txt": None}))

    res = du.copy_file_into_workspace("t", src, "/workspace/x.txt")
    assert res.ok is False and res.reason == "size_mismatch"


def test_copy_into_workspace_refuses_dst_outside_workspace(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun()
    monkeypatch.setattr(du.subprocess, "run", runner)

    res = du.copy_file_into_workspace("t", src, "/etc/cron.d/evil")

    assert res.ok is False and res.reason == "dst_outside_workspace"
    assert not any("docker" == c[0] and "cp" in c for c in runner.calls), (
        "a refused dst must never reach docker cp")


def test_copy_into_workspace_parent_mkdir_failure_is_reported(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    monkeypatch.setattr(du.subprocess, "run", _FakeRun(fail=("mkdir",)))

    res = du.copy_file_into_workspace("t", src, "/workspace/deep/x.txt")
    assert res.ok is False and res.reason == "mkdir_parent_failed"


def test_copy_into_workspace_mkdir_rc_is_checked(monkeypatch):
    from src.utils import docker_utils as du

    monkeypatch.setattr(du.subprocess, "run", _FakeRun(fail=("mkdir",)))
    res = du.copy_file_into_workspace("t", None, "/workspace/newdir", mkdir=True)
    assert res.ok is False and res.reason == "mkdir_failed"


def test_copy_into_workspace_container_down_is_warning(monkeypatch, caplog):
    from src.utils import docker_utils as du

    monkeypatch.setattr(du, "_container_running", lambda _t: False)
    with caplog.at_level(logging.WARNING, logger="src.utils.docker_utils"):
        res = du.copy_file_into_workspace("t", None, "/workspace/x.txt")

    assert res.ok is None and bool(res) is False
    assert any(r.levelno == logging.WARNING and "container not up" in r.getMessage()
               for r in caplog.records)


def test_copy_into_workspace_stamps_mtime_with_sim_epoch(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": src.stat().st_size})
    monkeypatch.setattr(du.subprocess, "run", runner)

    du.copy_file_into_workspace("t", src, "/workspace/x.txt",
                                mtime_epoch_ms=1793000000000)

    touch = [c for c in runner.calls if "touch" in c]
    assert touch == [["docker", "exec", "t", "touch", "-m", "-d", "@1793000000",
                      "/tmp_workspace/x.txt"]]


def test_copy_into_workspace_without_sim_epoch_touches_now(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": src.stat().st_size})
    monkeypatch.setattr(du.subprocess, "run", runner)

    du.copy_file_into_workspace("t", src, "/workspace/x.txt")

    touch = [c for c in runner.calls if "touch" in c]
    assert touch == [["docker", "exec", "t", "touch", "-m", "/tmp_workspace/x.txt"]]


def test_inject_data_into_workspace_stamps_baseline_at_t0(tmp_path, monkeypatch):
    """Baseline inputs get the task's T0 instant so per-turn drops sort after."""
    from src.utils import docker_utils as du

    (tmp_path / "prompts.json").write_text(json.dumps({
        "timezone": "America/Chicago",
        "turns": [{"turn": "T0", "timestamp": "2026-08-03T08:45:00-05:00"}],
    }), encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    runner = _FakeRun()
    monkeypatch.setattr(du.subprocess, "run", runner)
    du.inject_data_into_workspace("t", str(data_dir))

    stamps = [c for c in runner.calls if any("touch -m -d" in str(x) for x in c)]
    assert len(stamps) == 1
    expected_epoch = 1785764700  # 2026-08-03T08:45:00-05:00
    assert f"@{expected_epoch}" in stamps[0][-1]


def test_inject_data_into_workspace_without_prompts_json_skips_stamp(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runner = _FakeRun()
    monkeypatch.setattr(du.subprocess, "run", runner)
    du.inject_data_into_workspace("t", str(data_dir))

    assert not [c for c in runner.calls if any("touch" in str(x) for x in c)]


# --------------------------------------------------------------------------- #
# 8. End-to-end wiring with the REAL copy hook. Every other fs test stubs the
#    hook, so nothing else would catch InjectApplier and the run_batch closure
#    disagreeing about the mtime kwarg — the applier would silently drop the
#    stamp (or raise) with all stub-based tests still green.
# --------------------------------------------------------------------------- #

def _run_batch_shaped_hook(task_id):
    """Exact parameter shape of eval/run_batch.py::_copy_into_workspace."""
    from src.utils.docker_utils import copy_file_into_workspace

    def _copy_into_workspace(host_src, dst, mkdir=False, mtime_epoch_ms=None,
                             _tid=task_id):
        return copy_file_into_workspace(_tid, host_src, dst, mkdir=mkdir,
                                        mtime_epoch_ms=mtime_epoch_ms)
    return _copy_into_workspace


def test_real_hook_end_to_end_stamps_and_records_mapped_dst(tmp_path, monkeypatch):
    from src.utils import docker_utils as du
    from src.utils.inject_director import InjectApplier, InjectStage

    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    payload = stage_dir / "note.txt"
    payload.write_text("hello from inject", encoding="utf-8")

    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": payload.stat().st_size})
    monkeypatch.setattr(du.subprocess, "run", runner)

    ap = InjectApplier({}, None, tmp_path / "timeline.jsonl",
                       inject_root=tmp_path / "inject",
                       copy_into_workspace=_run_batch_shaped_hook("t"))
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-1", "action": "copy", "src": "note.txt",
                     "dst": "/data/home/note.txt"}],
        loud=[], silent=[], source=str(stage_dir / "mutations.json"))

    outcomes = ap.apply_stage(stage, turn_index=1,
                              clock=NarrativeClock(turn_epoch_ms=1793000000000))

    assert outcomes[0]["ok"] is True
    assert outcomes[0]["mapped_dst"] == "/tmp_workspace/home/note.txt"
    touch = [c for c in runner.calls if "touch" in c]
    assert touch == [["docker", "exec", "t", "touch", "-m", "-d", "@1793000000",
                      "/tmp_workspace/home/note.txt"]], (
        "the sim epoch must survive InjectApplier -> run_batch closure -> docker")


def test_real_hook_end_to_end_refuses_escaping_dst(tmp_path, monkeypatch):
    from src.utils import docker_utils as du
    from src.utils.inject_director import InjectApplier, InjectStage, is_defect

    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")

    runner = _FakeRun()
    monkeypatch.setattr(du.subprocess, "run", runner)

    ap = InjectApplier({}, None, tmp_path / "timeline.jsonl",
                       inject_root=tmp_path / "inject",
                       copy_into_workspace=_run_batch_shaped_hook("t"))
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-esc", "action": "copy", "src": "note.txt",
                     "dst": "/etc/cron.d/evil"}],
        loud=[], silent=[], source=str(stage_dir / "mutations.json"))

    outcomes = ap.apply_stage(stage, turn_index=1)

    assert outcomes[0]["status"] == "invalid_dst"
    assert outcomes[0]["reason"] == "dst_outside_workspace"
    assert is_defect(outcomes[0], phase="stage") is True
    assert not any("cp" in c for c in runner.calls)


# --------------------------------------------------------------------------- #
# 9. Narrative mtime resolution. The stamping machinery landed inert: the only
#    production caller could pass None (a turn with no resolvable sim clock),
#    and None silently skipped `touch -d` entirely, leaving the drop on its
#    authoring mtime — older than the T0-stamped baseline and therefore
#    invisible to the recency searches the scenario expects the agent to run.
# --------------------------------------------------------------------------- #

# The offset-aware shape mutations.json actually ships, and its epoch.
_STAGE_ISO = "2026-12-20T03:10:00-05:00"
_STAGE_MS = 1797754200000
_T0_MS = 1797735600000       # 2026-12-19T22:00:00-05:00, the baseline anchor
_TURN_MS = 1797775200000     # 2026-12-20T09:00:00-05:00, the boundary turn


def _fs_stage(tmp_path, *, ops, applied_at_epoch_ms=None, name="s1"):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    (stage_dir / "note.txt").write_text("payload", encoding="utf-8")
    return InjectStage(
        index=1, name=name, from_turn=0, to_turn=1, filesystem=list(ops),
        loud=[], silent=[], source=str(stage_dir / "mutations.json"),
        applied_at_epoch_ms=applied_at_epoch_ms)


def _copy_op(**extra):
    return {"id": "fs-1", "action": "copy", "src": "note.txt",
            "dst": "/workspace/home/note.txt", **extra}


def test_resolution_prefers_the_stage_applied_at_over_the_turn_clock(tmp_path):
    stage = _fs_stage(tmp_path, ops=[], applied_at_epoch_ms=_STAGE_MS)

    stamp = resolve_stage_mtime(
        stage, NarrativeClock(turn_epoch_ms=_TURN_MS, t0_epoch_ms=_T0_MS))

    assert stamp == MtimeStamp(_STAGE_MS, "stage")


def test_resolution_falls_back_to_the_boundary_turn_clock(tmp_path):
    stage = _fs_stage(tmp_path, ops=[])

    stamp = resolve_stage_mtime(
        stage, NarrativeClock(turn_epoch_ms=_TURN_MS, t0_epoch_ms=_T0_MS))

    assert stamp == MtimeStamp(_TURN_MS, "turn")


def test_resolution_falls_back_to_t0_loudly(tmp_path, caplog):
    stage = _fs_stage(tmp_path, ops=[])

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        stamp = resolve_stage_mtime(stage, NarrativeClock(t0_epoch_ms=_T0_MS))

    assert stamp == MtimeStamp(_T0_MS, "t0")
    assert any("T0 baseline epoch" in r.getMessage() for r in caplog.records), (
        "a T0 fallback ties the drop with the baseline; it must never be quiet")


def test_resolution_without_any_instant_is_loud_not_silent(tmp_path, caplog):
    stage = _fs_stage(tmp_path, ops=[])

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        stamp = resolve_stage_mtime(stage, None)

    assert stamp.epoch_ms is None and stamp.source == "unresolved"
    assert any("no narrative instant available" in r.getMessage()
               for r in caplog.records)


def test_inject_script_load_parses_applied_at_local_time(tmp_path):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True)
    (stage_dir / "mutations.json").write_text(json.dumps({
        "stage_name": "s1",
        "applies_between_turns": ["T0", "T1"],
        "applied_at_local_time": _STAGE_ISO,
        "mutations": {"filesystem": [_copy_op()]},
    }), encoding="utf-8")

    script = InjectScript.load(tmp_path / "inject")

    assert script.stages[0].applied_at_epoch_ms == _STAGE_MS


def test_inject_script_load_survives_a_naive_applied_at(tmp_path, caplog):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True)
    (stage_dir / "mutations.json").write_text(json.dumps({
        "stage_name": "s1",
        "applies_between_turns": ["T0", "T1"],
        "applied_at_local_time": "2026-12-20 03:10:00",
        "mutations": {"filesystem": [_copy_op()]},
    }), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        script = InjectScript.load(tmp_path / "inject")

    assert script.stages[0].applied_at_epoch_ms is None
    assert any("offset-aware" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw,expected", [
    (_STAGE_ISO, _STAGE_MS),
    ("2026-12-20T08:10:00Z", 1797754200000),
    (_STAGE_MS, _STAGE_MS),
    ("2026-12-20 03:10:00", None),
    ("not-a-time", None),
    (True, None),
    (None, None),
])
def test_parse_narrative_instant_accepts_iso_and_epoch_only(raw, expected):
    from src.utils.inject_director import parse_narrative_instant

    assert parse_narrative_instant(raw) == expected


def _stamped_epochs(runner):
    return [int(str(c[-2])[1:]) for c in runner.calls
            if "touch" in c and str(c[-2]).startswith("@")]


def _real_hook_applier(tmp_path):
    return InjectApplier({}, None, tmp_path / "timeline.jsonl",
                         inject_root=tmp_path / "inject",
                         copy_into_workspace=_run_batch_shaped_hook("t"))


def test_per_op_mtime_override_beats_the_stage_instant(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op(mtime="2026-03-02T11:00:00-05:00")],
                      applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    outcomes = _real_hook_applier(tmp_path).apply_stage(
        stage, turn_index=1,
        clock=NarrativeClock(turn_epoch_ms=_TURN_MS, t0_epoch_ms=_T0_MS))

    assert outcomes[0]["ok"] is True
    assert outcomes[0]["mtime_source"] == "op"
    assert _stamped_epochs(runner) == [1772467200]


def test_per_op_mtime_override_accepts_epoch_ms(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op(mtime=_T0_MS - 86_400_000)],
                      applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    outcomes = _real_hook_applier(tmp_path).apply_stage(stage, turn_index=1)

    assert outcomes[0]["mtime_epoch_ms"] == _T0_MS - 86_400_000


def test_unparseable_per_op_mtime_inherits_the_stage_instant(tmp_path, monkeypatch,
                                                             caplog):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op(mtime="yesterday-ish")],
                      applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        outcomes = _real_hook_applier(tmp_path).apply_stage(stage, turn_index=1)

    assert outcomes[0]["mtime_source"] == "stage"
    assert outcomes[0]["mtime_epoch_ms"] == _STAGE_MS
    assert any("override" in r.getMessage() for r in caplog.records)


def test_mtime_key_is_parsed_on_fs_ops_and_stripped_from_api_bodies():
    from src.utils.inject_director import INJECT_MTIME_KEY, _INJECT_ENVELOPE_KEYS

    assert INJECT_MTIME_KEY in _INJECT_ENVELOPE_KEYS, (
        "an API op carrying mtime must not have it written as a row column")
    assert InjectApplier._extract_fields(
        {"body": {INJECT_MTIME_KEY: _STAGE_ISO, "status": "late"}}) == {
            "status": "late"}


def test_stage_mtime_reaches_docker_touch_from_applied_at_alone(tmp_path,
                                                               monkeypatch):
    """No turn clock at all: the stage's own instant must still be stamped."""
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op()], applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    outcomes = _real_hook_applier(tmp_path).apply_stage(stage, turn_index=1)

    assert outcomes[0]["ok"] is True
    assert _stamped_epochs(runner) == [_STAGE_MS // 1000]


# --------------------------------------------------------------------------- #
# 10. Post-copy mtime invariant: `touch` can exit 0 and leave the old mtime.
# --------------------------------------------------------------------------- #

def test_mtime_that_did_not_stick_is_a_placement_failure(tmp_path, monkeypatch,
                                                         caplog):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": src.stat().st_size},
                      mtimes={"/tmp_workspace/x.txt": _T0_MS // 1000})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.ERROR, logger="src.utils.docker_utils"):
        res = du.copy_file_into_workspace("t", src, "/workspace/x.txt",
                                          mtime_epoch_ms=_STAGE_MS)

    assert res.ok is False and res.reason == "mtime_mismatch"
    assert any("INJECT FS NOT PLACED" in r.getMessage() for r in caplog.records)


def test_mtime_within_two_seconds_is_accepted(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": src.stat().st_size},
                      mtimes={"/tmp_workspace/x.txt": _STAGE_MS // 1000 + 2})
    monkeypatch.setattr(du.subprocess, "run", runner)

    res = du.copy_file_into_workspace("t", src, "/workspace/x.txt",
                                      mtime_epoch_ms=_STAGE_MS)
    assert res.ok is True


def test_unstampable_file_is_a_placement_failure(tmp_path, monkeypatch):
    from src.utils import docker_utils as du

    src = _host_file(tmp_path)
    runner = _FakeRun(sizes={"/tmp_workspace/x.txt": src.stat().st_size},
                      mtimes={"/tmp_workspace/x.txt": None})
    monkeypatch.setattr(du.subprocess, "run", runner)

    res = du.copy_file_into_workspace("t", src, "/workspace/x.txt",
                                      mtime_epoch_ms=_STAGE_MS)
    assert res.ok is False and res.reason == "mtime_mismatch"


def test_mtime_mismatch_is_recorded_failed_in_the_inject_timeline(tmp_path,
                                                                 monkeypatch):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op()], applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")},
                      mtimes={"/tmp_workspace/home/note.txt": _T0_MS // 1000})
    monkeypatch.setattr(du.subprocess, "run", runner)

    outcomes = _real_hook_applier(tmp_path).apply_stage(stage, turn_index=1)

    assert outcomes[0]["ok"] is False
    assert outcomes[0]["reason"] == "mtime_mismatch"
    assert is_defect(outcomes[0], phase="stage") is True
    entries = [json.loads(line) for line in
               (tmp_path / "timeline.jsonl").read_text().strip().splitlines()]
    fs_entry = next(e for e in entries if e["type"] == "inject.fs")
    assert fs_entry["ok"] is False and fs_entry["reason"] == "mtime_mismatch"
    stage_entry = next(e for e in entries if e["type"] == "inject.stage.applied")
    assert stage_entry["failed_ops"] == 1


# --------------------------------------------------------------------------- #
# 11. Recency-invisibility: a drop that does not sort after the T0 baseline.
# --------------------------------------------------------------------------- #

def test_stage_warns_when_a_drop_is_not_newer_than_the_baseline(tmp_path,
                                                                monkeypatch,
                                                                caplog):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op()],
                      applied_at_epoch_ms=_T0_MS - 1000)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        _real_hook_applier(tmp_path).apply_stage(
            stage, turn_index=1, clock=NarrativeClock(t0_epoch_ms=_T0_MS))

    assert any("invisible to the agent's recency searches" in r.getMessage()
               for r in caplog.records)
    entry = json.loads(
        (tmp_path / "timeline.jsonl").read_text().strip().splitlines()[-1])
    assert entry["recency_invisible_ops"] == ["fs-1"]


def test_a_drop_newer_than_the_baseline_does_not_warn(tmp_path, monkeypatch,
                                                      caplog):
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op()], applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        _real_hook_applier(tmp_path).apply_stage(
            stage, turn_index=1, clock=NarrativeClock(t0_epoch_ms=_T0_MS))

    assert not any("recency" in r.getMessage() for r in caplog.records)
    entry = json.loads(
        (tmp_path / "timeline.jsonl").read_text().strip().splitlines()[-1])
    assert entry["recency_invisible_ops"] == []
    assert entry["mtime_source"] == "stage"


def test_an_explicit_override_is_exempt_from_the_recency_warning(tmp_path,
                                                                 monkeypatch,
                                                                 caplog):
    """A deliberately buried document is SUPPOSED to look older than baseline."""
    from src.utils import docker_utils as du

    stage = _fs_stage(tmp_path, ops=[_copy_op(mtime=_T0_MS - 86_400_000)],
                      applied_at_epoch_ms=_STAGE_MS)
    runner = _FakeRun(sizes={"/tmp_workspace/home/note.txt": len("payload")})
    monkeypatch.setattr(du.subprocess, "run", runner)

    with caplog.at_level(logging.WARNING, logger="wildclaw.inject"):
        _real_hook_applier(tmp_path).apply_stage(
            stage, turn_index=1, clock=NarrativeClock(t0_epoch_ms=_T0_MS))

    assert not any("recency" in r.getMessage() for r in caplog.records)
