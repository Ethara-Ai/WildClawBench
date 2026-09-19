"""Fallback-chain coverage for the image/visual skill scripts.

Companion to the audio-side coverage of ``transcribe.sh``'s sidecar ->
local-whisper chain. The two image-side entry points shipped to agents used
to be single-backend and hard-failed the moment their one dependency was
missing:

  * environment/skills/video-frames/scripts/frame.sh
        ffmpeg only -> ``exit 127`` (bare "ffmpeg: command not found") when
        ffmpeg was not baked into the image.
  * environment/skills/pdf-extract/scripts/extract.py
        PyMuPDF only -> ``exit 2`` on ``ModuleNotFoundError: No module
        named 'fitz'`` when the offline wheelhouse install had not run.

Both now walk a chain, and these tests pin the ORDER of the rungs and the
hard-fail exit codes at the end of each chain:

    frame.sh    ffmpeg -> OpenCV (cv2)            -> exit 3 / exit 4
    extract.py  fitz -> pdfplumber -> pypdf -> pdftotext   -> exit 2
    extract.py  fitz -> pdfimages (images only)            -> exit 3

Everything runs OFFLINE and deterministically, and nothing here needs the
real ffmpeg/poppler binaries or the real PyMuPDF to be installed on the test
host:

  * Binary rungs are driven through stub shell scripts on ``PATH`` (and, for
    ffmpeg, through the script's own ``WCB_FFMPEG_BIN`` override, so the
    result does not depend on whether the host happens to ship ffmpeg).
  * Python-module rungs are driven through stub modules on ``PYTHONPATH``,
    which precedes site-packages -- so a stub that does ``raise
    ImportError`` reliably simulates "this package is absent" even on a host
    where the real package IS installed.
  * Each stub appends its name to a shared log file, which is what lets the
    tests assert the chain was walked in order rather than merely that some
    backend produced output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FRAME_SH = _REPO_ROOT / "environment" / "skills" / "video-frames" / "scripts" / "frame.sh"
_EXTRACT_PY = _REPO_ROOT / "environment" / "skills" / "pdf-extract" / "scripts" / "extract.py"

_MISSING_BIN = "/nonexistent/wcb-test/ffmpeg"


# ---------------------------------------------------------------------------
# Stub plumbing
# ---------------------------------------------------------------------------

def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_module(pylib: Path, name: str, body: str) -> Path:
    pylib.mkdir(parents=True, exist_ok=True)
    target = pylib / f"{name}.py"
    target.write_text(body, encoding="utf-8")
    return target


_ABSENT_MODULE = "raise ImportError('stubbed absent for test')\n"

# A cv2 good enough for frame.sh's probe and its extraction heredoc. Only the
# capture/write calls touch the log, so importing it during the probe does not
# make it look like the OpenCV rung actually ran.
_FAKE_CV2 = '''
import os

CAP_PROP_POS_MSEC = 0
CAP_PROP_POS_FRAMES = 1

_builtin_open = open


def _log(line):
    path = os.environ.get("FAKE_LOG")
    if path:
        with _builtin_open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\\n")


class VideoCapture:
    def __init__(self, path):
        _log("cv2.VideoCapture")
        self._ok = os.path.exists(path) and os.environ.get("FAKE_CV2_OPEN", "1") == "1"

    def isOpened(self):
        return self._ok

    def set(self, prop, value):
        _log("cv2.set %s %s" % (prop, value))
        return True

    def read(self):
        if os.environ.get("FAKE_CV2_READ", "1") != "1":
            return False, None
        return True, b"frame-bytes"

    def release(self):
        pass


def imwrite(dst, frame):
    _log("cv2.imwrite")
    if os.environ.get("FAKE_CV2_WRITE", "1") != "1":
        return False
    with _builtin_open(dst, "wb") as fh:
        fh.write(b"\\x89PNG\\r\\n\\x1a\\n fake frame")
    return True
'''

# A fitz good enough for BOTH halves of extract.py: the text chain
# (page_count / get_text) and the image chain (get_images / Pixmap.save).
_FAKE_FITZ = '''
import os

csRGB = "csRGB"

_builtin_open = open


def _log(line):
    path = os.environ.get("FAKE_LOG")
    if path:
        with _builtin_open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\\n")


class _Page:
    def __init__(self, i):
        self._i = i

    def get_text(self):
        return os.environ.get("FAKE_FITZ_TEXT", "")

    def get_images(self, full=False):
        return [(100 + self._i,)]


class _Doc:
    def __init__(self):
        self.page_count = int(os.environ.get("FAKE_PDF_PAGES", "2"))
        self._pages = [_Page(i) for i in range(self.page_count)]

    def __getitem__(self, i):
        return self._pages[i]

    def close(self):
        pass


class Pixmap:
    def __init__(self, a, b):
        self.n = 3
        self.alpha = 0

    def save(self, path):
        _log("fitz.Pixmap.save")
        with _builtin_open(str(path), "wb") as fh:
            fh.write(b"\\x89PNG fake")


def open(path, *a, **kw):
    _log("fitz.open")
    return _Doc()
'''


def _pdf_module(name: str, opener: str) -> str:
    """Body for a stub PDF text backend driven by FAKE_<NAME>_TEXT/FAKE_PDF_PAGES."""
    return f'''
import os

_builtin_open = open


def _log(line):
    path = os.environ.get("FAKE_LOG")
    if path:
        with _builtin_open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\\n")


def _pages():
    return int(os.environ.get("FAKE_PDF_PAGES", "2"))


def _body():
    return os.environ.get("FAKE_{name.upper()}_TEXT", "")


class _Page:
    def __init__(self, i):
        self._i = i

    def get_text(self):
        return _body()

    def extract_text(self):
        return _body()


class _Doc:
    def __init__(self):
        self.page_count = _pages()
        self.pages = [_Page(i) for i in range(self.page_count)]

    def __getitem__(self, i):
        return self.pages[i]

    def close(self):
        pass


def {opener}(path, *a, **kw):
    _log("{name}.{opener}")
    return _Doc()
'''


_FAKE_PDFPLUMBER = _pdf_module("pdfplumber", "open")
_FAKE_PYPDF = _pdf_module("pypdf", "PdfReader")


@pytest.fixture()
def sandbox(tmp_path: Path):
    """PATH/PYTHONPATH sandbox plus a log the stubs append to."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pylib = tmp_path / "pylib"
    pylib.mkdir()
    log = tmp_path / "chain.log"

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(pylib)
    env["FAKE_LOG"] = str(log)
    # Keep the host's real state from leaking into a rung under test.
    for key in ("WCB_VIDEO_FRAME_BACKEND", "WCB_FFMPEG_BIN", "WCB_PDF_BACKEND"):
        env.pop(key, None)

    class _Box:
        def __init__(self):
            self.bindir = bindir
            self.pylib = pylib
            self.env = env
            self.tmp = tmp_path

        def bin(self, name: str, body: str) -> Path:
            return _write_exec(bindir / name, body)

        def module(self, name: str, body: str) -> Path:
            return _write_module(pylib, name, body)

        def absent(self, *names: str) -> None:
            for name in names:
                _write_module(pylib, name, _ABSENT_MODULE)

        @property
        def chain(self):
            if not log.exists():
                return []
            return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln]

    return _Box()


