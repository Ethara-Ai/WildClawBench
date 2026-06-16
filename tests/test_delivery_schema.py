from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.delivery_schema import (  # noqa: E402
    build_delivery_trajectory,
    write_delivery_json,
)


def _minimal_traj(messages: list[dict] | None = None) -> dict:
    return {
        "session_id": "ses_test",
        "timestamp": "2026-06-09T00:00:00Z",
        "trajectory": {
            "meta_info": {
                "platform": "linux",
                "taxonomy_l2": "test_task_id",
            },
        },
        "messages": messages or [],
    }


def _msg(mid: str, role: str, content: list[dict], ts: str = "2026-06-09T00:00:00Z") -> dict:
    return {
        "type": "message",
        "id": mid,
        "timestamp": ts,
        "message": {"role": role, "content": content},
        "turn_index": 0,
    }


def _tool_result_msg(
    mid: str, tool_call_id: str, tool_name: str, text: str, is_error: bool = False,
) -> dict:
    return {
        "type": "message",
        "id": mid,
        "timestamp": "2026-06-09T00:00:00Z",
        "message": {
            "role": "toolResult",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "isError": is_error,
            "content": [{"type": "text", "text": text}],
            "details": {"exit_code": 0},
        },
    }


def test_meta_info_pulled_from_trajectory_when_params_omitted() -> None:
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "hello"}])])
    delivery = build_delivery_trajectory(traj)
    assert delivery["meta_info"]["task_type"] == "test_task_id"
    assert delivery["meta_info"]["platform"] == "linux"
    assert delivery["meta_info"]["task_description"] == "hello"
    assert delivery["meta_info"]["system_prompt"] == ""
    assert delivery["meta_info"]["task_completion_status"] == "completed"


def test_meta_info_params_override_trajectory_fallbacks() -> None:
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "hi"}])])
    delivery = build_delivery_trajectory(
        traj, task_type="my-task", system_prompt="You are a robot.",
    )
    assert delivery["meta_info"]["task_type"] == "my-task"
    assert delivery["meta_info"]["system_prompt"] == "You are a robot."


def test_completion_status_from_score_combined_reward() -> None:
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "x"}])])
    assert build_delivery_trajectory(traj, score={"combined_reward": 0.6})["meta_info"][
        "task_completion_status"
    ] == "completed"
    assert build_delivery_trajectory(traj, score={"combined_reward": 0.2})["meta_info"][
        "task_completion_status"
    ] == "partial"
    assert build_delivery_trajectory(traj, score={"error": "judge 401"})["meta_info"][
        "task_completion_status"
    ] == "partial"


def test_messages_reshape_text_and_thinking_blocks_drop_extra_metadata() -> None:
    traj = _minimal_traj([
        _msg("a1", "user", [{"type": "text", "text": "go"}]),
        _msg("a2", "assistant", [
            {"type": "thinking", "thinking": "let me see", "thinkingSignature": "sig123"},
            {"type": "text", "text": "ok"},
        ]),
    ])
    delivery = build_delivery_trajectory(traj)
    assert [m["id"] for m in delivery["messages"]] == ["a1", "a2"]
    assert delivery["messages"][0]["parentId"] is None
    assert delivery["messages"][0]["message"]["role"] == "user"
    assert "turn_index" not in delivery["messages"][0]
    a2 = delivery["messages"][1]["message"]
    assert a2["role"] == "assistant"
    assert a2["content"][0] == {
        "type": "thinking", "thinking": "let me see", "thinkingSignature": "sig123",
    }
    assert a2["content"][1] == {"type": "text", "text": "ok"}


def test_parent_id_pulled_from_chat_jsonl(tmp_path: Path) -> None:
    chat = tmp_path / "chat.jsonl"
    chat.write_text(
        "\n".join([
            json.dumps({"type": "session", "id": "root", "parentId": None}),
            json.dumps({"type": "message", "id": "a1", "parentId": "root"}),
            json.dumps({"type": "message", "id": "a2", "parentId": "a1"}),
            "",
            "{not json",
        ]),
        encoding="utf-8",
    )
    traj = _minimal_traj([
        _msg("a1", "user", [{"type": "text", "text": "go"}]),
        _msg("a2", "assistant", [{"type": "text", "text": "done"}]),
    ])
    delivery = build_delivery_trajectory(traj, chat_jsonl_path=chat)
    parents = {m["id"]: m["parentId"] for m in delivery["messages"]}
    assert parents == {"a1": "root", "a2": "a1"}


def test_missing_chat_jsonl_yields_none_parent_ids(tmp_path: Path) -> None:
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "go"}])])
    delivery = build_delivery_trajectory(
        traj, chat_jsonl_path=tmp_path / "does_not_exist.jsonl",
    )
    assert delivery["messages"][0]["parentId"] is None


