"""Seed-shape tolerance for list-valued columns.

The same logical column arrives in two shapes depending on the seed file:
CSV/TSV rows carry `"a;b"`, JSON rows carry `["a", "b"]`. A coercer that only
handles the string shape does not fail on the list shape --- it stringifies it
and splits, yielding a one-element list holding a Python repr. The route then
serves one garbage member, or none, with a 200 and nothing in the logs. That
silent corruption is what these tests pin shut.

Covered: the shared `opt_csv_list` coercer, the two services that used to
bypass it with a raw `.split()` / `json.loads()`, and the load-time detector
that shouts when a table is populated with an already-garbled value.
"""
from __future__ import annotations

import sys

import pytest

from ._helpers import ENV_DIR, data_module, load_app

if str(ENV_DIR) not in sys.path:
    sys.path.insert(0, str(ENV_DIR))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# opt_csv_list -- the shared coercer 17 services call
# ---------------------------------------------------------------------------

def test_opt_csv_list_accepts_an_already_split_list():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({"c": ["a", "b"]}, "c", sep=";") == ["a", "b"]


def test_opt_csv_list_accepts_a_tuple():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({"c": ("a", "b")}, "c", sep=";") == ["a", "b"]


def test_opt_csv_list_coerces_list_members_to_str():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({"c": [1, 2]}, "c") == ["1", "2"]


def test_opt_csv_list_drops_empty_list_members():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({"c": ["a", "", "b"]}, "c") == ["a", "b"]


def test_opt_csv_list_string_shape_is_unchanged():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({"c": "a;b"}, "c", sep=";") == ["a", "b"]
    assert opt_csv_list({"c": "a,b"}, "c") == ["a", "b"]
    assert opt_csv_list({"c": "solo"}, "c", sep=";") == ["solo"]


def test_opt_csv_list_missing_and_blank_still_take_the_default():
    from _mutable_store import opt_csv_list

    assert opt_csv_list({}, "c", default=["d"]) == ["d"]
    assert opt_csv_list({"c": None}, "c", default=["d"]) == ["d"]
    assert opt_csv_list({"c": "   "}, "c", default=["d"]) == ["d"]
    assert opt_csv_list({}, "c") == []


def test_opt_csv_list_never_yields_a_repr_of_the_list():
    """The exact corruption: str(["a","b"]).split(";") -> ["['a', 'b']"]."""
    from _mutable_store import opt_csv_list

    out = opt_csv_list({"c": ["a", "b"]}, "c", sep=";")
    assert all("[" not in member for member in out), out


# ---------------------------------------------------------------------------
# trello-api -- checklist items used to call r["items"].split(";") directly
# ---------------------------------------------------------------------------

def _trello_checklist_names(rows):
    with TestClient(load_app(ENV_DIR / "trello-api")) as client:
        trello = data_module(client.app, "trello_data")
        coerced = trello._coerce_checklists(rows)
    return [[i["name"] for i in c["check_items"]] for c in coerced]


def _checklist_row(items):
    return {"id": "cl0001", "name": "QA", "id_card": "c1", "id_board": "b1",
            "items": items}


def test_trello_checklist_items_round_trip_from_a_string():
    assert _trello_checklist_names(
        [_checklist_row("Draft:complete;Review:incomplete")]
    ) == [["Draft", "Review"]]


def test_trello_checklist_items_round_trip_from_a_list():
    assert _trello_checklist_names(
        [_checklist_row(["Draft:complete", "Review:incomplete"])]
    ) == [["Draft", "Review"]]


def test_trello_checklist_states_survive_the_list_shape():
    with TestClient(load_app(ENV_DIR / "trello-api")) as client:
        trello = data_module(client.app, "trello_data")
        coerced = trello._coerce_checklists(
            [_checklist_row(["Draft:complete", "Review:incomplete"])])
    assert [i["state"] for i in coerced[0]["check_items"]] == [
        "complete", "incomplete"]


# ---------------------------------------------------------------------------
# contentful-api -- _parse_json used to json.loads() an already-parsed value
# ---------------------------------------------------------------------------

def _contentful_parse(value, default=None):
    with TestClient(load_app(ENV_DIR / "contentful-api")) as client:
        contentful = data_module(client.app, "contentful_data")
        return contentful._parse_json(value, default)


def test_contentful_fields_round_trip_from_a_json_string():
    assert _contentful_parse('[{"id": "title"}]', []) == [{"id": "title"}]


def test_contentful_fields_round_trip_from_a_list():
    assert _contentful_parse([{"id": "title"}], []) == [{"id": "title"}]


def test_contentful_fields_round_trip_from_a_dict():
    assert _contentful_parse({"title": "Hi"}) == {"title": "Hi"}


def test_contentful_empty_values_still_take_the_default():
    assert _contentful_parse("", []) == []
    assert _contentful_parse(None) == {}


# ---------------------------------------------------------------------------
# Load-time garble detector
# ---------------------------------------------------------------------------

def _populate(rows, table="widgets"):
    from _mutable_store import Store

    store = Store("garble-probe")
    store.register(table, primary_key="id", initial_loader=lambda: rows)
    store.table(table)


def test_load_warns_when_a_column_holds_a_json_array_repr(capsys):
    _populate([{"id": "1", "tags": ["['a', 'b']"]}])
    err = capsys.readouterr().err
    assert "ONE-element list" in err and "tags" in err, err


def test_load_warns_when_a_column_holds_an_unsplit_token_run(capsys):
    _populate([{"id": "1", "member_ids": ["U01AMELIA;U02BEN"]}])
    err = capsys.readouterr().err
    assert "ONE-element list" in err and "member_ids" in err, err


def test_load_is_silent_for_a_correctly_split_list(capsys):
    _populate([{"id": "1", "tags": ["a", "b"]}])
    assert "ONE-element list" not in capsys.readouterr().err


def test_load_is_silent_for_a_legitimate_single_member(capsys):
    _populate([{"id": "1", "tags": ["solo"]}])
    assert "ONE-element list" not in capsys.readouterr().err


def test_load_is_silent_for_a_structured_grammar_value(capsys):
    """RFC 5545 recurrence is one element that legitimately carries ';'."""
    _populate([{"id": "1", "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU"]}])
    assert "ONE-element list" not in capsys.readouterr().err


def test_load_is_silent_for_prose_containing_a_comma(capsys):
    _populate([{"id": "1", "notes": ["Call Ana, then ship"]}])
    assert "ONE-element list" not in capsys.readouterr().err
