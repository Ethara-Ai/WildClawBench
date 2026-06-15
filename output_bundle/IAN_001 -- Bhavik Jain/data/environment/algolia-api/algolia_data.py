"""Data access module for the Algolia API mock service.

Models hosted-search objects: indices, records (objects with an ``objectID``),
and per-index settings. Query implements a case-insensitive substring match
across string fields and a simple ``attr:value`` equality filter syntax.
"""

import csv
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store

_store = get_store("algolia-api")


def _load(filename):
    with open(DATA_DIR / filename, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_bool(v):
    return str(v).strip().lower() == "true"


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _maybe_number(v):
    if v is None or v == "":
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


_NUMERIC_FIELDS = {"price"}
_BOOL_FIELDS = {"in_stock"}


def _coerce_record(row):
    out = {}
    for k, v in row.items():
        if k in _BOOL_FIELDS:
            out[k] = _to_bool(v)
        elif k in _NUMERIC_FIELDS:
            out[k] = _maybe_number(v)
        else:
            out[k] = v
    return out


_indices_meta = [dict(r) for r in _load("indices.csv")]


def _records_table_name(index: str) -> str:
    return f"records__{index}"


_store.register(
    "indices",
    primary_key="name",
    initial_loader=lambda: [
        {
            "name": m["name"],
            "entries": _to_int(m["entries"]),
            "dataSize": _to_int(m["data_size"]),
            "createdAt": m["created_at"],
            "updatedAt": m["updated_at"],
        }
        for m in _indices_meta
    ],
)

# one mutable Table per algolia index, PK = objectID (camelCase, idiosyncratic)
for _meta in _indices_meta:
    _store.register(
        _records_table_name(_meta["name"]),
        primary_key="objectID",
        initial_loader=(lambda csv_name=_meta["records_csv"]:
                        [_coerce_record(r) for r in _load(csv_name)]),
    )


def _coerce_settings(row):
    return {
        "searchableAttributes": [a.strip() for a in row["searchableAttributes"].split(",") if a.strip()],
        "attributesForFaceting": [a.strip() for a in row["attributesForFaceting"].split(",") if a.strip()],
        "hitsPerPage": _to_int(row["hitsPerPage"], 20),
        "ranking": [a.strip() for a in row["ranking"].split(",") if a.strip()],
    }


# settings stored as a Table keyed by "index" column (each row is per-index settings)
_store.register(
    "settings",
    primary_key="index",
    initial_loader=lambda: [
        {"index": r["index"], **_coerce_settings(r)} for r in _load("settings.csv")
    ],
)


def _new_object_id():
    return uuid.uuid4().hex[:16]


def _index_exists(index):
    return _store.table("indices").get(index) is not None


def _ensure_index(index):
    """Algolia auto-creates an index on first write -- register the records table dynamically if absent."""
    if _index_exists(index):
        return
    _store.table("indices").upsert({
        "name": index, "entries": 0, "dataSize": 0,
        "createdAt": "", "updatedAt": "",
    })
    tname = _records_table_name(index)
    if tname not in _store.list_tables():
        _store.register(tname, primary_key="objectID", initial_loader=lambda: [])


def _records(index):
    return _store.table(_records_table_name(index)).rows()


def _matches_query(record, query):
    if not query:
        return True
    q = query.lower()
    for v in record.values():
        if isinstance(v, str) and q in v.lower():
            return True
    return False


def _matches_filters(record, filters):
    """Support simple ``attr:value`` (optionally AND-joined) equality filters."""
    if not filters:
        return True
    clauses = [c.strip() for c in filters.replace(" AND ", "\n").split("\n") if c.strip()]
    for clause in clauses:
        if ":" not in clause:
            continue
        attr, _, value = clause.partition(":")
        attr = attr.strip()
        value = value.strip().strip('"')
        rv = record.get(attr)
        if rv is None:
            return False
        if str(rv).lower() != value.lower():
            return False
    return True


def list_indexes():
    return {"items": _store.table("indices").rows(), "nbPages": 1}


def get_settings(index):
    if not _index_exists(index):
        return {"error": f"Index {index} not found"}
    s = _store.table("settings").get(index)
    if s:
        return {k: v for k, v in s.items() if k != "index"}
    return {
        "searchableAttributes": [],
        "attributesForFaceting": [],
        "hitsPerPage": 20,
        "ranking": [],
    }


def query_index(index, query=None, filters=None, hits_per_page=20, page=0):
    if not _index_exists(index):
        return {"error": f"Index {index} not found"}
    records = _records(index)
    hits = [r for r in records if _matches_query(r, query) and _matches_filters(r, filters)]
    nb_hits = len(hits)
    try:
        hits_per_page = max(1, int(hits_per_page))
    except (TypeError, ValueError):
        hits_per_page = 20
    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0
    nb_pages = (nb_hits + hits_per_page - 1) // hits_per_page if nb_hits else 0
    start = page * hits_per_page
    page_hits = hits[start: start + hits_per_page]
    return {
        "hits": page_hits,
        "nbHits": nb_hits,
        "page": page,
        "nbPages": nb_pages,
        "hitsPerPage": hits_per_page,
        "query": query or "",
        "params": f"query={query or ''}&hitsPerPage={hits_per_page}&page={page}",
    }


def get_object(index, object_id):
    if not _index_exists(index):
        return {"error": f"Index {index} not found"}
    r = _store.table(_records_table_name(index)).get(object_id)
    if r:
        return r
    return {"error": f"Object {object_id} not found in index {index}"}


def add_object(index, body):
    _ensure_index(index)
    record = dict(body or {})
    object_id = record.get("objectID") or _new_object_id()
    record["objectID"] = object_id
    _store.table(_records_table_name(index)).upsert(record)
    return {"objectID": object_id, "createdAt": "",
            "taskID": _to_int(uuid.uuid4().int % 1000000)}


def update_object(index, object_id, body):
    if not _index_exists(index):
        return {"error": f"Index {index} not found"}
    tbl = _store.table(_records_table_name(index))
    cur = tbl.get(object_id)
    if cur:
        merged = {**cur, **(body or {}), "objectID": object_id}
        tbl.upsert(merged)
    else:
        record = dict(body or {})
        record["objectID"] = object_id
        tbl.upsert(record)
    return {"objectID": object_id, "updatedAt": "",
            "taskID": _to_int(uuid.uuid4().int % 1000000)}


def delete_object(index, object_id):
    if not _index_exists(index):
        return {"error": f"Index {index} not found"}
    tbl = _store.table(_records_table_name(index))
    if tbl.delete(object_id):
        return {"objectID": object_id, "deletedAt": "",
                "taskID": _to_int(uuid.uuid4().int % 1000000)}
    return {"error": f"Object {object_id} not found in index {index}"}
