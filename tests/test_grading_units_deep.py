"""Deep unit coverage for src/utils/grading.py helpers NOT already covered by
tests/test_judge_sonnet_tiebreak.py (which owns the `_grade_council`
aggregation rule). This file targets the untested lower-level machinery:

  * _extract_weight        — full weight/score/missing/non-numeric/NaN matrix
  * _parse_verdict_text    — valid / truncated / garbage / extra / empty vs _VERDICT_RE
  * _judge_user_prompt     — "[points: w]" rubric-block rendering against the REAL
                             system_prompts/judge_user.md format string
  * _split_evidence        — transcript-marker partition
  * _collect_deliverable_files / _gather_evidence — over real tmp_path artifact trees
  * a handful of _grade_council REWARD edge cases the tiebreak file does not
    exercise: all-negative rubric -> 0.0, mixed dict/string numerator inflation,
    NaN weight -> overall 1.0 (all three PIN CURRENT — possibly-defect — behavior)
  * print_summary / print_global_summary rollup math (capsys, tmp_path)

All tests are offline/deterministic: no docker, no network, no AWS/Bedrock, no
LiteLLM. The council-transport helpers are exercised only via monkeypatched
`grading._run_council` returning synthetic per-member result dicts, so no real
judge call is ever made. Temp data goes to pytest tmp_path only.
"""
from __future__ import annotations

import io
import json
import math
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

# sys.path bootstrap: repo root before "from src..." imports (matches
# tests/test_signed_reward.py / test_docker_env_validation.py convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402


# ---------------------------------------------------------------------------
# _extract_weight — full matrix
# ---------------------------------------------------------------------------


def test_extract_weight_missing_both_defaults_to_one():
    # No 'weight' and no 'score' key -> canonical positive default 1.0.
    assert grading._extract_weight({}) == 1.0
    assert grading._extract_weight({"criterion": "x"}) == 1.0


def test_extract_weight_uses_weight_when_present():
    assert grading._extract_weight({"weight": 5}) == 5.0
    assert grading._extract_weight({"weight": -3}) == -3.0


def test_extract_weight_score_fallback_preserves_sign():
    # kensei2-style rubrics store the (signed) weight under 'score'; the SIGN
    # encodes guardrail polarity and must survive the fallback.
    assert grading._extract_weight({"score": -5}) == -5.0
    assert grading._extract_weight({"score": 3}) == 3.0


def test_extract_weight_weight_takes_precedence_over_score():
    # 'weight' wins when both present (score only consulted if weight is None).
    assert grading._extract_weight({"weight": 2, "score": -9}) == 2.0


def test_extract_weight_explicit_none_weight_falls_through_to_score():
    assert grading._extract_weight({"weight": None, "score": -4}) == -4.0


def test_extract_weight_both_none_defaults_to_one():
    assert grading._extract_weight({"weight": None, "score": None}) == 1.0


def test_extract_weight_numeric_string_is_coerced():
    # "3" -> float 3.0 (str.format handles the display; float() the value).
    assert grading._extract_weight({"weight": "3"}) == 3.0
    assert grading._extract_weight({"score": "-2"}) == -2.0


def test_extract_weight_non_numeric_string_defaults_to_one():
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # A non-numeric weight silently collapses to positive 1.0 rather than
    # raising, so a malformed rubric weight is invisibly treated as a normal
    # positive criterion.
    assert grading._extract_weight({"weight": "abc"}) == 1.0
    assert grading._extract_weight({"weight": []}) == 1.0
    assert grading._extract_weight({"weight": {}}) == 1.0


def test_extract_weight_nan_string_returns_nan_not_default():
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # float("nan") SUCCEEDS, so "nan" is NOT caught by the except and flows
    # through as an actual NaN weight (it is NOT flattened to 1.0). Downstream
    # reward math then propagates the NaN (see the grade_council NaN test).
    w = grading._extract_weight({"weight": "nan"})
    assert math.isnan(w)
    w2 = grading._extract_weight({"weight": float("nan")})
    assert math.isnan(w2)


# ---------------------------------------------------------------------------
# _parse_verdict_text — against the real _VERDICT_RE
# ---------------------------------------------------------------------------


def test_parse_verdict_valid_two_criteria():
    resp = (
        "<judgment>\n"
        "1. Did the thing. [[RATIONALE: yes it did it]] "
        "[[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: No]]\n"
        "2. Did other thing. [[RATIONALE: no it did not]] [[SATISFIED: No]]\n"
        "</judgment>"
    )
    v = grading._parse_verdict_text(resp, 2)
    assert len(v) == 2
    assert v[0] == {
        "rationale": "yes it did it",
        "satisfied": True,
        "truncation_affected": False,
    }
    # Second verdict omits TRUNCATION_AFFECTED -> defaults False (optional group).
    assert v[1]["satisfied"] is False
    assert v[1]["truncation_affected"] is False


def test_parse_verdict_case_and_multiline_rationale():
    # DOTALL + IGNORECASE: rationale spans newlines; lowercase 'yes' accepted.
    resp = (
        "1. crit. [[rationale: line one\nline two]] [[satisfied: yes]] "
        "[[truncation_affected: yes]]"
    )
    v = grading._parse_verdict_text(resp, 1)
    assert len(v) == 1
    assert "line one" in v[0]["rationale"] and "line two" in v[0]["rationale"]
    assert v[0]["satisfied"] is True
    assert v[0]["truncation_affected"] is True


def test_parse_verdict_truncated_returns_partial_list():
    # Smaller-context judge truncates: only 1 verdict emitted for a 3-item
    # rubric. Partial list is returned (NOT padded, NOT raised) so the council
    # aggregator can vote per-covered-index.
    resp = "1. only one. [[RATIONALE: ok]] [[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: Yes]]"
    v = grading._parse_verdict_text(resp, 3)
    assert len(v) == 1
    assert v[0]["truncation_affected"] is True


def test_parse_verdict_extra_verdicts_capped_at_n_criteria():
    # A judge emits stray numbered items beyond the rubric -> capped at n.
    resp = "\n".join(
        f"{i}. c{i}. [[RATIONALE: r]] [[SATISFIED: Yes]]" for i in range(1, 6)
    )
    v = grading._parse_verdict_text(resp, 2)
    assert len(v) == 2


def test_parse_verdict_garbage_raises_valueerror():
    with pytest.raises(ValueError, match="no verdicts parsed"):
        grading._parse_verdict_text("totally unrelated prose, no verdicts here", 3)


def test_parse_verdict_missing_number_anchor_raises():
    # The leading 'N.' anchor is required; without it _VERDICT_RE misses.
    resp = "crit text [[RATIONALE: r]] [[SATISFIED: Yes]]"
    with pytest.raises(ValueError, match="no verdicts parsed"):
        grading._parse_verdict_text(resp, 1)


def test_parse_verdict_empty_response_raises():
    with pytest.raises(ValueError, match="empty judge response"):
        grading._parse_verdict_text("", 2)
    with pytest.raises(ValueError, match="empty judge response"):
        grading._parse_verdict_text(None, 2)  # falsy -> same guard


def test_parse_verdict_satisfied_no_and_absent_truncation_defaults():
    resp = "1. c. [[RATIONALE: reasoning]] [[SATISFIED: No]]"
    v = grading._parse_verdict_text(resp, 1)
    assert v[0]["satisfied"] is False
    assert v[0]["truncation_affected"] is False


# ---------------------------------------------------------------------------
# _split_evidence — transcript marker partition
# ---------------------------------------------------------------------------


def test_split_evidence_with_marker():
    ev = "FILES BLOB\n----- TRANSCRIPT (condensed) -----\nTHE TRANSCRIPT"
    files_part, transcript = grading._split_evidence(ev)
    assert files_part == "FILES BLOB"
    assert transcript == "THE TRANSCRIPT"


def test_split_evidence_without_marker_returns_all_as_files():
    ev = "just files, no transcript marker present"
    files_part, transcript = grading._split_evidence(ev)
    assert files_part == ev
    assert transcript == ""


# ---------------------------------------------------------------------------
# _judge_user_prompt — real prompt file "[points: w]" rendering
# ---------------------------------------------------------------------------


def _user_prompt_text(*args, **kwargs) -> str:
    """Text channel of `_judge_user_prompt`.

    It returns a `JudgeUserPayload` instead of a bare str when the evidence
    carried images lifted out of a deliverable; that seam is covered in
    tests/test_judge_gpt.py. These cases are all text-only.
    """
    return grading._payload_text(grading._judge_user_prompt(*args, **kwargs))


def test_judge_user_prompt_renders_points_and_header():
    rubrics = [
        {"criterion": "crit A", "weight": 3},
        {"criterion": "crit B", "score": -2},   # score fallback, negative
        "a bare string criterion",              # non-dict -> weight 1.0
    ]
    evidence = "SOME FILES\n----- TRANSCRIPT (condensed) -----\nTHE TALK"
    out = _user_prompt_text("accomplish the task", rubrics, evidence)

    # Rubric block: one numbered "[points: w]" line per criterion, in order.
    assert "1. crit A  [points: 3.0]" in out
    assert "2. crit B  [points: -2.0]" in out
    assert "3. a bare string criterion  [points: 1.0]" in out
    # n_criteria threaded into the RUBRIC header and closing instruction.
    assert "RUBRIC (3 criteria" in out
    assert "exactly 3 verdicts" in out
    # task_description + split transcript/files land in their slots.
    assert "accomplish the task" in out
    assert "THE TALK" in out
    assert "SOME FILES" in out


def test_judge_user_prompt_empty_evidence_uses_placeholders():
    out = _user_prompt_text("t", [{"criterion": "c", "weight": 1}], "")
    assert "(no transcript captured)" in out
    assert "(no deliverable files were collected)" in out


