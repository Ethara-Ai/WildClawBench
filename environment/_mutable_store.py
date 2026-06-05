"""
Process-local mutable record store for WildClawBench mock API services.

Purpose
-------
Each <api>_data.py module today loads CSV / JSON files at import time into
plain module-level lists or dicts. That state is mutable *in process* but has
no external mutation surface, and the per-task overlay files are bind-mounted
read-only, so there is no way to change what the mock returns mid-conversation.

This module introduces a tiny shared store every data module can register its
tables with. Once registered, the data module's accessor functions read
through the store, and the admin control plane (see ``admin_plane.py``) can
mutate those same tables at runtime --- inserting, updating, or deleting rows
--- causing subsequent agent-visible API responses to diverge from whatever
the persona's MEMORY.md committed to.

Design properties
-----------------

* **Process-local.** No file I/O at runtime. Mutations live in memory of the
  mock container's uvicorn process, so the read-only overlay CSVs on disk
  remain the canonical baseline (preserving reproducibility of the *initial*
  state across runs).

* **Per-container isolation.** Because the per-task mock stack spins a fresh
  container per task (see ``src/utils/mock_stack.py``), drift in one task
  cannot leak into another.

* **Backward compatible.** Data modules that have NOT migrated to the store
  continue to work unchanged --- the store is purely opt-in on the data-module
  side. APIs that opt in get drift support; APIs that don't, don't.

* **Snapshotable.** ``snapshot()`` / ``restore(snapshot_id)`` enable
  reproducible mid-run "undo" if a drift script wants to revert state.

* **Thread-safe.** A single ``threading.RLock`` per store guards all writes
  and snapshot/restore. Reads return shallow copies of the underlying row
  dicts so callers cannot corrupt store state by mutating returned objects.

Usage from a data module
------------------------

    # environment/airbnb-api/airbnb_data.py
    from _mutable_store import get_store

    _store = get_store("airbnb-api")

    def _initial_listings():
        return _coerce_listings(_load("listings.csv"))

    _store.register("listings", primary_key="listing_id",
                    initial_loader=_initial_listings)

    def get_listing(listing_id):
        row = _store.table("listings").get(listing_id)
        return row or {"error": "listing not found"}

That's it. The admin plane can now mutate the "listings" table via:

    PATCH /admin/data/listings/L_42  {"price_per_night": 999.0}

and ``get_listing("L_42")`` will return the updated row on the very next call.

Why not just expose the underlying dict?
----------------------------------------
Three reasons:

1. We want every mutation to flow through one chokepoint so the admin plane
   can record ``(timestamp, op, before, after)`` triples into a drift log
   without each data module needing to cooperate.

2. The store enforces a primary-key invariant. The existing data modules are
   inconsistent --- some build ``BY_ID`` dicts, some keep only lists, some
   do both --- and the store normalizes that.

3. Snapshots need a single owner. If every data module keeps its own state,
   snapshotting becomes per-module and racy.

This module has zero third-party dependencies on purpose --- it has to import
cleanly inside the slim Docker image used by every mock service.
"""

from __future__ import annotations

import copy
import csv
import math
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional


Row = Dict[str, Any]
Predicate = Callable[[Row], bool]


class StoreError(Exception):
    """Raised for misuse of the store API (unknown table, missing PK, etc.)."""


class CoerceError(StoreError):
    """Load-time failure coercing a mock CSV cell to its declared type.

    Subclasses StoreError for backward-compat (existing ``except StoreError``
    still catches it) while remaining greppable/isinstance-separable from the
    pre-existing primary-key-missing StoreError. Carries api/table/file/
    row_index/column/raw-value context so an operator can fix the exact cell.
    """


_MISSING = object()

_TRUE_TOKENS = frozenset({"true", "1", "yes", "t", "y"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "f", "n"})


def _ctx(row: Row, column: str) -> str:
    api = row.get("__api__", "?")
    table = row.get("__table__", "?")
    src = row.get("__file__", "?")
    idx = row.get("__row_index__", "?")
    return (
        f"api={api} table={table} file={src} "
        f"row_index={idx} column={column!r}"
    )


