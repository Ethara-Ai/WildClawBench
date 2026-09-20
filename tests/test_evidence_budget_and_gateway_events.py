"""Harness-side stamps for what the judge was never shown and what the gateway
cost the agent (2026-09-19 ariadne_kostas_8c8579bb forensics)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import grading  # noqa: E402
from src.utils.gateway_events import scan_gateway_log  # noqa: E402


def _transcript(turns: int) -> str:
    body = []
    for i in range(1, turns + 1):
        body.append(f"[user turn {i}] ask {i}")
        body.append("x" * 300)
        body.append('[assistant:tool] exec {"command": "ls"}')
        body.append("y" * 300)
    body.append("[FINAL ASSISTANT MESSAGE]")
    body.append("done")
    return "\n".join(body)


def _evidence(transcript: str, budget: int) -> str:
    return "files" + grading._TRANSCRIPT_MARKER + grading._budget_transcript(transcript, budget)


def test_report_names_the_dropped_turns():
    t = _transcript(10)
    rep = grading._evidence_budget_report(t, _evidence(t, 1500))
    assert rep["transcript_truncated"] is True
    assert rep["dropped_lines"] > 0
    assert rep["partial_user_turn"] == 2
    assert rep["dropped_user_turns"] == list(range(3, 11))
    assert rep["transcript_chars_shown"] < rep["transcript_chars"] == len(t)


def test_report_is_quiet_when_nothing_was_cut():
    t = _transcript(3)
    rep = grading._evidence_budget_report(t, _evidence(t, 10_000_000))
    assert rep["transcript_truncated"] is False
    assert "dropped_user_turns" not in rep


def test_echoed_cut_marker_is_not_a_truncation():
    t = "short\n... [truncated 5 lines] ...\nmore"
    rep = grading._evidence_budget_report(t, "f" + grading._TRANSCRIPT_MARKER + t)
    assert rep["transcript_truncated"] is False


def test_report_accepts_a_payload_and_never_raises():
    t = _transcript(10)
    payload = grading.JudgeUserPayload(text=_evidence(t, 1500), images=[])
    assert grading._evidence_budget_report(t, payload)["transcript_truncated"] is True
    assert grading._evidence_budget_report(t, object()) == {}


def test_budget_transcript_still_returns_a_string():
    assert isinstance(grading._budget_transcript(_transcript(10), 1500), str)


_GATEWAY = """\
2026-10-20T07:41:32.657-07:00 [tools] image failed: Image model failed (anthropic/claude-opus-4-6): An unknown error occurred
2026-10-20T07:47:32.520-07:00 [exec] obfuscation detected (gateway): Shell heredoc execution
2026-10-20T07:49:32.551-07:00 [ws] ⇄ res ✓ exec.approval.waitDecision 119984ms conn=7508 id=27f0
2026-10-20T07:49:32.571-07:00 [ws] ⇄ res ✗ agent 9ms errorCode=INVALID_REQUEST errorMessage=Error: Channel is required (no configured channels detected).
2026-10-22T11:37:09.346-07:00 [agent/embedded] using current snapshot: timed out during compaction runId=c1d9
2026-10-22T11:37:09.352-07:00 Done. I saw "[tools] image failed" and "obfuscation detected" earlier.
the [exec] obfuscation detected line above cost two minutes
"""


def test_scan_counts_gateway_lines_not_agent_prose(tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text(_GATEWAY, encoding="utf-8")
    assert scan_gateway_log(log) == {
        "exec_obfuscation_flags": 1,
        "exec_approval_waits": 1,
        "approval_channel_errors": 1,
        "image_tool_failures": 1,
        "compaction_timeouts": 1,
        "exec_approval_wait_ms": 119984,
    }


def test_scan_is_empty_for_clean_or_missing_log(tmp_path):
    clean = tmp_path / "gateway.log"
    clean.write_text("2026-10-20T07:40:35.304-07:00 [gateway] log file: /tmp/x.log\n",
                     encoding="utf-8")
    assert scan_gateway_log(clean) == {}
    assert scan_gateway_log(tmp_path / "absent.log") == {}


# --------------------------------------------------------------------------- #
# Priority shrink: clip tool blocks before any turn is dropped
# --------------------------------------------------------------------------- #
import csv  # noqa: E402
import json  # noqa: E402

from src.utils.state_diff import build_state_changes  # noqa: E402


def _long_run(n_turns: int = 12, body: int = 20_000) -> str:
    lines = []
    for t in range(1, n_turns + 1):
        lines.append(f"[user turn {t}] please do step {t}")
        lines.append("[assistant:tool] write " + json.dumps(
            {"file_path": f"/root/workspace/out{t}.html", "content": "H" * body}))
        lines.append("[toolResult] wrote file\n[S2] a bracketed output line\n" + "z" * 300)
        lines.append(f"[assistant] the figure for step {t} is {t}.42")
    lines[-1] = "[FINAL ASSISTANT MESSAGE] " + lines[-1]
    return "\n".join(lines)


def test_tool_blocks_are_clipped_before_any_turn_is_dropped():
    t = _long_run()
    budget = 60_000
    out = grading._budget_transcript(t, budget)
    assert len(out) <= budget
    assert "[tool calls in the omitted section" not in out      # no middle-drop
    for turn in range(1, 13):
        assert f"[user turn {turn}] please do step {turn}" in out
        assert f"the figure for step {turn} is {turn}.42" in out
    rep = grading._evidence_budget_report(t, "f" + grading._TRANSCRIPT_MARKER + out)
    assert rep["transcript_truncated"] is False
    assert rep["tool_blocks_clipped"] == 12


def test_file_write_bodies_are_clipped_before_tool_outputs():
    lines = ["[user turn 1] go",
             "[assistant:tool] write " + json.dumps({"file_path": "/w/a.html", "content": "H" * 30_000}),
             "[toolResult] " + "R" * 10_000,
             "[FINAL ASSISTANT MESSAGE] [assistant] done"]
    out = grading._budget_transcript("\n".join(lines), 30_000)
    assert "R" * 10_000 in out                 # output untouched
    assert "H" * 30_000 not in out             # write body clipped
    assert out.rstrip().endswith("done")


def test_bracketed_tool_output_lines_do_not_split_a_block():
    block = "[toolResult] head\n[S2] row\n['', ''] row\n" + "z" * 5000
    assert grading._BLOCK_START_RE.match("[toolResult] head")
    assert not grading._BLOCK_START_RE.match("[S2] row")
    assert not grading._BLOCK_START_RE.match("['', ''] row")
    clipped = grading._clip_block(block, 1500)
    assert len(clipped) <= 1500 and clipped.startswith("[toolResult] head")


def test_middle_drop_still_runs_when_clipping_is_not_enough():
    t = _long_run(n_turns=40, body=200)
    out = grading._budget_transcript(t, 3000)
    assert len(out) <= 3000
    assert "... [truncated" in out and out.rstrip().endswith("40.42")


def test_transcript_that_fits_is_returned_untouched():
    t = _long_run(n_turns=2, body=50)
    assert grading._budget_transcript(t, 1_000_000) == t


# --------------------------------------------------------------------------- #
# State diff: rows the agent wrote are shown wide, harness rows stay narrow
# --------------------------------------------------------------------------- #
def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _snapshot(tmp_path, agent_body: str, n_agent_rows: int = 1):
    snap = tmp_path / "snapshot"
    before = snap / "workspace_before" / "mock_data" / "github-api" / "comments.csv"
    after = snap / "workspace_after" / "mock_data" / "github-api" / "comments.csv"
    base = [{"id": "1", "issue_number": "501", "body": "seed"}]
    _write_csv(before, base)
    _write_csv(after, base
               + [{"id": "2", "issue_number": "501", "body": "HARNESS " + "h" * 500}]
               + [{"id": str(10 + i), "issue_number": "502", "body": agent_body}
                  for i in range(n_agent_rows)])
    timeline = tmp_path / "inject_timeline.jsonl"
    timeline.write_text(json.dumps({"type": "inject.api", "service": "github-api",
                                    "table": "comments", "pk": "2",
                                    "after": {"body": "HARNESS " + "h" * 500}}) + "\n",
                        encoding="utf-8")
    return snap, timeline


def test_agent_written_row_is_shown_in_full(tmp_path):
    body = "Corrected cohort figure. " + "x" * 2500 + " Marked VERIFIED."
    snap, tl = _snapshot(tmp_path, body)
    out = build_state_changes(snap, tl)
    agent_line = [ln for ln in out.splitlines() if ln.startswith("  + [agent]")][0]
    assert "Marked VERIFIED." in agent_line
    harness_line = [ln for ln in out.splitlines() if ln.startswith("  + [harness]")][0]
    assert "h" * 100 not in harness_line and "…" in harness_line


def test_agent_rows_narrow_stepwise_to_keep_the_block_inside_max_chars(tmp_path):
    snap, tl = _snapshot(tmp_path, "y" * 3000, n_agent_rows=12)
    out = build_state_changes(snap, tl, max_chars=9000)
    assert len(out) <= 9000
    assert "state diff truncated" not in out          # narrowed, not chopped
    assert sum(1 for ln in out.splitlines() if ln.startswith("  + [agent]")) == 12
