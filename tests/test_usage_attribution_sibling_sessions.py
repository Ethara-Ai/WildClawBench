"""Offline replay of the 2026-09-19 gama koji_sloan rerun, both lanes.

Both lanes shipped ``usage_attribution: {"status": "failed"}`` with every
per-message cost block empty — run_1 thirty-four rows over its message count,
run_2 two. Nothing is wrong with the labels: the sidecar named every request it
could, the run totals are right, and no row billed zero. The rows are simply
not this transcript's.

OpenClaw ran a SECOND session inside the rep. The agent's own ``cron`` tool
scheduled a main-session ``systemEvent``; the gateway enqueued it and the
HEALTH loop answered it on a session of its own, which the output collector
copied out beside the delivered one at ``<run>/task_output/sessions/``. run_1's
sibling, ``fdcddd7e-afb8-4136-b97c-659f159dbb5c.jsonl``, holds exactly 34
assistant messages; run_2's holds exactly 2; both are registered under
``agent:main:chat`` with ``origin {label: heartbeat, provider: cron-event}``.

The join is on tokens, because nothing else can join them: session timestamps
run on the agent's shimmed clock, tens of days from the sidecar's real UTC
``ts``. It is also exact — across both lanes not one sibling message's
four-column tuple collides with a delivered message's, and the rows left after
absorption are tuple-equal to the delivered transcript's assistant messages in
order, 164 for 164 and 152 for 152.

willie_prince run_1 is the control: it shipped ``attributed`` with a sibling
transcript sitting in the same directory, whose single turn was one request the
sidecar had already named ``heartbeat`` and booked to internal_calls. There is
no surplus there, so nothing may be absorbed, and the rep must replay exactly
as delivered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_batch import (
    _TRANSCRIPT_TURN_PURPOSES,
    _attribute_per_message_cost,
    _sibling_session_files,
    _stamped_usage_attribution,
    save_usage,
)

FIXTURE = (Path(__file__).parent / "fixtures"
           / "usage_replay_koji_sessions_20260919.json")

_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)
_SESSION_KEYS = ("input", "output", "cacheRead", "cacheWrite")
_KOJI = ("koji_run_1", "koji_run_2")


@pytest.fixture(scope="module")
def replay():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _lane(replay, name):
    return replay["runs"][name]


def _delivered_tuples(lane):
    """Each delivered assistant message's own four token counts, in order."""
    return [tuple(int(n) for n in m[2:].split(","))
            for m in lane["messages"] if m.startswith("a:")]


