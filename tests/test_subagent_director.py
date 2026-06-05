from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.subagent_director import (  # noqa: E402
    SubagentResult,
    SubagentSpec,
    _build_system_prompt,
    append_spawn_row,
    read_current_turn,
    run_with_invoker,
    write_full_transcript,
)


def _ok_invoker(_sys: str, _usr: str, _spec: SubagentSpec) -> Mapping[str, Any]:
    return {
        "output": "hello world",
        "tool_calls": 2,
        "tokens_in": 11,
        "tokens_out": 22,
    }


def test_from_dict_basic_shape():
    spec = SubagentSpec.from_dict(
        {
            "role": "budget-extractor",
            "instructions": "Pull budget totals.",
            "allowed_tools": ["read_file", "grep"],
            "context": "Files in /work",
            "model": "claude-haiku-4-5",
            "max_tool_calls": 10,
            "max_tokens": 1024,
            "timeout_seconds": 60,
        }
    )
    assert spec.role == "budget-extractor"
    assert spec.allowed_tools == ("read_file", "grep")
    assert spec.model == "claude-haiku-4-5"
    assert spec.max_tool_calls == 10


def test_from_dict_rejects_non_list_tools():
    with pytest.raises(ValueError):
        SubagentSpec.from_dict({"role": "r", "instructions": "i", "allowed_tools": "read_file"})


def test_run_with_invoker_happy_path():
    spec = SubagentSpec(role="extractor", instructions="Do the thing.")
    res = run_with_invoker(spec, _ok_invoker)
    assert res.status == "ok"
    assert res.output == "hello world"
    assert res.tool_calls == 2
    assert res.tokens_in == 11
    assert res.tokens_out == 22
    assert res.spawn_id.startswith("spw_")


def test_run_with_invoker_blocks_nested_spawn():
    spec = SubagentSpec(
        role="r",
        instructions="i",
        allowed_tools=("read_file", "spawn_subagent"),
    )
    res = run_with_invoker(spec, _ok_invoker)
    assert res.status == "blocked"
    assert "nested" in (res.error or "")
    assert res.output == ""


def test_run_with_invoker_blocks_missing_role():
    spec = SubagentSpec(role="", instructions="i")
    res = run_with_invoker(spec, _ok_invoker)
    assert res.status == "blocked"
    assert "role" in (res.error or "")


def test_run_with_invoker_blocks_missing_instructions():
    spec = SubagentSpec(role="r", instructions="   ")
    res = run_with_invoker(spec, _ok_invoker)
    assert res.status == "blocked"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("max_tool_calls", -1),
        ("max_tool_calls", 9999),
        ("max_tokens", 0),
        ("max_tokens", 10**9),
        ("timeout_seconds", 0),
        ("timeout_seconds", 10**9),
    ],
)
def test_run_with_invoker_clamps_out_of_range(field, bad_value):
    kwargs = dict(role="r", instructions="i")
    kwargs[field] = bad_value
    spec = SubagentSpec(**kwargs)
    res = run_with_invoker(spec, _ok_invoker)
    assert res.status == "blocked"
    assert field in (res.error or "")


def test_run_with_invoker_timeout():
    def slow(_s, _u, _spec):
        raise TimeoutError("model never replied")

    res = run_with_invoker(SubagentSpec(role="r", instructions="i"), slow)
    assert res.status == "timeout"
    assert "never replied" in (res.error or "")


def test_run_with_invoker_generic_error_surfaces():
    def boom(_s, _u, _spec):
        raise RuntimeError("upstream 503")

    res = run_with_invoker(SubagentSpec(role="r", instructions="i"), boom)
    assert res.status == "error"
    assert "RuntimeError" in (res.error or "")


def test_run_with_invoker_rejects_non_mapping_return():
    def junk(_s, _u, _spec):
        return "not a dict"  # type: ignore[return-value]

    res = run_with_invoker(SubagentSpec(role="r", instructions="i"), junk)
    assert res.status == "error"
    assert "non-mapping" in (res.error or "")