def test_judge_user_prompt_tags_only_file_targets():
    rubrics = [
        {"criterion": "wrote report.pdf", "weight": 5, "evaluation_target": "workspace_artifact"},
        {"criterion": "wrote data.csv", "weight": 3, "evaluation_target": "produced_file"},
        {"criterion": "answer states X", "weight": 3, "evaluation_target": "final_answer"},
        {"criterion": "called the API", "weight": 1, "evaluation_target": "trajectory"},
        {"criterion": "changed the record", "weight": 1, "evaluation_target": "state_change"},
        {"criterion": "messaged the user", "weight": 1, "evaluation_target": "user_facing_message"},
        {"criterion": "no target key", "weight": 1},
    ]
    out = _user_prompt_text("t", rubrics, "")
    # File targets (canonical + alias) carry the normalized tag.
    assert "1. wrote report.pdf  [points: 5.0]  [target: workspace_artifact]" in out
    assert "2. wrote data.csv  [points: 3.0]  [target: workspace_artifact]" in out
    # The four calibrated targets + a missing target render with NO tag.
    assert "3. answer states X  [points: 3.0]" in out
    assert "4. called the API  [points: 1.0]" in out
    assert "5. changed the record  [points: 1.0]" in out
    assert "6. messaged the user  [points: 1.0]" in out
    assert "7. no target key  [points: 1.0]" in out
    # Exactly two tags emitted (the two file-target criteria).
    assert out.count("[target:") == 2


# ---------------------------------------------------------------------------
# _collect_deliverable_files / _gather_evidence — real tmp_path trees
# ---------------------------------------------------------------------------


def _evidence_text(*args, **kwargs) -> str:
    """Text channel of `_gather_evidence`'s `JudgeUserPayload`.

    The payload also carries images lifted out of the deliverable text (see
    tests/test_judge_gpt.py); everything below asserts on the text channel only.
    """
    return grading._gather_evidence(*args, **kwargs).text


def _make_results_tree(tmp_path: Path) -> Path:
    """Build a workspace where results_path.name == 'results' so the sibling
    sweep (workspace_root = parent.parent) fires. Returns the results/ path.

    Layout:
      <root>/task_output/artifacts/results/report.md           (text, primary)
      <root>/task_output/artifacts/results/notes.pdf           (binary presence)
      <root>/task_output/artifacts/results/pic.png             (image deliverable)
      <root>/task_output/workspace_full/output/flagged.csv     (named-dir sweep)
      <root>/task_output/workspace_full/top.txt                (root-level glob)
    """
    task_output = tmp_path / "task_output"
    results = task_output / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("the report body", encoding="utf-8")
    (results / "notes.pdf").write_bytes(b"%PDF-1.4 secret binary bytes")
    (results / "pic.png").write_bytes(b"\x89PNG image bytes")
    wf = task_output / "workspace_full"
    (wf / "output").mkdir(parents=True)
    (wf / "output" / "flagged.csv").write_text("a,b,c", encoding="utf-8")
    (wf / "top.txt").write_text("top level deliverable", encoding="utf-8")
    return results


def test_collect_deliverable_files_text_binary_and_sibling_sweep(tmp_path):
    results = _make_results_tree(tmp_path)
    files = grading._collect_deliverable_files(results)
    names = sorted(f.name for f in files)
    # report.md (text) + notes.pdf (binary presence) + pic.png (image) from
    # results/, flagged.csv from workspace_full/output/, top.txt from wf root.
    assert names == ["flagged.csv", "notes.pdf", "pic.png", "report.md", "top.txt"]


def test_collect_deliverable_files_empty_dir_returns_empty(tmp_path):
    results = tmp_path / "task_output" / "results"
    results.mkdir(parents=True)
    assert grading._collect_deliverable_files(results) == []


def test_collect_deliverable_files_missing_dir_returns_empty(tmp_path):
    # Non-existent results path: _add_from short-circuits on not is_dir().
    assert grading._collect_deliverable_files(tmp_path / "nope" / "results") == []


def test_collect_does_not_byte_cap_documents_or_images(tmp_path):
    # The raw-byte cap is for root-scan TEXT only: documents and images of any
    # size are collected in the recursive sweep AND the root scan.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    big = b"x" * (grading._ROOT_SCAN_MAX_FILE_BYTES + 10)
    (results / "huge.pdf").write_bytes(big)
    (results / "huge.png").write_bytes(big)
    (results / "small.md").write_text("ok", encoding="utf-8")
    wf = tmp_path / "task_output" / "workspace_full"
    wf.mkdir()
    (wf / "root.docx").write_bytes(big)
    (wf / "root.jpg").write_bytes(big)
    triples = grading._collect_deliverables_with_status(results)
    assert sorted(f.name for f, _, _ in triples) == [
        "huge.pdf", "huge.png", "root.docx", "root.jpg", "small.md",
    ]
    assert all(reason is None for _, _, reason in triples)


