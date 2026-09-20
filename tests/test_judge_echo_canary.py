"""F1b — criterion-echo canary.

judge_system.md:19 makes every verdict block open with "a verbatim, exact-copy
repeat of the corresponding criterion in the list including its list number".
That echo is an independent witness of which criterion the judge believed it
was grading, so a verdict whose ordinal and echo disagree is a mis-binding that
the ordinal alone (F1) cannot detect.

The gate is deliberately LOW (`_ECHO_MATCH_THRESHOLD` 0.45 on a normalized
difflib ratio): rejecting only when the echo is CLEARLY a different criterion.
Formatting noise, the prompt's own "[points: ...]"/"[target: ...]" tags, and
light rewording must never cost a vote, and filename-heavy criteria — the
common case now that task prompts name deliverable files — must match.
"""
from __future__ import annotations

from src.utils import grading

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm"

_C0 = "The agent creates results/quarterly_revenue.xlsx with a Q3 total of 48200."
_C1 = "The final response explains why the shipment was delayed in Rotterdam."


def _members():
    return [
        grading.CouncilMember(family="sonnet", model=_SONNET),
        grading.CouncilMember(family="glm", model=_GLM),
    ]


def _ok(model: str, family: str, verdicts: list[dict]) -> dict:
    return {
        "model": model, "effective_model": model, "family": family,
        "ok": True, "verdicts": verdicts,
        "usage": dict(grading._ZERO_USAGE), "user_chars": 10,
    }


def _grade(monkeypatch, rubrics, results):
    monkeypatch.setattr(
        grading, "_run_council",
        lambda members, system, user, n, images=None: list(results),
    )
    return grading._grade_council(rubrics, "sys", "user", _members())


def _resp(*blocks: tuple[int, str, str]) -> str:
    return "\n".join(
        f"{n}. {echo} [[RATIONALE: r{n}]] [[SATISFIED: {sat}]] "
        f"[[TRUNCATION_AFFECTED: No]]"
        for n, echo, sat in blocks
    )


# ---------------------------------------------------------------------------
# _echo_matches_criterion — the comparison itself
# ---------------------------------------------------------------------------


def test_verbatim_echo_matches():
    ok, ratio = grading._echo_matches_criterion(_C0, _C0)
    assert ok and ratio == 1.0


def test_prompt_decorations_and_markdown_do_not_cost_a_vote():
    """The judge echoes the criterion LINE, tags and markdown emphasis included."""
    echoed = f"**{_C0}**  [points: 1.0]  [target: workspace_artifact]"
    ok, ratio = grading._echo_matches_criterion(echoed, _C0)
    assert ok and ratio == 1.0


def test_minor_rewording_passes():
    criterion = "The agent sends a confirmation email to the client after booking."
    echoed = "The agent sends a confirmation e-mail to the client once the booking is made."
    ok, ratio = grading._echo_matches_criterion(echoed, criterion)
    assert ok and ratio > grading._ECHO_MATCH_THRESHOLD


def test_filename_heavy_criterion_matches_and_filenames_survive_normalization():
    criterion = (
        "The agent writes results/q3_summary_report.md containing the "
        "2026-Q3 revenue table."
    )
    ok, ratio = grading._echo_matches_criterion(criterion, criterion)
    assert ok and ratio == 1.0
    # The normalizer must not eat the characters filenames are made of.
    norm = grading._normalize_echo(criterion)
    assert "results/q3_summary_report.md" in norm
    assert "2026-q3" in norm


def test_wholly_different_criterion_is_rejected():
    ok, ratio = grading._echo_matches_criterion(_C1, _C0)
    assert not ok
    assert ratio < grading._ECHO_MATCH_THRESHOLD


def test_short_or_absent_echo_is_never_punished():
    assert grading._echo_matches_criterion("", _C0)[0] is True
    assert grading._echo_matches_criterion("1.", _C0)[0] is True


# ---------------------------------------------------------------------------
# _grade_council — a rejected echo abstains exactly one criterion
# ---------------------------------------------------------------------------


def test_matching_echoes_bind_every_criterion(monkeypatch):
    rubrics = [{"criterion": _C0, "weight": 1}, {"criterion": _C1, "weight": 1}]
    good = grading._parse_verdict_text(_resp((1, _C0, "Yes"), (2, _C1, "Yes")), 2)
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", good),
        _ok(_GLM, "glm", list(good)),
    ])
    assert [c["votes"] for c in out["criteria"]] == ["Yes/Yes", "Yes/Yes"]
    assert out["abstention_flags"] == []


def test_echo_of_a_different_criterion_abstains_that_one_criterion(monkeypatch, caplog):
    """Ordinal 1 carries criterion 2's text — the ordinal alone looks fine."""
    rubrics = [{"criterion": _C0, "weight": 1}, {"criterion": _C1, "weight": 1}]
    forged = grading._parse_verdict_text(_resp((1, _C1, "Yes"), (2, _C1, "Yes")), 2)
    good = grading._parse_verdict_text(_resp((1, _C0, "Yes"), (2, _C1, "Yes")), 2)
    with caplog.at_level("WARNING"):
        out = _grade(monkeypatch, rubrics, [
            _ok(_SONNET, "sonnet", forged),
            _ok(_GLM, "glm", good),
        ])
    assert out["criteria"][0]["votes"] == "Abstain/Yes"
    assert out["criteria"][0]["resolved_by"] == "human_eval"
    assert out["abstention_flags"] == [0]
    # The second criterion is untouched — the canary is per-verdict.
    assert out["criteria"][1]["votes"] == "Yes/Yes"
    assert out["criteria"][1]["resolved_by"] == "unanimous"
    assert "echo mismatch" in caplog.text
    assert "ordinal=1" in caplog.text


def test_reworded_filename_echo_still_votes(monkeypatch):
    crit = "The agent writes results/q3_summary_report.md with the revenue table."
    echoed = f"{crit}  [points: 2.0]"
    rubrics = [{"criterion": crit, "weight": 2}]
    v = grading._parse_verdict_text(_resp((1, echoed, "Yes")), 1)
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", v),
        _ok(_GLM, "glm", list(v)),
    ])
    assert out["criteria"][0]["votes"] == "Yes/Yes"
    assert out["criteria"][0]["passed"] is True
    assert out["abstention_flags"] == []
