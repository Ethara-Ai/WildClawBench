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
