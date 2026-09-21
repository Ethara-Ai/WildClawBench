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

import inspect
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
    "getter_addresses",
    "is_miss",
    "list_surfaces",
    "project_in_process",
    "project_list_surfaces",
    "query_candidates",
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

_KEY_FOLD_RE = re.compile(r"[^a-z0-9]")


def _fold_key(key: Any) -> str:
    """``errorMessages``, ``error_messages`` and ``ERROR-MESSAGES`` are one key."""
    return _KEY_FOLD_RE.sub("", str(key).lower())


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


#: Envelope stamps that record WHEN a row was last touched. They are service
#: bookkeeping rather than scenario state: no rubric grades a clock, and the
#: fleet's own update routes are built to refuse to move one they did not earn.
#: A write whose only unreachable key is one of these has still delivered every
#: value the agent reads for meaning, which is what separates a misfiled stamp
#: from a lost business column (contentful's ``published_version`` encodes
#: publish state and is NOT one of these).
_CLOCK_STAMPS = frozenset({
    "updatedat", "modifiedat", "changedat", "touchedat", "editedat",
    "lastmodified", "lastupdated", "lastchanged", "lastseenat",
    "datemodified", "dateupdated", "updated", "modified", "mtime",
})


def is_clock_stamp(key: Any) -> bool:
    """True when ``key`` names a last-touched timestamp rather than state."""
    return _fold_key(key) in _CLOCK_STAMPS


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


# --------------------------------------------------------------------------- #
# Not-found envelopes: the vocabulary the fleet actually spells them in.
# --------------------------------------------------------------------------- #

# A getter says "no such row" by RETURNING an envelope rather than raising, and
# every service spells that envelope in its own vendor's dialect. Recognising
# only one spelling is not a partial answer but a wrong one: an unrecognised
# 404 body is handed back as a served row, and a caller that diffs before
# against after then compares a 404 to the same 404, finds them equal, and
# reports a write that landed perfectly as invisible.
#
# Census of every dict a fleet ``get_*``/``list_*`` returns on a not-found path
# (all 50 services): ``error`` in 42 services, ``message`` in 9, ``status`` in
# 4, ``code`` in 3, plus jira's ``errorMessages``/``errors``, plaid's
# ``error_code``/``error_message``, hubspot's ``category`` and bigcommerce's
# ``title``. Three of those shapes carry MORE than two keys, so size is not a
# usable proxy either.
_ERROR_MARKERS = frozenset({
    "error", "errors", "errormessage", "errormessages", "errorcode",
    "errordescription", "fault",
})

# Keys that ride ALONG with a marker inside a vendor envelope. On their own they
# are ordinary response fields -- datadog answers a real metrics query with
# ``status``, openlibrary serves a real book under ``title``/``type`` -- so they
# only count towards a miss when the WHOLE result is built out of them.
_ENVELOPE_COMPANIONS = frozenset({
    "message", "messages", "detail", "details", "status", "statuscode",
    "code", "title", "category", "type", "reason", "success", "ok",
})

def is_miss(result: Any) -> bool:
    """True when a getter's return value is a not-found envelope, not a row.

    A result is a miss when it is ``None`` or empty, or when every key it
    carries belongs to the envelope vocabulary AND at least one of them names
    the failure. Requiring EVERY key to be vocabulary is what keeps a real row
    that happens to hold an ``error`` column from being erased: such a row also
    carries its own business columns, and those are not in the vocabulary. An
    all-companion envelope of at most two keys (``{"status": 404, "message":
    ...}``) also counts, which preserves the old size-bounded behaviour for a
    service that never names the failure at all.
    """
    if result is None:
        return True
    if not isinstance(result, Mapping):
        return False
    if not result:
        return True
    folded = {_fold_key(k) for k in result}
    if not folded <= (_ERROR_MARKERS | _ENVELOPE_COMPANIONS):
        return False
    return bool(folded & _ERROR_MARKERS) or len(folded) <= 2


# --------------------------------------------------------------------------- #
# Getter addressing: the store's key is not always the getter's key.
# --------------------------------------------------------------------------- #

#: Fields a getter is likely to key on when the STORE keys on something else.
#: jira registers ``issues`` with ``primary_key="id"`` and answers
#: ``get_issue(issue_key)`` by matching ``row["key"]``, so addressing that
#: getter with the store pk returns a 404 for a row that reads back perfectly.
_NATURAL_ID_FIELDS = ("key", "slug", "sku", "number", "code", "name",
                      "external_id", "uid", "username", "email", "id")

#: How many addresses one projection may try. Bounded because this runs once
#: per op per probe inside preflight.
_ADDRESS_BUDGET = 6


def _table_rows(module: Any, table: str) -> Tuple[List[Any], Optional[str]]:
    """``(rows, primary_key)`` of a store table, or ``([], None)``."""
    store = getattr(module, "_store", None)
    if store is None:
        return [], None
    try:
        handle = store.table(table)
        return list(handle.rows()), handle.primary_key
    except Exception:  # noqa: BLE001 - no such table is the caller's finding
        return [], None


