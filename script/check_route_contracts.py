#!/usr/bin/env python3
"""Runtime scan for request-contract defects in the WildClawBench mock API fleet.

WHY A SECOND CHECKER
``script/check_lost_writes.py`` is an AST scanner, and it answers exactly one
question: once a value is inside the handler, does it reach a store write? That
leaves the whole INPUT half of the contract unguarded, and three fleet sweeps
found the damage concentrated there --- one live silent-200 lie, 136 routes that
accept and discard unknown keys, and 29 of 30 sampled services whose seed rows
do not survive their own loader. None of those are visible to a write-side AST
walk, and two of them are not visible to ANY static pass: FastAPI decides where
a parameter binds (query vs body) from the resolved annotation, and a coercer
only corrupts a row when it actually runs. So this checker imports each service
for real and interrogates the objects FastAPI and pydantic built.

FINDINGS
  QUERY_BOUND_WRITE  a POST/PUT/PATCH route whose parameters FastAPI bound to
                     the QUERY STRING with no request body at all. The agent
                     sends the JSON body every real client would send, the
                     server binds nothing from it, and the handler applies its
                     defaults. ERROR when every such parameter is optional --
                     the request then 200s while changing nothing, which is the
                     silent lie; WARN when at least one is required, because
                     FastAPI answers 422 and the caller at least finds out.
  OPEN_BODY_SCHEMA   (ERROR) a body model on a mutating route does not set
                     ``extra="forbid"``. pydantic's default is ``ignore``, so a
                     misspelled or renamed key is dropped before the handler
                     ever sees it and the route still answers 200/201.
  UNTYPED_BODY       (WARN) the body is a bare ``dict`` / ``Dict[str, Any]``.
                     Strictly worse than OPEN_BODY_SCHEMA -- there is not even
                     a schema to tighten -- but some vendors really are
                     open-shaped, so this reports rather than gates.
  SEED_COERCION_LOSS (ERROR) a seed field that is non-empty on disk is empty or
                     python-repr-garbled after the service's own loader runs.
                     This is the ``member_ids: ["a", "b"]`` -> ``"['a', 'b']"``
                     corruption that shipped three times: a JSON list handed to
                     ``opt_csv_list``/``strict_csv_list`` is stringified by
                     ``str(v).split(sep)`` and the ids are destroyed in place.
  UPDATE_DROPS_FIELD (WARN) an Update body model omits a field its Create
                     sibling declares. The resource can be born with the field
                     and then never edited through it.
  SERVICE_UNLOADABLE (WARN) ``server.py`` did not import. Reported so a service
                     cannot vanish from the scan silently; ``environment/
                     smoke_eager_load.py`` is the gate that owns this condition.

HOW THE SEED ROUND-TRIP WORKS
``_mutable_store.read_seed_with_ctx`` is the single point where seed bytes
become rows -- every data module funnels through it. We wrap it BEFORE importing
the service, so the ``from _mutable_store import ...`` inside the data module
binds our wrapper, and the rows it captures are the pre-coercion truth. The
module then runs its registrations and its ``eager_load()``, and we diff the
captured raw rows against ``store.table(...).rows()``. Rows are paired on the
table's primary key, falling back to load order when the key is synthetic
(``_pk``) and therefore absent from the raw row.

That probe now lives in ``src/utils/service_probe.py``, because the
pre-trajectory task gate asks the same question of one task's ``mock_data/``
overlay rather than of the pristine fleet. This script keeps the route-shape
detectors and the fleet sweep; the import machinery and detector 3 are shared.

ALLOWLIST
Same grammar as ``check_lost_writes.py``: ``<api>::<METHOD> <path>[::<FINDING>]``
per line, ``#`` comments allowed, omit the finding to silence the whole route.
Seed findings use the pseudo-route ``<api>::SEED <table>.<field>`` so one file
and one parser cover both halves of the contract.

USAGE
    python3 script/check_route_contracts.py environment
    python3 script/check_route_contracts.py environment \\
        --allowlist script/route_contracts_allowlist.txt
    python3 script/check_route_contracts.py environment --json findings.json --quiet
Exit code is 1 when any ERROR-tier finding survives the allowlist.
"""
from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Imported at module scope on purpose, and NOT lazily inside the helpers.
# ``load_service`` evicts every module the service import created; a deferred
# ``from fastapi.routing import APIRoute`` would therefore re-import fastapi
# AFTER the eviction and compare the app's routes against a freshly-built class
# object, so every ``isinstance`` would be False and the whole fleet would scan
# clean. Importing here puts fastapi and pydantic in the pre-import snapshot,
# which pins one identity for the entire run.
from fastapi.routing import APIRoute
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.service_probe import detect_seed_roundtrip, load_service  # noqa: E402

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})

