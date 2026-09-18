"""Cost isolation between runs that share one sidecar usage log.

Every concurrent run on a batch appends to the same usage.jsonl, so "which rows
are mine" is the whole of per-run cost. Selecting them by wall-clock window --
which is what the harness did until the run_key was threaded through -- answers
that question with "everything that happened while I was running", and under
--parallel that is every co-tenant's traffic as well. The 2026-08 delivery
measured 1.4x-62.7x inflation from it, and 809e3bf found the same window had
left all 37212 assistant messages in all 568 runs with no per-message usage at
all, because the rows and the window were not even on the same clock.

The fix was to match on the per-attempt key the runner mints and the sidecar
stamps on every row. What the existing suite pins is that single-run path:
tests/test_usage_attribution_replay.py drives one real run's 156 rows through
the classifier and the gate, and tests/test_run_batch_units.py covers the
attribution internals. Neither builds a log with more than one run in it, so
the property the incident was actually about -- that two runs sharing a log
cannot see each other's money -- was never asserted anywhere. This file is that
assertion, and it lives apart from both because its subject is the shape of a
multi-tenant log rather than the internals of either module: it drives the
totals path (src/utils/grading.py) and the per-message path
(eval/run_batch.py) against the same shared file and requires them to agree.

Row shapes and token payloads are the real ones, taken from the 2026-09-18
sean_callahan fixture and re-tagged, so the numbers under test are numbers the
sidecar actually wrote.

Both selectors are exercised through their production entry points. Nothing
here reimplements selection.
"""

from __future__ import annotations

import json
import logging
import re
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eval.run_batch import (
    _attribute_per_message_cost,
    recompute_combined,
    save_usage,
)
from src.utils.auth_provider import BEDROCK, OAUTH
from src.utils.grading import extract_usage_from_litellm_log
from src.utils.oauth_pricing import OPUS_RATES, cost_breakdown

FIXTURE = Path(__file__).parent / "fixtures" / "usage_replay_sean_20260918.json"

TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
SUM_COLUMNS = TOKEN_COLUMNS + ("total_tokens", "request_count")

# The wall clock every synthetic log below is laid out on. One shared minute is
# the point: every run's rows fall inside every other run's window.
EPOCH = datetime(2026, 9, 18, 6, 15, 0, tzinfo=timezone.utc)

# A window wide enough to contain the whole of any log this file writes. Passed
# to every totals call so that a selector leaking even one row through the
# window would show up as a surplus rather than being masked by a tight span.
WINDOW = (EPOCH.timestamp() - 3600, EPOCH.timestamp() + 3600)


def _key(task: str) -> str:
    """A run key in the runner's minted shape (runner.py:599)."""
    return f"wcb::{task}_claude-opus-5_20260918_0614_{uuid.uuid4().hex[:6]}::{uuid.uuid4().hex}"


@pytest.fixture(scope="module")
def sean() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shapes(sean) -> list[dict]:
    """The fixture's rows with their tagging stripped, as raw payload shapes."""
    return [{k: v for k, v in row.items() if k not in ("run_key", "ts")}
            for row in sean["rows"]]


def _row(shapes: list[dict], index: int, *, run_key: str | None, offset: float,
         **overrides) -> dict:
    """One real row shape, re-tagged and re-stamped onto the shared clock.

    ``run_key=None`` writes no run_key field at all, which is what the usage
    callback emits when it cannot read one off the request: the key is spread
    conditionally (litellm_usage_callback.py:581), so an untagged row is one
    with the column absent rather than empty.
    """
    row = dict(shapes[index % len(shapes)])
    row["ts"] = (EPOCH + timedelta(seconds=offset)).isoformat()
    if run_key is not None:
        row["run_key"] = run_key
    row.update(overrides)
    return row


