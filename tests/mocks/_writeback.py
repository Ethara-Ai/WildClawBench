"""Declarative write->read-back specs for the mock fleet.

The persistence contract every mock write route is supposed to honour is
spelled out in ``environment/_mutable_store.py``: a handler takes a request
body, and the fields it accepts must reach ``Table.upsert``/``patch``/
``delete`` so the *next* read serves them. A route that returns a plausible
201 while dropping the body on the floor is the "lost write" defect class --
the one commit 667131a catalogued across seven services.

``test_service_regressions.py`` pins those seven by hand. This module carries
the generic engine so the REST of the fleet can be covered by data rather than
by another 500 lines of near-identical request/assert pairs: a :class:`WriteSpec`
names the create/read/update/delete routes for one resource plus the field
values that must survive each hop, and the three ``check_*`` helpers below
execute it. The specs themselves live in ``_writeback_specs.py``.

Field expectations are dotted paths (``"issue.title"``, ``"records.0.fields.Name"``)
resolved by :func:`dig`, because the fleet's envelopes are not uniform -- Linear
wraps in ``{"issue": ...}``, Asana in ``{"data": ...}``, Stripe returns the row
bare. Expectations are deliberately asserted against the READ-BACK response, not
the write response: a handler that echoes its own input while persisting nothing
passes the latter and fails the former, and that asymmetry is the entire point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_MISSING = object()


@dataclass(frozen=True)
class Step:
    """One HTTP call. ``path`` may contain ``{id}``, filled from the create response."""

    method: str
    path: str
    body: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class WriteSpec:
    """A create -> read -> update -> read -> delete -> read(404) round trip.

    ``created_id`` is a dotted path into the CREATE response naming the value
    that identifies the new row; every later path template gets it substituted
    for ``{id}``. ``created_expect`` / ``updated_expect`` map dotted paths in the
    READ-BACK body to required values. ``updated_expect`` should also restate the
    fields an update must leave ALONE, so a handler that replaces the row instead
    of patching it is caught rather than rewarded.
    """

    api: str
    resource: str
    create: Step
    created_id: str
    read: str
    created_expect: Dict[str, Any]
    update: Optional[Step] = None
    updated_expect: Dict[str, Any] = field(default_factory=dict)
    delete: Optional[str] = None
    create_status: int = 201
    update_status: int = 200
    delete_status: int = 200

    @property
    def test_id(self) -> str:
        return f"{self.api.replace('-api', '')}-{self.resource}"


def dig(payload: Any, path: str) -> Any:
    """Resolve a dotted path, treating all-digit segments as list indices.

    Returns the ``_MISSING`` sentinel rather than raising so callers can report
    "field absent" distinctly from "field present but wrong" -- a dropped field
    and a mangled field are different defects.
    """
    cur = payload
    for part in path.split("."):
        if part.isdigit() and isinstance(cur, list):
            idx = int(part)
            if idx >= len(cur):
                return _MISSING
            cur = cur[idx]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _assert_fields(spec: WriteSpec, stage: str, payload: Any, expect: Dict[str, Any]) -> None:
    misses: List[str] = []
    for path, want in sorted(expect.items()):
        got = dig(payload, path)
        if got is _MISSING:
            misses.append(f"{path}: ABSENT (wanted {want!r})")
        elif got != want:
            misses.append(f"{path}: {got!r} != {want!r}")
    assert not misses, (
        f"{spec.api} {spec.resource}: {stage} read-back lost or mangled "
        f"{len(misses)} field(s):\n  " + "\n  ".join(misses) + f"\npayload: {payload!r}"
    )


def _fill(template: str, ident: Any) -> str:
    return template.replace("{id}", str(ident))


def check_create(client, spec: WriteSpec) -> Any:
    """POST/PUT the create body, then GET it back and assert the fields persisted.

    Returns the new row's identifier so the update/delete checks can reuse it.
    """
    r = client.request(spec.create.method, spec.create.path, json=spec.create.body)
    assert r.status_code == spec.create_status, (
        f"{spec.api} {spec.resource}: create "
        f"{spec.create.method} {spec.create.path} -> {r.status_code} "
        f"(want {spec.create_status}): {r.text}"
    )
    ident = dig(r.json(), spec.created_id)
    assert ident is not _MISSING, (
        f"{spec.api} {spec.resource}: create response has no {spec.created_id!r}: {r.text}"
    )

    path = _fill(spec.read, ident)
    rb = client.get(path)
    assert rb.status_code == 200, (
        f"{spec.api} {spec.resource}: created row not readable at GET {path} "
        f"-> {rb.status_code}: {rb.text}"
    )
    _assert_fields(spec, "create", rb.json(), spec.created_expect)
    return ident


def check_update(client, spec: WriteSpec, ident: Any) -> None:
    """Apply the update, then GET the row back and assert the new values stuck."""
    assert spec.update is not None, f"{spec.test_id} has no update step"
    path = _fill(spec.update.path, ident)
    r = client.request(spec.update.method, path, json=spec.update.body)
    assert r.status_code == spec.update_status, (
        f"{spec.api} {spec.resource}: update {spec.update.method} {path} "
        f"-> {r.status_code} (want {spec.update_status}): {r.text}"
    )

    read_path = _fill(spec.read, ident)
    rb = client.get(read_path)
    assert rb.status_code == 200, (
        f"{spec.api} {spec.resource}: row unreadable after update at GET "
        f"{read_path} -> {rb.status_code}: {rb.text}"
    )
    _assert_fields(spec, "update", rb.json(), spec.updated_expect)


def check_delete(client, spec: WriteSpec, ident: Any) -> None:
    """Delete the row, then assert the read-back 404s instead of serving a ghost."""
    assert spec.delete is not None, f"{spec.test_id} has no delete step"
    path = _fill(spec.delete, ident)
    r = client.delete(path)
    assert r.status_code == spec.delete_status, (
        f"{spec.api} {spec.resource}: delete DELETE {path} -> {r.status_code} "
        f"(want {spec.delete_status}): {r.text}"
    )

    read_path = _fill(spec.read, ident)
    rb = client.get(read_path)
    assert rb.status_code == 404, (
        f"{spec.api} {spec.resource}: deleted row still served at GET {read_path} "
        f"-> {rb.status_code}: {rb.text}"
    )
