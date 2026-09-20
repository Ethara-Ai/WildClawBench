"""Judge image evidence: what gets attached, what is refused, and why.

One invalid image part fails the WHOLE vision request (HTTP 400) and with it every
criterion of the chunk, so images are validated before they are attached, the
request is retried as text if the endpoint still objects, and every picture the
judge cannot see says so in its placeholder. On the other side, pictures that
were silently invisible — a chart.png deliverable, `<img src="chart.png">`, a
chart pasted into a .pptx — now reach an image-capable judge.
"""
from __future__ import annotations

import base64
import io
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import grading  # noqa: E402

Image = pytest.importorskip("PIL.Image")


def _img_bytes(side=(32, 24), color=(200, 30, 30), fmt="PNG", mode="RGB", noise=False) -> bytes:
    if noise:
        import random
        rnd = random.Random(7)
        img = Image.frombytes("RGB", side, bytes(rnd.randrange(256) for _ in range(side[0] * side[1] * 3)))
    else:
        img = Image.new(mode, side, color if mode == "RGB" else color + (128,))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _uri(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _decode(part) -> "Image.Image":
    return Image.open(io.BytesIO(base64.b64decode(part.data_uri.partition(",")[2])))


@pytest.fixture(autouse=True)
def _default_caps(monkeypatch):
    for k in ("KENSEI_JUDGE_MAX_IMAGES", "KENSEI_JUDGE_MAX_IMAGE_BYTES",
              "KENSEI_JUDGE_IMAGE_DETAIL", "WCB_JUDGE_OFFICE_MAX_IMAGES"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "task_output" / "artifacts"
    r.mkdir(parents=True)
    return r


# --------------------------------------------------------------------------- #
# Fix 1 — validation / normalisation
# --------------------------------------------------------------------------- #
class TestPrepareJudgeImage:
    def test_a_valid_png_is_attached_byte_for_byte(self):
        raw = _img_bytes()
        part, reason, dims = grading._prepare_judge_image(raw, "a#1")
        assert reason == "" and dims == (32, 24)
        assert part.mime == "image/png"
        assert base64.b64decode(part.data_uri.partition(",")[2]) == raw

    @pytest.mark.parametrize("side", [(1, 1), (7, 40), (40, 7)])
    def test_a_degenerate_raster_is_refused(self, side):
        part, reason, _ = grading._prepare_judge_image(_img_bytes(side=side), "a#1")
        assert part is None
        assert "below the 8 px minimum" in reason

    @pytest.mark.parametrize("raw", [b"", b"not an image at all", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
                                     b"<svg xmlns='http://www.w3.org/2000/svg'/>"])
    def test_undecodable_bytes_are_refused_not_raised(self, raw):
        part, reason, _ = grading._prepare_judge_image(raw, "a#1")
        assert part is None and reason

    def test_a_wrong_declared_mime_is_corrected_to_the_real_format(self):
        body = f'<img src="{_uri(_img_bytes(fmt="JPEG"), mime="image/png")}">'
        text, images = grading._extract_inline_images(body, "p.html")
        assert images[0].mime == "image/jpeg"
        assert images[0].data_uri.startswith("data:image/jpeg;base64,")
        assert "[inline image p.html#1, image/jpeg," in text

    def test_an_unsupported_but_decodable_format_is_reencoded(self):
        part, reason, _ = grading._prepare_judge_image(_img_bytes(fmt="BMP"), "a#1")
        assert reason == "" and part.mime in ("image/png", "image/jpeg")
        assert _decode(part).size == (32, 24)

    def test_an_oversized_image_is_downscaled_not_dropped(self):
        raw = _img_bytes(side=(3000, 1500))
        part, reason, dims = grading._prepare_judge_image(raw, "a#1")
        assert reason == "" and dims == (3000, 1500)
        assert max(_decode(part).size) <= grading._JUDGE_IMAGE_MAX_SIDE
        assert grading._data_uri_b64_len(part.data_uri) <= grading._JUDGE_IMAGE_MAX_B64

    def test_a_heavy_image_is_recompressed_under_the_per_image_cap(self):
        raw = _img_bytes(side=(1400, 1400), noise=True)  # incompressible PNG, > cap
        assert len(raw) * 4 // 3 > grading._JUDGE_IMAGE_MAX_B64
        part, reason, _ = grading._prepare_judge_image(raw, "a#1")
        assert reason == ""
        assert grading._data_uri_b64_len(part.data_uri) <= grading._JUDGE_IMAGE_MAX_B64

    def test_transparency_survives_a_png_reencode(self):
        raw = _img_bytes(side=(2600, 50), mode="RGBA", color=(10, 20, 30))
        part, _, _ = grading._prepare_judge_image(raw, "a#1")
        assert part.mime == "image/png" and _decode(part).mode == "RGBA"


class TestInlineExtractionRefusals:
    def test_bad_blob_is_lifted_out_of_the_text_but_never_attached(self):
        bad = "QUFB" * 40
        body = f'<p>x</p><img src="data:image/png;base64,{bad}"><p>y</p>'
        text, images = grading._extract_inline_images(body, "p.html")
        assert images == []
        assert bad not in text and "base64," not in text
        assert ("[inline image p.html#1, image/png; present — contents not included: "
                "not a decodable raster image]") in text

    def test_svg_data_uri_is_refused_as_not_a_raster(self):
        svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg' width='90' height='90'>"
                               b"<rect width='90' height='90'/></svg>").decode()
        text, images = grading._extract_inline_images(
            f'<img src="data:image/svg+xml;base64,{svg}">', "p.html")
        assert images == [] and "not a decodable raster image" in text

    def test_one_bad_image_does_not_cost_the_good_ones_and_labels_stay_positional(self):
        good = _uri(_img_bytes())
        body = f'<img src="data:image/png;base64,{"QUFB" * 40}"><img src="{good}">'
        text, images = grading._extract_inline_images(body, "p.html")
        assert [i.label for i in images] == ["p.html#2"]
        assert "p.html#1, image/png; present — contents not included" in text

    def test_conversation_log_images_disclose_that_they_are_unattached(self, root):
        (root / "a.md").write_text("report", encoding="utf-8")
        transcript = f"[toolResult] {_uri(_img_bytes())}\n[FINAL ASSISTANT MESSAGE] done"
        payload = grading._gather_evidence(root, transcript, budget=None)
        assert payload.images == []
        assert ("[inline image transcript#1, image/png; present — contents not included: "
                "images inside the conversation log are not attached]") in payload.text


# --------------------------------------------------------------------------- #
# Fix 1 — retry without images
# --------------------------------------------------------------------------- #
class TestRetryWithoutImages:
    def _payload(self):
        return grading.JudgeUserPayload(text="evidence", images=[grading.ImagePart(
            data_uri=_uri(_img_bytes()), mime="image/png", detail="auto", label="c.png#image")])

    def _http(self, code, body=b'{"error":{"message":"Invalid image data"}}'):
        return urllib.error.HTTPError("http://x", code, "bad", {}, io.BytesIO(body))

    def test_an_image_rejection_is_retried_once_as_text(self, monkeypatch):
        calls = []

        def once(model, system, user, **kw):
            calls.append(user)
            if len(calls) == 1:
                raise self._http(400)
            return "verdicts", {}
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        assert grading._call_judge_openai("m", "s", self._payload())[0] == "verdicts"
        assert isinstance(calls[0], grading.JudgeUserPayload)
        assert isinstance(calls[1], str)
        assert "NOT attached: c.png#image" in calls[1]
        assert "present — contents not included" in calls[1]

    @pytest.mark.parametrize("exc", [
        urllib.error.HTTPError("http://x", 401, "unauthorized", {}, io.BytesIO(b"{}")),
        urllib.error.HTTPError("http://x", 400, "bad", {},
                               io.BytesIO(b'{"error":{"message":"context_length_exceeded"}}')),
        RuntimeError("judge stream error: rate limit"),
        TimeoutError("read timed out"),
    ])
    def test_a_non_image_failure_is_not_retried(self, monkeypatch, exc):
        calls = []

        def once(*a, **k):
            calls.append(1)
            raise exc
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        with pytest.raises(type(exc)):
            grading._call_judge_openai("m", "s", self._payload())
        assert len(calls) == 1

    def test_a_text_only_request_is_never_retried(self, monkeypatch):
        calls = []

        def once(*a, **k):
            calls.append(1)
            raise self._http(400)
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        with pytest.raises(urllib.error.HTTPError):
            grading._call_judge_openai("m", "s", "plain text")
        assert len(calls) == 1

    def test_a_stream_error_about_an_image_is_retried(self, monkeypatch):
        calls = []

        def once(model, system, user, **kw):
            calls.append(user)
            if len(calls) == 1:
                raise RuntimeError("judge stream error: invalid image url")
            return "ok", {}
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        assert grading._call_judge_openai("m", "s", self._payload())[0] == "ok"


# --------------------------------------------------------------------------- #
# Fix 2 — standalone image files
# --------------------------------------------------------------------------- #
class TestStandaloneImageFiles:
    def test_an_image_deliverable_is_attached_on_an_image_capable_judge(self, root):
        raw = _img_bytes(side=(64, 48))
        (root / "chart.png").write_bytes(raw)
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["chart.png#image"]
        assert base64.b64decode(payload.images[0].data_uri.partition(",")[2]) == raw
        assert "----- DELIVERABLE: chart.png (image 64x48, attached) -----" in payload.text
        assert "[inline image chart.png#image, image/png," in payload.text

    def test_a_text_only_judge_keeps_the_presence_marker(self, root):
        (root / "chart.png").write_bytes(_img_bytes(side=(64, 48)))
        payload = grading._gather_evidence(root, "T", budget=None, attach_images=False)
        assert payload.images == []
        assert "image pixels are not attached for standalone image files (image 64x48)" in payload.text

    def test_a_broken_image_file_says_why_it_is_not_attached(self, root):
        (root / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk" * 20)
        payload = grading._gather_evidence(root, "T", budget=None)
        assert payload.images == []
        assert "image not attached: not a decodable raster image" in payload.text
        assert "present — contents not included" in payload.text

    def test_rubric_named_images_win_the_count_cap(self, root, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "1")
        (root / "aaa.png").write_bytes(_img_bytes(color=(1, 2, 3)))
        (root / "final_chart.png").write_bytes(_img_bytes(color=(9, 9, 9)))
        payload = grading._gather_evidence(
            root, "T", budget=None, rubric_names=frozenset({"final_chart.png"}))
        assert [i.label for i in payload.images] == ["final_chart.png#image"]
        assert "aaa.png#image, image/png" in payload.text
        assert "judge image count limit reached" in payload.text

    def test_the_marker_function_default_is_unchanged_for_other_callers(self, root):
        f = root / "chart.png"
        f.write_bytes(_img_bytes())
        block, images = grading._deliverable_evidence_marker(f, "chart.png")
        assert images == [] and "image pixels are not attached" in block


# --------------------------------------------------------------------------- #
# Fix 3 — relative <img src> / ![](...)
# --------------------------------------------------------------------------- #
class TestLinkedImages:
    def test_html_and_markdown_references_are_attached_in_place(self, root):
        (root / "charts").mkdir()
        (root / "charts" / "q1.png").write_bytes(_img_bytes(color=(1, 1, 200)))
        (root / "q2.jpg").write_bytes(_img_bytes(color=(1, 200, 1), fmt="JPEG"))
        (root / "report.html").write_text(
            '<h1>Q</h1><img alt="a" src="charts/q1.png"><p>tail</p>', encoding="utf-8")
        (root / "notes.md").write_text("intro ![second](q2.jpg) outro", encoding="utf-8")
        payload = grading._gather_evidence(root, "T", budget=None)
        text = payload.text
        assert '<img alt="a" src="charts/q1.png"> [inline image report.html#ref1, image/png,' in text
        assert "(the file charts/q1.png)" in text
        assert "![second](q2.jpg) [inline image notes.md#ref1, image/jpeg," in text

    def test_a_file_that_is_also_a_deliverable_is_attached_once(self, root):
        (root / "chart.png").write_bytes(_img_bytes())
        (root / "report.html").write_text('<img src="chart.png">', encoding="utf-8")
        payload = grading._gather_evidence(root, "T", budget=None)
        assert len(payload.images) == 1
        attached = payload.images[0].label
        other = ({"chart.png#image", "report.html#ref1"} - {attached}).pop()
        assert f"{other}, image/png, " in payload.text
        assert f"present — contents not included: same image as {attached}, attached once" in payload.text

    @pytest.mark.parametrize("src", [
        "../secret.png", "/etc/passwd.png", "http://evil.example/x.png",
        "https://cdn.example/x.png", "file:///tmp/x.png", "missing.png", "notes.txt"])
    def test_escapes_urls_and_missing_files_are_ignored(self, root, src):
        (root.parent / "secret.png").write_bytes(_img_bytes())
        (root / "notes.txt").write_text("x", encoding="utf-8")
        (root / "report.html").write_text(f'<img src="{src}">', encoding="utf-8")
        block, images = grading._deliverable_evidence_marker(
            root / "report.html", "report.html", attach_images=True, image_root=root)
        assert images == [] and "[inline image" not in block

    def test_repeated_reference_is_read_once(self, root):
        (root / "logo.png").write_bytes(_img_bytes())
        (root / "page.html").write_text('<img src="logo.png"><img src="logo.png">', encoding="utf-8")
        block, images = grading._deliverable_evidence_marker(
            root / "page.html", "page.html", attach_images=True, image_root=root)
        assert [i.label for i in images] == ["page.html#ref1"]
        assert "[same image as page.html#ref1]" in block

    def test_plain_text_files_are_not_scanned(self, root):
        (root / "logo.png").write_bytes(_img_bytes())
        (root / "dump.txt").write_text('<img src="logo.png">', encoding="utf-8")
        _, images = grading._deliverable_evidence_marker(
            root / "dump.txt", "dump.txt", attach_images=True, image_root=root)
        assert images == []


# --------------------------------------------------------------------------- #
# Fix 4 — pictures embedded in office documents
# --------------------------------------------------------------------------- #
def _write_pptx(path: Path, media: dict[str, bytes], text: str = "Revenue by quarter") -> None:
    slide = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
             f'<p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>'
             '</p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/slides/slide1.xml", slide)
        for name, data in media.items():
            zf.writestr(f"ppt/media/{name}", data)


class TestOfficeMedia:
    def test_pptx_pictures_are_attached_alongside_the_extracted_text(self, root):
        _write_pptx(root / "deck.pptx", {"image1.png": _img_bytes(color=(5, 5, 250)),
                                         "image2.jpeg": _img_bytes(color=(250, 5, 5), fmt="JPEG")})
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["deck.pptx#image1.png", "deck.pptx#image2.jpeg"]
        assert "Revenue by quarter" in payload.text
        assert "(picture embedded in the document: ppt/media/image1.png)" in payload.text

    def test_vector_and_broken_members_are_named_not_attached(self, root):
        _write_pptx(root / "deck.pptx", {"image1.emf": b"\x01\x00\x00\x00EMFDATA" * 8,
                                         "image2.png": _img_bytes()})
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["deck.pptx#image2.png"]
        assert ("[inline image deck.pptx#image1.emf, image; present — contents not included: "
                "not a decodable raster image]") in payload.text

    def test_per_document_cap_is_disclosed(self, root, monkeypatch):
        monkeypatch.setenv("WCB_JUDGE_OFFICE_MAX_IMAGES", "2")
        _write_pptx(root / "deck.pptx",
                    {f"image{i}.png": _img_bytes(color=(i * 20, 0, 0)) for i in range(1, 6)})
        payload = grading._gather_evidence(root, "T", budget=None)
        assert len(payload.images) == 2
        assert "(3 more embedded picture(s) not shown: per-document limit of 2)" in payload.text

    def test_cap_zero_disables_office_media(self, root, monkeypatch):
        monkeypatch.setenv("WCB_JUDGE_OFFICE_MAX_IMAGES", "0")
        _write_pptx(root / "deck.pptx", {"image1.png": _img_bytes()})
        assert grading._gather_evidence(root, "T", budget=None).images == []

    def test_a_text_only_judge_gets_no_office_media_work(self, root):
        _write_pptx(root / "deck.pptx", {"image1.png": _img_bytes()})
        payload = grading._gather_evidence(root, "T", budget=None, attach_images=False)
        assert payload.images == [] and "[inline image" not in payload.text

    def test_a_picture_only_document_still_yields_a_block(self, root):
        with zipfile.ZipFile(root / "scan.docx", "w") as zf:
            zf.writestr("word/document.xml",
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                        'wordprocessingml/2006/main"><w:body/></w:document>')
            zf.writestr("word/media/image1.png", _img_bytes())
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["scan.docx#image1.png"]

    def test_a_corrupt_zip_never_raises(self, root):
        (root / "deck.pptx").write_bytes(b"PK\x03\x04 not really a zip")
        assert grading._office_media_images(root / "deck.pptx", "deck.pptx") == ([], "")


# --------------------------------------------------------------------------- #
# Invariants that must survive all of the above
# --------------------------------------------------------------------------- #
class TestInvariants:
    @pytest.mark.parametrize("budget", [400, 900, 2500, 20_000])
    def test_text_stays_within_budget_and_every_attached_image_is_named(self, root, budget):
        (root / "chart.png").write_bytes(_img_bytes())
        (root / "report.html").write_text(
            "x" * 3000 + f'<img src="{_uri(_img_bytes(color=(3, 3, 3)))}"><img src="chart.png">',
            encoding="utf-8")
        _write_pptx(root / "deck.pptx", {"image1.png": _img_bytes(color=(7, 7, 7))})
        payload = grading._gather_evidence(root, "TRANSCRIPT " * 40, budget=budget)
        assert len(payload.text) <= budget
        for img in payload.images:
            assert grading._image_placeholder_prefix(img.label) in payload.text

    def test_every_attached_image_is_a_decodable_supported_raster(self, root):
        (root / "a.bmp").write_bytes(_img_bytes(fmt="BMP"))
        (root / "b.png").write_bytes(_img_bytes(side=(2500, 40)))
        (root / "c.html").write_text(
            f'<img src="data:image/png;base64,{"QUFB" * 40}">'
            f'<img src="{_uri(_img_bytes(side=(1, 1)))}">', encoding="utf-8")
        payload = grading._gather_evidence(root, "T", budget=None)
        assert payload.images
        for img in payload.images:
            pic = _decode(img)
            assert pic.format in grading._JUDGE_IMAGE_FORMATS
            assert min(pic.size) >= grading._JUDGE_IMAGE_MIN_SIDE
            assert max(pic.size) <= grading._JUDGE_IMAGE_MAX_SIDE
            assert img.mime == grading._JUDGE_IMAGE_FORMATS[pic.format]

    def test_total_attachment_respects_the_request_caps(self, root, monkeypatch):
        for i in range(12):
            (root / f"f{i:02d}.png").write_bytes(_img_bytes(color=(i * 10, 1, 1)))
        payload = grading._gather_evidence(root, "T", budget=None)
        assert len(payload.images) == grading._judge_max_images() == 8
        assert payload.text.count("judge image count limit reached") == 4


# --------------------------------------------------------------------------- #
# Fix 6 — the codex image probe
# --------------------------------------------------------------------------- #
class TestImageProbeRetry:
    @pytest.fixture(autouse=True)
    def _codex(self, monkeypatch):
        monkeypatch.setattr(grading, "_judge_codex_bridge_url", lambda: "http://127.0.0.1:9")
        monkeypatch.setattr(grading, "_judge_codex_bridge_secret", lambda: "s")
        monkeypatch.setattr(grading, "_judge_codex_bridge_model", lambda: "gpt-5.6-sol")
        monkeypatch.setattr(grading, "_call_judge_openai", lambda *a, **k: ("OK", {}))
        monkeypatch.setattr(grading.time, "sleep", lambda s: None)

    def test_a_transient_failure_is_retried_before_images_are_disabled(self, monkeypatch):
        calls = []

        def once(*a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("read timed out")
            return "OK", {}
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        ok, detail = grading.preflight_judge_codex()
        assert ok == "ok" and len(calls) == 3

    def test_a_persistent_failure_keeps_the_image_probe_contract(self, monkeypatch):
        def once(*a, **k):
            raise TimeoutError("read timed out")
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail" and detail.startswith("image probe:")
        assert "after 3 attempt(s)" in detail

    def test_the_probe_bypasses_the_retry_without_images_wrapper(self, monkeypatch):
        # Through the wrapper a dead image leg would be retried as TEXT and pass.
        def once(model, system, user, **k):
            if isinstance(user, grading.JudgeUserPayload):
                raise urllib.error.HTTPError("http://x", 400, "bad", {}, io.BytesIO(b"invalid image"))
            return "OK", {}
        monkeypatch.setattr(grading, "_call_judge_openai_once", once)
        monkeypatch.setenv("WCB_JUDGE_IMAGE_PROBE_RETRIES", "0")
        ok, detail = grading.preflight_judge_codex()
        assert ok == "fail" and detail.startswith("image probe:")

    def test_retries_are_bounded_and_tunable(self, monkeypatch):
        monkeypatch.setenv("WCB_JUDGE_IMAGE_PROBE_RETRIES", "99")
        assert grading._judge_image_probe_retries() == 5
        monkeypatch.setenv("WCB_JUDGE_IMAGE_PROBE_RETRIES", "x")
        assert grading._judge_image_probe_retries() == 2


# --------------------------------------------------------------------------- #
# Who gets the per-request cap (found on real runs: 60 scratch crops ate all 8
# slots while the deliverable's own pictures went unattached)
# --------------------------------------------------------------------------- #
class TestCapGoesToDeliverablesNotScratch:
    def test_agent_working_images_are_never_attached(self, root):
        (root / "_scratch" / "pdf").mkdir(parents=True)
        (root / "_scratch" / "pdf" / "pg1.png").write_bytes(_img_bytes(color=(1, 1, 1)))
        (root / "_pen_raw.png").write_bytes(_img_bytes(color=(2, 2, 2)))
        (root / ".cache.png").write_bytes(_img_bytes(color=(3, 3, 3)))
        (root / "chart.png").write_bytes(_img_bytes(color=(4, 4, 4)))
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["chart.png#image"]
        assert "image not attached: agent working file (scratch)" in payload.text

    def test_a_page_that_shows_a_scratch_image_still_gets_it(self, root):
        (root / "_ev").mkdir()
        (root / "_ev" / "badge.png").write_bytes(_img_bytes())
        (root / "slate.html").write_text('<img src="_ev/badge.png">', encoding="utf-8")
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["slate.html#ref1"]

    def test_a_rubric_named_underscore_image_is_a_deliverable(self, root):
        (root / "_final.png").write_bytes(_img_bytes())
        payload = grading._gather_evidence(
            root, "T", budget=None, rubric_names=frozenset({"_final.png"}))
        assert [i.label for i in payload.images] == ["_final.png#image"]

    def test_document_pictures_outrank_loose_image_files(self, root, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "2")
        for i in range(3):
            (root / f"a{i}.png").write_bytes(_img_bytes(color=(i + 1, 0, 0)))
        (root / "report.html").write_text(
            "x" * 5000 + f'<img src="{_uri(_img_bytes(color=(0, 9, 0)))}">'
            f'<img src="{_uri(_img_bytes(color=(0, 0, 9)))}">', encoding="utf-8")
        payload = grading._gather_evidence(root, "T", budget=None)
        assert [i.label for i in payload.images] == ["report.html#1", "report.html#2"]

    def test_rubric_named_files_outrank_everything(self, root, monkeypatch):
        monkeypatch.setenv("KENSEI_JUDGE_MAX_IMAGES", "1")
        (root / "report.html").write_text(
            f'<img src="{_uri(_img_bytes(color=(0, 9, 0)))}">', encoding="utf-8")
        (root / "zz_final.png").write_bytes(_img_bytes(color=(9, 9, 9)))
        payload = grading._gather_evidence(
            root, "T", budget=None, rubric_names=frozenset({"zz_final.png"}))
        assert [i.label for i in payload.images] == ["zz_final.png#image"]