def test_non_spawn_exec_passes_through_unchanged() -> None:
    traj = _minimal_traj([
        _msg("a1", "assistant", [
            {
                "type": "toolCall",
                "id": "tool_1",
                "name": "exec",
                "arguments": {"command": "ls -la /root/workspace"},
            },
        ]),
    ])
    delivery = build_delivery_trajectory(traj)
    block = delivery["messages"][0]["message"]["content"][0]
    assert block["name"] == "exec"
    assert block["arguments"]["command"] == "ls -la /root/workspace"


def test_spawn_exec_rewritten_as_sessions_spawn_with_parsed_spec(tmp_path: Path) -> None:
    spec = {"role": "trial-data-reconciler", "instructions": "Read /tmp_workspace/x.csv."}
    cmd = (
        "cat <<EOF > /tmp/spec.json\n"
        + json.dumps(spec)
        + "\nEOF\npython3 /usr/lib/node_modules/openclaw/skills/"
        + "spawn-subagent-connector/scripts/spawn_subagent.py < /tmp/spec.json"
    )
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(
        json.dumps({
            "spawn_id": "spw_abcdef123456",
            "role": "trial-data-reconciler",
            "status": "ok",
            "output_preview": "memo",
        }) + "\n",
        encoding="utf-8",
    )
    traj = _minimal_traj([
        _msg("a1", "assistant", [{
            "type": "toolCall", "id": "tc_1", "name": "exec",
            "arguments": {"command": cmd},
        }]),
        _tool_result_msg("a2", "tc_1", "exec", "memo full text"),
    ])
    delivery = build_delivery_trajectory(traj, spawn_tree_path=spawn_tree)

    spawn_block = delivery["messages"][0]["message"]["content"][0]
    assert spawn_block["name"] == "sessions_spawn"
    assert spawn_block["arguments"] == spec
    assert spawn_block["id"] == "tc_1"

    result = delivery["messages"][1]["message"]
    assert result["role"] == "toolResult"
    assert result["toolName"] == "sessions_yield"
    assert result["toolCallId"] == "tc_1"
    payload = json.loads(result["content"][0]["text"])
    assert payload == {
        "session_id": "spw_abcdef123456",
        "status": "completed",
        "output": "memo full text",
    }
    assert result["isError"] is False


def test_spawn_failure_status_propagates_to_session_yield(tmp_path: Path) -> None:
    spec = {"role": "guide-extractor", "instructions": "Read /tmp_workspace/g.pdf."}
    cmd = (
        "cat <<EOF > /tmp/spec.json\n" + json.dumps(spec) + "\nEOF\n"
        "python3 spawn_subagent.py < /tmp/spec.json"
    )
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(json.dumps({
        "spawn_id": "spw_xyz",
        "status": "timeout",
        "output_preview": "(no answer)",
    }) + "\n", encoding="utf-8")
    traj = _minimal_traj([
        _msg("a1", "assistant", [{
            "type": "toolCall", "id": "tc_z", "name": "exec",
            "arguments": {"command": cmd},
        }]),
        _tool_result_msg("a2", "tc_z", "exec", "", is_error=True),
    ])
    delivery = build_delivery_trajectory(traj, spawn_tree_path=spawn_tree)
    result = delivery["messages"][1]["message"]
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "timeout"
    assert payload["output"] == "(no answer)"
    assert result["isError"] is True


def test_multiple_spawns_join_in_file_order(tmp_path: Path) -> None:
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(
        json.dumps({"spawn_id": "spw_first", "status": "ok",
                    "output_preview": "first"}) + "\n"
        + json.dumps({"spawn_id": "spw_second", "status": "ok",
                      "output_preview": "second"}) + "\n",
        encoding="utf-8",
    )

    def _cmd(spec: dict) -> str:
        return (
            "cat <<EOF > /tmp/spec.json\n" + json.dumps(spec) + "\nEOF\n"
            "python3 spawn_subagent.py < /tmp/spec.json"
        )

    traj = _minimal_traj([
        _msg("a1", "assistant", [{
            "type": "toolCall", "id": "tc_A", "name": "exec",
            "arguments": {"command": _cmd({"role": "r1", "instructions": "i"})},
        }]),
        _tool_result_msg("a2", "tc_A", "exec", "first-out"),
        _msg("a3", "assistant", [{
            "type": "toolCall", "id": "tc_B", "name": "exec",
            "arguments": {"command": _cmd({"role": "r2", "instructions": "i"})},
        }]),
        _tool_result_msg("a4", "tc_B", "exec", "second-out"),
    ])
    delivery = build_delivery_trajectory(traj, spawn_tree_path=spawn_tree)
    yield_a = json.loads(delivery["messages"][1]["message"]["content"][0]["text"])
    yield_b = json.loads(delivery["messages"][3]["message"]["content"][0]["text"])
    assert yield_a["session_id"] == "spw_first"
    assert yield_b["session_id"] == "spw_second"