def _traj(lane):
    """The delivered transcript as ``_build_trajectory`` hands it over.

    Assistant messages carry the usage block chat.jsonl really shipped: real
    token counts, zeroed cost. That is the shape the back-fill updates in
    place, so the replay exercises the merge and not a greenfield write.
    """
    messages = []
    for entry in lane["messages"]:
        if entry == "user":
            messages.append({"message": {"role": "user", "content": []}})
            continue
        tup = tuple(int(n) for n in entry[2:].split(","))
        messages.append({"message": {
            "role": "assistant",
            "content": [],
            "usage": dict(zip(_SESSION_KEYS, tup), totalTokens=sum(tup), cost={
                "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                "total": 0}),
        }})
    return {"messages": messages}


def _rows(lane):
    """The lane's real sidecar rows, with the two dropped columns restored."""
    return [dict(r, run_key=lane["run_key"], model=lane["model"])
            for r in lane["rows"]]


def _rep(tmp_path, lane, *, name, sessions=True, extra=None, drop=()):
    """Write the rep's on-disk shape: usage log + chat.jsonl + sessions/.

    ``chat.jsonl`` is written as the delivered transcript so the main-session
    copy in sessions/ is byte-identical to it, which is how the discovery step
    is meant to recognise it.
    """
    run_dir = tmp_path / name
    (run_dir / "task_output" / "sessions").mkdir(parents=True, exist_ok=True)
    (run_dir / "usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _rows(lane)), encoding="utf-8")

    chat = "".join(
        json.dumps({"type": "message", "message": {"role": "user"}}
                   if m == "user" else
                   {"type": "message", "message": {
                       "role": "assistant",
                       "usage": dict(zip(_SESSION_KEYS,
                                         (int(n) for n in m[2:].split(","))))}}
                   ) + "\n"
        for m in lane["messages"])
    (run_dir / "chat.jsonl").write_text(chat, encoding="utf-8")
    sessions_dir = run_dir / "task_output" / "sessions"
    if sessions:
        (sessions_dir / "chat.jsonl").write_text(chat, encoding="utf-8")
        (sessions_dir / "sessions.json").write_text(
            json.dumps(lane["sessions_registry"]), encoding="utf-8")
        for fname, entries in lane["sibling_sessions"].items():
            if fname in drop:
                continue
            (sessions_dir / fname).write_text(
                "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    for fname, body in (extra or {}).items():
        (sessions_dir / fname).write_text(body, encoding="utf-8")
    return run_dir


def _session_lines(tuples):
    """A synthetic session transcript carrying exactly ``tuples``."""
    return "".join(json.dumps({"type": "message", "message": {
        "role": "assistant",
        "usage": dict(zip(_SESSION_KEYS, t), totalTokens=sum(t)),
    }}) + "\n" for t in tuples)


def _attribute(lane, run_dir):
    return _attribute_per_message_cost(
        _traj(lane), str(run_dir / "usage.jsonl"), lane["run_key"],
        agent_finished_ts=lane.get("agent_finished_ts"), run_dir=run_dir)


def _attribute_into(lane, run_dir, traj):
    return _attribute_per_message_cost(
        traj, str(run_dir / "usage.jsonl"), lane["run_key"],
        agent_finished_ts=lane.get("agent_finished_ts"), run_dir=run_dir)


# ============================================================================
# The runs as delivered
# ============================================================================


def test_fixture_is_the_two_runs_that_failed_and_the_one_that_did_not(replay):
    assert _lane(replay, "koji_run_1")["reported_attribution"] == {
        "status": "failed", "messages": 164, "rows_selected": 224,
        "rows_internal": 25, "rows_post_agent": 1, "rows_zero_token": 0,
        "rows_unmatched": 34,
    }
    assert _lane(replay, "koji_run_2")["reported_attribution"] == {
        "status": "failed", "messages": 152, "rows_selected": 182,
        "rows_internal": 28, "rows_post_agent": 0, "rows_zero_token": 0,
        "rows_unmatched": 2,
    }
    assert _lane(replay, "willie_run_1")["reported_attribution"] == {
        "status": "attributed", "messages": 112, "rows_selected": 145,
        "rows_internal": 33, "rows_post_agent": 0, "rows_zero_token": 0,
        "rows_unmatched": 0,
    }


@pytest.mark.parametrize("name,rows,assistants,unmatched,sibling_messages", [
    ("koji_run_1", 224, 164, 34, 34),
    ("koji_run_2", 182, 152, 2, 2),
    ("willie_run_1", 145, 112, 0, 1)])
def test_the_sibling_transcript_holds_exactly_the_unmatched_rows(
        replay, name, rows, assistants, unmatched, sibling_messages):
    """The count coincidence that started this, stated as data.

    One sibling transcript per rep, and on the two failing lanes its assistant
    message count IS the shipped rows_unmatched. On willie it is not, because
    willie had no surplus at all.
    """
    lane = _lane(replay, name)
    assert len(lane["rows"]) == rows
    assert sum(1 for m in lane["messages"] if m.startswith("a:")) == assistants
    assert lane["reported_attribution"]["rows_unmatched"] == unmatched
    assert len(lane["sibling_sessions"]) == 1
    entries, = lane["sibling_sessions"].values()
    assert sum(1 for e in entries
               if (e.get("message") or {}).get("role") == "assistant") \
        == sibling_messages


@pytest.mark.parametrize("name,provider,key", [
    ("koji_run_1", "cron-event", "agent:main:chat"),
    ("koji_run_2", "cron-event", "agent:main:chat"),
    ("willie_run_1", "heartbeat", "agent:main:main")])
def test_the_registry_names_the_sibling_a_self_issued_session(
        replay, name, provider, key):
    """Why there was a second transcript, from the rep's own sessions.json.

    Both koji siblings are cron-event deliveries; willie's is a plain
    heartbeat. Note what the registry does NOT give: its one entry points at
    the SIBLING, not at the delivered transcript, which is why the main session
    has to be recognised by its bytes instead.
    """
    lane = _lane(replay, name)
    registry = lane["sessions_registry"]
    assert list(registry) == [key]
    meta, = registry.values()
    assert meta["origin"]["label"] == "heartbeat"
    assert meta["origin"]["provider"] == provider
    sibling_file, = lane["sibling_sessions"]
    assert meta["sessionId"] == sibling_file[:-len(".jsonl")]


@pytest.mark.parametrize("name", _KOJI)
def test_no_sibling_message_could_be_mistaken_for_a_delivered_one(
        replay, name):
    """The join's precondition, checked before the join is trusted.

    Not one four-column tuple is shared between the sibling transcript and the
    delivered one, and neither side repeats a tuple internally. So absorbing on
    the tuple cannot take a row a delivered message needed.
    """
    lane = _lane(replay, name)
    entries, = lane["sibling_sessions"].values()
    sibling = [tuple(m["usage"][k] for k in _SESSION_KEYS)
               for m in (e.get("message") or {} for e in entries)
               if m.get("role") == "assistant"]
    delivered = _delivered_tuples(lane)
    assert set(sibling) & set(delivered) == set()
    assert len(set(sibling)) == len(sibling)
    assert len(set(delivered)) == len(delivered)


# ============================================================================
# The gate
# ============================================================================


@pytest.mark.parametrize("name,absorbed", [("koji_run_1", 34), ("koji_run_2", 2)])
def test_the_replay_reaches_attributed_through_the_sibling_session(
        replay, tmp_path, name, absorbed):
    lane = _lane(replay, name)
    run_dir = _rep(tmp_path, lane, name=name)
    report = _attribute(lane, run_dir)

    shipped = lane["reported_attribution"]
    assert report["status"] == "attributed"
    assert report["rows_unmatched"] == 0
    assert report["rows_other_session"] == absorbed
    assert report["messages"] == shipped["messages"]
    assert report["rows_selected"] == shipped["rows_selected"]
    assert report["rows_internal"] == shipped["rows_internal"]
    assert report["rows_post_agent"] == shipped["rows_post_agent"]
    assert report["rows_zero_token"] == 0

    sibling_file, = lane["sibling_sessions"]
    session_id = sibling_file[:-len(".jsonl")]
    block = report["other_session_calls"]
    assert block["request_count"] == absorbed
    assert list(block["by_session"]) == [session_id]
    entry = block["by_session"][session_id]
    assert entry["request_count"] == absorbed
    assert entry["assistant_messages"] == absorbed
    assert entry["session_file"] == sibling_file
    assert entry["session_key"] == "agent:main:chat"
    assert entry["origin"]["provider"] == "cron-event"


@pytest.mark.parametrize("name", _KOJI)
def test_every_row_left_over_is_the_message_that_billed_it(replay, tmp_path, name):
    """The exact-join proof, end to end.

    Each delivered assistant message already carries its own token counts in
    chat.jsonl. After the sibling takes its rows, the rows the back-fill writes
    onto those messages agree with them column for column and in order — which
    is the strongest statement available that the absorption removed the right
    rows and not merely the right NUMBER of rows.
    """
    lane = _lane(replay, name)
    run_dir = _rep(tmp_path, lane, name=name)
    traj = _traj(lane)
    report = _attribute_into(lane, run_dir, traj)
    assert report["status"] == "attributed"

    written = [tuple(m["message"]["usage"][k] for k in _SESSION_KEYS)
               for m in traj["messages"]
               if m["message"]["role"] == "assistant"]
    assert written == _delivered_tuples(lane)


@pytest.mark.parametrize("name", _KOJI)
def test_the_five_buckets_add_back_up_to_the_agent_total(replay, tmp_path, name):
    """per-message + internal + post_agent + zero_token + other_session.

    On all four token columns and on the request count, against what the
    delivered usage.json booked for sources.agent. Absorbing a row moves the
    claim that a delivered message produced it; it does not move the money out
    of the run.
    """
    lane = _lane(replay, name)
    run_dir = _rep(tmp_path, lane, name=name)
    traj = _traj(lane)
    report = _attribute_into(lane, run_dir, traj)
    assert report["status"] == "attributed"

    alias = {"input_tokens": "input", "output_tokens": "output",
             "cache_read_tokens": "cacheRead", "cache_write_tokens": "cacheWrite"}
    per_message = {c: 0 for c in _TOKEN_COLUMNS}
    counted = 0
    for message in traj["messages"]:
        usage = message["message"].get("usage")
        if not usage or message["message"]["role"] != "assistant":
            continue
        counted += 1
        for column in _TOKEN_COLUMNS:
            per_message[column] += usage[alias[column]]

    empty = {k: 0 for k in (*_TOKEN_COLUMNS, "request_count")}
    buckets = [report["internal_calls"],
               report.get("post_agent_calls") or empty,
               report.get("zero_token_calls") or empty,
               report.get("other_session_calls") or empty]
    expected = lane["expected_agent_totals"]
    for column in _TOKEN_COLUMNS:
        assert per_message[column] + sum(b[column] for b in buckets) \
            == expected[column], column
    assert counted == report["messages"]
    assert counted + sum(b["request_count"] for b in buckets) \
        == expected["request_count"] == report["rows_selected"]


# ============================================================================
# What must NOT happen
# ============================================================================


@pytest.mark.parametrize("name", ("koji_run_1", "koji_run_2", "willie_run_1"))
def test_without_a_sessions_directory_the_run_replays_as_delivered(
        replay, tmp_path, name):
    """Requirement zero: a rep with no sibling store behaves exactly as today.

    Checked twice — with the directory absent, and with ``run_dir`` not passed
    at all, which is the shape every other caller of this function still uses.
    """
    lane = _lane(replay, name)
    run_dir = _rep(tmp_path, lane, name=f"{name}_bare", sessions=False)
    shipped = lane["reported_attribution"]

    report = _attribute(lane, run_dir)
    assert {k: report[k] for k in shipped} == shipped
    assert report["rows_other_session"] == 0
    assert "other_session_calls" not in report

    unplumbed = _attribute_per_message_cost(
        _traj(lane), str(run_dir / "usage.jsonl"), lane["run_key"],
        agent_finished_ts=lane.get("agent_finished_ts"))
    assert {k: unplumbed[k] for k in shipped} == shipped


def test_a_rep_that_already_reconciles_absorbs_nothing(replay, tmp_path):
    """willie_prince run_1: sibling present, attributed before and after.

    Its one sibling turn was a single request the sidecar named ``heartbeat``,
    so the row was booked to internal_calls and never became a turn candidate.
    With no surplus there is nothing to explain and nothing may be taken —
    otherwise a delivered message would be left short.
    """
    lane = _lane(replay, "willie_run_1")
    run_dir = _rep(tmp_path, lane, name="willie")
    assert len(_sibling_session_files(run_dir)) == 1

    report = _attribute(lane, run_dir)
    assert {k: report[k] for k in lane["reported_attribution"]} \
        == lane["reported_attribution"]
    assert report["rows_other_session"] == 0
    assert "other_session_calls" not in report
    assert report["internal_calls"]["request_count"] == 33


def test_a_foreign_session_file_absorbs_nothing_and_the_run_still_fails(
        replay, tmp_path):
    """The pollution case: a session file that is not this rep's work.

    Its tuples are koji run_1's own sibling tuples with one column moved by
    one — as plausible as a token count gets, and as close to the truth as a
    stale or copied session file could plausibly land. Count alone would let it
    take 34 rows and declare the run reconciled. The exact join gives it
    nothing, and because the real sibling is not there, the 34 rows that have
    no message stay unmatched and the run stays failed.
    """
    lane = _lane(replay, "koji_run_1")
    entries, = lane["sibling_sessions"].values()
    real = [tuple(m["usage"][k] for k in _SESSION_KEYS)
            for m in (e.get("message") or {} for e in entries)
            if m.get("role") == "assistant"]
    forged = [(i, o, cr, cw + 1) for i, o, cr, cw in real]

    run_dir = _rep(tmp_path, lane, name="polluted",
                   drop=set(lane["sibling_sessions"]),
                   extra={"deadbeef-0000-0000-0000-000000000000.jsonl":
                          _session_lines(forged)})
    assert len(_sibling_session_files(run_dir)) == 1

    report = _attribute(lane, run_dir)
    assert report["rows_other_session"] == 0
    assert "other_session_calls" not in report
    assert {k: report[k] for k in lane["reported_attribution"]} \
        == lane["reported_attribution"]


def test_a_foreign_file_cannot_ride_in_beside_the_real_sibling(
        replay, tmp_path):
    """Same forgery, with the genuine sibling present too.

    The rep reconciles on the 34 rows its own session billed, and the foreign
    file adds nothing to the ledger — no second entry under by_session, no
    extra request_count, no extra tokens.
    """
    lane = _lane(replay, "koji_run_1")
    entries, = lane["sibling_sessions"].values()
    forged = [(i, o, cr, cw + 1) for i, o, cr, cw in
              ((m["usage"][k] for k in _SESSION_KEYS)
               for m in (e.get("message") or {} for e in entries)
               if m.get("role") == "assistant")]

    run_dir = _rep(tmp_path, lane, name="mixed",
                   extra={"deadbeef-0000-0000-0000-000000000000.jsonl":
                          _session_lines(forged)})
    assert len(_sibling_session_files(run_dir)) == 2

    report = _attribute(lane, run_dir)
    assert report["status"] == "attributed"
    assert report["rows_other_session"] == 34
    assert list(report["other_session_calls"]["by_session"]) == \
        [next(iter(lane["sibling_sessions"]))[:-len(".jsonl")]]


def test_a_row_cannot_be_absorbed_twice(replay, tmp_path):
    """A second session file repeating the first one's tuples takes nothing.

    One row per message and one message per row, across files as well as
    within one. Without that, a duplicated session file — which is what a
    retried or copied store looks like — would double-count 34 rows into the
    ledger and leave the transcript 34 messages short.
    """
    lane = _lane(replay, "koji_run_2")
    entries, = lane["sibling_sessions"].values()
    same = [tuple(m["usage"][k] for k in _SESSION_KEYS)
            for m in (e.get("message") or {} for e in entries)
            if m.get("role") == "assistant"]

    run_dir = _rep(tmp_path, lane, name="doubled",
                   extra={"zzzzzzzz-0000-0000-0000-000000000000.jsonl":
                          _session_lines(same + same)})
    assert len(_sibling_session_files(run_dir)) == 2

    report = _attribute(lane, run_dir)
    assert report["status"] == "attributed"
    assert report["rows_other_session"] == 2
    assert report["other_session_calls"]["request_count"] == 2
    assert len(report["other_session_calls"]["by_session"]) == 1


def test_only_transcripts_of_this_rep_are_even_looked_at(replay, tmp_path):
    """Discovery's own guards, one file at a time.

    The delivered transcript is skipped under its own name AND under a session
    id — the collector writes it as chat.jsonl, but the check that matters is
    the digest, because the registry points at the sibling and cannot be asked
    which file is the main one. The registry itself is not a transcript, and
    neither is the lock file openclaw leaves beside a session (seen on the
    2026-09-19 sean_callahan run_2).
    """
    lane = _lane(replay, "koji_run_1")
    run_dir = _rep(tmp_path, lane, name="koji_run_1")
    sessions = run_dir / "task_output" / "sessions"
    (sessions / "aaaaaaaa-0000-0000-0000-000000000000.jsonl").write_text(
        (run_dir / "chat.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    (sessions / "bbbbbbbb-0000-0000-0000-000000000000.jsonl.lock").write_text(
        "{}", encoding="utf-8")

    found = {p.name for p in _sibling_session_files(run_dir)}
    assert found == set(lane["sibling_sessions"])

    report = _attribute(lane, run_dir)
    assert report["status"] == "attributed"
    assert report["rows_other_session"] == 34


def test_usage_json_carries_the_block_and_score_json_carries_only_the_count(
        replay, tmp_path):
    """Where each half of the stamp is delivered.

    usage.json is where the money is reconciled, so it gets the ledger beside
    internal_calls. score.json carries verdicts, so it gets the row count that
    explains the verdict and none of the ledgers.
    """
    lane = _lane(replay, "koji_run_1")
    run_dir = _rep(tmp_path, lane, name="koji_run_1")
    result = {"usage_attribution": _attribute(lane, run_dir)}

    save_usage(run_dir, result, dict(lane["expected_agent_totals"],
                                     cost_usd=0.0), "koji")
    out = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert out["other_session_calls"]["request_count"] == 34
    assert out["usage_attribution"]["rows_other_session"] == 34
    assert "other_session_calls" not in out["usage_attribution"]
    assert set(out["usage_attribution"]) == {
        "status", "messages", "rows_selected", "rows_internal",
        "rows_post_agent", "rows_zero_token", "rows_other_session",
        "rows_unmatched"}

    stamp = _stamped_usage_attribution(result)
    scores = {k: v for k, v in stamp.items()
              if k not in ("internal_calls", "post_agent_calls",
                           "zero_token_calls", "other_session_calls")}
    assert scores["rows_other_session"] == 34
    assert not any(k.endswith("_calls") for k in scores)


@pytest.mark.parametrize("name", _KOJI)
def test_the_sibling_turns_stay_out_of_the_transcript(replay, tmp_path, name):
    """They are accounted for, not delivered, and not named a transcript turn.

    Absorbing a row adds no message, so the trajectory the judge reads has the
    same length before and after, and ``_TRANSCRIPT_TURN_PURPOSES`` — the list
    of labels whose turn IS in the delivered transcript — is untouched.
    """
    lane = _lane(replay, name)
    run_dir = _rep(tmp_path, lane, name=name)
    traj = _traj(lane)
    before = len(traj["messages"])
    report = _attribute_into(lane, run_dir, traj)

    assert report["status"] == "attributed"
    assert len(traj["messages"]) == before
    assert _TRANSCRIPT_TURN_PURPOSES == frozenset({"memory_flush"})
    assert "heartbeat_turns_in_transcript" not in report
    assert "memory_flush_turns_in_transcript" not in report
