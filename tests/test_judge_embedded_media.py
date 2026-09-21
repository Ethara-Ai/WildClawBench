"""A3-lite — pixels that live inside or beside a rubric-named deliverable.

`_collect_image_attachments` attached a named .png and rendered the pages of a
named .pdf, and stopped there. Two whole classes of deliverable carry their
figures somewhere else entirely:

An OOXML file IS a zip. `_extract_text_deliverable` already opens .docx/.pptx/
.xlsx and reads their `<w:t>` / `<a:t>` / sharedStrings nodes, and that
extraction returns NOTHING for the chart the criterion is actually about —
"report.docx contains a revenue chart" was graded off the agent's narration.
The chart is a PNG part sitting in `word/media/` in the same archive the walk
already has open.

An .html or .md page carries `<img src="assets/chart.png">` instead. The image
is a separate file the rubric usually does not name, so the deliverable sweep
either collected it as an unnamed image (dimension marker only) or never
reached it.

Both flow through the pipeline that was already there: the parent must be
rubric-named, the 8-image / 3.5MB caps are the same caps, routing is still
`_will_receive_pixels`, and the naming is `_pdf_page_attachments`' own
`parent#part` convention.

The linked-media half is also a trust boundary. The reference is agent-authored
text in a file the agent wrote, so it is a request and not a permission:
`http(s)://`, protocol-relative `//host/x.png`, inline `data:` payloads,
absolute filesystem paths, `..` traversal and symlinks pointing out of the tree
are all refused.
"""
from __future__ import annotations

import base64
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402

_DOCX_XML = (
    '<?xml version="1.0"?><w:document '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:t>QUARTERLY REVENUE</w:t></w:body></w:document>"
)


def _png(w: int = 6, h: int = 4) -> bytes:
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR"
            + ihdr + b"\x00" * 12)


def _results(tmp_path: Path) -> Path:
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    return results


def _names(attachments: list[dict]) -> list[str]:
    return [a["name"] for a in attachments]


# ---------------------------------------------------------------------------
# Office media
# ---------------------------------------------------------------------------


def _office(path: Path, prefix: str, *, count: int = 1) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", _DOCX_XML)
        for i in range(count):
            z.writestr(f"{prefix}image{i + 1}.png", _png())


def test_docx_embedded_media_is_attached_with_the_part_path(tmp_path):
    results = _results(tmp_path)
    _office(results / "report.docx", "word/media/", count=2)
    got = grading._collect_image_attachments(
        results, frozenset({"report.docx"}))
    assert _names(got) == ["report.docx#media/image1.png",
                           "report.docx#media/image2.png"]
    assert all(a["media_type"] == "image/png" for a in got)
    assert base64.b64decode(got[0]["b64"]) == _png()


def test_pptx_and_xlsx_media_prefixes_are_both_walked(tmp_path):
    results = _results(tmp_path)
    _office(results / "deck.pptx", "ppt/media/")
    _office(results / "book.xlsx", "xl/media/")
    assert _names(grading._collect_image_attachments(
        results, frozenset({"deck.pptx"}))) == ["deck.pptx#media/image1.png"]
    assert _names(grading._collect_image_attachments(
        results, frozenset({"book.xlsx"}))) == ["book.xlsx#media/image1.png"]


def test_office_media_is_deterministic_and_part_sorted(tmp_path):
    results = _results(tmp_path)
    with zipfile.ZipFile(results / "report.docx", "w") as z:
        z.writestr("word/document.xml", _DOCX_XML)
        for part in ("word/media/image9.png", "word/media/image10.png",
                     "word/media/image2.png"):
            z.writestr(part, _png())
    first = _names(grading._collect_image_attachments(
        results, frozenset({"report.docx"})))
    assert first == ["report.docx#media/image10.png",
                     "report.docx#media/image2.png",
                     "report.docx#media/image9.png"]
    for _ in range(3):
        assert _names(grading._collect_image_attachments(
            results, frozenset({"report.docx"}))) == first


