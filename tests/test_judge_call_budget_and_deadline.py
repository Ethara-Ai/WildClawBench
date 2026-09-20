"""F5 — judge call budget (a) and per-member pool deadline (b).

(a) `_graded_chunks` escalates a failed chunk by retrying it at full size and
    then re-splitting it into halves, twice deep. The tree is
    T(0)=1+1+2*T(1), T(1)=1+1+2*T(2), T(2)=2 -> 14 council invocations for ONE
    top-level chunk, i.e. 42 member API calls on a 3-member council, spent on a
    chunk that was never going to grade. `WCB_JUDGE_MAX_COUNCIL_CALLS` caps the
    escalation; the chunk then degrades through the existing synthetic-abstain
    path instead of burning the budget of every chunk behind it.

(b) `_run_council` used `pool.map`, which yields in submission order — one
    member wedged in a socket read held the whole council forever with the other
    members' verdicts already in hand. `WCB_JUDGE_MEMBER_DEADLINE_S` turns a
    straggler into an ordinary failed member.
"""
from __future__ import annotations

import time

import pytest

from src.utils import grading

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm"
_KIMI = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/kimi"

_VERDICT = "1. c [[RATIONALE: r]] [[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: No]]"


def _members(n: int = 3):
    roster = [
        grading.CouncilMember(family="sonnet", model=_SONNET),
        grading.CouncilMember(family="glm", model=_GLM),
        grading.CouncilMember(family="kimi", model=_KIMI),
    ]
    return roster[:n]


# ---------------------------------------------------------------------------
# tunable parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (None, grading._DEFAULT_MAX_COUNCIL_CALLS),
    ("", grading._DEFAULT_MAX_COUNCIL_CALLS),
    ("nonsense", grading._DEFAULT_MAX_COUNCIL_CALLS),
    ("0", grading._DEFAULT_MAX_COUNCIL_CALLS),
    ("-4", grading._DEFAULT_MAX_COUNCIL_CALLS),
    ("21", 21),
])
def test_max_council_calls_tunable_is_guarded(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WCB_JUDGE_MAX_COUNCIL_CALLS", raising=False)
    else:
        monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", raw)
    assert grading._judge_max_council_calls() == expected


@pytest.mark.parametrize("raw,expected", [
    (None, grading._DEFAULT_MEMBER_DEADLINE_S),
    ("", grading._DEFAULT_MEMBER_DEADLINE_S),
    ("nonsense", grading._DEFAULT_MEMBER_DEADLINE_S),
    ("0", grading._DEFAULT_MEMBER_DEADLINE_S),
    ("12.5", 12.5),
])
def test_member_deadline_tunable_is_guarded(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WCB_JUDGE_MEMBER_DEADLINE_S", raising=False)
    else:
        monkeypatch.setenv("WCB_JUDGE_MEMBER_DEADLINE_S", raw)
    assert grading._judge_member_deadline_s() == expected


# ---------------------------------------------------------------------------
# F5a — budget stops the retry/re-split fan-out
# ---------------------------------------------------------------------------


def _always_failing_council(monkeypatch, members, counter):
    """_grade_council that always errors, counting invocations AND member calls."""
    monkeypatch.setattr(grading, "council_members", lambda: members)
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)

    def _fake(rubrics, system, user_for_member, mem, images=None):
        counter["invocations"] += 1
        counter["member_calls"] += len(mem)
        return {
            "overall_score": 0.0, "rubric_weights_percentage": 0.0,
            "criteria_total": len(rubrics), "criteria_passed": 0,
            "criteria_failed": 0, "criteria_abstained": len(rubrics),
            "criteria": [], "judge_model": "council", "judge_council": {},
            "truncation_flags": [], "abstention_flags": [],
            "usage": dict(grading._ZERO_USAGE),
            "error": "parse: no verdicts parsed",
        }

    monkeypatch.setattr(grading, "_grade_council", _fake)


def test_unbudgeted_fanout_would_reach_fourteen_invocations(monkeypatch, tmp_path):
    """Documents the cost the cap exists to bound (T(0)=14).

    12 criteria is the smallest size that walks the whole tree: the re-split
    guard is `len(chunk) > 4`, so halves must still exceed 4 at depth 1."""
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "9999")
    counter = {"invocations": 0, "member_calls": 0}
    members = _members(3)
    _always_failing_council(monkeypatch, members, counter)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(12)]
    grading.grade_with_rubric(rubrics, "task", tmp_path, "transcript")
    assert counter["invocations"] == 14
    assert counter["member_calls"] == 42


def test_budget_exhaustion_stops_escalation(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "8")
    counter = {"invocations": 0, "member_calls": 0}
    members = _members(3)
    _always_failing_council(monkeypatch, members, counter)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(12)]
    with caplog.at_level("ERROR"):
        grading.grade_with_rubric(rubrics, "task", tmp_path, "transcript")
    # Primary pass + one full-size retry = 2 invocations = 6 member calls.
    # A re-split commits to both halves -> 2 more invocations (6 calls) -> 12 > 8.
    assert counter["invocations"] == 2
    assert counter["member_calls"] == 6
    assert "JUDGE CALL BUDGET EXHAUSTED" in caplog.text
    assert "re-split" in caplog.text
    assert counter["invocations"] * len(members) <= 8


