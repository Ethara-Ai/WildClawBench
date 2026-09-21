"""Unit cover for src/utils/serving_shape — the serving-projection primitives
shared by the injector read-back (item 1) and the D17 ingest check (item 4).

Both checkers used to verify a write by re-reading the RAW row and comparing the
keys they had just written, which is circular: the store shallow-merges any key
it is handed. These tests pin the vocabulary/orphan model that breaks the
circle, plus the three legitimate transforms that must NOT read as failures
(loader coercion, `fields` nesting, serializer renames).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.serving_shape import (  # noqa: E402
    ORPHAN_REASON,
    comparable_fields,
    describe_orphans,
    envelope_keys,
    envelope_vocabulary,
    find_getter,
    flatten_serving,
    getter_addresses,
    is_clock_stamp,
    is_miss,
    list_surfaces,
    loose_eq,
    near_miss,
    orphan_keys,
    partition_expected,
    project_in_process,
    project_list_surfaces,
    query_candidates,
    row_bag,
    serving_vocabulary,
    value_visible,
    verify_against_serving,
)


# --------------------------------------------------------------------------- #
# row_bag / partition_expected: the airtable `fields` wrapper
# --------------------------------------------------------------------------- #
def test_row_bag_unwraps_nested_fields():
    assert row_bag({"id": "r1", "fields": {"Status": "open"}}) == {"Status": "open"}


def test_row_bag_passes_through_flat_rows():
    assert row_bag({"id": "r1", "status": "open"}) == {"id": "r1", "status": "open"}


def test_row_bag_ignores_non_dict_fields_column():
    assert row_bag({"id": "r1", "fields": "not-a-dict"}) == {"id": "r1", "fields": "not-a-dict"}


def test_partition_expected_peels_patch_row_rewrap():
    # _patch_row sends a nested patch as {"fields": {...}}; judging that wrapper
    # as a column is the D17 `_patch_row re-wrap` false positive.
    assert partition_expected({"fields": {"Status": "closed"}}, nested=True) \
        == ({"Status": "closed"}, {})


def test_partition_expected_leaves_flat_patch_alone():
    assert partition_expected({"status": "closed"}, nested=False) \
        == ({"status": "closed"}, {})


def test_partition_expected_keeps_a_real_fields_column_when_row_is_flat():
    assert partition_expected({"fields": {"a": 1}}, nested=False) \
        == ({"fields": {"a": 1}}, {})


def test_partition_expected_splits_columns_from_envelope_stamps():
    # The contentful entry shape: the whole column bag plus the publish stamps
    # that move with it. The stamps are not columns and must not be judged as any.
    columns, envelope = partition_expected(
        {"fields": {"ageStatement": "12 Year"}, "updated_at": "T", "published_version": 9},
        nested=True)
    assert columns == {"ageStatement": "12 Year"}
    assert envelope == {"updated_at": "T", "published_version": 9}


def test_envelope_keys_are_everything_but_the_column_bag():
    assert envelope_keys({"id": "e1", "fields": {"a": 1}, "published_version": 8}) \
        == {"id", "published_version"}


def test_envelope_vocabulary_is_drawn_from_siblings():
    rows = [{"id": "e1", "fields": {"a": 1}, "published_version": 8},
            {"id": "e2", "fields": {"a": 2}, "updated_at": "T"}]
    assert envelope_vocabulary(rows, exclude_pk="e1") == {"id", "updated_at"}


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


# --------------------------------------------------------------------------- #
# the depth cutoff: a value the agent can read is not a lost write
#
# Payload shapes below are the real ones, trimmed: figma `get_file` nests a
# node tree under `document`, contentful `get_entry` links the content type at
# `sys.contentType.sys.id`, spotify `get_playlist` nests tracks three deep. The
# old one-level-deep flatten stopped at depth 2 and reported 34 of figma's 49
# served scalars as missing.
# --------------------------------------------------------------------------- #
FIGMA_FILE = {
    "name": "Corridor Campaign", "version": "2317", "lastModified": "2026-10-04",
    "document": {"id": "0:0", "type": "DOCUMENT", "children": [
        {"id": "1:1", "name": "Page 1", "children": [
            {"id": "2:1", "name": "Frame", "children": [
                {"id": "3:1", "type": "INSTANCE", "componentId": "comp-btn-primary"},
            ]},
        ]},
    ]},
    "components": {"3:1": {"key": "ck-88", "name": "Button/Primary"}},
}

CONTENTFUL_ENTRY = {
    "sys": {"id": "bottle-cascade-single-malt", "type": "Entry",
            "publishedVersion": 9, "updatedAt": "2026-10-04T09:38:12.000Z",
            "contentType": {"sys": {"type": "Link", "linkType": "ContentType",
                                    "id": "ct-bottle-profile"}}},
    "fields": {"name": "Rimrock Cascade Single Malt", "ageStatement": "12 Year"},
}


def test_flatten_serving_reaches_a_deeply_nested_node_tree():
    flat = flatten_serving(FIGMA_FILE)
    assert flat["document.children[0].children[0].children[0].componentId"] \
        == "comp-btn-primary"
    assert flat["components.3:1.key"] == "ck-88"


def test_value_visible_finds_a_value_past_the_old_depth_cutoff():
    # figma serves the component id four levels down; at depth 2 it read as lost.
    assert value_visible("comp-btn-primary", FIGMA_FILE)


def test_value_visible_finds_a_contentful_content_type_link():
    assert value_visible("ct-bottle-profile", CONTENTFUL_ENTRY)


def test_the_koji_entry_write_is_visible_in_both_namespaces():
    # The op that shipped dead: the age statement lives in the column bag, the
    # publish stamp in the sys envelope, and both must read as present.
    assert value_visible("12 Year", CONTENTFUL_ENTRY)
    assert value_visible(9, CONTENTFUL_ENTRY)


def test_flatten_serving_stops_at_the_node_budget():
    deep = {"a": 1}
    for _ in range(50):
        deep = {"nest": deep, "leaf": 2}
    assert flatten_serving(deep, budget=10) != {}
    assert len(flatten_serving(deep, budget=10)) <= 10


def test_flatten_serving_survives_a_tree_deeper_than_the_recursion_limit():
    deep: dict = {"leaf": "bottom"}
    for _ in range(3000):
        deep = {"nest": deep}
    assert value_visible("bottom", deep)


# --------------------------------------------------------------------------- #
# the transforms: not byte-findable, and not evidence of anything
#
# These three are why a missing value cannot be graded as a failure. Each one
# was measured against the live service module, and in all three the write
# lands perfectly while the projection shows something else.
# --------------------------------------------------------------------------- #
WOOCOMMERCE_PRODUCT = {"id": 141, "name": "Trail Jacket", "sku": "TJ-1",
                       "price": "18.00", "regular_price": "18.00",
                       "categories": [{"name": "Outerwear", "slug": "outerwear"}]}


def test_a_formatted_price_is_not_byte_findable():
    # woocommerce stores a float and serves f"{p:.2f}"; 18.0 never appears.
    assert not value_visible(18.0, WOOCOMMERCE_PRODUCT)
    assert value_visible("18.00", WOOCOMMERCE_PRODUCT)


def test_a_base64_encoded_body_is_not_byte_findable():
    body = "Ship the deck by Friday."
    served = {
        "id": "m-1", "snippet": body[:12],
        "payload": {"mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(body.encode()).decode(),
                             "size": len(body)}},
    }
    assert not value_visible(body, served)


def test_an_addressing_key_the_getter_never_echoes_is_not_byte_findable():
    # figma addresses rows by file_key; get_file returns everything but it.
    assert not value_visible("FK001abcdefg", FIGMA_FILE)


def test_a_genuinely_orphaned_write_is_still_invisible():
    # The true positive the three cases above must not be allowed to excuse:
    # `AgeStatement` is not a contentful column, so nothing carries the value.
    assert not value_visible("14 Year", CONTENTFUL_ENTRY)
    assert orphan_keys(["AgeStatement"], {"name", "ageStatement"}) == ["AgeStatement"]


# --------------------------------------------------------------------------- #
# vendor not-found envelopes
#
# A getter answers "no such row" with a body, not an exception, and each vendor
# spells that body its own way. An unrecognised envelope is handed back as a
# served row, so a caller diffing before against after compares a 404 with the
# same 404, sees no movement, and blames the write. Every literal below was
# taken off the fleet's own not-found return paths.
# --------------------------------------------------------------------------- #
JIRA_404 = {"errorMessages": ["Issue COPP-200 does not exist"], "errors": {}}
WOO_404 = {"error": "woocommerce_rest_product_invalid_id", "status": 404,
           "message": "Invalid product ID: 611"}
TWILIO_404 = {"code": 20404, "error": "not found", "status": 404}
BIGCOMMERCE_404 = {"error": "Not Found", "status": 404, "title": "Not Found"}
PLAID_404 = {"error_code": "ITEM_NOT_FOUND", "error_message": "no such item"}
HUBSPOT_404 = {"category": "OBJECT_NOT_FOUND", "error": "contact 9 not found"}
GENERIC_404 = {"error": "Message msg-9 not found"}

# Real answers that carry an envelope-shaped key and must survive intact.
DATADOG_SERIES = {"from_date": 1, "query": "avg:cpu", "series": [],
                  "status": "ok", "to_date": 2}
INTERCOM_LIST = {"data": [], "total_count": 0, "type": "list"}
OPENLIBRARY_WORK = {"authors": [], "description": "d", "first_publish_date": "1979",
                    "key": "/works/OL1W", "subjects": [], "title": "The Book",
                    "type": "work"}
ROW_WITH_AN_ERROR_COLUMN = {"id": "run-7", "error": "timeout", "name": "nightly"}


@pytest.mark.parametrize("envelope", [
    JIRA_404, WOO_404, TWILIO_404, BIGCOMMERCE_404, PLAID_404, HUBSPOT_404,
    GENERIC_404, None, {},
])
def test_every_fleet_not_found_envelope_reads_as_a_miss(envelope):
    assert is_miss(envelope)


@pytest.mark.parametrize("served", [
    DATADOG_SERIES, INTERCOM_LIST, OPENLIBRARY_WORK, ROW_WITH_AN_ERROR_COLUMN,
    CONTENTFUL_ENTRY, FIGMA_FILE, WOOCOMMERCE_PRODUCT,
])
def test_a_served_row_is_never_mistaken_for_an_envelope(served):
    assert not is_miss(served)


def test_an_envelope_wider_than_two_keys_is_still_a_miss():
    # The size of the body says nothing: three services answer not-found with
    # three keys, and woocommerce's is the one that made a landed write read as
    # a dead one.
    assert len(WOO_404) == 3 and is_miss(WOO_404)


def test_an_envelope_that_never_names_the_failure_is_bounded_by_size():
    assert is_miss({"status": 404, "message": "gone"})
    assert not is_miss({"status": "active", "type": "user", "title": "Lead"})


# --------------------------------------------------------------------------- #
# getter addressing: the store's key is not always the getter's key
# --------------------------------------------------------------------------- #
_JIRA_ROWS = [
    {"id": "20200", "key": "COPP-200", "summary": "cold-chain cutoff",
     "team": "ops"},
    {"id": "20201", "key": "COPP-201", "summary": "priority bump",
     "team": "ops"},
]


class _KeyedTable:
    primary_key = "id"

    def __init__(self, rows):
        self._rows = rows

    def get(self, pk):
        return next((r for r in self._rows if r["id"] == pk), None)

    def rows(self):
        return list(self._rows)


class _JiraLikeModule:
    """jira registers `issues` on `id` and answers `get_issue` on `key`."""

    _store = _FakeStore({"issues": _KeyedTable(_JIRA_ROWS)})

    @staticmethod
    def get_issue(issue_key):
        for row in _JIRA_ROWS:
            if row["key"] == issue_key:
                return {"key": row["key"], "fields": {"summary": row["summary"]}}
        return {"errorMessages": [f"Issue {issue_key} does not exist"],
                "errors": {}}


def test_getter_addresses_offers_the_store_pk_first():
    assert getter_addresses(_JiraLikeModule, "issues", "20200")[0] == "20200"


def test_a_getter_keyed_on_a_natural_id_is_reached_through_the_store_pk():
    # The op addresses `id`, the getter matches `key`; without the fallback the
    # projection is jira's 404 body and the row reads as unwritable.
    serving, mechanism = project_in_process(_JiraLikeModule, "issues", "20200")
    assert serving == {"key": "COPP-200", "fields": {"summary": "cold-chain cutoff"}}
    assert mechanism == "getter:get_issue"


def test_an_address_shared_with_a_sibling_is_never_tried():
    # `team` is identical on both rows, so using it could project the wrong one.
    assert "ops" not in getter_addresses(_JiraLikeModule, "issues", "20200")


def test_a_row_that_is_genuinely_absent_still_projects_to_none():
    serving, _ = project_in_process(_JiraLikeModule, "issues", "29999")
    assert serving is None


def test_a_getter_that_raises_is_reported_rather_than_re_addressed():
    class _Raising:
        _store = _FakeStore({"issues": _KeyedTable(_JIRA_ROWS)})

        @staticmethod
        def get_issue(_pk):
            raise ValueError("Unknown format code 'f' for object of type 'str'")

    serving, mechanism = project_in_process(_Raising, "issues", "20200")
    assert serving is None
    assert "ValueError" in mechanism


# --------------------------------------------------------------------------- #
# clock stamps: bookkeeping, not scenario state
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["updated_at", "updatedAt", "last_modified",
                                 "modified", "mtime", "date_updated"])
def test_a_last_touched_timestamp_is_a_clock_stamp(key):
    assert is_clock_stamp(key)


@pytest.mark.parametrize("key", ["published_version", "price_amount", "items",
                                 "status", "created_at", "socialDetail"])
def test_state_bearing_keys_are_not_clock_stamps(key):
    assert not is_clock_stamp(key)


# --------------------------------------------------------------------------- #
# list and query surfaces
#
# gmail never emits `is_starred` from get_message and publishes it only as
# membership of `?q=is:starred`, so a single-getter model cannot see the write
# at all.
# --------------------------------------------------------------------------- #
_GMAIL_ROWS = [
    {"id": "m-1", "thread_id": "t-1", "is_starred": False, "snippet": "one"},
    {"id": "m-2", "thread_id": "t-2", "is_starred": True, "snippet": "two"},
]


class _GmailLikeModule:
    _store = _FakeStore({"messages": _KeyedTable(_GMAIL_ROWS)})

    @staticmethod
    def get_message(pk):
        row = next((r for r in _GMAIL_ROWS if r["id"] == pk), None)
        if row is None:
            return {"error": f"Message {pk} not found"}
        return {"id": row["id"], "threadId": row["thread_id"],
                "snippet": row["snippet"]}

    @staticmethod
    def list_messages(query="", max_results=25):
        rows = _GMAIL_ROWS
        if "is:starred" in query:
            rows = [r for r in rows if r["is_starred"]]
        return {"messages": [{"id": r["id"]} for r in rows[:max_results]]}


def test_list_surfaces_finds_the_tables_collection_reader():
    assert [name for name, _fn in list_surfaces(_GmailLikeModule, "messages")] \
        == ["list_messages"]


def test_query_candidates_spell_a_truthy_flag_the_way_a_vendor_grammar_does():
    assert query_candidates([("is_starred", "true")]) == [
        "is_starred:true", "is:starred", "true"]


def test_query_candidates_leave_a_plain_column_alone():
    assert query_candidates([("price_amount", "749")]) == ["price_amount:749", "749"]


def test_the_getter_alone_cannot_see_a_flag_only_a_query_route_publishes():
    before = project_in_process(_GmailLikeModule, "messages", "m-1")[0]
    _GMAIL_ROWS[0]["is_starred"] = True
    try:
        assert project_in_process(_GmailLikeModule, "messages", "m-1")[0] == before
    finally:
        _GMAIL_ROWS[0]["is_starred"] = False


def test_the_query_surface_snapshot_moves_when_the_flag_flips():
    payload = [("is_starred", "true")]
    before = project_list_surfaces(_GmailLikeModule, "messages", payload)
    _GMAIL_ROWS[0]["is_starred"] = True
    try:
        after = project_list_surfaces(_GmailLikeModule, "messages", payload)
    finally:
        _GMAIL_ROWS[0]["is_starred"] = False
    assert before["list_messages?query=is:starred"] != \
        after["list_messages?query=is:starred"]
    assert before["list_messages"] == after["list_messages"]