def test_non_image_and_oversized_office_parts_are_skipped(tmp_path):
    results = _results(tmp_path)
    with zipfile.ZipFile(results / "report.docx", "w") as z:
        z.writestr("word/document.xml", _DOCX_XML)
        z.writestr("word/media/notes.bin", b"\x00" * 64)
        z.writestr("word/media/huge.png",
                   b"\x89PNG" + b"\x00" * (grading._IMAGE_ATTACH_MAX_BYTES + 1))
        z.writestr("word/media/ok.png", _png())
        z.writestr("word/embeddings/sheet.xlsx", b"PK\x03\x04")
    assert _names(grading._collect_image_attachments(
        results, frozenset({"report.docx"}))) == ["report.docx#media/ok.png"]


def test_an_office_file_the_rubric_does_not_name_contributes_nothing(tmp_path):
    results = _results(tmp_path)
    _office(results / "appendix.docx", "word/media/")
    assert grading._collect_image_attachments(
        results, frozenset({"report.md"})) == []


def test_a_corrupt_office_file_degrades_instead_of_raising(tmp_path):
    results = _results(tmp_path)
    (results / "report.docx").write_bytes(b"not a zip at all")
    assert grading._collect_image_attachments(
        results, frozenset({"report.docx"})) == []
    assert grading._ooxml_media_attachments(results / "report.docx", 8) == []


def test_an_office_file_with_no_media_yields_no_attachment(tmp_path):
    results = _results(tmp_path)
    with zipfile.ZipFile(results / "report.docx", "w") as z:
        z.writestr("word/document.xml", _DOCX_XML)
    assert grading._collect_image_attachments(
        results, frozenset({"report.docx"})) == []


# ---------------------------------------------------------------------------
# Linked media in HTML / Markdown
# ---------------------------------------------------------------------------


def test_html_img_src_inside_the_tree_is_attached(tmp_path):
    results = _results(tmp_path)
    (results / "assets").mkdir()
    (results / "assets" / "chart.png").write_bytes(_png())
    (results / "index.html").write_text(
        "<h1>Q3</h1><img src='assets/chart.png' alt='revenue'>",
        encoding="utf-8")
    got = grading._collect_image_attachments(
        results, frozenset({"index.html"}))
    assert _names(got) == ["index.html#assets/chart.png"]
    assert base64.b64decode(got[0]["b64"]) == _png()


def test_markdown_image_syntax_is_attached(tmp_path):
    results = _results(tmp_path)
    (results / "fig.png").write_bytes(_png())
    (results / "report.md").write_text(
        "# Findings\n\n![the revenue chart](fig.png)\n", encoding="utf-8")
    assert _names(grading._collect_image_attachments(
        results, frozenset({"report.md"}))) == ["report.md#fig.png"]


def test_remote_inline_and_absolute_references_are_all_refused(tmp_path):
    results = _results(tmp_path)
    (results / "ok.png").write_bytes(_png())
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())
    (results / "index.html").write_text(
        "<img src='https://evil.example/x.png'>"
        "<img src='http://evil.example/y.png'>"
        "<img src=\"//evil.example/z.png\">"
        "<img src='data:image/png;base64,AAAA'>"
        f"<img src='{outside}'>"
        "<img src='/etc/hosts.png'>"
        "![](https://evil.example/m.png)\n",
        encoding="utf-8")
    assert grading._collect_image_attachments(
        results, frozenset({"index.html"})) == []


def test_traversal_out_of_the_output_tree_is_refused(tmp_path):
    results = _results(tmp_path)
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png())
    (results / "index.html").write_text(
        "<img src='../../../secret.png'>"
        "<img src='../../secret.png'>",
        encoding="utf-8")
    assert grading._collect_image_attachments(
        results, frozenset({"index.html"})) == []


def test_a_symlink_pointing_out_of_the_tree_is_refused(tmp_path):
    results = _results(tmp_path)
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png())
    try:
        (results / "escape.png").symlink_to(secret)
    except OSError:  # pragma: no cover - platform without symlinks
        return
    (results / "index.html").write_text(
        "<img src='escape.png'>", encoding="utf-8")
    assert grading._collect_image_attachments(
        results, frozenset({"index.html"})) == []


def test_a_reference_to_a_file_that_does_not_exist_is_refused(tmp_path):
    results = _results(tmp_path)
    (results / "index.html").write_text(
        "<img src='assets/never_written.png'>", encoding="utf-8")
    assert grading._collect_image_attachments(
        results, frozenset({"index.html"})) == []


