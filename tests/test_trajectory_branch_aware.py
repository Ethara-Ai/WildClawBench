"""Branch-aware trajectory building: an unanswered duplicate user turn that
lives on an orphaned sibling branch of chat.jsonl must never reach output.json.

Shape under test comes from willie_prince run_3: a gateway 1006 made the agent's
embedded fallback re-deliver a prompt, appending a SECOND user row as a sibling
of the first under the same parentId. The model answered one of them; the other
is in the file but not in the conversation. The old file-order walk linearised
it into a real turn, so the judge saw 21 user turns for a 20-turn task.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

import pytest

import src.utils.trajectory.builder as bld
from src.utils.store import Task


BUILDER_LOGGER = "src.utils.trajectory.builder"
STAMP = "[Mon 2026-06-15 14:50 UTC] "


def _task() -> Task:
    return Task(
        id="pk-1",
        task_id="demo-task",
        persona="p",
        initial_prompt="do it",
        task_type="data_analysis",
    )


def _msg(entry_id: str, parent: Optional[str], role: str, text: str) -> dict:
    return {
        "type": "message",
        "id": entry_id,
        "parentId": parent,
        "timestamp": "t-%s" % entry_id,
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


def _session() -> dict:
    return {"type": "session", "id": "chat", "timestamp": "t0", "cwd": "/w"}


def _root() -> dict:
    return {"type": "model_change", "id": "root", "parentId": None, "timestamp": "t1"}


def _legacy_messages(entries: List[dict]) -> List[dict]:
    """The pre-change file-order walk, verbatim, as the byte-identity reference."""
    messages: List[dict] = []
    last_kept_id: Optional[str] = None
    seen_user_msg = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if not role:
            continue
        if role == "user":
            seen_user_msg = True
        elif role == "system" and not seen_user_msg:
            continue
        msg = bld.sanitize_jsonl_message(msg)
        entry_id = entry.get("id", "")
        parent_id = last_kept_id if last_kept_id else entry.get("parentId", "")
        messages.append({
            "type": "message",
            "id": entry_id,
            "parentId": parent_id or "",
            "timestamp": entry.get("timestamp", ""),
            "message": msg,
        })
        last_kept_id = entry_id
    messages = [bld._wrap_trajectory_message(m) for m in messages]
    messages = bld._unwrap_trajectory_messages(messages)
    bld._strip_turn_timestamp_prefix(messages)
    return messages


def _roles(out: dict) -> List[str]:
    return [m["message"]["role"] for m in out["messages"]]


def _texts(out: dict) -> List[str]:
    return [m["message"]["content"][0]["text"] for m in out["messages"]]


def _meta(out: dict) -> dict:
    return out["trajectory"]["meta_info"]


def _willie_entries() -> List[dict]:
    """Two sibling user rows; the model answered the SECOND (bare) one."""
    return [
        _session(),
        _root(),
        _msg("u1", "root", "user", STAMP + "summarise the shop notes"),
        _msg("u2", "root", "user", "summarise the shop notes"),
        _msg("a1", "u2", "assistant", "here is the summary"),
    ]


def test_willie_shape_prunes_unanswered_sibling() -> None:
    out = bld.build_trajectory_from_jsonl(_task(), _willie_entries())

    assert _roles(out) == ["user", "assistant"]
    assert _texts(out) == ["summarise the shop notes", "here is the summary"]
    assert [m["id"] for m in out["messages"]] == ["u2", "a1"]
    assert _meta(out)["pruned_orphans"] == 1
    assert _meta(out)["pruned_orphan_ids"] == ["u1"]
    assert _meta(out)["chain_walk_fallback"] is False


def test_mirror_shape_prunes_the_second_copy_when_first_was_answered() -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", STAMP + "summarise the shop notes"),
        _msg("u2", "root", "user", "summarise the shop notes"),
        _msg("a1", "u1", "assistant", "here is the summary"),
    ]
    out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert [m["id"] for m in out["messages"]] == ["u1", "a1"]
    assert _meta(out)["pruned_orphans"] == 1
    assert _meta(out)["pruned_orphan_ids"] == ["u2"]


def test_clean_linear_session_is_byte_identical_to_the_old_walk() -> None:
    entries = [
        _session(),
        _root(),
        {"type": "thinking_level_change", "id": "tl", "parentId": "root"},
        {"type": "custom", "id": "c1", "parentId": "tl"},
        _msg("u1", "c1", "user", STAMP + "first prompt"),
        _msg("a1", "u1", "assistant", "first answer"),
        {"type": "compaction", "id": "cmp", "parentId": "a1"},
        {"type": "model_change", "id": "mc2", "parentId": "cmp"},
        _msg("u2", "mc2", "user", STAMP + "second prompt"),
        _msg("tr1", "u2", "toolResult", "tool output"),
        _msg("a2", "tr1", "assistant", "second answer"),
    ]
    out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert json.dumps(out["messages"]) == json.dumps(_legacy_messages(entries))
    assert _meta(out)["pruned_orphans"] == 0
    assert _meta(out)["pruned_orphan_ids"] == []
    assert _meta(out)["chain_walk_fallback"] is False


def test_run_ending_on_unanswered_resend_still_selects_the_answered_branch() -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", STAMP + "summarise the shop notes"),
        _msg("a1", "u1", "assistant", "here is the summary"),
        _msg("u2", "root", "user", "summarise the shop notes"),
    ]
    out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert [m["id"] for m in out["messages"]] == ["u1", "a1"]
    assert _meta(out)["pruned_orphan_ids"] == ["u2"]


def test_broken_parent_falls_back_to_file_order_and_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", "first prompt"),
        _msg("a1", "nope-does-not-exist", "assistant", "answer"),
    ]
    with caplog.at_level(logging.ERROR, logger=BUILDER_LOGGER):
        out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert json.dumps(out["messages"]) == json.dumps(_legacy_messages(entries))
    assert _meta(out)["chain_walk_fallback"] is True
    assert _meta(out)["pruned_orphans"] == 0
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_parent_id_cycle_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", "first prompt"),
        {"type": "custom", "id": "m1", "parentId": "m2"},
        {"type": "custom", "id": "m2", "parentId": "m1"},
        _msg("a1", "m1", "assistant", "answer"),
    ]
    with caplog.at_level(logging.ERROR, logger=BUILDER_LOGGER):
        out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert json.dumps(out["messages"]) == json.dumps(_legacy_messages(entries))
    assert _meta(out)["chain_walk_fallback"] is True
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_entry_with_missing_id_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", "first prompt"),
        {"type": "custom", "parentId": "u1"},
        _msg("a1", "u1", "assistant", "answer"),
    ]
    with caplog.at_level(logging.ERROR, logger=BUILDER_LOGGER):
        out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert json.dumps(out["messages"]) == json.dumps(_legacy_messages(entries))
    assert _meta(out)["chain_walk_fallback"] is True
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_turn_feedback_alignment_is_unchanged_by_pruning() -> None:
    """The duplicate_resend branch used to absorb the orphan; with the orphan
    gone the branch stops firing and every surviving message keeps the exact
    feedback it had before."""
    turns = [
        {"prompt": "summarise the shop notes", "hints": "check the frame notes"},
        {"prompt": "now price it", "hints": "use the october sheet"},
    ]
    entries = _willie_entries()

    before = bld._wrap_messages_with_turn_feedback(
        [
            {"type": "message", "id": e["id"], "parentId": "", "timestamp": "",
             "message": e["message"]}
            for e in entries if e.get("type") == "message"
        ],
        turns,
    )
    after = bld._wrap_messages_with_turn_feedback(
        [
            {"type": "message", "id": e["id"], "parentId": "", "timestamp": "",
             "message": e["message"]}
            for e in entries if e.get("type") == "message" and e["id"] != "u1"
        ],
        turns,
    )

    def _feedback_by_id(wrapped: List[dict]) -> dict:
        out = {}
        for w in wrapped:
            inner = w.get("message") if "is_accepted" in w else w
            out[inner["id"]] = (w.get("is_accepted"), w.get("hints"))
        return out

    before_fb = _feedback_by_id(before)
    after_fb = _feedback_by_id(after)
    assert set(after_fb) == {"u2", "a1"}
    for kept_id in after_fb:
        assert after_fb[kept_id] == before_fb[kept_id]


def test_tool_result_messages_on_the_chain_are_kept() -> None:
    entries = [
        _session(),
        _root(),
        _msg("u1", "root", "user", "run the script"),
        _msg("u2", "root", "user", STAMP + "run the script"),
        _msg("tr1", "u1", "toolResult", "exit 0"),
        _msg("a1", "tr1", "assistant", "done"),
    ]
    out = bld.build_trajectory_from_jsonl(_task(), entries)

    assert _roles(out) == ["user", "toolResult", "assistant"]
    assert [m["id"] for m in out["messages"]] == ["u1", "tr1", "a1"]
    assert _meta(out)["pruned_orphan_ids"] == ["u2"]


def test_pruned_orphan_never_reaches_the_published_output() -> None:
    out = bld.build_trajectory_from_jsonl(_task(), _willie_entries())
    published = bld.build_published_trajectory(out, _task(), "success")

    assert [m["message"]["role"] for m in published["messages"]] == [
        "user", "assistant",
    ]
    assert list(published["meta_info"].keys()) == [
        "task_type", "task_description", "task_completion_status",
        "system_prompt", "platform",
    ]
