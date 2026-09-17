"""Unit cover for src/utils/serving_shape — the serving-projection primitives
shared by the injector read-back (item 1) and the D17 ingest check (item 4).

Both checkers used to verify a write by re-reading the RAW row and comparing the
keys they had just written, which is circular: the store shallow-merges any key
it is handed. These tests pin the vocabulary/orphan model that breaks the
circle, plus the three legitimate transforms that must NOT read as failures
(loader coercion, `fields` nesting, serializer renames).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.serving_shape import (  # noqa: E402
    ORPHAN_REASON,
    comparable_fields,
    describe_orphans,
    find_getter,
    flatten_serving,
    loose_eq,
    near_miss,
    orphan_keys,
    project_in_process,
    row_bag,
    serving_vocabulary,
    unwrap_expected,
    value_visible,
    verify_against_serving,
)


# --------------------------------------------------------------------------- #
# row_bag / unwrap_expected: the airtable `fields` wrapper
# --------------------------------------------------------------------------- #
def test_row_bag_unwraps_nested_fields():
    assert row_bag({"id": "r1", "fields": {"Status": "open"}}) == {"Status": "open"}


def test_row_bag_passes_through_flat_rows():
    assert row_bag({"id": "r1", "status": "open"}) == {"id": "r1", "status": "open"}


def test_row_bag_ignores_non_dict_fields_column():
    assert row_bag({"id": "r1", "fields": "not-a-dict"}) == {"id": "r1", "fields": "not-a-dict"}


def test_unwrap_expected_peels_patch_row_rewrap():
    # _patch_row re-wraps a nested patch as {"fields": {...}} before sending it;
    # judging that wrapper as a column is the D17 `_patch_row re-wrap` false positive.
    assert unwrap_expected({"fields": {"Status": "closed"}}, nested=True) == {"Status": "closed"}


def test_unwrap_expected_leaves_flat_patch_alone():
    assert unwrap_expected({"status": "closed"}, nested=False) == {"status": "closed"}


def test_unwrap_expected_keeps_a_real_fields_column_when_row_is_flat():
    assert unwrap_expected({"fields": {"a": 1}}, nested=False) == {"fields": {"a": 1}}


def test_unwrap_expected_keeps_fields_alongside_other_keys():
    payload = {"fields": {"a": 1}, "status": "x"}
    assert unwrap_expected(payload, nested=True) == payload


# --------------------------------------------------------------------------- #
# vocabulary + orphans
# --------------------------------------------------------------------------- #
def test_vocabulary_unions_sibling_columns():
    rows = [{"id": "a", "status": "x"}, {"id": "b", "owner": "y"}]
    assert serving_vocabulary(rows) == {"id", "status", "owner"}


def test_vocabulary_excludes_the_row_under_verification():
    # The patched row already carries the orphan key by read-back time, so it
    # must not be allowed to vouch for itself.
    rows = [{"id": "a", "status": "x", "Status": "orphan"}, {"id": "b", "status": "y"}]
    assert serving_vocabulary(rows, exclude_pk="a") == {"id", "status"}


def test_vocabulary_exclude_honors_a_declared_primary_key():
    rows = [{"sys_id": "a", "state": "1"}, {"sys_id": "b", "short_desc": "d"}]
    vocab = serving_vocabulary(rows, exclude_pk="a", pk_field="sys_id")
    assert vocab == {"sys_id", "short_desc"}


def test_vocabulary_extra_carries_pre_write_keys():
    # A legitimate column present on only the target row must survive.
    rows = [{"id": "b", "status": "y"}]
    vocab = serving_vocabulary(rows, exclude_pk="a", extra={"id", "status", "cover_url"})
    assert "cover_url" in vocab


def test_vocabulary_unwraps_nested_sibling_rows():
    rows = [{"id": "a", "fields": {"Name": "n", "Yield": 1}}]
    assert serving_vocabulary(rows) == {"Name", "Yield"}


def test_orphan_keys_is_case_sensitive():
    assert orphan_keys(["Status"], {"status", "id"}) == ["Status"]
    assert orphan_keys(["status"], {"status", "id"}) == []


def test_orphan_keys_empty_vocabulary_accuses_nobody():
    assert orphan_keys(["anything"], set()) == []


def test_near_miss_points_at_the_real_column():
    assert near_miss("Status", {"status", "id"}) == "status"
    assert near_miss("nonesuch", {"status"}) is None


def test_describe_orphans_carries_the_hint():
    msg = describe_orphans(["Status"], {"status"})
    assert ORPHAN_REASON in msg and "'status'" in msg


# --------------------------------------------------------------------------- #
# loose_eq / comparable_fields
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("a,b", [(True, "True"), (1, "1"), ("OPEN", "open"), (1.5, 1.5)])
def test_loose_eq_tolerates_bool_and_numeric_strings(a, b):
    assert loose_eq(a, b)


def test_loose_eq_rejects_real_differences():
    assert not loose_eq("open", "closed")


def test_comparable_fields_drops_nested_values():
    expected = {"status": "x", "props": {"a": 1}, "tags": ["a"]}
    assert comparable_fields(expected) == {"status": "x"}


# --------------------------------------------------------------------------- #
# verify_against_serving
# --------------------------------------------------------------------------- #
def test_verify_passes_a_correct_write():
    after, verified, orphans = verify_against_serving(
        {"status": "closed"}, {"status": "closed"}, {"status"})
    assert (after, verified, orphans) == ({"status": "closed"}, True, [])


def test_verify_fails_an_orphan_key_even_though_the_value_matches():
    # This is the whole defect: the store DID take `Status`, so a raw re-read
    # finds it and the old check said verified=True.
    after, verified, orphans = verify_against_serving(
        {"Status": "closed"}, {"Status": "closed", "status": "open"}, {"status"})
    assert verified is False
    assert orphans == ["Status"]
    assert after == {"Status": "closed"}


def test_verify_fails_a_value_that_did_not_stick():
    _, verified, orphans = verify_against_serving(
        {"status": "closed"}, {"status": "open"}, {"status"})
    assert verified is False and orphans == []


def test_verify_reports_but_does_not_assert_nested_values():
    after, verified, orphans = verify_against_serving(
        {"properties": {"rich": "object"}},
        {"properties": {"stored": "differently"}},
        {"properties"})
    assert verified is True and orphans == []
    assert after == {"properties": {"stored": "differently"}}


# --------------------------------------------------------------------------- #
# in-process projection
# --------------------------------------------------------------------------- #
class _FakeTable:
    primary_key = "id"

    def __init__(self, rows):
        self._rows = rows

    def get(self, pk):
        return self._rows.get(pk)

    def rows(self):
        return list(self._rows.values())


class _FakeStore:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return self._tables[name]


class _FakeModule:
    _store = _FakeStore({"cards": _FakeTable({"c1": {"id": "c1", "id_list": "L1"}})})

    @staticmethod
    def get_card(pk):
        return {"id": pk, "idList": "L1"}

    @staticmethod
    def get_incident(pk):
        return {"error": "not found"}


def test_find_getter_singularizes_the_table_name():
    assert find_getter(_FakeModule, "cards") is _FakeModule.get_card


def test_find_getter_strips_the_airtable_records_prefix():
    assert find_getter(_FakeModule, "records_cards") is _FakeModule.get_card


def test_find_getter_returns_none_for_a_list_only_table():
    assert find_getter(_FakeModule, "attachments") is None


def test_project_in_process_uses_the_real_getter():
    serving, mechanism = project_in_process(_FakeModule, "cards", "c1")
    assert serving == {"id": "c1", "idList": "L1"}
    assert mechanism == "getter:get_card"


def test_project_in_process_reports_raw_store_when_no_getter_exists():
    serving, mechanism = project_in_process(_FakeModule, "cards", "c1")
    assert mechanism.startswith("getter:")
    none_serving, none_mech = project_in_process(_FakeModule, "nosuch", "x")
    assert none_serving is None and none_mech == "raw-store"


def test_project_in_process_normalizes_a_getter_error_dict_to_none():
    serving, _ = project_in_process(_FakeModule, "incidents", "missing")
    assert serving is None


# --------------------------------------------------------------------------- #
# serializer renames must not read as lost writes (the D17 false positive)
# --------------------------------------------------------------------------- #
def test_flatten_serving_reaches_into_lists_of_objects():
    flat = flatten_serving({"labels": [{"name": "strategy"}], "pos": 1.0})
    assert flat["labels[0].name"] == "strategy"
    assert flat["pos"] == 1.0


def test_value_visible_survives_a_key_rename():
    # trello stores `id_list`, serves `idList`; the value is what matters.
    assert value_visible("L1", {"id": "c1", "idList": "L1"})


def test_value_visible_detects_a_genuinely_lost_write():
    assert not value_visible("L2", {"id": "c1", "idList": "L1"})


def test_value_visible_never_asserts_nested_expectations():
    assert value_visible({"rich": "object"}, {"anything": "else"})