def _write(tmp_path: Path, rows: list[dict], name: str = "usage.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _hand_total(rows: list[dict]) -> dict:
    """Sum rows by hand, the way a person auditing usage.jsonl would.

    Deliberately not a call into the production summer: the point of comparing
    against it is that it is written independently of the code under test.
    """
    total = {c: 0 for c in SUM_COLUMNS}
    total["cost_usd"] = 0.0
    for row in rows:
        total["request_count"] += 1
        for column in TOKEN_COLUMNS + ("total_tokens",):
            total[column] += int(row.get(column, 0) or 0)
        total["cost_usd"] += float(row.get("cost_usd", 0.0) or 0.0)
    total["cost_usd"] = round(total["cost_usd"], 6)
    return total


def _assert_totals_match(got: dict, want: dict, label: str = "") -> None:
    for column in SUM_COLUMNS:
        assert got[column] == want[column], f"{label} {column}: {got[column]} != {want[column]}"
    assert got["cost_usd"] == pytest.approx(want["cost_usd"], abs=1e-9), label


# ===========================================================================
# A. Parallel pollution: three runs, one log
# ===========================================================================


@pytest.fixture
def three_way(shapes, tmp_path):
    """Three concurrent runs, 30 rows each, round-robin into one log.

    Interleaved row by row on a single shared clock, so no run occupies a
    contiguous span and no run's window excludes another's rows. Any selector
    with a time component at all would over-collect here.
    """
    keys = [_key("alpha"), _key("bravo"), _key("chuck")]
    owned: dict[str, list[dict]] = {k: [] for k in keys}
    shared: list[dict] = []
    for i in range(90):
        key = keys[i % 3]
        row = _row(shapes, i, run_key=key, offset=i * 0.37)
        shared.append(row)
        owned[key].append(row)
    return keys, owned, shared, _write(tmp_path, shared, "shared.jsonl")


def test_each_concurrent_run_selects_exactly_its_own_rows(three_way):
    """The guard the incident is about: exact key, nothing else.

    Every run is handed a window spanning the entire file. If the window
    contributed anything at all -- as a union, as a tie-break, as a fallback
    that fires while tagged rows exist -- each run would come back with some or
    all of the other 60 rows.
    """
    keys, owned, _shared, log = three_way
    for key in keys:
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
        assert got["usage_source"] == "litellm_run_key"
        assert got["request_count"] == 30
        _assert_totals_match(got, _hand_total(owned[key]), key)


def test_the_three_runs_partition_the_log_with_no_gap_and_no_overlap(three_way):
    """Disjoint AND complete: the three totals sum to the file, exactly.

    Summing to the file total rules out omission; each run already matching its
    own rows exactly rules out double-counting. Together they say the selection
    is a partition, which is the property a batch-level cost roll-up depends on.
    """
    keys, _owned, shared, log = three_way
    aggregate = {c: 0 for c in SUM_COLUMNS}
    aggregate["cost_usd"] = 0.0
    for key in keys:
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
        for column in SUM_COLUMNS:
            aggregate[column] += got[column]
        aggregate["cost_usd"] += got["cost_usd"]
    aggregate["cost_usd"] = round(aggregate["cost_usd"], 6)
    _assert_totals_match(aggregate, _hand_total(shared), "partition")


def test_a_runs_total_does_not_move_when_a_co_tenant_is_added(shapes, tmp_path):
    """Adding a neighbour to the log must not change what the victim reads.

    The regression this forbids is the original one: the victim's figure was a
    function of who else was running, so the same trajectory costed differently
    depending on the batch's parallelism.
    """
    victim = _key("victim")
    mine = [_row(shapes, i, run_key=victim, offset=i) for i in range(12)]
    alone = extract_usage_from_litellm_log(
        _write(tmp_path, mine, "alone.jsonl"), *WINDOW, run_key=victim)

    crowded_rows = list(mine)
    for n in range(4):
        neighbour = _key(f"neighbour{n}")
        crowded_rows += [_row(shapes, 30 + n * 9 + i, run_key=neighbour,
                              offset=i + 0.5) for i in range(9)]
    crowded_rows.sort(key=lambda r: r["ts"])
    crowded = extract_usage_from_litellm_log(
        _write(tmp_path, crowded_rows, "crowded.jsonl"), *WINDOW, run_key=victim)

    assert len(crowded_rows) == 48
    _assert_totals_match(crowded, alone, "co-tenant invariance")


# ===========================================================================
# B. Leakage: untagged rows, foreign rows, near-miss keys
# ===========================================================================


# How a row that is NOT the victim's can be tagged. Each entry maps the
# victim's own key to the value the intruder row carries instead; ABSENT means
# the row has no run_key column at all, which is what the usage callback writes
# when it cannot read a key off the request (litellm_usage_callback.py:581).
ABSENT = object()

FOREIGN_TAGGINGS = {
    "absent": lambda victim: ABSENT,
    "json-null": lambda victim: None,
    "empty": lambda victim: "",
    "other-run": lambda victim: _key("other"),
    "superstring": lambda victim: victim + "x",
    "substring": lambda victim: victim[:-1],
    "case-folded": lambda victim: victim.upper(),
    "whitespace-padded": lambda victim: f" {victim} ",
    "probe": lambda victim: f"wcb::__probe__::{uuid.uuid4().hex}",
}


@pytest.mark.parametrize("label", sorted(FOREIGN_TAGGINGS))
def test_a_foreign_or_untagged_row_never_enters_a_tagged_selection(
        shapes, tmp_path, label):
    """No row that is not this run's own may contribute, whatever it looks like.

    The untagged shapes are the master-key deployment (the main agent's bearer
    carries no key, so the callback writes none) and any log predating the
    tagging. The near-miss keys are here because the match must be equality and
    not containment: a prefix, a suffix, a case fold or a stray space are all
    different runs, and ``wcb::__probe__::`` is the sidecar's own startup ping
    (litellm_sidecar.py:94-105), which belongs to no attempt at all.
    """
    victim = _key("victim")
    tagging = FOREIGN_TAGGINGS[label](victim)
    assert tagging is ABSENT or tagging != victim, "the intruder must be foreign"

    mine = [_row(shapes, i, run_key=victim, offset=i * 2) for i in range(8)]
    # Intruders are placed strictly inside the victim's own span, so any
    # selector with a time component would take every one of them.
    intruders = []
    for i in range(5):
        row = _row(shapes, 40 + i, run_key=None, offset=i * 2 + 1)
        if tagging is not ABSENT:
            row["run_key"] = tagging
        intruders.append(row)

    if label == "absent":
        assert all("run_key" not in r for r in intruders)
    elif label == "json-null":
        assert all(r["run_key"] is None for r in intruders)

    log = _write(tmp_path, [r for pair in zip(mine, intruders) for r in pair]
                 + mine[5:], f"leak_{label}.jsonl")
    # The file really does hold both populations, in the shape the reader sees.
    on_disk = [json.loads(line) for line in
               log.read_text(encoding="utf-8").splitlines()]
    assert len(on_disk) == 13
    assert sum(1 for r in on_disk if r.get("run_key") == victim) == 8

    got = extract_usage_from_litellm_log(log, *WINDOW, run_key=victim)

    assert got["usage_source"] == "litellm_run_key", label
    assert got["request_count"] == 8, label
    _assert_totals_match(got, _hand_total(mine), label)


def test_the_intruders_carried_real_money_that_was_correctly_refused(shapes, tmp_path):
    """A leak test only means something if there was something to leak.

    Pins that the rows the previous test excludes are not incidentally empty:
    they carry six figures of cache-read and real output tokens, so a selector
    that took them would be visibly wrong rather than harmlessly wrong.
    """
    victim = _key("victim")
    intruders = [_row(shapes, 40 + i, run_key=None, offset=i * 2 + 1)
                 for i in range(5)]
    noise = _hand_total(intruders)
    assert noise["output_tokens"] > 0
    assert noise["cache_read_tokens"] > 10_000

    mine = [_row(shapes, i, run_key=victim, offset=i * 2) for i in range(8)]
    log = _write(tmp_path, mine + intruders, "money.jsonl")
    got = extract_usage_from_litellm_log(log, *WINDOW, run_key=victim)
    for column in TOKEN_COLUMNS:
        assert got[column] == _hand_total(mine)[column]
        assert got[column] != _hand_total(mine + intruders)[column], column


def test_an_untagged_row_is_not_claimed_by_any_of_the_tagged_runs(three_way, shapes, tmp_path):
    """Untagged traffic belongs to nobody, not to everybody.

    The row shape here is the one the callback writes under master-key auth,
    dropped into a log three tagged runs are sharing. No run may absorb it.
    """
    keys, owned, shared, _log = three_way
    orphans = [_row(shapes, 100 + i, run_key=None, offset=i * 0.37 + 0.18)
               for i in range(6)]
    log = _write(tmp_path, shared + orphans, "orphans.jsonl")
    for key in keys:
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
        _assert_totals_match(got, _hand_total(owned[key]), key)


# ===========================================================================
# C. The window fallback: when it fires, and that it is always marked
# ===========================================================================


def test_the_window_cannot_contribute_while_tagged_rows_exist(three_way):
    """Degenerate window, tagged rows present: the window is not consulted.

    Handing the extractor a zero-width window at the epoch means a selector
    that merged the two channels, or took the window when it was wider, would
    return a different number here than with the full-file window. It must
    return the same 30 rows either way.
    """
    keys, owned, _shared, log = three_way
    for key in keys:
        tight = extract_usage_from_litellm_log(log, 0.0, 0.0, run_key=key)
        wide = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
        assert tight["usage_source"] == "litellm_run_key"
        _assert_totals_match(tight, wide, key)
        _assert_totals_match(tight, _hand_total(owned[key]), key)


def test_the_window_is_refused_outright_when_the_log_has_other_tenants_in_it(three_way):
    """A run absent from a MULTI-TENANT log is billed zero, not everyone else.

    Previously ``test_the_window_path_is_always_marked_as_not_run_key_scoped``,
    which asserted ``request_count == len(shared)`` on both of these calls --
    i.e. it pinned the pollution and settled for the ``usage_source`` label as
    the consolation. The label is still checked, because a roll-up excluding
    unreconciled runs reads that field and the window must never be allowed to
    claim ``litellm_run_key``; what is no longer accepted is the 3x figure it
    was labelling. Both ways in are covered: no key supplied at all (the
    master-key deployment, runner.py:1444) and a key that matches nothing.
    """
    keys, owned, shared, log = three_way

    no_key = extract_usage_from_litellm_log(log, *WINDOW, run_key="")
    assert no_key["usage_source"] == "litellm"
    assert no_key["usage_attribution"] == "no_run_key_window_refused"
    assert no_key["request_count"] == 0

    unmatched = extract_usage_from_litellm_log(
        log, *WINDOW, run_key=_key("never-ran"))
    assert unmatched["usage_source"] == "litellm"
    assert unmatched["usage_attribution"] == "run_key_absent_window_refused"
    assert unmatched["request_count"] == 0

    # Not one token of the 90 rows on disk reaches either of them.
    for column in TOKEN_COLUMNS + ("total_tokens",):
        assert no_key[column] == 0, column
        assert unmatched[column] == 0, column
    assert _hand_total(shared)["output_tokens"] > 0, (
        "the rows being refused carry real money")
    # The three tagged runs are untouched by the refusal.
    for k in keys:
        _assert_totals_match(extract_usage_from_litellm_log(log, *WINDOW, run_key=k),
                             _hand_total(owned[k]), k)


def test_the_refusal_of_the_window_is_loud(three_way, caplog):
    """A zero that replaces a plausible number has to explain itself.

    The old warning was gated on ``if run_key:`` (grading.py:2865), so the one
    caller that reaches this path with an empty key -- runner.py:1444 under
    master-key auth -- got the wrong number in silence. Both entries now warn,
    and the message names which of the two it was.
    """
    _keys, _owned, _shared, log = three_way

    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        extract_usage_from_litellm_log(log, *WINDOW, run_key="")
    blank = " ".join(r.getMessage() for r in caplog.records)
    assert "no run_key was supplied" in blank
    assert "master-key" in blank
    assert "Refusing the time-window fallback" in blank
    assert "ZERO" in blank

    caplog.clear()
    absent = _key("never-ran")
    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        extract_usage_from_litellm_log(log, *WINDOW, run_key=absent)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert absent in text
    assert "no row carries run_key" in text


def test_the_master_key_batch_warning_fires_at_parallel_one(monkeypatch, caplog):
    """The batch-start half of the same silence, with the gate removed.

    ``--parallel`` counts this process's own tasks and nothing else, but
    script/run.sh:716 hardcodes ``--parallel 1`` on every eval/run_batch.py it
    launches and fans out PROCESSES instead, all sharing the one
    WCB_SHARED_SIDECAR_USAGE_LOG. Gating on it made the warning unreachable
    from the canonical entry point, and left two operators on two terminals
    with no notice at all.
    """
    from eval.run_batch import _warn_if_master_key_auth_degrades_attribution

    monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
    monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="eval.run_batch"):
        _warn_if_master_key_auth_degrades_attribution(
            types.SimpleNamespace(parallel=1))
    message = " ".join(r.getMessage() for r in caplog.records)
    assert "master-key mode is ON" in message
    assert "UNCONDITIONAL" in message
    assert "WCB_SIDECAR_MASTER_KEY" in message


