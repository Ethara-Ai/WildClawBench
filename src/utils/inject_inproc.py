"""Replay injection ops against an in-process store instead of a live container.

``InjectApplier`` already owns the only faithful account of what an op does:
which store table a friendly name resolves to, how a nested ``fields`` bag is
re-wrapped before it is sent, which namespace an envelope stamp belongs in, and
how a write is judged against the column vocabulary a getter can actually name.
Restating any of that here would mean two accounts of the same semantics, and
the one nobody runs would drift.

So this does not restate it. ``InProcessApplier`` IS ``InjectApplier`` with its
four ``/admin/*`` HTTP calls answered out of a store the caller already holds —
the same store the mock serves from, loaded from the task's own overlay. Every
op shape the runtime supports is therefore replayed by the runtime's own code,
before a container exists.

What the transport cannot model it says so about rather than guessing:
``POST /admin/inject/raw`` carries an operation list the admin plane
interprets, and an op using it is reported ``NEEDS-RUNTIME`` instead of being
failed on a shim's behalf.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.utils.inject_director import InjectApplier, InjectStage
from src.utils.serving_shape import (is_clock_stamp, partition_expected,
                                     project_in_process, project_list_surfaces,
                                     value_visible)

__all__ = [
    "LANDS_AND_SERVES",
    "LANDS_BUT_INVISIBLE",
    "NEEDS_RUNTIME",
    "OpVerdict",
    "SERVES_WITH_ORPHAN",
    "TABLE_MISSING",
    "WOULD_ERROR",
    "InProcessApplier",
    "ordered_api_ops",
    "replay_service_ops",
]

# The six things a replayed op can turn out to be. Only the first is a clean
# pass, and two are survivable. NEEDS-RUNTIME names state an op targets that
# the seeds and the earlier stages do not carry, which is the signature of a
# row the AGENT is expected to create before the op fires; the mid-run verifier
# owns that case, and failing it here would refuse every task whose scenario
# has the agent build something the injection then edits.
#
# SERVES-WITH-ORPHAN names the other survivable shape: the op's graded payload
# reached the agent, and a key BESIDE it -- typically a cosmetic ``updated_at``
# stamp the author spelled in the wrong namespace -- landed where no getter
# reads. The dead key is worth reporting and is not worth refusing a task over,
# because the drift the rubric grades is observable either way. An op whose
# WHOLE payload is orphaned has no such defence and stays LANDS-BUT-INVISIBLE.
LANDS_AND_SERVES = "LANDS-AND-SERVES"
LANDS_BUT_INVISIBLE = "LANDS-BUT-INVISIBLE"
SERVES_WITH_ORPHAN = "SERVES-WITH-ORPHAN"
WOULD_ERROR = "WOULD-ERROR"
TABLE_MISSING = "TABLE-MISSING"
NEEDS_RUNTIME = "NEEDS-RUNTIME"

_ADMIN_TABLE = re.compile(r"^/admin/data/([^/]+)/?$")
_ADMIN_ROW = re.compile(r"^/admin/data/([^/]+)/([^/]+)/?$")
_ADMIN_DOC = re.compile(r"^/admin/doc/([^/]+)/?$")
_ADMIN_DOC_MERGE = re.compile(r"^/admin/doc/([^/]+)/merge/?$")

# Applier reasons that mean "the op is malformed", not "the world is not there
# yet". Everything else an unresolved op says is about missing state.
_MALFORMED_MARKERS = ("unknown admin op", "unsupported admin-plane replay",
                      "no such store table")


@dataclass(frozen=True)
class OpVerdict:
    """One replayed op, and what it would do to the world the agent reads."""

    op_id: str
    stage: str
    service: str
    verdict: str
    detail: str

    @property
    def fatal(self) -> bool:
        return self.verdict in (LANDS_BUT_INVISIBLE, WOULD_ERROR, TABLE_MISSING)

    @property
    def survivable(self) -> bool:
        return self.verdict in (NEEDS_RUNTIME, SERVES_WITH_ORPHAN)


# The str-then-int pk ladder that used to sit here is gone. It mirrored a copy
# in admin_plane, and a mirror is exactly what this module exists not to have:
# the two could disagree about which rows a replay can reach, and the preflight
# would then clear an op the runtime misses (or refuse one it lands). Both now
# call Table.admin_*, so tolerance is defined once and the gate judges the
# behaviour the container will actually have.


class InProcessApplier(InjectApplier):
    """``InjectApplier`` whose admin plane is a dict of loaded data modules.

    ``modules`` maps service slug -> the imported ``<api>_data`` module (see
    ``mock_overlay.overlaid_data_module``). Nothing is written to disk and no
    socket is opened; the timeline is kept in memory because the caller wants
    the records, not a file.
    """

    def __init__(self, modules: Dict[str, Any], scratch_dir: Path):
        super().__init__(
            host_api_to_url={api: f"inproc://{api}" for api in modules},
            admin_token=None,
            timeline_path=Path(scratch_dir) / "inproc_timeline.jsonl",
        )
        self._modules = dict(modules)
        self.records: List[Dict[str, Any]] = []

    # -- transport ----------------------------------------------------------

    def module(self, api: str) -> Optional[Any]:
        return self._modules.get(api)

    def _store(self, api: str) -> Optional[Any]:
        return getattr(self._modules.get(api), "_store", None)

    def _append(self, entry: Dict[str, Any], *, ui_values=None) -> None:
        self.records.append(entry)

    def _admin_get(self, api: str, suffix: str) -> Any:
        store = self._store(api)
        if store is None:
            return None
        if suffix.rstrip("/") == "/admin/tables":
            return {
                "tables": [{"name": t, "primary_key": store.table(t).primary_key}
                           for t in store.list_tables()],
                "documents": store.list_documents(),
            }
        try:
            row_match = _ADMIN_ROW.match(suffix)
            if row_match:
                return store.table(row_match.group(1)).admin_get(
                    row_match.group(2))[0]
            table_match = _ADMIN_TABLE.match(suffix)
            if table_match:
                return {"rows": store.table(table_match.group(1)).rows()}
            doc_match = _ADMIN_DOC.match(suffix)
            if doc_match:
                return store.document(doc_match.group(1)).get()
        except Exception:  # noqa: BLE001 - a missing table is a 404, not a crash
            return None
        return None

    def _admin_patch(self, api: str, table: str, pk: str,
                     fields: Dict[str, Any]) -> Dict[str, Any]:
        store = self._store(api)
        if store is None:
            return {"ok": False, "error": "no in-process store"}
        try:
            row, _pk, coercions = store.table(table).admin_patch(pk, fields)
            if row is not None:
                return self._transport_result(
                    {"ok": True, "status": 200, "body": row,
                     "coercions": coercions})
        except Exception as exc:  # noqa: BLE001 - StoreError and friends
            return {"ok": False, "status": 400, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "status": 404, "error": f"row '{pk}' not in table '{table}'"}

    def _admin_post(self, api: str, suffix: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        store = self._store(api)
        if store is None:
            return {"ok": False, "error": "no in-process store"}
        table_match = _ADMIN_TABLE.match(suffix)
        merge_match = _ADMIN_DOC_MERGE.match(suffix)
        try:
            if table_match and isinstance(payload.get("row"), dict):
                row, coercions = store.table(table_match.group(1)).admin_upsert(
                    payload["row"])
                return self._transport_result(
                    {"ok": True, "status": 200, "body": row,
                     "coercions": coercions})
            if merge_match and isinstance(payload.get("fields"), dict):
                value = store.document(merge_match.group(1)).merge(payload["fields"])
                return {"ok": True, "status": 200, "body": value}
        except Exception as exc:  # noqa: BLE001 - StoreError and friends
            return {"ok": False, "status": 400, "error": f"{type(exc).__name__}: {exc}"}
        # Everything else the admin plane serves (/inject/raw's operation list,
        # /apply_as_api's real-endpoint replay) needs the running service to
        # interpret. Say so; the caller turns this into NEEDS-RUNTIME.
        return {"ok": False, "status": 501, "unmodelled": True,
                "error": f"{suffix} is not modelled by the in-process transport"}


def ordered_api_ops(stages: List[InjectStage]) -> List[Tuple[InjectStage, Dict[str, Any]]]:
    """Every API op of every stage, in the order the runtime fires them.

    Seed first (it IS the pre-T0 baseline), then boundary stages by the turn
    they precede; silent before loud within a stage, mirroring ``apply_stage``.
    Order is the whole point: a stage-3 patch of a row a stage-2 upsert created
    is valid, and only a cumulative replay can see that.
    """
    ordered = sorted(stages, key=lambda s: (0, 0) if s.is_seed
                     else (1, s.to_turn if s.to_turn is not None else 0))
    out: List[Tuple[InjectStage, Dict[str, Any]]] = []
    for stage in ordered:
        for op in list(stage.silent) + list(stage.loud):
            if isinstance(op, dict):
                out.append((stage, op))
    return out


@dataclass(frozen=True)
class _Target:
    """What a statically-addressed op aims at, and what it means to write there.

    ``missing`` is set when the op names a store object the service does not
    register — a table typo reads as "row not found" through the admin plane,
    which would be reported as absent state rather than as the authoring error
    it is. ``table``/``pk`` are None for the shapes whose target is only known
    after resolution against live state; ``payload`` holds the key/value pairs
    the op means to make true, which is what tells a redundant write apart from
    a lost one when the serving shape does not move.

    The pairs are kept rather than just the values because severity depends on
    WHICH keys failed: an op is only excused for an orphaned key when the keys
    beside it still reach the agent, and that question cannot be asked of a bag
    of anonymous values.
    """

    table: Optional[str] = None
    pk: Optional[Any] = None
    payload: Tuple[Tuple[str, Any], ...] = ()
    missing: Optional[str] = None

    @property
    def addressable(self) -> bool:
        return bool(self.table) and self.pk is not None

    @property
    def written(self) -> Tuple[Any, ...]:
        return tuple(value for _key, value in self.payload)

    def written_outside(self, orphans: Iterable[str]) -> Tuple[Any, ...]:
        """The values written to keys that are NOT in ``orphans``."""
        dead = {str(k) for k in orphans}
        return tuple(v for k, v in self.payload if str(k) not in dead)


def _target_of(op: Dict[str, Any], applier: InProcessApplier, api: str) -> _Target:
    spec = op.get("admin")
    if not isinstance(spec, dict):
        return _Target()
    store = applier._store(api)
    kind = str(spec.get("op") or "patch").lower()
    if kind in ("doc_set", "doc_merge", "doc.merge"):
        doc = spec.get("document") or spec.get("doc")
        if store is not None and doc not in store.list_documents():
            return _Target(missing=f"document {doc!r} is not registered on {api}")
        return _Target()
    table = applier._resolve_store_table(api, spec.get("table"))
    if store is not None and table not in store.list_tables():
        return _Target(missing=f"table {spec.get('table')!r} is not registered on "
                               f"{api} (registered: {sorted(store.list_tables())})")
    if kind == "patch":
        set_ = spec.get("set") if isinstance(spec.get("set"), dict) else {}
        columns, envelope = partition_expected(set_, nested=True)
        return _Target(table, spec.get("pk"),
                       tuple({**columns, **envelope}.items()))
    if kind == "upsert":
        row = spec.get("row") if isinstance(spec.get("row"), dict) else {}
        bag = row.get("fields") if isinstance(row.get("fields"), dict) else row
        return _Target(table, row.get(spec.get("pk_field") or "id"),
                       tuple(bag.items()))
    return _Target()


def _verdict_from_record(rec: Dict[str, Any]) -> Tuple[str, str]:
    """Classify one applier outcome record. Returns ``(verdict, detail)``."""
    status = str(rec.get("status") or "")
    reason = str(rec.get("reason") or rec.get("error") or "")
    if rec.get("http") == 501:
        return NEEDS_RUNTIME, "admin-plane shape only the running service can interpret"
    if status == "error":
        return WOULD_ERROR, reason or "the applier raised"
    if status in ("unresolved", "no-match"):
        if any(marker in reason for marker in _MALFORMED_MARKERS):
            return WOULD_ERROR, reason
        return NEEDS_RUNTIME, reason or "no matching row in seed or earlier stages"
    if status == "partial":
        return LANDS_BUT_INVISIBLE, reason or "fields dropped on the way to the store"
    if status == "failed":
        if rec.get("orphan_fields") or "read-back" in reason or "serving shape" in reason:
            return LANDS_BUT_INVISIBLE, reason
        return WOULD_ERROR, reason or "the store refused the write"
    return LANDS_AND_SERVES, reason or str(rec.get("verified") or "applied")


def replay_service_ops(applier: InProcessApplier, api: str,
                       ops: List[Tuple[InjectStage, Dict[str, Any]]]) -> List[OpVerdict]:
    """Replay ``ops`` against ``api``'s live store, accumulating state.

    No snapshot is taken between ops on purpose: a stage-4 op is supposed to see
    what stages 0..3 did, and restoring between them would report a valid
    scenario as a pile of missing rows.

    Verdicts come from the applier's own record, then get one more question
    asked of them for the shapes whose target is known up front: did the
    SERVICE GETTER's output actually move? The record judges a write against the
    stored column vocabulary, which is the right test for a mis-cased key but
    cannot see a column the store keeps and the getter never serves.
    """
    out: List[OpVerdict] = []
    module = applier.module(api)
    for stage, op in ops:
        op_id = str(op.get("id") or f"{stage.name}:<unnamed>")
        target = _target_of(op, applier, api)
        if target.missing:
            out.append(OpVerdict(op_id, stage.name, api, TABLE_MISSING, target.missing))
            continue
        probe = module is not None and target.addressable
        before = _observe(module, target) if probe else _Observation()
        try:
            rec = applier._apply_api_mutation(op, stage, stage.to_turn or 0, silent=True)
        except Exception as exc:  # noqa: BLE001 - the raise IS the finding
            out.append(OpVerdict(op_id, stage.name, api, WOULD_ERROR,
                                 f"{type(exc).__name__}: {exc}"))
            continue
        verdict, detail = _verdict_from_record(rec)
        if probe:
            if verdict == LANDS_AND_SERVES:
                verdict, detail = _judge_serving(module, target, before, rec)
            elif verdict == LANDS_BUT_INVISIBLE and rec.get("orphan_fields"):
                verdict, detail = _judge_orphan_severity(module, target, before,
                                                         rec, detail)
        out.append(OpVerdict(op_id, stage.name, api, verdict, detail))
    return out


def _projects_a_sibling(module: Any, table: str, pk: Any) -> bool:
    """Whether this table's getter can be addressed by pk at all.

    Not every ``get_<entity>`` takes one: github's issues are addressed by
    (owner, repo, number), and zendesk's comments getter answers with a LIST.
    Both come back from ``project_in_process`` as "no projection",
    which is indistinguishable from "the row is gone" unless something else is
    tried. So a sibling row is tried. If the getter cannot project that one
    either, it was never a pk-addressable projection and says nothing about our
    write; the stored-row read-back the applier already did stands.
    """
    store = getattr(module, "_store", None)
    if store is None:
        return False
    try:
        rows = store.table(table).rows()
        primary = store.table(table).primary_key
    except Exception:  # noqa: BLE001 - no such table is the caller's finding
        return False
    for row in rows:
        other = row.get(primary)
        if other is None or str(other) == str(pk):
            continue
        if project_in_process(module, table, other)[0] is not None:
            return True
    return False


@dataclass
class _Observation:
    """Everything the agent could read about one row at one instant.

    The single-row getter is only part of the serving shape. A write can be
    published exclusively by a COLLECTION route — gmail never emits
    ``is_starred`` from ``get_message`` and serves it only as membership of
    ``?q=is:starred`` — so the row projection and the table's list/query
    surfaces are captured together and diffed together.
    """

    row: Optional[Dict[str, Any]] = None
    mechanism: str = "no-probe"
    surfaces: Dict[str, Any] = field(default_factory=dict)

    def moved_surface(self, other: "_Observation") -> Optional[str]:
        """The first list/query surface whose answer differs between the two."""
        for label, value in self.surfaces.items():
            if label in other.surfaces and other.surfaces[label] != value:
                return label
        return None


def _observe(module: Any, target: _Target) -> _Observation:
    row, mechanism = project_in_process(module, target.table, target.pk)
    return _Observation(row, mechanism,
                        project_list_surfaces(module, target.table, target.payload))


def _judge_serving(module: Any, target: _Target, before: _Observation,
                   rec: Dict[str, Any]) -> Tuple[str, str]:
    """Second opinion on an applied write: ask the service's read surfaces.

    A projection that MOVED is the proof the write reached the agent, and the
    row getter is asked first because it is the cheapest and the most direct.
    When it does not move, the table's collection routes are asked the same
    question, since a value can be published as membership of a filtered list
    without ever appearing as a field.

    Stillness on every surface is only a failure when the write actually
    produced drift. An op that restates state the world already holds leaves
    the stored row byte-identical, and a write that changed nothing cannot have
    changed something the agent is unable to see.
    """
    after = _observe(module, target)
    if after.row is None:
        if not _projects_a_sibling(module, target.table, target.pk):
            return (LANDS_AND_SERVES,
                    f"{after.mechanism} is not a pk-addressable projection; the "
                    "write was verified on the stored row")
        return (LANDS_BUT_INVISIBLE,
                f"the row is unreadable through {after.mechanism} after the write")
    if after.row != before.row:
        return LANDS_AND_SERVES, f"visible via {after.mechanism}"
    surface = before.moved_surface(after)
    if surface is not None:
        return LANDS_AND_SERVES, f"visible via {surface}"
    if not rec.get("changed"):
        return (LANDS_AND_SERVES,
                f"no-op: the write left the stored row unchanged, so there is no "
                f"drift for {after.mechanism} to serve")
    if all(value_visible(v, after.row) for v in target.written):
        return LANDS_AND_SERVES, f"no-op, already served via {after.mechanism}"
    return (LANDS_BUT_INVISIBLE,
            f"{after.mechanism} serves neither the written values nor anything "
            "new — the write cannot reach the agent")


def _judge_orphan_severity(module: Any, target: _Target, before: _Observation,
                           rec: Dict[str, Any], reason: str) -> Tuple[str, str]:
    """Grade an orphan-key finding by what the REST of the payload did.

    An orphan is always a real defect: the key landed where no getter reads it.
    It is not always a reason to refuse the task. Authors routinely stamp a
    cosmetic ``updated_at`` beside the business payload and spell it in the
    wrong namespace, and that stamp misfiling does not stop the graded drift
    from reaching the agent.

    So the payload is split the way it was written, and there are two ways to
    earn the downgrade. Either keys survive the orphan list and are visible in
    the post-write serving shape — visibility, not mere movement, because the
    orphan itself can move a projection that serves its namespace wholesale —
    or the orphans are nothing but clock stamps, in which case the op never
    carried a value the agent reads for meaning and there is none to lose.
    Anything else keeps the verdict: contentful's ``published_version`` encodes
    publish state, square's ``price_amount`` is the price, and an op whose whole
    payload is one of those reaches no one.
    """
    orphans = tuple(rec.get("orphan_fields") or ())
    survivors = target.written_outside(orphans)
    if not survivors:
        if orphans and all(is_clock_stamp(k) for k in orphans):
            return (SERVES_WITH_ORPHAN,
                    f"{reason} — the payload is a clock stamp and nothing else, "
                    "so no value the rubric grades was lost")
        return LANDS_BUT_INVISIBLE, reason
    after = _observe(module, target)
    if after.row is None or not all(value_visible(v, after.row) for v in survivors):
        return LANDS_BUT_INVISIBLE, reason
    return (SERVES_WITH_ORPHAN,
            f"{reason} — the rest of the payload is served via "
            f"{after.mechanism}, so the graded drift still reaches the agent")
