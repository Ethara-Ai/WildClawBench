"""Injector read-back must verify through the SERVING shape (QC item 1).

`_read_back_row` used to re-read the raw ADMIN row bag and compare only the keys
it had just written. The store shallow-merges any key it is handed, so a write
under a key no service getter reads found itself on re-read and reported
`verified: True` — that is how the pilot-2 dead xero patch shipped.

These tests drive the real `_apply_admin_op` / `_apply_api_mutation` paths
against a scripted admin plane (no docker, no network) and pin:
  * orphan-key write (wrong casing) -> verified=False, op marked a defect
  * correct write                   -> verified=True
  * nested/list values still reported-not-asserted
  * the read-back never touches the public port (audit contamination)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.inject_director import InjectApplier, InjectStage, is_defect  # noqa: E402
from src.utils.serving_shape import ORPHAN_REASON  # noqa: E402


API = "xero-api"


class _AdminPlane:
    """Minimal stand-in for one mock's /admin/* surface.

    `patch` shallow-merges exactly like `Table.patch`, which is precisely why an
    orphan key survives a raw re-read — the behavior under test.
    """

    def __init__(self, table="invoices", pk_field="id", rows=None):
        self.table = table
        self.pk_field = pk_field
        self.rows = {r[pk_field]: dict(r) for r in (rows or [])}
        self.public_calls: list[str] = []

    def get(self, _api, suffix):
        if suffix == "/admin/tables":
            return {"tables": [{"name": self.table, "primary_key": self.pk_field,
                                "rows": len(self.rows)}],
                    "documents": []}
        if suffix == f"/admin/data/{self.table}":
            return {"rows": [dict(r) for r in self.rows.values()]}
        prefix = f"/admin/data/{self.table}/"
        if suffix.startswith(prefix):
            row = self.rows.get(suffix[len(prefix):])
            return dict(row) if row else None
        if not suffix.startswith("/admin/"):
            self.public_calls.append(suffix)
        return None

    def patch(self, _api, _table, pk, fields):
        row = self.rows.get(str(pk))
        if row is None:
            return {"ok": False, "status": 404}
        row.update(fields)
        return {"ok": True, "status": 200}

    def post(self, _api, suffix, payload):
        if suffix == f"/admin/data/{self.table}":
            row = dict(payload.get("row") or {})
            self.rows[str(row.get(self.pk_field))] = row
            return {"ok": True, "status": 200}
        return {"ok": False, "status": 400}


def _applier(tmp_path, plane):
    ap = InjectApplier(
        host_api_to_url={API: "http://127.0.0.1:1"},
        admin_token=None,
        timeline_path=tmp_path / "inject_timeline.jsonl",
        task_id="t",
    )
    ap._admin_get = plane.get        # type: ignore[assignment]
    ap._admin_patch = plane.patch    # type: ignore[assignment]
    ap._admin_post = plane.post      # type: ignore[assignment]
    return ap


def _plane():
    return _AdminPlane(rows=[
        {"id": "INV-1", "status": "DRAFT", "total": 100.0},
        {"id": "INV-2", "status": "DRAFT", "total": 250.0},
    ])


def _patch_op(set_):
    return {"id": "sm-xero", "service": API,
            "admin": {"op": "patch", "table": "invoices", "pk": "INV-1", "set": set_}}


def _stage():
    return InjectStage(index=1, name="s1", from_turn=0, to_turn=1,
                       filesystem=[], loud=[], silent=[], source="")


# --------------------------------------------------------------------------- #
# the pilot-2 regression: orphan key self-verifies
# --------------------------------------------------------------------------- #
def test_orphan_key_wrong_casing_fails_verification(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    op = _patch_op({"Status": "AUTHORISED"})

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["ok"] is False
    assert rec["status"] == "failed"
    assert rec["orphan_fields"] == ["Status"]
    assert ORPHAN_REASON in rec["reason"]
    assert is_defect(rec) is True
    # The store really did take the orphan key — a raw re-read would have said
    # "verified". The serving column is untouched, which is the actual outcome.
    assert plane.rows["INV-1"]["Status"] == "AUTHORISED"
    assert plane.rows["INV-1"]["status"] == "DRAFT"


def test_correct_key_verifies(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(_patch_op({"status": "AUTHORISED"}), _stage(), 1, silent=True)

    assert rec["verified"] is True
    assert rec["ok"] is True
    assert rec["status"] == "applied"
    assert rec["after"] == {"status": "AUTHORISED"}
    assert "orphan_fields" not in rec
    assert is_defect(rec) is False


def test_value_that_did_not_stick_keeps_the_original_reason(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    ap._admin_patch = lambda *a, **k: {"ok": True, "status": 200}  # type: ignore

    rec = ap._apply_api_mutation(_patch_op({"status": "AUTHORISED"}), _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["reason"] == "write not observed on read-back"
    assert "orphan_fields" not in rec


def test_partly_orphan_write_fails_on_the_orphan_half(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(
        _patch_op({"status": "AUTHORISED", "Total": 9.0}), _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["orphan_fields"] == ["Total"]


# --------------------------------------------------------------------------- #
# legitimate shapes must keep verifying
# --------------------------------------------------------------------------- #
def test_nested_and_list_values_are_reported_not_asserted(tmp_path):
    plane = _AdminPlane(rows=[
        {"id": "P1", "properties": {"Owner": {"select": {"name": "ana"}}}, "tags": ["a"]},
        {"id": "P2", "properties": {}, "tags": []},
    ])
    ap = _applier(tmp_path, plane)
    op = {"id": "sm-n", "service": API,
          "admin": {"op": "patch", "table": "invoices", "pk": "P1",
                    "set": {"properties": {"Owner": {"select": {"name": "bo"}}},
                            "tags": ["b"]}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is True
    assert rec["ok"] is True
    assert rec["after"]["properties"] == {"Owner": {"select": {"name": "bo"}}}


def test_airtable_nested_fields_rewrap_is_not_an_orphan(tmp_path):
    # inject_director._patch_row re-wraps a nested patch as {"fields": {...}};
    # judging that wrapper as a column would flag every airtable write.
    plane = _AdminPlane(rows=[
        {"id": "recA", "fields": {"Yield_kg_m2": 14.2, "Plot": "N-1"}},
        {"id": "recB", "fields": {"Yield_kg_m2": 11.0, "Plot": "N-2"}},
    ])
    ap = _applier(tmp_path, plane)
    op = {"id": "sm-air", "service": API,
          "admin": {"op": "patch", "table": "invoices", "pk": "recA",
                    "set": {"Yield_kg_m2": 16.8}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is True
    assert plane.rows["recA"]["fields"]["Yield_kg_m2"] == 16.8


def test_column_present_on_only_the_target_row_is_not_an_orphan(tmp_path):
    # `cover_url` exists on INV-1 alone; sibling-derived vocabulary would miss
    # it, so the pre-write keys must be part of the vocabulary.
    plane = _AdminPlane(rows=[
        {"id": "INV-1", "status": "DRAFT", "cover_url": None},
        {"id": "INV-2", "status": "DRAFT"},
    ])
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(_patch_op({"cover_url": "http://x/y.png"}),
                                 _stage(), 1, silent=True)

    assert rec["verified"] is True


def test_upsert_of_a_new_row_judges_against_sibling_columns(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    op = {"id": "sm-up", "service": API,
          "admin": {"op": "upsert", "table": "invoices", "pk_field": "id",
                    "row": {"id": "INV-9", "Status": "DRAFT", "total": 5.0}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["orphan_fields"] == ["Status"]


def test_upsert_with_sibling_shaped_columns_verifies(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    op = {"id": "sm-up", "service": API,
          "admin": {"op": "upsert", "table": "invoices", "pk_field": "id",
                    "row": {"id": "INV-9", "status": "DRAFT", "total": 5.0}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is True and rec["ok"] is True


def test_update_where_verifies_against_the_first_matched_rows_columns(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    op = {"id": "sm-bulk", "service": API,
          "admin": {"op": "update_where", "table": "invoices",
                    "where": {"status": "DRAFT"}, "set": {"Status": "VOID"}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["orphan_fields"] == ["Status"]


# --------------------------------------------------------------------------- #
# HARD CONSTRAINT: no public-port traffic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("set_", [{"status": "AUTHORISED"}, {"Status": "AUTHORISED"}])
def test_read_back_never_touches_the_public_port(tmp_path, set_):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    ap._apply_api_mutation(_patch_op(set_), _stage(), 1, silent=True)
    assert plane.public_calls == []


def test_read_back_row_returns_the_three_tuple_contract(tmp_path):
    plane = _plane()
    ap = _applier(tmp_path, plane)
    after, verified, orphans = ap._read_back_row(
        API, "invoices", "INV-1", {"status": "DRAFT"}, known_keys={"id", "status", "total"})
    assert after == {"status": "DRAFT"} and verified is True and orphans == []


def test_read_back_row_on_a_missing_row_is_unverified(tmp_path):
    ap = _applier(tmp_path, _plane())
    after, verified, orphans = ap._read_back_row(API, "invoices", "NOPE", {"status": "x"})
    assert after is None and verified is False and orphans == []