def test_a_run_whose_every_request_failed_is_billed_zero_not_its_neighbours(
        shapes, tmp_path, caplog):
    """A 429 storm costs nothing, and nothing is what it must be billed.

    Previously ``test_a_run_whose_every_request_failed_falls_through_to_the_
    window``, which asserted ``request_count > 0`` to characterise the defect.
    ``extract_usage_from_litellm_log`` dropped ``preflight`` and ``failure``
    kinds BEFORE matching the run key, so a run whose every request errored --
    a 429 storm, a credential rotation mid-batch, an upstream outage -- had
    tagged rows in the log but none that survived to the match, the fallback
    fired, and it was billed its co-tenants' traffic. Ownership is now decided
    first and the kind filter runs on the run's OWN rows, so the same log
    yields zero: the run is present, and it provably spent nothing.
    """
    victim = _key("victim")
    neighbour = _key("neighbour")
    failures = [_row(shapes, 0, run_key=victim, offset=i * 3, kind="failure",
                     error_class="RateLimitError", error="429 Too Many Requests",
                     input_tokens=0, output_tokens=0, total_tokens=0,
                     cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0)
                for i in range(8)]
    healthy = [_row(shapes, i, run_key=neighbour, offset=i * 3 + 1)
               for i in range(20)]
    log = _write(tmp_path, failures + healthy, "all_failed.jsonl")

    assert _hand_total(failures)["output_tokens"] == 0
    assert _hand_total(healthy)["output_tokens"] > 0, "there is money to steal"

    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=victim)

    # Selection WAS by run key -- the run's own rows were found and then
    # emptied by the kind filter -- so the provenance is honest either way.
    assert got["usage_source"] == "litellm_run_key"
    assert got["usage_attribution"] == "run_key_zero_billable"
    _assert_totals_match(got, _hand_total([]), "all-failed run")
    stolen = _hand_total(healthy)
    for column in TOKEN_COLUMNS + ("total_tokens", "request_count"):
        assert got[column] == 0, column
        if stolen[column]:
            assert got[column] != stolen[column], (
                f"{column}: the neighbour's traffic reached the victim")

    message = " ".join(r.getMessage() for r in caplog.records)
    assert "preflight/failure" in message
    assert "ZERO" in message
    assert "time window is NOT consulted" in message

    # The neighbour, which does have surviving tagged rows, is untouched.
    unaffected = extract_usage_from_litellm_log(log, *WINDOW, run_key=neighbour)
    assert unaffected["usage_source"] == "litellm_run_key"
    _assert_totals_match(unaffected, _hand_total(healthy), "neighbour")

    # And the money is now on exactly one invoice instead of two.
    for column in TOKEN_COLUMNS:
        assert got[column] + unaffected[column] == _hand_total(failures + healthy)[column]


