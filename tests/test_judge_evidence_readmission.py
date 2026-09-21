"""Leftover budget is re-admitted deterministically, and no name is ever cut in
half to make room for it.

Two defects with one cause: the deliverable fill was a single greedy pass that
treated its own queueing rules as final.

`_EXTRACT_CHAR_CAP` is a QUEUEING rule — 100K per binary so one fat extraction
cannot starve the files behind it — but it was also the last word, so a
rubric-named 300K PDF lost 200K of itself on a member with 400K of unused room.
The head+tail partial had the same shape: exactly one block could be cut and
every block after it was omitted outright, whatever room was left.

And what the payload said about the omissions was worse than the omissions. The
manifest was truncated twice: a 40-name cap with an honest "[+N more]" count,
and then a raw character slice of the rendered string, which lands mid-filename.
The live sighting was `bench_run_pac` — so the one structure that exists to stop
the judge inferring absence from silence was handing it the name of a file that
does not exist.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402

_DOCX_XML = (
    '<?xml version="1.0"?><w:document '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{body}</w:body></w:document>"
)


def _results(tmp_path: Path) -> Path:
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    return results


def _docx(path: Path, chars: int) -> None:
    # One <w:t> run per 1000 chars: _extract_text_deliverable concatenates them,
    # so the extracted length is exactly `chars`.
    runs = "".join(f"<w:t>{'D' * 1000}</w:t>" for _ in range(chars // 1000))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", _DOCX_XML.format(body=runs))


# ---------------------------------------------------------------------------
# The 100K extraction cap is a first-pass rule, not a verdict
# ---------------------------------------------------------------------------


def test_named_binary_is_re_extracted_past_the_first_pass_cap(tmp_path):
    results = _results(tmp_path)
    _docx(results / "packet.docx", 300_000)
    named = frozenset({"packet.docx"})
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=260_000,
        rubric_names=named)
    body = grading._split_evidence(blob)[0]
    # The grown block is itself head+tail cut to the room available, so the
    # mass is what matters, not one contiguous run.
    assert body.count("D") > 250_000, "the cap must have lifted on pass two"
    assert len(blob) <= 260_000


def test_re_extraction_stops_at_the_hard_ceiling(tmp_path):
    results = _results(tmp_path)
    _docx(results / "packet.docx", 600_000)
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=1_175_000,
        rubric_names=frozenset({"packet.docx"}))
    body = grading._split_evidence(blob)[0]
    assert "D" * 399_000 in body
    assert "D" * (grading._READMIT_EXTRACT_CEILING + 1) not in body


def test_an_unnamed_binary_keeps_the_first_pass_cap(tmp_path):
    # Only the files the rubric grades BY NAME earn the lift; lifting every
    # binary would put the starvation the cap prevents straight back.
    results = _results(tmp_path)
    _docx(results / "appendix.docx", 300_000)
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=900_000)
    body = grading._split_evidence(blob)[0]
    assert "D" * grading._EXTRACT_CHAR_CAP in body
    assert "D" * (grading._EXTRACT_CHAR_CAP + 1) not in body


def test_extraction_remainder_is_empty_for_text_and_short_binaries(tmp_path):
    results = _results(tmp_path)
    (results / "notes.md").write_text("x" * 500, encoding="utf-8")
    _docx(results / "small.docx", 5_000)
    assert grading._extraction_remainder(results / "notes.md") == ""
    assert grading._extraction_remainder(results / "small.docx") == ""


# ---------------------------------------------------------------------------
# Re-admission order, and the budget it may never cross
# ---------------------------------------------------------------------------


def test_named_file_is_rescued_before_anything_else(tmp_path):
    results = _results(tmp_path)
    (results / "aaa_filler.md").write_text("F" * 30_000, encoding="utf-8")
    (results / "zzz_named.md").write_text("N" * 30_000, encoding="utf-8")
    (results / "mmm_other.md").write_text("O" * 30_000, encoding="utf-8")
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=45_000,
        rubric_names=frozenset({"zzz_named.md"}))
    body = grading._split_evidence(blob)[0]
    assert "N" * 20_000 in body, "the rubric-named file must survive first"
    assert len(blob) <= 45_000


def test_whole_small_omissions_are_rescued_after_the_partials(tmp_path):
    # `report_big` carries the report/flagged stem so it outranks the other two
    # whatever their sizes: the first pass spends its one partial on it and
    # omits both files behind it. The second pass completes the partial first
    # (priority b), and only the room still left after that reaches the
    # wholly-omitted tier (priority c), smallest first.
    results = _results(tmp_path)
    (results / "report_big.md").write_text("B" * 20_000, encoding="utf-8")
    (results / "tiny.md").write_text("T" * 500, encoding="utf-8")
    (results / "other.md").write_text("O" * 30_000, encoding="utf-8")
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=22_100)
    body = grading._split_evidence(blob)[0]
    assert "B" * 20_000 in body, "the partial was completed first"
    assert "T" * 500 in body, "then the smallest whole omission"
    assert "O" * 500 not in body, "the 30K stranger never fit"
    assert "other.md" in body, "and the one that did not fit is still named"
    assert len(blob) <= 22_100


def test_an_unnamed_omission_is_rescued_whole_or_not_at_all(tmp_path):
    # Half of a file the rubric never names is noise; the room belongs to the
    # named content behind it.
    # The first pass still spends its ONE head+tail partial on the first
    # overflow (filler.md here); a file behind that one is wholly omitted, and
    # THAT is the class the second pass refuses to admit in halves.
    results = _results(tmp_path)
    (results / "named.md").write_text("N" * 20_000, encoding="utf-8")
    (results / "filler.md").write_text("F" * 40_000, encoding="utf-8")
    (results / "stranger.md").write_text("Z" * 60_000, encoding="utf-8")
    blob = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=40_000,
        rubric_names=frozenset({"named.md"}))
    body = grading._split_evidence(blob)[0]
    assert "N" * 20_000 in body
    assert "Z" not in body
    assert "stranger.md" in body, "and it is still NAMED as omitted"


def test_re_admission_never_exceeds_the_budget(tmp_path):
    results = _results(tmp_path)
    for i in range(12):
        (results / f"file_{i:02d}.md").write_text(
            chr(97 + i) * (2_000 * (i + 1)), encoding="utf-8")
    _docx(results / "packet.docx", 350_000)
    transcript = "[FINAL ASSISTANT MESSAGE] " + "t" * 5_000
    named = frozenset({"packet.docx", "file_07.md"})
    for budget in (12_000, 40_000, 90_000, 175_000, 225_000, 450_000, 700_000):
        blob = grading._gather_evidence(
            results, transcript, budget=budget, rubric_names=named)
        assert len(blob) <= budget, budget
        assert grading._split_evidence(blob)[1] == transcript, budget


def test_re_admission_is_deterministic(tmp_path):
    results = _results(tmp_path)
    for i in range(8):
        (results / f"f{i}.md").write_text(chr(97 + i) * (3_000 + i * 500),
                                          encoding="utf-8")
    args = (results, "[FINAL ASSISTANT MESSAGE] done")
    kwargs = {"budget": 20_000, "rubric_names": frozenset({"f3.md"})}
    first = grading._gather_evidence(*args, **kwargs)
    for _ in range(4):
        assert grading._gather_evidence(*args, **kwargs) == first


def test_a_rescued_file_leaves_the_omission_list(tmp_path):
    results = _results(tmp_path)
    (results / "keep.md").write_text("K" * 2_000, encoding="utf-8")
    (results / "rescued.md").write_text("R" * 3_000, encoding="utf-8")
    tight = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=4_000)
    assert "rescued.md" in tight
    assert "EVIDENCE BUDGET NOTE" in tight
    roomy = grading._gather_evidence(
        results, "[FINAL ASSISTANT MESSAGE] done", budget=9_000)
    assert "R" * 3_000 in roomy
    assert "EVIDENCE BUDGET NOTE" not in roomy


# ---------------------------------------------------------------------------
# No name is ever cut in half
# ---------------------------------------------------------------------------


_LONG_NAMES = [f"bench_run_packet_section_{i:02d}.md" for i in range(40)]


def test_manifest_shrinks_by_whole_names_never_mid_name():
    # Every one of these shares the `bench_run_packet_section_` stem, so a
    # substring probe cannot tell a whole name from a fragment. Parse the
    # rendered listing instead and require each token to be a complete entry.
    entries = [f"{n}{grading._reason('over-budget')}" for n in _LONG_NAMES]
    for limit in range(60, 3200, 7):
        out = grading._omission_manifest(entries, limit)
        assert len(out) <= limit, limit
        if not out:
            continue
        listing = out.split("omitted or cut for budget:", 1)[1]
        listing = listing.split(" [+", 1)[0].removesuffix(" -----\n").strip()
        if not listing:
            continue
        for tok in listing.split(", "):
            assert tok in entries, (limit, tok)


def test_manifest_keeps_the_count_when_every_name_is_dropped():
    names = [f"{n}{grading._reason('over-budget')}" for n in _LONG_NAMES]
    out = grading._omission_manifest(names, 130)
    assert len(out) <= 130
    assert "40 collected file(s)" in out
    assert "[+40 more]" in out
    assert "bench_run" not in out


def test_unlimited_manifest_is_byte_identical_to_the_legacy_rendering():
    names = ["a.md", "b.md"]
    assert grading._omission_manifest(names) == (
        "\n----- EVIDENCE BUDGET NOTE: 2 collected file(s)"
        " omitted or cut for budget: a.md, b.md -----\n"
    )
    assert grading._duplicate_note([("w/x.md", "a/x.md")]) == (
        "\n----- DUPLICATE COPIES (byte-identical, content shown once above):"
        " w/x.md (identical to a/x.md)"
        " [reason: duplicate-of a/x.md] -----\n"
    )
    assert grading._scratch_note(["p.txt", "q.txt"]) == (
        "\n----- SCRATCH (agent work-product, not graded): p.txt, q.txt -----\n"
    )


def test_fit_by_whole_names_degrades_to_nothing_rather_than_a_fragment():
    # Nothing at all beats a fragment: when even the empty listing overruns,
    # the note is dropped rather than sliced.
    assert grading._fit_by_whole_names(lambda k: "x" * 500, 3, 10) == ""


def _listed(blob: str, header: str) -> list[str]:
    """The comma-separated names rendered inside one note, or [] when absent."""
    if header not in blob:
        return []
    tail = blob.split(header, 1)[1].split(" -----", 1)[0]
    tail = tail.split(" [+", 1)[0].strip()
    return [t for t in tail.split(", ") if t]


def test_scratch_and_duplicate_notes_never_cut_a_name(tmp_path):
    # Same shared-stem problem as the manifest: every scratch name here starts
    # `intermediate_comparison_dump_`, so only a parse of the rendered listing
    # can tell a dropped name from a sliced one.
    task_output = tmp_path / "task_output"
    artifacts = task_output / "artifacts" / "results"
    scratch = task_output / "artifacts" / ".scratch"
    mirror = task_output / "workspace_full" / "results"
    for d in (artifacts, scratch, mirror):
        d.mkdir(parents=True)
    (artifacts / "report.md").write_text("THE REPORT " * 2_000, encoding="utf-8")
    (mirror / "report.md").write_text("THE REPORT " * 2_000, encoding="utf-8")
    names = [f"intermediate_comparison_dump_{i:02d}.txt" for i in range(60)]
    for n in names:
        (scratch / n).write_text("s" * 900, encoding="utf-8")
    evidence = task_output / "artifacts"
    sliced_somewhere = False
    for budget in range(6_000, 40_000, 250):
        blob = grading._gather_evidence(
            evidence, "[FINAL ASSISTANT MESSAGE] done", budget=budget,
            rubric_names=frozenset({"report.md"}))
        assert len(blob) <= budget, budget
        listed = _listed(blob, "SCRATCH (agent work-product, not graded):")
        if 0 < len(listed) < 60:
            sliced_somewhere = True
        for tok in listed:
            assert tok in names, (budget, tok)
        for tok in _listed(blob, "DUPLICATE COPIES (byte-identical,"
                                 " content shown once above):"):
            assert tok.startswith("workspace_full/results/report.md "), (
                budget, tok)
            assert tok.endswith("[reason: duplicate-of"
                                " artifacts/results/report.md]"), (budget, tok)
    assert sliced_somewhere, "the sweep must actually exercise a shrunk note"
