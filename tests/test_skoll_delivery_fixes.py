"""Tests for the skoll-delivery bundle-output fixes:

1+2. user-message prefix trimming (output.json / delivery.json show exact prompt)
3.   delivery.json carries `artifacts` / `input_files`
4.   sub-agent costing (model capture + cost_usd)
5.   score.json reports real pytest counts, not rubric criteria
6.   bundle prompt.txt holds the full multi-turn prompt
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.utils.trajectory.builder import (
    _strip_user_turn_prefix,
    build_trajectory_from_jsonl,
)
from src.utils.store import Task as StoreTask
from src.utils.delivery_schema import build_delivery_trajectory
from src.utils.subagent_director import (
    SubagentResult,
    SubagentSpec,
    _subagent_cost_usd,
    run_with_invoker,
    summarize_results,
)
from eval.run_batch import _overlay_pytest_counts
from src.utils.harbor.bundle import _resolve_bundle_prompt


# ---------------------------------------------------------------- Fix 1+2 ----

_BOILERPLATE = (
    "You are an expert in a restricted, non-interactive environment. "
    "Solve the task efficiently before the timeout (1800s). Run all processes "
    "in the foreground without user input or background services. Provide a "
    "complete, functional solution in a single pass with no placeholders. "
)


def test_strip_turn0_boilerplate_and_header():
    raw = _BOILERPLATE + "\n[TURN 1 (Day 1, 06:45, Multi-Agent)]\n\n\nDo the thing."
    assert _strip_user_turn_prefix(raw) == "Do the thing."


def test_strip_utc_and_turn_header():
    raw = "[Mon 2026-06-15 17:48 UTC] [TURN 2 (Day 1, 08:30, Light)]\n\n\nUpdate the tracker."
    assert _strip_user_turn_prefix(raw) == "Update the tracker."


def test_strip_leaves_plain_text_untouched():
    # arbitrary text that merely begins with a bracket must be preserved
    for s in ["[not a turn] hello", "Normal prompt with [brackets] inside",
              "[TURNSTILE] keep", "plain prompt"]:
        assert _strip_user_turn_prefix(s) == s


def test_builder_trims_user_messages():
    entries = [
        {"type": "message", "id": "u0", "timestamp": "t0", "message": {
            "role": "user", "content": [{"type": "text",
                "text": _BOILERPLATE + "\n[TURN 1 (Day 1, 06:45, Multi-Agent)]\n\n\nHello."}]}},
        {"type": "message", "id": "a0", "timestamp": "t1", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "[keep me] reply"}]}},
        {"type": "message", "id": "u1", "timestamp": "t2", "message": {
            "role": "user", "content": [{"type": "text",
                "text": "[Mon 2026-06-15 17:48 UTC] [TURN 2 (Day 1, 08:30, Light)]\n\n\nSecond turn."}]}},
    ]
    task = StoreTask(id="T", task_id="T", persona="", initial_prompt="",
                     seed_prompt="", task_type="")
    traj = build_trajectory_from_jsonl(task, entries)
    users = [m for m in traj["messages"] if m["message"].get("role") == "user"]
    assert users[0]["message"]["content"][0]["text"] == "Hello."
    assert users[1]["message"]["content"][0]["text"] == "Second turn."
    # assistant content with a leading bracket must NOT be trimmed
    asst = [m for m in traj["messages"] if m["message"].get("role") == "assistant"][0]
    assert asst["message"]["content"][0]["text"] == "[keep me] reply"


# ------------------------------------------------------------------ Fix 3 ----

def test_delivery_includes_artifacts_and_input_files():
    traj = {
        "messages": [{"type": "message", "id": "u0", "timestamp": "t",
                      "message": {"role": "user",
                                  "content": [{"type": "text", "text": "hi"}]}}],
        "output_artifacts": [{"filename": "brief.md", "path": "/w/brief.md"}],
        "input_files": [{"filename": "data.csv"}],
    }
    deliv = build_delivery_trajectory(traj)
    assert "artifacts" in deliv and "input_files" in deliv
    assert deliv["artifacts"] == [{"filename": "brief.md", "path": "/w/brief.md"}]
    assert deliv["input_files"] == [{"filename": "data.csv"}]


def test_delivery_artifacts_default_empty():
    deliv = build_delivery_trajectory({"messages": []})
    assert deliv["artifacts"] == []
    assert deliv["input_files"] == []


# ------------------------------------------------------------------ Fix 4 ----

def test_subagent_cost_static_table():
    # opus: 700k input * 15e-6 + 10k output * 75e-6 = 10.5 + 0.75
    assert _subagent_cost_usd("claude-opus-4-6", 700_000, 10_000) == 11.25
    # sonnet: 100k * 3e-6 + 5k * 15e-6 = 0.3 + 0.075
    assert _subagent_cost_usd("claude-sonnet-4-6", 100_000, 5_000) == 0.375


def test_subagent_cost_unknown_and_none_are_zero():
    assert _subagent_cost_usd(None, 100, 100) == 0.0
    assert _subagent_cost_usd("totally-unknown-xyz", 100, 100) == 0.0


def test_subagent_cost_includes_cache_tokens():
    # opus cache_read 1.5e-6, cache_write 18.75e-6
    cost = _subagent_cost_usd("claude-opus-4-6", 0, 0, cache_read=10_000, cache_write=1_000)
    assert cost == round(10_000 * 1.5e-6 + 1_000 * 18.75e-6, 6)


def test_run_with_invoker_prices_and_logs_cost():
    def fake(sys_p, usr_p, spec):
        return {"output": "done", "tool_calls": 1, "tokens_in": 700_000,
                "tokens_out": 10_000, "model": "claude-opus-4-6", "rounds": []}
    spec = SubagentSpec(role="drift", instructions="x", allowed_tools=("Read",))
    res = run_with_invoker(spec, fake, spawn_id="spw_x")
    assert res.model == "claude-opus-4-6"
    assert res.cost_usd == 11.25
    row = res.to_log_row(spec=spec, turn_index=4, parent_session_id="s")
    assert row["cost_usd"] == 11.25
    assert row["model"] == "claude-opus-4-6"
    summ = summarize_results([res], scope="single")
    assert summ["cost_usd"] == 11.25


# ------------------------------------------------------------------ Fix 5 ----

def test_overlay_pytest_counts_replaces_tests_keeps_criteria():
    scores = {"criteria_total": 32, "criteria_passed": 0, "criteria_abstained": 32,
              "tests_total": 32, "tests_passed": 0, "tests_failed": 0}
    result = {"test_result": {"tests_total": 17, "tests_passed": 8,
                              "tests_failed": 9, "reward": 0.6429}}
    _overlay_pytest_counts(scores, result)
    assert (scores["tests_total"], scores["tests_passed"], scores["tests_failed"]) == (17, 8, 9)
    # rubric criteria untouched
    assert scores["criteria_total"] == 32 and scores["criteria_abstained"] == 32


def test_overlay_noop_without_test_result():
    scores = {"criteria_total": 10, "tests_total": 10, "tests_passed": 3, "tests_failed": 7}
    _overlay_pytest_counts(scores, {})
    assert scores["tests_total"] == 10  # legacy alias preserved for rubric-only tasks


# ------------------------------------------------------------------ Fix 6 ----

def test_resolve_bundle_prompt_prefers_full_prompts_file(tmp_path: Path):
    full = "--- TURN 1 ---\nfirst\n--- TURN 2 ---\nsecond"
    (tmp_path / "prompts.txt").write_text(full, encoding="utf-8")
    task = SimpleNamespace(initial_prompt="first", seed_prompt="")
    assert _resolve_bundle_prompt(task, tmp_path) == full


def test_resolve_bundle_prompt_falls_back_to_initial(tmp_path: Path):
    task = SimpleNamespace(initial_prompt="just turn 0", seed_prompt="")
    assert _resolve_bundle_prompt(task, tmp_path) == "just turn 0"
    assert _resolve_bundle_prompt(task, None) == "just turn 0"


def test_instruction_md_decoupled_from_full_prompt():
    # Regression guard: prompt.txt must use the full multi-turn prompt while
    # instruction.md stays the single-turn initial prompt (reference parity).
    import inspect
    from src.utils.harbor import bundle
    src = inspect.getsource(bundle.write_bundle)
    assert 'prompt_text = _resolve_bundle_prompt' in src
    assert '(out_dir / "prompt.txt").write_text(prompt_text' in src
    assert 'instruction_text = task.initial_prompt or task.seed_prompt' in src
    assert '(data_dir / "instruction.md").write_text(instruction_text' in src
