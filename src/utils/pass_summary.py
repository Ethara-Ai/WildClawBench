"""Single source of truth for `pass_summary.json` — entry, doc, and locked write.

Before this module there were THREE hand-synced copies of the same rollup
pipeline, and they had already drifted:

  * ``eval/run_batch.py``            — the live batch writer (read-modify-write
                                       of one rep's entry into the per-model doc).
  * ``script/backfill_pass_summary.py`` — the production repair writer, wired into
                                       ``script/run.sh`` (3 sites), ``deliver.sh``
                                       and ``script/regrade.py``.
  * ``script/rebuild_pass_summary.py``  — an operator CLI with a *reward.txt-first*
                                       scalar-reward precedence, versus backfill's
                                       *overall_score-first*.

Because ``run.sh`` runs backfill across the ENTIRE backend output tree after every
batch, any divergence between the writers was a bulk-rewrite hazard: whichever
copy ran last silently restated every historical summary under its own rules.

CANONICAL SEMANTICS = the live batch path (``eval/run_batch.py``). Everything
here preserves exactly what a batch produces today, with three deliberate fixes
applied ONCE, here, instead of three times:

  1. the ungraded predicate is shared (``is_ungraded``) so a dead judge is
     classified identically by the batch writer, both repair scripts and the
     aggregator — and stays excluded even under ``WCB_INCLUDE_INVALID_RUNS=1``;
  2. the write is atomic (temp file in the same dir + ``os.replace``) and a
     corrupt existing file is quarantined instead of silently reset to ``{}``;
  3. an empty / all-excluded ``per_run`` no longer fabricates a bare
     ``average_reward: 0.0`` with no marker.

ON THE reward.txt-vs-overall_score PRECEDENCE (resolved in favour of
overall_score-first): both artifacts are written from the SAME in-memory value —
``run_batch.py`` writes ``reward.txt`` as ``f"{te['reward']:.6f}"`` and hands the
same ``te['reward']`` to ``harbor.ctrf.build_ctrf``, which stores it as
``round(float(reward), 4)``. The two sources can therefore never disagree in
meaning, only in decimal precision, so there is no scenario in which reading one
first is *semantically* correct and the other wrong. overall_score-first wins
because it is what the production writer (backfill) has always used: adopting it
makes ``run.sh``'s tree-wide rewrite a no-op on already-correct trees, whereas
adopting reward.txt-first would shift every historical average by up to 5e-5 on
the next run. reward.txt remains the fallback, so a ctrf without a scalar still
resolves to the more precise number.
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.utils.grading import grading_failure_reason

logger = logging.getLogger(__name__)

PASS_SUMMARY_FILENAME = "pass_summary.json"
PASS_SUMMARY_LOCK_FILENAME = ".pass_summary.lock"
RUN_DIR_RE = re.compile(r"^run_(\d+)$")

__all__ = [
    "PASS_SUMMARY_FILENAME",
    "PASS_SUMMARY_LOCK_FILENAME",
    "RUN_DIR_RE",
    "PassSummaryCorruptError",
    "atomic_write_text",
    "ctrf_test_result",
    "discover_run_dirs",
    "finite_float",
    "include_incomplete_runs",
    "include_invalid_runs",
    "is_ungraded",
    "load_existing_doc",
    "load_json_or_none",
    "locked",
    "mean_or_none",
    "pass_summary_doc",
    "pass_summary_entry",
    "pass_summary_text",
    "rebuild_model_dir",
    "run_exclusion_reason",
    "upsert_pass_summary",
    "write_pass_summary",
]


class PassSummaryCorruptError(RuntimeError):
    """An existing ``pass_summary.json`` could not be parsed.

    The unreadable file is moved aside FIRST (see ``quarantined``) so the prior
    run history is never discarded, then this is raised: a read-modify-write that
    silently fell back to ``{}`` would drop every previously recorded rep and
    republish the doc as if only the current rep had ever run.
    """

    def __init__(self, path: Path, quarantined: Path, detail: str) -> None:
        self.path = Path(path)
        self.quarantined = Path(quarantined)
        self.detail = detail
        super().__init__(
            f"corrupt pass_summary at {self.path}: {detail}; prior history "
            f"preserved at {self.quarantined} — refusing to discard it"
        )


# --------------------------------------------------------------------------- #
# numeric helpers
# --------------------------------------------------------------------------- #
def finite_float(v: Any) -> float | None:
    """Return ``v`` as a float iff it is a finite real number, else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
        return float(v)
    return None