def getter_addresses(module: Any, table: str, pk: Any) -> List[Any]:
    """Every address a getter for ``table`` might answer to for row ``pk``.

    The store primary key comes first -- it is the right address for most of the
    fleet. The alternatives come from THE ROW ITSELF, not from a per-service
    table: each natural-identifier field the row carries whose value is UNIQUE
    within the table. Uniqueness is the safety property: an alternate address
    can only ever reach the row it was read off, so a fallback can rescue a
    mis-addressed projection but never silently project a sibling.
    """
    addresses: List[Any] = [pk]
    rows, primary = _table_rows(module, table)
    if primary is None:
        return addresses
    row = next((r for r in rows if isinstance(r, Mapping)
                and str(r.get(primary)) == str(pk)), None)
    if row is None:
        return addresses
    seen = {str(pk)}
    for field in _NATURAL_ID_FIELDS:
        if len(addresses) >= _ADDRESS_BUDGET:
            break
        if field == primary or field not in row:
            continue
        value = row[field]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        if str(value) in seen or str(value) == "":
            continue
        twins = sum(1 for r in rows if isinstance(r, Mapping)
                    and str(r.get(field)) == str(value))
        if twins != 1:
            continue  # ambiguous: this address could reach a sibling
        seen.add(str(value))
        addresses.append(value)
    return addresses


def project_in_process(module: Any, table: str, pk: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read one row back through the service's own getter.

    Returns ``(serving_row, mechanism)``. ``mechanism`` is ``"getter:<name>"``
    when the real projection ran and ``"raw-store"`` when the module exposes no
    getter for the table and the caller is seeing the unprojected row.

    A getter that MISSES on the store pk is asked again at the row's own natural
    identifiers before the row is called unreadable (see ``getter_addresses``);
    a getter that RAISES is reported straight away, because a read surface the
    agent cannot call without a 500 is itself the finding.
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
    for address in getter_addresses(module, table, pk):
        try:
            result = fn(address)
        except Exception as exc:  # a getter that raises is itself the finding
            return None, f"getter:{fn.__name__}:{type(exc).__name__}: {exc}"
        if isinstance(result, Mapping) and not is_miss(result):
            return dict(result), f"getter:{fn.__name__}"
    return None, f"getter:{fn.__name__}"


# --------------------------------------------------------------------------- #
# List and query surfaces: one getter is not the whole serving shape.
# --------------------------------------------------------------------------- #

#: Parameter names a list surface uses for its free-form filter. A write whose
#: only reader is a query route (gmail serves ``is_starred`` exclusively through
#: ``?q=is:starred``) is invisible to every projection that does not pass one.
_QUERY_PARAMS = ("query", "q", "search", "filter", "term", "text")

#: Cost ceiling: this runs per op, per probe, inside preflight.
_SURFACE_BUDGET = 4
_QUERY_BUDGET = 6

_TRUTHY = {"true", "yes", "1", "on"}


def list_surfaces(module: Any, table: str) -> List[Tuple[str, Callable[..., Any]]]:
    """The collection-shaped read functions a data module exposes for ``table``.

    Named by the same convention ``find_getter`` uses, widened to the three
    prefixes the fleet spells a collection with. Order is deterministic and the
    count is bounded so a wide module cannot make the gate quadratic.
    """
    singular = _singular(table)
    names: List[str] = []
    for prefix in ("list_", "search_", "query_"):
        for stem in (table, singular):
            candidate = f"{prefix}{stem}"
            if candidate not in names:
                names.append(candidate)
    out: List[Tuple[str, Callable[..., Any]]] = []
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            out.append((name, fn))
        if len(out) >= _SURFACE_BUDGET:
            break
    return out


def query_candidates(payload: Iterable[Tuple[str, Any]]) -> List[str]:
    """Filter strings worth pushing through a list surface for this payload.

    These are GUESSES at a vendor's query grammar, and they are safe to guess
    precisely because the caller uses them differentially: a candidate only
    counts when the surface's answer MOVED across the write, and a query the
    service does not understand answers the same way before and after. So a
    wrong guess costs one call and can never manufacture a verdict.

    Three spellings, in order: ``key:value`` (the common ``field:term`` form),
    ``head:tail`` for a truthy ``head_tail`` flag (gmail's ``is_unread`` is
    queried as ``is:unread``, github's ``is_open`` as ``is:open``), and the bare
    value as free text.
    """
    out: List[str] = []

    def add(text: str) -> None:
        if text and text not in out and len(out) < _QUERY_BUDGET:
            out.append(text)

    for key, value in payload:
        if isinstance(value, (dict, list)):
            continue
        name, text = str(key), str(value)
        add(f"{name}:{text}")
        head, sep, tail = name.partition("_")
        if sep and tail and text.strip().lower() in _TRUTHY:
            add(f"{head}:{tail}")
        add(text)
    return out


def project_list_surfaces(module: Any, table: str,
                          payload: Iterable[Tuple[str, Any]] = ()) -> Dict[str, Any]:
    """Snapshot every list/query surface of ``table``, keyed by how it was read.

    Each surface is read once unfiltered and once per query candidate, so the
    caller can diff the two snapshots and see a write whose only reader is a
    query route. A surface that raises or refuses its arguments is simply absent
    from the snapshot on both sides, which keeps it out of the diff.
    """
    snapshot: Dict[str, Any] = {}
    queries = list(query_candidates(payload))
    for name, fn in list_surfaces(module, table):
        param = _query_param(fn)
        for label, kwargs in [(name, {})] + (
                [(f"{name}?{param}={q}", {param: q}) for q in queries]
                if param else []):
            try:
                snapshot[label] = fn(**kwargs)
            except Exception:  # noqa: BLE001 - an unusable surface is not a diff
                continue
    return snapshot


def _query_param(fn: Callable[..., Any]) -> Optional[str]:
    """The free-form filter parameter ``fn`` accepts, when it has one."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins and C callables have no signature
        return None
    for name in _QUERY_PARAMS:
        param = params.get(name)
        if param is not None and param.kind in (param.POSITIONAL_OR_KEYWORD,
                                                param.KEYWORD_ONLY):
            return name
    return None


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