def test_root_scan_oversized_text_is_disclosed_not_dropped(tmp_path):
    artifacts = tmp_path / "task_output" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "keep.md").write_text("KEEP", encoding="utf-8")
    wf = tmp_path / "task_output" / "workspace_full"
    wf.mkdir()
    size = grading._ROOT_SCAN_MAX_FILE_BYTES + 1
    (wf / "dump.csv").write_bytes(b"SECRETROW," * (size // 10 + 1))
    # Exactly at the cap is still included in full.
    (wf / "edge.txt").write_bytes(b"e" * grading._ROOT_SCAN_MAX_FILE_BYTES)
    status = {label: reason for _, label, reason in
              grading._collect_deliverables_with_status(artifacts)}
    assert status["dump.csv"] == grading._REASON_ROOT_TEXT_CAP
    assert status["edge.txt"] is None
    ev = _evidence_text(artifacts, "", budget=None)
    assert "----- DELIVERABLE: dump.csv\n(" in ev
    assert "present — contents not included: text file exceeds the 100000-byte root-scan size cap" in ev
    assert "SECRETROW" not in ev
    assert "----- DELIVERABLE: edge.txt -----" in ev


def _make_run_tree(tmp_path: Path) -> Path:
    """Real collected-run shape: artifacts/ (baseline-diff copy) and
    workspace_full/ (full-tree copy) hold the SAME agent files at different
    paths, next to persona scaffolding and harness-written files. Returns the
    artifacts/ dir (what _pick_evidence_dir hands to grading)."""
    task_output = tmp_path / "task_output"
    artifacts = task_output / "artifacts"
    wf = task_output / "workspace_full"
    for root in (artifacts, wf):
        (root / "output").mkdir(parents=True)
        (root / "report.md").write_text("REPORT BODY", encoding="utf-8")
        (root / "output" / "rows.csv").write_text("a,b\n1,2", encoding="utf-8")
        # Agent-modified persona file: the baseline diff copies it to artifacts/.
        (root / "MEMORY.md").write_text("agent wrote this memory", encoding="utf-8")
    # Unmodified persona scaffolding exists only in the full-tree copy.
    (wf / "SOUL.md").write_text("persona soul", encoding="utf-8")
    (wf / "AGENTS.md").write_text("persona agents", encoding="utf-8")
    # results/ is excluded from the baseline diff -> only under workspace_full/.
    (wf / "results").mkdir()
    (wf / "results" / "final.md").write_text("FINAL", encoding="utf-8")
    # Harness-written files at the task_output/ top level.
    (task_output / "openclaw-2026-10-04.log").write_text("gateway log", encoding="utf-8")
    (task_output / "artifacts_excluded.json").write_text("[]", encoding="utf-8")
    return artifacts


def test_collect_dedups_artifacts_vs_workspace_full_copies(tmp_path):
    artifacts = _make_run_tree(tmp_path)
    pairs = grading._collect_deliverables(artifacts)
    labels = sorted(label for _, label in pairs)
    # One copy of each agent file (the artifacts/ one wins: it is walked first),
    # results/ recovered from workspace_full/, nothing doubled.
    assert labels == ["MEMORY.md", "output/rows.csv", "report.md", "results/final.md"]
    by_label = {label: f for f, label in pairs}
    assert by_label["report.md"].parent == artifacts
    assert by_label["output/rows.csv"].parent == artifacts / "output"


def test_collect_excludes_persona_scaffold_and_harness_files(tmp_path):
    artifacts = _make_run_tree(tmp_path)
    names = {f.name for f in grading._collect_deliverable_files(artifacts)}
    assert not names & {"SOUL.md", "AGENTS.md", "openclaw-2026-10-04.log", "artifacts_excluded.json"}
    # A persona file the agent MODIFIED still reaches the judge via artifacts/.
    assert "MEMORY.md" in names


def test_collect_keeps_same_bytes_under_different_name_and_same_name_different_bytes(tmp_path):
    artifacts = tmp_path / "task_output" / "artifacts"
    (artifacts / "v2").mkdir(parents=True)
    (artifacts / "check.md").write_text("identical", encoding="utf-8")
    (artifacts / "final.md").write_text("identical", encoding="utf-8")
    (artifacts / "v2" / "final.md").write_text("different body", encoding="utf-8")
    # The workspace_full copy of final.md diverged from the artifacts/ one.
    wf = tmp_path / "task_output" / "workspace_full"
    wf.mkdir()
    (wf / "final.md").write_text("diverged", encoding="utf-8")
    labels = sorted(label for _, label in grading._collect_deliverables(artifacts))
    # Dedup needs label AND content to match: nothing here is a true repeat.
    assert labels == ["check.md", "final.md", "final.md", "v2/final.md"]


def test_gather_evidence_header_carries_relative_path_not_host_path(tmp_path):
    artifacts = _make_run_tree(tmp_path)
    ev = _evidence_text(artifacts, "T", budget=None)
    # Top-level header is unchanged (bare name); nested files show their folder.
    assert "----- DELIVERABLE: report.md -----" in ev
    assert "----- DELIVERABLE: output/rows.csv -----" in ev
    assert "----- DELIVERABLE: results/final.md -----" in ev
    assert ev.count("REPORT BODY") == 1
    assert "persona soul" not in ev and "gateway log" not in ev
    assert str(tmp_path) not in ev



def _real_png_uri(seed: int) -> str:
    """A distinct, decodable 12x12 PNG per seed. The old `QUFB`*20 stand-ins were
    not images at all, and an undecodable blob is now refused on purpose (one bad
    image part fails the whole judge request)."""
    import base64
    import io
    from PIL import Image
    img = Image.new("RGB", (12, 12), (seed * 37 % 256, seed * 91 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _kb(uri: str) -> str:
    return f"{len(uri.partition(',')[2]) * 3 / 4 / 1024:.1f}"


def test_gather_evidence_same_basename_files_get_distinct_image_labels(tmp_path):
    artifacts = tmp_path / "task_output" / "artifacts"
    (artifacts / "output").mkdir(parents=True)
    uri_a = _real_png_uri(1)
    uri_b = _real_png_uri(2)
    (artifacts / "page.html").write_text(f"<img src='{uri_a}'>", encoding="utf-8")
    (artifacts / "output" / "page.html").write_text(f"<img src='{uri_b}'>", encoding="utf-8")
    payload = grading._gather_evidence(artifacts, "T", budget=None)
    assert sorted(i.label for i in payload.images) == ["output/page.html#1", "page.html#1"]


def test_gather_evidence_omission_manifest_names_relative_path(tmp_path):
    artifacts = tmp_path / "task_output" / "artifacts"
    (artifacts / "output").mkdir(parents=True)
    (artifacts / "a.md").write_text("small", encoding="utf-8")
    (artifacts / "output" / "big.md").write_text("x" * 9000, encoding="utf-8")
    (artifacts / "output" / "bigger.md").write_text("y" * 12000, encoding="utf-8")
    ev = _evidence_text(artifacts, "TRANSCRIPT " * 50, budget=6000)
    note = ev[ev.index("EVIDENCE BUDGET NOTE"):]
    assert "output/big.md (partial)" in note
    assert "output/bigger.md" in note


def test_gather_evidence_orders_primary_first_and_binary_is_presence_only(tmp_path):
    # Ordering: a 'report' stem sorts ahead of a larger, non-primary csv.
    # Binary polarity: a collected .pdf appears as a presence-only marker
    # ("contents not extractable"), NOT its raw bytes — the mojibake-poisoning
    # hazard is closed. The judge_system.md file-target legend maps this marker
    # to No + TRUNCATION_AFFECTED for binary content criteria.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "zdata.csv").write_text("x,y,z," * 200, encoding="utf-8")
    (results / "report.md").write_text("PRIMARY REPORT", encoding="utf-8")
    (results / "notes.pdf").write_bytes(b"%PDF secret-body-marker")

    ev = _evidence_text(results, "MY TRANSCRIPT", budget=None)
    # Primary 'report' sorts ahead of the larger, non-primary csv.
    assert ev.index("report.md") < ev.index("zdata.csv")
    # PDF is listed for presence but its raw bytes are NOT dumped.
    assert "DELIVERABLE: notes.pdf" in ev
    assert "contents not extractable" in ev
    assert "secret-body-marker" not in ev
    # Transcript appended under the condensed marker.
    assert "MY TRANSCRIPT" in ev
    assert "TRANSCRIPT (condensed)" in ev


def test_gather_evidence_budget_truncates(tmp_path):
    # Boundary-aware budget (Issue 5): deliverables are bounded by the budget,
    # but the transcript marker + content are NEVER sliced away — the marker
    # must always survive so _split_evidence never silently returns "".
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("A" * 5000, encoding="utf-8")
    ev = _evidence_text(results, "the transcript body", budget=2100)
    # Deliverable portion is bounded; transcript marker + body preserved.
    assert "----- TRANSCRIPT (condensed) -----" in ev
    assert "the transcript body" in ev
    files_part, transcript = grading._split_evidence(ev)
    assert transcript == "the transcript body"


def test_gather_evidence_tiny_budget_preserves_transcript_marker(tmp_path):
    # Hard-clamp contract (OAuth 200K gate #18): total evidence is ALWAYS
    # <= budget. At a pathologically small budget the marker survives and the
    # transcript tail is non-empty (END of the final turn, so the judge never
    # grades against "(no transcript captured)") — but never unbounded overshoot.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("A" * 5000, encoding="utf-8")
    ev = _evidence_text(results, "final answer here", budget=50)
    assert len(ev) <= 50
    assert grading._TRANSCRIPT_MARKER.strip() in ev
    _files, transcript = grading._split_evidence(ev)
    assert transcript  # non-empty: keeps the end of the final turn
    # At a realistic budget the entire final turn survives whole.
    ev2 = _evidence_text(results, "final answer here", budget=6000)
    _f2, transcript2 = grading._split_evidence(ev2)
    assert "final answer here" in transcript2


def test_gather_evidence_no_deliverables_placeholder(tmp_path):
    results = tmp_path / "task_output" / "results"
    results.mkdir(parents=True)
    ev = _evidence_text(results, "", budget=None)
    assert "no deliverable files were collected under any of" in ev
    # Every recognised deliverable-dir name appears in the placeholder.
    for name in grading._DELIVERABLE_DIR_NAMES:
        assert f"{name}/" in ev


def test_gather_evidence_split_roundtrips_through_split_evidence(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("BODY", encoding="utf-8")
    ev = _evidence_text(results, "THE TRANSCRIPT", budget=None)
    files_part, transcript = grading._split_evidence(ev)
    assert transcript == "THE TRANSCRIPT"
    assert "TRANSCRIPT (condensed)" not in files_part
    assert "BODY" in files_part


def test_budget_transcript_middle_drop_emits_marker_and_keeps_final_turn():
    body = "\n".join(f"[user] msg {i} " + "w" * 100 for i in range(100))
    transcript = body + "\n[FINAL ASSISTANT MESSAGE] [assistant] done"
    out = grading._budget_transcript(transcript, 3000)
    assert "... [truncated" in out and "lines] ..." in out
    assert out.rstrip().endswith("done")
    assert len(out) <= 3000


def test_budget_transcript_spurious_early_landmark_stays_bounded():
    filler = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    decoy = "[FINAL ASSISTANT MESSAGE] echoed by an agent tool result"
    real_final = "[FINAL ASSISTANT MESSAGE] [assistant] the real final answer"
    transcript = filler + "\n" + decoy + "\n" + filler + "\n" + real_final
    out = grading._budget_transcript(transcript, 5000)
    assert len(out) <= 5000
    assert "the real final answer" in out


def test_budget_transcript_landmark_at_line_zero_stays_bounded():
    transcript = (
        "[FINAL ASSISTANT MESSAGE] final-para-1\nfinal-para-2\nfinal-para-3\n"
        + "z" * 5000
    )
    out = grading._budget_transcript(transcript, 200)
    assert len(out) <= 200


def test_budget_transcript_huge_final_turn_clamped_to_budget():
    # Final [SUBMIT TOOL OUTPUT] turn alone dwarfs the budget: must clamp to the
    # END of that turn, never return the whole transcript (OAuth 200K gate #18).
    head = "\n".join(f"[user] q{i} " + "a" * 50 for i in range(50))
    big_final = "[SUBMIT TOOL OUTPUT] [toolResult] " + "Z" * 400_000 + " END_OF_OUTPUT"
    transcript = head + "\n" + big_final
    out = grading._budget_transcript(transcript, 175_000)
    assert len(out) <= 175_000
    assert out.rstrip().endswith("END_OF_OUTPUT")


def test_budget_transcript_zero_or_negative_budget_returns_empty():
    assert grading._budget_transcript("anything\nhere", 0) == ""
    assert grading._budget_transcript("anything\nhere", -5) == ""


def test_gather_evidence_never_exceeds_effective_budget(tmp_path):
    # Assembled evidence must NEVER exceed the member budget (OAuth 200K gate #18),
    # even when both deliverables and transcript are individually huge.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("R" * 500_000, encoding="utf-8")
    transcript = "\n".join(f"[user] t{i} " + "x" * 200 for i in range(3000))
    transcript += "\n[FINAL ASSISTANT MESSAGE] [assistant] final"
    ev = _evidence_text(results, transcript, budget=300_000)
    assert len(ev) <= 300_000


def _png_bytes(w, h):
    import struct
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return sig + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr


def test_image_deliverable_is_collected_and_gets_dimension_marker(tmp_path):
    # Issue 2: .png must be collected (not silently dropped) and surfaced with
    # stdlib-parsed dimensions (no Pillow).
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "chart.png").write_bytes(_png_bytes(640, 480))
    collected = grading._collect_deliverable_files(results)
    assert any(p.name == "chart.png" for p in collected)
    ev = _evidence_text(results, "", budget=None)
    assert "chart.png" in ev
    assert "image 640x480" in ev


def _docx_bytes(tmp_path, body, name="summary.docx"):
    import zipfile
    p = tmp_path / name
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = (f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>'
           f'<w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", doc)
    return p


def test_docx_deliverable_stdlib_text_extraction(tmp_path):
    # Issue 3: .docx content is extracted via stdlib zipfile+xml (no python-docx).
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _docx_bytes(results, "QUARTERLY_REVENUE_4200")
    ev = _evidence_text(results, "", budget=None)
    assert "QUARTERLY_REVENUE_4200" in ev
    assert "extracted text" in ev


def test_docx_corrupt_degrades_to_presence_marker(tmp_path):
    # Issue 3: a corrupt .docx must never raise; it degrades to presence-only.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "broken.docx").write_bytes(b"not a zip")
    assert grading._extract_text_deliverable(results / "broken.docx") is None
    ev = _evidence_text(results, "", budget=None)
    assert "broken.docx" in ev
    assert "contents not extractable" in ev


def test_pdf_guarded_extraction_degrades_when_pypdf_absent(tmp_path):
    # Issue 4: guarded optional pypdf. When absent (default), extraction returns
    # None and the deliverable degrades to a presence marker (never raises).
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "invoice.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    try:
        import pypdf  # noqa: F401
        pytest.skip("pypdf is installed; guarded-degrade branch not exercised")
    except ImportError:
        pass
    assert grading._extract_text_deliverable(results / "invoice.pdf") is None
    ev = _evidence_text(results, "", budget=None)
    assert "invoice.pdf" in ev
    assert "contents not extractable" in ev


def _xlsx_bytes(tmp_path, name="report.xlsx"):
    # Real-writer shape (openpyxl/pandas): string cells reference sharedStrings by
    # INDEX (<c t="s"><v>i</v>), numeric cells store the number bare (<c><v>N</v>),
    # inline strings use <c t="inlineStr"><is><t>. Numbers NEVER go in sharedStrings.
    import zipfile
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{ns}"><si><t>Revenue</t></si></sst>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>'
            '<row><c t="s"><v>0</v></c><c><v>42000</v></c>'
            '<c t="inlineStr"><is><t>InlineCell</t></is></c></row>'
            "</sheetData></worksheet>",
        )
    return p


def _pptx_bytes(tmp_path, texts, name="deck.pptx"):
    import zipfile
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = tmp_path / name
    body = "".join(f"<a:t>{t}</a:t>" for t in texts)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ppt/slides/slide1.xml", f'<?xml version="1.0"?><sld xmlns:a="{a}">{body}</sld>')
    return p


def test_xlsx_deliverable_stdlib_text_extraction(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _xlsx_bytes(results)
    ev = _evidence_text(results, "", budget=None)
    assert "Revenue" in ev and "42000" in ev and "InlineCell" in ev
    assert "extracted text" in ev


def test_pptx_deliverable_stdlib_text_extraction(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _pptx_bytes(results, ["Q3 Launch Plan", "Budget"])
    ev = _evidence_text(results, "", budget=None)
    assert "Q3 Launch Plan" in ev and "Budget" in ev
    assert "extracted text" in ev


def test_xlsx_corrupt_and_empty_degrade_without_raising(tmp_path):
    import zipfile
    (tmp_path / "bad.xlsx").write_bytes(b"not a zip")
    assert grading._extract_text_deliverable(tmp_path / "bad.xlsx") is None
    empty = tmp_path / "empty.xlsx"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("xl/other.xml", "<x/>")
    assert grading._extract_text_deliverable(empty) is None


def test_synthetic_abstain_judges_match_member_families():
    class _M:
        def __init__(self, fam):
            self.family = fam
    members = [_M("sonnet"), _M("kimi"), _M("glm")]
    crit = grading._synthetic_abstain_criterion(0, {"criterion": "x", "weight": 5}, members)
    assert crit["judges"] == ["sonnet", "kimi", "glm"]
    assert len(crit["judges"]) == len(crit["satisfied_by_judge"]) == len(members)


# ---------------------------------------------------------------------------
# Issue 1: rubric batching (_merge_batched_grades, threshold gate)
# ---------------------------------------------------------------------------


def test_rubric_batch_size_env_guard(monkeypatch):
    monkeypatch.delenv("WCB_JUDGE_RUBRIC_BATCH_SIZE", raising=False)
    assert grading._rubric_batch_size() == grading._DEFAULT_RUBRIC_BATCH_SIZE
    monkeypatch.setenv("WCB_JUDGE_RUBRIC_BATCH_SIZE", "notanint")
    assert grading._rubric_batch_size() == grading._DEFAULT_RUBRIC_BATCH_SIZE
    monkeypatch.setenv("WCB_JUDGE_RUBRIC_BATCH_SIZE", "0")
    assert grading._rubric_batch_size() == grading._DEFAULT_RUBRIC_BATCH_SIZE
    monkeypatch.setenv("WCB_JUDGE_RUBRIC_BATCH_SIZE", "7")
    assert grading._rubric_batch_size() == 7


def _fake_council_result(rubrics, satisfied_ids=()):
    # Build a _grade_council-shaped dict for a chunk: ids are POSITIONAL (0..n-1),
    # matching the real aggregator. satisfied_ids are the positional indices that
    # passed (positive-weight satisfied).
    crit, abst = [], []
    weighted = 0.0
    passed = 0
    for i, r in enumerate(rubrics):
        wt = r["weight"] if isinstance(r, dict) else 1.0
        sat = i in satisfied_ids
        crit.append({"id": i, "weight": wt, "satisfied": sat,
                     "passed": sat, "resolved_by": "unanimous", "is_positive": wt >= 0})
        if sat:
            weighted += wt
            passed += 1
    total_w = sum(r["weight"] for r in rubrics if isinstance(r, dict) and r["weight"] > 0) or 1.0
    return {
        "overall_score": round(weighted / total_w, 4),
        "rubric_weights_percentage": round(weighted / total_w * 100, 2),
        "criteria_total": len(rubrics), "criteria_passed": passed,
        "criteria_failed": len(rubrics) - passed, "criteria_abstained": 0,
        "criteria": crit, "judge_model": "council",
        "judge_council": {"members": ["sonnet"], "surviving": ["sonnet"], "failed": [],
                          "aggregation": "unanimous_or_sonnet_tiebreak",
                          "per_member_user_chars": {"sonnet": 1000},
                          "per_member_verdict_count": {"sonnet": len(rubrics)}},
        "truncation_flags": [], "abstention_flags": abst,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0,
                  "cache_write_tokens": 0, "total_tokens": 15, "request_count": 1,
                  "cost_usd": 0.01, "per_member": {"sonnet": {
                      "model": "sonnet-x", "input_tokens": 10, "output_tokens": 5,
                      "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": 15,
                      "request_count": 1, "cost_usd": 0.01, "ok": True}}},
    }


class _M:
    def __init__(self, family):
        self.family = family
        self.model = f"{family}-x"


def test_batching_merge_remaps_ids_and_recomputes_denominator_once():
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(100)]
    members = [_M("sonnet")]
    chunks = [rubrics[0:40], rubrics[40:80], rubrics[80:100]]
    # Each chunk passes all its criteria (positional ids within the chunk).
    chunk_results = [(ch, _fake_council_result(ch, satisfied_ids=range(len(ch)))) for ch in chunks]
    merged = grading._merge_batched_grades(rubrics, members, chunk_results)
    assert merged["criteria_total"] == 100
    assert merged["criteria_passed"] == 100
    assert merged["criteria_abstained"] == 0
    ids = [c["id"] for c in merged["criteria"]]
    assert ids == list(range(100))  # global remap, each exactly once
    assert merged["overall_score"] == 1.0
    assert merged["judge_council"]["per_member_verdict_count"]["sonnet"] == 100


def test_batching_one_chunk_failure_degrades_to_abstain_not_zero():
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(80)]
    members = [_M("sonnet")]
    good = (rubrics[0:40], _fake_council_result(rubrics[0:40], satisfied_ids=range(40)))
    bad = (rubrics[40:80], {"overall_score": 0.0, "error": "chunk council failed"})
    merged = grading._merge_batched_grades(rubrics, members, [good, bad])
    assert merged["criteria_total"] == 80
    assert merged["criteria_passed"] == 40
    assert merged["criteria_abstained"] == 40
    assert set(merged["abstention_flags"]) == set(range(40, 80))
    assert "error" not in merged  # partial failure must NOT set top-level error
    assert merged["overall_score"] == 0.5  # 40 passed / 80 positive weight


def test_batching_all_chunks_fail_sets_error():
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(20)]
    members = [_M("sonnet")]
    fail = {"overall_score": 0.0, "error": "boom"}
    merged = grading._merge_batched_grades(
        rubrics, members, [(rubrics[0:10], fail), (rubrics[10:20], fail)])
    assert merged.get("error") == "all rubric batches failed to grade"
    assert merged["criteria_abstained"] == 20


# ---------------------------------------------------------------------------
# _grade_council reward EDGE cases (NOT covered by test_judge_sonnet_tiebreak)
# ---------------------------------------------------------------------------

_SONNET = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/sonnet-x"
_GLM = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/glm-x"
_KIMI = "bedrock/arn:aws:bedrock:us-east-1:1:application-inference-profile/kimi-x"


def _members():
    return [
        grading.CouncilMember(family="sonnet", model=_SONNET),
        grading.CouncilMember(family="glm", model=_GLM),
        grading.CouncilMember(family="kimi", model=_KIMI),
    ]


def _verdicts(*sats):
    return [
        {"satisfied": s, "rationale": "r", "truncation_affected": False}
        for s in sats
    ]


def _ok(model, family, verds):
    return {
        "model": model,
        "effective_model": model,
        "family": family,
        "ok": True,
        "verdicts": verds,
        "usage": dict(grading._ZERO_USAGE),
        "user_chars": 10,
    }


def _grade(monkeypatch, rubrics, member_results):
    monkeypatch.setattr(
        grading, "_run_council",
        lambda members, system, user, n: list(member_results),
    )
    return grading._grade_council(rubrics, "sys", "user", _members())


def test_all_negative_rubric_guardrails_held_scores_zero(monkeypatch):
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # Rubric of ONLY guardrail (negative-weight) criteria. Denominator is the
    # sum of POSITIVE weights = 0 -> falls back to 1.0. When all guardrails
    # held (satisfied=No -> passed), the numerator stays 0, so overall is 0.0
    # even though the agent did everything right. criteria_passed is still 1.
    rubrics = [{"criterion": "forbidden thing", "weight": -3}]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", _verdicts(False)),
        _ok(_GLM, "glm", _verdicts(False)),
        _ok(_KIMI, "kimi", _verdicts(False)),
    ])
    assert out["overall_score"] == 0.0
    assert out["criteria_passed"] == 1
    assert out["criteria_failed"] == 0
    assert out["criteria"][0]["passed"] is True
    assert out["criteria"][0]["is_positive"] is False