def mean_or_none(vals) -> float | None:
    nums = [v for v in vals if v is not None]
    return (sum(nums) / len(nums)) if nums else None


def load_json_or_none(path: Path):
    """Best-effort JSON read used for per-run artifacts (score.json, ctrf.json).

    Unlike :func:`load_existing_doc` a miss here is routine and carries no
    history: a run with no ``score.json`` is an ordinary, expected state.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# locking + durable write
# --------------------------------------------------------------------------- #
@contextmanager
def locked(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for the duration of the block.

    ``fcntl.flock`` is advisory and cross-process on the same host — enough to
    serialize the read-modify-write of shared per-model files (pass_summary.json)
    when reps for one (task, model) run in parallel. Advisory locks only exclude
    when every writer names the SAME file, which is why the batch writer, the
    repair scripts and regrade all route through this one function.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so a crash can never leave a partial file.

    ``Path.write_text`` truncates the destination and then streams into it: a
    kill (or a full disk) mid-write leaves a TRUNCATED pass_summary.json, which
    the next reader cannot parse — the whole rollup history for that model dir
    is gone. Here the bytes land in a sibling temp file in the SAME directory
    (so ``os.replace`` stays within one filesystem and is therefore atomic), are
    fsync'd, and only then replace the destination. A reader either sees the
    complete old file or the complete new one, never a splice of the two.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid-tagged so two processes that somehow bypass the lock cannot share a
    # temp file and interleave their bytes into it.
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _quarantine_corrupt(path: Path) -> Path:
    """Move an unparseable pass_summary aside, returning the new location."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    dst = path.with_name(f"{path.name}.corrupt.{stamp}")
    suffix = 0
    while dst.exists():
        suffix += 1
        dst = path.with_name(f"{path.name}.corrupt.{stamp}.{suffix}")
    os.replace(path, dst)
    return dst