def read_csv_with_ctx(path: Any, api: str, table: str) -> List[Row]:
    """Centralized load-time CSV contract inherited by all mock APIs.

    Raises CoerceError on file-shape corruption: duplicate header columns
    (DictReader would silently keep last), ragged rows = more fields than
    header (unquoted-comma bug; would silently misalign columns), and
    non-utf-8/csv.Error. Returns [] for empty/header-only files (valid empty
    table). Injects __api__/__table__/__file__/__row_index__ per row.

    Deliberate asymmetry (do not "fix"): SHORT rows (fewer fields -> None for
    missing keys) are NOT rejected here -- strict_* raises on those Nones,
    opt_* defaults them, preserving author-encoded required/optional intent.
    Per-cell coercion is the caller's job, not this function's.
    """
    src = str(path)
    try:
        with open(src, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, restkey="__ragged__", restval=None)
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                dupes = sorted({c for c in fieldnames if fieldnames.count(c) > 1})
                raise CoerceError(
                    f"duplicate header columns {dupes!r}: "
                    f"api={api} table={table} file={src}"
                )
            rows: List[Row] = []
            for idx, r in enumerate(reader):
                ragged = r.pop("__ragged__", None)
                if ragged:
                    raise CoerceError(
                        f"ragged row: expected {len(fieldnames)} fields "
                        f"({fieldnames!r}), got {len(fieldnames) + len(ragged)} "
                        f"(extra values: {ragged!r}); likely an unquoted comma "
                        f"in a text field. api={api} table={table} file={src} "
                        f"row_index={idx}"
                    )
                r["__api__"] = api
                r["__table__"] = table
                r["__file__"] = src
                r["__row_index__"] = idx
                rows.append(r)
            return rows
    except CoerceError:
        raise
    except UnicodeDecodeError as exc:
        raise CoerceError(
            f"file is not valid UTF-8 ({exc}): "
            f"api={api} table={table} file={src}"
        )
    except csv.Error as exc:
        raise CoerceError(
            f"malformed CSV ({exc}): api={api} table={table} file={src}"
        )


def strict_int(row: Row, column: str) -> int:
    if column not in row:
        raise CoerceError(f"required column missing: {_ctx(row, column)}")
    v = row[column]
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise CoerceError(f"required int is blank: {_ctx(row, column)}")
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        raise CoerceError(
            f"required int unparseable, got {v!r}: {_ctx(row, column)}"
        )


def strict_float(row: Row, column: str) -> float:
    if column not in row:
        raise CoerceError(f"required column missing: {_ctx(row, column)}")
    v = row[column]
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise CoerceError(f"required float is blank: {_ctx(row, column)}")
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        raise CoerceError(
            f"required float unparseable, got {v!r}: {_ctx(row, column)}"
        )
    if math.isnan(f) or math.isinf(f):
        raise CoerceError(
            f"required float is non-finite ({f}), got {v!r}: {_ctx(row, column)}"
        )
    return f


def strict_bool(row: Row, column: str) -> bool:
    if column not in row:
        raise CoerceError(f"required column missing: {_ctx(row, column)}")
    v = row[column]
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise CoerceError(f"required bool is blank: {_ctx(row, column)}")
    token = str(v).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise CoerceError(
        f"required bool unrecognized, got {v!r} "
        f"(expected one of true/false/1/0/yes/no): {_ctx(row, column)}"
    )


def strict_str(row: Row, column: str) -> str:
    if column not in row:
        raise CoerceError(f"required column missing: {_ctx(row, column)}")
    v = row[column]
    return "" if v is None else str(v)


def strict_csv_list(row: Row, column: str, sep: str = ",") -> List[str]:
    if column not in row:
        raise CoerceError(f"required column missing: {_ctx(row, column)}")
    v = row[column]
    if v is None or str(v).strip() == "":
        return []
    return [part for part in str(v).split(sep)]