def test_tool_result_without_spawn_match_strips_details_field() -> None:
    traj = _minimal_traj([
        _tool_result_msg("a1", "unmatched_tc", "exec", "hello", is_error=False),
    ])
    delivery = build_delivery_trajectory(traj)
    result = delivery["messages"][0]["message"]
    assert result["role"] == "toolResult"
    assert result["toolName"] == "exec"
    assert result["toolCallId"] == "unmatched_tc"
    assert result["content"] == [{"type": "text", "text": "hello"}]
    assert "details" not in result


def test_write_delivery_json_round_trip_on_disk(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "task_output" / "workspace_full").mkdir(parents=True)
    (run_dir / "chat.jsonl").write_text(
        json.dumps({"type": "message", "id": "a1", "parentId": "root"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "task_output" / "workspace_full" / "spawn_tree.jsonl").write_text("", encoding="utf-8")
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "go"}])])
    dest = write_delivery_json(run_dir, traj, score={"combined_reward": 0.75})
    assert dest == run_dir / "delivery.json"
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["meta_info"]["task_completion_status"] == "completed"
    assert loaded["messages"][0]["parentId"] == "root"


def test_write_delivery_json_works_without_any_sidecar_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "bare_run"
    run_dir.mkdir()
    traj = _minimal_traj([_msg("a1", "user", [{"type": "text", "text": "x"}])])
    dest = write_delivery_json(run_dir, traj)
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["messages"][0]["parentId"] is None


def _spawn_setup(tmp_path: Path, spawn_id: str, role: str) -> tuple[dict, Path, Path]:
    spec = {"role": role, "instructions": "do the thing"}
    cmd = (
        "cat <<EOF > /tmp/spec.json\n" + json.dumps(spec) + "\nEOF\n"
        "python3 spawn_subagent.py < /tmp/spec.json"
    )
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(json.dumps({
        "spawn_id": spawn_id, "role": role, "status": "ok",
        "output_preview": "out",
    }) + "\n", encoding="utf-8")
    traj = _minimal_traj([
        _msg("a1", "assistant", [{
            "type": "toolCall", "id": "tc_1", "name": "exec",
            "arguments": {"command": cmd},
        }]),
        _tool_result_msg("a2", "tc_1", "exec", "child output"),
    ])
    subagents_dir = tmp_path / "subagents"
    subagents_dir.mkdir()
    return traj, spawn_tree, subagents_dir


def test_sub_agent_trajectories_embeds_matching_delivery_file(tmp_path: Path) -> None:
    traj, spawn_tree, subagents_dir = _spawn_setup(tmp_path, "spw_match01", "extractor")
    child_payload = {
        "meta_info": {
            "task_type": "extractor",
            "task_description": "do the thing",
            "task_completion_status": "completed",
            "system_prompt": "S",
            "platform": "linux",
        },
        "messages": [{"id": "spw_match01:m0", "parentId": None}],
    }
    (subagents_dir / "spw_match01.delivery.json").write_text(
        json.dumps(child_payload), encoding="utf-8",
    )
    delivery = build_delivery_trajectory(
        traj, spawn_tree_path=spawn_tree, subagents_dir=subagents_dir,
    )
    assert "sub_agent_trajectories" in delivery
    assert set(delivery["sub_agent_trajectories"].keys()) == {"spw_match01"}
    embedded = delivery["sub_agent_trajectories"]["spw_match01"]
    assert embedded["meta_info"]["task_type"] == "extractor"
    assert embedded["messages"] == [{"id": "spw_match01:m0", "parentId": None}]


def test_sub_agent_trajectories_empty_when_no_sibling_file(tmp_path: Path) -> None:
    traj, spawn_tree, subagents_dir = _spawn_setup(tmp_path, "spw_nofile02", "r")
    delivery = build_delivery_trajectory(
        traj, spawn_tree_path=spawn_tree, subagents_dir=subagents_dir,
    )
    assert delivery["sub_agent_trajectories"] == {}