def test_budget_denied_chunks_degrade_to_synthetic_abstains(
    monkeypatch, tmp_path, caplog,
):
    """The remainder takes the pre-existing failed-chunk path, not a new one."""
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "8")
    monkeypatch.setenv("WCB_JUDGE_RUBRIC_BATCH_SIZE", "6")
    counter = {"invocations": 0, "member_calls": 0}
    members = _members(3)
    _always_failing_council(monkeypatch, members, counter)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(12)]
    with caplog.at_level("ERROR"):
        out = grading.grade_with_rubric(rubrics, "task", tmp_path, "transcript")
    # chunk 1: primary(3) + retry(6), re-split refused. chunk 2: primary(9),
    # retry refused -- a later chunk is still always graded once.
    assert counter["invocations"] == 3
    assert "refusing to re-split" in caplog.text
    assert "refusing to retry" in caplog.text
    assert out["criteria_total"] == 12
    assert out["criteria_abstained"] == 12
    assert out["criteria_passed"] == 0
    assert out["overall_score"] == 0.0
    assert sorted(out["abstention_flags"]) == list(range(12))
    assert all(c["resolved_by"] == "human_eval" for c in out["criteria"])
    assert all(c["votes"] == "Abstain/Abstain/Abstain" for c in out["criteria"])
    assert out["error"] == "all rubric batches failed to grade"


def test_budget_of_one_invocation_blocks_even_the_retry(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "3")
    counter = {"invocations": 0, "member_calls": 0}
    members = _members(3)
    _always_failing_council(monkeypatch, members, counter)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(8)]
    with caplog.at_level("ERROR"):
        grading.grade_with_rubric(rubrics, "task", tmp_path, "transcript")
    assert counter["invocations"] == 1
    assert "refusing to retry" in caplog.text


def test_budget_is_per_grade_with_rubric_call(monkeypatch, tmp_path):
    """A fresh call starts with a fresh budget — no cross-task leakage."""
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "8")
    members = _members(3)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(8)]
    for _ in range(2):
        counter = {"invocations": 0, "member_calls": 0}
        _always_failing_council(monkeypatch, members, counter)
        grading.grade_with_rubric(rubrics, "task", tmp_path, "transcript")
        assert counter["invocations"] == 2


def test_healthy_grade_never_touches_the_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("WCB_JUDGE_MAX_COUNCIL_CALLS", "3")
    members = _members(3)
    monkeypatch.setattr(grading, "council_members", lambda: members)
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)
    calls = {"n": 0}

    def _fake(rubrics, system, user_for_member, mem, images=None):
        calls["n"] += 1
        return {
            "overall_score": 1.0, "rubric_weights_percentage": 100.0,
            "criteria_total": len(rubrics), "criteria_passed": len(rubrics),
            "criteria_failed": 0, "criteria_abstained": 0,
            "criteria": [], "judge_model": "council",
            "judge_council": {"per_member_verdict_count": {"sonnet": len(rubrics)}},
            "truncation_flags": [], "abstention_flags": [],
            "usage": dict(grading._ZERO_USAGE),
        }

    monkeypatch.setattr(grading, "_grade_council", _fake)
    out = grading.grade_with_rubric(
        [{"criterion": "c0", "weight": 1}], "task", tmp_path, "transcript")
    assert calls["n"] == 1
    assert out["overall_score"] == 1.0


# ---------------------------------------------------------------------------
# F5b — a wedged member no longer holds the council
# ---------------------------------------------------------------------------


def test_sleeping_member_times_out_and_the_others_verdicts_are_used(monkeypatch):
    monkeypatch.setenv("WCB_JUDGE_MEMBER_DEADLINE_S", "0.4")

    def _fake_call(model, system, user, family=None, images=None):
        if family == "sonnet":
            time.sleep(5)
            return (_VERDICT, dict(grading._ZERO_USAGE))
        return (_VERDICT, dict(grading._ZERO_USAGE))

    monkeypatch.setattr(grading, "_call_one_judge", _fake_call)
    members = _members(2)
    t0 = time.monotonic()
    results = grading._run_council(members, "sys", "user", 1)
    elapsed = time.monotonic() - t0

    assert elapsed < 10, "the batch must not wait on the wedged member"
    # Submission order preserved.
    assert [r["family"] for r in results] == ["sonnet", "glm"]
    sonnet, glm = results
    assert sonnet["ok"] is False
    assert "deadline" in sonnet["error"]
    assert sonnet["usage"]["total_tokens"] == 0
    assert glm["ok"] is True
    assert len(glm["verdicts"]) == 1


def test_timed_out_member_is_not_a_survivor_but_grading_continues(monkeypatch):
    monkeypatch.setenv("WCB_JUDGE_MEMBER_DEADLINE_S", "0.4")

    def _fake_call(model, system, user, family=None, images=None):
        if family == "kimi":
            time.sleep(5)
        return (_VERDICT, dict(grading._ZERO_USAGE))

    monkeypatch.setattr(grading, "_call_one_judge", _fake_call)
    members = _members(3)
    out = grading._grade_council([{"criterion": "c", "weight": 1}],
                                 "sys", "user", members)
    assert out["criteria"][0]["votes"] == "Yes/Yes/Abstain"
    # Sonnet voted, so the criterion still resolves rather than abstaining.
    assert out["criteria"][0]["resolved_by"] == "sonnet"
    assert out["criteria"][0]["passed"] is True
    failed = {f["model"]: f["error"] for f in out["judge_council"]["failed"]}
    assert any("deadline" in e for e in failed.values())


def test_all_members_healthy_under_the_deadline(monkeypatch):
    monkeypatch.setenv("WCB_JUDGE_MEMBER_DEADLINE_S", "30")

    def _fake_call(model, system, user, family=None, images=None):
        return (_VERDICT, dict(grading._ZERO_USAGE))

    monkeypatch.setattr(grading, "_call_one_judge", _fake_call)
    members = _members(3)
    results = grading._run_council(members, "sys", "user", 1)
    assert [r["family"] for r in results] == ["sonnet", "glm", "kimi"]
    assert all(r["ok"] for r in results)
