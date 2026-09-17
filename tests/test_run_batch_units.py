"""Unit tests for pure helpers in eval/run_batch.py.

Focus (per SCORING_AUDIT_REPORT.md task brief):
  * _augment_score_with_combined_rewards — the (test+rubric)/2 blend, including
    negative-passthrough, single-channel fallbacks, None-when-neither, and the
    math.isfinite / isinstance-bool guards.
  * _pass_summary_entry / _pass_summary_doc — per_run rollup math.
  * assorted small pure helpers (_finite_float, _mean_or_none, _model_type,
    _merge_usage_source, recompute_combined, _augment_task_with_mocks,
    _project_agent_usage_top_level, _project_artifact_record,
    _condense_transcript_for_judge, _normalize_display_model,
    _compute_testgen_cache_key).

All tests are OFFLINE and deterministic: no docker, no network, no boto3, no
sleeps. Temp files only under pytest tmp_path.

Import-bootstrap style matches tests/test_score_json_last_resort.py (sys.path
insert of repo root before `from eval...` / `from ...` imports).
"""
from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run_batch import (  # noqa: E402
    ALLOW_MISSING_REQUIRED_APIS_ENV,
    MissingRequiredApisError,
    _augment_score_with_combined_rewards,
    _augment_task_with_mocks,
    _compute_testgen_cache_key,
    _condense_transcript_for_judge,
    _finite_float,
    _mean_or_none,
    _merge_usage_source,
    _model_type,
    _normalize_display_model,
    _pass_summary_doc,
    _pass_summary_entry,
    _backfill_per_message_cost,
    _project_agent_usage_top_level,
    _project_artifact_record,
    _resolve_task_apis,
    _write_pass_summary,
    recompute_combined,
    save_usage,
)
from src.utils import skills_inference  # noqa: E402
from src.utils.oauth_pricing import reprice_oauth_sources  # noqa: E402


# ---------------------------------------------------------------------------
# _augment_score_with_combined_rewards — the (test_reward + rubric_reward) / 2
# blend. This is the core scoring surface flagged in the task brief.
#
# Signature: _augment_score_with_combined_rewards(scores: dict, result: dict)
#   test_reward  ← result["test_result"]["reward"], gated on tests_total truthy
#   rubric_reward ← scores["overall_score"]
#   writes scores["test_based_reward"], ["rubric_based_reward"], ["combined_reward"]
# ---------------------------------------------------------------------------


def _augment(overall_score, test_reward=None, tests_total=None):
    """Helper: build (scores, result) and run the augmenter, return scores."""
    scores: dict = {}
    if overall_score is not None:
        scores["overall_score"] = overall_score
    te: dict = {}
    if test_reward is not None:
        te["reward"] = test_reward
    if tests_total is not None:
        te["tests_total"] = tests_total
    result = {"test_result": te}
    _augment_score_with_combined_rewards(scores, result)
    return scores