#: Nested body models are part of the same request schema, so a lax one is the
#: same defect at one more level of indirection. Bounded because a self-
#: referential model (``Block.children: List[Block]``) would otherwise loop.
MAX_MODEL_DEPTH = 4


def _field_required(field: Any) -> bool:
    """``True`` when a FastAPI ``ModelField`` has no default.

    FastAPI moved this from ``ModelField.required`` (pydantic v1 era) onto the
    wrapped ``FieldInfo`` for v2, and both spellings are still in the wild, so
    ask for whichever the installed version actually has.
    """
    info = getattr(field, "field_info", None)
    if info is not None and hasattr(info, "is_required"):
        return bool(info.is_required())
    return bool(getattr(field, "required", False))


def _field_annotation(field: Any) -> Any:
    info = getattr(field, "field_info", None)
    if info is not None and getattr(info, "annotation", None) is not None:
        return info.annotation
    return getattr(field, "type_", None)


def _model_fields(model: type) -> Dict[str, Any]:
    return getattr(model, "model_fields", None) or getattr(model, "__fields__", {}) or {}


def _extra_policy(model: type) -> Optional[str]:
    """The model's ``extra`` setting, or ``None`` when it never set one.

    pydantic v2 folds a legacy ``class Config: extra = "allow"`` into
    ``model_config``, so salesforce's deprecated spelling is read here too.
    """
    config = getattr(model, "model_config", None)
    if isinstance(config, dict):
        return config.get("extra")
    return getattr(getattr(model, "Config", None), "extra", None)


def _is_body_model(obj: Any) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


def _walk_models(annotation: Any, depth: int = 0,
                 seen: Optional[Set[type]] = None) -> List[type]:
    """Every pydantic model reachable from a type annotation, outermost first."""
    seen = set() if seen is None else seen
    if depth > MAX_MODEL_DEPTH or annotation is None:
        return []
    if _is_body_model(annotation):
        if annotation in seen:
            return []
        seen.add(annotation)
        out = [annotation]
        for field in _model_fields(annotation).values():
            out.extend(_walk_models(getattr(field, "annotation", None), depth + 1, seen))
        return out
    out = []
    for arg in typing.get_args(annotation):
        out.extend(_walk_models(arg, depth + 1, seen))
    return out


def _is_untyped_mapping(annotation: Any) -> bool:
    """``dict`` / ``Dict[str, Any]`` / ``Any`` -- a body with no schema at all."""
    if annotation is Any or annotation is dict:
        return True
    origin = typing.get_origin(annotation)
    if origin is dict:
        args = typing.get_args(annotation)
        return not args or args[-1] is Any
    if origin is typing.Union:
        return any(_is_untyped_mapping(a) for a in typing.get_args(annotation)
                   if a is not type(None))
    return False


def iter_api_routes(app: Any) -> List[Tuple[str, str, Any]]:
    """``(METHOD, path, route)`` for every concrete route, mounts excluded."""
    out: List[Tuple[str, str, Any]] = []
    for route in getattr(app, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, route.path, route))
    return sorted(out, key=lambda r: (r[1], r[0]))