def test_a_run_that_never_logged_a_row_absorbs_nothing(shapes, tmp_path, caplog):
    """The container that died before its first request, in a shared log.

    Distinct from the all-failed case: there the run owns rows and they are all
    unbillable, here it owns none at all. Both used to land in the window and
    come back with a co-tenant's bill. The rule that separates them from a
    legacy log is whether the FILE carries tagging: it does here, so the run's
    absence from it means absence, not "no tagging available".
    """
    ghost = _key("container-died-at-startup")
    healthy = [_row(shapes, i, run_key=_key("neighbour"), offset=i * 3)
               for i in range(20)]
    log = _write(tmp_path, healthy, "zero_rows.jsonl")
    assert not any(r["run_key"] == ghost for r in healthy)

    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=ghost)

    assert got["usage_source"] == "litellm"
    assert got["usage_attribution"] == "run_key_absent_window_refused"
    _assert_totals_match(got, _hand_total([]), "ghost run")
    assert "Refusing the time-window fallback" in \
        " ".join(r.getMessage() for r in caplog.records)


def test_the_window_still_serves_a_genuinely_untagged_legacy_log(shapes, tmp_path, caplog):
    """The fallback is narrowed, not removed.

    A log in which NO row anywhere carries a run_key is a single-run log from
    before the key was threaded through, and the window is both the only
    selector available and sound: there is no second tenant in the file to
    confuse it with. This is the case runner.py still depends on for replayed
    and archived runs, and it must keep returning the same numbers it always
    did -- while saying loudly that it did so.
    """
    legacy = [_row(shapes, i, run_key=None, offset=i * 2) for i in range(9)]
    legacy.append(_row(shapes, 40, run_key=None, offset=99, kind="failure",
                       input_tokens=0, output_tokens=0, total_tokens=0,
                       cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0))
    log = _write(tmp_path, legacy, "legacy.jsonl")
    assert all("run_key" not in r for r in legacy)

    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        got = extract_usage_from_litellm_log(log, *WINDOW, run_key=_key("whoever"))

    assert got["usage_source"] == "litellm"
    assert got["usage_attribution"] == "time_window_legacy"
    _assert_totals_match(got, _hand_total(legacy[:9]), "legacy window")
    assert "NO row in it carries a run_key at all" in \
        " ".join(r.getMessage() for r in caplog.records)

    # Same file, no key at all: identical, and equally loud.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        keyless = extract_usage_from_litellm_log(log, *WINDOW)
    _assert_totals_match(keyless, got, "legacy window, keyless")
    assert caplog.records, "the window must never be taken in silence"