def test_all_negative_rubric_guardrail_breached_scores_zero(monkeypatch):
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # Same all-negative rubric but forbidden behavior OCCURRED (satisfied=Yes
    # unanimously). Numerator subtracts |weight| -> -3; the formula is
    # deliberately UNCLAMPED (negative-weight violation checkers must pull the
    # reward below zero), so overall is -3/1.0 = -3.0.
    rubrics = [{"criterion": "forbidden thing", "weight": -3}]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", _verdicts(True)),
        _ok(_GLM, "glm", _verdicts(True)),
        _ok(_KIMI, "kimi", _verdicts(True)),
    ])
    assert out["overall_score"] == -3.0
    assert out["criteria"][0]["passed"] is False


def test_mixed_dict_and_string_rubric_inflates_numerator(monkeypatch):
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # A bare-string rubric entry is treated as a positive weight-1.0 criterion
    # in the NUMERATOR (its satisfied verdict adds +1.0 to `weighted`) but a
    # string is NOT a dict, so it is EXCLUDED from `total_w` (denominator only
    # sums dict positive weights). Numerator 2+1=3 over denominator 2 = 1.5
    # (no clamp). The string entry silently inflates the score.
    rubrics = [{"criterion": "c0", "weight": 2}, "just a string criterion"]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", _verdicts(True, True)),
        _ok(_GLM, "glm", _verdicts(True, True)),
        _ok(_KIMI, "kimi", _verdicts(True, True)),
    ])
    # Denominator 2; numerator 3 -> unclamped 1.5.
    assert out["overall_score"] == 1.5
    # String criterion rendered as positive weight 1.0.
    assert out["criteria"][1]["weight"] == 1.0
    assert out["criteria"][1]["is_positive"] is True
    assert out["criteria_passed"] == 2


