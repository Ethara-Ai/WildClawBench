"""Serving-shape projection: what a mock's public getter can actually read.

Two harness checkers verify a write by re-reading the RAW stored row and
comparing the keys they just wrote (``inject_director._read_back_row`` and the
D17 ingest check in ``script/coerce_dryrun.py``). That comparison is circular:
the store shallow-merges whatever key it is handed, so a write that lands under
a key the service getter never names self-verifies. The pilot-2 xero patch
shipped dead exactly this way -- ``set: {"Status": ...}`` against a row whose
live column is ``status`` created an orphan top-level key, the read-back found
``Status``, and the injector reported ``verified: True`` while the agent kept
seeing the old value.

The fix both sites need is the same: verify against the SERVING projection, not
the raw bag.

How the serving projection is defined
-------------------------------------
Every ``environment/<svc>-api/<svc>_data.py`` getter reads the stored row by
LITERAL key -- ``c["id_board"]``, ``b["member_ids"]`` -- either directly or via
a ``_serialize_<entity>`` mapper. A key no getter names is invisible to the
agent no matter what the store holds. So the set of keys that can reach the
serving shape is exactly the key vocabulary the loader + registered row coercer
produced, which is observable from the rows themselves.

That gives two mechanisms, one per caller:

* **Out-of-process** (``inject_director``, talking to a live container): derive
  the vocabulary from the row as it existed BEFORE the write, unioned with the
  table's sibling rows, both read over the ``/admin/*`` plane. A written key
  outside that vocabulary is an orphan and fails verification. Public-port GETs
  stay forbidden -- they are audit-logged and would corrupt the request counts
  the deterministic checkers grade.

* **In-process** (``coerce_dryrun``, which already imports the overlaid data
  module): call the service getter itself and read the real serving shape.
  Modelling the loader coercion, ``fields`` nesting and key renames for real is
  what removes the D17 false positives.

stdlib-only: imported by a script that deliberately runs without the docker
stack and by a module that static validation paths import.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

__all__ = [
    "row_bag",
    "envelope_keys",
    "partition_expected",
    "loose_eq",
    "comparable_fields",
    "serving_vocabulary",
    "envelope_vocabulary",
    "orphan_keys",
    "verify_against_serving",
    "find_getter",
    "project_in_process",
    "ORPHAN_REASON",
    "UNVERIFIABLE",
    "UNVERIFIABLE_REASON",
]

# Reason stamped on an injector record whose write landed outside the serving
# vocabulary. Deliberately distinct from the value-mismatch reason ("write not
# observed on read-back") so the two failure modes stay tellable apart in
# drift_timeline.jsonl.
ORPHAN_REASON = "write landed on key(s) outside the serving shape"

# Third verdict, stamped on ``verified`` beside True/False. A write that moved
# the row on a LIVE column but did not come back byte-equal is not evidence of
# anything: services reserialize what they store (woocommerce holds a float
# price and serves ``f"{p:.2f}"``, gmail serves a message body base64url-encoded
# under ``payload.body.data``, figma never echoes the ``file_key`` it addresses
# rows by). Reporting those as failures buried the orphan findings that ARE
# provable, so "cannot prove either way" gets its own outcome instead of
# borrowing the failing one.
UNVERIFIABLE = "unverifiable"
UNVERIFIABLE_REASON = ("write moved the row on a live column but the stored form "
                       "is not byte-equal to what was sent — the service coerces "
                       "or reserializes it, so the write can be neither confirmed "
                       "nor disproved from the outside")

# Airtable-style stores nest the business columns under ``fields``; everything
# else keeps them top-level. Both the stored row and a patch payload built by
# ``inject_director._patch_row`` use this wrapper.
NESTED_KEY = "fields"


def row_bag(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The column bag of a stored row: the nested ``fields`` object when the
    store uses one, else the row itself."""
    nested = row.get(NESTED_KEY) if isinstance(row, Mapping) else None
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(row) if isinstance(row, Mapping) else {}


def envelope_keys(row: Mapping[str, Any]) -> Set[str]:
    """The envelope keys of a stored row: everything beside the column bag.

    A nested row carries two namespaces — the ``fields`` object holding the
    business columns, and the envelope the SERVICE owns around it (``id``,
    ``created_at``, ``updated_at``, ``published_version``, ...). They are
    addressed differently by a patch and must be judged separately.
    """
    if not isinstance(row, Mapping):
        return set()
    return {str(k) for k in row} - {NESTED_KEY}