def test_two_terminals_sharing_one_sidecar_log_cannot_bill_each_other(
        shapes, tmp_path, caplog):
    """The cross-process case, which no single process can detect for itself.

    script/run.sh:716 launches every eval/run_batch.py with ``--parallel 1``
    and gets concurrency by fanning out PROCESSES onto one
    WCB_SHARED_SIDECAR_USAGE_LOG; a second operator on a second terminal
    inheriting an exported WCB_SHARED_SIDECAR lands in the same file. Run keys
    are minted from uuid4 per attempt (runner.py:599) so they cannot collide
    across processes, and a foreign key is foreign whichever process minted it.

    The interesting half is the failure: terminal A's run dies in a 429 storm
    while terminal B's runs healthily. A must not come back holding B's bill,
    and B's own figure must not move because A was there at all.
    """
    term_a, term_b = _key("termA"), _key("termB")
    a_failed = [_row(shapes, 0, run_key=term_a, offset=i * 2, kind="failure",
                     error_class="RateLimitError", error="429 Too Many Requests",
                     input_tokens=0, output_tokens=0, total_tokens=0,
                     cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0)
                for i in range(6)]
    b_healthy = [_row(shapes, i, run_key=term_b, offset=i * 2 + 1) for i in range(12)]
    shared = [r for pair in zip(a_failed + a_failed[:6], b_healthy) for r in pair]
    log = _write(tmp_path, shared, "two_terminals.jsonl")

    with caplog.at_level(logging.WARNING, logger="src.utils.grading"):
        a = extract_usage_from_litellm_log(log, *WINDOW, run_key=term_a)
    b = extract_usage_from_litellm_log(log, *WINDOW, run_key=term_b)

    assert a["usage_attribution"] == "run_key_zero_billable"
    for column in TOKEN_COLUMNS + ("total_tokens", "request_count"):
        assert a[column] == 0, column
    assert caplog.records, "the zero has to be explained"

    assert b["usage_source"] == "litellm_run_key"
    _assert_totals_match(b, _hand_total(b_healthy), "terminal B")

    # B alone in its own log reads exactly the same -- A's presence, and A's
    # failure, are both invisible to it.
    alone = extract_usage_from_litellm_log(
        _write(tmp_path, b_healthy, "terminal_b_alone.jsonl"), *WINDOW, run_key=term_b)
    _assert_totals_match(b, alone, "terminal B co-tenant invariance")


def test_the_per_message_path_refuses_rather_than_falling_through(shapes, tmp_path):
    """Same log, same key, through the attribution path: nothing is attributed.

    Pins the half of the asymmetry that is correct, so a future change that
    "unifies" the two orderings cannot quietly adopt the wrong one.
    """
    victim = _key("victim")
    neighbour = _key("neighbour")
    failures = [_row(shapes, 0, run_key=victim, offset=i * 3, kind="failure",
                     input_tokens=0, output_tokens=0, total_tokens=0,
                     cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0)
                for i in range(8)]
    healthy = [_row(shapes, i, run_key=neighbour, offset=i * 3 + 1)
               for i in range(20)]
    log = _write(tmp_path, failures + healthy, "all_failed_msg.jsonl")

    traj = {"messages": [{"message": {"role": role, "content": ""}}
                         for role in ["user", "assistant"] * 10]}
    report = _attribute_per_message_cost(traj, str(log), victim,
                                         oauth_route=False, model="claude-opus-5")
    assert report["status"] == "failed"
    assert report["rows_selected"] == 0
    for message in traj["messages"]:
        assert "usage" not in message["message"]


def test_the_attribution_status_never_reads_attributed_off_the_window(shapes, tmp_path):
    """``attributed`` is reserved for run-key selection; the window gets ``partial``.

    score.json and usage.json both carry this stamp, and a consumer filtering
    for reconciled runs filters on it. It must track the selector, not the
    outcome: a window selection that happens to produce a clean count is still
    a figure that over-attributes under parallelism.
    """
    victim = _key("victim")
    neighbour = _key("neighbour")
    mine = [_row(shapes, i, run_key=None, offset=i * 2) for i in range(4)]
    theirs = [_row(shapes, 20 + i, run_key=neighbour, offset=i * 2 + 1)
              for i in range(4)]
    log = _write(tmp_path, mine + theirs, "untagged_run.jsonl")

    stamps = [datetime(2026, 9, 18, 6, 15, i, tzinfo=timezone.utc).isoformat()
              for i in range(8)]
    traj = {"messages": [{"message": {"role": "assistant", "content": ""},
                          "timestamp": stamps[i]} for i in range(8)]}
    report = _attribute_per_message_cost(traj, str(log), "",
                                         oauth_route=False, model="claude-opus-5")
    assert report["status"] == "partial"
    assert report["status"] != "attributed"

    # And the same trajectory attributed by key is the reconciled one.
    tagged = [{**r, "run_key": victim} for r in mine + theirs]
    log2 = _write(tmp_path, tagged, "tagged_run.jsonl")
    traj2 = {"messages": [{"message": {"role": "assistant", "content": ""}}
                          for _ in range(8)]}
    report2 = _attribute_per_message_cost(traj2, str(log2), victim,
                                          oauth_route=False, model="claude-opus-5")
    assert report2["status"] == "attributed"


# ===========================================================================
# D. Token accuracy and source separation
# ===========================================================================


