"""Contract test guarding the store-write-persistence invariant.

Background
----------
Mock data-module accessors (`_X_rows()` / `_X_doc()`) return DEEP COPIES of the
shared store's state (so callers can't corrupt it and so drift/injection stays
safe). That means mutating an accessor result does NOT persist:

    _customers_rows().append(row)     # appends to a throwaway copy -> LOST
    _issues_rows()[i]["state"] = "x"  # mutates a copy -> LOST
    _notes_rows().pop(i)              # deletes from a copy -> LOST
    _shop_doc()[k] = v                # mutates a copy of the document -> LOST

Writes must instead go through the store (`upsert` / `patch` / `delete`, or the
`_store_insert` / `_store_patch` / `_store_delete` helpers, or
`store.document(...).set/merge`).

This module has two guards:

1. ``test_no_lost_write_idiom`` — a static scan asserting the four lost-write
   idioms have not reappeared in any data module. This is the cheap guard that
   would have caught the original regression at CI time.

2. ``test_create_then_read_persists`` / ``test_update_and_delete_persist`` —
   behavioral checks that exercise representative create/update/delete paths and
   assert the change is visible on the next read.
"""
import importlib.util
import pathlib
import re
import sys

import pytest

ENV = pathlib.Path(__file__).resolve().parent.parent / "environment"

# Four lost-write idioms on a store-backed accessor result.
LOST_WRITE_RE = re.compile(
    r"_[a-z_]+_rows\(\)\.append\("                       # insert into a copy
    r"|_[a-z_]+_rows\(\)\.(pop|remove)\b"                # delete from a copy
    r"|_[a-z_]+_rows\(\)\[[^]]*\]\s*(\[[^]]*\]\s*)?="    # index/field update on a copy
    r"|_[a-z_]+_doc\(\)\[[^]]*\]\s*(\[[^]]*\]\s*)?="     # document key update on a copy
    r"|_[a-z_]+_doc\(\)\.(append|pop|remove)\b"          # document mutate on a copy
)

DATA_MODULES = sorted(ENV.glob("*/*_data.py"))


@pytest.mark.parametrize("path", DATA_MODULES, ids=lambda p: p.parent.name)
def test_no_lost_write_idiom(path):
    """No data module may mutate the copy returned by an accessor."""
    offenders = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if LOST_WRITE_RE.search(line):
            offenders.append(f"{path.parent.name}:{n}: {line.strip()}")
    assert not offenders, (
        "Lost-write idiom found (mutates a copy, not the store). Route the write "
        "through the store (_store_insert/_store_patch/_store_delete or "
        "store.document().set/merge):\n" + "\n".join(offenders)
    )


def _load(api_dir, module_name):
    p = ENV / api_dir
    sys.path.insert(0, str(ENV))
    sys.path.insert(0, str(p))
    try:
        spec = importlib.util.spec_from_file_location(module_name, p / f"{module_name}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)
        sys.path.pop(0)


def test_create_then_read_persists():
    """A created entity must be readable back (the original C1 bug).

    monday replaces stripe, which left in the newreq convergence, and carries
    the same shim case in one table: ``items`` is registered with
    primary_key="item_id", so ``_store_insert`` has to synthesize the
    registered key from the row's ``id`` for the create to be addressable.
    """
    md = _load("monday-api", "monday_data")
    item = md.create_item(board_id="board-101", name="Probe", group_id="grp-todo")
    assert md._store.table("items").primary_key != "id"
    assert "error" not in md.get_item(item["id"]), "created item did not persist"


def test_update_and_delete_persist():
    """Update and delete must survive a fresh accessor read."""
    md = _load("monday-api", "monday_data")
    created = md.create_item(board_id="board-101", name="persist_probe",
                             group_id="grp-todo",
                             column_values={"status": {"text": "Working on it"}})
    item_id = created["id"]

    md.update_item(item_id, name="persist_probe_renamed")
    assert md.get_item(item_id)["name"] == "persist_probe_renamed", "update did not persist"

    md.delete_item(item_id)
    assert all(i["item_id"] != item_id for i in md._items_rows()), "delete did not persist"
    assert all(cv["item_id"] != item_id for cv in md._column_values_rows()), \
        "delete_where cascade did not persist: the item's column value outlived it"


def test_injection_still_visible():
    """Out-of-band store mutation (drift/injection) is visible on the next read.

    Asserted against github rather than monday so the create/read path and the
    injection path are not the same module: an accessor that had stopped
    reading the store would otherwise be able to fail both tests the same way
    and look like one bug.
    """
    gh = _load("github-api", "github_data")
    issue = gh.create_issue("orbit-labs", "auth-api", title="DriftMe", body="seed")
    gh._store.table("issues").patch(issue["id"], {"title": "drifted"})
    assert gh.get_issue("orbit-labs", "auth-api", issue["number"])["title"] == "drifted"
