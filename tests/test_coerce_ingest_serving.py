"""D17 ingest check must model loader coercion (QC item 4).

The old check compared a patch's `set` keys against the RAW stored-row keys,
which models none of what sits between the two: the loader coercer renames and
retypes columns, airtable-style stores nest them under `fields`, and
`inject_director._patch_row` re-wraps a nested patch before sending it. All
three legitimate transforms read as lost writes.

`script/coerce_dryrun.py --ingest` now replays each mutation against the
in-process store copy and reads it back through the service's own getter. These
tests use a coerced-shape fixture (store column `id_list` -> serving key
`idList`, CSV `member_ids` -> list) that the key comparison false-positived on,
plus the genuine orphan writes that must still fail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

import coerce_dryrun  # noqa: E402
from coerce_dryrun import ingest_ops, replay_patch  # noqa: E402


# --------------------------------------------------------------------------- #
# A store + data module shaped exactly like environment/trello-api: snake_case
# columns on disk, camelCase in the serving projection.
# --------------------------------------------------------------------------- #
class _Table:
    def __init__(self, primary_key, rows, coercer=None):
        self.primary_key = primary_key
        self._coercer = coercer
        self._rows = {r[primary_key]: dict(r) for r in rows}

    def get(self, pk):
        row = self._rows.get(pk)
        return dict(row) if row is not None else None

    def rows(self):
        return [dict(r) for r in self._rows.values()]

    def patch(self, pk, fields):
        row = self._rows.get(pk)
        if row is None:
            return None
        row.update(fields)
        if self._coercer:
            self._rows[pk] = self._coercer(row)
        return dict(self._rows[pk])


class _Store:
    def __init__(self, tables):
        self._tables = tables
        self._snapshots = {}
        self._n = 0

    def table(self, name):
        if name not in self._tables:
            raise KeyError(f"table '{name}' is not registered")
        return self._tables[name]

    def snapshot(self, label=""):
        self._n += 1
        sid = f"{label}-{self._n}"
        self._snapshots[sid] = {
            name: [dict(r) for r in t.rows()] for name, t in self._tables.items()
        }
        return sid

    def restore(self, sid):
        for name, rows in self._snapshots.pop(sid, {}).items():
            table = self._tables[name]
            table._rows = {r[table.primary_key]: dict(r) for r in rows}
        return True


def _coerce_card(row):
    row = dict(row)
    ids = row.get("member_ids")
    if isinstance(ids, str):
        row["member_ids"] = [p for p in ids.split(";") if p]
    if "pos" in row and row["pos"] is not None:
        row["pos"] = float(row["pos"])
    return row


class _TrelloModule:
    """Serializer renames snake_case columns to the camelCase the agent sees."""

    _store = _Store({
        "cards": _Table("id", [
            {"id": "c1", "name": "Define Q2 themes", "id_list": "L1",
             "id_board": "B1", "member_ids": ["m1"], "pos": 16384.0},
            {"id": "c2", "name": "Ship the deck", "id_list": "L2",
             "id_board": "B1", "member_ids": [], "pos": 32768.0},
        ], coercer=_coerce_card),
        "attachments": _Table("id", [{"id": "a1", "card_id": "c1"}]),
    })

    @staticmethod
    def get_card(pk):
        row = _TrelloModule._store.table("cards").get(pk)
        if row is None:
            return {"error": "not found"}
        return {
            "id": row["id"],
            "name": row["name"],
            "idList": row["id_list"],
            "idBoard": row["id_board"],
            "idMembers": row["member_ids"],
            "pos": row["pos"],
        }


class _AirtableModule:
    """Nested-`fields` store with no getter: projection degrades to raw-store."""

    _store = _Store({
        "records_tblA": _Table("id", [
            {"id": "recA", "fields": {"Yield_kg_m2": 14.2, "Plot": "N-1"}},
            {"id": "recB", "fields": {"Yield_kg_m2": 11.0, "Plot": "N-2"}},
        ]),
    })


def _spec(**kw):
    base = {"id": "op1", "table": "cards", "pk": "c1"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# the false positives: legitimate coercion/rename/nesting
# --------------------------------------------------------------------------- #
def test_serializer_rename_is_not_a_lost_write():
    # Old check: patch key `id_list` absent from the serving keys -> FAIL.
    ok, info = replay_patch(_TrelloModule, _spec(set={"id_list": "L9"}))
    assert ok is True
    assert "getter:get_card" in info


def test_coercer_retyping_is_not_a_lost_write():
    # `member_ids` arrives as a ';'-joined CSV string; the coercer splits it, so
    # the stored value never equals the written one.
    ok, info = replay_patch(_TrelloModule, _spec(set={"member_ids": "m1;m2"}))
    assert ok is True, info


def test_numeric_coercion_is_not_a_lost_write():
    ok, info = replay_patch(_TrelloModule, _spec(set={"pos": "49152"}))
    assert ok is True, info


def test_plain_column_write_passes():
    ok, info = replay_patch(_TrelloModule, _spec(set={"name": "Renamed"}))
    assert ok is True, info


def test_nested_fields_patch_row_rewrap_is_not_an_orphan():
    ok, info = replay_patch(
        _AirtableModule,
        _spec(table="records_tblA", pk="recA", set={"fields": {"Yield_kg_m2": 16.8}}))
    assert ok is True, info
    assert "raw-store" in info


def test_a_table_with_no_getter_says_so_instead_of_claiming_a_projection():
    _, info = replay_patch(
        _AirtableModule, _spec(table="records_tblA", pk="recA", set={"Plot": "N-9"}))
    assert "raw-store" in info


def test_rewriting_the_value_a_row_already_serves_is_a_no_op_not_a_failure():
    ok, info = replay_patch(_TrelloModule, _spec(set={"id_list": "L1"}))
    assert ok is True and "no-op" in info


def test_a_write_the_service_discards_is_caught():
    # The deepcopy-and-discard family (notion/servicenow): the patch returns,
    # the projection never moves.
    class _Discarding(_TrelloModule):
        @staticmethod
        def get_card(pk):
            return {"id": pk, "name": "frozen", "idList": "L1"}

    ok, info = replay_patch(_Discarding, _spec(set={"name": "Renamed"}))
    assert ok is False and "unchanged" in info


# --------------------------------------------------------------------------- #
# the true positives must survive
# --------------------------------------------------------------------------- #
def test_serving_shape_key_written_to_the_store_is_an_orphan():
    # `idList` is what the AGENT sees; writing it to the store creates a dead key.
    ok, info = replay_patch(_TrelloModule, _spec(set={"idList": "L9"}))
    assert ok is False
    assert "orphan" in info and "idList" in info


def test_wrong_casing_is_an_orphan():
    ok, info = replay_patch(_TrelloModule, _spec(set={"Name": "Renamed"}))
    assert ok is False and "Name" in info


def test_a_missing_row_is_reported_not_silently_passed():
    ok, info = replay_patch(_TrelloModule, _spec(pk="nope", set={"name": "x"}))
    assert ok is False and "not in table" in info


def test_an_unregistered_table_is_reported():
    ok, info = replay_patch(_TrelloModule, _spec(table="nosuch", set={"name": "x"}))
    assert ok is False and "no such store table" in info


def test_an_empty_set_block_is_reported():
    ok, info = replay_patch(_TrelloModule, _spec(set={}))
    assert ok is False and "no `set` block" in info


def test_a_module_without_a_store_is_reported():
    ok, info = replay_patch(object(), _spec(set={"name": "x"}))
    assert ok is False and "no _store" in info


# --------------------------------------------------------------------------- #
# the replay must not leak state between ops
# --------------------------------------------------------------------------- #
def test_replay_restores_the_store_so_ops_stay_independent():
    before = _TrelloModule._store.table("cards").get("c1")
    replay_patch(_TrelloModule, _spec(set={"name": "Mutated"}))
    assert _TrelloModule._store.table("cards").get("c1") == before


def test_a_failing_replay_also_restores():
    before = _TrelloModule._store.table("cards").get("c1")
    replay_patch(_TrelloModule, _spec(set={"idList": "L9"}))
    assert _TrelloModule._store.table("cards").get("c1") == before


# --------------------------------------------------------------------------- #
# mutations.json parsing
# --------------------------------------------------------------------------- #
def _write_stage(task_dir: Path, stage: str, doc) -> None:
    d = task_dir / "inject" / stage
    d.mkdir(parents=True, exist_ok=True)
    (d / "mutations.json").write_text(json.dumps(doc), encoding="utf-8")


def test_ingest_ops_collects_admin_patches_by_service(tmp_path):
    _write_stage(tmp_path, "stage1", {"mutations": {"silent": [
        {"id": "sm1", "service": "trello-api",
         "admin": {"op": "patch", "table": "cards", "pk": "c1", "set": {"name": "x"}}},
    ]}})
    ops = ingest_ops(tmp_path)
    assert list(ops) == ["trello-api"]
    assert ops["trello-api"][0]["table"] == "cards"
    assert ops["trello-api"][0]["stage"] == "stage1"


def test_ingest_ops_reads_both_silent_and_loud_buckets(tmp_path):
    _write_stage(tmp_path, "stage1", {"mutations": {
        "silent": [{"id": "s", "service": "a-api",
                    "admin": {"op": "patch", "table": "t", "pk": "1", "set": {"k": 1}}}],
        "loud": [{"id": "l", "service": "a-api",
                  "admin": {"op": "patch", "table": "t", "pk": "2", "set": {"k": 2}}}],
    }})
    assert len(ingest_ops(tmp_path)["a-api"]) == 2


def test_ingest_ops_spans_stages(tmp_path):
    for stage in ("stage1", "stage2"):
        _write_stage(tmp_path, stage, {"mutations": {"silent": [
            {"id": stage, "service": "a-api",
             "admin": {"op": "patch", "table": "t", "pk": "1", "set": {"k": 1}}}]}})
    assert len(ingest_ops(tmp_path)["a-api"]) == 2


def test_ingest_ops_skips_bare_rest_ops_it_cannot_resolve_offline(tmp_path):
    _write_stage(tmp_path, "stage1", {"mutations": {"silent": [
        {"id": "rest", "service": "a-api", "method": "PATCH", "path": "/v1/pages/{x}"},
    ]}})
    assert ingest_ops(tmp_path) == {}


def test_ingest_ops_skips_non_patch_admin_kinds(tmp_path):
    _write_stage(tmp_path, "stage1", {"mutations": {"silent": [
        {"id": "d", "service": "a-api",
         "admin": {"op": "doc_set", "document": "properties", "path": ["a"], "value": 1}},
    ]}})
    assert ingest_ops(tmp_path) == {}


def test_ingest_ops_tolerates_a_flat_list_stage(tmp_path):
    _write_stage(tmp_path, "stage1", [
        {"id": "s", "service": "a-api",
         "admin": {"op": "patch", "table": "t", "pk": "1", "set": {"k": 1}}}])
    assert len(ingest_ops(tmp_path)["a-api"]) == 1


def test_ingest_ops_tolerates_malformed_json(tmp_path):
    d = tmp_path / "inject" / "stage1"
    d.mkdir(parents=True)
    (d / "mutations.json").write_text("{not json", encoding="utf-8")
    assert ingest_ops(tmp_path) == {}


def test_ingest_ops_on_a_task_with_no_inject_dir_is_empty(tmp_path):
    assert ingest_ops(tmp_path) == {}


# --------------------------------------------------------------------------- #
# end-to-end against a REAL service module (no containers, no ports)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not (Path(__file__).resolve().parents[1]
                         / "environment" / "trello-api").is_dir(),
                    reason="trello-api not in this fleet")
def test_real_trello_module_rename_passes_and_orphan_fails(tmp_path):
    overlay = tmp_path / "trello-api"
    overlay.mkdir()
    seen: dict = {}

    def probe(module):
        table = module._store.table("cards")
        pk = table.rows()[0][table.primary_key]
        seen["rename"] = replay_patch(
            module, {"id": "r", "table": "cards", "pk": pk, "set": {"id_list": "LZ"}})
        seen["orphan"] = replay_patch(
            module, {"id": "o", "table": "cards", "pk": pk, "set": {"idList": "LZ"}})

    loaded, info = coerce_dryrun._check_api("trello-api", overlay, on_module=probe)
    assert loaded, info
    assert seen["rename"][0] is True, seen["rename"]
    assert seen["orphan"][0] is False and "orphan" in seen["orphan"][1]