def opt_int(row: Row, column: str, default: Optional[int] = None) -> Optional[int]:
    v = row.get(column, _MISSING)
    if v is _MISSING or v is None or (isinstance(v, str) and v.strip() == ""):
        return default
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def opt_float(row: Row, column: str, default: Optional[float] = None) -> Optional[float]:
    v = row.get(column, _MISSING)
    if v is _MISSING or v is None or (isinstance(v, str) and v.strip() == ""):
        return default
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def opt_bool(row: Row, column: str, default: bool = False) -> bool:
    v = row.get(column, _MISSING)
    if v is _MISSING or v is None or (isinstance(v, str) and v.strip() == ""):
        return default
    token = str(v).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return default


def opt_str(row: Row, column: str, default: str = "") -> str:
    v = row.get(column, _MISSING)
    if v is _MISSING or v is None:
        return default
    return str(v)


def opt_csv_list(
    row: Row, column: str, sep: str = ",", default: Optional[List[str]] = None
) -> List[str]:
    v = row.get(column, _MISSING)
    if v is _MISSING or v is None or str(v).strip() == "":
        return [] if default is None else list(default)
    return [part for part in str(v).split(sep)]


class Table:
    """A single mutable table of rows with a declared primary key.

    Reads return *copies* of rows to keep callers from corrupting internal
    state. Writes go through the parent store's lock so concurrent writes
    from multiple uvicorn workers (or from admin plane + agent at once)
    serialize cleanly.

    The store keeps the canonical insertion order in ``_order`` so that
    list-returning accessors (e.g. ``search_listings``) produce stable output
    --- agents have been observed to take CSV row order as a relevance signal
    in the existing benchmark, so we deliberately preserve it across
    mutations rather than rebuilding from a dict.
    """

    __slots__ = ("_name", "_pk", "_rows", "_order", "_lock", "_parent")

    def __init__(self, name: str, primary_key: str, parent_lock: threading.RLock,
                 parent: "Store"):
        self._name = name
        self._pk = primary_key
        self._rows: Dict[Any, Row] = {}
        self._order: List[Any] = []
        self._lock = parent_lock
        self._parent = parent

    @property
    def name(self) -> str:
        return self._name

    @property
    def primary_key(self) -> str:
        return self._pk

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[Row]:
        with self._lock:
            order = list(self._order)
            rows = {k: copy.deepcopy(self._rows[k]) for k in order if k in self._rows}
        for k in order:
            if k in rows:
                yield rows[k]

    def rows(self) -> List[Row]:
        """Return all rows in insertion order, as deep copies."""
        with self._lock:
            return [copy.deepcopy(self._rows[k]) for k in self._order if k in self._rows]

    def get(self, pk_value: Any) -> Optional[Row]:
        """Return a single row by primary key, or None.

        Returns a deep copy. Callers may mutate the result freely.
        """
        with self._lock:
            r = self._rows.get(pk_value)
            return copy.deepcopy(r) if r is not None else None

    def find(self, predicate: Predicate) -> List[Row]:
        """Return all rows where ``predicate(row)`` is truthy.

        The predicate runs against deep copies, so it cannot mutate state.
        """
        with self._lock:
            order = list(self._order)
            snapshot = {k: copy.deepcopy(self._rows[k]) for k in order if k in self._rows}
        return [snapshot[k] for k in order if k in snapshot and predicate(snapshot[k])]

    def find_one(self, predicate: Predicate) -> Optional[Row]:
        """First row matching ``predicate``, or None."""
        results = self.find(predicate)
        return results[0] if results else None

    def upsert(self, row: Row) -> Row:
        """Insert or replace a row. The row MUST contain the primary key.

        Returns a copy of the stored row.
        """
        if self._pk not in row:
            raise StoreError(
                f"upsert into '{self._name}' missing primary key '{self._pk}'"
            )
        pk_value = row[self._pk]
        with self._lock:
            existed = pk_value in self._rows
            self._rows[pk_value] = copy.deepcopy(row)
            if not existed:
                self._order.append(pk_value)
            return copy.deepcopy(self._rows[pk_value])

    def patch(self, pk_value: Any, fields: Row) -> Optional[Row]:
        """Shallow-update fields on the row identified by ``pk_value``.

        Returns the updated row (copy), or None if not found.
        Refuses to change the primary-key field itself --- that's a
        delete-plus-insert, which the admin plane spells out explicitly.
        """
        with self._lock:
            if pk_value not in self._rows:
                return None
            row = self._rows[pk_value]
            for k, v in fields.items():
                if k == self._pk and v != pk_value:
                    raise StoreError(
                        f"patch may not change primary key '{self._pk}' "
                        f"on table '{self._name}'"
                    )
                row[k] = copy.deepcopy(v)
            return copy.deepcopy(row)

    def delete(self, pk_value: Any) -> bool:
        """Remove a row. Returns True if it existed."""
        with self._lock:
            if pk_value not in self._rows:
                return False
            del self._rows[pk_value]
            self._order = [k for k in self._order if k != pk_value]
            return True

    def update_where(self, predicate: Predicate, fields: Row) -> int:
        """Apply ``fields`` to every row matching ``predicate``. Returns count.

        Behaves as a series of ``patch`` calls under a single lock, so the
        admin plane can express bulk drifts like "raise all prices by 50%"
        atomically.
        """
        n = 0
        with self._lock:
            for pk_value in list(self._order):
                row = self._rows.get(pk_value)
                if row is None:
                    continue
                if predicate(copy.deepcopy(row)):
                    for k, v in fields.items():
                        if k == self._pk:
                            raise StoreError(
                                f"update_where may not change primary key "
                                f"'{self._pk}' on table '{self._name}'"
                            )
                        row[k] = copy.deepcopy(v)
                    n += 1
        return n

    def delete_where(self, predicate: Predicate) -> int:
        """Delete every row matching ``predicate``. Returns count."""
        n = 0
        with self._lock:
            keep: List[Any] = []
            for pk_value in self._order:
                row = self._rows.get(pk_value)
                if row is None:
                    continue
                if predicate(copy.deepcopy(row)):
                    del self._rows[pk_value]
                    n += 1
                else:
                    keep.append(pk_value)
            self._order = keep
        return n

    def _dump(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "primary_key": self._pk,
                "order": list(self._order),
                "rows": {k: copy.deepcopy(v) for k, v in self._rows.items()},
            }

    def _load(self, blob: Dict[str, Any]) -> None:
        with self._lock:
            self._pk = blob["primary_key"]
            self._order = list(blob["order"])
            self._rows = {k: copy.deepcopy(v) for k, v in blob["rows"].items()}