def partition_expected(expected: Mapping[str, Any],
                       nested: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a patch payload into ``(columns, envelope)`` for a nested row.

    ``_patch_row`` sends a nested patch as ``{"fields": {...}}`` plus whatever
    envelope keys the op named, so the caller's ``expected`` mixes two
    namespaces: ``fields`` is a store wrapper rather than a column, and the keys
    beside it are envelope stamps rather than columns. Comparing the whole
    payload against the unwrapped bag flags the wrapper AND every stamp as an
    orphan — contentful's entry ops set the age statement inside ``fields`` and
    move ``updated_at``/``published_version`` next to it in the same op, and all
    three read as missing columns.

    A flat row has no envelope, so everything written is a column.
    """
    if not isinstance(expected, Mapping):
        return {}, {}
    inner = expected.get(NESTED_KEY)
    if nested and isinstance(inner, Mapping):
        return dict(inner), {k: v for k, v in expected.items() if k != NESTED_KEY}
    return dict(expected), {}


def loose_eq(a: Any, b: Any) -> bool:
    """Exact ``==`` first, then a case-insensitive string comparison, so
    ``true``/``"True"`` and ``1``/``"1"`` match. Mirrors the tolerance the
    injector's ``where`` matcher has always applied."""
    if a == b:
        return True
    return str(a).strip().lower() == str(b).strip().lower()


def comparable_fields(expected: Mapping[str, Any]) -> Dict[str, Any]:
    """Scalar expectations only. Nested dict/list values (rich notion property
    objects and the like) are reported but never asserted -- the store holds a
    normalized form the injector cannot reconstruct."""
    return {k: v for k, v in expected.items() if not isinstance(v, (dict, list))}


def _is_target(row: Mapping[str, Any], exclude_pk: Any,
               pk_field: Optional[str]) -> bool:
    """True when ``row`` is the row under verification, which may not vouch for
    the keys the write just introduced on it."""
    if exclude_pk is None:
        return False
    candidates = [row.get(pk_field)] if pk_field else []
    candidates += [row.get("id"), row.get("pk")]
    return any(c is not None and str(c) == str(exclude_pk) for c in candidates)


def serving_vocabulary(
    rows: Iterable[Any],
    *,
    exclude_pk: Any = None,
    pk_field: Optional[str] = None,
    extra: Iterable[str] = (),
) -> Set[str]:
    """Union of the column names visible across ``rows``, i.e. every key a
    service getter could legitimately name.

    ``exclude_pk`` drops the row currently under verification so a key the write
    itself introduced cannot vouch for itself. ``extra`` carries the target
    row's own PRE-write keys, which is what keeps a legitimate column that
    happens to exist on only one row from being called an orphan.
    """
    vocab: Set[str] = {str(k) for k in extra}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if _is_target(row, exclude_pk, pk_field):
            continue
        vocab.update(str(k) for k in row_bag(row))
    return vocab


def envelope_vocabulary(
    rows: Iterable[Any],
    *,
    exclude_pk: Any = None,
    pk_field: Optional[str] = None,
    extra: Iterable[str] = (),
) -> Set[str]:
    """``serving_vocabulary`` for the OTHER namespace: the envelope stamps a
    nested row carries beside its column bag.

    Judged from siblings for the same reason columns are — the patched row
    already carries whatever stamp the write invented by read-back time, so an
    envelope key no other row of the table has is an orphan just as surely as a
    mis-cased column.
    """
    vocab: Set[str] = {str(k) for k in extra}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if _is_target(row, exclude_pk, pk_field):
            continue
        vocab.update(envelope_keys(row))
    return vocab


def orphan_keys(written: Iterable[str], vocabulary: Set[str]) -> List[str]:
    """Written keys absent from the serving vocabulary, sorted.

    Matching is CASE-SENSITIVE on purpose: ``Status`` against a live ``status``
    column is the exact defect this guards -- the store accepts it, the getter
    never reads it. An empty vocabulary means we have nothing to judge against
    and returns no orphans rather than failing every write.
    """
    if not vocabulary:
        return []
    return sorted({str(k) for k in written} - vocabulary)


def near_miss(key: str, vocabulary: Set[str]) -> Optional[str]:
    """The case-insensitive twin of an orphan key, when the vocabulary has one.
    Turns 'no such column' into the actionable 'you meant `status`'."""
    lowered = str(key).strip().lower()
    for known in sorted(vocabulary):
        if known.strip().lower() == lowered:
            return known
    return None


def verify_against_serving(
    expected: Mapping[str, Any],
    bag: Mapping[str, Any],
    vocabulary: Set[str],
) -> Tuple[Dict[str, Any], bool, List[str]]:
    """Post-write verdict for one row.

    Returns ``(after, verified, orphans)`` where ``after`` holds the live value
    of every touched key, ``verified`` is True only when no written key is an
    orphan AND every scalar expectation matches, and ``orphans`` names the keys
    that cannot reach the serving shape.
    """
    orphans = orphan_keys(expected.keys(), vocabulary)
    after = {k: bag.get(k) for k in expected}
    values_ok = all(loose_eq(bag.get(k), v)
                    for k, v in comparable_fields(expected).items())
    return after, (values_ok and not orphans), orphans


def describe_orphans(orphans: Sequence[str], vocabulary: Set[str]) -> str:
    """Human-readable orphan report with near-miss hints, for timeline reasons."""
    parts = []
    for key in orphans:
        hint = near_miss(key, vocabulary)
        parts.append(f"{key!r}" + (f" (did you mean {hint!r}?)" if hint else ""))
    return f"{ORPHAN_REASON}: {', '.join(parts)}"


# --------------------------------------------------------------------------- #
# In-process projection (coerce_dryrun): call the service getter for real.
# --------------------------------------------------------------------------- #

# environment/<svc>-api/<svc>_data.py getters follow one naming convention:
# get_<entity> / list_<entities>. A store table is registered under the plural
# ("cards", "incidents", "records_<tableId>"), so map table -> getter by
# singularizing and trying the documented spellings in order.
_RECORDS_PREFIX = re.compile(r"^records?_", re.I)


def _singular(name: str) -> str:
    n = _RECORDS_PREFIX.sub("", str(name))
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    if n.endswith("sses") or n.endswith("ches") or n.endswith("shes"):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def find_getter(module: Any, table: str) -> Optional[Callable[..., Any]]:
    """The single-row getter a service data-module exposes for ``table``.

    Tries ``get_<singular>`` then ``get_<table>``; returns None when the module
    ships no getter for that table (some tables are list-only). A caller that
    gets None must fall back to the raw row rather than claim a projection it
    never made.
    """
    for candidate in (f"get_{_singular(table)}", f"get_{table}"):
        fn = getattr(module, candidate, None)
        if callable(fn):
            return fn
    return None


def project_in_process(module: Any, table: str, pk: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read one row back through the service's own getter.

    Returns ``(serving_row, mechanism)``. ``mechanism`` is ``"getter:<name>"``
    when the real projection ran and ``"raw-store"`` when the module exposes no
    getter for the table and the caller is seeing the unprojected row. Getters
    signal 'not found' by returning an error dict rather than raising, so an
    ``error`` key is normalized to None.
    """
    fn = find_getter(module, table)
    if fn is None:
        store = getattr(module, "_store", None)
        if store is None:
            return None, "raw-store"
        try:
            return store.table(table).get(pk), "raw-store"
        except Exception:
            return None, "raw-store"
    try:
        result = fn(pk)
    except Exception as exc:  # a getter that raises is itself the finding
        return None, f"getter:{fn.__name__}:{type(exc).__name__}: {exc}"
    if isinstance(result, Mapping) and "error" in result and len(result) <= 2:
        return None, f"getter:{fn.__name__}"
    if not isinstance(result, Mapping):
        return None, f"getter:{fn.__name__}"
    return dict(result), f"getter:{fn.__name__}"


# A serving projection is a JSON document, so its size is what has to be
# bounded, not how deep the walk is allowed to go. The old bound was a depth-2
# cutoff, which silently dropped 34 of figma ``get_file``'s 49 scalars (the whole
# ``document`` node tree), 36 of spotify ``get_playlist``'s 47, and contentful's
# ``sys.contentType.sys.*`` — every one of them read as a lost write. The node
# budget costs nothing on the shapes the fleet actually serves (the widest is
# ~50 scalars) and still refuses to walk a pathological document forever.
_FLATTEN_NODE_BUDGET = 20_000


def flatten_serving(value: Any, *, budget: int = _FLATTEN_NODE_BUDGET) -> Dict[str, Any]:
    """Flatten a serving row into ``{path: scalar}`` pairs, at any depth.

    Serializers rename (``id_board`` -> ``idBoard``) and re-shape
    (``labels: [{"name": n}]``), so a written value is checked for PRESENCE
    anywhere in the projection rather than under the key it was written to --
    the rename is legitimate, the disappearance is not. "Anywhere" has to mean
    anywhere: a value nested past an arbitrary cutoff is present in what the
    agent reads, and calling it missing is the same false negative the rename
    check exists to avoid.

    Walked iteratively so a deep node tree cannot exhaust the interpreter stack,
    and bounded by ``budget`` total nodes rather than by depth.
    """
    out: Dict[str, Any] = {}
    if not isinstance(value, Mapping):
        return out
    stack: List[Tuple[str, Any]] = [("", value)]
    seen = 0
    while stack and seen < budget:
        prefix, node = stack.pop()
        seen += 1
        if isinstance(node, Mapping):
            for k, v in node.items():
                stack.append((f"{prefix}.{k}" if prefix else str(k), v))
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                stack.append((f"{prefix}[{i}]", item))
        elif prefix:
            out[prefix] = node
    return out


def value_visible(expected_value: Any, serving: Mapping[str, Any]) -> bool:
    """True when ``expected_value`` appears anywhere in the serving projection.

    Presence-based rather than key-based so a serializer rename does not read as
    a lost write. Nested dict/list expectations are not asserted, matching the
    out-of-process checker's contract.
    """
    if isinstance(expected_value, (dict, list)):
        return True
    return any(loose_eq(v, expected_value)
               for v in flatten_serving(serving).values())