def _run_frame(box, *args, **env_overrides):
    env = dict(box.env)
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        ["bash", str(_FRAME_SH), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _run_extract(box, *args, **env_overrides):
    env = dict(box.env)
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        [sys.executable, str(_EXTRACT_PY), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real stream")
    return path


@pytest.fixture()
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n% stub, backends are mocked\n")
    return path


_FFMPEG_OK = """#!/usr/bin/env bash
out="${@: -1}"
printf '\\xff\\xd8\\xff fake jpeg' > "$out"
echo "ffmpeg.ran" >> "$FAKE_LOG"
exit 0
"""

# ffmpeg's real behaviour for an out-of-range select=: success, no file.
_FFMPEG_SILENT = """#!/usr/bin/env bash
echo "ffmpeg.ran" >> "$FAKE_LOG"
exit 0
"""


# ---------------------------------------------------------------------------
# frame.sh -- ffmpeg -> OpenCV -> hard fail
# ---------------------------------------------------------------------------

class TestFrameShFallback:
    def test_ffmpeg_is_preferred_when_it_works(self, sandbox, video):
        ff = sandbox.bin("ffmpeg", _FFMPEG_OK)
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(sandbox, video, "--out", out, WCB_FFMPEG_BIN=ff)

        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == str(out)
        assert "== frame: ffmpeg" in res.stderr
        assert "== frame: opencv" not in res.stderr
        # cv2 was importable (the probe ran) but was never asked to decode.
        assert sandbox.chain == ["ffmpeg.ran"]

    def test_falls_back_to_opencv_when_ffmpeg_absent(self, sandbox, video):
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.png"

        res = _run_frame(sandbox, video, "--out", out, WCB_FFMPEG_BIN=_MISSING_BIN)

        assert res.returncode == 0, res.stderr
        assert "== frame: ffmpeg" not in res.stderr
        assert "== frame: opencv (cv2)" in res.stderr
        assert out.read_bytes().startswith(b"\x89PNG")
        assert "cv2.imwrite" in sandbox.chain

    def test_falls_back_when_ffmpeg_exits_zero_without_writing(self, sandbox, video):
        ff = sandbox.bin("ffmpeg", _FFMPEG_SILENT)
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(sandbox, video, "--out", out, WCB_FFMPEG_BIN=ff)

        assert res.returncode == 0, res.stderr
        assert "ffmpeg produced no frame" in res.stderr
        assert "falling back to OpenCV" in res.stderr
        assert sandbox.chain[0] == "ffmpeg.ran"
        assert "cv2.imwrite" in sandbox.chain
        assert out.read_bytes().startswith(b"\x89PNG")

    def test_opencv_rung_seeks_by_index(self, sandbox, video):
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.png"

        res = _run_frame(
            sandbox, video, "--index", "7", "--out", out, WCB_FFMPEG_BIN=_MISSING_BIN
        )

        assert res.returncode == 0, res.stderr
        assert "cv2.set 1 7" in sandbox.chain  # CAP_PROP_POS_FRAMES

    def test_opencv_rung_seeks_by_timestamp(self, sandbox, video):
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.png"

        res = _run_frame(
            sandbox, video, "--time", "00:01:30", "--out", out, WCB_FFMPEG_BIN=_MISSING_BIN
        )

        assert res.returncode == 0, res.stderr
        assert "cv2.set 0 90000.0" in sandbox.chain  # CAP_PROP_POS_MSEC

    def test_exit_3_when_no_decoder_at_all(self, sandbox, video):
        sandbox.absent("cv2")
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(sandbox, video, "--out", out, WCB_FFMPEG_BIN=_MISSING_BIN)

        assert res.returncode == 3
        assert "no usable video decoder backend" in res.stderr
        assert f"tried ffmpeg (no '{_MISSING_BIN}' on PATH)" in res.stderr
        assert "tried opencv (python3 -c 'import cv2' failed)" in res.stderr
        assert "apt-get install -y ffmpeg" in res.stderr
        assert "pip install opencv-python-headless" in res.stderr
        assert not out.exists()

    def test_exit_4_and_no_partial_output_when_every_backend_fails(self, sandbox, video):
        ff = sandbox.bin("ffmpeg", _FFMPEG_SILENT)
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(
            sandbox, video, "--out", out, WCB_FFMPEG_BIN=ff, FAKE_CV2_READ="0"
        )

        assert res.returncode == 4
        assert "every available backend failed to extract a frame" in res.stderr
        assert "OpenCV produced no frame" in res.stderr
        # The whole point: never leave the caller a half-written image.
        assert not out.exists()

    def test_backend_pin_ffmpeg_skips_the_opencv_rung(self, sandbox, video):
        ff = sandbox.bin("ffmpeg", _FFMPEG_SILENT)
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(
            sandbox, video, "--out", out,
            WCB_FFMPEG_BIN=ff, WCB_VIDEO_FRAME_BACKEND="ffmpeg",
        )

        assert res.returncode == 4
        assert "== frame: opencv" not in res.stderr
        assert "cv2.imwrite" not in sandbox.chain

    def test_backend_pin_opencv_skips_the_ffmpeg_rung(self, sandbox, video):
        sandbox.bin("ffmpeg", _FFMPEG_OK)
        sandbox.module("cv2", _FAKE_CV2)
        out = sandbox.tmp / "f.png"

        res = _run_frame(
            sandbox, video, "--out", out, WCB_VIDEO_FRAME_BACKEND="opencv"
        )

        assert res.returncode == 0, res.stderr
        assert "== frame: ffmpeg" not in res.stderr
        assert "ffmpeg.ran" not in sandbox.chain
        assert "cv2.imwrite" in sandbox.chain

    def test_unknown_backend_pin_is_a_usage_error(self, sandbox, video):
        out = sandbox.tmp / "f.jpg"

        res = _run_frame(sandbox, video, "--out", out, WCB_VIDEO_FRAME_BACKEND="magick")

        assert res.returncode == 2
        assert "must be auto, ffmpeg or opencv" in res.stderr

    def test_missing_input_still_exits_1_before_any_probe(self, sandbox):
        res = _run_frame(sandbox, sandbox.tmp / "nope.mp4", "--out", sandbox.tmp / "f.jpg")

        assert res.returncode == 1
        assert "File not found" in res.stderr


# ---------------------------------------------------------------------------
# extract.py -- fitz -> pdfplumber -> pypdf -> pdftotext
# ---------------------------------------------------------------------------

_PDFINFO_OK = """#!/usr/bin/env bash
echo "pdfinfo.ran" >> "$FAKE_LOG"
echo "Pages:          2"
exit 0
"""

_PDFTOTEXT_OK = """#!/usr/bin/env bash
echo "pdftotext.ran" >> "$FAKE_LOG"
printf '%s\\n' "poppler recovered text"
exit 0
"""

_PDFIMAGES_OK = """#!/usr/bin/env bash
echo "pdfimages.ran" >> "$FAKE_LOG"
prefix="${!#}"
printf '\\x89PNG fake' > "${prefix}-000.png"
exit 0
"""


class TestExtractPyTextFallback:
    def test_fitz_is_preferred_when_it_works(self, sandbox, pdf):
        sandbox.module("fitz", _FAKE_FITZ)
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)

        res = _run_extract(sandbox, pdf, FAKE_FITZ_TEXT="from pymupdf")

        assert res.returncode == 0, res.stderr
        assert "from pymupdf" in res.stdout
        assert "via fitz" in res.stderr
        assert sandbox.chain == ["fitz.open"]

    def test_falls_back_to_pdfplumber_when_fitz_absent(self, sandbox, pdf):
        sandbox.absent("fitz")
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)

        res = _run_extract(sandbox, pdf, FAKE_PDFPLUMBER_TEXT="from pdfplumber")

        assert res.returncode == 0, res.stderr
        assert "from pdfplumber" in res.stdout
        assert "PyMuPDF unusable; using the pdfplumber backend" in res.stderr
        assert "via pdfplumber" in res.stderr
        assert sandbox.chain == ["pdfplumber.open"]

    def test_falls_back_to_pypdf_when_fitz_and_pdfplumber_absent(self, sandbox, pdf):
        sandbox.absent("fitz", "pdfplumber")
        sandbox.module("pypdf", _FAKE_PYPDF)

        res = _run_extract(sandbox, pdf, FAKE_PYPDF_TEXT="from pypdf")

        assert res.returncode == 0, res.stderr
        assert "from pypdf" in res.stdout
        assert "via pypdf" in res.stderr
        assert sandbox.chain == ["pypdf.PdfReader"]

    def test_falls_back_to_poppler_when_every_python_backend_absent(self, sandbox, pdf):
        sandbox.absent("fitz", "pdfplumber", "pypdf")
        sandbox.bin("pdfinfo", _PDFINFO_OK)
        sandbox.bin("pdftotext", _PDFTOTEXT_OK)

        res = _run_extract(sandbox, pdf)

        assert res.returncode == 0, res.stderr
        assert "poppler recovered text" in res.stdout
        assert "via pdftotext" in res.stderr
        assert "pages: 2 (extracted 2" in res.stderr
        assert sandbox.chain.count("pdftotext.ran") == 2  # once per page

    def test_backend_that_recovers_nothing_yields_to_the_next_rung(self, sandbox, pdf):
        sandbox.module("fitz", _FAKE_FITZ)
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)

        res = _run_extract(
            sandbox, pdf, FAKE_FITZ_TEXT="", FAKE_PDFPLUMBER_TEXT="recovered downstream"
        )

        assert res.returncode == 0, res.stderr
        assert "recovered downstream" in res.stdout
        assert "via pdfplumber" in res.stderr
        # fitz was tried FIRST and skipped only because it produced nothing.
        assert sandbox.chain == ["fitz.open", "pdfplumber.open"]

    def test_exit_2_when_no_text_backend_exists(self, sandbox, pdf):
        sandbox.absent("fitz", "pdfplumber", "pypdf")

        res = _run_extract(sandbox, pdf)

        assert res.returncode == 2
        assert "no usable PDF text backend" in res.stderr
        for name in ("fitz", "pdfplumber", "pypdf", "pdftotext"):
            assert f"tried {name} (not installed)" in res.stderr
        assert "pip install pymupdf" in res.stderr
        assert "apt-get install -y poppler-utils" in res.stderr

    def test_all_backends_empty_warns_loudly_but_still_emits_pages(self, sandbox, pdf):
        sandbox.module("fitz", _FAKE_FITZ)
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)
        sandbox.module("pypdf", _FAKE_PYPDF)

        res = _run_extract(sandbox, pdf)

        assert res.returncode == 0, res.stderr
        assert "===== page 1 =====" in res.stdout
        assert "WARNING - no backend recovered any text" in res.stderr
        assert "tried fitz (0 characters recovered)" in res.stderr
        assert "tried pdfplumber (0 characters recovered)" in res.stderr
        assert "OCR them" in res.stderr

    def test_backend_pin_restricts_the_chain_to_one_rung(self, sandbox, pdf):
        sandbox.module("fitz", _FAKE_FITZ)
        sandbox.module("pypdf", _FAKE_PYPDF)

        res = _run_extract(
            sandbox, pdf,
            FAKE_FITZ_TEXT="from pymupdf",
            FAKE_PYPDF_TEXT="from pypdf",
            WCB_PDF_BACKEND="pypdf",
        )

        assert res.returncode == 0, res.stderr
        assert "from pypdf" in res.stdout
        assert "from pymupdf" not in res.stdout
        assert "pinned by WCB_PDF_BACKEND; using the pypdf backend" in res.stderr
        assert sandbox.chain == ["pypdf.PdfReader"]

    def test_backend_pin_to_a_missing_rung_reports_the_pin(self, sandbox, pdf):
        sandbox.module("fitz", _FAKE_FITZ)
        sandbox.absent("pdfplumber")

        res = _run_extract(sandbox, pdf, WCB_PDF_BACKEND="pdfplumber")

        assert res.returncode == 2
        assert "tried pdfplumber (not installed)" in res.stderr
        assert "WCB_PDF_BACKEND pinned this run to 'pdfplumber'" in res.stderr

    def test_unknown_backend_pin_exits_4(self, sandbox, pdf):
        res = _run_extract(sandbox, pdf, WCB_PDF_BACKEND="imagemagick")

        assert res.returncode == 4
        assert "WCB_PDF_BACKEND='imagemagick' is not one of" in res.stderr

    def test_missing_pdf_still_exits_1(self, sandbox):
        res = _run_extract(sandbox, sandbox.tmp / "nope.pdf")

        assert res.returncode == 1
        assert "not found:" in res.stderr