def test_nan_weight_propagates_to_overall_one(monkeypatch):
    # NOTE: pins current behavior — see SCORING_AUDIT_REPORT.md
    # A NaN weight (e.g. rubric weight literally "nan") is excluded from total_w
    # because `nan > 0` is False, so total_w falls back to 1.0. `weighted`
    # becomes NaN (0 + nan) and the unclamped nan/1.0 stays NaN — the poison
    # value now PROPAGATES (previously the clamp silently converted it to a
    # perfect 1.0).
    rubrics = [{"criterion": "c", "weight": "nan"}]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", _verdicts(True)),
        _ok(_GLM, "glm", _verdicts(True)),
        _ok(_KIMI, "kimi", _verdicts(True)),
    ])
    assert math.isnan(out["overall_score"])
    assert math.isnan(out["criteria"][0]["weight"])


def test_grade_council_empty_rubrics_via_grade_with_rubric():
    # grade_with_rubric short-circuits on empty rubrics without touching the
    # council (no members, no pricing) — the fast structured-error path.
    out = grading.grade_with_rubric([], "task", Path("/nonexistent"))
    assert out == {"overall_score": 0.0, "error": "no rubric criteria"}


def test_grade_council_zero_survivors_all_abstain(monkeypatch):
    # Every member failed the call -> zero survivors, no Sonnet verdict, every
    # criterion abstains, overall 0.0. Confirms the total-council-failure path.
    failed = {
        "model": _SONNET, "effective_model": _SONNET, "family": "sonnet",
        "ok": False, "error": "call: boom",
        "usage": dict(grading._ZERO_USAGE), "user_chars": 10,
    }
    rubrics = [{"criterion": "c", "weight": 5}]
    monkeypatch.setattr(
        grading, "_run_council", lambda *a, **k: [dict(failed)],
    )
    out = grading._grade_council(
        rubrics, "sys", "user",
        [grading.CouncilMember(family="sonnet", model=_SONNET)],
    )
    assert out["overall_score"] == 0.0
    assert out["criteria_abstained"] == 1
    assert out["abstention_flags"] == [0]
    assert out["criteria"][0]["resolved_by"] == "human_eval"
    # No verdict from anyone: the 0.0 is the no-signal sentinel, not a grade.
    assert "judge council cast no verdicts" in out["error"]


# ---------------------------------------------------------------------------
# deliverable predicates (direct)
# ---------------------------------------------------------------------------


def test_deliverable_predicates_direct():
    assert grading._is_text_deliverable(Path("report.md")) is True
    assert grading._is_text_deliverable(Path("data.CSV")) is True   # case-insensitive
    assert grading._is_text_deliverable(Path("report.pdf")) is False
    assert grading._is_binary_deliverable(Path("report.pdf")) is True
    assert grading._is_binary_deliverable(Path("report.md")) is False


def test_looks_like_deliverable_wrong_ext_and_oversize(tmp_path):
    good = tmp_path / "a.md"
    good.write_text("hi", encoding="utf-8")
    assert grading._looks_like_deliverable(good, tmp_path) is True
    # Image extension is now supported (Issue 2) -> True.
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    assert grading._looks_like_deliverable(img, tmp_path) is True
    # Genuinely-unsupported extension -> False regardless of size.
    bad = tmp_path / "a.bin"
    bad.write_bytes(b"\x00\x01\x02")
    assert grading._looks_like_deliverable(bad, tmp_path) is False
    # Supported extension but oversized -> False (size gate).
    big = tmp_path / "b.csv"
    big.write_bytes(b"z" * (grading._ROOT_SCAN_MAX_FILE_BYTES + 1))
    assert grading._looks_like_deliverable(big, tmp_path) is False


def test_gather_evidence_discloses_unreadable_file(tmp_path, monkeypatch):
    # A deliverable whose read_text raises is not fatal and not silently
    # dropped: it gets a presence marker, and the readable one survives.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("GOOD BODY", encoding="utf-8")
    (results / "broken.md").write_text("bad", encoding="utf-8")

    real_read_text = Path.read_text

    def _boom(self, *a, **k):
        if self.name == "broken.md":
            raise OSError("simulated read failure")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _boom)
    ev = _evidence_text(results, "", budget=None)
    assert "GOOD BODY" in ev
    assert "----- DELIVERABLE: broken.md\n(" in ev
    assert "present — contents not included: file could not be read" in ev


# ---------------------------------------------------------------------------
# _grade_council — truncation flags + short-verdict partial abstain
# ---------------------------------------------------------------------------


def test_grade_council_truncation_flag_recorded(monkeypatch):
    # A member reporting truncation_affected=True on a criterion registers that
    # index in truncation_flags (independent of the verdict resolution).
    rubrics = [{"criterion": "c0", "weight": 1}]
    trunc_verd = [{"satisfied": True, "rationale": "r", "truncation_affected": True}]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", trunc_verd),
        _ok(_GLM, "glm", _verdicts(True)),
        _ok(_KIMI, "kimi", _verdicts(True)),
    ])
    assert out["truncation_flags"] == [0]
    # Verdict still resolves unanimous (truncation flag is orthogonal).
    assert out["criteria"][0]["resolved_by"] == "unanimous"


def test_grade_council_short_verdict_list_abstains_that_index(monkeypatch):
    # Kimi returns only 1 verdict for a 2-criterion rubric: index 1 is beyond
    # its list -> that member shows "Abstain" with the truncated-before-index
    # rationale, and the criterion resolves via Sonnet (not unanimous).
    rubrics = [{"criterion": "c0", "weight": 1}, {"criterion": "c1", "weight": 1}]
    short = [{"satisfied": True, "rationale": "r", "truncation_affected": False}]
    out = _grade(monkeypatch, rubrics, [
        _ok(_SONNET, "sonnet", _verdicts(True, True)),
        _ok(_GLM, "glm", _verdicts(True, True)),
        _ok(_KIMI, "kimi", short),
    ])
    c1 = out["criteria"][1]
    assert c1["votes"] == "Yes/Yes/Abstain"
    assert c1["resolved_by"] == "sonnet"
    assert c1["voted_by_judge"] == [True, True, False]
    assert "truncated before this criterion" in c1["rationales_by_judge"][2]


# ---------------------------------------------------------------------------
# format_scores
# ---------------------------------------------------------------------------


def test_format_scores_pure_error_returns_error_line():
    s = grading.format_scores("t1", {"error": "no rubric criteria"})
    assert s == "[t1] Grading error: no rubric criteria"


def test_format_scores_numeric_renders_bars():
    s = grading.format_scores("t1", {"overall_score": 0.5, "criteria_passed": 3})
    assert "t1" in s
    assert "0.50" in s
    assert "overall_score" in s
    assert "█" in s  # progress bar rendered for numeric values


# ---------------------------------------------------------------------------
# print_summary — rollup math + JSON side file (capsys / tmp_path)
# ---------------------------------------------------------------------------


