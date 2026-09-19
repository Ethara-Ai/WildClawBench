"""Published-trajectory hygiene: output.json must not carry per-turn usage/cost
or raw infra-failure noise from tool results (see builder._scrub_published_message).

The rich in-memory trajectory must be left untouched so grading still sees what
the agent actually saw.

Also covers the completion status stamped into meta_info: a lane killed by the
exec approval gate must not publish as `success` (see the approval-gate section
at the bottom).
"""

from src.utils.trajectory.builder import (
    _neutralize_infra_text,
    build_published_trajectory,
    classify_child_completion,
    ends_with_approval_plea,
)

# Canonical inner-message keys per Golden_Trajectory.json.
_CANON_COMMON = {"role", "content"}
_CANON_TOOLRESULT = {"role", "content", "toolCallId", "toolName", "isError"}


class _Task:
    task_type = "research_and_analysis"
    task_description = "desc"
    system_prompt = "sys"


def _rich(*inner_messages):
    return {"messages": [
        {"type": "message", "id": f"id{i}", "parentId": "",
         "timestamp": "2026-06-16T00:00:00Z", "message": m, "turn_index": i}
        for i, m in enumerate(inner_messages)
    ]}


def _tool_result(text):
    return {"role": "toolResult", "toolName": "exec",
            "content": [{"type": "text", "text": text}]}


def test_per_turn_usage_is_dropped():
    rich = _rich(
        {"role": "assistant",
         "content": [{"type": "text", "text": "ok"}],
         "usage": {"input": 10, "output": 5, "cost": {"total": 0.01}}},
    )
    out = build_published_trajectory(rich, _Task(), "success")
    assert "usage" not in out["messages"][0]["message"]
    # rich source untouched -> grading/usage aggregation still works
    assert rich["messages"][0]["message"]["usage"]["cost"]["total"] == 0.01


def test_infra_noise_in_tool_result_is_neutralized():
    noisy = (
        "*   Trying 172.18.0.4:8035...\n"
        "* connect to 172.18.0.4 port 8035 failed: Connection refused\n"
        "* Closing connection 0"
    )
    rich = _rich(_tool_result(noisy))
    out = build_published_trajectory(rich, _Task(), "success")
    txt = out["messages"][0]["message"]["content"][0]["text"]
    assert "Connection refused" not in txt
    assert txt.startswith("[tool output omitted")
    # original tool output preserved in the rich trajectory
    assert "Connection refused" in rich["messages"][0]["message"]["content"][0]["text"]


def test_each_infra_signature_fires():
    cases = [
        "* connect to 172.18.0.4 port 8000 failed: Connection refused",
        "ModuleNotFoundError: No module named 'fitz'\n(Command exited with code 1)",
        "ERROR: Could not find a version that satisfies the requirement openpyxl",
        "Failed to establish a new connection: [Errno -3] Temporary failure in name resolution",
        "sh: 1: pdftotext: not found",
        "< HTTP/1.1 500 Internal Server Error",
        "Internal Server Error",  # bare 500 body, no numeric prefix
        '{"detail":"Not Found"}---{"detail":"Not Found"}---{"detail":"Not Found"}',
        # mixed probe wall with a multi-line ZERO_RESULTS json fragment
        '{"detail":"Not Found"}---\n{\n    "status": "ZERO_RESULTS",\n    "results": []\n}',
    ]
    for text in cases:
        assert _neutralize_infra_text(text) is not None, text


def test_assistant_text_is_never_rewritten():
    # The model's own narration is genuine run content, not infra noise.
    narration = "Honest: Gmail is down right now — 500 Internal Server Error on the inbox."
    rich = _rich({"role": "assistant",
                  "content": [{"type": "text", "text": narration}]})
    out = build_published_trajectory(rich, _Task(), "success")
    assert out["messages"][0]["message"]["content"][0]["text"] == narration


def test_legitimate_tool_output_is_untouched():
    real = "AGENTS.md\nfile_1.pdf\nimg_1.jpg\ntext_1.txt\n"
    assert _neutralize_infra_text(real) is None
    # a real geocoding hit that merely mentions a status must survive
    assert _neutralize_infra_text('{"status":"OK","results":[{"name":"Rockland"}]}') is None


