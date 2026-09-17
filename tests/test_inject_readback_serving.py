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
from src.utils.serving_shape import (  # noqa: E402
    ORPHAN_REASON, UNVERIFIABLE, UNVERIFIABLE_REASON,
)


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
        self.payloads: list[dict] = []

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
        self.payloads.append(fields)
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


# --------------------------------------------------------------------------- #
# a patch that names the column bag AND the stamps beside it
#
# The koji_sloan_eeb3be30 stage-1 op verbatim: contentful entries nest their
# columns under `fields`, and the op rewrites the whole bag while moving the
# publish stamps with it. Merging that `set` into the bag wholesale buried the
# columns at fields.fields and demoted the stamps into the bag — HTTP 200, and
# `ageStatement` still served as "10 Year".
# --------------------------------------------------------------------------- #
CASCADE = "bottle-cascade-single-malt"

ENTRY_COLUMNS = {
    "name": "Rimrock Cascade Single Malt", "ageStatement": "12 Year",
    "strength": "43.0% ABV", "mashBill": "Malted Barley 100",
    "cask": "Ex-Bourbon", "batchCode": "RR-CSM-2026-08", "flight": "holiday-2026",
}


def _entries_plane():
    return _AdminPlane(table="entries", rows=[
        {"id": CASCADE, "content_type": "ct-bottle-profile",
         "created_at": "2026-04-11T09:14:00.000Z",
         "updated_at": "2026-09-24T14:05:52.000Z", "published_version": 8,
         "fields": {**ENTRY_COLUMNS, "ageStatement": "10 Year"}},
        {"id": "bottle-ponderosa-wheat", "content_type": "ct-bottle-profile",
         "created_at": "2026-04-11T09:18:00.000Z",
         "updated_at": "2026-09-24T14:11:00.000Z", "published_version": 5,
         "fields": {**ENTRY_COLUMNS, "name": "Rimrock Ponderosa Wheat"}},
    ])


def _entry_op(set_, pk=CASCADE):
    return {"id": "sil-malt-age-10y-to-12y", "service": API,
            "admin": {"op": "patch", "table": "entries", "pk": pk, "set": set_}}


def _koji_set():
    return {"fields": dict(ENTRY_COLUMNS),
            "updated_at": "2026-10-04T09:38:12.000Z", "published_version": 9}


def test_columns_land_in_the_bag_and_stamps_stay_on_the_envelope(tmp_path):
    plane = _entries_plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(_entry_op(_koji_set()), _stage(), 1, silent=True)

    row = plane.rows[CASCADE]
    assert row["fields"]["ageStatement"] == "12 Year"
    assert "fields" not in row["fields"]
    assert row["published_version"] == 9
    assert row["updated_at"] == "2026-10-04T09:38:12.000Z"
    assert row["fields"].get("published_version") is None
    assert rec["verified"] is True and rec["ok"] is True
    assert is_defect(rec) is False


def test_the_rewritten_bag_does_not_drop_columns_the_op_left_out(tmp_path):
    plane = _entries_plane()
    plane.rows[CASCADE]["fields"]["tastingNotes"] = "orchard fruit"
    ap = _applier(tmp_path, plane)

    ap._apply_api_mutation(_entry_op(_koji_set()), _stage(), 1, silent=True)

    assert plane.rows[CASCADE]["fields"]["tastingNotes"] == "orchard fruit"


def test_the_record_reports_the_stamps_it_moved(tmp_path):
    plane = _entries_plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(_entry_op(_koji_set()), _stage(), 1, silent=True)

    assert rec["before"]["published_version"] == 8
    assert rec["after"]["published_version"] == 9
    assert rec["after"]["fields"]["ageStatement"] == "12 Year"
    assert rec["changed"] is True