def test_print_summary_rollup_and_json(tmp_path):
    results = [
        {
            "task_id": "t1",
            "scores": {"overall_score": 0.8, "criteria_passed": 4, "criteria_total": 5},
            "usage": {"output_tokens": 100, "cost_usd": 0.5},
        },
        {  # empty scores + outer agent error -> "No valid numeric scores" branch
            "task_id": "t2",
            "scores": {},
            "error": "agent boom",
            "usage": {"output_tokens": 0, "cost_usd": 0.0},
        },
        {  # scores dict has only an 'error' key -> "Grading error" branch
            "task_id": "t3",
            "scores": {"error": "grading boom"},
            "usage": {},
        },
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_summary(results, "catX", tmp_path, "modelZ")
    out = buf.getvalue()

    assert "Summary Report — catX" in out
    assert "t1: avg" in out           # numeric task summarized
    assert "Grading error grading boom" in out   # t3 error branch
    assert "0.80" in out              # t1 overall_score bar line
    # Token/cost table totals: only t1 contributes 100 tokens / $0.5.
    assert "100" in out
    # Side-car summary JSON written under <output_dir>/<category>/.
    summary_path = tmp_path / "catX" / "summary_modelZ.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text())
    assert len(data) == 3


def test_print_summary_agent_error_note_when_numeric_present(tmp_path):
    # A task WITH numeric scores AND an outer agent error prints the "!" status
    # plus an ` agent_error=` note (distinct from the empty-scores path).
    results = [{
        "task_id": "ta",
        "scores": {"overall_score": 0.5},
        "error": "partial fail",
        "usage": {"output_tokens": 10, "cost_usd": 0.1},
    }]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_summary(results, "c", tmp_path, "m")
    assert "agent_error=partial fail" in buf.getvalue()


def test_print_summary_grading_error_note_when_numeric_present(tmp_path):
    # numeric score present AND scores.error present -> ` grading_error=` note.
    results = [{
        "task_id": "tb",
        "scores": {"overall_score": 0.5, "error": "grade partial"},
        "usage": {},
    }]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_summary(results, "c", tmp_path, "m")
    assert "grading_error=grade partial" in buf.getvalue()


def test_print_summary_no_scores_note(tmp_path):
    # scores falsy AND no outer error -> "No scores" line, no crash.
    results = [{"task_id": "tc", "scores": None, "usage": {}}]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_summary(results, "c", tmp_path, "m")
    assert "No scores" in buf.getvalue()


# ---------------------------------------------------------------------------
# print_global_summary — global average math + JSON side file
# ---------------------------------------------------------------------------


def test_print_global_summary_average_divides_by_total_tasks(tmp_path):
    # global_avg = sum(overall_score of scored) / TOTAL tasks (missing count in
    # the denominator). One scored task at 0.8 over 3 total -> 0.266667.
    results = [
        {"task_id": "t1", "scores": {"overall_score": 0.8}, "usage": {"output_tokens": 100, "cost_usd": 0.5}},
        {"task_id": "t2", "scores": {}, "usage": {"output_tokens": 0, "cost_usd": 0.0}},
        {"task_id": "t3", "scores": {"error": "boom"}, "usage": {}},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_global_summary(results, tmp_path, "modelZ")
    out = buf.getvalue()
    assert "Global Summary Report" in out
    assert "Completed tasks: 1 / 3" in out
    assert "Tasks without a valid score.json: 2" in out

    gp = tmp_path / "summary_all_modelZ.json"
    gd = json.loads(gp.read_text())
    assert gd["scored_task_count"] == 1
    assert gd["missing_score_task_count"] == 2
    assert gd["task_count"] == 3
    assert gd["global_avg"] == pytest.approx(0.8 / 3)


def test_print_global_summary_avg_of_numeric_when_no_overall_score(tmp_path):
    # When a task's scores have no 'overall_score', the fallback is the mean of
    # its numeric values. {a:0.4,b:0.6} -> 0.5, single task -> global 0.5.
    results = [
        {"task_id": "t1", "scores": {"a": 0.4, "b": 0.6}, "usage": {}},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_global_summary(results, tmp_path, "m")
    gd = json.loads((tmp_path / "summary_all_m.json").read_text())
    assert gd["global_avg"] == pytest.approx(0.5)


def test_print_global_summary_empty_results_no_tasks(tmp_path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        grading.print_global_summary([], tmp_path, "m")
    out = buf.getvalue()
    assert "No tasks found" in out
    gd = json.loads((tmp_path / "summary_all_m.json").read_text())
    assert gd["global_avg"] is None
    assert gd["task_count"] == 0


# ---------------------------------------------------------------------------
# rubric-aware evidence ranking + omission manifest (ajax_moreno 2026-09-05)
# ---------------------------------------------------------------------------


def test_rubric_file_names_extracts_basenames():
    rubrics = [
        {"criterion": "The response delivers winter_creative_clearance.pdf recording X."},
        {"criterion": "winter_creative_board.html embeds the proof frame"},
        "a plain-string criterion mentioning notes.md here",
        {"criterion": "no file mentioned at all"},
    ]
    names = grading._rubric_file_names(rubrics)
    assert "winter_creative_clearance.pdf" in names
    assert "winter_creative_board.html" in names
    assert "notes.md" in names
    assert len(names) == 3


def test_gather_evidence_rubric_named_file_ranks_first(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "clearance_notes.md").write_text("DECISIVE" * 150, encoding="utf-8")
    for i in range(5):
        (results / f"aaa{i}.md").write_text("filler" * 50, encoding="utf-8")

    named = frozenset({"clearance_notes.md"})
    ev = _evidence_text(results, "tail", budget=None, rubric_names=named)
    assert ev.index("clearance_notes.md") < ev.index("aaa0.md")

    ev2 = _evidence_text(results, "tail", budget=None)
    assert ev2.index("aaa0.md") < ev2.index("clearance_notes.md")


def test_gather_evidence_scratch_subdirs_demoted(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    (results / "extract").mkdir(parents=True)
    (results / "extract" / "dump0.txt").write_text("x", encoding="utf-8")
    (results / "final.md").write_text("REAL DELIVERABLE" * 300, encoding="utf-8")

    ev = _evidence_text(results, "tail", budget=None)
    assert ev.index("final.md") < ev.index("dump0.txt")


def test_gather_evidence_scratch_check_ignores_host_path_components(tmp_path):
    build_like = tmp_path / "build" / "task_output" / "artifacts" / "results"
    build_like.mkdir(parents=True)
    (build_like / "real.md").write_text("CONTENT", encoding="utf-8")
    files = grading._collect_deliverable_files(build_like)
    assert files and not grading._in_scratch_subdir(files[0])


def test_gather_evidence_omission_manifest_names_cut_files(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "keep.md").write_text("K" * 1000, encoding="utf-8")
    (results / "cut_one.md").write_text("C" * 5000, encoding="utf-8")
    (results / "cut_two.md").write_text("D" * 5000, encoding="utf-8")

    ev = _evidence_text(results, "T" * 100, budget=2500)
    assert len(ev) <= 2500
    assert "K" * 100 in ev
    assert "EVIDENCE BUDGET NOTE" in ev
    assert "cut_one.md (partial)" in ev
    assert "cut_two.md" in ev
    assert "... [truncated for evidence budget] ..." in ev
    _files, transcript = grading._split_evidence(ev)
    assert transcript == "T" * 100


def test_gather_evidence_no_manifest_when_everything_fits(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "small.md").write_text("tiny", encoding="utf-8")
    ev = _evidence_text(results, "the transcript", budget=100_000)
    assert "EVIDENCE BUDGET NOTE" not in ev
    assert "tiny" in ev


# ---------------------------------------------------------------------------
# presence disclosure: a produced file is never silently dropped
# ---------------------------------------------------------------------------


def _artifacts_root(tmp_path: Path) -> Path:
    root = tmp_path / "task_output" / "artifacts"
    root.mkdir(parents=True)
    return root


def _padded_docx(path: Path, body: str, pad_bytes: int = 0) -> None:
    """A .docx whose extracted text is *body*, padded with incompressible bytes
    so its raw size can exceed the root-scan byte cap."""
    import os
    import zipfile
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = (f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>'
           f'<w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        if pad_bytes:
            z.writestr("word/media/pad.bin", os.urandom(pad_bytes))


def test_presence_marker_exact_format(tmp_path):
    f = tmp_path / "x.csv"
    f.write_bytes(b"12345")
    assert grading._presence_marker("out/x.csv", f, "some reason") == (
        "\n----- DELIVERABLE: out/x.csv\n"
        "(5 bytes, present — contents not included: some reason)\n"
        "-----\n"
    )
    assert "size unknown" in grading._presence_marker("gone.csv", tmp_path / "gone.csv", "r")


def test_document_over_root_byte_cap_is_extracted_when_text_fits(tmp_path):
    root = _artifacts_root(tmp_path)
    _padded_docx(root / "big.docx", "DOC_BODY_VALUE_42",
                 pad_bytes=grading._ROOT_SCAN_MAX_FILE_BYTES + 50_000)
    assert (root / "big.docx").stat().st_size > grading._ROOT_SCAN_MAX_FILE_BYTES
    ev = _evidence_text(root, "", budget=None)
    assert "----- DELIVERABLE: big.docx (extracted text) -----" in ev
    assert "DOC_BODY_VALUE_42" in ev


def test_document_over_extraction_cap_is_truncated_and_disclosed(tmp_path):
    root = _artifacts_root(tmp_path)
    cap = grading._EXTRACT_CHAR_CAP
    body = "A" * cap + "TAIL_BEYOND_CAP"
    _padded_docx(root / "long.docx", body)
    ev = _evidence_text(root, "", budget=None)
    assert "DELIVERABLE: long.docx (extracted text, truncated: first 150000 of" in ev
    assert f"of {len(body)} chars included" in ev
    assert "present — remaining contents not included" in ev
    assert "TAIL_BEYOND_CAP" not in ev
    assert "A" * cap in ev
    # The capped helper keeps its contract.
    assert grading._extract_text_deliverable(root / "long.docx") == "A" * cap


def test_unextractable_document_is_a_presence_marker(tmp_path):
    root = _artifacts_root(tmp_path)
    (root / "broken.xlsx").write_bytes(b"not a zip at all")
    ev = _evidence_text(root, "", budget=None)
    assert "----- DELIVERABLE: broken.xlsx\n(16 bytes, present — contents not included: contents not extractable" in ev


def test_large_standalone_image_is_collected_with_presence_marker(tmp_path):
    root = _artifacts_root(tmp_path)
    data = _png_bytes(1920, 1080) + b"\x00" * (grading._ROOT_SCAN_MAX_FILE_BYTES * 3)
    (root / "photo.png").write_bytes(data)
    ev = _evidence_text(root, "", budget=None)
    assert f"----- DELIVERABLE: photo.png\n({len(data)} bytes, present — contents not included:" in ev
    assert "image 1920x1080" in ev


def test_every_budget_dropped_file_gets_a_presence_marker(tmp_path):
    root = _artifacts_root(tmp_path)
    (root / "report.md").write_text("R" * 1500, encoding="utf-8")
    for i in range(6):
        (root / f"data{i}.md").write_text(f"D{i}" * 2000, encoding="utf-8")
    transcript = "[user] hi\n[FINAL ASSISTANT MESSAGE] [assistant] done"
    budget = 5000
    ev = _evidence_text(root, transcript, budget=budget)
    assert len(ev) <= budget
    files_part, t = grading._split_evidence(ev)
    assert "done" in t
    # report.md fits in full; every data file is kept partially or disclosed.
    assert "R" * 1500 in files_part
    note = files_part[files_part.index("EVIDENCE BUDGET NOTE"):]
    for i in range(6):
        label = f"data{i}.md"
        partial = f"{label} (partial)" in note
        marker = f"----- DELIVERABLE: {label}\n(" in files_part
        assert partial or marker, label
        assert label in note
    assert files_part.count("present — contents not included: evidence budget exceeded") >= 5
    # The note is never clipped mid-list.
    assert note.split("\n", 1)[0].endswith(" -----")


def test_count_only_note_when_markers_cannot_all_fit(tmp_path):
    root = _artifacts_root(tmp_path)
    for i in range(60):
        (root / f"file_with_a_fairly_long_name_{i:03d}.md").write_text("x" * 500, encoding="utf-8")
    budget = 2500
    ev = _evidence_text(root, "T", budget=budget)
    assert len(ev) <= budget
    files_part, t = grading._split_evidence(ev)
    assert t == "T"
    assert "EVIDENCE BUDGET NOTE: 60 collected file(s) present but contents not included for budget" in files_part
    listed = files_part.count("present — contents not included: evidence budget exceeded")
    assert listed > 0
    assert f"{60 - listed} of them not listed by name for budget" in files_part


def test_budget_disclosure_also_applies_without_transcript(tmp_path):
    root = _artifacts_root(tmp_path)
    (root / "a.md").write_text("A" * 3000, encoding="utf-8")
    (root / "b.md").write_text("B" * 3000, encoding="utf-8")
    ev = _evidence_text(root, "", budget=2000)
    assert len(ev) <= 2000
    assert "a.md" in ev and "b.md" in ev
    assert "EVIDENCE BUDGET NOTE" in ev


@pytest.mark.parametrize("budget", [500, 700, 1500, 4000, 9000, 30000])
def test_evidence_never_exceeds_budget_and_discloses_all_files(tmp_path, budget):
    root = _artifacts_root(tmp_path)
    (root / "sub").mkdir()
    sizes = [50, 900, 2500, 7000, 120, 15000]
    for i, n in enumerate(sizes):
        (root / ("sub" if i % 2 else ".") / f"f{i}.md").write_text("z" * n, encoding="utf-8")
    (root / "doc.docx").write_bytes(b"corrupt")
    transcript = "\n".join(f"[user] line {i}" for i in range(400)) + "\n[FINAL ASSISTANT MESSAGE] end"
    ev = _evidence_text(root, transcript, budget=budget)
    assert len(ev) <= budget
    files_part, _ = grading._split_evidence(ev)
    labels = ["f0.md", "sub/f1.md", "f2.md", "sub/f3.md", "f4.md", "sub/f5.md", "doc.docx"]
    if "not listed by name for budget" in files_part:
        return  # count-only mode: the count note is the disclosure
    for label in labels:
        assert label in files_part, (budget, label)


def test_inline_image_over_count_limit_is_disclosed_and_not_attached(tmp_path, monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "1")
    root = _artifacts_root(tmp_path)
    uri_a = _real_png_uri(1)
    uri_b = _real_png_uri(2)
    (root / "page.html").write_text(f"<img src='{uri_a}'><img src='{uri_b}'>", encoding="utf-8")
    payload = grading._gather_evidence(root, "T", budget=None)
    assert [i.label for i in payload.images] == ["page.html#1"]
    assert f"[inline image page.html#2, image/png, ~{_kb(uri_b)}KB; present — contents not included: judge image count limit reached (1 per request)]" in payload.text
    assert f"[inline image page.html#1, image/png, ~{_kb(uri_a)}KB]" in payload.text


def test_text_only_member_marks_every_inline_image_not_attached(tmp_path):
    root = _artifacts_root(tmp_path)
    uri = _real_png_uri(1)
    (root / "page.html").write_text(f"<img src='{uri}'>", encoding="utf-8")
    payload = grading._gather_evidence(root, "T", budget=50_000, attach_images=False)
    assert payload.images == []
    assert "; present — contents not included: this judge does not receive image attachments]" in payload.text
    assert len(payload.text) <= 50_000


def test_image_limit_disclosure_counts_against_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "0")
    root = _artifacts_root(tmp_path)
    uri = _real_png_uri(1)
    (root / "page.html").write_text("".join(f"<img src='{uri}'>" for _ in range(40)), encoding="utf-8")
    for budget in (800, 2500, 6000):
        payload = grading._gather_evidence(root, "T", budget=budget)
        assert len(payload.text) <= budget
        assert payload.images == []
        assert "page.html" in payload.text


def test_same_label_files_get_distinct_names_and_independent_image_decisions(tmp_path, monkeypatch):
    # Collection keeps two DIFFERENT files that share a label (artifacts/ copy vs
    # a diverged workspace_full/ copy). Each needs its own evidence name so image
    # attach/disclose decisions for one can never apply to the other.
    monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "1")
    artifacts = _artifacts_root(tmp_path)
    wf = tmp_path / "task_output" / "workspace_full"
    wf.mkdir()
    uri_a = _real_png_uri(1)
    uri_b = _real_png_uri(2)
    (artifacts / "page.html").write_text(f"A <img src='{uri_a}'>", encoding="utf-8")
    (wf / "page.html").write_text(f"B-diverged <img src='{uri_b}'>", encoding="utf-8")
    payload = grading._gather_evidence(artifacts, "T", budget=None)
    text = payload.text
    assert "----- DELIVERABLE: page.html -----" in text
    assert "----- DELIVERABLE: page.html [2] -----" in text
    assert [i.label for i in payload.images] == ["page.html#1"]
    assert payload.images[0].data_uri == uri_a
    assert f"[inline image page.html#1, image/png, ~{_kb(uri_a)}KB]" in text
    assert f"[inline image page.html [2]#1, image/png, ~{_kb(uri_b)}KB; present — contents not included: judge image count limit reached (1 per request)]" in text


def test_presence_wording_matches_judge_prompt_contract():
    # The judge prompt keys the abstain rule on this exact phrase; every
    # disclosure the harness emits must use it.
    from src.utils.prompt_loader import load_prompt
    phrase = "present — contents not included"
    assert phrase in load_prompt("judge_system")
    assert phrase in grading._presence_marker("x.md", Path("/nonexistent/x.md"), "r")
    assert phrase in grading._disclose_unattached_images(
        "[inline image a.html#1, image/png, ~1.0KB]", {"a.html#1": "r"}
    )


def test_evidence_order_uses_rendered_size_not_disk_size(tmp_path):
    # A document that is huge on disk but small once extracted, and an image
    # that contributes a one-line marker, must not sort behind bulkier text.
    root = _artifacts_root(tmp_path)
    _padded_docx(root / "summary.docx", "DOC_VALUE_7", pad_bytes=300_000)
    (root / "photo.png").write_bytes(_png_bytes(800, 600) + b"\0" * 400_000)
    (root / "notes.md").write_text("N" * 20_000, encoding="utf-8")
    ev = _evidence_text(root, "", budget=None)
    assert ev.index("DELIVERABLE: summary.docx") < ev.index("DELIVERABLE: notes.md")
    assert ev.index("DELIVERABLE: photo.png") < ev.index("DELIVERABLE: notes.md")
    # Under a budget that fits the small blocks but not the bulky text, the
    # document's extracted text survives in full.
    tight = _evidence_text(root, "", budget=4_000)
    assert "DOC_VALUE_7" in tight
    assert "image 800x600" in tight
    assert "N" * 20_000 not in tight


def test_duplicate_label_numbering_follows_collection_order_not_size(tmp_path):
    # The evidence-dir copy keeps the plain name even when it is the larger one.
    artifacts = _artifacts_root(tmp_path)
    wf = tmp_path / "task_output" / "workspace_full"
    wf.mkdir()
    (artifacts / "final.md").write_text("ARTIFACT COPY " * 200, encoding="utf-8")
    (wf / "final.md").write_text("older", encoding="utf-8")
    ev = _evidence_text(artifacts, "", budget=None)
    plain_hdr = "----- DELIVERABLE: final.md -----\n"
    numbered_hdr = "----- DELIVERABLE: final.md [2] -----\n"
    assert ev[ev.index(plain_hdr) + len(plain_hdr):].startswith("ARTIFACT COPY")
    assert ev[ev.index(numbered_hdr) + len(numbered_hdr):].startswith("older")


# --- ported from neo-version bd8afc26 (phase 1: code/svg/ipynb evidence) ---
def test_code_and_svg_deliverables_included_verbatim(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "build_hero.py").write_text("CREDIT = 'fig: N.F. / 10-04'", encoding="utf-8")
    (results / "chart.svg").write_text("<svg><text>Re-cut minutes</text></svg>", encoding="utf-8")
    (results / "deploy.sh").write_text("echo deploying", encoding="utf-8")
    (results / "app.js").write_text("console.log('boot')", encoding="utf-8")
    ev = grading._payload_text(grading._gather_evidence(results, "t", budget=None))
    assert "fig: N.F. / 10-04" in ev
    assert "Re-cut minutes" in ev
    assert "echo deploying" in ev
    assert "console.log('boot')" in ev


def test_ipynb_extraction_keeps_source_and_text_drops_base64(tmp_path):
    import json as _json
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["x = compute_reward()\n"],
             "outputs": [
                 {"output_type": "stream", "text": ["reward=0.42\n"]},
                 {"output_type": "display_data",
                  "data": {"image/png": "iVBORw0KGgoAAAANSUhEU" * 500,
                           "text/plain": ["<Figure 640x480>"]}},
             ]},
            {"cell_type": "markdown", "source": ["## Analysis section"], "outputs": []},
        ]
    }
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "analysis.ipynb").write_text(_json.dumps(nb), encoding="utf-8")
    out = grading._extract_text_deliverable(results / "analysis.ipynb")
    assert "x = compute_reward()" in out
    assert "reward=0.42" in out
    assert "## Analysis section" in out
    assert "<Figure 640x480>" in out
    assert "iVBORw0KGgo" not in out