class Document:
    """A single mutable JSON-like value (object, array, scalar).

    Used by data modules that have one-off config blobs that don't fit a
    table model --- e.g. Stripe's ``balance.json``, the persona ``profile.json``
    inside fintrack. The admin plane treats documents as opaque values:
    ``GET /admin/doc/<name>`` and ``PUT /admin/doc/<name>``.
    """

    __slots__ = ("_name", "_value", "_lock")

    def __init__(self, name: str, value: Any, parent_lock: threading.RLock):
        self._name = name
        self._value = copy.deepcopy(value)
        self._lock = parent_lock

    @property
    def name(self) -> str:
        return self._name

    def get(self) -> Any:
        with self._lock:
            return copy.deepcopy(self._value)

    def set(self, value: Any) -> Any:
        with self._lock:
            self._value = copy.deepcopy(value)
            return copy.deepcopy(self._value)

    def merge(self, fields: Dict[str, Any]) -> Any:
        """Shallow-merge ``fields`` into the document if it's an object."""
        with self._lock:
            if not isinstance(self._value, dict):
                raise StoreError(
                    f"merge requires document '{self._name}' to be an object"
                )
            for k, v in fields.items():
                self._value[k] = copy.deepcopy(v)
            return copy.deepcopy(self._value)

    def _dump(self) -> Any:
        with self._lock:
            return copy.deepcopy(self._value)

    def _load(self, blob: Any) -> None:
        with self._lock:
            self._value = copy.deepcopy(blob)