def test_the_same_image_referenced_twice_is_attached_once(tmp_path):
    results = _results(tmp_path)
    (results / "chart.png").write_bytes(_png())
    (results / "index.html").write_text(
        "<img src='chart.png'><img src=\"./chart.png\">"
        "![again](chart.png)", encoding="utf-8")
    assert _names(grading._collect_image_attachments(
        results, frozenset({"index.html"}))) == ["index.html#chart.png"]


def test_an_oversized_linked_image_is_skipped(tmp_path):
    results = _results(tmp_path)
    (results / "big.png").write_bytes(
        b"\x89PNG" + b"\x00" * (grading._IMAGE_ATTACH_MAX_BYTES + 1))
    (results / "ok.png").write_bytes(_png())
    (results / "index.html").write_text(
        "<img src='big.png'><img src='ok.png'>", encoding="utf-8")
    assert _names(grading._collect_image_attachments(
        results, frozenset({"index.html"}))) == ["index.html#ok.png"]


# ---------------------------------------------------------------------------
# The pipeline these ride on is unchanged
# ---------------------------------------------------------------------------


def test_the_eight_image_cap_still_holds_across_the_new_sources(tmp_path):
    results = _results(tmp_path)
    _office(results / "report.docx", "word/media/", count=12)
    got = grading._collect_image_attachments(
        results, frozenset({"report.docx"}))
    assert len(got) == grading._judge_max_images() == 8


def test_attach_disabled_switches_every_source_off(tmp_path, monkeypatch):
    monkeypatch.setenv("WCB_JUDGE_ATTACH_IMAGES", "0")
    results = _results(tmp_path)
    _office(results / "report.docx", "word/media/")
    (results / "chart.png").write_bytes(_png())
    (results / "index.html").write_text(
        "<img src='chart.png'>", encoding="utf-8")
    assert grading._collect_image_attachments(
        results, frozenset({"report.docx", "index.html"})) == []


def test_only_the_vision_member_is_routed_and_told(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    _office(results / "report.docx", "word/media/")
    sonnet = grading.CouncilMember(family="sonnet", model="bedrock/sonnet-arn")
    glm = grading.CouncilMember(family="glm", model="bedrock/glm-arn")
    monkeypatch.setattr(grading, "council_members", lambda: [sonnet, glm])
    monkeypatch.setattr(grading, "validate_judge_pricing", lambda m: None)
    seen: dict = {}

    def _fake_council(rubrics, system, user_for_member, mem, images=None):
        seen["prompts"] = {m.family: user_for_member[m.model] for m in mem}
        seen["images"] = images
        seen["routes"] = {m.family: grading._will_receive_pixels(m, images)
                          for m in mem}
        return {
            "overall_score": 1.0, "rubric_weights_percentage": 100.0,
            "criteria_total": len(rubrics), "criteria_passed": len(rubrics),
            "criteria_failed": 0, "criteria_abstained": 0, "criteria": [],
            "judge_model": "council", "judge_council": {},
            "truncation_flags": [], "abstention_flags": [],
            "usage": dict(grading._ZERO_USAGE),
        }

    monkeypatch.setattr(grading, "_grade_council", _fake_council)
    grading.grade_with_rubric(
        [{"criterion": "report.docx shows a revenue chart", "weight": 1}],
        "task", results, "[FINAL ASSISTANT MESSAGE] done",
    )
    assert _names(seen["images"]) == ["report.docx#media/image1.png"]
    assert seen["routes"] == {"sonnet": True, "glm": False}
    assert "report.docx#media/image1.png" in seen["prompts"]["sonnet"]
    assert "ATTACHED IMAGES" in seen["prompts"]["sonnet"]
    assert "ATTACHED IMAGES" not in seen["prompts"]["glm"]


def test_the_prompt_explains_the_parent_hash_part_naming():
    from src.utils.prompt_loader import load_prompt
    prompt = load_prompt("judge_system")
    assert '"file.pdf#pageN"' in prompt
    assert '"report.docx#media/image1.png"' in prompt
    assert '"index.html#assets/chart.png"' in prompt
    assert "belongs to the deliverable named before it" in prompt
