"""The fleet-wide invariant: no agent-facing request model may be lax.

script/check_route_contracts.py gates the same class, but only per *route*, and a
route entry can only ever name the top-level body model. 21 of the models the F6
pass tightened are nested sub-models (jira IssueFields, zendesk TicketCreate,
square Money, ...) that receive agent JSON without owning a route of their own.
This module closes that gap statically: it re-derives which models an agent's
JSON can reach and asserts each one forbids unknown keys, so laxness cannot come
back through a shape the route guard cannot see.

"Agent-facing" is computed, not listed. A model is agent-facing when a route
handler binds it (as a parameter annotation, or by handing it to a body-parsing
helper the way trello's _body_fields does), or when it is reachable from such a
model through a field annotation. Anything else -- a loader shape, a dead
declaration -- is out of scope, as is the whole /admin control plane.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENV_DIR = Path(__file__).resolve().parent.parent / "environment"

ROUTE_VERBS = {"get", "post", "patch", "put", "delete", "head", "options",
               "api_route", "route"}

# environment/admin_plane.py is the injection control plane, not agent surface:
# its shallow-merge ops are addressed by the harness, and several injections rely
# on passing through keys the model does not declare. Out of scope by design.
EXEMPT_FILES = {"admin_plane.py"}

# The one true fidelity exception. Salesforce sObject field names are the
# customer org's own metadata, so extra="forbid" would reject every legitimate
# custom field (Custom_Field__c) that a real org can define; the vendor contract
# is an open body and script/route_contracts_allowlist.txt vouches for the same
# two routes. Mirrored here so the two guards cannot drift apart.
EXEMPT_MODELS = {("salesforce-api", "SObjectBody"): "allow"}


def _annotation_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                inner = ast.parse(sub.value, mode="eval")
            except SyntaxError:
                continue
            out |= {n.id for n in ast.walk(inner) if isinstance(n, ast.Name)}
    return out


def _route_paths(fn: ast.AST) -> list[str]:
    paths = []
    for dec in getattr(fn, "decorator_list", []):
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        if dec.func.attr.lower() not in ROUTE_VERBS:
            continue
        arg = dec.args[0] if dec.args else None
        # BASE + "/x" and f"{BASE}/x" are both used in the fleet; an unresolvable
        # path is treated as public, which is the conservative direction here.
        paths.append(arg.value if isinstance(arg, ast.Constant) else "")
    return paths


def _declared_extra(cls: ast.ClassDef) -> str | None:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            if isinstance(stmt.value, ast.Call):
                for kw in stmt.value.keywords:
                    if kw.arg == "extra" and isinstance(kw.value, ast.Constant):
                        return kw.value.value
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Config":
            for inner in stmt.body:
                if isinstance(inner, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "extra" for t in inner.targets
                ):
                    if isinstance(inner.value, ast.Constant):
                        return inner.value.value
    return None


def _base_names(cls: ast.ClassDef) -> list[str]:
    return [b.id if isinstance(b, ast.Name) else b.attr
            for b in cls.bases if isinstance(b, (ast.Name, ast.Attribute))]


def agent_facing_models(path: Path) -> dict[str, str | None]:
    """Map model name -> declared ``extra`` for every model agent JSON reaches."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    def is_model(name: str, seen: tuple[str, ...] = ()) -> bool:
        if name == "BaseModel":
            return True
        if name in seen or name not in classes:
            return False
        return any(is_model(b, seen + (name,)) for b in _base_names(classes[name]))

    models = {n for n in classes if is_model(n)}

    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = _route_paths(node)
        if not paths or all(p.startswith("/admin") for p in paths):
            continue
        args = node.args
        for a in args.args + args.posonlyargs + args.kwonlyargs:
            bound |= _annotation_names(a.annotation) & models
        bound |= {s.id for s in ast.walk(node)
                  if isinstance(s, ast.Name) and s.id in models}

    edges = {
        n: {
            ref
            for stmt in classes[n].body if isinstance(stmt, ast.AnnAssign)
            for ref in _annotation_names(stmt.annotation) & models
        }
        for n in models
    }

    reachable, stack = set(bound), list(bound)
    while stack:
        for nxt in edges[stack.pop()]:
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)

    return {n: _declared_extra(classes[n]) for n in sorted(reachable)}


def _server_files() -> list[Path]:
    return sorted(
        p for p in ENV_DIR.glob("*/*.py")
        if p.name not in EXEMPT_FILES and "__pycache__" not in p.parts
    )


def test_fleet_has_agent_facing_models_to_check():
    """A classifier that silently matches nothing would pass every assertion."""
    total = sum(len(agent_facing_models(p)) for p in _server_files())
    assert total >= 150, total


@pytest.mark.parametrize("server", _server_files(), ids=lambda p: p.parent.name)
def test_agent_facing_request_models_forbid_unknown_keys(server: Path):
    service = server.parent.name
    lax = {}
    for model, extra in agent_facing_models(server).items():
        expected = EXEMPT_MODELS.get((service, model), "forbid")
        if extra != expected:
            lax[model] = extra
    assert not lax, (
        f"{service}/{server.name}: agent-facing request model(s) do not forbid "
        f"unknown keys: {lax}. An unknown or misspelled field on these routes is "
        f"dropped before the handler runs, so the agent is told a write landed "
        f"when part of it did not. Add model_config = ConfigDict(extra=\"forbid\"), "
        f"or -- only with a real-API citation that the vendor accepts an open body "
        f"-- record the model in EXEMPT_MODELS and in "
        f"script/route_contracts_allowlist.txt."
    )
