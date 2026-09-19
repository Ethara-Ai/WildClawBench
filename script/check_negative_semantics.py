#!/usr/bin/env python3
"""Audit negative-weight rubric polarity in delivered ``report.json`` files.

A negative-weight criterion is a violation checker: its ``satisfied`` verdict
tracks the LITERAL criterion text ("the agent did the forbidden thing"), and
the aggregator applies the sign afterwards
(``src/utils/grading.py:_criterion_pass_from_satisfied``)::

    passed = (not satisfied) if weight < 0 else satisfied

so a negative criterion that PASSED means the guardrail held and contributes
NOTHING, while one that FAILED subtracts ``|weight|``. The reward is::

    reward% = (Σ passed positive weights − Σ |weight| of failed negatives)
              / Σ positive weights × 100

Bundled ``report.json`` carries only ``score`` (the weight), ``is_positive``
and ``passed`` — the ``satisfied`` verdict is gone — so a downstream consumer
that reads ``passed`` uniformly as "satisfied" silently inverts every
guardrail. This script recomputes each run's reward under BOTH readings and
compares them against the stored percentage to prove which one the artifact
actually encodes.

CLASSES (one per run)
  ok                 violation-absent convention reproduces the stored reward
  NO_NEGATIVES       run has no negative weights — the two readings coincide,
                     stored reward still verified
  INVERSION          the stored reward matches the INVERTED reading: negative
                     criteria were charged when their guardrail HELD
  ABSTAIN_AMBIGUOUS  neither reading matches, but dropping some subset of the
                     failed negative criteria does — the signature of council
                     abstentions flattened to ``passed: false`` with no
                     surviving marker, which is arithmetically identical to a
                     penalty. Not decidable from report.json alone; re-run with
                     the source ``score.json`` present, or repackage with the
                     ``abstained`` marker (script/repackage_to_bundle.py)
  MISMATCH           the stored reward reproduces under neither reading and no
                     abstention subset explains the gap

ABSTENTIONS
  A criterion the judge council could not resolve is recorded
  ``resolved_by: "human_eval"`` and contributes 0 to the numerator. Bundling
  flattens it to ``passed: false``. For a POSITIVE weight that is harmless (a
  failed positive also contributes 0), but for a NEGATIVE weight it is not:
  ``passed: false`` reads as "violation occurred", charging a penalty the
  grader never applied. Abstentions are recovered, in order, from
    1. a sibling ``score.json`` (``resolved_by == "human_eval"``), else
    2. the ``abstained: true`` marker on report.json rubric entries.
  Neither present and the arithmetic short → ABSTAIN_AMBIGUOUS.

ADVISORY FLAGS (reported, never fatal)
  sign         ``is_positive`` disagrees with ``sign(score)``
  xcheck       sibling score.json ``satisfied`` + sign does not reproduce the
               report's ``passed`` (human_eval criteria are skipped)
  blend        ``final_reward`` is not the documented blend of the channels

USAGE
    python3 script/check_negative_semantics.py output_bundle
    python3 script/check_negative_semantics.py --quiet delivery-1 delivery-2
Exit code is 1 when any run classified INVERSION or MISMATCH.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# Stored percentages are round(x, 2); anything inside half a last place is the
# same number. Comparisons are made in percentage units throughout.
_TOL = 0.011

_ABSTAIN_RESOLVERS = ("human_eval",)


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _weight(entry: dict[str, Any]) -> float:
    try:
        return float(entry.get("score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_abstained_criterion(c: dict[str, Any]) -> bool:
    """True for a score.json criterion the council could not resolve."""
    if c.get("resolved_by") in _ABSTAIN_RESOLVERS:
        return True
    return str(c.get("human_eval") or "") == "required"


def _criterion_index(entry: dict[str, Any], position: int) -> int:
    """Rubric ``number`` is ``R{score.json id + 1}``; fall back to position."""
    number = str(entry.get("number") or "")
    if number.startswith("R") and number[1:].isdigit():
        return int(number[1:]) - 1
    return position


def _score_criteria_by_index(score: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(score, dict):
        return out
    criteria = score.get("criteria")
    if not isinstance(criteria, list):
        return out
    for position, c in enumerate(criteria):
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        out[int(cid) if isinstance(cid, int) else position] = c
    return out


def _abstained_positions(
    rubric: list[dict[str, Any]], by_index: dict[int, dict[str, Any]]
) -> tuple[set[int], str]:
    """Positions in ``rubric`` that were council abstentions, and the source.

    score.json wins when present because it is complete: the ``abstained``
    marker is omitted (not emitted false) to keep the report.json schema
    stable, so an all-clear run is indistinguishable from an emitter that
    never learned the marker.
    """
    if by_index:
        positions = {
            position
            for position, entry in enumerate(rubric)
            if _is_abstained_criterion(by_index.get(_criterion_index(entry, position), {}))
        }
        return positions, "score.json"
    positions = {
        position
        for position, entry in enumerate(rubric)
        if entry.get("abstained") is True
    }
    return (positions, "marker") if positions else (set(), "")


def _numerators(
    rubric: list[dict[str, Any]], abstained: set[int]
) -> tuple[float, float, float]:
    """(violation-absent numerator, inverted numerator, positive-weight denom).

    Violation-absent: ``satisfied = passed`` for a positive weight and
    ``not passed`` for a negative one, then every satisfied weight is summed —
    exactly the aggregator. Inverted: ``satisfied = passed`` for every weight,
    which charges a negative criterion precisely when its guardrail held.
    """
    absent = inverted = denom = 0.0
    for position, entry in enumerate(rubric):
        weight = _weight(entry)
        if weight > 0:
            denom += weight
        if position in abstained:
            continue
        passed = bool(entry.get("passed", False))
        if passed if weight >= 0 else not passed:
            absent += weight
        if passed:
            inverted += weight
    return absent, inverted, denom or 1.0


def _reachable_sums(values: Iterable[float], cap: int = 20) -> list[float]:
    """Every subset sum of ``values`` (deduplicated, ``cap`` guards blowup)."""
    sums = {0.0}
    for i, v in enumerate(values):
        if i >= cap:
            break
        sums |= {s + v for s in sums}
    return sorted(sums)


def _stored_percentage(report: dict[str, Any]) -> float | None:
    """Rubric channel percentage; rubric-only bundles carry it as final_reward."""
    for key in ("rubric_weights_percentage", "final_reward"):
        value = report.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _blend_flag(report: dict[str, Any]) -> str:
    """final_reward is the two-channel mean, or the rubric alone when Channel A
    did not run (rubric-only bundles emit no ``pytest`` block)."""
    final = report.get("final_reward")
    rubric_pct = report.get("rubric_weights_percentage")
    if not isinstance(final, (int, float)) or not isinstance(rubric_pct, (int, float)):
        return ""
    if "pytest" in report:
        expected = round((float(report.get("test_weights_percentage", 0.0))
                          + float(rubric_pct)) / 2.0, 2)
    else:
        expected = round(float(rubric_pct), 2)
    if abs(float(final) - expected) > _TOL:
        return f"blend(final_reward={final} expected={expected})"
    return ""


def _sign_flags(rubric: list[dict[str, Any]]) -> list[str]:
    flags = []
    for position, entry in enumerate(rubric):
        weight = _weight(entry)
        declared = entry.get("is_positive")
        if isinstance(declared, bool) and declared != (weight >= 0):
            flags.append(
                f"sign({entry.get('number') or position}: "
                f"is_positive={declared} score={int(weight)})"
            )
    return flags


def _xcheck_flags(
    rubric: list[dict[str, Any]], by_index: dict[int, dict[str, Any]]
) -> list[str]:
    """Report ``passed`` must be score.json ``satisfied`` with the sign applied."""
    flags = []
    for position, entry in enumerate(rubric):
        c = by_index.get(_criterion_index(entry, position))
        if not c or _is_abstained_criterion(c):
            continue
        satisfied = bool(c.get("satisfied", False))
        weight = _weight(entry)
        expected = (not satisfied) if weight < 0 else satisfied
        if bool(entry.get("passed", False)) != expected:
            flags.append(
                f"xcheck({entry.get('number') or position}: "
                f"passed={entry.get('passed')} satisfied={satisfied} "
                f"score={int(weight)})"
            )
    return flags


def check_run(report_path: Path) -> dict[str, Any]:
    """Classify one run. ``status`` is one of the CLASSES in the module docstring
    plus ``SKIP`` for a report this checker cannot evaluate."""
    result: dict[str, Any] = {
        "path": report_path, "status": "SKIP", "detail": "", "flags": [],
        "negatives": 0, "stored": None, "absent": None, "inverted": None,
        "abstain_source": "", "abstained": 0,
    }
    report = _load_json(report_path)
    if not isinstance(report, dict):
        result["detail"] = "unreadable report.json"
        return result
    rubric = report.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        result["detail"] = "no rubric array"
        return result
    rubric = [e for e in rubric if isinstance(e, dict)]

    stored = _stored_percentage(report)
    if stored is None:
        result["detail"] = "no stored rubric percentage"
        return result

    score = _load_json(report_path.parent / "score.json")
    by_index = _score_criteria_by_index(score)
    abstained, abstain_source = _abstained_positions(rubric, by_index)

    absent_num, inverted_num, denom = _numerators(rubric, abstained)
    absent_pct = round(absent_num / denom * 100.0, 2)
    inverted_pct = round(inverted_num / denom * 100.0, 2)
    negatives = [e for e in rubric if _weight(e) < 0]

    result.update({
        "stored": stored, "absent": absent_pct, "inverted": inverted_pct,
        "negatives": len(negatives), "abstained": len(abstained),
        "abstain_source": abstain_source,
    })
    result["flags"] = _sign_flags(rubric) + _xcheck_flags(rubric, by_index)
    blend = _blend_flag(report)
    if blend:
        result["flags"].append(blend)

    if abs(absent_pct - stored) <= _TOL:
        result["status"] = "NO_NEGATIVES" if not negatives else "ok"
        return result

    # Unmarked abstentions only ever move the numerator UP, by the |weight| of
    # negative criteria flattened to passed=false. Positive abstentions are
    # invisible here: a failed positive contributes 0 either way.
    gap = stored / 100.0 * denom - absent_num
    dropped = [-_weight(e) for e in rubric
               if _weight(e) < 0 and not bool(e.get("passed", False))]
    abstain_explains = bool(
        not abstain_source and dropped
        and any(abs(gap - s) / denom * 100.0 <= _TOL
                for s in _reachable_sums(dropped) if s)
    )

    # The inverted reading is only DISTINGUISHABLE from flattened abstentions
    # when some negative criterion passed: charging a held guardrail pushes the
    # numerator DOWN, which no abstention can do. With every negative failed,
    # "inverted" and "all negatives abstained" are the same arithmetic — that is
    # the whole-run council failure, and it must not be called an inversion.
    matches_inverted = bool(negatives) and abs(inverted_pct - stored) <= _TOL
    charged_held_guardrail = any(
        _weight(e) < 0 and bool(e.get("passed", False)) for e in rubric
    )
    if matches_inverted and (charged_held_guardrail or not abstain_explains):
        result["status"] = "INVERSION"
        result["detail"] = (
            f"stored {stored} reproduces only under the inverted reading "
            f"(violation-absent gives {absent_pct})"
        )
        return result
    if abstain_explains:
        result["status"] = "ABSTAIN_AMBIGUOUS"
        result["detail"] = (
            f"stored {stored} vs violation-absent {absent_pct}; gap "
            f"{gap:+.0f} weight point(s) is a subset of the failed negative "
            f"criteria — indistinguishable from flattened abstentions"
        )
        return result

    result["status"] = "MISMATCH"
    result["detail"] = (
        f"stored {stored} matches neither violation-absent {absent_pct} nor "
        f"inverted {inverted_pct}"
    )
    return result


def _report_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.name == "report.json" else []
    return sorted(root.glob("**/trajectories/*/run_*/report.json"))


def _task_label(report_path: Path) -> str:
    """``<task>/<model>/<run>`` — the run's identity inside a delivery root.

    Layout is ``<task>/trajectories/<model>/run_N/report.json``; the constant
    ``trajectories`` segment is dropped."""
    parts = report_path.parts
    if len(parts) >= 5:
        return "/".join((parts[-5], parts[-3], parts[-2]))
    return "/".join(parts[-3:-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="Delivery roots to walk")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print runs that are not plain ok/NO_NEGATIVES")
    args = ap.parse_args(argv)

    counts: dict[str, int] = {}
    flagged = fatal = 0
    for root in args.roots:
        rp = Path(root)
        if not rp.exists():
            print(f"!! skip (missing): {root}", file=sys.stderr)
            continue
        for report_path in _report_paths(rp):
            r = check_run(report_path)
            status = r["status"]
            counts[status] = counts.get(status, 0) + 1
            if r["flags"]:
                flagged += 1
            if status in ("INVERSION", "MISMATCH"):
                fatal += 1
            clean = status in ("ok", "NO_NEGATIVES") and not r["flags"]
            if args.quiet and clean:
                continue
            stored = "-" if r["stored"] is None else f"{r['stored']:7.2f}"
            absent = "-" if r["absent"] is None else f"{r['absent']:7.2f}"
            inverted = "-" if r["inverted"] is None else f"{r['inverted']:7.2f}"
            print(
                f"[{status:17s}] neg={r['negatives']:<2d} stored={stored} "
                f"absent={absent} inverted={inverted}  {_task_label(report_path)}"
            )
            if r["detail"]:
                print(f"    {r['detail']}")
            if r["abstain_source"]:
                print(f"    {r['abstained']} abstention(s) via {r['abstain_source']}")
            for f in r["flags"]:
                print(f"    flag  {f}")

    total = sum(counts.values())
    breakdown = ", ".join(f"{counts[k]} {k}" for k in sorted(counts)) or "none"
    print("-" * 70)
    print(f"{total} run(s) checked: {breakdown}; {flagged} with advisory flag(s)")
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