# --- ported from neo-version 1c4babd9 / 21a7633c (judge chunk retries) ---
def _fake_grade_success(n):
    return {
        "overall_score": 1.0, "rubric_weights_percentage": 100.0,
        "criteria_total": n, "criteria_passed": n, "criteria_failed": 0,
        "criteria_abstained": 0, "criteria": [
            {"id": i, "weight": 1.0, "satisfied": True, "passed": True,
             "resolved_by": "unanimous", "human_eval": "", "voters": 1,
             "criterion": f"c{i}", "votes": "Yes", "satisfied_by_judge": [True],
             "voted_by_judge": [True], "rationales_by_judge": ["r"],
             "truncation_affected_by_judge": [False], "judges": ["sonnet"],
             "is_positive": True} for i in range(n)],
        "judge_model": "council", "judge_council": {"members": ["m"], "surviving": ["m"], "failed": [], "per_member_verdict_count": {"sonnet": n}},
        "truncation_flags": [], "abstention_flags": [], "usage": dict(grading._ZERO_USAGE),
    }


def test_parse_truncation_retries_same_size_once(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "bedrock/arn:aws:bedrock:x:1:application-inference-profile/s1")
    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda members: None)
    calls = []

    def fake_council(chunk, system, user_for_member, members, images=None):
        calls.append(len(chunk))
        if len(calls) == 1:
            return {"overall_score": 0.0, "error": "parse: expected up to 6 verdicts, matched 2", "usage": dict(grading._ZERO_USAGE)}
        return _fake_grade_success(len(chunk))

    monkeypatch.setattr(grading, "_grade_council", fake_council)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(6)]
    ws = tmp_path / "task_output" / "results"; ws.mkdir(parents=True)
    out = grading.grade_with_rubric(rubrics, "task", ws, transcript_text="t")
    assert calls == [6, 6], "one same-size retry, no halving needed"
    assert out["criteria_abstained"] == 0


