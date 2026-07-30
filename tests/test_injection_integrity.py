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
    # seed-time fs op, copy hook absent -> benign by design
    ({"ok": False, "status": "skipped", "action": "copy",
      "reason": "no workspace copy hook"}, "seed", False),
    # seed-time fs op, hook returned False (container not up) -> benign
    ({"ok": False, "status": "copied", "action": "copy"}, "seed", False),
    ({"ok": False, "status": "mkdir", "action": "mkdir"}, "seed", False),
    # same fs failure MID-RUN is a real defect
    ({"ok": False, "status": "copied", "action": "copy"}, "stage", True),
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
    ap._resolve_target = lambda api, op: ("pages", "pk1", {"msrp": 15.99})  # type: ignore

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
