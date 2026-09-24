"""Inline `data:image/...;base64,...` payloads inside an HTML/Markdown board.

The agent is told to deliver "a single file that works off a tablet", so it
inlines its photos instead of referencing them. `_deliverable_evidence_marker`
read `.html` verbatim, so a 1.2 MB board whose real markup is 30 KB spent the
whole deliverable budget on characters no judge can read: across 74 graded runs
44 pegged the 700K OAuth budget, 31 clipped an HTML board that was 81-99%
base64, and 142 criteria naming a clipped file failed with NO truncation flag.

The payload is now replaced by `[inline image N: image/jpeg, 300 KB]` in the
evidence text and decoded back to real pixels on the channel that already
carries a page's images to the judge (`_linked_image_attachments`), under the
caps that were already there. The tag and its `alt` caption never move.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402

# 300 KB of "JPEG": 307200 bytes -> exactly 409600 base64 chars, no padding.
_BIG_JPEG = b"\xff\xd8\xff\xe0" + bytes(307_200 - 4)
_BIG_B64 = base64.b64encode(_BIG_JPEG).decode("ascii")
# An icon well under the strip floor - it stays inline where it costs nothing.
_TINY_B64 = base64.b64encode(bytes(90)).decode("ascii")


def _big_b64(i: int) -> str:
    """A distinct 300 KB photo. Four copies of ONE payload would collapse to a
    single attachment by the dedup, which is a different property."""
    return base64.b64encode(
        _BIG_JPEG[:4] + bytes([i + 1]) + _BIG_JPEG[5:]).decode("ascii")


def _results(tmp_path: Path) -> Path:
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    return results


def _board_html() -> str:
    return (
        "<html><body><h1>Holiday proof board</h1>\n"
        f'<img src="data:image/jpeg;base64,{_BIG_B64}" '
        'alt="Alpine Gin Pair label proof PR-2214-D" title="proof 1">\n'
        "<p>Hold code: HOLD-9931</p>\n"
        f'<img src="data:image/png;base64,{_TINY_B64}" alt="bullet">\n'
        "</body></html>\n"
    )


# ---------------------------------------------------------------------------
# T1 - the evidence text
# ---------------------------------------------------------------------------


def test_big_inline_payload_becomes_a_placeholder_and_the_caption_survives(tmp_path):
    results = _results(tmp_path)
    (results / "board.html").write_text(_board_html(), encoding="utf-8")

    marker = grading._deliverable_evidence_marker(results / "board.html")

    assert "[inline image 1: image/jpeg, 300 KB]" in marker
    # The tag and the caption the criterion is actually about are untouched.
    assert 'alt="Alpine Gin Pair label proof PR-2214-D"' in marker
    assert 'title="proof 1"' in marker
    assert "<img src=\"[inline image 1:" in marker
    assert "Hold code: HOLD-9931" in marker
    # The payload itself is gone; the icon below the floor is not.
    assert _BIG_B64 not in marker
    assert _BIG_B64[:200] not in marker
    assert f"data:image/png;base64,{_TINY_B64}" in marker
    # 300 KB of board collapses to something a budget can hold.
    assert len(marker) < 10_000


def test_the_strip_floor_is_the_payload_length(tmp_path):
    at = "A" * grading._DATA_URI_MIN_PAYLOAD
    under = "A" * (grading._DATA_URI_MIN_PAYLOAD - 1)
    results = _results(tmp_path)
    (results / "page.html").write_text(
        f"<img src='data:image/png;base64,{under}'>"
        f"<img src='data:image/png;base64,{at}'>", encoding="utf-8")

    marker = grading._deliverable_evidence_marker(results / "page.html")

    assert f"data:image/png;base64,{under}'" in marker
    assert at not in marker
    # Numbering counts only what was stripped, so the placeholder is N=1.
    assert "[inline image 1: image/png," in marker


# ---------------------------------------------------------------------------
# T2 - the pixels
# ---------------------------------------------------------------------------


def test_the_stripped_image_reaches_the_judge_as_an_attachment(tmp_path):
    results = _results(tmp_path)
    (results / "board.html").write_text(_board_html(), encoding="utf-8")

    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))

    assert len(got) == 1
    assert got[0]["name"] == "board.html#inline-image-1"
    assert got[0]["media_type"] == "image/jpeg"
    assert base64.b64decode(got[0]["b64"]) == _BIG_JPEG


def test_unattachable_and_corrupt_payloads_are_skipped_not_raised(tmp_path):
    results = _results(tmp_path)
    (results / "board.html").write_text(
        f'<img src="data:image/svg+xml;base64,{"A" * 400}">'
        f'<img src="data:image/png;base64,{"A" * 201}">'
        f'<img src="data:image/jpeg;base64,{_BIG_B64}">',
        encoding="utf-8")

    marker = grading._deliverable_evidence_marker(results / "board.html")
    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))

    # All three are stripped from the text and numbered in document order...
    assert "[inline image 1: image/svg+xml," in marker
    assert "[inline image 2: image/png," in marker
    assert "[inline image 3: image/jpeg," in marker
    # ...but only the one that is both an attachable type and decodable ships.
    assert [a["name"] for a in got] == ["board.html#inline-image-3"]
    assert base64.b64decode(got[0]["b64"]) == _BIG_JPEG


def test_a_repeated_logo_does_not_eat_the_whole_cap(tmp_path):
    results = _results(tmp_path)
    logo = base64.b64encode(b"\x89PNG" + bytes(600)).decode("ascii")
    photo = base64.b64encode(b"\xff\xd8\xff" + bytes(900)).decode("ascii")
    (results / "board.html").write_text(
        "".join(f'<img src="data:image/png;base64,{logo}" alt="logo">'
                for _ in range(9))
        + f'<img src="data:image/png;base64,{photo}" alt="the photo">',
        encoding="utf-8")

    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))

    # One logo, one photo - and N is still the document-order position, so the
    # photo's label names the 10th placeholder in the text.
    assert [a["name"] for a in got] == [
        "board.html#inline-image-1", "board.html#inline-image-10"]
    assert base64.b64decode(got[1]["b64"]) == b"\xff\xd8\xff" + bytes(900)


def test_the_non_standard_image_jpg_type_still_ships_pixels(tmp_path):
    results = _results(tmp_path)
    (results / "board.html").write_text(
        f'<img src="data:image/jpg;base64,{_BIG_B64}" alt="proof">',
        encoding="utf-8")

    marker = grading._deliverable_evidence_marker(results / "board.html")
    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))

    # The text keeps the author's own spelling; the attachment is corrected to
    # the media type the judge APIs actually accept.
    assert "[inline image 1: image/jpg, 300 KB]" in marker
    assert len(got) == 1
    assert got[0]["media_type"] == "image/jpeg"
    assert base64.b64decode(got[0]["b64"]) == _BIG_JPEG


def test_the_image_cap_still_bounds_inline_attachments(tmp_path):
    results = _results(tmp_path)
    (results / "board.html").write_text(
        "".join(
            '<img src="data:image/png;base64,'
            + base64.b64encode(bytes([i + 1]) + bytes(600)).decode("ascii")
            + '">'
            for i in range(grading._judge_max_images() + 4)),
        encoding="utf-8")

    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))

    assert len(got) == grading._judge_max_images()
    # Document order: the cap drops the images lowest on the page.
    assert got[0]["name"] == "board.html#inline-image-1"
    assert got[-1]["name"] == (
        f"board.html#inline-image-{grading._judge_max_images()}")


# ---------------------------------------------------------------------------
# T3 - the whole board survives evidence assembly
# ---------------------------------------------------------------------------


def test_a_1_2mb_inlined_board_is_no_longer_the_partial_block(tmp_path):
    results = _results(tmp_path)
    markup = ("<p>row MIDDLE-SENTINEL-4417 hold</p>\n" * 800)[:30_000]
    board = (
        "<html><body>\n"
        + f'<img src="data:image/jpeg;base64,{_big_b64(0)}" alt="proof a">\n'
        + markup
        + f'<img src="data:image/jpeg;base64,{_big_b64(1)}" alt="proof b">\n'
        + f'<img src="data:image/jpeg;base64,{_big_b64(2)}" alt="proof c">\n'
        + f'<img src="data:image/jpeg;base64,{_big_b64(3)}" alt="proof d">\n'
        + "</body></html>\n"
    )
    (results / "board.html").write_text(board, encoding="utf-8")
    assert len(board) > 1_200_000
    transcript = "[user] " + "t" * 450_000

    ev = grading._gather_evidence(
        results, transcript, budget=700_000,
        rubric_names=frozenset({"board.html"}))

    assert "board.html (partial)" not in ev
    assert "EVIDENCE BUDGET NOTE" not in ev
    assert "[truncated for evidence budget]" not in ev
    # The middle of the page - what the head+tail cut used to eat - is there.
    assert "MIDDLE-SENTINEL-4417" in ev
    assert "[inline image 4: image/jpeg, 300 KB]" in ev
    assert _big_b64(3)[:400] not in ev
    assert grading._split_evidence(ev)[1] == transcript

    got = grading._collect_image_attachments(
        results, frozenset({"board.html"}))
    assert [a["name"] for a in got] == [
        f"board.html#inline-image-{n}" for n in (1, 2, 3, 4)]


# ---------------------------------------------------------------------------
# T4 - which extensions are touched
# ---------------------------------------------------------------------------


def test_markdown_gets_the_same_treatment(tmp_path):
    results = _results(tmp_path)
    (results / "report.md").write_text(
        "# Findings\n\n"
        f"![the revenue chart](data:image/jpeg;base64,{_BIG_B64})\n",
        encoding="utf-8")

    marker = grading._deliverable_evidence_marker(results / "report.md")
    got = grading._collect_image_attachments(
        results, frozenset({"report.md"}))

    assert "![the revenue chart]([inline image 1: image/jpeg, 300 KB])" in marker
    assert _BIG_B64 not in marker
    assert [a["name"] for a in got] == ["report.md#inline-image-1"]


def test_non_page_text_deliverables_are_byte_identical(tmp_path):
    results = _results(tmp_path)
    body = f"col\ndata:image/png;base64,{_BIG_B64}\n"
    for name in ("notes.txt", "blob.json", "rows.csv"):
        (results / name).write_text(body, encoding="utf-8")

    for name in ("notes.txt", "blob.json", "rows.csv"):
        marker = grading._deliverable_evidence_marker(results / name)
        assert marker == f"\n----- DELIVERABLE: {name} -----\n{body}"
        assert "[inline image" not in marker
