"""Refuse a defective task before it costs a trajectory.

A model failure is the product. A harness bug is ours to fix. A TASK defect is
neither: it produces a graded artifact that says something about a world that
was never there, and it costs a container, forty-five minutes and twenty
dollars to produce. Every confirmed injection incident on record — the willie
linkedin counters, the two koji contentful entries, sean's ironbark row, the
five abena monday items — was an op whose write could not reach the agent, and
every one of them was decidable in seconds without a container. The 51-service
stale-fleet run was decidable the same way, one directory listing at a time.

So both are decided here, before anything is spent.

(A) INJECTION REPLAY. Load the task's own mock world in-process (its
``mock_data/`` overlaid on ``environment/``, because pristine seeds answer a
question the task never asked), replay every op of every stage in order with
state accumulating, and read back through the service's own getter. See
``inject_inproc`` for the verdicts; four of the five are fatal and the fifth,
``NEEDS-RUNTIME``, is the one the mid-run verifier owns — an op targeting state
the agent is meant to create first is a scenario, not a defect.

(B) ENVIRONMENT SURFACE. Every REQUIRED service must exist in the catalog, ship
a connector skill, load with the task's overlay, and survive its own loader. A
required service whose seeds corrupt on load serves the agent nothing; a
distractor's do not change what the task asks for, so those warn.

This gate COMPLEMENTS the runtime verifiers and replaces none of them. It says
only what is decidable before the run: that the world the task describes is a
world the fleet can actually build.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.utils.inject_director import (
    InjectConfigError, InjectScript, NarrativeClock, resolve_stage_mtime,
)
from src.utils.inject_inproc import (
    InProcessApplier, LANDS_AND_SERVES, NEEDS_RUNTIME, OpVerdict, ordered_api_ops,
    replay_service_ops,
)
from src.utils.inject_validator import _resolve_fs_src, validate_inject_script
from src.utils.mock_overlay import ENVIRONMENT_DIR, OverlayError, overlaid_data_module, overlaid_tree
from src.utils.service_probe import detect_seed_roundtrip, load_service
from src.utils.sim_clock import compute_sim_clock
from src.utils.skills_inference import catalog_apis

__all__ = ["FATAL", "WARN", "GateFinding", "GateReport", "gate_task"]

FATAL, WARN = "FATAL", "WARN"

# OpenClaw's own tools can be named as a loud-inject `service`; they deliver
# in-band to the agent and have no environment/ folder, so the catalog checks
# say nothing about them.
NATIVE_SERVICES = frozenset({
    "message", "cron", "nodes", "canvas", "gateway", "image",
    "sessions_send", "subagents", "agents_list",
})


@dataclass(frozen=True)
class GateFinding:
    severity: str
    kind: str
    subject: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.kind}] {self.subject}: {self.reason}"


@dataclass(frozen=True)
class GateReport:
    """Everything the gate decided about one task, and what it cost to decide."""

    task_id: str
    findings: Tuple[GateFinding, ...] = ()
    ops: int = 0
    elapsed_ms: int = 0

    @property
    def fatal(self) -> Tuple[GateFinding, ...]:
        return tuple(f for f in self.findings if f.severity == FATAL)

    @property
    def warnings(self) -> Tuple[GateFinding, ...]:
        return tuple(f for f in self.findings if f.severity == WARN)

    @property
    def ok(self) -> bool:
        return not self.fatal

    def stamp(self, status: str) -> Dict[str, Any]:
        """The record a run carries so a gate verdict is never only a log line.

        A bypassed gate stays visible in the artifact for as long as the
        artifact exists, which is the point: a run that was allowed past a known
        defect must not look like a run that passed.
        """
        record: Dict[str, Any] = {"status": status, "ops": self.ops,
                                  "warns": len(self.warnings),
                                  "elapsed_ms": self.elapsed_ms}
        if status != "passed":
            record["findings"] = [
                {"kind": f.kind, "subject": f.subject, "reason": f.reason}
                for f in self.fatal
            ]
        return record

    def defect_record(self, status: str) -> Dict[str, Any]:
        """The full account of a gate verdict, for a ``defect.json`` on disk.

        ``stamp`` is the index entry a score carries; this is the page it points
        at, and it is written next to the artifact so the reader who finds a
        suspect run does not have to re-run the gate to learn what it said.

        Two differences from the stamp, both deliberate. It carries EVERY
        finding rather than the fatal ones, because a warning that never reached
        disk is a warning nobody will act on — ``NEEDS-RUNTIME`` in particular
        is the verdict that turns out to have been the defect once the run comes
        back empty. And it is timestamped, because the task directory it judged
        is mutable: a defect.json that cannot say WHEN it was decided is a claim
        about a task that may no longer exist in that shape.

        Flat by construction — one header, one list of findings, no nesting to
        walk — because its reader is as often a person with ``less`` as a
        program with ``jq``.
        """
        return {
            "task": self.task_id,
            "status": status,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "elapsed_ms": self.elapsed_ms,
            "ops": self.ops,
            "fatal": len(self.fatal),
            "warns": len(self.warnings),
            "findings": [
                {"severity": f.severity, "kind": f.kind,
                 "subject": f.subject, "reason": f.reason}
                for f in self.fatal + self.warnings
            ],
        }

    def summary(self) -> str:
        head = (f"task gate: {len(self.fatal)} fatal, {len(self.warnings)} warn "
                f"({self.ops} op(s), {self.elapsed_ms}ms)")
        return "\n".join([head] + [f"  {f}" for f in self.fatal + self.warnings])


def _declared_apis(task_dir: Path) -> Tuple[List[str], List[str]]:
    """``(required, distractor)`` as the task declares them, ``-api``-suffixed.

    Normalized the way ``task_parser`` normalizes a declaration, so the gate
    judges the same list the launcher mounts. ``auto`` is the documented
    full-catalog sentinel for distractors, never a service name.
    """
    for name in ("task.yaml", "task.json"):
        path = task_dir / name
        if not path.is_file():
            continue
        try:
            if name.endswith(".json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
            else:
                import yaml  # noqa: PLC0415 - optional dependency, read here only

                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed task file is section 5's finding
            continue
        if not isinstance(raw, dict):
            continue
        return (_normalize(raw.get("required_apis") or raw.get("required_mock_apis")),
                _normalize(raw.get("distractor_apis") or raw.get("distractor_mock_apis")))
    return [], []


def _normalize(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [] if value.strip().lower() in ("auto", "__auto__") else [value]
    if not isinstance(value, list):
        return []
    out = {f"{str(v).strip()}-api" if not str(v).strip().endswith("-api") else str(v).strip()
           for v in value if str(v).strip()}
    return sorted(out)


def _overlay_for(task_dir: Path, api: str) -> Optional[Path]:
    overlay = task_dir / "mock_data" / api
    return overlay if overlay.is_dir() else None


# --------------------------------------------------------------------------- #
# (A) injection replay
# --------------------------------------------------------------------------- #

def _authoring_findings(script: InjectScript, task_dir: Path,
                        environment_dir: Path) -> List[GateFinding]:
    """Fold in the static validator rather than restating it.

    ``validate_inject_script`` already owns unresolvable slugs, off-catalog
    services, field-less ops and the filesystem src/dst/mtime-override contract.
    Two classes of its findings are dropped here:

    * ops addressed to an OpenClaw native tool — they have no service directory
      by design, so "no admin URL" is the map's answer, not a defect;
    * ``missing-target`` — it guesses at row existence by scraping ids out of
      ``mock_data/``, which sees nothing of the rows a service seeds itself and
      nothing of what an earlier op created. The replay below settles the same
      question by asking the loaded store, so keeping both would mean failing a
      task on the weaker of two answers.
    """
    urls = {api: f"inproc://{api}" for api in catalog_apis(str(environment_dir))}
    native_ops = {str(op.get("id")) for stage in script.stages
                  for op in list(stage.silent) + list(stage.loud)
                  if (op.get("service") or op.get("api")) in NATIVE_SERVICES}
    fatal, warnings = validate_inject_script(
        script, urls, task_dir / "mock_data", environment_dir)
    out: List[GateFinding] = []
    for entries, severity in ((fatal, FATAL), (warnings, WARN)):
        for entry in entries:
            if str(entry.get("id")) in native_ops or entry.get("status") == "missing-target":
                continue
            out.append(GateFinding(
                severity, str(entry.get("status") or "inject-authoring"),
                f"{entry.get('stage')}/{entry.get('id')}", str(entry.get("reason") or "")))
    return out


def _mtime_findings(script: InjectScript, task_dir: Path) -> List[GateFinding]:
    """Dry-run the mtime ladder for every stage that drops a file.

    Resolution is attempted with the boundary turn's clock deliberately absent,
    because a stage that needs it is already leaning on the rung below its own
    ``applied_at_local_time``. What remains is T0, and a task that cannot even
    reach T0 has no narrative timeline to stamp against — at apply time that
    raises mid-run, with the container already up and paid for.
    """
    sim = compute_sim_clock({"task_dir": str(task_dir)})
    clock = NarrativeClock(t0_epoch_ms=sim.epoch_ms if sim is not None else None)
    out: List[GateFinding] = []
    for stage in script.stages:
        if not stage.filesystem:
            continue
        try:
            resolve_stage_mtime(stage, clock)
        except InjectConfigError as exc:
            out.append(GateFinding(FATAL, "MTIME-UNRESOLVABLE", stage.name, str(exc)))
    return out


def _payload_findings(script: InjectScript) -> List[GateFinding]:
    """A copy op whose payload is missing or empty drops nothing on the agent.

    ``_resolve_fs_src`` is the runtime hook's own three-step chain, so a src
    that resolves here resolves there. Emptiness is the half no other check
    covers: a zero-byte stand-in satisfies every existence test and gives the
    agent a file with nothing in it to find.
    """
    out: List[GateFinding] = []
    for stage in script.stages:
        for op in stage.filesystem:
            src = op.get("src")
            if op.get("action") != "copy" or not src:
                continue
            resolved, _ = _resolve_fs_src(stage, str(src))
            if resolved is not None and resolved.is_file() and resolved.stat().st_size == 0:
                out.append(GateFinding(
                    FATAL, "FS-PAYLOAD-EMPTY", f"{stage.name}/{op.get('id')}",
                    f"src {src!r} resolves to a zero-byte file ({resolved}) — the "
                    "drop lands and the agent finds nothing in it"))
    return out


def _replay_findings(script: InjectScript, task_dir: Path,
                     environment_dir: Path) -> Tuple[List[GateFinding], int]:
    """Replay every API op, one service at a time, state accumulating."""
    ops = ordered_api_ops(script.stages)
    by_service: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {}
    for stage, op in ops:
        service = op.get("service") or op.get("api")
        if service and service not in NATIVE_SERVICES:
            by_service.setdefault(str(service), []).append((stage, op))

    findings: List[GateFinding] = []
    replayed = 0
    with tempfile.TemporaryDirectory(prefix="inject-preflight-") as scratch:
        for service, service_ops in sorted(by_service.items()):
            try:
                with overlaid_data_module(service, _overlay_for(task_dir, service),
                                          environment_dir) as module:
                    applier = InProcessApplier({service: module}, Path(scratch))
                    verdicts = replay_service_ops(applier, service, service_ops)
            except OverlayError as exc:
                findings.append(GateFinding(FATAL, "SERVICE-MISSING", service, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 - a loader may raise anything
                findings.append(GateFinding(
                    FATAL, "OVERLAY-UNLOADABLE", service,
                    f"the task's own mock_data does not load: {type(exc).__name__}: {exc}"))
                continue
            replayed += len(verdicts)
            findings.extend(_finding_for(v) for v in verdicts
                            if v.verdict != LANDS_AND_SERVES)
    return findings, replayed


def _finding_for(verdict: OpVerdict) -> GateFinding:
    severity = WARN if verdict.verdict == NEEDS_RUNTIME else FATAL
    return GateFinding(severity, verdict.verdict,
                       f"{verdict.service} {verdict.stage}/{verdict.op_id}",
                       verdict.detail)


# --------------------------------------------------------------------------- #
# (B) environment surface
# --------------------------------------------------------------------------- #

def _surface_findings(task_dir: Path, apis: Sequence[str], severity: str,
                      environment_dir: Path) -> List[GateFinding]:
    """Catalog, connector, in-process load and seed round-trip for each api.

    ``severity`` is FATAL for a required service and WARN for a distractor: a
    distractor that will not load is a fleet problem worth reporting, but it
    does not stop the task from being answerable.
    """
    catalog = set(catalog_apis(str(environment_dir)))
    out: List[GateFinding] = []
    for api in apis:
        if catalog and api not in catalog:
            out.append(GateFinding(
                severity, "SERVICE-NOT-IN-CATALOG", api,
                f"no {environment_dir.name}/{api}/service.toml exists, so the "
                "fleet cannot serve it"))
            continue
        if not (environment_dir / "skills" / f"{api}-connector").is_dir():
            out.append(GateFinding(
                severity, "CONNECTOR-MISSING", api,
                f"no {environment_dir.name}/skills/{api}-connector — the agent is "
                "given no documented way to call this service"))
        out.extend(_load_findings(task_dir, api, severity, environment_dir))
    return out


def _load_findings(task_dir: Path, api: str, severity: str,
                   environment_dir: Path) -> List[GateFinding]:
    overlay = _overlay_for(task_dir, api)
    try:
        with overlaid_tree(api, overlay, environment_dir) as service_dir:
            probe = load_service(service_dir)
            if probe.app is None:
                return [GateFinding(
                    severity, "SERVICE-UNLOADABLE", api,
                    f"server.py does not import under this task's overlay: {probe.error}")]
            return [GateFinding(severity, "SEED-COERCION-LOSS",
                                f"{api} {item['path']}", str(item["detail"]))
                    for item in detect_seed_roundtrip(probe)]
    except OverlayError as exc:
        return [GateFinding(severity, "SERVICE-MISSING", api, str(exc))]


def _grading_findings(task_dir: Path) -> List[GateFinding]:
    """The two loaders that otherwise raise mid-launch, surfaced by task name."""
    out: List[GateFinding] = []
    rubric = task_dir / "rubric.json"
    if not rubric.is_file():
        out.append(GateFinding(FATAL, "RUBRIC-MISSING", task_dir.name,
                               "rubric.json is absent — nothing to grade against"))
    else:
        try:
            json.loads(rubric.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append(GateFinding(FATAL, "RUBRIC-UNPARSEABLE", task_dir.name,
                                   f"rubric.json does not parse: {exc}"))
    has_json = (task_dir / "prompts.json").is_file()
    has_txt = (task_dir / "prompts.txt").is_file()
    if not (has_json or has_txt):
        out.append(GateFinding(FATAL, "PROMPTS-MISSING", task_dir.name,
                               "neither prompts.json nor prompts.txt is present — "
                               "the agent has nothing to be woken with"))
    elif has_json and not has_txt:
        out.append(GateFinding(FATAL, "PROMPTS-PAIR-INCOMPLETE", task_dir.name,
                               "prompts.json ships without its companion "
                               "prompts.txt, which is what the client receives"))
    return out


# --------------------------------------------------------------------------- #

def gate_task(task_dir: Path | str, required_apis: Optional[Sequence[str]] = None,
              environment_dir: Optional[Path] = None) -> GateReport:
    """Decide whether ``task_dir`` describes a world the fleet can build.

    ``required_apis`` lets the launcher hand in the list it actually mounts;
    when omitted the task's own declaration is read, which is what an authoring
    preflight wants. Runs without docker and without a model, in seconds.
    """
    started = time.monotonic()
    task_dir = Path(task_dir)
    env_dir = Path(environment_dir) if environment_dir else ENVIRONMENT_DIR
    findings: List[GateFinding] = list(_grading_findings(task_dir))
    ops = 0

    declared_required, distractors = _declared_apis(task_dir)
    required = list(required_apis) if required_apis is not None else declared_required
    findings.extend(_surface_findings(task_dir, required, FATAL, env_dir))
    findings.extend(_surface_findings(
        task_dir, [a for a in distractors if a not in set(required)], WARN, env_dir))

    inject_dir = task_dir / "inject"
    if inject_dir.is_dir():
        try:
            script = InjectScript.load(inject_dir)
        except InjectConfigError as exc:
            findings.append(GateFinding(FATAL, "INJECT-UNLOADABLE", task_dir.name, str(exc)))
            script = None
        if script is not None:
            findings.extend(_authoring_findings(script, task_dir, env_dir))
            findings.extend(_mtime_findings(script, task_dir))
            findings.extend(_payload_findings(script))
            replay, ops = _replay_findings(script, task_dir, env_dir)
            findings.extend(replay)

    return GateReport(task_dir.name, tuple(findings), ops,
                      int((time.monotonic() - started) * 1000))