# ---------------------------------------------------------------------------
# extract.py -- fitz -> pdfimages (embedded images)
# ---------------------------------------------------------------------------

class TestExtractPyImageFallback:
    def test_images_fall_back_to_pdfimages_when_fitz_absent(self, sandbox, pdf):
        sandbox.absent("fitz")
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)
        sandbox.bin("pdfimages", _PDFIMAGES_OK)
        imgs = sandbox.tmp / "imgs"

        res = _run_extract(
            sandbox, pdf, "--images-dir", imgs, FAKE_PDFPLUMBER_TEXT="text ok"
        )

        assert res.returncode == 0, res.stderr
        assert "fitz image backend unavailable" in res.stderr
        assert "(via pdfimages)" in res.stderr
        assert "images: 1 ->" in res.stderr
        assert (imgs / "p-000.png").exists()
        assert "pdfimages.ran" in sandbox.chain

    def test_exit_3_when_no_image_backend_exists(self, sandbox, pdf):
        sandbox.absent("fitz")
        sandbox.module("pdfplumber", _FAKE_PDFPLUMBER)
        imgs = sandbox.tmp / "imgs"

        res = _run_extract(
            sandbox, pdf, "--images-dir", imgs, FAKE_PDFPLUMBER_TEXT="text ok"
        )

        assert res.returncode == 3
        assert "no usable PDF image backend" in res.stderr
        assert "Tried PyMuPDF (import fitz) and pdfimages (poppler-utils)" in res.stderr
        # Page renders must NOT be silently substituted for embedded images.
        assert "do not" in res.stderr and "substitute" in res.stderr

    def test_fitz_is_preferred_for_images_when_available(self, sandbox, pdf):
        sandbox.bin("pdfimages", _PDFIMAGES_OK)
        sandbox.module("fitz", _FAKE_FITZ)
        imgs = sandbox.tmp / "imgs"

        res = _run_extract(
            sandbox, pdf, "--images-dir", imgs, FAKE_FITZ_TEXT="from pymupdf"
        )

        assert res.returncode == 0, res.stderr
        assert "(via fitz)" in res.stderr
        assert "pdfimages.ran" not in sandbox.chain