def test_sources_agent_is_the_raw_row_sum_of_the_runs_own_rows(sean, tmp_path):
    """Hand-summed against the run's own log, column by column.

    No rounding, no dropped cache column, no float drift: the token columns are
    integers and must survive as integers, and cache_read/cache_write are the
    two the old jsonl-derived path used to lose.
    """
    key = sean["rows"][0]["run_key"]
    log = _write(tmp_path, sean["rows"], "sean.jsonl")
    got = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
    expected = sean["expected_agent_totals"]
    for column in SUM_COLUMNS:
        assert got[column] == expected[column], column
    _assert_totals_match(got, _hand_total(sean["rows"]), "raw")
    assert got["input_tokens"] + got["output_tokens"] + got["cache_read_tokens"] \
        + got["cache_write_tokens"] == got["total_tokens"]


def test_cost_is_reproducible_by_hand_from_the_four_token_columns(sean):
    """The published Opus card, applied to the run's own totals, arrived at
    independently of the pricing module and required to agree with it.

    This is the arithmetic a finance reviewer would redo, and after c21652c it
    is the arithmetic the OAuth route publishes.
    """
    totals = sean["expected_agent_totals"]
    by_hand = (
        totals["input_tokens"] * 5.0
        + totals["output_tokens"] * 25.0
        + totals["cache_read_tokens"] * 0.50
        + totals["cache_write_tokens"] * 6.25
    ) / 1_000_000
    assert by_hand == pytest.approx(17.043494, abs=5e-7)

    derived = cost_breakdown(
        "claude-opus-5",
        input_tokens=totals["input_tokens"], output_tokens=totals["output_tokens"],
        cache_read_tokens=totals["cache_read_tokens"],
        cache_write_tokens=totals["cache_write_tokens"],
    )
    assert derived["total"] == pytest.approx(by_hand, abs=1e-6)
    # The ratios the card is built on, pinned so a rate edit cannot silently
    # break the 0.1x / 1.25x relationship the hand figure above assumes.
    assert OPUS_RATES.cache_read_per_mtok == pytest.approx(0.1 * OPUS_RATES.input_per_mtok)
    assert OPUS_RATES.cache_write_per_mtok == pytest.approx(1.25 * OPUS_RATES.input_per_mtok)
    assert OPUS_RATES.output_per_mtok == pytest.approx(5.0 * OPUS_RATES.input_per_mtok)


def test_the_judges_usage_never_lands_in_the_agent_total(sean, tmp_path):
    """Judge spend is a separate source and stays one, on both routes.

    The judge runs on the harness's own credentials after the agent is done. It
    shares neither the run key nor the bill, and a leaderboard comparing agent
    cost across models is meaningless if grading noise is inside it.
    """
    key = sean["rows"][0]["run_key"]
    agent = extract_usage_from_litellm_log(
        _write(tmp_path, sean["rows"], "sean.jsonl"), *WINDOW, run_key=key)
    judge = {
        "input_tokens": 500_000, "output_tokens": 40_000,
        "cache_read_tokens": 900_000, "cache_write_tokens": 3_000,
        "total_tokens": 1_443_000, "cost_usd": 3.63, "request_count": 9,
        "model": "claude-sonnet-5", "usage_source": "litellm_judge",
    }
    for route in (True, False):
        out_dir = tmp_path / f"route_{route}"
        out_dir.mkdir()
        result: dict = {}
        save_usage(out_dir, result, dict(agent), "task", judge_usage=dict(judge),
                   model="claude-opus-5", oauth_route=route)
        written = json.loads((out_dir / "usage.json").read_text(encoding="utf-8"))

        for column in TOKEN_COLUMNS + ("request_count",):
            assert written["sources"]["agent"][column] == agent[column], (route, column)
            assert written["sources"]["judge"][column] == judge[column], (route, column)
        assert written["sources"]["agent"]["request_count"] == 156
        # Combined is the sum of the sources, so judge tokens are visible in the
        # roll-up while remaining attributable -- the failure mode is them being
        # inside sources.agent, where nothing could separate them again.
        assert written["input_tokens"] == agent["input_tokens"] + judge["input_tokens"]
        assert written["output_tokens"] == agent["output_tokens"] + judge["output_tokens"]


def test_an_oauth_run_stamps_its_provider_and_prices_from_tokens(sean, tmp_path):
    """After c21652c, ``cost_usd == 0`` no longer identifies the subscription
    route, so usage.json carries the routing flag itself. The agent's dollars
    on that route are derived from its own token counts, not from whatever
    LiteLLM happened to book against a prepaid key.
    """
    key = sean["rows"][0]["run_key"]
    agent = extract_usage_from_litellm_log(
        _write(tmp_path, sean["rows"], "sean.jsonl"), *WINDOW, run_key=key)
    assert agent["cost_usd"] < 0.01, "the subscription booked this at rounding noise"

    out_dir = tmp_path / "oauth"
    out_dir.mkdir()
    save_usage(out_dir, {}, dict(agent), "task", model="claude-opus-5",
               oauth_route=True)
    written = json.loads((out_dir / "usage.json").read_text(encoding="utf-8"))

    assert written["auth_provider"] == OAUTH
    expected = cost_breakdown(
        "claude-opus-5",
        input_tokens=agent["input_tokens"], output_tokens=agent["output_tokens"],
        cache_read_tokens=agent["cache_read_tokens"],
        cache_write_tokens=agent["cache_write_tokens"],
    )["total"]
    assert written["sources"]["agent"]["cost_usd"] == pytest.approx(expected, abs=1e-6)
    # Repricing moves dollars only. The token columns are the measurement.
    for column in TOKEN_COLUMNS:
        assert written["sources"]["agent"][column] == agent[column], column

    bedrock_dir = tmp_path / "bedrock"
    bedrock_dir.mkdir()
    save_usage(bedrock_dir, {}, dict(agent), "task", model="claude-opus-5",
               oauth_route=False)
    bedrock = json.loads((bedrock_dir / "usage.json").read_text(encoding="utf-8"))
    assert bedrock["auth_provider"] == BEDROCK
    assert bedrock["sources"]["agent"]["cost_usd"] == pytest.approx(
        agent["cost_usd"], abs=1e-9), "a Bedrock run keeps its recorded figure"