def detect_query_bound_write(method: str, route: Any) -> Optional[Tuple[str, str]]:
    """Detector 1 -- a mutating route whose payload binds to the query string.

    ``route.body_field is None`` is authoritative: FastAPI has already resolved
    every annotation and decided none of them is a body. A handler that takes a
    raw ``Request`` is exempt because it can read the body itself, which is
    invisible to both this checker and the OpenAPI schema.
    """
    if method not in MUTATING_METHODS or route.body_field is not None:
        return None
    dependant = route.dependant
    if getattr(dependant, "request_param_name", None):
        return None
    if getattr(dependant, "http_connection_param_name", None):
        return None
    params = list(dependant.query_params)
    if not params:
        return None
    names = ", ".join(sorted(p.name for p in params))
    if any(_field_required(p) for p in params):
        return ("WARN",
                f"binds {len(params)} parameter(s) to the query string ({names}) and "
                "declares no request body; a client that sends JSON gets 422 because "
                "the required parameters are missing from the query")
    return ("ERROR",
            f"binds {len(params)} OPTIONAL parameter(s) to the query string ({names}) "
            "and declares no request body; a client that sends JSON gets 200 and "
            "nothing changes -- the body is never read")


def detect_body_schema(method: str, route: Any) -> List[Tuple[str, str, str]]:
    """Detector 2 -- ``(finding, tier, detail)`` for every lax body on a route."""
    if method not in MUTATING_METHODS:
        return []
    body_params = list(route.dependant.body_params)
    if not body_params:
        return []

    untyped = sorted({p.name for p in body_params
                      if _is_untyped_mapping(_field_annotation(p))})
    open_models: List[str] = []
    for param in body_params:
        for model in _walk_models(_field_annotation(param)):
            if _extra_policy(model) != "forbid":
                open_models.append(model.__name__)

    out: List[Tuple[str, str, str]] = []
    if open_models:
        shown = sorted(set(open_models))
        out.append((
            "OPEN_BODY_SCHEMA", "ERROR",
            f"body model(s) {', '.join(shown[:6])} do not set extra=\"forbid\"; "
            "pydantic drops unknown keys before the handler runs, so a misspelled "
            "or renamed field is accepted and silently discarded"))
    if untyped:
        out.append((
            "UNTYPED_BODY", "WARN",
            f"body parameter(s) {', '.join(untyped)} are a bare mapping; there is no "
            "schema to reject an unknown key against, so every key is accepted and "
            "only the handler decides what survives"))
    return out


def _route_body_models(routes: List[Tuple[str, str, Any]]) -> Dict[str, List[Tuple[str, str]]]:
    """``model name -> [(METHOD, path), ...]`` over the mutating routes."""
    index: Dict[str, List[Tuple[str, str]]] = {}
    for method, path, route in routes:
        if method not in MUTATING_METHODS:
            continue
        for param in route.dependant.body_params:
            for model in _walk_models(_field_annotation(param)):
                index.setdefault(model.__name__, []).append((method, path))
    return index


CREATE_TOKENS = ("create", "new")
UPDATE_TOKENS = ("update", "patch", "edit")


def _resource_stem(name: str, tokens: Tuple[str, ...]) -> Optional[str]:
    """``ItemCreateBody`` + create-tokens -> ``itembody``; pairs with Update."""
    low = name.lower()
    for token in tokens:
        idx = low.find(token)
        if idx >= 0:
            return low[:idx] + low[idx + len(token):]
    return None


def detect_update_asymmetry(routes: List[Tuple[str, str, Any]]) -> List[Dict[str, Any]]:
    """Detector 4 -- an Update model missing fields its Create sibling declares.

    Only models that a mutating route actually binds are considered, so a
    retired or read-only model cannot raise a finding nobody can act on.
    """
    bound = _route_body_models(routes)
    models: Dict[str, type] = {}
    for _, _, route in routes:
        for param in route.dependant.body_params:
            for model in _walk_models(_field_annotation(param)):
                models[model.__name__] = model

    creates: Dict[str, type] = {}
    for name, model in models.items():
        stem = _resource_stem(name, CREATE_TOKENS)
        if stem and _resource_stem(name, UPDATE_TOKENS) is None:
            creates.setdefault(stem, model)

    out: List[Dict[str, Any]] = []
    for name, model in sorted(models.items()):
        stem = _resource_stem(name, UPDATE_TOKENS)
        sibling = creates.get(stem) if stem else None
        if sibling is None or sibling is model:
            continue
        missing = sorted(set(_model_fields(sibling)) - set(_model_fields(model)))
        if not missing:
            continue
        for method, path in sorted(set(bound.get(name, []))):
            out.append({
                "method": method, "path": path, "finding": "UPDATE_DROPS_FIELD",
                "tier": "WARN", "subject": name,
                "detail": f"{name} omits {', '.join(missing)}, declared by its create "
                          f"sibling {sibling.__name__}; the resource can be born with "
                          "those fields but never edited through this route",
            })
    return out