def test_reply_token_stripped_from_assistant_only():
    rich = _rich(
        {"role": "assistant",
         "content": [{"type": "text", "text": "[[reply_to_current]] Here's the picture."}]},
        {"role": "user",
         "content": [{"type": "text", "text": "[[reply_to_current]] verbatim user text"}]},
    )
    out = build_published_trajectory(rich, _Task(), "success")
    assert out["messages"][0]["message"]["content"][0]["text"] == "Here's the picture."
    # user text is never rewritten
    assert "[[reply_to_current]]" in out["messages"][1]["message"]["content"][0]["text"]


def test_only_canonical_inner_keys_remain():
    rich = _rich(
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
         "api": "anthropic-messages", "provider": "anthropic", "model": "claude-opus-4.7",
         "stopReason": "stop", "timestamp": "t", "usage": {"cost": {"total": 1}}},
        {"role": "toolResult", "toolCallId": "tc1", "toolName": "exec", "isError": False,
         "details": {"x": 1}, "timestamp": "t", "content": [{"type": "text", "text": "ls"}]},
    )
    out = build_published_trajectory(rich, _Task(), "success")
    assert set(out["messages"][0]["message"]) == _CANON_COMMON
    assert set(out["messages"][1]["message"]) == _CANON_TOOLRESULT


def test_parent_id_threaded():
    rich = _rich(
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
        {"role": "user", "content": [{"type": "text", "text": "more"}]},
    )
    msgs = build_published_trajectory(rich, _Task(), "success")["messages"]
    assert msgs[0]["parentId"] == ""               # root has no parent
    assert msgs[1]["parentId"] == msgs[0]["id"]     # threaded to previous id
    assert msgs[2]["parentId"] == msgs[1]["id"]
    assert list(msgs[0].keys()) == ["type", "id", "parentId", "timestamp", "message"]


def test_mock_hostname_redacted_in_tool_result():
    text = ("*   Trying 172.18.0.4:8017...\n"
            "* Connected to mocks-task-alden_002_haul_out_week-744148 (172.18.0.4) port 8017\n"
            "> Host: mocks-task-alden_002_haul_out_week-744148:8017\n"
            "{\"ok\": true}")
    rich = _rich({"role": "toolResult", "toolCallId": "t", "toolName": "exec",
                  "isError": False, "content": [{"type": "text", "text": text}]})
    out = build_published_trajectory(rich, _Task(), "success")["messages"][0]
    cleaned = out["message"]["content"][0]["text"]
    assert "mocks-task-alden_002_haul_out_week-744148" not in cleaned
    assert "mock-services" in cleaned
    assert '{"ok": true}' in cleaned  # real payload preserved


# ---------------------------------------------------------------------------
# Approval-gate completion status.
#
# The exec approval gate has no channel to answer in a headless run, so a lane
# it fires on dies with an `/approve <id>` plea as its last word. Sub-agent
# lanes have been derived from that ending since the 2026-07-06 audit; the
# parent lane was still stamped from the run-level verdict alone ("no fatal
# error" => success), publishing a dead run as a clean one. Both lanes now go
# through the one detector (ends_with_approval_plea).
# ---------------------------------------------------------------------------

_PLEA = "/approve exec_01JQ8M4 to run the inline-eval command"