def test_sub_agent_trajectories_only_embeds_present_spawns(tmp_path: Path) -> None:
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(
        json.dumps({"spawn_id": "spw_have", "status": "ok",
                    "output_preview": "a"}) + "\n"
        + json.dumps({"spawn_id": "spw_miss", "status": "ok",
                      "output_preview": "b"}) + "\n",
        encoding="utf-8",
    )

    def _cmd(spec: dict) -> str:
        return (
            "cat <<EOF > /tmp/spec.json\n" + json.dumps(spec) + "\nEOF\n"
            "python3 spawn_subagent.py < /tmp/spec.json"
        )

    traj = _minimal_traj([
        _msg("a1", "assistant", [{
            "type": "toolCall", "id": "tc_A", "name": "exec",
            "arguments": {"command": _cmd({"role": "r1", "instructions": "i"})},
        }]),
        _tool_result_msg("a2", "tc_A", "exec", "first"),
        _msg("a3", "assistant", [{
            "type": "toolCall", "id": "tc_B", "name": "exec",
            "arguments": {"command": _cmd({"role": "r2", "instructions": "i"})},
        }]),
        _tool_result_msg("a4", "tc_B", "exec", "second"),
    ])
    subagents_dir = tmp_path / "subagents"
    subagents_dir.mkdir()
    (subagents_dir / "spw_have.delivery.json").write_text(
        json.dumps({"meta_info": {"task_type": "r1"}, "messages": []}),
        encoding="utf-8",
    )

    delivery = build_delivery_trajectory(
        traj, spawn_tree_path=spawn_tree, subagents_dir=subagents_dir,
    )
    assert set(delivery["sub_agent_trajectories"].keys()) == {"spw_have"}
    assert delivery["sub_agent_trajectories"]["spw_have"]["meta_info"]["task_type"] == "r1"


def test_sub_agent_trajectories_embeds_all_present_even_undetected(tmp_path: Path) -> None:
    # report §10: subagents fanned out via wrapper scripts -> NO detectable spawn
    # exec call in the parent, but spawn_tree rows + spw delivery files exist.
    # Delivery must embed ALL of them, not just the (zero) detected ones.
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(
        "\n".join(
            json.dumps({"spawn_id": s, "status": "ok", "output_preview": s})
            for s in ("spw_a", "spw_b", "spw_c")
        ) + "\n",
        encoding="utf-8",
    )
    subagents_dir = tmp_path / "subagents"
    subagents_dir.mkdir()
    for s in ("spw_a", "spw_b", "spw_c"):
        (subagents_dir / f"{s}.delivery.json").write_text(
            json.dumps({"meta_info": {"task_name": s}, "messages": []}),
            encoding="utf-8",
        )
    # a trajectory with NO spawn exec calls -> spawn_id_map would be empty
    traj = _minimal_traj([_msg("a1", "assistant", [{"type": "text", "text": "hi"}])])

    delivery = build_delivery_trajectory(
        traj, spawn_tree_path=spawn_tree, subagents_dir=subagents_dir,
    )
    # all three embedded, ordered by spawn_tree appearance
    assert list(delivery["sub_agent_trajectories"].keys()) == ["spw_a", "spw_b", "spw_c"]


def test_sub_agent_trajectories_skips_missing_files(tmp_path: Path) -> None:
    # a spawn_tree row whose delivery file is absent must be skipped (no crash).
    spawn_tree = tmp_path / "spawn_tree.jsonl"
    spawn_tree.write_text(
        json.dumps({"spawn_id": "spw_have"}) + "\n"
        + json.dumps({"spawn_id": "spw_gone"}) + "\n",
        encoding="utf-8",
    )
    subagents_dir = tmp_path / "subagents"
    subagents_dir.mkdir()
    (subagents_dir / "spw_have.delivery.json").write_text(
        json.dumps({"meta_info": {}, "messages": []}), encoding="utf-8")
    delivery = build_delivery_trajectory(
        _minimal_traj(), spawn_tree_path=spawn_tree, subagents_dir=subagents_dir)
    assert set(delivery["sub_agent_trajectories"].keys()) == {"spw_have"}


def test_meta_info_uses_passed_task_yaml_fields() -> None:
    traj = _minimal_traj([_msg("u1", "user", [{"type": "text", "text": "hello"}])])
    deliv = build_delivery_trajectory(
        traj, task_type="A, B", task_description="Authored desc", system_prompt="SYS")
    mi = deliv["meta_info"]
    assert mi["task_type"] == "A, B"
    assert mi["task_description"] == "Authored desc"
    assert mi["system_prompt"] == "SYS"


def test_meta_info_task_description_falls_back_to_derived() -> None:
    traj = _minimal_traj([_msg("u1", "user", [{"type": "text", "text": "first user turn"}])])
    deliv = build_delivery_trajectory(traj)  # no task_description passed
    assert deliv["meta_info"]["task_description"] == "first user turn"
