"""Guards `_MEMBER_EVIDENCE_BUDGETS` against silent context-window overruns.

Worst-case dispatch must fit:

    budget_chars / EMPIRICAL_CHARS_PER_TOKEN_FLOOR
        + member_max_output_tokens + SAFETY_TOKEN_BUFFER
        <= ctx_window

The chars/token FLOOR (denser-than-typical evidence) is the regression-canary;
silent drifts in maxTokens or budget constants previously caused the
alden-croft run_2/run_3 council to collapse 0/3. This test makes the next
such drift fail loud at CI time instead of at first-judge time.

Context windows + max_output match AWS-published model cards (Sonnet 4.6,
Kimi K2.5, GLM 5). chars/token floors match the numbers stamped in
src/utils/grading.py:_MEMBER_EVIDENCE_BUDGETS comment block.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402


SAFETY_TOKEN_BUFFER = 3000
SCAFFOLD_CHARS = 5000


_MEMBER_LIMITS = {
    "urg0zifsjiga": {"ctx_window": 1_000_000, "chars_per_token_floor": 1.375},
    "q6g7fi6wumk3": {"ctx_window":   262_144, "chars_per_token_floor": 1.15},
    "u4czm4f2p3ws": {"ctx_window":   202_752, "chars_per_token_floor": 1.15},
}


@pytest.mark.parametrize("substring,budget,max_output", grading._MEMBER_EVIDENCE_BUDGETS)
def test_member_budget_fits_context_window(substring, budget, max_output):
    limits = _MEMBER_LIMITS.get(substring)
    assert limits is not None, (
        f"_MEMBER_EVIDENCE_BUDGETS has profile id substring {substring!r} but "
        "_MEMBER_LIMITS in this test does not — add the new member's ctx_window "
        "and chars_per_token_floor (run probe_judge_only.py to measure the floor)."
    )
    ctx = limits["ctx_window"]
    cpt_floor = limits["chars_per_token_floor"]
    dispatched_chars = budget + SCAFFOLD_CHARS
    worst_case_input_tokens = dispatched_chars / cpt_floor
    total_tokens = worst_case_input_tokens + max_output + SAFETY_TOKEN_BUFFER
    assert total_tokens <= ctx, (
        f"Budget overrun for {substring}: "
        f"({budget:,} budget + {SCAFFOLD_CHARS} scaffold) / {cpt_floor} cpt = "
        f"{worst_case_input_tokens:,.0f} input tokens + "
        f"{max_output} maxTokens + {SAFETY_TOKEN_BUFFER} safety = "
        f"{total_tokens:,.0f} total > {ctx:,} context window. "
        "Either lower the budget, lower this member's max_output, or raise the "
        "chars/token floor (only with new probe evidence)."
    )


def test_budget_table_covers_default_council():
    profile_substrings = {needle for needle, _budget, _max_out in grading._MEMBER_EVIDENCE_BUDGETS}
    for member in grading._DEFAULT_COUNCIL_MEMBERS:
        assert any(needle in member for needle in profile_substrings), (
            f"Default council member {member!r} has no budget entry in "
            "_MEMBER_EVIDENCE_BUDGETS. Add it before shipping or it will fall "
            "through to _DEFAULT_JUDGE_MAX_EVIDENCE (likely too tight or too loose)."
        )