def _assistant(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _status(rich, completion_status="success"):
    return build_published_trajectory(
        rich, _Task(), completion_status,
    )["meta_info"]["task_completion_status"]


def test_parent_ending_on_approval_plea_is_not_success():
    rich = _rich(
        {"role": "user", "content": [{"type": "text", "text": "audit the repo"}]},
        _assistant(_PLEA),
    )
    assert _status(rich) == "blocked_on_approval"


def test_parent_finishing_normally_stays_success():
    rich = _rich(
        {"role": "user", "content": [{"type": "text", "text": "audit the repo"}]},
        _assistant("Audit complete: 3 findings, all filed."),
    )
    assert _status(rich) == "success"


def test_parent_merely_mentioning_approval_stays_success():
    """Prose about the gate is not a plea: the detector is anchored to the
    START of the FINAL turn, so neither a mid-run plea the agent recovered
    from nor a closing report that talks about approval is reclassified."""
    rich = _rich(
        {"role": "user", "content": [{"type": "text", "text": "audit the repo"}]},
        _assistant(_PLEA),  # blocked mid-run...
        {"role": "user", "content": [{"type": "text", "text": "use the sandbox"}]},
        _assistant("Reran it in the sandbox; no /approve was needed. Done."),
    )
    assert _status(rich) == "success"


def test_parent_failure_verdict_is_never_overridden():
    """An explicit non-success verdict from the caller is the stronger signal
    (fatal error); the plea must not downgrade it to blocked_on_approval."""
    rich = _rich(_assistant(_PLEA))
    assert _status(rich, "failure") == "failure"


def test_parent_unset_status_is_classified():
    """The bundle writer publishes with an empty status (no __completion_status__
    on the entry); it must still flag the approval-gate ending."""
    rich = _rich(_assistant(_PLEA))
    assert _status(rich, "") == "blocked_on_approval"
    rich_ok = _rich(_assistant("All set."))
    assert _status(rich_ok, "") == ""


def test_parent_takes_only_the_plea_signal_not_aborted():
    """Parents inherit blocked_on_approval ONLY. The child classifier's
    `aborted` verdicts (trailing toolCall, non-assistant ending, ...) are
    run-shape noise at parent level and must not rewrite the run verdict."""
    trailing_call = _rich(
        _assistant("looking"),
        {"role": "assistant", "content": [{"type": "toolCall", "id": "t1", "name": "exec"}]},
    )
    assert _status(trailing_call) == "success"
    ends_on_user = _rich({"role": "user", "content": [{"type": "text", "text": "hi"}]})
    assert _status(ends_on_user) == "success"


def test_parent_meta_info_key_set_unchanged():
    """meta_info is an exact reference-schema contract: classifying the parent
    must not smuggle an ended_reason key into it."""
    rich = _rich(_assistant(_PLEA))
    out = build_published_trajectory(rich, _Task(), "success")
    assert list(out["meta_info"].keys()) == [
        "task_type", "task_description", "task_completion_status",
        "system_prompt", "platform",
    ]


# --- the shared detector, and the child verdicts it must leave untouched ----


def _msgs(*inner):
    return [{"type": "message", "id": f"m{i}", "message": m}
            for i, m in enumerate(inner)]


def test_detector_matches_only_a_final_leading_plea():
    assert ends_with_approval_plea(_msgs(_assistant(_PLEA)))
    assert ends_with_approval_plea(_msgs(_assistant("\n  " + _PLEA)))
    # not final
    assert not ends_with_approval_plea(_msgs(_assistant(_PLEA), _assistant("done")))
    # not leading
    assert not ends_with_approval_plea(_msgs(_assistant("Please run " + _PLEA)))
    # word-boundary: a path that merely starts with the same prefix
    assert not ends_with_approval_plea(_msgs(_assistant("/approved.md is the log")))
    assert not ends_with_approval_plea([])


def test_child_verdicts_are_unchanged():
    """Precision contract of classify_child_completion, pinned while the plea
    detector is shared with the parent lane."""
    cases = [
        (_msgs(_assistant(_PLEA)), "blocked_on_approval"),
        (_msgs(_assistant(_PLEA), _assistant("report text")), "success"),
        (_msgs(_assistant("report text")), "success"),
        (_msgs({"role": "assistant", "content": "plain string report"}), "success"),
        (_msgs({"role": "assistant", "content": [{"type": "toolCall", "id": "t1"}]}), "aborted"),
        (_msgs({"role": "assistant", "content": []}), "aborted"),
        (_msgs({"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]}), "aborted"),
        (_msgs({"role": "assistant", "content": None}), "aborted"),
        (_msgs({"role": "user", "content": [{"type": "text", "text": "hi"}]}), "aborted"),
        ([], "aborted"),
    ]
    for msgs, expected in cases:
        status, reason = classify_child_completion(msgs)
        assert status == expected, (msgs, status)
        assert reason  # every verdict carries a human-readable reason


def test_child_plea_verdict_beats_a_trailing_tool_call():
    """A plea turn that also carries a toolCall is still blocked_on_approval —
    the gate is the cause, the dangling call is the symptom."""
    msgs = _msgs({"role": "assistant", "content": [
        {"type": "text", "text": _PLEA},
        {"type": "toolCall", "id": "t1", "name": "exec"},
    ]})
    assert classify_child_completion(msgs)[0] == "blocked_on_approval"