class TestAugmentCombinedRewards:
    def test_both_channels_present_averages(self):
        # test=0.8, rubric=0.6 -> combined = 0.7
        s = _augment(overall_score=0.6, test_reward=0.8, tests_total=4)
        assert s["test_based_reward"] == 0.8
        assert s["rubric_based_reward"] == 0.6
        assert s["combined_reward"] == pytest.approx(0.7)

    def test_negative_test_reward_passthrough_into_blend(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # A negative test reward (guardrail-triggered) flows straight into the
        # blend un-clamped: (-7.0 + 0.2) / 2 = -3.4.
        s = _augment(overall_score=0.2, test_reward=-7.0, tests_total=3)
        assert s["test_based_reward"] == -7.0
        assert s["rubric_based_reward"] == 0.2
        assert s["combined_reward"] == pytest.approx(-3.4)

    def test_only_test_channel(self):
        s = _augment(overall_score=None, test_reward=0.5, tests_total=2)
        assert s["test_based_reward"] == 0.5
        assert s["rubric_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_only_rubric_channel(self):
        s = _augment(overall_score=0.9, test_reward=None, tests_total=None)
        assert s["test_based_reward"] is None
        assert s["rubric_based_reward"] == 0.9
        assert s["combined_reward"] == 0.9

    def test_neither_channel_yields_none_combined(self):
        s = _augment(overall_score=None, test_reward=None, tests_total=None)
        assert s["test_based_reward"] is None
        assert s["rubric_based_reward"] is None
        assert s["combined_reward"] is None

    def test_tests_total_zero_disables_test_channel(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # test reward is ONLY honored when tests_total is truthy; a reward with
        # tests_total=0 is ignored (falsy guard), so only rubric survives.
        s = _augment(overall_score=0.4, test_reward=0.99, tests_total=0)
        assert s["test_based_reward"] is None
        assert s["rubric_based_reward"] == 0.4
        assert s["combined_reward"] == 0.4

    def test_tests_total_missing_disables_test_channel(self):
        # tests_total absent entirely -> te.get returns None (falsy) -> ignored.
        s = _augment(overall_score=0.4, test_reward=0.99, tests_total=None)
        assert s["test_based_reward"] is None
        assert s["combined_reward"] == 0.4

    def test_nan_test_reward_rejected(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # math.isfinite guard rejects NaN in the test channel.
        s = _augment(overall_score=0.5, test_reward=float("nan"), tests_total=3)
        assert s["test_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_inf_test_reward_rejected(self):
        s = _augment(overall_score=0.5, test_reward=float("inf"), tests_total=3)
        assert s["test_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_nan_rubric_rejected(self):
        s = _augment(overall_score=float("nan"), test_reward=0.5, tests_total=3)
        assert s["rubric_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_inf_rubric_rejected(self):
        s = _augment(overall_score=float("-inf"), test_reward=0.5, tests_total=3)
        assert s["rubric_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_bool_test_reward_rejected(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # isinstance(x, bool) check: True is an int subclass but must NOT be
        # treated as a numeric reward.
        s = _augment(overall_score=0.5, test_reward=True, tests_total=3)
        assert s["test_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_bool_rubric_reward_rejected(self):
        s = _augment(overall_score=True, test_reward=0.5, tests_total=3)
        assert s["rubric_based_reward"] is None
        assert s["combined_reward"] == 0.5

    def test_int_rewards_are_accepted_as_float(self):
        # int rewards are valid numerics and get floated.
        s = _augment(overall_score=1, test_reward=0, tests_total=2)
        assert s["test_based_reward"] == 0.0
        assert isinstance(s["test_based_reward"], float)
        assert s["rubric_based_reward"] == 1.0
        assert s["combined_reward"] == pytest.approx(0.5)

    def test_non_dict_scores_is_noop(self):
        # Guard clause: non-dict scores returns immediately without raising.
        obj = ["not", "a", "dict"]
        _augment_score_with_combined_rewards(obj, {"test_result": {}})  # no raise
        assert obj == ["not", "a", "dict"]

    def test_none_result_treated_as_empty(self):
        # (result or {}) guard: None result must not raise.
        scores = {"overall_score": 0.3}
        _augment_score_with_combined_rewards(scores, None)
        assert scores["rubric_based_reward"] == 0.3
        assert scores["test_based_reward"] is None
        assert scores["combined_reward"] == 0.3

    def test_test_result_not_a_dict_is_ignored(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # te is coerced to {} when result["test_result"] is falsy, and when it
        # is a non-dict truthy value the isinstance(te, dict) guard skips it.
        scores = {"overall_score": 0.3}
        _augment_score_with_combined_rewards(scores, {"test_result": ["x"]})
        assert scores["test_based_reward"] is None
        assert scores["combined_reward"] == 0.3

    def test_negative_both_channels_average(self):
        # Two negatives average to a negative (fully un-clamped).
        s = _augment(overall_score=-0.4, test_reward=-0.6, tests_total=2)
        assert s["combined_reward"] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# _finite_float
# ---------------------------------------------------------------------------


class TestFiniteFloat:
    def test_accepts_int(self):
        assert _finite_float(3) == 3.0

    def test_accepts_float(self):
        assert _finite_float(2.5) == 2.5

    def test_accepts_negative(self):
        assert _finite_float(-1.25) == -1.25

    def test_rejects_bool_true(self):
        assert _finite_float(True) is None

    def test_rejects_bool_false(self):
        assert _finite_float(False) is None

    def test_rejects_nan(self):
        assert _finite_float(float("nan")) is None

    def test_rejects_inf(self):
        assert _finite_float(float("inf")) is None

    def test_rejects_string(self):
        assert _finite_float("1.0") is None

    def test_rejects_none(self):
        assert _finite_float(None) is None


# ---------------------------------------------------------------------------
# _mean_or_none
# ---------------------------------------------------------------------------


class TestMeanOrNone:
    def test_simple_mean(self):
        assert _mean_or_none([1.0, 2.0, 3.0]) == 2.0

    def test_drops_none(self):
        assert _mean_or_none([2.0, None, 4.0]) == 3.0

    def test_all_none_returns_none(self):
        assert _mean_or_none([None, None]) is None

    def test_empty_returns_none(self):
        assert _mean_or_none([]) is None

    def test_single_value(self):
        assert _mean_or_none([0.7]) == 0.7

    def test_negatives_included(self):
        assert _mean_or_none([-1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# _model_type — model id -> kensei pod folder name
# ---------------------------------------------------------------------------


class TestModelType:
    def test_claude_family(self):
        assert _model_type("anthropic/claude-opus-4.7") == "claude"

    def test_claude_bare(self):
        assert _model_type("claude-sonnet-4.6") == "claude"

    def test_gpt_family(self):
        assert _model_type("openai/gpt-5.5") == "gpt"

    def test_o1_family(self):
        assert _model_type("o1-preview") == "gpt"

    def test_o3_family(self):
        assert _model_type("o3") == "gpt"

    def test_o4_family(self):
        assert _model_type("o4-mini") == "gpt"

    def test_other_model_sanitized(self):
        # Non-claude/gpt models get lowercased + non-[a-z0-9.\-_] replaced by _.
        assert _model_type("Kimi/K2 Thinking!") == "k2_thinking_"

    def test_sanitize_preserves_allowed_chars(self):
        assert _model_type("some/glm-4.6_v2") == "glm-4.6_v2"

    def test_uppercase_gpt_normalized(self):
        assert _model_type("OpenAI/GPT-4o") == "gpt"


# ---------------------------------------------------------------------------
# _pass_summary_entry — per_run record with BOTH scoring channels
# ---------------------------------------------------------------------------


class TestPassSummaryEntry:
    def test_rubric_only_run(self):
        scores = {
            "overall_score": 0.4,
            "criteria_total": 5,
            "criteria_passed": 2,
            "criteria_failed": 3,
        }
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=None)
        assert entry["run_index"] == 0
        assert entry["criteria_total"] == 5
        assert entry["criteria_passed"] == 2
        assert entry["criteria_failed"] == 3
        assert entry["rubric_reward"] == 0.4
        # rubric_pct derived from reward * 100 when absent
        assert entry["rubric_weights_percentage"] == 40.0
        # no tests -> combined falls back to rubric; reward = combined
        assert entry["tests_total"] == 0
        assert entry["test_reward"] is None
        assert entry["combined_reward"] == 0.4
        assert entry["reward"] == 0.4
        assert "__last_resort_stub__" not in entry

    def test_legacy_tests_keys_fall_back_for_criteria(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # criteria_* falls back to legacy tests_* keys inside `scores`.
        scores = {"overall_score": 0.5, "tests_total": 7, "tests_passed": 4, "tests_failed": 3}
        entry = _pass_summary_entry(run_index=1, scores=scores, test_result=None)
        assert entry["criteria_total"] == 7
        assert entry["criteria_passed"] == 4
        assert entry["criteria_failed"] == 3

    def test_both_channels_combined_averaged(self):
        scores = {
            "overall_score": 0.6,
            "criteria_total": 3,
            "test_based_reward": 0.8,
            "rubric_based_reward": 0.6,
        }
        test_result = {"tests_total": 4, "tests_passed": 3, "tests_failed": 1, "reward": 0.8}
        entry = _pass_summary_entry(run_index=2, scores=scores, test_result=test_result)
        assert entry["tests_total"] == 4
        assert entry["tests_passed"] == 3
        assert entry["tests_failed"] == 1
        assert entry["test_reward"] == 0.8
        assert entry["rubric_reward"] == 0.6
        # combined_reward absent from scores -> recomputed here as (0.8+0.6)/2
        assert entry["combined_reward"] == pytest.approx(0.7)
        assert entry["reward"] == pytest.approx(0.7)

    def test_uses_precomputed_combined_when_present(self):
        scores = {
            "overall_score": 0.6,
            "test_based_reward": 0.8,
            "rubric_based_reward": 0.6,
            "combined_reward": 0.123,  # deliberately inconsistent to prove passthrough
        }
        test_result = {"tests_total": 2, "reward": 0.8}
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=test_result)
        assert entry["combined_reward"] == 0.123
        assert entry["reward"] == 0.123

    def test_test_reward_from_ctrf_when_scores_lack_it(self):
        # test_based_reward absent from scores but tests ran -> pulled from ctrf.
        scores = {"overall_score": 0.2}
        test_result = {"tests_total": 3, "reward": 0.9}
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=test_result)
        assert entry["test_reward"] == 0.9
        assert entry["combined_reward"] == pytest.approx((0.9 + 0.2) / 2)

    def test_no_scores_no_tests_zero_reward(self):
        entry = _pass_summary_entry(run_index=0, scores=None, test_result=None)
        assert entry["criteria_total"] == 0
        assert entry["rubric_reward"] is None
        assert entry["combined_reward"] is None
        # authoritative reward: combined None, rubric None -> `rubric_reward or 0.0`
        assert entry["reward"] == 0.0

    def test_last_resort_stub_marker_propagates(self):
        scores = {"overall_score": None, "__last_resort_stub__": True}
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=None)
        assert entry["__last_resort_stub__"] is True

    def test_explicit_rubric_pct_preferred_over_derived(self):
        scores = {"overall_score": 0.5, "rubric_weights_percentage": 55.0}
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=None)
        assert entry["rubric_weights_percentage"] == 55.0

    def test_tests_errored_and_skipped_carried(self):
        scores = {"overall_score": 0.1}
        test_result = {
            "tests_total": 5, "tests_passed": 2, "tests_failed": 1,
            "tests_errored": 1, "tests_skipped": 1, "reward": 0.4,
        }
        entry = _pass_summary_entry(run_index=0, scores=scores, test_result=test_result)
        assert entry["tests_errored"] == 1
        assert entry["tests_skipped"] == 1


# ---------------------------------------------------------------------------
# _pass_summary_doc — cross-run rollup
# ---------------------------------------------------------------------------


class TestPassSummaryDoc:
    def _entry(self, idx, reward, combined, rubric, test, pct):
        return {
            "run_index": idx,
            "reward": reward,
            "combined_reward": combined,
            "rubric_reward": rubric,
            "test_reward": test,
            "rubric_weights_percentage": pct,
        }

    def test_averages_and_sorts(self):
        per_run = [
            self._entry(1, 0.6, 0.6, 0.6, None, 60.0),
            self._entry(0, 0.4, 0.4, 0.4, None, 40.0),
        ]
        doc = _pass_summary_doc("claude", per_run)
        assert doc["model"] == "claude"
        assert doc["runs"] == 2
        assert doc["average_reward"] == pytest.approx(0.5)
        assert doc["average_rubric_reward"] == pytest.approx(0.5)
        assert doc["average_rubric_weights_percentage"] == 50.0
        # sorted ascending by run_index
        assert [r["run_index"] for r in doc["per_run"]] == [0, 1]

    def test_none_test_rewards_excluded_from_test_mean(self):
        per_run = [
            self._entry(0, 0.4, 0.4, 0.4, None, 40.0),
            self._entry(1, 0.8, 0.8, 0.8, 0.8, 80.0),
        ]
        doc = _pass_summary_doc("gpt", per_run)
        # only run 1 has a test_reward -> mean over the single non-None value
        assert doc["average_test_reward"] == 0.8

    def test_empty_per_run_zeroes(self):
        doc = _pass_summary_doc("claude", [])
        assert doc["runs"] == 0
        assert doc["average_reward"] == 0.0
        assert doc["average_combined_reward"] is None
        assert doc["average_rubric_weights_percentage"] is None


# ---------------------------------------------------------------------------
# _merge_usage_source / recompute_combined
# ---------------------------------------------------------------------------


class TestUsageMerge:
    def test_merge_adds_numeric_keys(self):
        dst: dict = {}
        _merge_usage_source(dst, {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01})
        assert dst["input_tokens"] == 10
        assert dst["output_tokens"] == 5
        assert dst["cost_usd"] == 0.01

    def test_merge_accumulates(self):
        dst = {"input_tokens": 3, "cost_usd": 0.5}
        _merge_usage_source(dst, {"input_tokens": 7, "cost_usd": 0.25})
        assert dst["input_tokens"] == 10
        assert dst["cost_usd"] == 0.75

    def test_merge_empty_src_noop(self):
        dst = {"input_tokens": 4}
        _merge_usage_source(dst, {})
        assert dst == {"input_tokens": 4}

    def test_merge_none_values_treated_as_zero(self):
        dst: dict = {}
        _merge_usage_source(dst, {"input_tokens": None, "output_tokens": 2})
        assert dst["input_tokens"] == 0
        assert dst["output_tokens"] == 2

    def test_recompute_combined_enforces_total_invariant(self):
        # total_tokens is overwritten to input+output+cache_read+cache_write,
        # even if a source lied about it.
        sources = {
            "agent": {
                "input_tokens": 100, "output_tokens": 50,
                "cache_read_tokens": 10, "cache_write_tokens": 5,
                "total_tokens": 999999,  # bogus
                "request_count": 3, "cost_usd": 0.02,
            }
        }
        combined = recompute_combined(sources, task_id="t")
        assert combined["total_tokens"] == 100 + 50 + 10 + 5
        assert combined["input_tokens"] == 100
        assert combined["request_count"] == 3
        assert combined["cost_usd"] == pytest.approx(0.02)

    def test_recompute_combined_sums_multiple_sources(self):
        sources = {
            "agent": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
                      "cache_read_tokens": 0, "cache_write_tokens": 0,
                      "request_count": 1, "cost_usd": 0.01},
            "judge": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26,
                      "cache_read_tokens": 0, "cache_write_tokens": 0,
                      "request_count": 2, "cost_usd": 0.03},
        }
        combined = recompute_combined(sources, task_id="t")
        assert combined["input_tokens"] == 30
        assert combined["output_tokens"] == 10
        assert combined["request_count"] == 3
        assert combined["total_tokens"] == 40
        assert combined["cost_usd"] == pytest.approx(0.04)

    def test_recompute_combined_empty_sources(self):
        combined = recompute_combined({}, task_id="t")
        assert combined["total_tokens"] == 0
        assert combined["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# _project_agent_usage_top_level
# ---------------------------------------------------------------------------


class TestProjectAgentUsage:
    def test_none_usage_returns_zeroed_shape(self):
        out = _project_agent_usage_top_level(None)
        assert out == {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
        }

    def test_empty_dict_returns_zeroed_shape(self):
        out = _project_agent_usage_top_level({})
        assert out["input_tokens"] == 0
        assert out["cost_usd"] == 0.0

    def test_maps_cache_read_to_cached_input(self):
        # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
        # cached_input_tokens is aliased from cache_read_tokens.
        out = _project_agent_usage_top_level(
            {"input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 9,
             "cache_write_tokens": 1, "cost_usd": 0.1234567}
        )
        assert out["input_tokens"] == 5
        assert out["output_tokens"] == 2
        assert out["cached_input_tokens"] == 9
        assert out["cache_read_tokens"] == 9
        assert out["cache_write_tokens"] == 1
        # cost rounded to 6 places
        assert out["cost_usd"] == 0.123457

    def test_non_numeric_fields_coerced_to_zero(self):
        out = _project_agent_usage_top_level(
            {"input_tokens": "oops", "cost_usd": "nan-ish"}
        )
        assert out["input_tokens"] == 0
        assert out["cost_usd"] == 0.0

    def test_none_field_values_coerced_to_zero(self):
        out = _project_agent_usage_top_level({"input_tokens": None, "cost_usd": None})
        assert out["input_tokens"] == 0
        assert out["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# _project_artifact_record
# ---------------------------------------------------------------------------


class TestProjectArtifactRecord:
    def test_relativizes_absolute_path_under_run_dir(self, tmp_path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        rich = {
            "container_path": str(run_dir / "task_output" / "out.txt"),
            "filename": "out.txt", "mime_type": "text/plain", "size_bytes": 12,
        }
        rec = _project_artifact_record(rich, ref_id="artifact_0", run_dir=run_dir)
        assert rec["ref_id"] == "artifact_0"
        assert rec["path"] == "task_output/out.txt"
        assert rec["filename"] == "out.txt"
        assert rec["mime_type"] == "text/plain"
        assert rec["size_bytes"] == 12
        assert rec["source"] == "agent_workspace"

    def test_absolute_path_outside_run_dir_kept_verbatim(self, tmp_path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        rich = {"container_path": "/root/workspace/thing.bin", "filename": "thing.bin"}
        rec = _project_artifact_record(rich, ref_id="artifact_3", run_dir=run_dir)
        # not under run_dir -> ValueError on relative_to -> path kept as-is
        assert rec["path"] == "/root/workspace/thing.bin"

    def test_bad_size_coerced_to_zero(self, tmp_path):
        rec = _project_artifact_record(
            {"container_path": "", "size_bytes": "big"},
            ref_id="a", run_dir=tmp_path,
        )
        assert rec["size_bytes"] == 0

    def test_missing_fields_default_empty(self, tmp_path):
        rec = _project_artifact_record({}, ref_id="a", run_dir=tmp_path)
        assert rec["path"] == ""
        assert rec["filename"] == ""
        assert rec["mime_type"] == ""
        assert rec["size_bytes"] == 0


# ---------------------------------------------------------------------------
# _condense_transcript_for_judge
# ---------------------------------------------------------------------------


class TestCondenseTranscript:
    def test_empty_trajectory(self):
        assert _condense_transcript_for_judge({}) == ""
        assert _condense_transcript_for_judge({"messages": []}) == ""

    def test_plain_string_content(self):
        traj = {"messages": [{"message": {"role": "user", "content": "hello"}}]}
        assert _condense_transcript_for_judge(traj) == \
            "[FINAL ASSISTANT MESSAGE] [user turn 1] hello"

    def test_text_block_content(self):
        traj = {
            "messages": [
                {"message": {"role": "assistant",
                             "content": [{"type": "text", "text": "answer"}]}}
            ]
        }
        assert _condense_transcript_for_judge(traj) == "[FINAL ASSISTANT MESSAGE] [assistant] answer"

    def test_tool_call_block(self):
        traj = {
            "messages": [
                {"message": {"role": "assistant",
                             "content": [{"type": "toolCall", "name": "ls",
                                          "arguments": {"path": "/"}}]}}
            ]
        }
        out = _condense_transcript_for_judge(traj)
        assert out == '[FINAL ASSISTANT MESSAGE] [assistant:tool] ls {"path": "/"}'

    def test_tool_result_block(self):
        traj = {
            "messages": [
                {"message": {"role": "user",
                             "content": [{"type": "toolResult", "text": "file listing"}]}}
            ]
        }
        assert _condense_transcript_for_judge(traj) == "[SUBMIT TOOL OUTPUT] [toolResult] file listing"

    def test_whitespace_only_string_skipped(self):
        traj = {"messages": [{"message": {"role": "user", "content": "   "}}]}
        assert _condense_transcript_for_judge(traj) == ""

    def test_limit_kwarg_ignored(self):
        # By policy the limit is never applied; full text is always emitted.
        long = "x" * 5000
        traj = {"messages": [{"message": {"role": "user", "content": long}}]}
        out = _condense_transcript_for_judge(traj, limit=10)
        assert out == f"[FINAL ASSISTANT MESSAGE] [user turn 1] {long}"

    def test_message_without_wrapper(self):
        # entries where role/content live at top level (no `message` wrapper).
        traj = {"messages": [{"role": "user", "content": "top-level"}]}
        assert _condense_transcript_for_judge(traj) == \
            "[FINAL ASSISTANT MESSAGE] [user turn 1] top-level"

    def test_landmark_only_on_last_entry(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": "first"}},
            {"message": {"role": "assistant", "content": "final"}},
        ]}
        out = _condense_transcript_for_judge(traj)
        assert out == "[user turn 1] first\n[FINAL ASSISTANT MESSAGE] [assistant] final"

    def test_submit_tool_landmark_when_ending_on_toolresult(self):
        traj = {"messages": [
            {"message": {"role": "assistant", "content": "doing"}},
            {"message": {"role": "user", "content": [{"type": "toolResult", "text": "done"}]}},
        ]}
        out = _condense_transcript_for_judge(traj)
        assert out.endswith("[SUBMIT TOOL OUTPUT] [toolResult] done")


class TestCondenseUserTurnNumbering:
    """Duplicate-resend fix: the judge must read turn ordinals off explicit
    labels instead of counting '[user]' lines, and a harness re-send of a
    stalled turn must not consume a second ordinal."""

    def _lines(self, traj, **kw):
        return _condense_transcript_for_judge(traj, **kw).splitlines()

    def test_user_turns_numbered_in_order(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": "t1"}},
            {"message": {"role": "assistant", "content": "a1"}},
            {"message": {"role": "user", "content": "t2"}},
            {"message": {"role": "assistant", "content": "a2"}},
        ]}
        lines = self._lines(traj)
        assert lines[0] == "[user turn 1] t1"
        assert lines[2] == "[user turn 2] t2"

    def test_identical_consecutive_user_rows_collapse(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": "t1"}},
            {"message": {"role": "assistant", "content": "a1"}},
            {"message": {"role": "user", "content": "same turn"}},
            {"message": {"role": "user", "content": "same turn"}},
            {"message": {"role": "assistant", "content": "a2"}},
        ]}
        lines = self._lines(traj)
        assert lines == [
            "[user turn 1] t1",
            "[assistant] a1",
            "[user turn 2 — resent by harness after a stall; duplicate collapsed] same turn",
            "[FINAL ASSISTANT MESSAGE] [assistant] a2",
        ]

    def test_collapse_survives_differing_timestamp_prefixes(self):
        # The agent stamps each delivery with its own wall clock, and a stall
        # retry lands >=600s later, so the two copies never match verbatim.
        traj = {"messages": [
            {"message": {"role": "user", "content": "[Mon 2026-06-15 14:50 UTC] do it"}},
            {"message": {"role": "user", "content": "[Mon 2026-06-15 15:05 UTC] do it"}},
        ]}
        assert self._lines(traj) == [
            "[FINAL ASSISTANT MESSAGE] [user turn 1 — resent by harness after "
            "a stall; duplicate collapsed] do it",
        ]

    def test_distinct_consecutive_user_rows_kept(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": "first ask"}},
            {"message": {"role": "user", "content": "second ask"}},
        ]}
        assert self._lines(traj) == [
            "[user turn 1] first ask",
            "[FINAL ASSISTANT MESSAGE] [user turn 2] second ask",
        ]

    def test_repeat_after_agent_output_kept_without_hint(self):
        # Content alone must not collapse a genuine repeat: the user really can
        # ask the same thing twice after the agent replied.
        traj = {"messages": [
            {"message": {"role": "user", "content": "status?"}},
            {"message": {"role": "assistant", "content": "working"}},
            {"message": {"role": "user", "content": "status?"}},
        ]}
        assert self._lines(traj) == [
            "[user turn 1] status?",
            "[assistant] working",
            "[FINAL ASSISTANT MESSAGE] [user turn 2] status?",
        ]

    def test_turns_duplicated_hint_collapses_across_aborted_output(self):
        # The stalled attempt emitted partial output before it wedged, so the
        # re-send is not adjacent; the runner's marker (0-based turn 0) says a
        # retry fired there and the text confirms it.
        traj = {"messages": [
            {"message": {"role": "user", "content": "status?"}},
            {"message": {"role": "assistant", "content": "working"}},
            {"message": {"role": "user", "content": "status?"}},
        ]}
        assert self._lines(traj, turns_duplicated=[0]) == [
            "[user turn 1 — resent by harness after a stall; duplicate collapsed] status?",
            "[FINAL ASSISTANT MESSAGE] [assistant] working",
        ]

    def test_hint_for_other_turn_does_not_collapse(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": "status?"}},
            {"message": {"role": "assistant", "content": "working"}},
            {"message": {"role": "user", "content": "status?"}},
        ]}
        assert self._lines(traj, turns_duplicated=[5])[-1] == \
            "[FINAL ASSISTANT MESSAGE] [user turn 2] status?"

    def test_tool_result_rows_do_not_consume_turn_numbers(self):
        # OpenClaw records tool results as role='user' entries.
        traj = {"messages": [
            {"message": {"role": "user", "content": "t1"}},
            {"message": {"role": "user", "content": [{"type": "toolResult", "text": "ls out"}]}},
            {"message": {"role": "user", "content": "t2"}},
        ]}
        lines = self._lines(traj)
        assert lines[0] == "[user turn 1] t1"
        assert lines[1] == "[toolResult] ls out"
        assert lines[2] == "[FINAL ASSISTANT MESSAGE] [user turn 2] t2"

    def test_text_blocks_are_numbered_too(self):
        traj = {"messages": [
            {"message": {"role": "user", "content": [{"type": "text", "text": "block ask"}]}},
        ]}
        assert self._lines(traj) == ["[FINAL ASSISTANT MESSAGE] [user turn 1] block ask"]

    def test_garbage_hint_values_ignored(self):
        traj = {"messages": [{"message": {"role": "user", "content": "x"}}]}
        assert self._lines(traj, turns_duplicated=["a", None, True]) == [
            "[FINAL ASSISTANT MESSAGE] [user turn 1] x",
        ]


# ---------------------------------------------------------------------------
# _normalize_display_model — recursive model-id relabel
# ---------------------------------------------------------------------------


class TestNormalizeDisplayModel:
    def test_rewrites_dash_opus_id_in_dict(self):
        obj = {"model": "claude-opus-4-6"}
        _normalize_display_model(obj)
        assert obj["model"] == "claude-opus-4.7"

    def test_rewrites_provider_qualified_id(self):
        obj = {"model": "anthropic/claude-opus-4-6"}
        _normalize_display_model(obj)
        assert obj["model"] == "anthropic/claude-opus-4.7"

    def test_leaves_unknown_model_untouched(self):
        obj = {"model": "gpt-5.5"}
        _normalize_display_model(obj)
        assert obj["model"] == "gpt-5.5"

    def test_recurses_into_nested_structures(self):
        obj = {"messages": [{"message": {"model": "claude-opus-4-6"}}]}
        _normalize_display_model(obj)
        assert obj["messages"][0]["message"]["model"] == "claude-opus-4.7"

    def test_non_model_string_keys_left_alone(self):
        obj = {"name": "claude-opus-4-6"}
        _normalize_display_model(obj)
        assert obj["name"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# _compute_testgen_cache_key — content hash over rubric/prompt/config/mock_data
# ---------------------------------------------------------------------------


class TestComputeTestgenCacheKey:
    def test_no_task_dir_returns_empty(self):
        assert _compute_testgen_cache_key({}) == ""

    def test_missing_dir_returns_empty(self, tmp_path):
        assert _compute_testgen_cache_key({"task_dir": str(tmp_path / "nope")}) == ""

    def test_stable_for_same_content(self, tmp_path):
        d = tmp_path / "task"
        d.mkdir()
        (d / "rubric.json").write_text('{"a": 1}')
        (d / "prompt.txt").write_text("do the thing")
        k1 = _compute_testgen_cache_key({"task_dir": str(d)})
        k2 = _compute_testgen_cache_key({"task_dir": str(d)})
        assert k1 == k2
        assert len(k1) == 32

    def test_changes_when_prompt_changes(self, tmp_path):
        d = tmp_path / "task"
        d.mkdir()
        (d / "rubric.json").write_text('{"a": 1}')
        (d / "prompt.txt").write_text("v1")
        k1 = _compute_testgen_cache_key({"task_dir": str(d)})
        (d / "prompt.txt").write_text("v2")
        k2 = _compute_testgen_cache_key({"task_dir": str(d)})
        assert k1 != k2

    def test_changes_when_mock_data_content_changes(self, tmp_path):
        d = tmp_path / "task"
        (d / "mock_data" / "figma-api").mkdir(parents=True)
        (d / "rubric.json").write_text("{}")
        fixture = d / "mock_data" / "figma-api" / "data.json"
        fixture.write_text('{"x": 1}')
        k1 = _compute_testgen_cache_key({"task_dir": str(d)})
        # same byte length, different content -> must change the key
        fixture.write_text('{"x": 2}')
        k2 = _compute_testgen_cache_key({"task_dir": str(d)})
        assert k1 != k2


# ---------------------------------------------------------------------------
# _augment_task_with_mocks — task-dict population (no docker)
#
# Delegates required/distractor resolution to _resolve_task_apis; here we drive
# it with a task that has no task_dir / no declared APIs so inference is a no-op,
# using a minimal fake config to avoid touching the real environment catalog.
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, tmp_path):
        self.environment_dir = tmp_path / "environment"  # nonexistent -> empty catalog
        self.work_dir = tmp_path / "work"
        self.wildclaw_skills_dir = tmp_path / "skills"
        self.default_skills = []


def _fake_catalog(tmp_path, names):
    """Materialize a fake service catalog and return a config pointed at it.

    `catalog_apis` identifies a service as any `service.toml`-bearing dir, and
    lru_caches the scan per env-dir string — hence the cache_clear, since one
    tmp_path may be populated after an earlier empty-catalog read.
    """
    cfg = _FakeConfig(tmp_path)
    cfg.environment_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        d = cfg.environment_dir / name
        d.mkdir(exist_ok=True)
        (d / "service.toml").write_text('[service]\nname = "%s"\nport = 8000\n' % name)
    skills_inference._disk_services.cache_clear()
    skills_inference._build_catalog.cache_clear()
    return cfg


@pytest.fixture(autouse=True)
def _clear_skills_inference_caches():
    skills_inference._disk_services.cache_clear()
    skills_inference._build_catalog.cache_clear()
    yield
    skills_inference._disk_services.cache_clear()
    skills_inference._build_catalog.cache_clear()


class TestAugmentTaskWithMocks:
    def test_populates_core_fields(self, tmp_path):
        cfg = _FakeConfig(tmp_path)
        task = {"task_id": "t1", "prompt": "hi", "distractor_apis_declared": "__ABSENT__"}
        _augment_task_with_mocks(task, cfg, mock_env_dict={"FIGMA_API_URL": "http://x"})
        assert task["required_apis"] == []
        assert task["distractor_apis"] == []
        assert task["mock_overlays"] == {}
        assert task["env_dict"] == {"FIGMA_API_URL": "http://x"}
        assert task["env_dir"] == str(cfg.environment_dir)
        assert task["skills_path"] == str(cfg.wildclaw_skills_dir)

    def test_env_dict_defaults_empty_when_no_mock_env(self, tmp_path):
        cfg = _FakeConfig(tmp_path)
        task = {"task_id": "t2", "prompt": "hi", "distractor_apis_declared": "__ABSENT__"}
        _augment_task_with_mocks(task, cfg, mock_env_dict=None)
        assert task["env_dict"] == {}

    def test_default_skills_merged_and_deduped(self, tmp_path):
        cfg = _FakeConfig(tmp_path)
        cfg.default_skills = ["pdf-extract", "video-frames"]
        task = {
            "task_id": "t3", "prompt": "hi",
            "distractor_apis_declared": "__ABSENT__",
            "skills": "pdf-extract\ncustom-skill",
        }
        _augment_task_with_mocks(task, cfg, mock_env_dict=None)
        # existing first, new appended, dupes removed, order preserved
        assert task["skills"].splitlines() == ["pdf-extract", "custom-skill", "video-frames"]

    def test_explicit_required_apis_declared(self, tmp_path):
        cfg = _fake_catalog(tmp_path, ["etsy-api", "linear-api"])
        task = {
            "task_id": "t4", "prompt": "hi",
            "required_apis_declared": ["etsy-api"],
            "distractor_apis_declared": "__ABSENT__",
        }
        _augment_task_with_mocks(task, cfg, mock_env_dict=None)
        assert task["required_apis"] == ["etsy-api"]

    def test_declared_api_absent_from_catalog_is_fatal(self, tmp_path):
        # Was: dropped with a logger.warning, leaving required_apis == [] and the
        # agent scored as a model failure for work it had no services to do.
        cfg = _fake_catalog(tmp_path, ["etsy-api"])
        task = {
            "task_id": "t5", "prompt": "hi",
            "required_apis_declared": ["totally-made-up-api"],
            "distractor_apis_declared": "__ABSENT__",
        }
        with pytest.raises(MissingRequiredApisError):
            _augment_task_with_mocks(task, cfg, mock_env_dict=None)

    def test_env_dict_filtered_to_the_full_fleet(self, tmp_path):
        cfg = _fake_catalog(tmp_path, ["etsy-api", "linear-api"])
        task = {
            "task_id": "t6", "prompt": "hi",
            "required_apis_declared": ["etsy-api"],
        }
        _augment_task_with_mocks(task, cfg, mock_env_dict={
            "ETSY_API_URL": "http://etsy",
            "LINEAR_API_URL": "http://linear",
            "PRUNED_API_URL": "http://gone",
            "MOCK_ADMIN_TOKEN": "keep-me",
        })
        # linear-api is a distractor, so its URL is exposed; a service outside
        # the catalog is not, and non-URL vars pass through untouched.
        assert task["env_dict"] == {
            "ETSY_API_URL": "http://etsy",
            "LINEAR_API_URL": "http://linear",
            "MOCK_ADMIN_TOKEN": "keep-me",
        }


# ---------------------------------------------------------------------------
# _resolve_task_apis — mock_data overlays + distractor policy
# ---------------------------------------------------------------------------


_FLEET = ["etsy-api", "linear-api", "notion-api", "stripe-api"]


class TestResolveTaskApisDeclaredFirst:
    def test_declared_list_is_the_required_set_verbatim(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "d1", "prompt": "hi",
                "required_apis_declared": ["etsy-api", "notion-api"]}
        required, _, _ = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api", "notion-api"}

    def test_declared_bare_names_are_normalized_to_env_dir_names(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "d2", "prompt": "hi",
                "required_apis_declared": ["etsy", "linear-api", "  ", "notion"]}
        required, _, _ = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api", "linear-api", "notion-api"}

    def test_declared_wins_over_mock_data_directory_scan(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task_dir = tmp_path / "task"
        api_dir = task_dir / "mock_data" / "linear-api"
        api_dir.mkdir(parents=True)
        (api_dir / "issues.json").write_text("{}")
        task = {
            "task_id": "d3", "prompt": "hi", "task_dir": str(task_dir),
            "required_apis_declared": ["etsy-api"],
        }
        required, distractor, overlays = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api"}
        # Seeded-but-undeclared lands in overlays + distractors, never required:
        # the service still serves its data without joining the graded contract.
        assert "linear-api" in overlays
        assert "linear-api" in distractor

    def test_declared_wins_over_keyword_inference(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {
            "task_id": "d4",
            "prompt": "reconcile the stripe payouts and file them in notion",
            "required_apis_declared": ["etsy-api"],
        }
        required, _, _ = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api"}

    def test_inference_fires_only_without_a_declaration(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "d5",
                "prompt": "reconcile the stripe payouts and file them in notion"}
        required, _, _ = _resolve_task_apis(task, cfg)
        assert required == {"notion-api", "stripe-api"}

    def test_mock_data_unions_into_required_when_undeclared(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task_dir = tmp_path / "task"
        api_dir = task_dir / "mock_data" / "etsy-api"
        api_dir.mkdir(parents=True)
        (api_dir / "listings.json").write_text('{"x": 1}')
        task = {"task_id": "d6", "prompt": "hi", "task_dir": str(task_dir)}
        required, _, overlays = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api"}
        assert overlays["etsy-api"]["listings.json"] == str((api_dir / "listings.json").resolve())

    def test_inferred_service_absent_from_disk_is_filtered_not_fatal(self, tmp_path):
        # 'quickbooks' is a curated keyword whose service is off disk here.
        # Asymmetry under test: inference is a guess, so a miss is filtered
        # silently; a declaration is a contract, so a miss is fatal.
        cfg = _fake_catalog(tmp_path, ["notion-api"])
        task = {"task_id": "d7",
                "prompt": "log this invoice in quickbooks and update notion"}
        required, distractor, _ = _resolve_task_apis(task, cfg)
        assert required == {"notion-api"}
        assert distractor == []


class TestResolveTaskApisFullFleetDistractors:
    def test_distractor_is_exactly_catalog_minus_required(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "f1", "prompt": "hi",
                "required_apis_declared": ["etsy-api"]}
        required, distractor, _ = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api"}
        assert distractor == ["linear-api", "notion-api", "stripe-api"]
        assert set(required) | set(distractor) == set(_FLEET)

    def test_every_task_mounts_the_whole_fleet(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        for declared in (["etsy-api"], ["notion-api", "stripe-api"], _FLEET):
            required, distractor, _ = _resolve_task_apis(
                {"task_id": "f2", "prompt": "hi", "required_apis_declared": declared}, cfg)
            assert set(required) | set(distractor) == set(_FLEET)
            assert not set(required) & set(distractor)

    def test_declared_distractor_list_no_longer_narrows_the_fleet(self, tmp_path):
        # `distractor_apis:` is inert under a standardized fleet — a guardrail
        # probe must be reachable on every service, not on an authored subset.
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "f3", "prompt": "hi",
                "required_apis_declared": ["etsy-api"],
                "distractor_apis_declared": ["linear-api"]}
        _, distractor, _ = _resolve_task_apis(task, cfg)
        assert distractor == ["linear-api", "notion-api", "stripe-api"]

    def test_absent_distractor_key_no_longer_means_no_distractors(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "f4", "prompt": "hi",
                "required_apis_declared": ["etsy-api"]}
        _, distractor, _ = _resolve_task_apis(task, cfg)
        assert distractor == ["linear-api", "notion-api", "stripe-api"]

    def test_empty_catalog_yields_no_distractors(self, tmp_path):
        cfg = _FakeConfig(tmp_path)
        _, distractor, _ = _resolve_task_apis({"task_id": "f5", "prompt": "hi"}, cfg)
        assert distractor == []


class TestMissingRequiredApisGate:
    def test_missing_declared_required_raises_with_task_and_names(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "gate-task-9", "prompt": "hi",
                "required_apis_declared": ["etsy-api", "pruned", "also-gone-api"]}
        with pytest.raises(MissingRequiredApisError) as excinfo:
            _resolve_task_apis(task, cfg)
        err = excinfo.value
        assert err.task_id == "gate-task-9"
        assert err.missing == ["also-gone-api", "pruned-api"]
        assert err.catalog_size == len(_FLEET)
        msg = str(err)
        assert "gate-task-9" in msg
        assert "also-gone-api" in msg and "pruned-api" in msg
        assert str(len(_FLEET)) in msg
        assert ALLOW_MISSING_REQUIRED_APIS_ENV in msg

    def test_gate_is_skipped_when_there_is_no_catalog_to_validate_against(self, tmp_path):
        cfg = _FakeConfig(tmp_path)
        task = {"task_id": "g2", "prompt": "hi",
                "required_apis_declared": ["anything-api"]}
        required, _, _ = _resolve_task_apis(task, cfg)
        assert required == {"anything-api"}

    def test_fully_satisfied_declaration_does_not_raise(self, tmp_path):
        cfg = _fake_catalog(tmp_path, _FLEET)
        required, _, _ = _resolve_task_apis(
            {"task_id": "g3", "prompt": "hi",
             "required_apis_declared": ["etsy-api", "notion-api"]}, cfg)
        assert required == {"etsy-api", "notion-api"}

    def test_escape_hatch_degrades_loudly(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(ALLOW_MISSING_REQUIRED_APIS_ENV, "1")
        cfg = _fake_catalog(tmp_path, _FLEET)
        task = {"task_id": "hatch-task", "prompt": "hi",
                "required_apis_declared": ["etsy-api", "pruned-api"]}
        with caplog.at_level(logging.ERROR, logger="eval.run_batch"):
            required, distractor, _ = _resolve_task_apis(task, cfg)
        assert required == {"etsy-api"}
        assert distractor == ["linear-api", "notion-api", "stripe-api"]
        loud = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert loud, "degrading silently is the exact defect this gate replaces"
        msg = loud[0].getMessage()
        assert "hatch-task" in msg and "pruned-api" in msg
        assert ALLOW_MISSING_REQUIRED_APIS_ENV in msg

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_escape_hatch_truthy_spellings(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv(ALLOW_MISSING_REQUIRED_APIS_ENV, value)
        cfg = _fake_catalog(tmp_path, _FLEET)
        required, _, _ = _resolve_task_apis(
            {"task_id": "h2", "prompt": "hi",
             "required_apis_declared": ["etsy-api", "pruned-api"]}, cfg)
        assert required == {"etsy-api"}

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
    def test_escape_hatch_stays_closed_for_other_values(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv(ALLOW_MISSING_REQUIRED_APIS_ENV, value)
        cfg = _fake_catalog(tmp_path, _FLEET)
        with pytest.raises(MissingRequiredApisError):
            _resolve_task_apis(
                {"task_id": "h3", "prompt": "hi",
                 "required_apis_declared": ["pruned-api"]}, cfg)


# ---------------------------------------------------------------------------
# _write_pass_summary — locked read-modify-write of pass_summary.json (offline)
# ---------------------------------------------------------------------------


class TestWritePassSummary:
    def test_creates_summary_file(self, tmp_path):
        model_dir = tmp_path / "claude"
        _write_pass_summary(
            model_dir, "claude", run_index=0,
            scores={"overall_score": 0.5, "criteria_total": 2}, test_result=None,
        )
        doc = json.loads((model_dir / "pass_summary.json").read_text())
        assert doc["model"] == "claude"
        assert doc["runs"] == 1
        assert doc["per_run"][0]["run_index"] == 0
        assert doc["average_reward"] == 0.5

    def test_second_run_appends(self, tmp_path):
        model_dir = tmp_path / "claude"
        _write_pass_summary(model_dir, "claude", 0, {"overall_score": 0.4}, None)
        _write_pass_summary(model_dir, "claude", 1, {"overall_score": 0.6}, None)
        doc = json.loads((model_dir / "pass_summary.json").read_text())
        assert doc["runs"] == 2
        assert doc["average_reward"] == pytest.approx(0.5)
        assert [r["run_index"] for r in doc["per_run"]] == [0, 1]

    def test_rerun_same_index_replaces(self, tmp_path):
        model_dir = tmp_path / "claude"
        _write_pass_summary(model_dir, "claude", 0, {"overall_score": 0.2}, None)
        # re-run index 0 with a better score -> old entry replaced, not duplicated
        _write_pass_summary(model_dir, "claude", 0, {"overall_score": 0.9}, None)
        doc = json.loads((model_dir / "pass_summary.json").read_text())
        assert doc["runs"] == 1
        assert doc["per_run"][0]["rubric_reward"] == 0.9

    def test_corrupt_existing_summary_recovers(self, tmp_path):
        model_dir = tmp_path / "claude"
        model_dir.mkdir()
        (model_dir / "pass_summary.json").write_text("{ this is not valid json")
        # malformed existing file -> treated as empty, does not raise
        _write_pass_summary(model_dir, "claude", 0, {"overall_score": 0.3}, None)
        doc = json.loads((model_dir / "pass_summary.json").read_text())
        assert doc["runs"] == 1


# ---------------------------------------------------------------------------
# _backfill_per_message_cost — per-message usage/cost attribution from the
# sidecar usage.jsonl. Run-key selection must match the totals path in
# src/utils/grading.py::extract_usage_from_litellm_log, and a row/message count
# mismatch must be loud instead of silently shifting every later message's
# tokens and dollars.
# ---------------------------------------------------------------------------

_RK_A = "wcb::task_a::aaaa1111"
_RK_B = "wcb::task_b::bbbb2222"


def _usage_row(ts, run_key=None, *, out=10, cost=0.01, kind="agent", **extra):
    row = {
        "ts": ts, "kind": kind, "model": "bedrock/opus",
        "input_tokens": 100, "output_tokens": out, "total_tokens": 100 + out,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "audio_seconds": 0.0, "cost_usd": cost,
    }
    if run_key is not None:
        row["run_key"] = run_key
    row.update(extra)
    return row


def _write_usage_log(tmp_path, rows, name="usage.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def _traj(n_assistants, day="17"):
    msgs = [{"timestamp": f"2026-08-{day}T07:50:00+00:00", "role": "user"}]
    for i in range(n_assistants):
        msgs.append({
            "timestamp": f"2026-08-{day}T07:50:{i + 1:02d}+00:00",
            "role": "assistant",
        })
    return {"messages": msgs}


def _costs(traj):
    return [
        m["usage"]["cost"]["total"]
        for m in traj["messages"]
        if m.get("role") == "assistant" and isinstance(m.get("usage"), dict)
    ]


class TestBackfillPerMessageCostRunKey:
    def test_parallel_runs_do_not_cross_attribute(self, tmp_path):
        # Two runs interleaved on one shared sidecar log, as happens under
        # --parallel. Each run must see only its own rows.
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
            _usage_row("2026-08-17T07:50:01+00:00", _RK_B, cost=0.91),
            _usage_row("2026-08-17T07:50:02+00:00", _RK_B, cost=0.92),
            _usage_row("2026-08-17T07:50:02+00:00", _RK_A, cost=0.12),
        ])
        traj_a = _traj(2)
        assert _backfill_per_message_cost(traj_a, log, _RK_A) == 2
        assert _costs(traj_a) == [0.11, 0.12]

        traj_b = _traj(2)
        assert _backfill_per_message_cost(traj_b, log, _RK_B) == 2
        assert _costs(traj_b) == [0.91, 0.92]

    def test_untagged_concurrent_rows_are_ignored(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", None, cost=9.99),
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
            _usage_row("2026-08-17T07:50:02+00:00", None, cost=9.99),
            _usage_row("2026-08-17T07:50:02+00:00", _RK_A, cost=0.12),
        ])
        traj = _traj(2)
        assert _backfill_per_message_cost(traj, log, _RK_A) == 2
        assert _costs(traj) == [0.11, 0.12]

    def test_tokens_and_split_preserved(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, out=50, cost=0.6),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(traj, log, _RK_A) == 1
        u = traj["messages"][1]["usage"]
        assert u["input"] == 100 and u["output"] == 50
        assert u["totalTokens"] == 150
        assert u["cost"]["total"] == pytest.approx(0.6)
        # proportional split over the 1x input / 5x output weights
        assert sum(u["cost"][k] for k in ("input", "output", "cacheRead",
                                          "cacheWrite")) == pytest.approx(0.6)
        assert u["cost"]["output"] > u["cost"]["input"]

    def test_failure_and_preflight_rows_excluded(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:00+00:00", _RK_A, kind="preflight"),
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, kind="failure"),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(traj, log, _RK_A) == 1
        assert _costs(traj) == [0.11]

    def test_whisper_transcription_row_excluded(self, tmp_path):
        # The audio-extract skill bills by duration on the run's own key and
        # produces no assistant message; counting it would force a mismatch.
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.02,
                       input_tokens=0, output_tokens=0, total_tokens=0,
                       audio_seconds=12.5),
            _usage_row("2026-08-17T07:50:02+00:00", _RK_A, cost=0.12),
        ])
        traj = _traj(2)
        assert _backfill_per_message_cost(traj, log, _RK_A) == 2
        assert _costs(traj) == [0.11, 0.12]

    def test_row_with_unreadable_ts_still_attributed(self, tmp_path):
        # The tagged path must not depend on parsing ts at all.
        log = _write_usage_log(tmp_path, [
            _usage_row("not-a-timestamp", _RK_A, cost=0.11),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(traj, log, _RK_A) == 1
        assert _costs(traj) == [0.11]


class TestBackfillPerMessageCostMismatch:
    def test_orphaned_retry_rows_are_loud_and_unattributed(self, tmp_path, caplog):
        # A stalled turn is rolled back by runner.py::_restore_session_to but
        # its usage rows survive under the SAME run_key, so a run_key filter
        # alone still leaves a positional surplus.
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.91),
            _usage_row("2026-08-17T07:50:02+00:00", _RK_A, cost=0.92),
            _usage_row("2026-08-17T07:50:03+00:00", _RK_A, cost=0.11),
            _usage_row("2026-08-17T07:50:04+00:00", _RK_A, cost=0.12),
        ])
        traj = _traj(2)
        with caplog.at_level(logging.ERROR, logger="eval.run_batch"):
            assert _backfill_per_message_cost(traj, log, _RK_A) == 0
        loud = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert loud, "surplus rows must be reported, not silently truncated"
        msg = loud[0].getMessage()
        assert "4" in msg and "2" in msg
        # No message carries another request's dollars.
        assert _costs(traj) == []

    def test_deficit_rows_do_not_silently_truncate(self, tmp_path, caplog):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
        ])
        traj = _traj(3)
        with caplog.at_level(logging.ERROR, logger="eval.run_batch"):
            assert _backfill_per_message_cost(traj, log, _RK_A) == 0
        assert [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert _costs(traj) == []

    def test_no_assistants_is_a_noop(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A),
        ])
        assert _backfill_per_message_cost(
            {"messages": [{"timestamp": "2026-08-17T07:50:00+00:00",
                           "role": "user"}]}, log, _RK_A) == 0

    def test_missing_log_is_a_noop(self, tmp_path):
        assert _backfill_per_message_cost(
            _traj(2), str(tmp_path / "absent.jsonl"), _RK_A) == 0


class TestBackfillPerMessageCostLegacyFallback:
    def test_legacy_log_without_run_key_uses_window_and_warns(self, tmp_path, caplog):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", None, cost=0.11),
            _usage_row("2026-08-17T07:50:02+00:00", None, cost=0.12),
        ])
        traj = _traj(2)
        with caplog.at_level(logging.WARNING, logger="eval.run_batch"):
            assert _backfill_per_message_cost(traj, log, "") == 2
        warned = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warned, "the legacy window fallback must announce itself"
        assert "OVER-ATTRIBUTES" in warned[0].getMessage()
        assert _costs(traj) == [0.11, 0.12]

    def test_window_fallback_warns_when_run_key_matches_nothing(self, tmp_path, caplog):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_B, cost=0.91),
        ])
        traj = _traj(1)
        with caplog.at_level(logging.WARNING, logger="eval.run_batch"):
            _backfill_per_message_cost(traj, log, _RK_A)
        assert [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_window_excludes_rows_outside_the_run(self, tmp_path, caplog):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T06:00:00+00:00", None, cost=9.99),
            _usage_row("2026-08-17T07:50:01+00:00", None, cost=0.11),
            _usage_row("2026-08-17T09:00:00+00:00", None, cost=9.99),
        ])
        traj = _traj(1)
        with caplog.at_level(logging.WARNING, logger="eval.run_batch"):
            assert _backfill_per_message_cost(traj, log, "") == 1
        assert _costs(traj) == [0.11]

    def test_faketime_narrative_clock_defeats_the_window(self, tmp_path, caplog):
        # The agent container runs under docker/agent_faketime_shim.js, so
        # chat.jsonl timestamps are narrative-clock values tens of days from
        # the sidecar's real UTC ts. This is why the window cannot be the
        # primary selector; the run-key path below still works.
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A, cost=0.11),
        ])
        narrative = _traj(1, day="17")
        narrative["messages"] = [
            dict(m, timestamp=m["timestamp"].replace("2026-08-17", "2026-11-03"))
            for m in narrative["messages"]
        ]
        with caplog.at_level(logging.ERROR, logger="eval.run_batch"):
            assert _backfill_per_message_cost(narrative, log, "") == 0
        assert _costs(narrative) == []

        tagged = _traj(1, day="17")
        tagged["messages"] = [
            dict(m, timestamp=m["timestamp"].replace("2026-08-17", "2026-11-03"))
            for m in tagged["messages"]
        ]
        assert _backfill_per_message_cost(tagged, log, _RK_A) == 1
        assert _costs(tagged) == [0.11]

    def test_naive_message_clock_does_not_raise(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", None, cost=0.11),
        ])
        traj = {"messages": [
            {"timestamp": "2026-08-17T07:50:00", "role": "user"},
            {"timestamp": "2026-08-17T07:50:01", "role": "assistant"},
        ]}
        assert _backfill_per_message_cost(traj, log, "") == 0


