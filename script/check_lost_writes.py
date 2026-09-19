#!/usr/bin/env python3
"""Static scan for "lost write" defects in the WildClawBench mock API fleet.

THE DEFECT CLASS
Every mock write route is supposed to honour one persistence contract, defined
in ``environment/_mutable_store.py``: whatever the handler accepts in its request
body must reach a store write --- ``Table.upsert/patch/delete/update_where/
delete_where`` or ``Document.set/merge`` --- so the NEXT read serves it. A route
that returns a plausible 201 while persisting nothing is invisible to the agent
until a later turn reads the resource back and finds the old value. Commit
667131a catalogued seven of these across monday/gmail/woocommerce/figma/shippo/
box/notion; task 16.22 was contaminated by exactly this mock tax. This checker is
the systemic guard against recurrence.

Note the trap that makes eyeballing unreliable: ``Table.get()`` and ``rows()``
return DEEP COPIES. A handler that fetches a row, mutates it, and returns it
looks correct in the write response and in any test that only reads that
response --- but nothing was persisted. That is finding class MUTATES_COPY.

FINDINGS
  NO_STORE_WRITE   (ERROR) a mutating route reaches no store write on any call
                   path --- the body is accepted and dropped. The core defect.
  MUTATES_COPY     (WARN) the handler's call graph mutates a subscript of a
                   value obtained from a store read and never writes it back.
  UNMIGRATED_STORE (WARN) the route persists into a module-level list instead of
                   the shared store. The agent-visible write survives, so this is
                   NOT a lost write -- but the row has no admin/drift surface, so
                   injection cannot reach it.
  DROPPED_FIELD    (WARN) a field declared on the route's pydantic body model is
                   never read anywhere in the handler's call graph. This is the
                   monday ``item_name`` defect: pydantic accepts the key, the
                   handler never forwards it.
  UNKEYED_UPSERT   (WARN) a dict literal is upserted into a table registered
                   with a synthetic primary key (``_pk``) without setting that
                   key --- an unconditional StoreError at runtime. This is the
                   shippo ``tracking`` defect 667131a fixed.

Resolution is inter-procedural on purpose: services wrap persistence in local
helpers (``_store_insert``, ``_store_patch``), so a handler almost never calls
``.upsert(`` itself. Call graphs are resolved across every module in the service
directory and walked to a bounded depth; a call this checker cannot resolve is
treated as POSSIBLY writing, which is what keeps the ERROR tier quiet enough to
gate on. The WARN tier carries the zero-false-negative bias instead.

ALLOWLIST
Read-shaped POSTs are legitimate and common (``POST /v1/search``, Slack's
``auth.test``, batch getters). Silence them with ``--allowlist``: one
``<api>::<METHOD> <path>[::<FINDING>]`` per line, ``#`` comments allowed. Omit
the finding to allowlist every finding on that route.

USAGE
    python3 script/check_lost_writes.py environment
    python3 script/check_lost_writes.py environment --allowlist script/lost_writes_allowlist.txt
    python3 script/check_lost_writes.py environment --json findings.json --quiet
Exit code is 1 when any ERROR-tier finding survives the allowlist.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

WRITE_METHODS = ("post", "put", "patch", "delete")

STORE_WRITE_ATTRS = frozenset({
    "upsert", "patch", "delete", "update_where", "delete_where", "set", "merge",
})
# ``get`` is deliberately NOT here: ``payload.get("x")`` on a plain dict is
# everywhere in this fleet, and counting it as a store read made every
# row-shaping helper look like it was mutating a copy. Store reads through
# ``get`` are recognised structurally instead, via a ``.table(...)`` receiver.
STORE_READ_ATTRS = frozenset({"rows", "find", "find_one"})

#: Mutating a module-level list IS persistence -- just the pre-store kind, with
#: no admin/drift surface. Distinguishing it keeps UNMIGRATED_STORE out of the
#: NO_STORE_WRITE bucket, which would otherwise be badly wrong: those routes do
#: survive a read-back.
LIST_MUTATORS = frozenset({"append", "extend", "insert", "remove", "pop", "clear"})

# Depth is generous relative to the fleet's shape (route -> data-module fn ->
# _store_insert) but bounded so a recursive helper cannot hang the scan.
MAX_DEPTH = 6

def _method_and_path(dec: ast.expr) -> Optional[Tuple[str, str]]:
    """Return ``(METHOD, path)`` for an ``@app.post("/x")``-style decorator."""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    attr = dec.func.attr.lower()
    if attr not in WRITE_METHODS or not isinstance(dec.func.value, ast.Name):
        return None
    if not dec.args or not isinstance(dec.args[0], ast.Constant):
        return None
    path = dec.args[0].value
    return (attr.upper(), path) if isinstance(path, str) else None


class _ModuleIndex:
    """Function defs, pydantic body models, and store table keys for one service.

    Functions are keyed ``"<module_stem>.<name>"`` because the fleet routinely
    gives a route handler and the data-module function it delegates to the SAME
    name (``slack_data.chat_post_message`` behind ``def chat_post_message``). A
    bare-name index would resolve that call to the handler itself, the walk would
    stop on the already-seen marker, and every such route would be misreported as
    persisting nothing.
    """

    def __init__(self) -> None:
        self.functions: Dict[str, ast.FunctionDef] = {}
        self.by_name: Dict[str, List[str]] = {}
        self.route_keys: Set[str] = set()
        self.models: Dict[str, List[str]] = {}
        self.table_keys: Dict[str, str] = {}
        self.module_state: Set[str] = set()
        self.routes: List[Tuple[str, str, str, Path]] = []

    def add_module(self, path: Path, tree: ast.Module) -> None:
        mod = path.stem
        for node in tree.body:
            # Module-level `_contacts = _coerce(_load(...))` / `_playback_state
            # = {...}`: the pre-store persistence mechanism. Its presence is what
            # separates "writes into a legacy collection" from "writes nowhere".
            # ALL_CAPS names are excluded as constants; everything else at module
            # scope is a candidate, since the legacy tables are just as often
            # built by a loader call as by a literal.
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and not tgt.id.isupper():
                        self.module_state.add(tgt.id)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{mod}.{node.name}"
                fn = ast.FunctionDef(
                    name=node.name, args=node.args, body=node.body,
                    decorator_list=node.decorator_list, returns=None, type_comment=None,
                )
                self.functions[key] = fn
                self.by_name.setdefault(node.name, []).append(key)
                for dec in node.decorator_list:
                    mp = _method_and_path(dec)
                    if mp:
                        self.route_keys.add(key)
                        self.routes.append((mp[0], mp[1], key, path))
            elif isinstance(node, ast.ClassDef):
                fields = [
                    st.target.id for st in node.body
                    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                ]
                if fields:
                    self.models[node.name] = fields
            elif isinstance(node, ast.Call):
                self._maybe_register(node)

    def _maybe_register(self, node: ast.Call) -> None:
        """Capture ``_store.register("tracking", primary_key="_pk", ...)``."""
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "register":
            return
        if not node.args or not isinstance(node.args[0], ast.Constant):
            return
        table = node.args[0].value
        for kw in node.keywords:
            if kw.arg == "primary_key" and isinstance(kw.value, ast.Constant):
                self.table_keys[table] = kw.value.value
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            self.table_keys.setdefault(table, node.args[1].value)

    def resolve(self, callee: str) -> Optional[str]:
        """Qualified name wins; otherwise prefer a def that is NOT a route handler."""
        if callee in self.functions:
            return callee
        candidates = self.by_name.get(callee.rsplit(".", 1)[-1], [])
        for key in candidates:
            if key not in self.route_keys:
                return key
        return candidates[0] if candidates else None


def _base_name(node: ast.expr) -> str:
    """Left-most identifier of an expression (``a.b[c].d`` -> ``a``)."""
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, (ast.Subscript, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        else:
            break
    return node.id if isinstance(node, ast.Name) else "?"


def _is_table_receiver(node: ast.expr) -> bool:
    """True for ``_store.table("x")`` / ``_t`` -- the receivers whose ``.get`` is
    a store read rather than ``dict.get``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("table", "document")
    return isinstance(node, ast.Name) and node.id.startswith("_t")