class Store:
    """Holds the tables and documents for a single mock API service.

    One ``Store`` instance per API (keyed by API directory name, e.g.
    "airbnb-api"). The store is reachable from anywhere in the process via
    ``get_store(api_name)``, so the admin plane can mutate state without the
    data module having to expose its internals.

    The store also keeps a drift log: every write goes through ``_record`` so
    the admin plane can return ``GET /admin/drift/log`` with the timeline of
    mutations applied since process start.
    """

    def __init__(self, name: str):
        self._name = name
        self._lock = threading.RLock()
        self._tables: Dict[str, Table] = {}
        self._documents: Dict[str, Document] = {}
        self._initial_loaders: Dict[str, Callable[[], Iterable[Row]]] = {}
        self._initial_docs: Dict[str, Callable[[], Any]] = {}
        self._initialized: Dict[str, bool] = {}
        self._drift_log: List[Dict[str, Any]] = []
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._initial_baseline: Optional[Dict[str, Any]] = None

    @property
    def name(self) -> str:
        return self._name

    def register(
        self,
        table_name: str,
        primary_key: str,
        initial_loader: Callable[[], Iterable[Row]],
    ) -> Table:
        """Register a table. The loader runs the first time the table is
        accessed, not at registration time --- this keeps import order
        independent and avoids re-reading CSVs in test contexts.
        """
        with self._lock:
            if table_name in self._tables:
                return self._tables[table_name]
            t = Table(table_name, primary_key, self._lock, self)
            self._tables[table_name] = t
            self._initial_loaders[table_name] = initial_loader
            self._initialized[table_name] = False
            return t

    def register_document(
        self,
        doc_name: str,
        initial_loader: Callable[[], Any],
    ) -> Document:
        with self._lock:
            if doc_name in self._documents:
                return self._documents[doc_name]
            d = Document(doc_name, None, self._lock)
            self._documents[doc_name] = d
            self._initial_docs[doc_name] = initial_loader
            self._initialized[f"doc::{doc_name}"] = False
            return d

    def eager_load(self) -> None:
        """Force every registered table and document to load now, surfacing any
        CoerceError at import time (before the container reports healthy) instead
        of lazily on the first agent request. Idempotent: already-initialized
        tables are skipped, so output is identical to lazy loading."""
        with self._lock:
            for table_name in list(self._tables):
                if not self._initialized[table_name]:
                    self._populate_table(table_name)
            for doc_name in list(self._documents):
                if not self._initialized[f"doc::{doc_name}"]:
                    d = self._documents[doc_name]
                    d._value = copy.deepcopy(self._initial_docs[doc_name]())
                    self._initialized[f"doc::{doc_name}"] = True

    def table(self, table_name: str) -> Table:
        with self._lock:
            if table_name not in self._tables:
                raise StoreError(
                    f"table '{table_name}' not registered on store '{self._name}'"
                )
            if not self._initialized[table_name]:
                self._populate_table(table_name)
            return self._tables[table_name]

    def document(self, doc_name: str) -> Document:
        with self._lock:
            if doc_name not in self._documents:
                raise StoreError(
                    f"document '{doc_name}' not registered on store '{self._name}'"
                )
            if not self._initialized[f"doc::{doc_name}"]:
                d = self._documents[doc_name]
                d._value = copy.deepcopy(self._initial_docs[doc_name]())
                self._initialized[f"doc::{doc_name}"] = True
            return self._documents[doc_name]

    def _populate_table(self, table_name: str) -> None:
        t = self._tables[table_name]
        rows = list(self._initial_loaders[table_name]())
        for r in rows:
            if t._pk not in r:
                raise StoreError(
                    f"initial row for table '{table_name}' missing primary key "
                    f"'{t._pk}': {list(r.keys())[:8]}"
                )
            pk_value = r[t._pk]
            t._rows[pk_value] = copy.deepcopy(r)
            t._order.append(pk_value)
        self._initialized[table_name] = True

    def list_tables(self) -> List[str]:
        with self._lock:
            return sorted(self._tables.keys())

    def list_documents(self) -> List[str]:
        with self._lock:
            return sorted(self._documents.keys())

    def record(self, op: str, **fields: Any) -> None:
        """Append an entry to the drift log. Called by the admin plane after
        every mutation. Kept simple: ``timestamp``, ``op``, plus whatever
        context the caller wants to attach (table, pk, before, after, ...).
        """
        with self._lock:
            entry = {
                "ts": time.time(),
                "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "op": op,
                **fields,
            }
            self._drift_log.append(entry)

    def drift_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(e) for e in self._drift_log]

    def clear_drift_log(self) -> int:
        with self._lock:
            n = len(self._drift_log)
            self._drift_log.clear()
            return n

    def snapshot(self, label: Optional[str] = None) -> str:
        """Capture full state (all tables, all documents). Returns an opaque
        snapshot id the caller can later pass to ``restore``.
        """
        with self._lock:
            for tn in list(self._tables):
                if not self._initialized[tn]:
                    self._populate_table(tn)
            for dn in list(self._documents):
                if not self._initialized[f"doc::{dn}"]:
                    self.document(dn)
            snap_id = label or f"snap_{uuid.uuid4().hex[:8]}"
            self._snapshots[snap_id] = {
                "tables": {tn: t._dump() for tn, t in self._tables.items()},
                "documents": {dn: d._dump() for dn, d in self._documents.items()},
                "ts": time.time(),
            }
            return snap_id

    def restore(self, snapshot_id: str) -> bool:
        with self._lock:
            blob = self._snapshots.get(snapshot_id)
            if blob is None:
                if snapshot_id == "__baseline__":
                    return self._restore_baseline()
                return False
            for tn, t in self._tables.items():
                if tn in blob["tables"]:
                    t._load(blob["tables"][tn])
            for dn, d in self._documents.items():
                if dn in blob["documents"]:
                    d._load(blob["documents"][dn])
            return True

    def capture_baseline(self) -> None:
        """Snapshot the post-initial-load state under the reserved id
        ``__baseline__``. Called by the admin plane on first /admin access
        so operators always have a known restore point.
        """
        with self._lock:
            if self._initial_baseline is not None:
                return
            for tn in list(self._tables):
                if not self._initialized[tn]:
                    self._populate_table(tn)
            for dn in list(self._documents):
                self.document(dn)
            self._initial_baseline = {
                "tables": {tn: t._dump() for tn, t in self._tables.items()},
                "documents": {dn: d._dump() for dn, d in self._documents.items()},
                "ts": time.time(),
            }

    def _restore_baseline(self) -> bool:
        if self._initial_baseline is None:
            return False
        for tn, t in self._tables.items():
            if tn in self._initial_baseline["tables"]:
                t._load(self._initial_baseline["tables"][tn])
        for dn, d in self._documents.items():
            if dn in self._initial_baseline["documents"]:
                d._load(self._initial_baseline["documents"][dn])
        return True


_STORES: Dict[str, Store] = {}
_REGISTRY_LOCK = threading.RLock()


def get_store(api_name: str) -> Store:
    """Return the Store for ``api_name``, creating it on first call.

    ``api_name`` is the directory name under ``environment/`` (e.g.
    ``"airbnb-api"``). Using the directory name --- not a pretty name ---
    makes the admin plane's URL path mirror the filesystem layout, which
    helps operators when debugging.
    """
    with _REGISTRY_LOCK:
        if api_name not in _STORES:
            _STORES[api_name] = Store(api_name)
        return _STORES[api_name]


def known_stores() -> List[str]:
    with _REGISTRY_LOCK:
        return sorted(_STORES.keys())
