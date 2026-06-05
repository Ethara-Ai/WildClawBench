"""
WildClawBench InjectDirector: Talos-style staged, inject-between-turns.

This is the second silent-injection model in WildClawBench (alongside the
``stage_director`` / ``stages.yaml`` model). It consumes the richer Talos
``inject/stageN/mutations.json`` layout shipped by tasks like
``LAYLA_001_october_grant_crunch`` and applies each stage's *silent* mutations
between agent turns, while the agent is idle, via each mock API's ``/admin/*``
admin plane (so the change never appears in the agent-visible ``/audit/*`` feed).

Why a separate module from ``stage_director``
---------------------------------------------
* ``stages.yaml`` expresses mutations directly as admin-plane ops
  (``{api, op: data.patch, table, pk, fields}``) and uses ``1 + len(stages)``
  turns with a single neutral nudge.
* The Talos ``inject/`` format expresses mutations as **service REST calls**
  (``{service, method, path, body}``), drives a fixed 50-turn script from
  ``prompts.txt``, and applies each ``stageN`` between specific turn boundaries
  (e.g. ``applies_between_turns: ["T12", "T13"]``).

Design choices
--------------
* **Baseline already seeded.** The task's ``mock_data/`` overlays already
  contain the canonical pre-T0 state, so the stage0 ``loud`` API mutations are
  NOT replayed by default (they would only re-assert state already present and
  would pollute the audit feed). Only stage0 ``filesystem`` drops are seeded
  (optional, requires a workspace copy hook). Set ``replay_loud=True`` to also
  replay ``loud`` ops as visible seed history.
* **Apply-time resolution.** The Talos mutations carry unresolved placeholders
  (``{rec_UDI-2026-007}``, ``{page_id_...}``) and field-name casing that may not
  match the live store columns. Rather than trust the literal path/body, the
  applier reads the live admin state (``GET /admin/data/<table>``), locates the
  target row by its embedded business key, and maps fields case-insensitively
  before issuing a ``PATCH /admin/data/<table>/<pk>``. Anything it cannot
  resolve is logged to the timeline as ``unresolved`` rather than silently
  dropped.
* **Silent only.** Mutations flagged ``silent: true`` (or every entry in the
  ``silent`` array) are applied through ``/admin/*`` and are therefore invisible
  to the agent. ``loud`` / ``filesystem`` are seed-time concerns.

Turn model
----------
``turn_messages(...)`` returns the full per-turn wake-up list parsed from
``prompts.txt``. ``stage_for_boundary(turn_index)`` returns the stage that must
be applied *before* running turn ``turn_index`` (i.e. the stage whose
``applies_between_turns`` ends at that turn). run_batch wires this into the
openclaw runner's ``before_turn`` hook.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

LOG = logging.getLogger("wildclaw.inject")


class InjectConfigError(Exception):
    """Raised for a malformed inject/ directory."""


# ---------------------------------------------------------------------------
# Script model
# ---------------------------------------------------------------------------

def _turn_to_index(token: Any) -> Optional[int]:
    """Parse a turn token like ``"T13"`` / ``13`` / ``null`` -> int or None."""
    if token is None:
        return None
    if isinstance(token, int):
        return token
    m = re.match(r"\s*T?(\d+)\s*$", str(token))
    return int(m.group(1)) if m else None


@dataclass
class InjectStage:
    index: int
    name: str
    # (from_turn, to_turn): the mutation is applied AFTER from_turn and BEFORE
    # to_turn. from_turn is None for the pre-T0 seed stage.
    from_turn: Optional[int]
    to_turn: Optional[int]
    filesystem: List[Dict[str, Any]] = field(default_factory=list)
    loud: List[Dict[str, Any]] = field(default_factory=list)
    silent: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""

    @property
    def is_seed(self) -> bool:
        return self.from_turn is None


def _coerce_mutation_buckets(raw_muts: Any) -> Tuple[list, list, list]:
    """Return (filesystem, loud, silent) from a stage's ``mutations`` value.

    Tolerates two on-disk shapes seen in the Talos export:
      * dict form: ``{"filesystem": [...], "loud": [...], "silent": [...]}``
      * list form: a flat list of op dicts, each optionally carrying
        ``silent: true`` / ``kind`` / ``bucket`` to classify it.
    Unknown shapes yield three empty lists (logged by the caller).
    """
    if isinstance(raw_muts, dict):
        return (
            list(raw_muts.get("filesystem") or []),
            list(raw_muts.get("loud") or []),
            list(raw_muts.get("silent") or []),
        )
    if isinstance(raw_muts, list):
        fs: list = []
        loud: list = []
        silent: list = []
        for op in raw_muts:
            if not isinstance(op, dict):
                continue
            bucket = op.get("bucket") or op.get("kind")
            if op.get("silent") is True or bucket == "silent":
                silent.append(op)
            elif "action" in op or bucket == "filesystem":
                fs.append(op)
            elif op.get("service") or op.get("path"):
                # An API op with no explicit silent flag in list form: treat as
                # loud (visible) by default — silent must be opted into.
                loud.append(op)
        return fs, loud, silent
    return [], [], []


@dataclass
class InjectScript:
    description: str
    stages: List[InjectStage] = field(default_factory=list)

    @classmethod
    def load(cls, inject_dir: Path | str) -> "InjectScript":
        d = Path(inject_dir)
        if not d.is_dir():
            raise InjectConfigError(f"inject dir not found: {d}")
        stage_dirs = sorted(
            (p for p in d.iterdir() if p.is_dir() and re.match(r"stage\d+$", p.name)),
            key=lambda p: int(re.match(r"stage(\d+)$", p.name).group(1)),
        )
        if not stage_dirs:
            raise InjectConfigError(f"no stageN/ dirs under {d}")
        stages: List[InjectStage] = []
        for sd in stage_dirs:
            mf = sd / "mutations.json"
            if not mf.is_file():
                LOG.warning("inject: %s has no mutations.json; skipping", sd.name)
                continue
            try:
                raw = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise InjectConfigError(f"{mf}: {exc}") from exc
            idx = int(re.match(r"stage(\d+)$", sd.name).group(1))
            between = (
                raw.get("applies_between_turns")
                or raw.get("applied_between")
                or [None, None]
            )
            from_turn = _turn_to_index(between[0]) if len(between) > 0 else None
            to_turn = _turn_to_index(between[1]) if len(between) > 1 else None
            fs, loud, silent = _coerce_mutation_buckets(raw.get("mutations"))
            if not (fs or loud or silent):
                LOG.warning("inject: %s mutations had no recognized ops "
                            "(shape=%s)", sd.name, type(raw.get("mutations")).__name__)
            stages.append(InjectStage(
                index=idx,
                name=str(raw.get("stage_name") or sd.name),
                from_turn=from_turn,
                to_turn=to_turn,
                filesystem=fs,
                loud=loud,
                silent=silent,
                source=str(mf),
            ))
        return cls(description=f"inject:{d.name}", stages=stages)

    def seed_stage(self) -> Optional[InjectStage]:
        for s in self.stages:
            if s.is_seed:
                return s
        return None

    def stage_for_boundary(self, turn_index: int) -> Optional[InjectStage]:
        """The non-seed stage that must be applied BEFORE running ``turn_index``.

        A stage with ``applies_between_turns: ["T12", "T13"]`` is returned when
        ``turn_index == 13`` (its ``to_turn``).
        """
        for s in self.stages:
            if not s.is_seed and s.to_turn == turn_index:
                return s
        return None


# ---------------------------------------------------------------------------
# prompts.txt parsing (50-turn wake-up script)
# ---------------------------------------------------------------------------

_TURN_RE = re.compile(r"^---\s*TURN\s+T(\d+)\b.*?---\s*$", re.IGNORECASE)


def parse_prompts_file(path: Path | str) -> List[str]:
    """Parse a ``prompts.txt`` into an ordered list of per-turn wake-up messages.

    Recognizes block headers of the form ``--- TURN T<n> (...) ---``; the body
    is every non-comment, non-blank line until the next header. Leading ``#``
    banner/comment lines (before the first TURN header, and full-line ``#``
    comments) are ignored. Turns are returned ordered by their T-index.
    """
    text = Path(path).read_text(encoding="utf-8")
    turns: Dict[int, List[str]] = {}
    current: Optional[int] = None
    for line in text.splitlines():
        m = _TURN_RE.match(line.strip())
        if m:
            current = int(m.group(1))
            turns.setdefault(current, [])
            continue
        if current is None:
            continue
        if line.strip().startswith("#"):
            continue
        turns[current].append(line)
    ordered = []
    for idx in sorted(turns):
        body = "\n".join(turns[idx]).strip()
        ordered.append(body)
    return ordered


# ---------------------------------------------------------------------------
# Applier
# ---------------------------------------------------------------------------

# Map a Talos ``service`` name -> the admin-plane store table(s) to search and
# the columns that hold a human/business key we can match a placeholder against.
# Each entry: (candidate_table_prefixes, business_key_columns).
_SERVICE_RESOLUTION = {
    "airtable-api": (("records_",), ("PlotID", "plot_id", "Name", "name", "id")),
    "notion-api": (("pages",), ("title", "Name", "name", "id")),
    "confluence-api": (("pages",), ("title", "Name", "name", "id")),
}


class InjectApplier:
    """Applies a stage's silent mutations through each API's ``/admin/*`` plane.

    ``host_api_to_url`` maps api-name -> ``http://127.0.0.1:<published-port>``
    (the same map the DriftDirector / StageApplier use). Every applied or
    skipped mutation is appended to ``inject_timeline.jsonl``.

    ``copy_into_workspace`` (optional) is ``fn(host_src: Path, container_dst:
    str) -> bool`` used to seed stage0 filesystem drops; when absent, filesystem
    ops are logged as ``skipped``.
    """

    def __init__(
        self,
        host_api_to_url: Dict[str, str],
        admin_token: Optional[str],
        timeline_path: Path,
        inject_root: Optional[Path] = None,
        copy_into_workspace=None,
        replay_loud: bool = False,
    ):
        self._urls = dict(host_api_to_url)
        self._token = admin_token
        self._timeline_path = Path(timeline_path)
        self._timeline_path.parent.mkdir(parents=True, exist_ok=True)
        self._inject_root = Path(inject_root) if inject_root else None
        self._copy = copy_into_workspace
        self._replay_loud = replay_loud
        self._session = requests.Session()

    # -- public API ---------------------------------------------------------

    def seed(self, script: InjectScript) -> None:
        stage = script.seed_stage()
        if stage is None:
            return
        self._append({"type": "inject.seed.start", "ts": time.time(),
                      "stage": stage.name,
                      "fs": len(stage.filesystem), "loud": len(stage.loud)})
        for op in stage.filesystem:
            self._apply_filesystem(op, stage)
        if self._replay_loud:
            for op in stage.loud:
                self._apply_api_mutation(op, stage, turn_index=0, silent=False)
        self._append({"type": "inject.seed.done", "ts": time.time(),
                      "stage": stage.name})

    def apply_stage(self, stage: InjectStage, turn_index: int) -> List[Dict[str, Any]]:
        outcomes: List[Dict[str, Any]] = []
        for op in stage.silent:
            outcomes.append(self._apply_api_mutation(op, stage, turn_index, silent=True))
        # list-form stages may also carry filesystem drops mid-run
        for op in stage.filesystem:
            outcomes.append(self._apply_filesystem(op, stage))
        self._append({
            "type": "inject.stage.applied",
            "ts": time.time(),
            "stage": stage.name,
            "applied_before_turn": turn_index,
            "silent_ops": len(stage.silent),
            "outcomes": outcomes,
        })
        LOG.info("inject stage '%s' applied before turn %d: %d silent op(s)",
                 stage.name, turn_index, len(stage.silent))
        return outcomes

    def close(self) -> None:
        self._session.close()

    # -- filesystem ---------------------------------------------------------

    def _apply_filesystem(self, op: Dict[str, Any], stage: InjectStage) -> Dict[str, Any]:
        action = op.get("action")
        dst = op.get("dst")
        rec = {"id": op.get("id"), "action": action, "dst": dst}
        if self._copy is None or self._inject_root is None:
            rec.update(ok=False, status="skipped", reason="no workspace copy hook")
            self._append({"type": "inject.fs", **rec, "ts": time.time()})
            return rec
        if action == "mkdir":
            ok = self._copy(None, dst, mkdir=True)
            rec.update(ok=bool(ok), status="mkdir")
            self._append({"type": "inject.fs", **rec, "ts": time.time()})
            return rec
        src = op.get("src")
        if not src or not dst:
            rec.update(ok=False, status="skipped", reason="missing src/dst")
            self._append({"type": "inject.fs", **rec, "ts": time.time()})
            return rec
        host_src = (Path(stage.source).parent / src).resolve()
        # Placeholder stand-ins are never load-bearing content; skip with a note.
        if not host_src.exists():
            rec.update(ok=False, status="missing_src", reason=str(host_src))
            self._append({"type": "inject.fs", **rec, "ts": time.time()})
            return rec
        try:
            ok = self._copy(host_src, dst)
            rec.update(ok=bool(ok), status="copied")
        except Exception as exc:  # pragma: no cover - defensive
            rec.update(ok=False, status="error", reason=str(exc))
        self._append({"type": "inject.fs", **rec, "ts": time.time()})
        return rec

    # -- API mutation -------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {"X-Admin-Token": self._token} if self._token else {}

    def _admin_get(self, api: str, suffix: str) -> Any:
        base = self._urls.get(api)
        if not base:
            return None
        try:
            r = self._session.get(base.rstrip("/") + suffix,
                                  headers=self._headers(), timeout=5.0)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            return None
        return None

    def _admin_patch(self, api: str, table: str, pk: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        base = self._urls.get(api)
        if not base:
            return {"ok": False, "error": "no admin URL"}
        try:
            r = self._session.patch(
                base.rstrip("/") + f"/admin/data/{table}/{pk}",
                json={"fields": fields},
                headers=self._headers(), timeout=5.0,
            )
            ctype = r.headers.get("content-type", "")
            return {"ok": r.status_code < 300, "status": r.status_code,
                    "body": r.json() if ctype.startswith("application/json") else r.text[:200]}
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)}

    def _apply_api_mutation(self, op: Dict[str, Any], stage: InjectStage,
                            turn_index: int, silent: bool) -> Dict[str, Any]:
        api = op.get("service") or op.get("api")
        rec = {"id": op.get("id"), "service": api, "method": op.get("method"),
               "path": op.get("path"), "silent": silent}
        if not api or api not in self._urls:
            rec.update(ok=False, status="unresolved", reason=f"no admin URL for {api}")
            self._append({"type": "inject.api", **rec, "ts": time.time()})
            return rec
        resolved = self._resolve_target(api, op)
        if resolved is None:
            rec.update(ok=False, status="unresolved",
                       reason="could not locate target row in live store")
            self._append({"type": "inject.api", **rec, "ts": time.time()})
            return rec
        table, pk, fields = resolved
        rec.update(table=table, pk=pk, fields=list(fields.keys()))
        result = self._admin_patch(api, table, pk, fields)
        rec.update(result)
        rec["status"] = rec.get("status", "applied" if result.get("ok") else "failed")
        self._append({"type": "inject.api", **rec, "ts": time.time()})
        return rec

    def _resolve_target(self, api: str, op: Dict[str, Any]
                        ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """Resolve a Talos REST mutation to (table, pk, fields) against live state.

        Strategy: pull the flat field map from the op body (supports the airtable
        ``{fields:{...}}`` and notion/confluence ``{properties:{...}}`` shapes),
        extract the business key embedded in the path placeholder, then scan the
        candidate store tables for the matching row and map field casing to the
        row's real column names.
        """
        fields = self._extract_fields(op)
        if not fields:
            return None
        key = self._extract_key_from_path(op.get("path", ""))
        prefixes, key_cols = _SERVICE_RESOLUTION.get(api, ((), ("id",)))
        tables = self._admin_get(api, "/admin/tables") or []
        table_names = [t.get("name") if isinstance(t, dict) else t for t in tables]
        candidates = [t for t in table_names
                      if any(t.startswith(p) for p in prefixes)] or table_names
        for table in candidates:
            rows = self._admin_get(api, f"/admin/data/{table}") or []
            if isinstance(rows, dict):
                rows = rows.get("rows") or rows.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if key is not None and not self._row_matches_key(row, key, key_cols):
                    continue
                pk = row.get("id") or row.get("pk")
                if pk is None:
                    continue
                mapped = self._map_fields_to_row(fields, row)
                if mapped:
                    return table, str(pk), mapped
        return None

    @staticmethod
    def _extract_fields(op: Dict[str, Any]) -> Dict[str, Any]:
        body = op.get("body") or {}
        if not isinstance(body, dict):
            return {}
        if isinstance(body.get("fields"), dict):
            flat = {k: v for k, v in body["fields"].items() if not k.startswith("_")}
            return flat
        if isinstance(body.get("properties"), dict):
            # Flatten notion/confluence property shapes to leaf scalar values.
            flat = {}
            for k, v in body["properties"].items():
                flat[k] = _flatten_property_value(v)
            return flat
        # whole-body scalar fields (rare)
        return {k: v for k, v in body.items()
                if isinstance(v, (str, int, float, bool)) and not k.startswith("_")}

    @staticmethod
    def _extract_key_from_path(path: str) -> Optional[str]:
        """Pull the business key out of a path placeholder.

        ``/v0/app/Field-Trial-Udi/records/{rec_UDI-2026-007}`` -> ``UDI-2026-007``
        ``/v1/pages/{page_id_WAITA-EACRI_Proposal_v8.0}``       -> ``WAITA-EACRI_Proposal_v8.0``
        A non-placeholder trailing id is returned verbatim.
        """
        m = re.search(r"\{([^}]+)\}", path or "")
        token = m.group(1) if m else (path or "").rstrip("/").rsplit("/", 1)[-1]
        if not token:
            return None
        token = re.sub(r"^(rec_|page_id_|id_|rec|page_)", "", token)
        return token.strip() or None

    @staticmethod
    def _row_matches_key(row: Dict[str, Any], key: str, key_cols: Tuple[str, ...]) -> bool:
        norm = key.replace("_", " ").replace("-", " ").lower().strip()
        for col in key_cols:
            for rk, rv in row.items():
                if rk.lower() != col.lower():
                    continue
                if rv is None:
                    continue
                rvn = str(rv).replace("_", " ").replace("-", " ").lower().strip()
                if rvn == norm or norm in rvn or rvn in norm:
                    return True
        # also try any column equalling the raw key
        for rv in row.values():
            if rv is not None and str(rv).strip().lower() == key.strip().lower():
                return True
        return False

    @staticmethod
    def _map_fields_to_row(fields: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
        """Map mutation field names to the row's real column casing.

        ``{"yield_kg_m2": 16.8}`` against a row with column ``Yield_kg_m2`` ->
        ``{"Yield_kg_m2": 16.8}``. Unmatched fields are passed through verbatim
        (the admin plane will add them as-is) so a deliberately new column still
        lands, but matched ones avoid creating a dead duplicate-cased column.
        """
        lower_to_real = {k.lower(): k for k in row.keys()}
        mapped: Dict[str, Any] = {}
        for k, v in fields.items():
            real = lower_to_real.get(k.lower(), k)
            mapped[real] = v
        return mapped

    # -- timeline -----------------------------------------------------------

    def _append(self, entry: Dict[str, Any]) -> None:
        entry.setdefault("ts", time.time())
        entry.setdefault("ts_iso", time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry.get("ts", time.time()))))
        with open(self._timeline_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")


def _flatten_property_value(v: Any) -> Any:
    """Reduce a notion/confluence property object to a representative scalar."""
    if isinstance(v, dict):
        if "email" in v:
            return v["email"]
        if "select" in v and isinstance(v["select"], dict):
            return v["select"].get("name")
        if "date" in v and isinstance(v["date"], dict):
            return v["date"].get("start")
        for key in ("title", "rich_text"):
            arr = v.get(key)
            if isinstance(arr, list) and arr:
                t = arr[0].get("text") if isinstance(arr[0], dict) else None
                if isinstance(t, dict):
                    return t.get("content")
        if "value" in v:
            return v["value"]
    return v