def test_the_run_key_never_reaches_the_delivered_artifact(sean, tmp_path):
    """On a keyless sidecar the run key IS the agent's bearer.

    save_usage strips the private channel (run_batch.py:795-796); usage.json is
    shipped in the bundle, so a key surviving into it would be a credential in
    a delivered file.
    """
    key = sean["rows"][0]["run_key"]
    agent = extract_usage_from_litellm_log(
        _write(tmp_path, sean["rows"], "sean.jsonl"), *WINDOW, run_key=key)
    agent["__run_key__"] = key
    agent["__agent_finished_ts__"] = EPOCH.timestamp()

    out_dir = tmp_path / "stripped"
    out_dir.mkdir()
    save_usage(out_dir, {}, agent, "task", model="claude-opus-5")
    raw = (out_dir / "usage.json").read_text(encoding="utf-8")
    assert "wcb::" not in raw
    assert "__run_key__" not in raw
    assert "__agent_finished_ts__" not in raw


# ===========================================================================
# E. Two reps of one task, running at once
# ===========================================================================


def test_two_reps_of_the_same_task_get_different_keys(shapes):
    """K-rerun puts run_1 and run_2 of one task on the wire together.

    The key is minted per ATTEMPT from a fresh uuid4 (runner.py:599) on top of a
    task_id that already carries its own uuid4 suffix (run_batch.py:2847,2853),
    so neither the task, the model, nor the minute they started in can collide
    them. A key derived from task+run slot would, which is why it is not.
    """
    minted = {_key("07_task_413") for _ in range(2000)}
    assert len(minted) == 2000
    for key in list(minted)[:5]:
        assert re.fullmatch(r"wcb::.+::[0-9a-f]{32}", key)


def test_two_reps_of_the_same_task_cannot_read_each_others_rows(shapes, tmp_path):
    """The same task, the same model, the same minute, one log, alternating rows."""
    rep1, rep2 = _key("07_task_413"), _key("07_task_413")
    rows1 = [_row(shapes, i, run_key=rep1, offset=i * 2) for i in range(12)]
    rows2 = [_row(shapes, i + 12, run_key=rep2, offset=i * 2 + 1) for i in range(12)]
    log = _write(tmp_path, [r for pair in zip(rows1, rows2) for r in pair],
                 "k_rerun.jsonl")

    got1 = extract_usage_from_litellm_log(log, *WINDOW, run_key=rep1)
    got2 = extract_usage_from_litellm_log(log, *WINDOW, run_key=rep2)
    _assert_totals_match(got1, _hand_total(rows1), "rep1")
    _assert_totals_match(got2, _hand_total(rows2), "rep2")
    for column in SUM_COLUMNS:
        assert got1[column] + got2[column] == _hand_total(rows1 + rows2)[column], column


def test_a_retry_of_one_attempt_does_not_bill_the_earlier_attempt(shapes, tmp_path):
    """A stall-recovery turn or a rolled-back attempt keeps the SAME key, and a
    fresh attempt of the same task gets a new one.

    The rows a rolled-back attempt leaves behind stay in its own run's total --
    they were really spent -- and cannot migrate to the retry, which is a
    different attempt with a different key.
    """
    first, second = _key("retried"), _key("retried")
    aborted = [_row(shapes, i, run_key=first, offset=i) for i in range(5)]
    recovered = [_row(shapes, 5 + i, run_key=first, offset=20 + i) for i in range(3)]
    fresh = [_row(shapes, 10 + i, run_key=second, offset=40 + i) for i in range(7)]
    log = _write(tmp_path, aborted + recovered + fresh, "retry.jsonl")

    got_first = extract_usage_from_litellm_log(log, *WINDOW, run_key=first)
    got_second = extract_usage_from_litellm_log(log, *WINDOW, run_key=second)
    _assert_totals_match(got_first, _hand_total(aborted + recovered), "attempt 1")
    _assert_totals_match(got_second, _hand_total(fresh), "attempt 2")


# ===========================================================================
# The reconciliation invariant, under parallelism
# ===========================================================================