class TestBackfillPerMessageCostOAuthRepricing:
    # On the OAuth route a row's recorded cost_usd is not comparable with a
    # Bedrock run's (it is either the zeroed subscription price or the paid-API
    # list price, depending on the sidecar's per-token config), so the block is
    # re-derived from the row's own tokens at the same card usage.json uses.
    @staticmethod
    def _oauth_row(ts, run_key):
        return _usage_row(
            ts, run_key, model="claude-opus-5", cost=4.2,
            input_tokens=1_000, output_tokens=2_000,
            cache_read_tokens=10_000, cache_write_tokens=4_000,
            total_tokens=17_000,
        )

    def test_oauth_row_is_repriced_from_its_own_tokens(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            self._oauth_row("2026-08-17T07:50:01+00:00", _RK_A),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(
            traj, log, _RK_A, oauth_route=True, model="claude-opus-5") == 1
        cost = traj["messages"][1]["usage"]["cost"]
        assert cost == {
            "input": pytest.approx(0.005),
            "output": pytest.approx(0.05),
            "cacheRead": pytest.approx(0.005),
            "cacheWrite": pytest.approx(0.025),
            "total": pytest.approx(0.085),
        }

    def test_oauth_split_is_exact_not_apportioned(self, tmp_path):
        # Off the card each token class carries its own published rate, so the
        # parts are priced directly instead of dividing a total by weights.
        log = _write_usage_log(tmp_path, [
            self._oauth_row("2026-08-17T07:50:01+00:00", _RK_A),
        ])
        traj = _traj(1)
        _backfill_per_message_cost(
            traj, log, _RK_A, oauth_route=True, model="claude-opus-5")
        cost = traj["messages"][1]["usage"]["cost"]
        parts = sum(cost[k] for k in ("input", "output", "cacheRead", "cacheWrite"))
        assert parts == pytest.approx(cost["total"])

    def test_per_message_totals_sum_to_the_agent_source_cost(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            self._oauth_row("2026-08-17T07:50:01+00:00", _RK_A),
            self._oauth_row("2026-08-17T07:50:02+00:00", _RK_A),
        ])
        traj = _traj(2)
        _backfill_per_message_cost(
            traj, log, _RK_A, oauth_route=True, model="claude-opus-5")
        agent = {
            "input_tokens": 2_000, "output_tokens": 4_000,
            "cache_read_tokens": 20_000, "cache_write_tokens": 8_000,
            "cost_usd": 4.2,
        }
        reprice_oauth_sources({"agent": agent}, model="claude-opus-5", oauth_route=True)
        assert sum(_costs(traj)) == pytest.approx(agent["cost_usd"])

    def test_bedrock_rows_keep_the_recorded_cost_and_weighted_split(self, tmp_path):
        log = _write_usage_log(tmp_path, [
            self._oauth_row("2026-08-17T07:50:01+00:00", _RK_A),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(
            traj, log, _RK_A, model="claude-opus-5") == 1
        assert traj["messages"][1]["usage"]["cost"]["total"] == pytest.approx(4.2)

    def test_oauth_row_off_the_card_falls_back_to_the_recorded_cost(self, tmp_path):
        # A model the card cannot price is left with the cost it was billed at,
        # never re-derived into an invented figure or flattened to $0. The OAuth
        # branch never serves gpt-5.5, but a row for it can still land in a
        # shared sidecar log.
        log = _write_usage_log(tmp_path, [
            _usage_row("2026-08-17T07:50:01+00:00", _RK_A,
                       model="gpt-5.5", cost=0.77),
        ])
        traj = _traj(1)
        assert _backfill_per_message_cost(
            traj, log, _RK_A, oauth_route=True, model="gpt-5.5") == 1
        assert _costs(traj) == [pytest.approx(0.77)]


class TestSaveUsageOneCostColumn:
    _AGENT = {
        "input_tokens": 1_000, "output_tokens": 2_000,
        "cache_read_tokens": 10_000, "cache_write_tokens": 4_000,
        "total_tokens": 17_000, "cost_usd": 6e-06, "request_count": 3,
        "usage_source": "litellm_run_key",
    }
    _JUDGE = {
        "input_tokens": 20_000, "output_tokens": 1_000,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "total_tokens": 21_000, "cost_usd": 4.2, "request_count": 1,
        "per_member": {
            "sonnet": {
                "model": "claude-sonnet-5",
                "input_tokens": 20_000, "output_tokens": 1_000,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "total_tokens": 21_000, "cost_usd": 4.2, "request_count": 1,
            },
        },
    }

    def _save(self, tmp_path, *, oauth_route):
        tmp_path.mkdir(parents=True, exist_ok=True)
        save_usage(
            tmp_path, {}, dict(self._AGENT), "t1",
            judge_usage=json.loads(json.dumps(self._JUDGE)),
            model="claude-opus-5", oauth_route=oauth_route,
        )
        return json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))

    def test_oauth_costs_are_all_token_derived(self, tmp_path):
        out = self._save(tmp_path, oauth_route=True)
        assert out["sources"]["agent"]["cost_usd"] == pytest.approx(0.085)
        assert out["sources"]["judge"]["cost_usd"] == pytest.approx(0.075)
        member = out["sources"]["judge"]["per_member"]["sonnet"]
        assert member["cost_usd"] == pytest.approx(0.075)
        assert out["cost_usd"] == pytest.approx(0.16)

    def test_oauth_rollup_is_the_sum_of_the_repriced_sources(self, tmp_path):
        out = self._save(tmp_path, oauth_route=True)
        assert out["cost_usd"] == pytest.approx(
            out["sources"]["agent"]["cost_usd"]
            + out["sources"]["judge"]["cost_usd"]
        )

    def test_oauth_run_is_stamped_with_its_route(self, tmp_path):
        assert self._save(tmp_path, oauth_route=True)["auth_provider"] == "oauth"

    def test_bedrock_run_is_stamped_with_its_route(self, tmp_path):
        assert self._save(tmp_path, oauth_route=False)["auth_provider"] == "bedrock"

    def test_bedrock_costs_are_recorded_verbatim(self, tmp_path):
        # Regression pin: nothing on the Bedrock route may be re-derived. The
        # provenance stamp is the only key this change adds to its usage.json.
        out = self._save(tmp_path, oauth_route=False)
        assert out.pop("auth_provider") == "bedrock"
        assert out == {
            "input_tokens": 21_000,
            "output_tokens": 3_000,
            "cache_read_tokens": 10_000,
            "cache_write_tokens": 4_000,
            "total_tokens": 38_000,
            "request_count": 4,
            "cost_usd": pytest.approx(4.200006),
            "usage_source": "litellm_run_key",
            "sources": {"agent": self._AGENT, "judge": self._JUDGE},
        }

    def test_the_provenance_stamp_survives_a_route_whose_costs_look_paid(self, tmp_path):
        # The old "cost_usd == 0 proves this ran on the subscription" read is
        # dead — both routes now carry real dollars — so the stamp is the only
        # thing distinguishing them in the artifact.
        oauth = self._save(tmp_path / "o", oauth_route=True)
        bedrock = self._save(tmp_path / "b", oauth_route=False)
        assert oauth["cost_usd"] > 0 and bedrock["cost_usd"] > 0
        assert oauth["auth_provider"] != bedrock["auth_provider"]


class TestSaveUsageStripsRunKey:
    def test_run_key_never_reaches_usage_json(self, tmp_path):
        # In keyless sidecar mode the run key IS the agent's bearer, so it must
        # not survive into a delivered artifact.
        usage = {
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 0.5, "request_count": 1,
            "usage_source": "litellm_run_key",
            "__run_key__": "wcb::t1::deadbeefcafe",
        }
        result = save_usage(tmp_path, {}, usage, "t1")
        written = (tmp_path / "usage.json").read_text(encoding="utf-8")
        assert "deadbeefcafe" not in written
        assert "__run_key__" not in written
        assert "deadbeefcafe" not in json.dumps(result)
        assert json.loads(written)["input_tokens"] == 10
