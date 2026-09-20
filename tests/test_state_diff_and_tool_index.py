"""Fix E (mock state diff as judge evidence) and Fix C (tool-call index for the
dropped middle of a budgeted transcript).

E: src/utils/state_diff.py turns snapshot/workspace_{before,after}/mock_data
   into a compact block attributed [agent] / [harness]; grading puts it first
   in the deliverables half so a transcript cut cannot hide what changed.
C: grading._budget_transcript keeps a one-line-per-call index of the tool calls
   it drops, tagged with their user turn, without breaking the <= budget
   invariant.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import grading  # noqa: E402
from src.utils.state_diff import (  # noqa: E402
    STATE_CHANGES_HEADER,
    build_state_changes,
    build_state_changes_for_run,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header: list[str] = []
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run_1"
    before = run / "snapshot" / "workspace_before" / "mock_data"
    after = run / "snapshot" / "workspace_after" / "mock_data"
    _write_csv(before / "github-api" / "issues.csv", [
        {"id": "9100501", "number": "501", "state": "open", "body": "cohort sheet v1"},
        {"id": "9100502", "number": "502", "state": "open", "body": "draft figure"},
    ])
    _write_csv(after / "github-api" / "issues.csv", [
        {"id": "9100501", "number": "501", "state": "open", "body": "cohort sheet v2"},
        {"id": "9100502", "number": "502", "state": "closed", "body": "draft figure"},
    ])
    _write_csv(before / "microsoft-teams-api" / "messages.csv", [
        {"id": "msg-001", "channel_id": "chan-009", "content": "hello"},
    ])
    _write_csv(after / "microsoft-teams-api" / "messages.csv", [
        {"id": "msg-001", "channel_id": "chan-009", "content": "hello"},
        {"id": "msg-047", "channel_id": "chan-009", "content": "baseline correction"},
    ])
    _write_csv(before / "gmail-api" / "messages.csv", [{"id": "m1", "subject": "x"}])
    _write_csv(after / "gmail-api" / "messages.csv", [{"id": "m1", "subject": "x"}])
    (run / "inject_timeline.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "inject.api", "service": "github-api", "table": "issues", "pk": 9100501},
        {"type": "inject.api", "service": "microsoft-teams-api", "table": "messages",
         "pk": "msg-047"},
        {"type": "inject.fs", "id": "fs_x"},
    ]) + "\n", encoding="utf-8")
    return run


# --------------------------------------------------------------------------- #
# E: state diff
# --------------------------------------------------------------------------- #
def test_state_diff_attributes_agent_and_harness_changes(tmp_path):
    out = build_state_changes_for_run(_run_dir(tmp_path))
    assert out.lstrip().startswith(STATE_CHANGES_HEADER)
    # changed rows carry their identity (number) so "#502" is recognisable
    assert "~ [agent] issues 9100502 (number=502): state: 'open' -> 'closed'" in out
    assert "~ [harness] issues 9100501 (number=501)" in out
    assert "+ [harness] messages msg-047" in out
    assert "microsoft-teams-api: 1 change(s), 0 by the agent" in out
    # Covered services are listed so an unchanged covered service is provably
    # untouched, while an uncovered one is explicitly "not observed".
    assert "Covered services (3): github-api, gmail-api, microsoft-teams-api" in out
    assert "gmail-api:" not in out  # covered, unchanged


def test_injected_row_later_edited_by_agent_is_harness_plus_agent(tmp_path):
    # benicio 875c ticket 70408: the harness patched the subject, then the
    # agent wrote the ticket again. It must not be credited to the harness alone.
    run = tmp_path / "run_1"
    _write_csv(run / "snapshot/workspace_before/mock_data/fd-api/tickets.csv", [
        {"id": "70408", "subject": "RG-B", "status": "2", "updated_at": "t0"}])
    _write_csv(run / "snapshot/workspace_after/mock_data/fd-api/tickets.csv", [
        {"id": "70408", "subject": "RG-B / fault confirmed / WITHDRAWN", "status": "2",
         "updated_at": "t9"}])
    (run / "inject_timeline.jsonl").write_text(json.dumps(
        {"type": "inject.api", "service": "fd-api", "table": "tickets", "pk": 70408,
         "ok": True, "after": {"subject": "RG-B / fault confirmed"}}) + "\n",
        encoding="utf-8")
    out = build_state_changes_for_run(run)
    assert "~ [harness+agent] tickets 70408" in out
    assert "fd-api: 1 change(s), 1 by the agent" in out


def test_injection_alone_stays_harness_even_when_the_store_restamps_updated_at(tmp_path):
    run = tmp_path / "run_1"
    _write_csv(run / "snapshot/workspace_before/mock_data/fd-api/tickets.csv", [
        {"id": "1", "status": "2", "updated_at": "t0"}])
    _write_csv(run / "snapshot/workspace_after/mock_data/fd-api/tickets.csv", [
        {"id": "1", "status": "3", "updated_at": "t5"}])
    (run / "inject_timeline.jsonl").write_text(json.dumps(
        {"type": "inject.api", "service": "fd-api", "table": "tickets", "pk": "1",
         "ok": True, "after": {"status": "3"}}) + "\n", encoding="utf-8")
    assert "~ [harness] tickets 1:" in build_state_changes_for_run(run)


def test_table_without_unique_id_uses_a_composite_key(tmp_path):
    # monday column_values: item_id repeats, (item_id, column_id) is unique.
    run = tmp_path / "run_1"
    rows_b = [{"item_id": "i1", "column_id": "status", "text": "Working"},
              {"item_id": "i1", "column_id": "owner", "text": "Ana"}]
    rows_a = [{"item_id": "i1", "column_id": "status", "text": "Done"},
              {"item_id": "i1", "column_id": "owner", "text": "Ana"}]
    _write_csv(run / "snapshot/workspace_before/mock_data/m-api/column_values.csv", rows_b)
    _write_csv(run / "snapshot/workspace_after/mock_data/m-api/column_values.csv", rows_a)
    out = build_state_changes_for_run(run)
    assert "~ [agent] column_values i1/status: text: 'Working' -> 'Done'" in out
    assert "i1/owner" not in out


def test_table_with_no_unique_prefix_is_diffed_by_content(tmp_path):
    run = tmp_path / "run_1"
    same = {"pair": "XBT", "t": "1", "o": "5", "c": "6"}
    _write_csv(run / "snapshot/workspace_before/mock_data/k-api/ohlc.csv", [same, same])
    _write_csv(run / "snapshot/workspace_after/mock_data/k-api/ohlc.csv",
               [same, same, {"pair": "XBT", "t": "1", "o": "5", "c": "7"}])
    out = build_state_changes_for_run(run)
    assert "+ [agent] ohlc row: pair=XBT" in out
    assert out.count("ohlc row") == 1  # the two identical unchanged rows are not reported


def test_state_diff_long_text_shows_where_it_changed(tmp_path):
    run = tmp_path / "run_1"
    long_a = "x" * 200 + " withdrawn Carmel Meadows"
    long_b = "x" * 200 + " active Carmel Meadows"
    _write_csv(run / "snapshot/workspace_before/mock_data/a-api/t.csv", [{"id": "1", "body": long_a}])
    _write_csv(run / "snapshot/workspace_after/mock_data/a-api/t.csv", [{"id": "1", "body": long_b}])
    out = build_state_changes_for_run(run)
    assert "withdrawn Carmel" in out and "active Carmel" in out


def test_state_diff_no_snapshot_is_empty(tmp_path):
    assert build_state_changes_for_run(tmp_path / "nope") == ""


def test_state_diff_no_changes_says_so(tmp_path):
    run = tmp_path / "run_1"
    for side in ("before", "after"):
        _write_csv(run / f"snapshot/workspace_{side}/mock_data/a-api/t.csv", [{"id": "1"}])
    assert "no mock service data changed" in build_state_changes_for_run(run)


def test_state_diff_respects_max_chars(tmp_path):
    run = tmp_path / "run_1"
    _write_csv(run / "snapshot/workspace_before/mock_data/a-api/t.csv", [{"id": "0"}])
    _write_csv(run / "snapshot/workspace_after/mock_data/a-api/t.csv",
               [{"id": str(i), "body": "y" * 80} for i in range(200)])
    out = build_state_changes(run / "snapshot", None, max_chars=1500)
    assert len(out) <= 1500
    assert "truncated to fit" in out or "more change(s)" in out


# --------------------------------------------------------------------------- #
# E: wiring into judge evidence
# --------------------------------------------------------------------------- #
def test_state_block_leads_evidence_and_survives_a_tight_budget(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("R" * 5000, encoding="utf-8")
    state = f"\n{STATE_CHANGES_HEADER}\n~ [agent] issues 502: state 'open' -> 'closed'\n-----\n"
    ev = grading._payload_text(grading._gather_evidence(
        results, "T" * 5000, budget=3000, state_changes=state))
    assert len(ev) <= 3000
    assert ev.startswith(state)
    assert "----- TRANSCRIPT (condensed) -----" in ev


def test_grade_with_rubric_forwards_state_changes(monkeypatch, tmp_path):
    seen = {}

    def fake_gather(*a, **k):
        seen["state"] = k.get("state_changes")
        return grading.JudgeUserPayload(text="ev", images=[])

    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "_gather_evidence", fake_gather)
    monkeypatch.setattr(grading, "council_members", lambda: [
        grading.CouncilMember(family="sonnet", model="bedrock/arn:x/sonnet-x")])
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)
    monkeypatch.setattr(grading, "_grade_council", lambda *a, **k: {
        "overall_score": 1.0, "criteria_total": 1, "criteria_abstained": 0})
    grading.grade_with_rubric([{"criterion": "c", "weight": 1}], "t", tmp_path, "tx",
                              state_changes="STATE")
    assert seen["state"] == "STATE"


def test_judge_prompt_explains_both_blocks():
    text = (_REPO_ROOT / "system_prompts" / "judge_system.md").read_text(encoding="utf-8")
    assert "MOCK STATE CHANGES" in text
    assert "Covered services" in text
    assert "tool calls in the omitted section" in text


# --------------------------------------------------------------------------- #
# C: dropped-section tool-call index
# --------------------------------------------------------------------------- #
def _transcript(n_turns: int = 12, filler: int = 400) -> str:
    lines = []
    for t in range(1, n_turns + 1):
        lines.append(f"[user turn {t}] please do step {t}")
        lines.append('[assistant:tool] exec ' + json.dumps(
            {"command": f"curl -s $GITHUB_API_URL/repos/r/issues/50{t % 3}  # step {t}"}))
        lines.append("[toolResult] " + "z" * filler)
        lines.append(f"[assistant] done step {t}")
    lines[-1] = "[FINAL ASSISTANT MESSAGE] " + lines[-1]
    return "\n".join(lines)


def test_budget_transcript_keeps_an_index_of_dropped_calls():
    t = _transcript()
    out = grading._budget_transcript(t, 2500)
    assert len(out) <= 2500
    assert "... [truncated" in out
    assert "[tool calls in the omitted section" in out
    # the dropped turns' calls are listed with their turn tag
    assert any(ln.startswith("T") and "exec: [apis: GITHUB | paths: /repos/r/issues/50" in ln
               and "curl" in ln for ln in out.splitlines())
    assert out.rstrip().endswith("done step 12")  # final turn intact


def test_budget_transcript_invariant_holds_across_budgets():
    t = _transcript(n_turns=20, filler=900)
    for budget in (300, 800, 1500, 4000, 9000, 20000):
        assert len(grading._budget_transcript(t, budget)) <= budget


def test_budget_transcript_untouched_when_it_fits():
    t = _transcript(n_turns=2, filler=10)
    assert grading._budget_transcript(t, 100_000) == t


def test_tool_index_entries_are_tagged_and_capped():
    lines = ["[user turn 11] go",
             '[assistant:tool] exec ' + json.dumps({"command": "x" * 1000}),
             "[toolResult] ok"]
    entries = grading._tool_call_index_entries(lines)
    assert len(entries) == 1
    pos, entry = entries[0]
    assert pos == 1 and entry.startswith("T11 exec: ")
    assert len(entry) <= grading._TOOL_INDEX_ENTRY_CHARS


def test_render_tool_index_keeps_both_ends_when_tight():
    entries = [f"T{i} exec: call {i}" for i in range(1, 101)]
    out = grading._render_tool_index(entries, 400)
    assert len(out) <= 400
    assert "T1 exec: call 1" in out and "T100 exec: call 100" in out
    assert "more call(s) not listed" in out


# --------------------------------------------------------------------------- #
# C refinement: what a dropped call touched is lifted to the front of its line
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd, expect", [
    ('S=os.environ["SERVICENOW_API_URL"].rstrip("/")\nrecs=get(S+"/api/now/table/incident")',
     "[apis: SERVICENOW | paths: /api/now/table/incident] "),
    ('curl -s -X PATCH "$GITHUB_API_URL/repos/$R/issues/502" -d @/tmp/x.json',
     "[apis: GITHUB | methods: PATCH | paths: /repos/$R/issues/502] "),
    ('F=os.environ["FRESHDESK_API_URL"]; urllib.request.Request(f"{F}/api/v2/tickets/{tid}", method="PUT")',
     "[apis: FRESHDESK | methods: PUT | paths: /api/v2/tickets/{tid}] "),
    ("cd /root/workspace && python3 build.py", ""),
])
def test_tool_call_touches(cmd, expect):
    assert grading._tool_call_touches(" ".join(cmd.split())) == expect


def test_touch_summary_survives_long_boilerplate():
    boiler = "import os,json,urllib.request,re\n" + "def helper(): pass\n" * 40
    cmd = boiler + 'S=os.environ["SERVICENOW_API_URL"]\nget(S+"/api/now/table/incident")'
    lines = ["[user turn 18] go", "[assistant:tool] exec " + json.dumps({"command": cmd})]
    (_, entry), = grading._tool_call_index_entries(lines)
    assert entry.startswith("T18 exec: [apis: SERVICENOW | paths: /api/now/table/incident] ")
    assert len(entry) <= grading._TOOL_INDEX_ENTRY_CHARS


# --------------------------------------------------------------------------- #
# 3a: rendered pages of rubric-named PDFs reach the image-capable judge
# --------------------------------------------------------------------------- #
pymupdf = pytest.importorskip("pymupdf")


def _make_pdf(path: Path) -> None:
    """Page 1: text only. Page 2: text + an embedded image."""
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Summary page, text only")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "EXHIBIT: MCRI-RG-2170 card")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), 0)
    pix.clear_with(200)
    p2.insert_image(pymupdf.Rect(72, 100, 272, 250), pixmap=pix)
    doc.save(str(path))


def test_pdf_page_images_render_only_image_bearing_pages(tmp_path, monkeypatch):
    monkeypatch.delenv("WCB_JUDGE_PDF_MAX_PAGES", raising=False)
    monkeypatch.delenv("WCB_JUDGE_PDF_PAGE_DETAIL", raising=False)
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    pages = grading._pdf_page_images(pdf, "report.pdf")
    assert [n for n, _ in pages] == [2]
    (_, img), = pages
    assert img.label == "report.pdf#page2"
    assert img.mime == "image/jpeg" and img.data_uri.startswith("data:image/jpeg;base64,")
    assert img.detail == "high"


def test_pdf_page_images_can_be_disabled(tmp_path, monkeypatch):
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("WCB_JUDGE_PDF_MAX_PAGES", "0")
    assert grading._pdf_page_images(pdf, "report.pdf") == []


def test_pdf_page_images_degrade_without_pymupdf(tmp_path, monkeypatch):
    import builtins
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    real_import = builtins.__import__

    def no_pdf(name, *a, **k):
        if name in ("pymupdf", "fitz"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pdf)
    assert grading._pdf_page_images(pdf, "report.pdf") == []


def test_named_pdf_pages_attach_only_for_image_judges(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _make_pdf(results / "report.pdf")
    _make_pdf(results / "notes.pdf")
    named = frozenset({"report.pdf"})

    ev = grading._gather_evidence(results, "t", budget=None, rubric_names=named)
    labels = [i.label for i in ev.images]
    assert labels == ["report.pdf#page2"]  # notes.pdf is not rubric-named
    assert "(rendered image of PDF page 2" in ev.text
    assert "EXHIBIT: MCRI-RG-2170 card" in ev.text  # extracted text still there

    text_only = grading._gather_evidence(results, "t", budget=None, rubric_names=named,
                                         attach_images=False)
    assert text_only.images == []
    assert "rendered image of PDF page" not in text_only.text


def test_unreadable_after_snapshot_is_never_blamed_on_the_agent(tmp_path):
    # A failed snapshot read writes an EMPTY table file; a service missing from
    # one side was not observed. Neither may read as "the agent deleted it".
    run = tmp_path / "run_1"
    _write_csv(run / "snapshot/workspace_before/mock_data/fd-api/tickets.csv",
               [{"id": str(i), "subject": "s"} for i in range(5)])
    after = run / "snapshot/workspace_after/mock_data/fd-api/tickets.csv"
    after.parent.mkdir(parents=True)
    after.write_text("", encoding="utf-8")
    _write_csv(run / "snapshot/workspace_before/mock_data/gone-api/t.csv", [{"id": "1"}])
    out = build_state_changes_for_run(run)
    assert not [ln for ln in out.splitlines() if ln.startswith(("  + [agent]", "  ~ [agent]", "  - [agent]"))]
    assert "? tickets: all 5 row(s) are absent" in out
    assert "gone-api" not in out
    assert "fd-api: 1 change(s), 0 by the agent" in out
