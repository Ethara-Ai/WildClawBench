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
    InjectScript, InjectApplier, parse_prompts_file,
)

TASK = ROOT / "input" / "LAYLA_001_october_grant_crunch"

pytestmark = []
if not TASK.is_dir():  # fixture not shipped -> skip cleanly
    import pytest
    pytestmark = pytest.mark.skip(reason="LAYLA fixture not present")


def test_prompts_parsing_yields_ordered_turns():
    turns = parse_prompts_file(TASK / "prompts.txt")
    assert len(turns) == 50
    assert turns[0].startswith("[TURN")
    assert "Wed 1 Oct" in turns[0]
    assert not turns[0].lstrip().startswith("#")


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
    table, pk, fields = resolved
    assert pk == "recUDI007"                 # {rec_UDI-2026-007} -> real record id
    assert fields.get("Yield_kg_m2") == 16.8  # yield_kg_m2 -> real column casing
    assert "_last_modified_by" not in fields  # underscore meta stripped
    assert "_last_modified_at" not in fields


# ---------------------------------------------------------------------------
# Talos task-pack export form (GLORIA-style) compatibility
# ---------------------------------------------------------------------------
#
# These tests run without the LAYLA fixture: they build tiny on-disk inject
# trees in tmp_path so they exercise the loader/parser additions in isolation.

import json
import pytest


def _write_stage(stage_dir: Path, payload: dict, filename: str) -> None:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / filename).write_text(json.dumps(payload))


def test_load_accepts_stagen_inject_filename(tmp_path):
    """Talos export uses STAGE{N}_INJECT.json instead of mutations.json."""
    inject_root = tmp_path / "inject"
    _write_stage(inject_root / "stage0", {"mutations": {"silent": []}}, "STAGE0_INJECT.json")
    _write_stage(
        inject_root / "stage1",
        {"applies_between_turns": ["T1", "T2"], "mutations": {"silent": []}},
        "STAGE1_INJECT.json",
    )

    sc = InjectScript.load(inject_root)
    by_idx = {s.index: s for s in sc.stages}
    assert set(by_idx) == {0, 1}
    assert by_idx[0].is_seed
    assert (by_idx[1].from_turn, by_idx[1].to_turn) == (1, 2)


def test_load_synthesizes_boundary_from_fires_after_turn(tmp_path):
    """fires_after_turn: N -> applied_between [N, N+1]; stage 0 stays seed."""
    inject_root = tmp_path / "inject"
    _write_stage(
        inject_root / "stage0",
        {"fires_after_turn": 0, "mutations": {"silent": []}},
        "STAGE0_INJECT.json",
    )
    _write_stage(
        inject_root / "stage1",
        {"fires_after_turn": 5, "mutations": {"silent": []}},
        "STAGE1_INJECT.json",
    )
    _write_stage(
        inject_root / "stage2",
        {"fires_after_turn": 12, "mutations": {"silent": []}},
        "STAGE2_INJECT.json",
    )

    sc = InjectScript.load(inject_root)
    by_idx = {s.index: s for s in sc.stages}
    # stage 0 is always seed regardless of fires_after_turn value
    assert by_idx[0].is_seed
    assert (by_idx[0].from_turn, by_idx[0].to_turn) == (None, None)
    # other stages synthesize a [N, N+1] boundary
    assert (by_idx[1].from_turn, by_idx[1].to_turn) == (5, 6)
    assert (by_idx[2].from_turn, by_idx[2].to_turn) == (12, 13)
    assert sc.stage_for_boundary(6) is by_idx[1]
    assert sc.stage_for_boundary(13) is by_idx[2]
    assert sc.stage_for_boundary(99) is None


def test_load_accepts_flat_injections_list_with_silent_flag(tmp_path):
    """Talos uses 'injections:[]' with per-op 'silent: true|false'."""
    inject_root = tmp_path / "inject"
    _write_stage(
        inject_root / "stage0",
        {
            "injections": [
                {"id": "F0-A", "kind": "filesystem", "copy_into_workspace": ["data/x.txt", "x.txt"]},
                {"id": "LOUD-A", "service": "airtable-api", "method": "POST",
                 "path": "/v0/seed", "body": {"x": 1}, "silent": False},
                {"id": "SM-A", "service": "airtable-api", "method": "PATCH",
                 "path": "/v0/things/recA", "body": {"fields": {"x": 2}}, "silent": True},
            ],
        },
        "STAGE0_INJECT.json",
    )

    sc = InjectScript.load(inject_root)
    [stage0] = sc.stages
    # silent flag routes to silent bucket; missing/False routes to loud
    silent_ids = [op.get("id") for op in stage0.silent]
    loud_ids = [op.get("id") for op in stage0.loud]
    fs_ids = [op.get("id") for op in stage0.filesystem]
    assert silent_ids == ["SM-A"]
    assert loud_ids == ["LOUD-A"]
    assert fs_ids == ["F0-A"]


def test_parse_prompts_file_accepts_turn_without_t_prefix(tmp_path):
    """GLORIA uses '--- TURN <n> ---'; LAYLA uses '--- TURN T<n> ---'.

    Header inner text is preserved as a bracket-tagged first line of the body
    so personas can route on the per-turn label (e.g. ``Multi-Agent`` vs
    ``Light``).
    """
    p = tmp_path / "prompts.txt"
    p.write_text(
        "# header comment\n"
        "--- TURN 1 (Day 1, 08:00, Light) ---\n"
        "first turn body line one\n"
        "first turn body line two\n"
        "--- TURN T2 (Day 1, 09:00, Multi-Agent) ---\n"
        "second turn body\n"
        "--- TURN 3 ---\n"
        "third turn body\n"
    )
    turns = parse_prompts_file(p)
    assert len(turns) == 3
    assert turns[0].startswith("[TURN 1 (Day 1, 08:00, Light)]")
    assert "first turn body line one" in turns[0]
    assert "first turn body line two" in turns[0]
    assert turns[1].startswith("[TURN T2 (Day 1, 09:00, Multi-Agent)]")
    assert "second turn body" in turns[1]
    assert turns[2].startswith("[TURN 3]")
    assert "third turn body" in turns[2]
    assert "header comment" not in "\n".join(turns)
