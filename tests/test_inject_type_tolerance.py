"""Locks for the admin plane's lossless type tolerance.

The injection plane is authored by hand, and until now it demanded that a task
spell every primary key and every cell in the type the seed loader happened to
produce. A pk written "99" against a table the loader keyed on int 99 missed,
silently, and the op became an orphan -- the authoring side of the
willie/koji/sean/abena class, where the scenario was right and the world never
got built.

These tests pin the two rules that close it and, just as importantly, the
places they must NOT fire:

  * a lookup bends, stored data never does
  * a cell is retyped only against an example, and only on an exact round trip
  * every fired AND every refused coercion is reported
  * documents, containers, nulls, empty tables and mixed columns are untouched
  * the agent-facing ``Table.get`` is exactly as strict as it was

Run with: pytest tests/test_inject_type_tolerance.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = REPO_ROOT / "environment"

if str(ENV_DIR) not in sys.path:
    sys.path.insert(0, str(ENV_DIR))

import _mutable_store as ms  # noqa: E402

pytest.importorskip("fastapi")


SEED = [
    {"iid": 99, "project_id": 101, "title": "seed-a", "weight": 3,
     "locked": True, "ratio": 1.5, "labels": ["x"]},
    {"iid": 100, "project_id": 101, "title": "seed-b", "weight": 5,
     "locked": False, "ratio": 2.5, "labels": ["y"]},
]


def _fresh_store(name: str) -> ms.Store:
    store = ms.Store(name)
    store.register("issues", "iid", lambda: [dict(r) for r in SEED])
    store.register("texts", "id", lambda: [{"id": "a-1", "n": "7"},
                                           {"id": "a-2", "n": "8"}])
    store.register("mixed", "id", lambda: [{"id": 1, "v": 1}, {"id": 2, "v": "two"}])
    store.register("empties", "id", lambda: [])
    store.register_document("cfg", lambda: {"n": 1, "flag": True})
    return store


@pytest.fixture
def store() -> ms.Store:
    return _fresh_store("demo-api")


@pytest.fixture
def issues(store: ms.Store) -> ms.Table:
    return store.table("issues")


def _details(notes, kind):
    return [n["detail"] for n in notes if n["kind"] == kind]


# --- rule 1: the lookup bends, the stored row does not ----------------------


def test_string_pk_reaches_an_int_keyed_row(issues):
    row, notes = issues.admin_get("99")
    assert row is not None and row["iid"] == 99
    assert _details(notes, "coercion") == ["pk '99' -> 99 (demo-api issues)"]


def test_int_pk_reaches_a_string_keyed_row(store):
    texts = store.table("texts")
    texts.upsert({"id": "7", "n": "x"})
    row, notes = texts.admin_get(7)
    assert row is not None and row["id"] == "7"
    assert _details(notes, "coercion") == ["pk 7 -> '7' (demo-api texts)"]


def test_correctly_typed_pk_still_lands_and_says_nothing(issues):
    row, notes = issues.admin_get(99)
    assert row["iid"] == 99
    assert notes == []


def test_patch_through_a_string_pk_lands_on_the_int_row(issues):
    row, used_pk, notes = issues.admin_patch("99", {"title": "patched"})
    assert row["title"] == "patched"
    assert used_pk == 99
    assert issues.get(99)["title"] == "patched"
    assert _details(notes, "coercion") == ["pk '99' -> 99 (demo-api issues)"]


def test_delete_through_a_string_pk_removes_the_int_row(issues):
    ok, used_pk, notes = issues.admin_delete("99")
    assert ok is True and used_pk == 99
    assert issues.get(99) is None
    assert len(issues) == 1
    assert _details(notes, "coercion") == ["pk '99' -> 99 (demo-api issues)"]


def test_upsert_with_a_string_pk_updates_rather_than_duplicating(issues):
    before = len(issues)
    row, notes = issues.admin_upsert(
        {"iid": "99", "project_id": 101, "title": "replaced", "weight": 3,
         "locked": True, "ratio": 1.5, "labels": ["x"]})
    assert len(issues) == before
    assert row["iid"] == 99, "the stored row keeps the type it was loaded with"
    assert issues.get(99)["title"] == "replaced"
    assert _details(notes, "coercion") == ["pk '99' -> 99 (demo-api issues)"]


def test_patch_restating_the_primary_key_is_not_a_move(issues):
    """A body echoing its own pk in the author's spelling must not 400.

    ``Table.patch`` refuses to change the primary key. Tolerant resolution
    turns "99" into 99, which would make the echoed "99" look like an attempt
    to move the row onto a new key.
    """
    row, _used, _notes = issues.admin_patch("99", {"iid": "99", "title": "ok"})
    assert row["title"] == "ok"
    assert row["iid"] == 99


def test_a_genuine_primary_key_change_is_still_refused(issues):
    with pytest.raises(ms.StoreError):
        issues.admin_patch("99", {"iid": 1234})


def test_a_bool_pk_reaches_its_text_spelling(store):
    texts = store.table("texts")
    texts.upsert({"id": "True", "n": "flagged"})
    row, notes = texts.admin_get(True)
    assert row is not None and row["id"] == "True"
    assert _details(notes, "coercion") == ["pk True -> 'True' (demo-api texts)"]


def test_both_spellings_hit_their_own_row_when_a_table_holds_both(store):
    """Exact-type match wins, so a table holding both is still deterministic.

    This is the state the pre-fix duplicate-insert bug produced, and a store
    that has one must keep answering each spelling with its own row rather
    than folding one onto the other.
    """
    texts = store.table("texts")
    texts.upsert({"id": "7", "n": "str-seven"})
    texts.upsert({"id": 7, "n": "int-seven"})
    assert texts.admin_get("7") == ({"id": "7", "n": "str-seven"}, [])
    assert texts.admin_get(7) == ({"id": 7, "n": "int-seven"}, [])


class _LooseText(str):
    """A key that folds like its text but matches nothing by equality.

    Dict equality is coarser than folding for every scalar JSON produces --
    anything folding to ``"7"`` is either numerically 7 or the string "7", and
    both hit the rows dict directly. That makes the multi-hit branch
    unreachable from a real payload, so it is exercised here through a type
    that deliberately separates the two notions of equality.
    """

    __hash__ = str.__hash__

    def __eq__(self, other):
        return False


def test_an_ambiguous_fold_resolves_nothing_and_says_so(store):
    texts = store.table("texts")
    texts.upsert({"id": "7", "n": "str-seven"})
    texts.upsert({"id": 7, "n": "int-seven"})

    match = texts.admin_resolve_pk(_LooseText("7"))

    assert match.matched is False
    assert match.warning["kind"] == "coercion_refused"
    assert "folds onto 2 stored keys" in match.warning["detail"]


def test_upsert_leaves_an_ambiguous_pk_raw(store):
    texts = store.table("texts")
    texts.upsert({"id": "7", "n": "str-seven"})
    texts.upsert({"id": 7, "n": "int-seven"})
    before = len(texts)

    row, notes = texts.admin_upsert({"id": _LooseText("7"), "n": "third"})

    assert len(texts) == before + 1, "neither rival row may be overwritten"
    assert texts.get("7")["n"] == "str-seven"
    assert texts.get(7)["n"] == "int-seven"
    assert _details(notes, "coercion_refused")


# --- rule 2: alignment by example, lossless only ---------------------------


def test_exact_int_text_is_aligned(issues):
    row, _used, notes = issues.admin_patch(99, {"weight": "42"})
    assert row["weight"] == 42 and isinstance(row["weight"], int)
    assert _details(notes, "coercion") == [
        "weight '42' -> 42 (demo-api issues.weight)"]


def test_true_and_false_are_aligned_in_any_casing(issues):
    row, _used, _n = issues.admin_patch(99, {"locked": "FALSE"})
    assert row["locked"] is False
    row, _used, _n = issues.admin_patch(99, {"locked": "true"})
    assert row["locked"] is True


def test_exact_float_text_is_aligned(issues):
    row, _used, notes = issues.admin_patch(99, {"ratio": "9.5"})
    assert row["ratio"] == 9.5 and isinstance(row["ratio"], float)
    assert _details(notes, "coercion")


@pytest.mark.parametrize("raw", ["9.5", "9.5abc", "", " ", "007", "+7", "1_0"])
def test_text_that_does_not_spell_an_int_exactly_stays_raw(issues, raw):
    row, _used, notes = issues.admin_patch(99, {"weight": raw})
    assert row["weight"] == raw, f"{raw!r} must not be coerced into an int column"
    assert _details(notes, "coercion_refused"), f"{raw!r} must be reported"


def test_float_text_carrying_more_precision_than_a_float_stays_raw(issues):
    lossy = "1.0000000000000000001"
    row, _used, notes = issues.admin_patch(99, {"ratio": lossy})
    assert row["ratio"] == lossy
    assert _details(notes, "coercion_refused")


@pytest.mark.parametrize("raw", ["1", "0", "yes", "no", "t", "f", "True!"])
def test_only_true_and_false_reach_a_bool_column(issues, raw):
    row, _used, notes = issues.admin_patch(99, {"locked": raw})
    assert row["locked"] == raw
    assert _details(notes, "coercion_refused")


def test_an_int_is_never_stringified_into_a_text_column(issues):
    row, _used, notes = issues.admin_patch(99, {"title": 42})
    assert row["title"] == 42
    assert _details(notes, "coercion_refused") == [
        "title 42 is int where the column holds str; left raw "
        "(demo-api issues.title)"]


def test_containers_and_nulls_are_never_touched(issues):
    payload = {"labels": ["1", "2"], "title": None, "weight": {"n": "1"}}
    row, _used, notes = issues.admin_patch(99, payload)
    assert row["labels"] == ["1", "2"]
    assert row["title"] is None
    assert row["weight"] == {"n": "1"}
    assert _details(notes, "coercion") == []


def test_an_empty_table_offers_no_example(store):
    empties = store.table("empties")
    row, notes = empties.admin_upsert({"id": "7", "v": "42", "b": "true"})
    assert row == {"id": "7", "v": "42", "b": "true"}
    assert notes == []


def test_a_mixed_column_offers_no_example(store):
    mixed = store.table("mixed")
    row, _used, notes = mixed.admin_patch(1, {"v": "99"})
    assert row["v"] == "99"
    assert _details(notes, "coercion") == []


def test_a_new_row_joins_its_siblings_key_type(issues):
    """An insert aligns its pk by example, so it stays reachable.

    Without this the new row is the one row a correctly typed getter --
    ``m["iid"] == int(mr_iid)`` -- cannot address.
    """
    row, notes = issues.admin_upsert(
        {"iid": "777", "project_id": 101, "title": "new", "weight": 1,
         "locked": False, "ratio": 1.0, "labels": []})
    assert row["iid"] == 777 and isinstance(row["iid"], int)
    assert issues.get(777) is not None
    assert _details(notes, "coercion")


def test_load_time_context_keys_are_left_alone(store):
    store.register("ctx", "id", lambda: [
        {"id": 1, "__row_index__": 0, "_pk": "1#0", "n": 5}])
    ctx = store.table("ctx")
    row, _used, _n = ctx.admin_patch(1, {"__row_index__": "9", "_pk": "9", "n": "6"})
    assert row["__row_index__"] == "9" and row["_pk"] == "9"
    assert row["n"] == 6


# --- documents stay byte-raw ------------------------------------------------


def test_documents_are_never_coerced(store):
    doc = store.document("cfg")
    value = doc.set({"n": "42", "flag": "true", "deep": {"k": "1"}, "l": ["2"]})
    assert value == {"n": "42", "flag": "true", "deep": {"k": "1"}, "l": ["2"]}
    assert doc.get() == value


def test_document_merge_is_never_coerced(store):
    doc = store.document("cfg")
    merged = doc.merge({"n": "42", "flag": "false"})
    assert merged["n"] == "42" and merged["flag"] == "false"


# --- the agent-facing surface is untouched ---------------------------------


def test_the_plain_getter_is_exactly_as_strict_as_before(issues):
    """``Table.get`` is what every ``<api>_data.py`` getter calls.

    Tolerance belongs to the admin plane. An agent that sends the wrong type
    still gets its 404/422 -- that refusal is a thing the benchmark grades, and
    loosening it here would quietly delete the signal.
    """
    assert issues.get("99") is None
    assert issues.get(99) is not None
    assert issues.patch("99", {"title": "x"}) is None
    assert issues.delete("99") is False


def test_a_plain_upsert_still_bypasses_alignment(issues):
    before = len(issues)
    row = issues.upsert({"iid": "99", "title": "raw"})
    assert row["iid"] == "99"
    assert len(issues) == before + 1, "the un-tolerant path is unchanged"


# --- lossless probes --------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("0", 0), ("7", 7), ("-7", -7), (" 7 ", 7), ("1000", 1000)])
def test_exact_int_accepts_only_an_exact_spelling(text, expected):
    assert ms._exact_int(text) == expected


@pytest.mark.parametrize("text", ["007", "+7", "7.0", "7e0", "", "x", "1_0"])
def test_exact_int_refuses_everything_else(text):
    assert ms._exact_int(text) is ms._MISSING


@pytest.mark.parametrize("text,expected", [
    ("1.5", 1.5), ("0.1", 0.1), ("1e3", 1000.0), ("-2.25", -2.25)])
def test_exact_float_accepts_values_a_float_carries_exactly(text, expected):
    assert ms._exact_float(text) == expected


@pytest.mark.parametrize("text", [
    "1.0000000000000000001", "nan", "inf", "-inf", "", "x"])
def test_exact_float_refuses_lossy_and_non_finite(text):
    assert ms._exact_float(text) is ms._MISSING


def test_example_scalar_type_refuses_to_guess():
    assert ms.example_scalar_type([1, 2, 3]) is int
    assert ms.example_scalar_type([True, False]) is bool
    assert ms.example_scalar_type([1, True]) is None, "bool is not an int here"
    assert ms.example_scalar_type([1, "2"]) is None
    assert ms.example_scalar_type([1, None, 2]) is int
    assert ms.example_scalar_type([]) is None
    assert ms.example_scalar_type([None, None]) is None
    assert ms.example_scalar_type([[1], [2]]) is None


# --- the admin plane reports over HTTP -------------------------------------


@pytest.fixture
def admin_app(monkeypatch, store):
    monkeypatch.setenv("MOCK_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MOCK_ADMIN_ALLOWLIST", "")
    monkeypatch.delenv("MOCK_ADMIN_TOKEN", raising=False)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import admin_plane

    app = FastAPI()
    admin_plane.install_admin_plane(app, store)
    return TestClient(app), admin_plane.COERCION_HEADER


def _header_notes(response, header):
    raw = response.headers.get(header)
    return json.loads(raw) if raw else []


def test_http_patch_reports_a_fired_coercion(admin_app):
    client, header = admin_app
    r = client.patch("/admin/data/issues/99", json={"fields": {"weight": "42"}})
    assert r.status_code == 200
    assert r.json()["weight"] == 42
    kinds = {(n["kind"], n["rule"]) for n in _header_notes(r, header)}
    assert ("coercion", "pk") in kinds
    assert ("coercion", "value") in kinds


def test_http_patch_reports_a_refused_coercion(admin_app):
    client, header = admin_app
    r = client.patch("/admin/data/issues/100", json={"fields": {"weight": "9.5"}})
    assert r.status_code == 200
    assert r.json()["weight"] == "9.5"
    refused = [n for n in _header_notes(r, header)
               if n["kind"] == "coercion_refused"]
    assert len(refused) == 1
    assert refused[0]["rule"] == "value" and refused[0]["column"] == "weight"


def test_a_correctly_typed_payload_draws_no_value_warning(admin_app):
    """Over HTTP the pk fold is still reported -- a path param is always text.

    Deciding whether that fold undid the AUTHOR or merely undid the URL needs
    the op's authored pk type, which only the injector has; see
    ``InjectApplier._drop_path_roundtrip`` and the replay tests below. What
    must be silent here is the payload: a correctly typed body says nothing.
    """
    client, header = admin_app
    r = client.patch("/admin/data/issues/99", json={"fields": {"weight": 42}})
    assert r.status_code == 200
    assert [n for n in _header_notes(r, header) if n["rule"] == "value"] == []


def test_a_string_keyed_table_is_untouched_end_to_end(admin_app):
    client, header = admin_app
    r = client.patch("/admin/data/texts/a-1", json={"fields": {"n": "9"}})
    assert r.status_code == 200
    assert r.json()["n"] == "9"
    assert header not in r.headers, "nothing was mistyped; say nothing"


def test_http_upsert_updates_the_int_row_rather_than_duplicating(admin_app):
    client, _header = admin_app
    before = len(client.get("/admin/data/issues").json()["rows"])
    r = client.post("/admin/data/issues", json={"row": {
        "iid": "99", "project_id": 101, "title": "replaced", "weight": 3,
        "locked": True, "ratio": 1.5, "labels": []}})
    assert r.status_code == 200 and r.json()["iid"] == 99
    assert len(client.get("/admin/data/issues").json()["rows"]) == before


def test_the_response_body_carries_no_warning_key(admin_app):
    """The body is the stored row and nothing else.

    ``inject_director._read_back_row`` verifies that body against the service's
    serving vocabulary; a warnings key inside it would read as a column no
    getter can name and the write would fail its own read-back.
    """
    client, _header = admin_app
    body = client.patch("/admin/data/issues/99",
                        json={"fields": {"weight": "42"}}).json()
    assert set(body) == set(SEED[0]), body


def test_http_doc_put_is_byte_raw(admin_app):
    client, header = admin_app
    payload = {"n": "42", "flag": "true", "deep": {"k": "1"}}
    r = client.put("/admin/doc/cfg", json={"value": payload})
    assert r.json() == payload
    assert header not in r.headers


def test_inject_raw_reports_per_operation_and_in_the_header(admin_app):
    client, header = admin_app
    r = client.post("/admin/inject/raw", json={"operations": [
        {"op": "data.patch", "table": "issues", "pk": "99",
         "fields": {"weight": "42"}},
        {"op": "data.patch", "table": "issues", "pk": "100",
         "fields": {"weight": "9.5"}},
    ]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert all(res["ok"] for res in results)
    assert results[0]["row"]["weight"] == 42
    assert results[1]["row"]["weight"] == "9.5"
    assert all(res["coercions"] for res in results)
    kinds = {n["kind"] for n in _header_notes(r, header)}
    assert kinds == {"coercion", "coercion_refused"}


def test_a_missing_row_is_still_a_404(admin_app):
    client, _header = admin_app
    assert client.get("/admin/data/issues/nope").status_code == 404
    assert client.patch("/admin/data/issues/nope",
                        json={"fields": {"title": "x"}}).status_code == 404
    assert client.delete("/admin/data/issues/nope").status_code == 404


# --- the injector translates the report into the timeline -------------------


def test_the_header_constant_matches_on_both_sides_of_the_container():
    import admin_plane
    from src.utils.inject_director import ADMIN_COERCION_HEADER

    assert ADMIN_COERCION_HEADER == admin_plane.COERCION_HEADER


def _replay(op, seed=None):
    from src.utils.inject_director import InjectStage
    from src.utils.inject_inproc import InProcessApplier, replay_service_ops

    store = ms.Store("gitlab-api")
    store.register("issues", "iid",
                   lambda: [dict(r) for r in (seed if seed is not None else SEED)])
    module = types.ModuleType("gitlab_data")
    module._store = store
    module.get_issue = lambda iid: store.table("issues").get(int(iid))
    with tempfile.TemporaryDirectory() as scratch:
        applier = InProcessApplier({"gitlab-api": module}, scratch)
        stage = InjectStage(index=1, name="stage1", from_turn=0, to_turn=1,
                            silent=[op])
        verdicts = replay_service_ops(applier, "gitlab-api", [(stage, op)])
        return verdicts, applier.records, store


def test_a_preflight_replay_of_a_string_pk_op_lands_on_an_int_pk_seed():
    from src.utils.inject_inproc import LANDS_AND_SERVES

    op = {"id": "sil-99-closed", "service": "gitlab-api",
          "admin": {"op": "patch", "table": "issues", "pk": "99",
                    "set": {"title": "closed", "project_id": "101"}}}
    verdicts, records, store = _replay(op)

    assert [v.verdict for v in verdicts] == [LANDS_AND_SERVES]
    assert store.table("issues").get(99)["title"] == "closed"
    assert store.table("issues").get(99)["project_id"] == 101


def test_the_replay_writes_a_warn_entry_per_coercion():
    op = {"id": "sil-99-closed", "service": "gitlab-api",
          "admin": {"op": "patch", "table": "issues", "pk": "99",
                    "set": {"title": "closed", "project_id": "101"}}}
    _verdicts, records, _store = _replay(op)

    warns = [r for r in records if r["type"] == "inject.coercion"]
    assert len(warns) == 2
    for warn in warns:
        assert warn["level"] == "WARN"
        assert warn["id"] == "sil-99-closed"
        assert warn["service"] == "gitlab-api"
        assert warn["admin_op"] == "patch"
        assert warn["kind"] in ("coercion", "coercion_refused")
        assert warn["detail"]
    assert {w["rule"] for w in warns} == {"pk", "value"}
    assert warns[0]["detail"] == "pk '99' -> 99 (gitlab-api issues)"


def test_a_refused_coercion_also_reaches_the_timeline():
    op = {"id": "sil-99-lossy", "service": "gitlab-api",
          "admin": {"op": "patch", "table": "issues", "pk": 99,
                    "set": {"weight": "9.5"}}}
    _verdicts, records, store = _replay(op)

    warns = [r for r in records if r["type"] == "inject.coercion"]
    assert [w["kind"] for w in warns] == ["coercion_refused"]
    assert store.table("issues").get(99)["weight"] == "9.5"


def test_the_warnings_precede_the_op_record_they_describe():
    op = {"id": "sil-99-closed", "service": "gitlab-api",
          "admin": {"op": "patch", "table": "issues", "pk": "99",
                    "set": {"title": "closed"}}}
    _verdicts, records, _store = _replay(op)

    types_in_order = [r["type"] for r in records]
    assert types_in_order.index("inject.coercion") < types_in_order.index("inject.api")


def test_a_correctly_typed_replay_writes_no_coercion_entry():
    op = {"id": "sil-99-clean", "service": "gitlab-api",
          "admin": {"op": "patch", "table": "issues", "pk": 99,
                    "set": {"title": "closed"}}}
    _verdicts, records, _store = _replay(op)

    assert [r for r in records if r["type"] == "inject.coercion"] == []


# --- observability: one getter is not the whole serving shape ---------------
#
# The gate's second opinion on an applied write used to consult exactly one
# projection, `get_<singular>(store_pk)`. Three shapes defeat that and each one
# turned a landed, agent-reachable write into a LANDS-BUT-INVISIBLE refusal: a
# value published only by a query route, an op that restates state the world
# already holds, and a cosmetic stamp misfiled beside a payload that serves.


def _replay_module(api, module, store, ops, seed_table=None):
    from src.utils.inject_director import InjectStage
    from src.utils.inject_inproc import InProcessApplier, replay_service_ops

    module._store = store
    with tempfile.TemporaryDirectory() as scratch:
        applier = InProcessApplier({api: module}, scratch)
        stage = InjectStage(index=1, name="stage1", from_turn=0, to_turn=1,
                            silent=list(ops))
        verdicts = replay_service_ops(applier, api, [(stage, op) for op in ops])
    return verdicts, store


def _gmail_like():
    store = ms.Store("gmail-api")
    store.register("messages", "id", lambda: [
        {"id": "msg-brief", "thread_id": "thr-1", "is_starred": False,
         "is_unread": True, "snippet": "cold chain"},
        {"id": "msg-other", "thread_id": "thr-2", "is_starred": True,
         "is_unread": False, "snippet": "other"},
    ])
    module = types.ModuleType("gmail_data")

    def get_message(pk):
        row = store.table("messages").get(pk)
        if row is None:
            return {"error": f"Message {pk} not found"}
        return {"id": row["id"], "threadId": row["thread_id"],
                "snippet": row["snippet"]}

    def list_messages(query="", max_results=25):
        rows = store.table("messages").rows()
        if "is:starred" in query:
            rows = [r for r in rows if r["is_starred"]]
        if "is:unread" in query:
            rows = [r for r in rows if r["is_unread"]]
        return {"messages": [{"id": r["id"]} for r in rows[:max_results]]}

    module.get_message = get_message
    module.list_messages = list_messages
    return module, store


def test_a_write_only_a_query_route_publishes_still_counts_as_served():
    from src.utils.inject_inproc import LANDS_AND_SERVES

    module, store = _gmail_like()
    op = {"id": "loud_star_brief", "service": "gmail-api",
          "admin": {"op": "patch", "table": "messages", "pk": "msg-brief",
                    "set": {"is_starred": "true"}}}
    verdicts, store = _replay_module("gmail-api", module, store, [op])

    # get_message never emits the flag; ?q=is:starred is the only reader.
    assert [v.verdict for v in verdicts] == [LANDS_AND_SERVES]
    assert "list_messages" in verdicts[0].detail
    assert store.table("messages").get("msg-brief")["is_starred"] is True


def test_an_op_that_restates_the_baseline_is_not_an_invisible_write():
    from src.utils.inject_inproc import LANDS_AND_SERVES

    module, store = _gmail_like()
    op = {"id": "loud_brief_unread", "service": "gmail-api",
          "admin": {"op": "patch", "table": "messages", "pk": "msg-brief",
                    "set": {"is_unread": "true"}}}
    verdicts, _store = _replay_module("gmail-api", module, store, [op])

    # The row is already unread, so the write moves nothing anywhere; a write
    # that produced no drift cannot have produced drift the agent cannot see.
    assert [v.verdict for v in verdicts] == [LANDS_AND_SERVES]
    assert "no-op" in verdicts[0].detail