def test_a_flat_set_on_a_nested_row_sends_exactly_what_it_always_did(tmp_path):
    # The airtable reprice shape (`set: {"Budget": 425}`): no `fields` key, so
    # the payload must stay byte-identical to the pre-fix one.
    plane = _AdminPlane(table="records_tblProjects00001", rows=[
        {"id": "recProj0000000101", "fields": {"Name": "Rouge River Morning",
                                               "Budget": 380, "Status": "Available"}},
        {"id": "recProj0000000103", "fields": {"Name": "Hollyhocks",
                                               "Budget": 445, "Status": "Committed"}},
    ])
    before = dict(plane.rows["recProj0000000101"]["fields"])
    ap = _applier(tmp_path, plane)
    op = {"id": "sil_ms108_reprice_380_to_425", "service": API,
          "admin": {"op": "patch", "table": "records_tblProjects00001",
                    "pk": "recProj0000000101", "set": {"Budget": 425}}}

    rec = ap._apply_api_mutation(op, _stage(), 1, silent=True)

    assert plane.payloads == [{"fields": {**before, "Budget": 425}}]
    assert rec["verified"] is True and rec["ok"] is True


def test_an_invented_stamp_beside_the_bag_is_an_orphan(tmp_path):
    # The envelope has its own vocabulary; a stamp no sibling row carries is as
    # dead as a mis-cased column.
    plane = _entries_plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(
        _entry_op({**_koji_set(), "Published_Version": 9}), _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["orphan_fields"] == ["Published_Version"]
    assert is_defect(rec) is True


def test_a_mis_cased_column_inside_the_bag_still_fails_hard(tmp_path):
    plane = _entries_plane()
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(
        _entry_op({"fields": {"AgeStatement": "12 Year"},
                   "published_version": 9}), _stage(), 1, silent=True)

    assert rec["verified"] is False
    assert rec["orphan_fields"] == ["AgeStatement"]
    assert is_defect(rec) is True


# --------------------------------------------------------------------------- #
# "cannot prove either way" is not a failure
# --------------------------------------------------------------------------- #
class _RetypingPlane(_AdminPlane):
    """A store whose row coercer retypes what it is handed — the family that
    turns "49152" into 49152.0 and a ';'-joined CSV into a list."""

    def patch(self, api, table, pk, fields):
        return super().patch(api, table, pk,
                             {k: float(v) if str(v).replace(".", "", 1).isdigit() else v
                              for k, v in fields.items()})


def test_a_service_that_retypes_the_write_is_unverifiable_not_failed(tmp_path):
    plane = _RetypingPlane(rows=[{"id": "INV-1", "status": "DRAFT", "total": 100.0},
                                 {"id": "INV-2", "status": "DRAFT", "total": 250.0}])
    ap = _applier(tmp_path, plane)

    rec = ap._apply_api_mutation(_patch_op({"total": "425"}), _stage(), 1, silent=True)

    assert rec["verified"] == UNVERIFIABLE
    assert rec["reason"] == UNVERIFIABLE_REASON
    assert rec["ok"] is True and rec["status"] == "applied"
    assert is_defect(rec) is False
    assert plane.rows["INV-1"]["total"] == 425.0


def test_an_unverifiable_outcome_is_still_told_apart_from_a_clean_one(tmp_path):
    plane = _RetypingPlane(rows=[{"id": "INV-1", "status": "DRAFT", "total": 100.0}])
    ap = _applier(tmp_path, plane)

    clean = ap._apply_api_mutation(_patch_op({"status": "AUTHORISED"}), _stage(), 1,
                                   silent=True)

    assert clean["verified"] is True and "reason" not in clean


def test_a_write_that_never_moved_the_row_is_still_a_hard_failure(tmp_path):
    # The discriminator: nothing moved, so there is no transform to appeal to.
    plane = _plane()
    ap = _applier(tmp_path, plane)
    ap._admin_patch = lambda *a, **k: {"ok": True, "status": 200}  # type: ignore

    rec = ap._apply_api_mutation(_patch_op({"status": "AUTHORISED"}), _stage(), 1,
                                 silent=True)

    assert rec["verified"] is False and rec["ok"] is False
    assert rec["reason"] == "write not observed on read-back"
    assert is_defect(rec) is True
