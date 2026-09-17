"""Acceptance checks a reconstruction has to clear before it is trusted.

A reconstruction that merely produces files is worth very little; the question
is whether the tree loads, dates and grades the same way the bundle it came
from did. Each gate answers one of those, and each says which side it compared.

  G1  the loader reads as many turns as the header declares
  G2  every turn resolves a simulated clock, matching the run when one exists
  G3  preflight passes on the reconstructed tree
  G4  no mock module differs from the baseline
  G5  the overlay stays inside the task's declared API surface
  G6  every turn is byte-identical to the bundle's own prompt file
  G7  the rubric is the bundle's rubric
  G8  every staged attachment came back
  G9  the inject script loads on both sides with the same stage boundaries
  G10 the persona set is complete

G4 and G5 are release-blocking by owner decision: a module that no longer
matches the harness will not reproduce the run, and an overlay outside the
declared surface is the fifty-API failure that prompted this work.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
STRICT, WARN_ONLY, OFF = "strict", "warn", "off"

#: Gates that only ever advise, whatever the mode.
ADVISORY = {"G10"}


@dataclass
class Gate:
    id: str
    name: str
    status: str
    detail: str = ""

    def line(self) -> str:
        return f"{self.id} {self.status:<4} {self.name} — {self.detail}"


@dataclass
class GateContext:
    """Everything the gates compare, gathered by the orchestrator."""

    bundle: Path
    out_dir: Path
    prompts: object = None
    instants: object = None
    meta: object = None
    mock: object = None
    drift: dict = field(default_factory=dict)
    legacy: bool = False
    rubric_override: str = ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def g1_turn_count(ctx: GateContext) -> Gate:
    from src.utils.task_parser import load_task

    if ctx.prompts is None:
        return Gate("G1", "loader turn count", SKIP, "no prompt file recovered")
    declared = int(ctx.prompts.header.get("turn_count") or 0)
    try:
        task = load_task(ctx.out_dir)
    except Exception as exc:
        return Gate("G1", "loader turn count", FAIL, f"load_task raised: {exc}")
    loaded = len(task.get("turn_messages") or [])
    if loaded == declared and declared > 0:
        return Gate("G1", "loader turn count", PASS,
                    f"load_task reads {loaded} turn(s), header declares {declared}")
    return Gate("G1", "loader turn count", FAIL,
                f"load_task reads {loaded} turn(s) but the header declares {declared}")


def g2_sim_clock(ctx: GateContext) -> Gate:
    from src.utils.sim_clock import compute_sim_clock_for_turn
    from src.utils.task_parser import load_task

    if ctx.prompts is None or not ctx.instants.values:
        return Gate("G2", "simulated clock", FAIL,
                    "no prompts.json was written, so no turn carries an instant")
    try:
        task = load_task(ctx.out_dir)
    except Exception as exc:
        return Gate("G2", "simulated clock", FAIL, f"load_task raised: {exc}")
    total = len(ctx.prompts.turns)
    resolved, mismatched = 0, []
    for i, expected in enumerate(ctx.instants.values):
        clock = compute_sim_clock_for_turn(task, i)
        if clock is None:
            continue
        resolved += 1
        if int(expected.timestamp() * 1000) != clock.epoch_ms:
            mismatched.append(i)
    if resolved != total:
        return Gate("G2", "simulated clock", FAIL,
                    f"{resolved}/{total} turn(s) resolve a simulated clock")
    if mismatched:
        return Gate("G2", "simulated clock", FAIL,
                    f"turn(s) {mismatched[:5]} resolve to an instant other than "
                    f"{ctx.instants.source}")
    status = PASS if ctx.instants.fidelity == "exact" else WARN
    return Gate("G2", "simulated clock", status,
                f"{total}/{total} turn(s) resolve, matching {ctx.instants.source} "
                f"({ctx.instants.fidelity})")


#: preflight reports the generated-test channel from three places — the
#: structural file list, the compile check and the weights check. That channel
#: is retired and the reconstruction deliberately does not write it back, so
#: under --legacy every complaint about those two files is the expected residue
#: of the decision rather than a defect in the tree. Matched on the filename so
#: a reworded message cannot quietly turn back into a failure.
RETIRED_TEST_FILES = ("test_outputs.py", "test_weights.json")


def _is_retired_test_complaint(line: str) -> bool:
    return any(line.startswith(name) for name in RETIRED_TEST_FILES)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def g3_preflight(ctx: GateContext) -> Gate:
    script = Path(__file__).resolve().parents[3] / "script" / "preflight_task.py"
    cmd = [sys.executable, str(script), str(ctx.out_dir)]
    if ctx.legacy:
        cmd.append("--legacy")
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return Gate("G3", "preflight", FAIL, f"could not run preflight: {exc}")
    waived = " (--legacy)" if ctx.legacy else ""
    if done.returncode == 0:
        return Gate("G3", "preflight", PASS, f"preflight_task exits 0{waived}")
    fails = [_ANSI.sub("", ln).replace("✘", "").strip()
             for ln in done.stdout.splitlines() if "✘" in ln]
    if ctx.legacy:
        fails = [f for f in fails if not _is_retired_test_complaint(f)]
        if not fails:
            return Gate("G3", "preflight", PASS,
                        "preflight_task reports only the retired generated-test "
                        "files, which are deliberately not written back (--legacy)")
    return Gate("G3", "preflight", FAIL,
                f"preflight_task exits {done.returncode}{waived}: "
                f"{'; '.join(fails[:3]) or 'see its output'}")


def g4_module_drift(ctx: GateContext) -> Gate:
    stale = list(ctx.drift.get("stale") or [])
    if not stale:
        return Gate("G4", "mock module drift", PASS,
                    f"{ctx.drift.get('module_count', 0)} module(s) match "
                    f"{ctx.drift.get('source_environment', 'the baseline')}")
    return Gate("G4", "mock module drift", FAIL,
                f"{len(stale)} module(s) differ from the baseline: "
                f"{', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}")


def g5_overlay_containment(ctx: GateContext) -> Gate:
    if ctx.mock is None:
        return Gate("G5", "overlay containment", SKIP, "no overlay was searched")
    if not ctx.mock.scope_proven:
        return Gate("G5", "overlay containment", FAIL,
                    "the task declares no API surface, so containment cannot be "
                    "shown; the overlay spans whatever the bundle staged")
    declared = ctx.meta.scoped_apis if ctx.meta is not None else set()
    escaped = sorted(set(ctx.mock.overlays) - set(declared))
    if escaped:
        return Gate("G5", "overlay containment", FAIL,
                    f"overlay touches undeclared API(s): {', '.join(escaped[:5])}")
    return Gate("G5", "overlay containment", PASS,
                f"{len(ctx.mock.overlays)} overlaid api(s) all within the "
                f"{len(declared)} declared")


def g6_turn_fidelity(ctx: GateContext) -> Gate:
    from src.utils.inject_director import parse_prompts_file

    if ctx.prompts is None:
        return Gate("G6", "turn fidelity", SKIP, "no prompt file recovered")
    try:
        ours = parse_prompts_file(ctx.out_dir / "prompts.txt")
    except Exception as exc:
        return Gate("G6", "turn fidelity", FAIL, f"prompts.txt unreadable: {exc}")
    theirs = [t.text for t in ctx.prompts.turns]
    if len(ours) != len(theirs):
        return Gate("G6", "turn fidelity", FAIL,
                    f"reconstructed {len(ours)} turn(s) from the bundle's {len(theirs)}")
    differing = [i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b]
    if differing:
        return Gate("G6", "turn fidelity", FAIL,
                    f"turn(s) {differing[:5]} differ from {ctx.prompts.source.rel}")
    return Gate("G6", "turn fidelity", PASS,
                f"all {len(ours)} turn(s) identical to {ctx.prompts.source.rel}")


def g7_rubric(ctx: GateContext) -> Gate:
    theirs, ours = ctx.bundle / "rubric.json", ctx.out_dir / "rubric.json"
    if ctx.rubric_override:
        return Gate("G7", "rubric identity", WARN,
                    f"rubric replaced on request from {ctx.rubric_override}")
    if not theirs.is_file():
        return Gate("G7", "rubric identity", SKIP, "the bundle ships no rubric")
    if not ours.is_file():
        return Gate("G7", "rubric identity", FAIL, "no rubric was recovered")
    digest = _sha256(theirs)
    if digest != _sha256(ours):
        return Gate("G7", "rubric identity", FAIL,
                    "the recovered rubric is not the bundle's")
    return Gate("G7", "rubric identity", PASS, f"sha256 {digest[:12]} matches")


def g8_attachment_parity(ctx: GateContext) -> Gate:
    from script.lib.recon.sources import ARTIFACTS_SUBPATH

    staged = ctx.bundle.joinpath(*ARTIFACTS_SUBPATH)
    if not staged.is_dir():
        return Gate("G8", "attachment parity", SKIP, "the bundle stages no inputs")
    theirs = {p.relative_to(staged).as_posix()
              for p in staged.rglob("*") if p.is_file() and p.name != ".DS_Store"}
    ours_root = ctx.out_dir / "data"
    ours = {p.relative_to(ours_root).as_posix()
            for p in ours_root.rglob("*") if p.is_file()} if ours_root.is_dir() else set()
    missing = sorted(theirs - ours)
    if missing:
        return Gate("G8", "attachment parity", FAIL,
                    f"{len(missing)} staged file(s) not recovered: "
                    f"{', '.join(missing[:3])}")
    return Gate("G8", "attachment parity", PASS,
                f"all {len(theirs)} staged file(s) recovered at their published paths")


def g9_inject_parity(ctx: GateContext) -> Gate:
    from src.utils.inject_director import InjectScript

    theirs_dir, ours_dir = ctx.bundle / "inject", ctx.out_dir / "inject"
    if not theirs_dir.is_dir():
        return Gate("G9", "inject parity", SKIP, "the bundle ships no inject spec")
    try:
        theirs = InjectScript.load(theirs_dir)
        ours = InjectScript.load(ours_dir)
    except Exception as exc:
        return Gate("G9", "inject parity", FAIL, f"InjectScript.load raised: {exc}")
    shape = lambda s: [(st.index, st.from_turn, st.to_turn) for st in s.stages]
    if shape(theirs) != shape(ours):
        return Gate("G9", "inject parity", FAIL,
                    f"stage boundaries differ: {shape(ours)} != {shape(theirs)}")
    return Gate("G9", "inject parity", PASS,
                f"{len(ours.stages)} stage(s) load with identical boundaries")


def g10_persona(ctx: GateContext) -> Gate:
    from script.lib.recon.sources import PERSONA_FILE_COUNT

    persona = ctx.out_dir / "persona"
    found = sorted(p.name for p in persona.iterdir() if p.is_file()) \
        if persona.is_dir() else []
    if len(found) == PERSONA_FILE_COUNT:
        return Gate("G10", "persona completeness", PASS,
                    f"all {PERSONA_FILE_COUNT} persona file(s) present")
    return Gate("G10", "persona completeness", WARN,
                f"{len(found)} persona file(s), expected {PERSONA_FILE_COUNT}")


ALL_GATES = (g1_turn_count, g2_sim_clock, g3_preflight, g4_module_drift,
             g5_overlay_containment, g6_turn_fidelity, g7_rubric,
             g8_attachment_parity, g9_inject_parity, g10_persona)


def run(ctx: GateContext, mode: str = STRICT) -> list:
    """Run every gate, downgrading failures to warnings outside strict mode."""
    if mode == OFF:
        return []
    results = []
    for gate in ALL_GATES:
        try:
            outcome = gate(ctx)
        except Exception as exc:
            outcome = Gate(gate.__name__.split("_")[0].upper(), gate.__name__,
                           FAIL, f"gate raised: {exc}")
        if outcome.status == FAIL and (mode == WARN_ONLY or outcome.id in ADVISORY):
            outcome.status = WARN
        results.append(outcome)
    return results


def failures(results) -> list:
    return [g for g in results if g.status == FAIL]