def test_the_three_ledgers_close_against_sources_agent_on_a_shared_log(shapes, tmp_path):
    """Σ(per-message) + internal_calls + post_agent_calls == sources.agent.

    The invariant e18298c and 80f12fd established, asserted here with two other
    runs' rows interleaved into the same file. Both sides of the equality are
    computed by the production code from the same shared log: the left by
    ``_attribute_per_message_cost``, the right by
    ``extract_usage_from_litellm_log``. If either selector took a foreign row
    the two would disagree, and if both took the same foreign rows the totals
    would agree with each other but not with the run's own eight.
    """
    victim = _key("victim")
    boundary = (EPOCH + timedelta(seconds=60)).timestamp()

    turns = [_row(shapes, i, run_key=victim, offset=i * 4) for i in range(6)]
    internal = [
        _row(shapes, 1, run_key=victim, offset=2, purpose="embeddings"),
        _row(shapes, 30, run_key=victim, offset=18, purpose="compaction"),
    ]
    late = [_row(shapes, 40, run_key=victim, offset=75)]
    mine = turns + internal + late

    foreign: list[dict] = []
    for n, name in enumerate(("neighbour_a", "neighbour_b")):
        other = _key(name)
        foreign += [_row(shapes, 50 + n * 7 + i, run_key=other, offset=i * 4 + 1.5)
                    for i in range(7)]
    log = _write(tmp_path, sorted(mine + foreign, key=lambda r: r["ts"]),
                 "reconcile.jsonl")

    traj = {"messages": [{"message": {"role": role, "content": ""}}
                         for role in ["user", "assistant"] * 6]}
    report = _attribute_per_message_cost(
        traj, str(log), victim, oauth_route=True, model="claude-opus-5",
        agent_finished_ts=boundary)

    assert report["status"] == "attributed"
    assert report["rows_selected"] == 9
    assert report["rows_internal"] == 2
    assert report["rows_post_agent"] == 1
    assert report["rows_unmatched"] == 0

    agent = extract_usage_from_litellm_log(log, *WINDOW, run_key=victim)
    _assert_totals_match(agent, _hand_total(mine), "sources.agent")
    assert agent["request_count"] == 9, "23 rows in the file, 9 of them this run's"

    per_message = {c: 0 for c in TOKEN_COLUMNS}
    counted = 0
    alias = {"input_tokens": "input", "output_tokens": "output",
             "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    for message in traj["messages"]:
        usage = message["message"].get("usage")
        if not usage:
            continue
        counted += 1
        for column in TOKEN_COLUMNS:
            per_message[column] += usage[alias[column]]

    for column in TOKEN_COLUMNS:
        assert (per_message[column]
                + report["internal_calls"][column]
                + report["post_agent_calls"][column]) == agent[column], column
    assert (counted
            + report["internal_calls"]["request_count"]
            + report["post_agent_calls"]["request_count"]) == agent["request_count"]


def test_a_co_tenants_rows_cannot_shift_the_per_message_blocks(shapes, tmp_path):
    """The 809e3bf failure mode, directly: zip() truncating on a polluted list.

    Selecting by window put a neighbour's rows into the middle of the victim's,
    so every later message took the previous request's tokens and the tail fell
    off the end in silence. Attributed by key, each message must carry the
    numbers of its own row no matter how many neighbours are interleaved.
    """
    victim = _key("victim")
    mine = [_row(shapes, i, run_key=victim, offset=i * 4) for i in range(6)]
    foreign = [_row(shapes, 60 + i, run_key=_key("neighbour"), offset=i * 4 + 2)
               for i in range(6)]
    log = _write(tmp_path, [r for pair in zip(mine, foreign) for r in pair],
                 "shift.jsonl")

    traj = {"messages": [{"message": {"role": role, "content": ""}}
                         for role in ["user", "assistant"] * 6]}
    report = _attribute_per_message_cost(traj, str(log), victim,
                                         oauth_route=True, model="claude-opus-5")
    assert report["status"] == "attributed"

    assistants = [m for m in traj["messages"] if m["message"]["role"] == "assistant"]
    assert len(assistants) == 6
    for message, row in zip(assistants, mine):
        usage = message["message"]["usage"]
        assert usage["input"] == row["input_tokens"]
        assert usage["output"] == row["output_tokens"]
        assert usage["cacheRead"] == row["cache_read_tokens"]
        assert usage["cacheWrite"] == row["cache_write_tokens"]


def test_the_combined_roll_up_of_parallel_runs_equals_the_log(three_way, tmp_path):
    """Batch-level arithmetic: N runs' usage.json files sum to the shared log.

    The property a delivery's cost table depends on. Preflight is deliberately
    excluded here -- it is replicated into every task's artifact by design
    (save_usage's own note), so it is the one source that must not be summed
    across runs.
    """
    keys, owned, shared, log = three_way
    rolled = {c: 0 for c in SUM_COLUMNS}
    for i, key in enumerate(keys):
        agent = extract_usage_from_litellm_log(log, *WINDOW, run_key=key)
        out_dir = tmp_path / f"run_{i}"
        out_dir.mkdir()
        save_usage(out_dir, {}, dict(agent), f"task_{i}", model="claude-opus-5")
        written = json.loads((out_dir / "usage.json").read_text(encoding="utf-8"))
        assert written["sources"]["agent"]["request_count"] == len(owned[key])
        for column in SUM_COLUMNS:
            rolled[column] += written[column]
    truth = _hand_total(shared)
    for column in SUM_COLUMNS:
        assert rolled[column] == truth[column], column


def test_recompute_combined_keeps_the_sources_separable(sean, tmp_path):
    """Summing sources must not mutate them: the per-source figures stay readable
    after the roll-up, which is what makes an agent-vs-judge split auditable."""
    key = sean["rows"][0]["run_key"]
    agent = extract_usage_from_litellm_log(
        _write(tmp_path, sean["rows"], "sean.jsonl"), *WINDOW, run_key=key)
    judge = {"input_tokens": 11, "output_tokens": 22, "cache_read_tokens": 33,
             "cache_write_tokens": 44, "total_tokens": 110, "cost_usd": 1.5,
             "request_count": 2}
    sources = {"agent": dict(agent), "judge": dict(judge)}
    combined = recompute_combined(sources, "task")
    for column in TOKEN_COLUMNS:
        assert combined[column] == agent[column] + judge[column], column
        assert sources["agent"][column] == agent[column], column
        assert sources["judge"][column] == judge[column], column