def load_existing_doc(path: Path) -> dict:
    """Read an existing pass_summary doc for a read-modify-write.

    Returns ``{}`` when the file simply does not exist (the first rep of a batch).
    A file that exists but does NOT parse is quarantined and
    :class:`PassSummaryCorruptError` is raised — the historical behavior here was
    ``except json.JSONDecodeError: existing = {}``, which republished the doc with
    only the current rep in it and destroyed every earlier rep's record.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    # An OSError is a filesystem problem, not corruption: let it propagate
    # untouched rather than quarantining a file we could not even read.
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        quarantined = _quarantine_corrupt(path)
        logger.error("pass_summary at %s is unparseable (%s); moved to %s",
                     path, exc, quarantined)
        raise PassSummaryCorruptError(path, quarantined, str(exc)) from exc
    if not isinstance(doc, dict):
        quarantined = _quarantine_corrupt(path)
        detail = f"top-level JSON is {type(doc).__name__}, expected object"
        logger.error("pass_summary at %s is unusable (%s); moved to %s",
                     path, detail, quarantined)
        raise PassSummaryCorruptError(path, quarantined, detail)
    return doc


def pass_summary_text(doc: dict) -> str:
    """Encode a doc exactly the way every writer has always encoded it.

    ``indent=2``, default ASCII escaping. pass_summary.json is diffed across the
    batch / backfill / rebuild / regrade writers, so the encoder must not vary by
    which one last touched the file.
    """
    return json.dumps(doc, indent=2)


# --------------------------------------------------------------------------- #
# Channel A reconstruction (ctrf.json / reward.txt)
# --------------------------------------------------------------------------- #
def ctrf_test_result(run_dir: Path) -> dict:
    """Reconstruct the in-memory ``test_result`` dict from on-disk verifier output.

    Counts come from the per-test status list when present — the CTRF *summary*
    lumps errored tests into ``other``, so the list is strictly more accurate —
    falling back to ``results.summary`` and then to a legacy top-level ``summary``.

    The scalar reward is ``summary.overall_score`` first with ``reward.txt`` as
    the fallback; see the module docstring for why that precedence (and not
    reward.txt-first) is the consolidated one.
    """
    verifier = Path(run_dir) / "task_output" / "logs" / "verifier"
    ctrf = load_json_or_none(verifier / "ctrf.json")
    out = {"tests_total": 0, "tests_passed": 0, "tests_failed": 0,
           "tests_errored": 0, "tests_skipped": 0, "reward": None}
    if isinstance(ctrf, dict):
        results = ctrf.get("results") or {}
        # `ctrf.get("summary")` is the legacy flat shape some historical trees
        # carry; it only fires when there is no results.summary at all.
        summary = results.get("summary") or ctrf.get("summary") or {}
        tests = results.get("tests") or []
        if isinstance(tests, list) and tests:
            counts = {"passed": 0, "failed": 0, "errored": 0, "skipped": 0}
            for t in tests:
                st = (t or {}).get("status", "")
                if st in counts:
                    counts[st] += 1
            out["tests_total"] = len(tests)
            out["tests_passed"] = counts["passed"]
            out["tests_failed"] = counts["failed"]
            out["tests_errored"] = counts["errored"]
            out["tests_skipped"] = counts["skipped"]
        else:
            out["tests_total"] = int(summary.get("tests", 0) or 0)
            out["tests_passed"] = int(summary.get("passed", 0) or 0)
            out["tests_failed"] = int(summary.get("failed", 0) or 0)
            out["tests_errored"] = int(summary.get("other", 0) or 0)
            out["tests_skipped"] = int(summary.get("skipped", 0) or 0)
        out["reward"] = finite_float(summary.get("overall_score"))
    if out["reward"] is None:
        try:
            out["reward"] = finite_float(
                float((verifier / "reward.txt").read_text().strip()))
        except (OSError, ValueError):
            pass
    return out


# --------------------------------------------------------------------------- #
# the shared ungraded predicate (D2)
# --------------------------------------------------------------------------- #
def is_ungraded(record: Any) -> bool:
    """True when ``record`` carries no grading signal at all.

    THE single ungraded predicate. It accepts either shape the rollup readers
    handle — a raw ``score.json`` / ``score.failed.json`` payload, or a per_run
    entry already stamped by :func:`pass_summary_entry` — because the two travel
    different routes to the same decision:

      * the batch writer sees the raw scores and stamps the entry;
      * the repair scripts re-read score.json from disk, where a HISTORICAL run
        (written before ``grading.write_score``'s failure gate existed) carries
        the dead-judge shape in ``error`` / ``criteria_abstained`` but no
        ``grading_status`` key at all;
      * ``script/aggregate_runs.py`` reads the same raw payload directly.

    Checking only ``grading_status == "failed"`` — which the three script readers
    used to do — let those historical payloads through as a genuine 0.0 and
    deflated every average they touched (measured: 0.0821 against a true 0.1642).
    Delegating to ``grading.grading_failure_reason`` puts every reader on the
    exact criterion ``grading.write_score`` itself uses to refuse a score.json.

    On an already-built entry the delegation is a no-op (entries carry neither
    ``error`` nor ``criteria_abstained``), so the predicate is safe to apply
    uniformly at all four sites.
    """
    if not isinstance(record, dict):
        return False
    if record.get("grading_status") == "failed":
        return True
    return grading_failure_reason(record) is not None


def include_incomplete_runs() -> bool:
    return os.environ.get("WCB_INCLUDE_INCOMPLETE_RUNS", "").strip().lower() in (
        "1", "true", "yes", "on")


def include_invalid_runs() -> bool:
    # Opt-OUT of the fail-closed exclusion of invalid runs (injection failed /
    # unmeasured). Default is fail-CLOSED: an injects-never-landed run or an
    # empty-trajectory run is NOT a valid measurement of the scenario, so it
    # must not contaminate pass@K averages. WCB_INCLUDE_INVALID_RUNS=1 folds
    # them back in for debugging (mirror of WCB_INCLUDE_INCOMPLETE_RUNS).
    return os.environ.get("WCB_INCLUDE_INVALID_RUNS", "").strip().lower() in (
        "1", "true", "yes", "on")


def run_exclusion_reason(record: dict) -> str | None:
    """Why this record must NOT count toward averages, or None.

    Fail-closed: only valid measurements average in. ``ungraded`` is checked
    FIRST and has NO opt-out env, unlike the three reasons below: those exclude a
    run that WAS measured and judged invalid, so folding them back in for
    debugging still surfaces a real number. A totally failed judge produced no
    number at all — the only thing an opt-out could fold in is the fabricated 0.0
    placeholder, which is the precise defect this exists to kill.
    """
    if is_ungraded(record):
        return "ungraded"
    if record.get("run_incomplete") and not include_incomplete_runs():
        return "incomplete"
    if not include_invalid_runs():
        # `is False` (not falsy): a missing key on a legacy run must NOT exclude.
        if record.get("injection_ok") is False:
            return "injection_failed"
        # eval_skipped is a non-empty reason string when the eval phase refused
        # to grade (empty trajectory / never-ran): overall_score is None, so the
        # run carries no signal and would otherwise be averaged in as a 0.0.
        if record.get("eval_skipped"):
            return "unmeasured"
    return None


# --------------------------------------------------------------------------- #
# entry + doc
# --------------------------------------------------------------------------- #
def pass_summary_entry(run_index: int, scores: dict | None,
                       test_result: dict | None) -> dict:
    """Build one per_run record carrying BOTH scoring channels.

    Channel B (rubric): criteria_* + rubric_reward, from score.json.
    Channel A (pytest): tests_* + test_reward, from the real test_result/ctrf —
        NOT aliased to criteria_*.
    combined_reward mirrors ``_augment_score_with_combined_rewards``; ``reward``
    is the authoritative run reward (combined when tests ran, else rubric).
    """
    s = scores or {}
    tr = test_result or {}
    # --- Channel B: rubric (canonical criteria_*, legacy tests_* fallback) ---
    crit_total = int(s.get("criteria_total", s.get("tests_total", 0)) or 0)
    crit_passed = int(s.get("criteria_passed", s.get("tests_passed", 0)) or 0)
    crit_failed = int(s.get("criteria_failed", s.get("tests_failed", 0)) or 0)
    rubric_reward = finite_float(s.get("rubric_based_reward"))
    if rubric_reward is None:
        rubric_reward = finite_float(s.get("overall_score"))
    rubric_pct = finite_float(s.get("rubric_weights_percentage"))
    if rubric_pct is None and rubric_reward is not None:
        rubric_pct = rubric_reward * 100.0
    # --- Channel A: real pytest counts ---
    t_total = int(tr.get("tests_total", 0) or 0)
    t_passed = int(tr.get("tests_passed", 0) or 0)
    t_failed = int(tr.get("tests_failed", 0) or 0)
    t_err = int(tr.get("tests_errored", 0) or 0)
    t_skip = int(tr.get("tests_skipped", 0) or 0)
    test_reward = finite_float(s.get("test_based_reward"))
    if test_reward is None and t_total > 0:
        test_reward = finite_float(tr.get("reward"))
    # --- combined ---
    combined = finite_float(s.get("combined_reward"))
    if combined is None:
        if test_reward is not None and rubric_reward is not None:
            combined = (test_reward + rubric_reward) / 2.0
        elif test_reward is not None:
            combined = test_reward
        else:
            combined = rubric_reward
    authoritative = combined if combined is not None else (rubric_reward or 0.0)
    entry = {
        "run_index": run_index,
        # Channel B — rubric judge
        "criteria_total": crit_total,
        "criteria_passed": crit_passed,
        "criteria_failed": crit_failed,
        "rubric_reward": rubric_reward,
        "rubric_weights_percentage": round(rubric_pct, 2) if rubric_pct is not None else None,
        # Channel A — real pytest
        "tests_total": t_total,
        "tests_passed": t_passed,
        "tests_failed": t_failed,
        "tests_errored": t_err,
        "tests_skipped": t_skip,
        "test_reward": test_reward,
        # authoritative run reward = combined (falls back to rubric when no tests)
        "combined_reward": combined,
        "reward": authoritative,
    }
    # Preserve the last-resort-stub marker so operators + downstream tools can
    # distinguish "grader wrote 0" from "no grader ran; stub emitted by finally".
    if s.get("__last_resort_stub__"):
        entry["__last_resort_stub__"] = True
    # Same for the injection-integrity flag: a run whose silent mutations
    # failed is not a valid measurement of the injection scenario.
    if s.get("injection_ok") is False:
        entry["injection_ok"] = False
    # Turn-completion marker: pass_summary_doc excludes flagged runs from
    # averages; the entry itself is preserved so the run never silently
    # disappears from per_run.
    if s.get("run_incomplete"):
        entry["run_incomplete"] = True
        entry["turns_planned"] = s.get("turns_planned")
        entry["turns_completed"] = s.get("turns_completed")
    # Unmeasured marker: the eval phase refused to grade (empty trajectory or
    # never-ran). overall_score is None; pass_summary_doc excludes it from
    # averages so a no-signal run is not folded in as a 0.0.
    if s.get("eval_skipped"):
        entry["eval_skipped"] = s.get("eval_skipped")
    # Ungraded marker: the judge died outright, so `reward` above collapsed to
    # the 0.0 placeholder out of a payload that measured nothing. Stamp it so
    # pass_summary_doc drops the run from every average instead of folding a
    # dead judge in as a genuine zero.
    if is_ungraded(s):
        entry["grading_status"] = "failed"
    if s.get("turns_duplicated"):
        entry["turns_duplicated"] = list(s["turns_duplicated"])
    return entry


def pass_summary_doc(model_type: str, per_run: list) -> dict:
    per_run = sorted(per_run, key=lambda r: r["run_index"])
    # Invalid runs (ungraded / incomplete / injection-failed / unmeasured) are
    # kept in per_run for visibility but excluded from every average, so pass@K
    # is computed over valid measurements only. The opt-out envs fold the last
    # three back; `ungraded` has no opt-out.
    reasons = {r["run_index"]: run_exclusion_reason(r) for r in per_run}
    used = [r for r in per_run if reasons[r["run_index"]] is None]
    reason_counts: dict[str, int] = {}
    for _v in reasons.values():
        if _v:
            reason_counts[_v] = reason_counts.get(_v, 0) + 1
    excluded = len(per_run) - len(used)
    if used:
        avg_reward = mean_or_none([r.get("reward") for r in used])
        if avg_reward is None:
            # Reps were used but every `reward` was null — only reachable for a
            # doc written before the entry schema guaranteed a scalar (
            # pass_summary_entry always emits at least 0.0). Keep the historical
            # 0.0 so re-reading such a doc is stable.
            avg_reward = 0.0
    else:
        # NOTHING to average. The old `_mean_or_none(...) or 0.0` fabricated a
        # bare 0.0 here, and the `all_runs_excluded` marker was stamped only
        # inside `if excluded:` — so an EMPTY per_run produced
        # `runs: 0, average_reward: 0.0` with NO marker whatsoever, which
        # delivery reads as a genuine zero score. Emit null + a marker instead:
        # "no measurement" must never be spellable as a number.
        avg_reward = None
    avg_combined = mean_or_none([r.get("combined_reward") for r in used])
    avg_rubric = mean_or_none([r.get("rubric_reward") for r in used])
    avg_test = mean_or_none([r.get("test_reward") for r in used])
    avg_pct = mean_or_none([r.get("rubric_weights_percentage") for r in used])
    doc = {
        "model": model_type,
        "runs": len(per_run),
        # average_reward is the authoritative (combined) mean, not rubric-only
        "average_reward": avg_reward,
        "average_combined_reward": avg_combined,
        "average_rubric_reward": avg_rubric,
        "average_test_reward": avg_test,
        "average_rubric_weights_percentage": round(avg_pct, 2) if avg_pct is not None else None,
        "per_run": per_run,
    }
    # `or not used` covers the empty-per_run case, where `excluded` is 0 because
    # there was nothing to exclude — yet there is still nothing to average.
    if excluded or not used:
        doc["runs_used"] = len(used)
        for _reason, _key in (
            ("incomplete", "runs_excluded_incomplete"),
            ("injection_failed", "runs_excluded_injection_failed"),
            ("unmeasured", "runs_excluded_unmeasured"),
            ("ungraded", "runs_ungraded"),
        ):
            if reason_counts.get(_reason):
                doc[_key] = reason_counts[_reason]
        # No usable rep at all: average_reward is null, NOT a measurement.
        # Surface it so delivery does not read it as a genuine zero.
        doc["all_runs_excluded"] = True
    return doc


# --------------------------------------------------------------------------- #
# whole-dir rebuild + the two write paths
# --------------------------------------------------------------------------- #
def discover_run_dirs(model_dir: Path) -> list[tuple[int, Path]]:
    """Return sorted ``[(run_index, run_dir), ...]`` under ``model_dir``."""
    out: list[tuple[int, Path]] = []
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        return out
    for child in model_dir.iterdir():
        if not child.is_dir():
            continue
        m = RUN_DIR_RE.match(child.name)
        if not m:
            continue
        out.append((int(m.group(1)), child))
    out.sort(key=lambda x: x[0])
    return out


def rebuild_model_dir(model_dir: Path, model_type: str | None = None) -> dict | None:
    """Recompute the whole doc from the run_N artifacts, or None when there are none."""
    model_dir = Path(model_dir)
    runs = discover_run_dirs(model_dir)
    if not runs:
        return None
    per_run: list[dict] = []
    for run_index, run_dir in runs:
        scores = load_json_or_none(run_dir / "score.json")
        if scores is None:
            # Only score.failed.json => judge died; it carries grading_status so
            # pass_summary_entry / run_exclusion_reason drop the run from the
            # averages instead of scoring it 0.0.
            scores = load_json_or_none(run_dir / "score.failed.json")
        per_run.append(pass_summary_entry(run_index, scores or {},
                                          ctrf_test_result(run_dir)))
    return pass_summary_doc(model_type or model_dir.name, per_run)


def write_pass_summary(model_dir: Path) -> dict | None:
    """Rebuild and persist ``<model_dir>/pass_summary.json`` under the batch lock.

    The rebuild happens INSIDE the lock: it is a read-modify-write over every
    run_N in the dir, so computing outside would let a rep that finishes
    mid-rebuild be silently dropped from the doc we then write.

    Returns the written doc, or None when the dir holds no run_N at all.
    """
    model_dir = Path(model_dir)
    with locked(model_dir / PASS_SUMMARY_LOCK_FILENAME):
        doc = rebuild_model_dir(model_dir)
        if doc is None:
            return None
        atomic_write_text(model_dir / PASS_SUMMARY_FILENAME, pass_summary_text(doc))
    return doc


def upsert_pass_summary(model_dir: Path, model_type: str, run_index: int,
                        scores: dict | None = None,
                        test_result: dict | None = None) -> dict:
    """Merge ONE rep's entry into the per-model doc (the live batch write path).

    Unlike :func:`write_pass_summary` this does not re-read the run_N artifacts:
    the batch already holds the in-memory scores/test_result for the rep that
    just finished, and the other reps' entries come from the existing doc. That
    is why a corrupt existing doc is fatal here (see :func:`load_existing_doc`)
    but merely irrelevant to the rebuild path.
    """
    model_dir = Path(model_dir)
    with locked(model_dir / PASS_SUMMARY_LOCK_FILENAME):
        p = model_dir / PASS_SUMMARY_FILENAME
        existing = load_existing_doc(p)
        per_run = [r for r in existing.get("per_run", [])
                   if r.get("run_index") != run_index]
        per_run.append(pass_summary_entry(run_index, scores, test_result))
        doc = pass_summary_doc(model_type, per_run)
        atomic_write_text(p, pass_summary_text(doc))
    return doc