class _BodyScan(ast.NodeVisitor):
    """Collect the store/attribute evidence inside a single function body."""

    #: ``body.model_dump()`` hands every declared field to the callee at once, so
    #: no individual ``body.<field>`` access will ever appear. Treat the whole
    #: model as forwarded rather than reporting each field as dropped.
    FORWARD_ALL = frozenset({"model_dump", "dict", "model_dump_json", "json"})

    def __init__(self) -> None:
        self.write_attrs: Set[str] = set()
        self.read_attrs: Set[str] = set()
        self.called: Set[str] = set()
        self.names_read: Set[str] = set()
        self.names_used: Set[str] = set()
        self.forwarded: Set[str] = set()
        self.subscript_stores: Set[str] = set()
        self.list_mutations: Set[str] = set()
        self.aliases: Dict[str, Set[str]] = {}
        self.upserts: List[ast.Call] = []
        self.table_writes: List[Tuple[str, ast.Dict, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in STORE_WRITE_ATTRS:
                self.write_attrs.add(func.attr)
                if func.attr == "upsert":
                    self.upserts.append(node)
            if func.attr in STORE_READ_ATTRS or (
                func.attr == "get" and _is_table_receiver(func.value)
            ):
                self.read_attrs.add(func.attr)
            if func.attr in LIST_MUTATORS:
                self.list_mutations.add(_base_name(func.value))
            if isinstance(func.value, ast.Name):
                if func.attr in self.FORWARD_ALL:
                    self.forwarded.add(func.value.id)
                self.called.add(f"{func.value.id}.{func.attr}")
            else:
                self.called.add(func.attr)
            self._record_table_write(node, func.attr)
        elif isinstance(func, ast.Name):
            self.called.add(func.id)
            self._record_table_write(node, func.id)
        self.generic_visit(node)

    def _record_table_write(self, node: ast.Call, callee: str) -> None:
        """Capture ``_store_insert("tracking", {...})``-shaped persistence calls.

        The fleet almost never upserts inline; it funnels through a per-service
        helper, which puts the table NAME and the row LITERAL in different
        functions. Recording the pair here is what lets UNKEYED_UPSERT see across
        that hop -- without it the check only ever fires on the inline form, which
        is the form the fleet does not use."""
        if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant):
            table = node.args[0].value
            if isinstance(table, str) and isinstance(node.args[1], ast.Dict):
                self.table_writes.append((table, node.args[1], callee))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            self.names_read.add(f"{node.value.id}.{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names_used.add(node.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        for tgt in node.targets:
            if isinstance(tgt, ast.Subscript):
                self.subscript_stores.add(_base_name(tgt.value))
            elif isinstance(tgt, ast.Name) and isinstance(node.value, ast.Call):
                # `c = _find(_contacts, id)` hands back a REFERENCE into a
                # module-level collection, so mutating `c` does persist. Record
                # the argument names so the caller can tell that apart from
                # `out = []`, a local accumulator whose mutation persists nothing.
                seeds = {a.id for a in node.value.args if isinstance(a, ast.Name)}
                callee = node.value.func
                if isinstance(callee, ast.Name):
                    seeds.add(callee.id)
                elif isinstance(callee, ast.Attribute):
                    # `_carts.get(cart_id)`: the RECEIVER is the module-level
                    # collection, so seed on it as well as the method name.
                    seeds.add(callee.attr)
                    seeds.add(_base_name(callee.value))
                self.aliases[tgt.id] = seeds
        self.generic_visit(node)


def _scan(fn: ast.FunctionDef) -> _BodyScan:
    scan = _BodyScan()
    for stmt in fn.body:
        scan.visit(stmt)
    return scan


class _Reach:
    """Union of the evidence over everything a route handler can reach."""

    __slots__ = ("writes", "reads", "names", "forwarded", "lists", "subscripts",
                 "aliases", "upserts", "table_writes", "entered_data")

    def __init__(self) -> None:
        self.writes: Set[str] = set()
        self.reads: Set[str] = set()
        self.names: Set[str] = set()
        self.forwarded: Set[str] = set()
        self.lists: Set[str] = set()
        self.subscripts: Set[str] = set()
        self.aliases: Dict[str, Set[str]] = {}
        self.upserts: List[ast.Call] = []
        self.table_writes: List[Tuple[str, ast.Dict, str]] = []
        self.entered_data = False


def _walk_callgraph(start_key: str, index: _ModuleIndex) -> _Reach:
    """Fold every reachable function's evidence into one :class:`_Reach`.

    ``entered_data`` records whether the walk ever resolved into a ``*_data``
    module. It is the tier discriminator: a handler we followed all the way into
    the data layer and still found no store write is a real defect (ERROR), while
    one that never got there may simply be delegating somewhere this checker
    cannot see, and is only worth a WARN.
    """
    out = _Reach()
    seen: Set[str] = {start_key}
    frontier = [(start_key, 0)]
    while frontier:
        key, depth = frontier.pop()
        fn = index.functions.get(key)
        if fn is None:
            continue
        scan = _scan(fn)
        out.writes |= scan.write_attrs
        out.reads |= scan.read_attrs
        out.names |= scan.names_read
        out.forwarded |= scan.forwarded
        out.lists |= scan.list_mutations
        out.subscripts |= scan.subscript_stores
        out.aliases.update(scan.aliases)
        out.upserts.extend(scan.upserts)
        out.table_writes.extend(scan.table_writes)
        if depth >= MAX_DEPTH:
            continue
        for callee in scan.called:
            target = index.resolve(callee)
            if target is None or target in seen:
                continue
            seen.add(target)
            if target.split(".", 1)[0].endswith("_data"):
                out.entered_data = True
            frontier.append((target, depth + 1))
    return out


def _mutates_copy(start_key: str, index: _ModuleIndex) -> bool:
    """True when a reachable function mutates a store read without writing back.

    Deliberately narrow: it fires only when the SAME function performs a store
    read, stores into a subscript, and reaches no store write itself. Row-shaping
    helpers that build fresh dicts never touch the store, so they cannot trip it.
    """
    seen: Set[str] = {start_key}
    frontier = [(start_key, 0)]
    while frontier:
        key, depth = frontier.pop()
        fn = index.functions.get(key)
        if fn is None:
            continue
        scan = _scan(fn)
        if scan.read_attrs and scan.subscript_stores and not scan.write_attrs:
            return True
        if depth >= MAX_DEPTH:
            continue
        for callee in scan.called:
            target = index.resolve(callee)
            if target is not None and target not in seen:
                seen.add(target)
                frontier.append((target, depth + 1))
    return False


def _body_params(fn: ast.FunctionDef, index: _ModuleIndex) -> List[Tuple[str, List[str]]]:
    """``(param_name, declared_fields)`` for each pydantic body model parameter."""
    out: List[Tuple[str, List[str]]] = []
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs):
        ann = arg.annotation
        if isinstance(ann, ast.Name) and ann.id in index.models:
            out.append((arg.arg, index.models[ann.id]))
    return out


def _missing_key(row: ast.Dict, key: str) -> bool:
    """True when a dict literal neither sets ``key`` nor splats another mapping."""
    keys = {k.value for k in row.keys if isinstance(k, ast.Constant)}
    return key not in keys and not any(k is None for k in row.keys)


def _unkeyed_upserts(reach: "_Reach", index: _ModuleIndex) -> List[str]:
    """Rows written into a synthetic-key table without setting that key.

    This is the shippo ``tracking`` defect from 667131a: the table is registered
    ``primary_key="_pk"``, the loader synthesizes ``_pk`` for every seed row, and
    a later create path writes a fresh literal that has none --- an unconditional
    StoreError the first time the route is exercised. Only synthetic keys (a
    leading underscore) are checked, because a natural key like ``object_id`` is
    routinely filled in by the helper rather than the caller.
    """
    bad: List[str] = []
    for table, row, callee in reach.table_writes:
        key = index.table_keys.get(table)
        if not key or not key.startswith("_") or not _missing_key(row, key):
            continue
        target = index.resolve(callee)
        fn = index.functions.get(target) if target else None
        if fn is not None and "upsert" not in _scan(fn).write_attrs:
            continue
        bad.append(f"{table!r} (primary_key={key!r})")

    for call in reach.upserts:
        parent = call.func
        if not (isinstance(parent, ast.Attribute) and isinstance(parent.value, ast.Call)):
            continue
        inner = parent.value
        if not (inner.args and isinstance(inner.args[0], ast.Constant)):
            continue
        table = inner.args[0].value
        key = index.table_keys.get(table)
        if not key or not key.startswith("_") or not call.args:
            continue
        if isinstance(call.args[0], ast.Dict) and _missing_key(call.args[0], key):
            bad.append(f"{table!r} (primary_key={key!r})")
    return sorted(set(bad))


def _aliases_module_state(seeds: Set[str], index: _ModuleIndex) -> bool:
    """True when ``X = f(...)`` handed back a reference into module-level state.

    Either a module-state name was passed straight in (``_find(_contacts, id)``),
    or the callee itself reads module-level state and therefore can be returning
    a row out of it (``_get_cart(cart_id)`` closing over ``_carts``). Mutating the
    result of either persists; mutating a fresh local accumulator does not, and
    that is the distinction this predicate exists to draw.
    """
    if seeds & index.module_state:
        return True
    for seed in seeds:
        key = index.resolve(seed)
        fn = index.functions.get(key) if key else None
        if fn is not None and _scan(fn).names_used & index.module_state:
            return True
    return False


def check_service(api_dir: Path) -> List[Dict[str, Any]]:
    """Return every finding for one ``environment/<svc>-api/`` directory."""
    index = _ModuleIndex()
    for py in sorted(api_dir.glob("*.py")):
        try:
            index.add_module(py, ast.parse(py.read_text(encoding="utf-8")))
        except (OSError, SyntaxError) as exc:
            print(f"!! unparseable: {py} ({exc})", file=sys.stderr)

    findings: List[Dict[str, Any]] = []

    def add(method, path, key, kind, tier, detail, src):
        findings.append({
            "api": api_dir.name, "method": method, "path": path, "finding": kind,
            "tier": tier, "handler": key,
            "file": str(src.relative_to(api_dir.parent)), "detail": detail,
        })

    for method, path, key, src in sorted(index.routes, key=lambda r: (r[1], r[0])):
        fn = index.functions[key]
        reach = _walk_callgraph(key, index)

        mutated = reach.lists | reach.subscripts
        globals_hit = sorted(mutated & index.module_state)
        aliased = sorted(
            name for name in mutated
            if _aliases_module_state(reach.aliases.get(name, set()), index)
        )
        if not reach.writes and (globals_hit or aliased):
            # Naming the module-level collection when we can see it is the whole
            # value of this finding; when only locals are mutated the row still
            # reaches the caller by reference off a module list (hubspot's
            # `c = _find(_contacts, id); c[...] = ...`), so the tier is the same
            # but the operator has to open the file to confirm which.
            where = (f"module-level state ({', '.join(globals_hit[:4])})" if globals_hit
                     else f"a row fetched out of module-level state "
                          f"({', '.join(aliased[:4])})")
            add(method, path, key, "UNMIGRATED_STORE", "WARN",
                f"mutates {where} instead of the shared store: the write survives a "
                "read-back, but the row has no admin/drift surface, so injection "
                "cannot reach it", src)
        elif not reach.writes:
            if reach.entered_data:
                add(method, path, key, "NO_STORE_WRITE", "ERROR",
                    "handler reaches the data module but no store write and no "
                    "collection mutation on any call path; the request body "
                    "cannot be persisted", src)
            else:
                add(method, path, key, "NO_STORE_WRITE", "WARN",
                    "no store write reachable and the call graph never entered a "
                    "*_data module -- verify by hand", src)
        elif _mutates_copy(key, index):
            add(method, path, key, "MUTATES_COPY", "WARN",
                "a reachable function mutates a subscript of a store read "
                "(Table.get/rows return deep copies) without writing it back", src)

        for param, fields in _body_params(fn, index):
            if param in reach.forwarded:
                continue
            for field in fields:
                if f"{param}.{field}" not in reach.names:
                    add(method, path, key, "DROPPED_FIELD", "WARN",
                        f"body field {field!r} is declared on the request model but "
                        f"never read as {param}.{field} anywhere in the call graph", src)

        for bad in _unkeyed_upserts(reach, index):
            add(method, path, key, "UNKEYED_UPSERT", "WARN",
                f"dict literal upserted into {bad} without that key; "
                "raises StoreError at runtime", src)

    return findings


def load_allowlist(path: Optional[Path]) -> Set[str]:
    if path is None:
        return set()
    entries: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def _keys(f: Dict[str, Any]) -> Tuple[str, str]:
    route = f"{f['api']}::{f['method']} {f['path']}"
    return route, f"{route}::{f['finding']}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="environment/ dirs (or a single *-api dir)")
    ap.add_argument("--allowlist", type=Path, help="File of verified-intentional routes")
    ap.add_argument("--json", type=Path, help="Write the full findings array here")
    ap.add_argument("--quiet", action="store_true", help="Print ERROR-tier findings only")
    args = ap.parse_args(argv)

    allow = load_allowlist(args.allowlist)
    findings: List[Dict[str, Any]] = []
    scanned = 0
    for root in args.roots:
        rp = Path(root)
        if not rp.exists():
            print(f"!! skip (missing): {root}", file=sys.stderr)
            continue
        dirs = [rp] if rp.name.endswith("-api") else sorted(rp.glob("*-api"))
        for api_dir in dirs:
            if api_dir.is_dir():
                scanned += 1
                findings.extend(check_service(api_dir))

    kept: List[Dict[str, Any]] = []
    suppressed = 0
    for f in findings:
        route, exact = _keys(f)
        if route in allow or exact in allow:
            suppressed += 1
            continue
        kept.append(f)

    if args.json:
        args.json.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")

    # Only the ERROR tier gates the exit code, and in practice only
    # NO_STORE_WRITE reaches it. The WARN findings carry the zero-false-negative
    # bias deliberately -- they are tuned to over-report rather than stay silent,
    # which makes them worth reading and wrong to block a build on.
    counts: Dict[str, int] = {}
    errors = 0
    for f in sorted(kept, key=lambda x: (x["tier"] != "ERROR", x["api"], x["path"])):
        counts[f["finding"]] = counts.get(f["finding"], 0) + 1
        if f["tier"] == "ERROR":
            errors += 1
        elif args.quiet:
            continue
        print(f"[{f['tier']:5s} {f['finding']:14s}] {f['api']}  "
              f"{f['method']} {f['path']}  ({f['handler']})")
        print(f"    {f['detail']}")

    breakdown = ", ".join(f"{counts[k]} {k}" for k in sorted(counts)) or "none"
    print("-" * 70)
    print(f"{scanned} service(s) scanned: {breakdown}; "
          f"{errors} ERROR-tier, {suppressed} allowlisted")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