def test_build_system_prompt_mentions_constraints():
    spec = SubagentSpec(
        role="reconciler",
        instructions="x",
        allowed_tools=("read_file", "grep"),
        max_tool_calls=7,
    )
    prompt = _build_system_prompt(spec)
    assert "reconciler" in prompt
    assert "read_file" in prompt and "grep" in prompt
    assert "MUST NOT spawn" in prompt
    assert "7" in prompt


def test_build_system_prompt_handles_no_tools():
    spec = SubagentSpec(role="r", instructions="x")
    prompt = _build_system_prompt(spec)
    assert "(none" in prompt


def test_to_log_row_truncates_preview_and_hashes_full():
    spec = SubagentSpec(role="r", instructions="i", allowed_tools=("read_file",))
    long_out = "abc" * 500
    result = SubagentResult(
        spawn_id="spw_test1234",
        role="r",
        output=long_out,
        tool_calls=3,
        tokens_in=10,
        tokens_out=20,
        elapsed_seconds=0.123,
        status="ok",
    )
    row = result.to_log_row(spec=spec, turn_index=4, parent_session_id="ses_abc")
    assert row["spawn_id"] == "spw_test1234"
    assert row["turn_index"] == 4
    assert row["parent_session_id"] == "ses_abc"
    assert row["allowed_tools"] == ["read_file"]
    assert len(row["output_preview"]) == 240
    assert row["output_chars"] == len(long_out)
    assert len(row["output_sha256"]) == 64


def test_read_current_turn_missing_returns_minus_one(tmp_path: Path):
    assert read_current_turn(tmp_path / "absent") == -1


def test_read_current_turn_parses_int(tmp_path: Path):
    p = tmp_path / "turn"
    p.write_text("7\n")
    assert read_current_turn(p) == 7


def test_read_current_turn_malformed_returns_minus_one(tmp_path: Path):
    p = tmp_path / "turn"
    p.write_text("not-a-number")
    assert read_current_turn(p) == -1


def test_append_spawn_row_writes_ndjson(tmp_path: Path):
    path = tmp_path / "tree" / "spawn_tree.jsonl"
    append_spawn_row({"a": 1}, spawn_tree_path=path)
    append_spawn_row({"b": 2}, spawn_tree_path=path)
    lines = path.read_text().splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"b": 2}]


def test_write_full_transcript_records_three_rows(tmp_path: Path):
    spec = SubagentSpec(role="r", instructions="i")
    result = SubagentResult(
        spawn_id="spw_xyz",
        role="r",
        output="done",
        tool_calls=0,
        tokens_in=1,
        tokens_out=2,
        elapsed_seconds=0.01,
        status="ok",
    )
    out_path = write_full_transcript(
        "spw_xyz",
        spec=spec,
        result=result,
        sys_prompt="SYS",
        usr_prompt="USR",
        transcript_dir=tmp_path,
    )
    assert out_path.name == "spw_xyz.jsonl"
    rows = [json.loads(l) for l in out_path.read_text().splitlines()]
    assert [r["role"] for r in rows] == ["system", "user", "assistant"]
    assert rows[0]["content"] == "SYS"
    assert rows[1]["content"] == "USR"
    assert rows[2]["content"] == "done"
    assert rows[2]["status"] == "ok"


def test_run_batch_parallel_runs_all_and_caps_at_five():
    from src.utils.subagent_director import run_batch_parallel

    specs = [
        SubagentSpec(role=f"r{i}", instructions=f"do {i}")
        for i in range(7)
    ]

    def _inv(_s, _u, spec):
        return {"output": f"hi-{spec.role}", "tokens_in": 1, "tokens_out": 2}

    results = run_batch_parallel(specs, _inv)
    assert len(results) == 7
    assert {r.status for r in results} == {"ok"}
    assert {r.role for r in results} == {f"r{i}" for i in range(7)}
    assert {r.output for r in results} == {f"hi-r{i}" for i in range(7)}


def test_run_batch_parallel_empty():
    from src.utils.subagent_director import run_batch_parallel
    assert run_batch_parallel([], _ok_invoker) == []