def test_parse_truncation_persistent_falls_back_to_halving(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "bedrock/arn:aws:bedrock:x:1:application-inference-profile/s1")
    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda members: None)
    calls = []

    def fake_council(chunk, system, user_for_member, members, images=None):
        calls.append(len(chunk))
        if len(chunk) > 3:
            return {"overall_score": 0.0, "error": "parse: expected up to 6 verdicts, matched 1", "usage": dict(grading._ZERO_USAGE)}
        return _fake_grade_success(len(chunk))

    monkeypatch.setattr(grading, "_grade_council", fake_council)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(6)]
    ws = tmp_path / "task_output" / "results"; ws.mkdir(parents=True)
    out = grading.grade_with_rubric(rubrics, "task", ws, transcript_text="t")
    assert calls == [6, 6, 3, 3], "full, retry, then two clean halves"
    assert out["criteria_abstained"] == 0
    assert out["criteria_passed"] == 6


def test_ok_but_partial_sonnet_coverage_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "bedrock/arn:aws:bedrock:x:1:application-inference-profile/s1")
    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda members: None)
    calls = []

    def fake_council(chunk, system, user_for_member, members, images=None):
        calls.append(len(chunk))
        if len(calls) == 1:
            partial = _fake_grade_success(len(chunk))
            partial["judge_council"]["per_member_verdict_count"] = {"sonnet": 2}
            partial["criteria_abstained"] = len(chunk) - 2
            return partial
        return _fake_grade_success(len(chunk))

    monkeypatch.setattr(grading, "_grade_council", fake_council)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(6)]
    ws = tmp_path / "task_output" / "results"; ws.mkdir(parents=True)
    out = grading.grade_with_rubric(rubrics, "task", ws, transcript_text="t")
    assert calls == [6, 6], "ok-but-partial sonnet coverage must retry same-size"
    assert out["criteria_abstained"] == 0


def test_glm_partial_coverage_alone_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_COUNCIL_SONNET_ARN", "bedrock/arn:aws:bedrock:x:1:application-inference-profile/s1")
    monkeypatch.setenv("JUDGE_GPT_PRIMARY", "0")
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda members: None)
    calls = []

    def fake_council(chunk, system, user_for_member, members, images=None):
        calls.append(len(chunk))
        res = _fake_grade_success(len(chunk))
        res["judge_council"]["per_member_verdict_count"] = {"sonnet": len(chunk), "glm": 1}
        return res

    monkeypatch.setattr(grading, "_grade_council", fake_council)
    rubrics = [{"criterion": f"c{i}", "weight": 1} for i in range(6)]
    ws = tmp_path / "task_output" / "results"; ws.mkdir(parents=True)
    grading.grade_with_rubric(rubrics, "task", ws, transcript_text="t")
    assert calls == [6], "glm small-context truncation is by-design, no retry"


# ---------------------------------------------------------------------------
# Phase 3: offline audio evidence (judge_asr)
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Phase 3: offline audio evidence (judge_asr) — ported from neo-version f3bd6862
# ---------------------------------------------------------------------------


def _write_wav(path, seconds=1.0, rate=16000):
    import wave as _wave, struct as _struct, math
    with _wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(b"".join(
            _struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(n)))


def test_audio_collected_with_own_size_gate(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _write_wav(results / "memo.wav", seconds=1.0)
    names = [f.name for f in grading._collect_deliverable_files(results)]
    assert "memo.wav" in names


def test_audio_marker_uses_transcript_when_asr_available(tmp_path, monkeypatch):
    from src.utils import judge_asr
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _write_wav(results / "memo.wav")
    monkeypatch.setattr(judge_asr, "transcribe", lambda p: "hello from the memo")
    ev = grading._payload_text(grading._gather_evidence(results, "t", budget=None))
    assert "memo.wav (audio, transcribed offline)" in ev
    assert "hello from the memo" in ev


def test_audio_marker_falls_back_to_wav_duration(tmp_path, monkeypatch):
    from src.utils import judge_asr
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    _write_wav(results / "memo.wav", seconds=2.0)
    monkeypatch.setattr(judge_asr, "transcribe", lambda p: None)
    ev = grading._payload_text(grading._gather_evidence(results, "t", budget=None))
    assert "audio 2.0s, 16000 Hz, 1 channel(s)" in ev
    assert "transcript unavailable" in ev


def test_audio_marker_presence_only_for_undecodable(tmp_path, monkeypatch):
    from src.utils import judge_asr
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "song.mp3").write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)
    monkeypatch.setattr(judge_asr, "transcribe", lambda p: None)
    ev = grading._payload_text(grading._gather_evidence(results, "t", budget=None))
    assert "song.mp3" in ev and "audio transcript unavailable" in ev


def test_wav_duration_marker_stdlib_only(tmp_path):
    from src.utils import judge_asr
    _write_wav(tmp_path / "clip.wav", seconds=3.5, rate=8000)
    assert judge_asr.wav_duration_marker(tmp_path / "clip.wav") == \
        "audio 3.5s, 8000 Hz, 1 channel(s)"


def test_decode_wav_stdlib(tmp_path):
    from src.utils import judge_asr
    _write_wav(tmp_path / "clip.wav", seconds=0.5)
    out = judge_asr._decode_wav(tmp_path / "clip.wav")
    assert out is not None
    samples, rate = out
    assert rate == 16000 and len(samples) == 8000
    assert all(-1.0 <= s <= 1.0 for s in samples)


def test_transcribe_disabled_by_env(tmp_path, monkeypatch):
    from src.utils import judge_asr
    monkeypatch.setenv("WCB_JUDGE_AUDIO_TRANSCRIBE", "0")
    _write_wav(tmp_path / "clip.wav")
    assert judge_asr.transcribe(tmp_path / "clip.wav") is None


# --- ported from neo-version 14551784 (scratch exclusion) ---
def _results_dir(tmp_path):
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True, exist_ok=True)
    return results


_SCRATCH_DIRS = (".scratch", ".tmp", ".cache", "work", "intermediate", "_wip", ".hidden")


def test_in_scratch_subdir_covers_dot_prefixed_and_new_names(tmp_path):
    results = _results_dir(tmp_path)
    for d in _SCRATCH_DIRS:
        (results / d).mkdir()
        (results / d / "note.txt").write_text("x", encoding="utf-8")
        assert grading._in_scratch_subdir(results / d / "note.txt"), d
    (results / "final.md").write_text("real", encoding="utf-8")
    assert not grading._in_scratch_subdir(results / "final.md")


def test_gather_evidence_dot_scratch_demoted_and_relabelled(tmp_path):
    results = _results_dir(tmp_path)
    (results / ".scratch").mkdir()
    (results / ".scratch" / "cmp_pdf.txt").write_text("AGENT NOTES", encoding="utf-8")
    (results / "final.md").write_text("REAL DELIVERABLE" * 300, encoding="utf-8")

    ev = grading._payload_text(grading._gather_evidence(results, "tail", budget=None))
    assert ev.index("final.md") < ev.index("cmp_pdf.txt")
    # No rubric-named file: the block is kept (may be the only evidence) but
    # must never present itself as a deliverable.
    assert "----- SCRATCH: .scratch/cmp_pdf.txt -----" in ev
    assert "DELIVERABLE: .scratch/cmp_pdf.txt" not in ev
    assert "AGENT NOTES" in ev
    assert "----- DELIVERABLE: final.md -----" in ev


def test_gather_evidence_scratch_excluded_when_named_file_present(tmp_path):
    results = _results_dir(tmp_path)
    (results / ".scratch").mkdir()
    (results / ".scratch" / "cmp_pdf.txt").write_text("SCRATCH CLAIM", encoding="utf-8")
    (results / ".scratch" / "b.txt").write_text("MORE SCRATCH", encoding="utf-8")
    (results / "handout.md").write_text("DELIVERED CONTENT", encoding="utf-8")

    ev = grading._payload_text(grading._gather_evidence(
        results, "tail", budget=None, rubric_names=frozenset({"handout.md"})
    ))
    assert "DELIVERED CONTENT" in ev
    assert "SCRATCH CLAIM" not in ev
    assert "MORE SCRATCH" not in ev
    assert "DELIVERABLE: .scratch/cmp_pdf.txt" not in ev
    assert ("----- SCRATCH (agent work-product, not graded): "
            ".scratch/b.txt, .scratch/cmp_pdf.txt -----") in ev


def test_gather_evidence_scratch_kept_when_no_named_deliverable(tmp_path):
    results = _results_dir(tmp_path)
    (results / "tmp").mkdir()
    (results / "tmp" / "workings.md").write_text("PARTIAL WORK", encoding="utf-8")

    ev = grading._payload_text(grading._gather_evidence(results, "tail", budget=None, rubric_names=frozenset()))
    assert "PARTIAL WORK" in ev
    assert "----- SCRATCH: tmp/workings.md -----" in ev
    assert "agent work-product, not graded" not in ev


def test_gather_evidence_scratch_line_survives_budget_cut(tmp_path):
    results = _results_dir(tmp_path)
    (results / ".scratch").mkdir()
    (results / ".scratch" / "dump.txt").write_text("S" * 5000, encoding="utf-8")
    (results / "handout.md").write_text("H" * 5000, encoding="utf-8")

    ev = grading._payload_text(grading._gather_evidence(
        results, "T" * 100, budget=3000, rubric_names=frozenset({"handout.md"})
    ))
    assert len(ev) <= 3000
    assert "agent work-product, not graded): .scratch/dump.txt" in ev
    assert "S" * 100 not in ev


