"""Offline tests for the Talos inject-format director (no Docker required).

Exercises the three pieces that have no network dependency:
  * prompts.txt -> ordered per-turn wake-up list
  * inject/stageN/mutations.json -> InjectScript stages + boundary mapping
  * apply-time resolution of a silent REST mutation against live admin state,
    covering the LAYLA quirks (placeholder ids, field-name casing, _meta strip).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.inject_director import (  # noqa: E402
    InjectScript, InjectApplier, InjectStage, parse_prompts_file,
)

TASK = ROOT / "input" / "LAYLA_001_october_grant_crunch"

import pytest  # noqa: E402

# Scope the fixture skip to the LAYLA-dependent tests only; the unit tests below
# that build their own InjectStage must still run without the fixture.
layla_only = pytest.mark.skipif(
    not TASK.is_dir(), reason="LAYLA fixture not present")


@layla_only
def test_prompts_parsing_yields_ordered_turns():
    turns = parse_prompts_file(TASK / "prompts.txt")
    assert len(turns) == 50
    assert turns[0].startswith("Wed 1 Oct")
    # banner/comment lines must not leak into a turn body
    assert not turns[0].lstrip().startswith("#")


@layla_only
def test_inject_script_stages_and_boundaries():
    sc = InjectScript.load(TASK / "inject")
    by_idx = {s.index: s for s in sc.stages}
    assert set(by_idx) == {0, 1, 2, 3}
    assert by_idx[0].is_seed and by_idx[0].from_turn is None
    # stage1 applies between T12 and T13
    assert (by_idx[1].from_turn, by_idx[1].to_turn) == (12, 13)
    assert sc.stage_for_boundary(13) is by_idx[1]
    assert sc.stage_for_boundary(26) is by_idx[2]
    assert sc.stage_for_boundary(39) is by_idx[3]
    assert sc.stage_for_boundary(7) is None
    # stage3 ships its mutations as a list shape; the parser must still classify
    assert by_idx[3].silent or by_idx[3].filesystem


@layla_only
def test_sm3_resolves_against_live_store_despite_placeholder_and_casing():
    import csv

    sc = InjectScript.load(TASK / "inject")
    sm3 = next(s for s in sc.stages if s.index == 1).silent[0]
    assert sm3["id"] == "SM3"

    with open(TASK / "mock_data/airtable-api/records_plots.csv") as f:
        plots = [dict(r) for r in csv.DictReader(f)]
    live = {"airtable-api": {"records_tblFieldTrialUdi": plots}}

    class FakeApplier(InjectApplier):
        def _admin_get(self, api, suffix):
            if suffix == "/admin/tables":
                return [{"name": n} for n in live.get(api, {})]
            if suffix.startswith("/admin/data/"):
                return live.get(api, {}).get(suffix.split("/admin/data/")[1], [])
            return None

    ap = FakeApplier({"airtable-api": "http://x"}, None, Path("/tmp/inj_test_timeline.jsonl"))
    resolved = ap._resolve_target("airtable-api", sm3)
    assert resolved is not None, "SM3 must resolve against the live store"
    table, pk, fields, unmapped = resolved
    assert pk == "recUDI007"                 # {rec_UDI-2026-007} -> real record id
    assert fields.get("Yield_kg_m2") == 16.8  # yield_kg_m2 -> real column casing
    assert "_last_modified_by" not in fields  # underscore meta stripped
    assert "_last_modified_at" not in fields
    assert unmapped == []                     # every SM3 field maps to a live column


def test_mid_run_loud_op_is_applied_visibly(tmp_path):
    """A `loud` op in a mid-run stage must fire as a VISIBLE (silent=False) API
    mutation — not silently dropped, the pre-fix behaviour."""
    calls = []

    class RecordingApplier(InjectApplier):
        def _apply_api_mutation(self, op, stage, turn_index, silent):
            calls.append({"id": op.get("id"), "silent": silent})
            return {"id": op.get("id"), "silent": silent, "ok": True, "status": "applied"}

    ap = RecordingApplier({}, None, tmp_path / "timeline.jsonl")
    stage = InjectStage(
        index=1, name="overnight_t12_to_t13", from_turn=12, to_turn=13,
        silent=[{"id": "S1", "service": "gmail-api"}],
        loud=[{"id": "L1-G13", "service": "gmail-api",
               "admin": {"table": "messages", "op": "upsert"}}],
    )
    ap.apply_stage(stage, turn_index=13)

    by_id = {c["id"]: c["silent"] for c in calls}
    assert by_id == {"S1": True, "L1-G13": False}, (
        "mid-run loud op must be applied with silent=False alongside the silent op")

    # timeline summary records both buckets
    line = (tmp_path / "timeline.jsonl").read_text().strip().splitlines()[-1]
    import json
    entry = json.loads(line)
    assert entry["silent_ops"] == 1 and entry["loud_ops"] == 1


# --------------------------------------------------------------------------- #
# Filesystem-op allowlist dispatch (_apply_filesystem). The copy hook supports
# ONLY {copy, mkdir}; any other action is rejected as status='invalid' (a
# defect) instead of the old silent no-op that let mid-run edits vanish.
# --------------------------------------------------------------------------- #

def _fs_stage(tmp_path, fs_ops):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    return InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=list(fs_ops), loud=[], silent=[],
        source=str(stage_dir / "mutations.json"),
    )


def _fs_applier(tmp_path, copies):
    def hook(host_src, dst, mkdir=False):
        copies.append({"src": str(host_src) if host_src else None,
                       "dst": dst, "mkdir": mkdir})
        return True
    return InjectApplier(
        {}, None, tmp_path / "timeline.jsonl",
        inject_root=tmp_path / "inject",
        copy_into_workspace=hook,
    )


@pytest.mark.parametrize("bad_action", ["patch", "delete", "append", "move", None])
def test_fs_disallowed_action_is_rejected_as_invalid(tmp_path, bad_action):
    copies = []
    ap = _fs_applier(tmp_path, copies)
    stage = _fs_stage(tmp_path, [
        {"id": "fs-bad", "action": bad_action,
         "src": "note.txt", "dst": "/workspace/note.txt"}])
    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is False
    assert rec["status"] == "invalid"
    assert "not supported" in rec["reason"]
    # the copy hook must NEVER have been invoked for a rejected action
    assert copies == []
    # a rejected op is a defect in BOTH phases (mid-run and seed)
    from src.utils.inject_director import is_defect
    assert is_defect(rec, phase="stage") is True
    assert is_defect(rec, phase="seed") is True


def test_fs_copy_action_lands_via_hook(tmp_path):
    # a real source file must exist under the stage dir for the copy path
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    (stage_dir / "note.txt").write_text("hello from inject", encoding="utf-8")
    copies = []
    ap = _fs_applier(tmp_path, copies)
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-ok", "action": "copy",
                     "src": "note.txt", "dst": "/workspace/note.txt"}],
        loud=[], silent=[], source=str(stage_dir / "mutations.json"))
    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is True and rec["status"] == "copied"
    assert len(copies) == 1
    assert copies[0]["dst"] == "/workspace/note.txt"
    assert copies[0]["src"].endswith("note.txt")


def test_fs_mkdir_action_lands_via_hook(tmp_path):
    copies = []
    ap = _fs_applier(tmp_path, copies)
    stage = _fs_stage(tmp_path, [
        {"id": "fs-mk", "action": "mkdir", "dst": "/workspace/newdir"}])
    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is True and rec["status"] == "mkdir"
    assert copies == [{"src": None, "dst": "/workspace/newdir", "mkdir": True}]


# --------------------------------------------------------------------------- #
# Mapped-dst bookkeeping + mtime threading. The timeline must record where a
# payload actually LANDED (the raw authored dst can be an alias), and per-turn
# drops must be stamped so they sort after the T0-stamped baseline.
# --------------------------------------------------------------------------- #

class _Outcome:
    def __init__(self, ok, mapped_dst=None, reason=""):
        self.ok = ok
        self.mapped_dst = mapped_dst
        self.reason = reason

    def __bool__(self):
        return bool(self.ok)


def _outcome_applier(tmp_path, calls, outcome, inject_root=None):
    def hook(host_src, dst, mkdir=False, mtime_epoch_ms=None):
        calls.append({"src": str(host_src) if host_src else None, "dst": dst,
                      "mkdir": mkdir, "mtime_epoch_ms": mtime_epoch_ms})
        return outcome
    return InjectApplier(
        {}, None, tmp_path / "timeline.jsonl",
        inject_root=inject_root or (tmp_path / "inject"),
        copy_into_workspace=hook,
    )


def _copy_op_stage(tmp_path, dst="/workspace/note.txt"):
    stage_dir = tmp_path / "inject" / "stage1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "mutations.json").write_text("{}", encoding="utf-8")
    (stage_dir / "note.txt").write_text("hello", encoding="utf-8")
    return InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-1", "action": "copy", "src": "note.txt", "dst": dst}],
        loud=[], silent=[], source=str(stage_dir / "mutations.json"),
    )


def test_fs_record_carries_mapped_dst(tmp_path):
    calls = []
    ap = _outcome_applier(tmp_path, calls,
                          _Outcome(True, "/tmp_workspace/home/note.txt"))
    stage = _copy_op_stage(tmp_path, dst="/data/home/note.txt")

    rec = ap._apply_filesystem(stage.filesystem[0], stage)

    assert rec["ok"] is True
    assert rec["dst"] == "/data/home/note.txt", "raw authored dst is preserved"
    assert rec["mapped_dst"] == "/tmp_workspace/home/note.txt"


def test_fs_dst_outside_workspace_is_a_defect(tmp_path):
    from src.utils.inject_director import is_defect

    calls = []
    ap = _outcome_applier(tmp_path, calls,
                          _Outcome(False, None, "dst_outside_workspace"))
    stage = _copy_op_stage(tmp_path, dst="/etc/cron.d/evil")

    rec = ap._apply_filesystem(stage.filesystem[0], stage)

    assert rec["ok"] is False
    assert rec["status"] == "invalid_dst"
    assert rec["reason"] == "dst_outside_workspace"
    assert is_defect(rec, phase="stage") is True
    assert is_defect(rec, phase="seed") is True


def test_fs_mkdir_dst_outside_workspace_is_a_defect(tmp_path):
    calls = []
    ap = _outcome_applier(tmp_path, calls,
                          _Outcome(False, None, "dst_outside_workspace"))
    stage = InjectStage(
        index=1, name="s1", from_turn=0, to_turn=1,
        filesystem=[{"id": "fs-mk", "action": "mkdir", "dst": "/etc/evil"}],
        loud=[], silent=[], source="")

    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is False and rec["status"] == "invalid_dst"


def test_fs_warns_when_mapped_dst_leaves_staged_home_tree(tmp_path):
    (tmp_path / "data" / "home" / "Pictures").mkdir(parents=True)
    calls = []
    ap = _outcome_applier(tmp_path, calls, _Outcome(True, "/tmp_workspace/note.txt"))
    stage = _copy_op_stage(tmp_path, dst="/workspace/note.txt")

    rec = ap._apply_filesystem(stage.filesystem[0], stage)

    assert rec["warning"] == "dst outside staged input tree"


def test_fs_no_warning_when_mapped_dst_stays_in_staged_home_tree(tmp_path):
    (tmp_path / "data" / "home" / "Pictures").mkdir(parents=True)
    calls = []
    ap = _outcome_applier(tmp_path, calls,
                          _Outcome(True, "/tmp_workspace/home/home/Pictures/note.txt"))
    stage = _copy_op_stage(tmp_path, dst="/workspace/home/home/Pictures/note.txt")

    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert "warning" not in rec


def test_apply_stage_threads_sim_epoch_to_copy_hook(tmp_path):
    calls = []
    ap = _outcome_applier(tmp_path, calls, _Outcome(True, "/tmp_workspace/note.txt"))
    stage = _copy_op_stage(tmp_path)

    ap.apply_stage(stage, turn_index=1, mtime_epoch_ms=1793000000000)

    assert [c["mtime_epoch_ms"] for c in calls] == [1793000000000]


def test_legacy_three_arg_hook_survives_mtime_threading(tmp_path):
    """Stubs written to the published fn(host_src, dst, mkdir=False) contract
    must not be handed a kwarg they cannot accept."""
    calls = []

    def legacy_hook(host_src, dst, mkdir=False):
        calls.append({"dst": dst, "mkdir": mkdir})
        return True

    ap = InjectApplier({}, None, tmp_path / "timeline.jsonl",
                       inject_root=tmp_path / "inject",
                       copy_into_workspace=legacy_hook)
    stage = _copy_op_stage(tmp_path)

    outcomes = ap.apply_stage(stage, turn_index=1, mtime_epoch_ms=1793000000000)

    assert calls == [{"dst": "/workspace/note.txt", "mkdir": False}]
    assert outcomes[0]["ok"] is True and outcomes[0]["status"] == "copied"


def test_plain_bool_hook_return_still_supported(tmp_path):
    calls = []
    ap = _outcome_applier(tmp_path, calls, True)
    stage = _copy_op_stage(tmp_path)

    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is True and "mapped_dst" not in rec


def test_none_hook_return_still_means_container_down(tmp_path):
    calls = []
    ap = _outcome_applier(tmp_path, calls, None)
    stage = _copy_op_stage(tmp_path)

    rec = ap._apply_filesystem(stage.filesystem[0], stage)
    assert rec["ok"] is False and rec["status"] == "skipped_container_down"