def check_service(api_dir: Path) -> List[Dict[str, Any]]:
    """Return every finding for one ``environment/<svc>-api/`` directory."""
    probe = load_service(api_dir)
    findings: List[Dict[str, Any]] = []

    def add(method, path, kind, tier, detail, subject=""):
        findings.append({
            "api": probe.api, "method": method, "path": path, "finding": kind,
            "tier": tier, "subject": subject, "detail": detail,
        })

    if probe.app is None:
        add("IMPORT", "server.py", "SERVICE_UNLOADABLE", "WARN",
            f"server.py did not import ({probe.error}); every route-contract check "
            "was skipped for this service")
        return findings

    routes = iter_api_routes(probe.app)
    for method, path, route in routes:
        verdict = detect_query_bound_write(method, route)
        if verdict is not None:
            add(method, path, "QUERY_BOUND_WRITE", verdict[0], verdict[1])
        for kind, tier, detail in detect_body_schema(method, route):
            add(method, path, kind, tier, detail)

    for item in detect_update_asymmetry(routes) + detect_seed_roundtrip(probe):
        add(item["method"], item["path"], item["finding"], item["tier"],
            item["detail"], item.get("subject", ""))
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


def collect(roots: List[str]) -> Tuple[List[Dict[str, Any]], int]:
    findings: List[Dict[str, Any]] = []
    scanned = 0
    for root in roots:
        rp = Path(root)
        if not rp.exists():
            print(f"!! skip (missing): {root}", file=sys.stderr)
            continue
        dirs = [rp] if rp.name.endswith("-api") else sorted(rp.glob("*-api"))
        for api_dir in dirs:
            if api_dir.is_dir() and (api_dir / "server.py").exists():
                scanned += 1
                findings.extend(check_service(api_dir))
    return findings, scanned


def partition(findings: List[Dict[str, Any]],
              allow: Set[str]) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    suppressed = 0
    for f in findings:
        route, exact = _keys(f)
        if route in allow or exact in allow:
            suppressed += 1
            continue
        kept.append(f)
    return kept, suppressed


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="environment/ dirs (or a single *-api dir)")
    ap.add_argument("--allowlist", type=Path, help="File of verified-intentional findings")
    ap.add_argument("--json", type=Path, help="Write the full findings array here")
    ap.add_argument("--quiet", action="store_true", help="Print ERROR-tier findings only")
    args = ap.parse_args(argv)

    findings, scanned = collect(args.roots)
    kept, suppressed = partition(findings, load_allowlist(args.allowlist))

    if args.json:
        args.json.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")

    counts: Dict[str, int] = {}
    errors = 0
    for f in sorted(kept, key=lambda x: (x["tier"] != "ERROR", x["api"], x["path"])):
        counts[f["finding"]] = counts.get(f["finding"], 0) + 1
        if f["tier"] == "ERROR":
            errors += 1
        elif args.quiet:
            continue
        print(f"[{f['tier']:5s} {f['finding']:18s}] {f['api']}  "
              f"{f['method']} {f['path']}")
        print(f"    {f['detail']}")

    breakdown = ", ".join(f"{counts[k]} {k}" for k in sorted(counts)) or "none"
    print("-" * 70)
    print(f"{scanned} service(s) scanned: {breakdown}; "
          f"{errors} ERROR-tier, {suppressed} allowlisted")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
