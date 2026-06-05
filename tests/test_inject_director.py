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
    assert turns[0].startswith("Wed 1 Oct")
    # banner/comment lines must not leak into a turn body
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
