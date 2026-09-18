"""Import one mock service for real, with a tap on the rows its loader read.

``script/check_route_contracts.py`` grew this machinery to answer a question no
static pass can: a coercer only corrupts a row when it actually runs. The seed
round-trip diffs the bytes a service was handed against the rows it ended up
holding, which is how the ``member_ids: ["a", "b"]`` -> ``"['a', 'b']"``
corruption became visible at all.

It lives here rather than in that script because the pre-trajectory task gate
(``src.utils.inject_preflight``) asks the same question of a DIFFERENT input:
not the pristine fleet, but one task's ``mock_data/`` overlay laid over it. A
required service whose seeds corrupt on load serves the agent nothing, and the
run that follows grades a model against an environment that was never there.
Both callers want the same probe, so there is one.

``fastapi`` and ``pydantic`` are imported at module scope on purpose: every
``load_service`` call evicts the modules the service import created, and a
deferred import would be re-run after the eviction and hand back a fresh class
object. Importing here puts them in the pre-import snapshot, which pins one
identity for the whole process — and keeps the per-service import off the hot
path, which is what lets the gate finish in seconds.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import fastapi.routing  # noqa: F401 - identity pin, see module docstring
import pydantic  # noqa: F401 - identity pin, see module docstring

__all__ = [
    "CTX_KEYS",
    "REPR_ARTIFACT",
    "ServiceProbe",
    "detect_seed_roundtrip",
    "is_empty",
    "is_null_sentinel",
    "load_service",
    "pair_rows",
    "repr_garbled",
]

#: Context keys ``read_seed_with_ctx`` injects for error messages. They are
#: stripped by every ``_strip_ctx`` coercer, so comparing them would report the
#: entire fleet.
CTX_KEYS = frozenset({"__api__", "__table__", "__file__", "__row_index__", "__ragged__"})

#: The fingerprint of ``str(<python list>)`` surviving into a stored value.
#: ``str(["a", "b"]).split(";")`` yields ``["['a', 'b']"]`` and splitting on
#: ``","`` yields ``["['a'", " 'b']"]``, so every fragment carries either a
#: bracket glued to a quote or is a bare quoted token.
REPR_ARTIFACT = re.compile(
    r"""(?:^|[\s,])[\[\{(]\s*['"]"""     # "['a"   leading fragment
    r"""|['"]\s*[\]\})]\s*$"""            # "'b']"  trailing fragment
    r"""|^\s*['"][^'"]*['"]\s*$"""        # " 'b'"  middle fragment
)

_EMPTY = (None, "", [], {}, ())

#: Spellings a seed file uses for "this cell has no value". CSV has no null, so
#: authors write one, and a loader that turns ``"null"`` into ``None`` is
#: reading the seed correctly rather than destroying it. Without this the
#: round-trip reports every deliberately-blank cell as a coercion loss.
_NULL_SENTINELS = frozenset({"null", "none", "nan", "n/a", "na", "-", "--"})


def is_empty(value: Any) -> bool:
    return any(value is e or value == e for e in _EMPTY) if not isinstance(value, bool) else False


def is_null_sentinel(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _NULL_SENTINELS


class ServiceProbe:
    """One imported service: its FastAPI app, its store, and its raw seed rows."""

    __slots__ = ("api", "app", "store", "raw_seeds", "error")

    def __init__(self, api: str) -> None:
        self.api = api
        self.app: Any = None
        self.store: Any = None
        self.raw_seeds: Dict[str, List[Dict[str, Any]]] = {}
        self.error: Optional[str] = None


def load_service(api_dir: Path) -> ServiceProbe:
    """Import ``api_dir/server.py`` with a tap on the seed reader.

    The import has to happen with BOTH the service dir and ``environment/`` on
    ``sys.path`` -- every ``server.py`` imports its data module and
    ``tracking_middleware`` as flat siblings. Modules created by the import are
    evicted afterwards so the next service does not inherit a stale
    ``<name>_data``; ``_mutable_store`` is deliberately imported BEFORE the
    snapshot so it survives eviction and keeps its ``_STORES`` registry, which
    is the only handle we have on the rows the service actually loaded.

    A FRESH ``_mutable_store`` is forced for every service, and the caller's
    instance is put back afterwards. ``Store.register`` and ``eager_load`` are
    both idempotent, so a store that some earlier importer already populated
    would run neither the loader nor the tap, and the seed round-trip would
    report a clean service by measuring nothing at all. Re-importing is the
    only way to guarantee the loaders actually run under the tap.
    """
    probe = ServiceProbe(api_dir.name)
    env_dir = api_dir.parent
    saved_path = list(sys.path)
    caller_store_mod = sys.modules.pop("_mutable_store", None)
    sys.path[:0] = [str(api_dir), str(env_dir)]
    try:
        store_mod = importlib.import_module("_mutable_store")
    except ImportError as exc:
        sys.path[:] = saved_path
        if caller_store_mod is not None:
            sys.modules["_mutable_store"] = caller_store_mod
        probe.error = f"{type(exc).__name__}: {exc}"
        return probe

    original_reader = store_mod.read_seed_with_ctx

    def tapped_reader(path: Any, api: str, table: str) -> Any:
        rows = original_reader(path, api, table)
        probe.raw_seeds.setdefault(table, []).extend(copy.deepcopy(list(rows)))
        return rows

    saved_mods = dict(sys.modules)
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        store_mod.read_seed_with_ctx = tapped_reader
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            spec = importlib.util.spec_from_file_location(
                f"_probe_{api_dir.name.replace('-', '_')}_server", str(api_dir / "server.py"))
            if spec is None or spec.loader is None:
                raise ImportError("no import spec for server.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        probe.app = module.app
        probe.store = store_mod.get_store(api_dir.name)
    except BaseException as exc:  # noqa: BLE001 - a service may raise anything
        probe.error = f"{type(exc).__name__}: {exc}"
    finally:
        store_mod.read_seed_with_ctx = original_reader
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name not in saved_mods:
                del sys.modules[name]
        if caller_store_mod is not None:
            sys.modules["_mutable_store"] = caller_store_mod
        else:
            sys.modules.pop("_mutable_store", None)
    return probe


def pair_rows(raw: List[Dict[str, Any]], coerced: List[Dict[str, Any]],
              primary_key: str) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Match raw seed rows to loaded rows on the primary key, else on order.

    A synthetic key (``_pk``) is minted by the loader, so it is absent from the
    raw row by construction; load order is then the only honest pairing, and it
    holds exactly when the coercer is the 1:1 map the fleet uses.
    """
    by_key = {}
    for row in coerced:
        if primary_key in row:
            by_key[str(row[primary_key])] = row
    if by_key and all(primary_key in r for r in raw):
        return [(r, by_key[str(r[primary_key])]) for r in raw
                if str(r[primary_key]) in by_key]
    if len(raw) == len(coerced):
        return list(zip(raw, coerced))
    return []


def repr_garbled(value: Any) -> bool:
    """True when a stored value carries a ``str(<python list>)`` artifact."""
    if isinstance(value, str):
        return bool(REPR_ARTIFACT.search(value))
    if isinstance(value, (list, tuple)):
        return any(repr_garbled(v) for v in value)
    return False


def detect_seed_roundtrip(probe: ServiceProbe) -> List[Dict[str, Any]]:
    """Seed fields destroyed by the service's own loader.

    Only fields the loader KEPT are compared. A coercer that renames or drops a
    column is making a deliberate projection; a coercer that keeps the column
    and empties or stringifies it is losing data the seed author wrote.
    """
    if probe.store is None:
        return []
    out: List[Dict[str, Any]] = []
    for table_name, raw_rows in sorted(probe.raw_seeds.items()):
        try:
            table = probe.store.table(table_name)
            coerced = table.rows()
        except Exception:  # noqa: BLE001 - table name may not be a store table
            continue
        primary_key = getattr(table, "primary_key", None) or getattr(table, "_primary_key", "id")
        seen: Set[str] = set()
        for raw_row, live_row in pair_rows(raw_rows, coerced, primary_key):
            for field, raw_value in raw_row.items():
                if field in CTX_KEYS or field in seen or field not in live_row:
                    continue
                if is_empty(raw_value) or is_null_sentinel(raw_value):
                    continue
                live_value = live_row[field]
                if is_empty(live_value):
                    reason = (f"raw {raw_value!r} coerces to {live_value!r} -- the seeded "
                              "value is gone after the loader runs")
                elif repr_garbled(live_value) and not repr_garbled(raw_value):
                    reason = (f"raw {raw_value!r} coerces to {live_value!r} -- a python "
                              "repr leaked into the stored value, the signature of a "
                              "JSON list handed to a csv-list coercer")
                else:
                    continue
                seen.add(field)
                out.append({
                    "method": "SEED", "path": f"{table_name}.{field}",
                    "finding": "SEED_COERCION_LOSS", "tier": "ERROR",
                    "subject": table_name, "detail": reason,
                })
    return out
