"""End-of-run execution summary rendering (Rich table + stats panel).

Consumes the ``results`` list produced by ``eval/run_batch.py`` — a list of dicts
shaped like ``{"task_id", "scores": {...}, "usage": {...}, "error": ...}`` (see
``src/utils/grading.py::print_summary`` for the canonical producer) — and renders:

  * a per-task results table with a clear ✓ / ✗ indicator, final score, output
    tokens and cost, and any error, and
  * a summary panel with, in order: total / passed / failed task counts, pass &
    fail rates, wall-clock execution time, agents & sub-agents spawned, rubric
    criteria (total / passed / failed / pass rate), and test cases (total /
    passed / failed / pass rate).

A task counts as **passed** when it executed without error AND its final score
(``overall_score``; None when absent) is at least a cut-off score. The cut-off is
an internal classification detail only (overridable via the ``WCB_PASS_THRESHOLD``
env var); it is deliberately NOT surfaced anywhere in the rendered summary.
Everything else (agent error, grading error, no scores, sub-cutoff score) counts
as failed — which is surfaced explicitly in the table so the classification is
transparent.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .console import get_console
from .events import EV_SUMMARY, get_bus


def _pass_threshold() -> float:
    raw = os.environ.get("WCB_PASS_THRESHOLD", "0.5")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.5


def _final_score(scores: Dict[str, Any]) -> Optional[float]:
    """Final numeric score for a task, or None when there is no score.

    The canonical final score is ``overall_score`` — ``grading.py`` always emits
    it (a value in ``[0, 1]``), including on the error path
    (``{"overall_score": 0.0, "error": ...}``). We deliberately do NOT average
    the other numeric values in the dict: it also carries integer counts
    (``criteria_passed`` / ``criteria_total``) and a scaled duplicate
    (``rubric_weights_percentage`` = ``overall_score`` * 100), so a blind mean
    would fabricate a meaningless number. When ``overall_score`` is absent we
    return None, which ``classify`` reports honestly as "no scores".
    """
    if not isinstance(scores, dict):
        return None
    v = scores.get("overall_score")
    # bool is an int subclass; exclude it so a stray True can't become 1.0.
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def classify(result: Dict[str, Any], threshold: Optional[float] = None) -> Tuple[bool, Optional[float], str]:
    """Return ``(passed, final_score, reason)`` for one result dict."""
    if threshold is None:
        threshold = _pass_threshold()
    scores = result.get("scores") or {}
    if result.get("error"):
        return False, _final_score(scores), f"agent error: {result['error']}"
    if isinstance(scores, dict) and scores.get("error"):
        return False, _final_score(scores), f"grading error: {scores['error']}"
    score = _final_score(scores)
    if score is None:
        return False, None, "no scores"
    if score >= threshold:
        return True, score, ""
    # Reason conveys the qualitative fail cause WITHOUT exposing the numeric
    # cut-off — the summary never displays a pass-threshold value/metric.
    return False, score, f"score {score:.2f} below pass cut-off"


def _agent_spawned(result: Dict[str, Any]) -> bool:
    """True when the task launched its primary (main) agent.

    Each task spawns exactly one main agent; the only case where none is spawned
    is a container-startup failure, which the runner surfaces verbatim as
    "Container startup failed" in ``result['error']``.
    """
    err = result.get("error")
    if isinstance(err, str) and "Container startup failed" in err:
        return False
    return True


def _subagent_count(result: Dict[str, Any]) -> int:
    """Number of sub-agents this task spawned.

    The openclaw runner folds the spawn-tree row count into
    ``usage['subagent_count']`` (see runner ``_collect_output``); single-agent
    tasks never set the key, so the default of 0 is correct.
    """
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return 0
    try:
        return max(0, int(usage.get("subagent_count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _criteria_counts(scores: Dict[str, Any]) -> Tuple[int, int, int]:
    """(total, passed, failed) rubric criteria for one task (Channel B).

    Prefers the canonical integer counts grading.py always emits
    (``criteria_*``; ``tests_*`` are their deprecated aliases in score.json).
    Falls back to deriving from the per-criterion ``criteria`` list, excluding
    abstentions ("Human Evaluation required") from passed/failed the same way
    ``build_criteria_table`` does.
    """
    if not isinstance(scores, dict):
        return 0, 0, 0
    total = int(scores.get("criteria_total", scores.get("tests_total", 0)) or 0)
    passed = int(scores.get("criteria_passed", scores.get("tests_passed", 0)) or 0)
    failed = int(scores.get("criteria_failed", scores.get("tests_failed", 0)) or 0)
    if total == 0 and not passed and not failed:
        criteria = scores.get("criteria")
        if isinstance(criteria, list) and criteria:
            total = len(criteria)
            for c in criteria:
                if not isinstance(c, dict):
                    continue
                abstained = (
                    c.get("resolved_by") == "human_eval"
                    or str(c.get("human_eval") or "").strip() != ""
                )
                if abstained:
                    continue
                if bool(c.get("passed", c.get("satisfied"))):
                    passed += 1
                else:
                    failed += 1
    return total, passed, failed


def _test_case_counts(test_result: Dict[str, Any]) -> Tuple[int, int, int]:
    """(total, passed, failed) executed test cases for one task (Channel A)."""
    if not isinstance(test_result, dict):
        return 0, 0, 0
    total = int(test_result.get("tests_total", 0) or 0)
    passed = int(test_result.get("tests_passed", 0) or 0)
    failed = int(test_result.get("tests_failed", 0) or 0)
    return total, passed, failed


def compute_stats(results: List[Dict[str, Any]], threshold: Optional[float] = None) -> Dict[str, Any]:
    """Aggregate task, agent, rubric-criteria and test-case counts over ``results``.

    ``threshold`` is retained ONLY as the internal pass/fail cut-off used by
    ``classify``; it is never displayed in the rendered summary.
    """
    if threshold is None:
        threshold = _pass_threshold()
    total = len(results)
    passed = 0
    agents = subagents = 0
    rubric_total = rubric_passed = rubric_failed = 0
    tests_total = tests_passed = tests_failed = 0
    for r in results:
        ok, _, _ = classify(r, threshold)
        if ok:
            passed += 1
        if _agent_spawned(r):
            agents += 1
        subagents += _subagent_count(r)
        c_t, c_p, c_f = _criteria_counts(r.get("scores") or {})
        rubric_total += c_t
        rubric_passed += c_p
        rubric_failed += c_f
        t_t, t_p, t_f = _test_case_counts(r.get("test_result") or {})
        tests_total += t_t
        tests_passed += t_p
        tests_failed += t_f
    failed = total - passed
    pass_rate = (passed / total * 100.0) if total else 0.0
    fail_rate = (failed / total * 100.0) if total else 0.0
    rubric_pass_rate = (rubric_passed / rubric_total * 100.0) if rubric_total else 0.0
    tests_pass_rate = (tests_passed / tests_total * 100.0) if tests_total else 0.0
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "fail_rate": fail_rate,
        "agents_spawned": agents,
        "subagents_spawned": subagents,
        "rubric_total": rubric_total,
        "rubric_passed": rubric_passed,
        "rubric_failed": rubric_failed,
        "rubric_pass_rate": rubric_pass_rate,
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "tests_pass_rate": tests_pass_rate,
        # Internal-only classification cut-off; never rendered (see module docstring).
        "threshold": threshold,
    }


def _fmt_elapsed(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def build_results_table(results: List[Dict[str, Any]], threshold: Optional[float] = None):
    """Build a Rich Table of per-task results. Returns None if rich is absent."""
    try:
        from rich.table import Table
        from rich import box
    except Exception:  # pragma: no cover
        return None
    if threshold is None:
        threshold = _pass_threshold()

    # Caption makes the PASS/FAIL column self-documenting: it is a UI-side
    # classification, NOT part of the harness's scalar reward — the harness has
    # no binary pass/fail verdict. The numeric cut-off is deliberately not shown.
    table = Table(
        title="Task Results",
        caption="PASS/FAIL is a UI classification, not a harness scalar reward",
        caption_style="dim",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        expand=False,
    )
    table.add_column("", justify="center", no_wrap=True)
    table.add_column("Task", style="white", overflow="fold")
    table.add_column("Result", justify="left", no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Out tok", justify="right", no_wrap=True)
    table.add_column("Cost $", justify="right", no_wrap=True)
    table.add_column("Note", style="dim", overflow="fold")

    for r in sorted(results, key=lambda x: str(x.get("task_id", ""))):
        ok, score, reason = classify(r, threshold)
        usage = r.get("usage") or {}
        out_tok = usage.get("output_tokens", 0) or 0
        cost = usage.get("cost_usd", 0.0) or 0.0
        glyph = "[bold green]✓[/]" if ok else "[bold red]✗[/]"
        result_word = "[bold green]PASS[/]" if ok else "[bold red]FAIL[/]"
        score_str = f"{score:.2f}" if score is not None else "—"
        table.add_row(
            glyph,
            str(r.get("task_id", "<unknown>")),
            result_word,
            score_str,
            f"{int(out_tok):,}",
            f"{cost:.4f}",
            reason,
        )
    return table


def _short(text: str, n: int = 78) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_criteria_table(scores: Dict[str, Any]):
    """Per-rubric-criterion pass/fail table from a task's judge scores.

    Reads the ``criteria`` list the judge writes into score.json (grading.py),
    which each task result already carries in-memory as ``result["scores"]``.
    Returns None when there is no per-criterion detail or rich is unavailable.
    """
    if not isinstance(scores, dict):
        return None
    criteria = scores.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return None
    try:
        from rich.table import Table
        from rich import box
    except Exception:  # pragma: no cover
        return None

    table = Table(title="Rubric Criteria", box=box.MINIMAL, header_style="bold", expand=False)
    table.add_column("", justify="center", no_wrap=True)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Wt", justify="right", no_wrap=True)
    table.add_column("Criterion", overflow="fold")
    for c in criteria:
        if not isinstance(c, dict):
            continue
        passed = bool(c.get("passed", c.get("satisfied")))
        is_positive = c.get("is_positive", True)
        # A criterion abstains ("Human Evaluation required") when the council
        # couldn't resolve it: grading.py records resolved_by="human_eval" and
        # human_eval="required" (see _grade_council). Match that real schema —
        # the old "abstain" literal never appears in score.json. The human_eval
        # check stays as a defensive fallback; for real data both agree.
        abstained = (
            c.get("resolved_by") == "human_eval"
            or str(c.get("human_eval") or "").strip() != ""
        )
        if abstained:
            glyph, verdict = "[yellow]?[/]", "[yellow]HUMAN[/]"
        elif passed:
            glyph, verdict = "[bold green]✓[/]", "[green]PASS[/]"
        else:
            glyph, verdict = "[bold red]✗[/]", "[red]FAIL[/]"
        # Mark guardrail (negative-weight) criteria so a "fail" reads correctly.
        wt = c.get("weight", "")
        wt_str = (f"-{abs(float(wt)):g}" if not is_positive else f"{float(wt):g}") if wt != "" else ""
        table.add_row(glyph, str(c.get("id", "")), wt_str, f"{verdict}  {_short(c.get('criterion', ''))}")
    return table


def build_tests_table(test_result: Dict[str, Any]):
    """Per-test-case pass/fail table from a task's test-execution result.

    Normalizes ``result["test_result"]`` through the harness's own ``build_ctrf``
    so the pass/fail mapping is identical to ctrf.json. Returns None when there
    is no test detail or rich/ctrf is unavailable.
    """
    if not isinstance(test_result, dict):
        return None
    try:
        from rich.table import Table
        from rich import box
        from src.utils.harbor.ctrf import build_ctrf
    except Exception:  # pragma: no cover
        return None
    try:
        ctrf = build_ctrf(
            tests_total=test_result.get("tests_total", 0),
            tests_passed=test_result.get("tests_passed", 0),
            tests_failed=test_result.get("tests_failed", 0),
            tests_errored=test_result.get("tests_errored", 0),
            test_scores_json=test_result.get("test_scores", "{}"),
            tests_skipped=test_result.get("tests_skipped", 0),
            reward=test_result.get("reward"),
        )
        tests = ctrf.get("results", {}).get("tests") or []
    except Exception:
        return None
    if not tests:
        return None

    table = Table(title="Test Cases", box=box.MINIMAL, header_style="bold", expand=False)
    table.add_column("", justify="center", no_wrap=True)
    table.add_column("Test", overflow="fold")
    table.add_column("Status", no_wrap=True)
    for tc in tests:
        status = str(tc.get("status", "failed"))
        if status == "passed":
            glyph, color = "[bold green]✓[/]", "green"
        elif status in ("skipped", "pending"):
            glyph, color = "[yellow]—[/]", "yellow"
        else:
            glyph, color = "[bold red]✗[/]", "red"
        table.add_row(glyph, str(tc.get("name", "")), f"[{color}]{status}[/]")
    return table


def render_task_details(results: List[Dict[str, Any]], console) -> None:
    """Print per-task rubric-criteria and test-case breakdown tables."""
    if console is None:
        return
    try:
        from rich.panel import Panel
        from rich.console import Group
    except Exception:  # pragma: no cover
        return
    for r in sorted(results, key=lambda x: str(x.get("task_id", ""))):
        crit = build_criteria_table(r.get("scores") or {})
        tests = build_tests_table(r.get("test_result") or {})
        parts = [t for t in (crit, tests) if t is not None]
        if not parts:
            continue
        console.print(Panel(Group(*parts), title=f"Details — {r.get('task_id', '<unknown>')}",
                            border_style="cyan", expand=False))


def build_summary_panel(stats: Dict[str, Any], elapsed_seconds: Optional[float] = None):
    """Build a Rich Panel with the aggregate stats. Returns None if rich absent."""
    try:
        from rich.panel import Panel
        from rich.table import Table
        from rich import box
    except Exception:  # pragma: no cover
        return None

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="bold")
    grid.add_column(justify="left")
    # --- Tasks ---
    grid.add_row("Total tasks", str(stats["total"]))
    grid.add_row("Passed", f"[bold green]{stats['passed']}[/]")
    grid.add_row("Failed", f"[bold red]{stats['failed']}[/]")
    grid.add_row("Pass rate", f"[bold green]{stats['pass_rate']:.1f}%[/]")
    grid.add_row("Fail rate", f"[bold red]{stats['fail_rate']:.1f}%[/]")
    grid.add_row("Execution time", _fmt_elapsed(elapsed_seconds))
    # --- Agents ---
    grid.add_row("", "")
    grid.add_row("Agents spawned", str(stats["agents_spawned"]))
    grid.add_row("Sub-agents spawned", str(stats["subagents_spawned"]))
    # --- Rubric criteria (Channel B) ---
    grid.add_row("", "")
    grid.add_row("Rubric criteria", str(stats["rubric_total"]))
    grid.add_row("  passed", f"[green]{stats['rubric_passed']}[/]")
    grid.add_row("  failed", f"[red]{stats['rubric_failed']}[/]")
    grid.add_row("Rubric pass rate", f"{stats['rubric_pass_rate']:.1f}%")
    # --- Test cases (Channel A) ---
    grid.add_row("", "")
    grid.add_row("Test cases", str(stats["tests_total"]))
    grid.add_row("  passed", f"[green]{stats['tests_passed']}[/]")
    grid.add_row("  failed", f"[red]{stats['tests_failed']}[/]")
    grid.add_row("Test case pass rate", f"{stats['tests_pass_rate']:.1f}%")

    border = "green" if stats["failed"] == 0 and stats["total"] > 0 else (
        "red" if stats["failed"] else "cyan"
    )
    return Panel(
        grid,
        title="Execution Summary",
        border_style=border,
        box=box.ROUNDED,
        expand=False,
    )


def render_execution_summary(
    results: List[Dict[str, Any]],
    elapsed_seconds: Optional[float] = None,
    console=None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Render the results table + summary panel to the console.

    Returns the computed stats dict (handy for tests / callers). Falls back to a
    plain-text summary when rich is unavailable. Also publishes an ``EV_SUMMARY``
    event so the Textual dashboard can render it in-app.
    """
    if threshold is None:
        threshold = _pass_threshold()
    stats = compute_stats(results, threshold)

    try:
        get_bus().emit(EV_SUMMARY, stats=stats, elapsed_seconds=elapsed_seconds, results=results)
    except Exception:
        pass

    # When the Textual dashboard owns the terminal it renders the summary in-app
    # from the EV_SUMMARY event above; printing to the shared console here would
    # corrupt the full-screen canvas, so stop after emitting.
    from . import lifecycle as _lifecycle
    if console is None and _lifecycle.is_dashboard_active():
        return stats

    console = console or get_console()
    table = build_results_table(results, threshold)
    panel = build_summary_panel(stats, elapsed_seconds)

    if console is None or table is None or panel is None:
        # Plain-text fallback (rich unavailable).
        print("\n=== Execution Summary ===")
        print(f"Total tasks         : {stats['total']}")
        print(f"Passed              : {stats['passed']}")
        print(f"Failed              : {stats['failed']}")
        print(f"Pass rate           : {stats['pass_rate']:.1f}%")
        print(f"Fail rate           : {stats['fail_rate']:.1f}%")
        print(f"Execution time      : {_fmt_elapsed(elapsed_seconds)}")
        print(f"Agents spawned      : {stats['agents_spawned']}")
        print(f"Sub-agents spawned  : {stats['subagents_spawned']}")
        print(f"Rubric criteria     : {stats['rubric_total']}")
        print(f"  passed            : {stats['rubric_passed']}")
        print(f"  failed            : {stats['rubric_failed']}")
        print(f"Rubric pass rate    : {stats['rubric_pass_rate']:.1f}%")
        print(f"Test cases          : {stats['tests_total']}")
        print(f"  passed            : {stats['tests_passed']}")
        print(f"  failed            : {stats['tests_failed']}")
        print(f"Test case pass rate : {stats['tests_pass_rate']:.1f}%")
        _print_details_plain(results)
        return stats

    console.print()
    console.print(table)
    console.print(panel)
    render_task_details(results, console)
    return stats


def _print_details_plain(results: List[Dict[str, Any]]) -> None:
    """Plain-text per-task rubric + test breakdown (rich-unavailable fallback)."""
    import json as _json
    for r in results:
        tid = r.get("task_id", "<unknown>")
        scores = r.get("scores") or {}
        criteria = scores.get("criteria") if isinstance(scores, dict) else None
        if isinstance(criteria, list) and criteria:
            print(f"\n  Rubric criteria [{tid}]:")
            for c in criteria:
                if not isinstance(c, dict):
                    continue
                mark = "PASS" if c.get("passed", c.get("satisfied")) else "FAIL"
                print(f"    [{mark}] #{c.get('id','')} {_short(c.get('criterion',''))}")
        tr = r.get("test_result") or {}
        raw = tr.get("test_scores") if isinstance(tr, dict) else None
        try:
            tmap = _json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception:
            tmap = {}
        if tmap:
            print(f"  Test cases [{tid}]:")
            for name, status in tmap.items():
                st = status if isinstance(status, str) else (status or {}).get("status", "failed")
                mark = "PASS" if st == "passed" else "FAIL"
                print(f"    [{mark}] {str(name).split('::')[-1]} ({st})")
    return
