#!/usr/bin/env python3
"""Extract text (and optionally embedded images) from a PDF.

Backend fallback chain
----------------------
Text is recovered by the first backend that both imports and yields
characters::

    PyMuPDF (fitz) -> pdfplumber -> pypdf -> pdftotext (poppler-utils)

Embedded images are extracted by::

    PyMuPDF (fitz) -> pdfimages (poppler-utils)

PyMuPDF stays the preferred backend everywhere: it is the fastest and the
only one that returns text and images from a single open document. The other
rungs exist because the agent container is not guaranteed to have it -- the
offline wheelhouse install can fail and older images predate it, and the
observed failure was a bare ``ModuleNotFoundError: No module named 'fitz'``
that ended the run with no output at all. Rather than hard-fail we walk down
to whatever the image does ship; poppler's CLI tools come from the offline
debhouse and need no Python package.

A backend that imports but recovers zero characters is not accepted while
another rung remains: pdfminer (under pdfplumber) returns empty strings for
some producer-specific encodings that poppler reads fine. If every rung comes
back empty we still emit the page skeleton -- an image-only scan genuinely
has no text layer -- but say so loudly on stderr instead of pretending the
extraction worked.

Page renders are deliberately NOT used as a fallback for embedded images:
``pdftoppm`` output is a different artifact, and substituting it would be
silently returning the wrong thing.

Set ``WCB_PDF_BACKEND`` to pin a single text rung
(``fitz``|``pdfplumber``|``pypdf``|``pdftotext``) for debugging; the default
``auto`` walks the chain.

Exit codes:
    0  extraction succeeded
    1  input PDF not found
    2  no text backend available at all
    3  --images-dir requested but no image backend available
    4  WCB_PDF_BACKEND names an unknown backend
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional


def _page_range(spec: str, n: int) -> range:
    if not spec:
        return range(n)
    a, _, b = spec.partition("-")
    start = max(1, int(a)) - 1
    end = int(b) if b else int(a)
    return range(start, min(end, n))


class _TextBackend(NamedTuple):
    """A resolved PDF text source: page count, per-page text, teardown."""

    name: str
    page_count: int
    page_text: Callable[[int], str]
    close: Callable[[], None]


class _Extraction(NamedTuple):
    backend: str
    page_count: int
    pages: List[int]
    text: str
    recovered: int


def _open_fitz(src: Path) -> Optional[_TextBackend]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    doc = fitz.open(src)
    return _TextBackend("fitz", doc.page_count, lambda i: doc[i].get_text(), doc.close)


def _open_pdfplumber(src: Path) -> Optional[_TextBackend]:
    try:
        import pdfplumber
    except ImportError:
        return None
    doc = pdfplumber.open(str(src))
    return _TextBackend(
        "pdfplumber",
        len(doc.pages),
        lambda i: doc.pages[i].extract_text() or "",
        doc.close,
    )


def _open_pypdf(src: Path) -> Optional[_TextBackend]:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore[no-redef]
        except ImportError:
            return None
    reader = PdfReader(str(src))
    return _TextBackend(
        "pypdf",
        len(reader.pages),
        lambda i: reader.pages[i].extract_text() or "",
        lambda: None,
    )


def _open_pdftotext(src: Path) -> Optional[_TextBackend]:
    if shutil.which("pdftotext") is None or shutil.which("pdfinfo") is None:
        return None
    info = subprocess.run(
        ["pdfinfo", str(src)], capture_output=True, text=True, check=False
    )
    if info.returncode != 0:
        return None
    count = 0
    for line in info.stdout.splitlines():
        if line.startswith("Pages:"):
            count = int(line.split(":", 1)[1].strip())
            break
    if count <= 0:
        return None

    def page_text(i: int) -> str:
        n = str(i + 1)
        out = subprocess.run(
            ["pdftotext", "-f", n, "-l", n, str(src), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout if out.returncode == 0 else ""

    return _TextBackend("pdftotext", count, page_text, lambda: None)


_TEXT_OPENERS = (
    ("fitz", _open_fitz),
    ("pdfplumber", _open_pdfplumber),
    ("pypdf", _open_pypdf),
    ("pdftotext", _open_pdftotext),
)


def _read_pages(backend: _TextBackend, spec: str) -> _Extraction:
    pages = list(_page_range(spec, backend.page_count))
    chunks: List[str] = []
    recovered = 0
    for i in pages:
        body = backend.page_text(i)
        recovered += len(body.strip())
        chunks.append(f"\n===== page {i + 1} =====\n{body}")
    return _Extraction(
        backend.name, backend.page_count, pages, "".join(chunks), recovered
    )


def _extract_text(src: Path, spec: str, pin: str):
    """Walk the text chain; return (extraction_or_None, tried_notes)."""
    tried: List[str] = []
    first: Optional[_Extraction] = None
    for name, opener in _TEXT_OPENERS:
        if pin != "auto" and pin != name:
            continue
        try:
            backend = opener(src)
        except Exception as exc:  # a present-but-broken backend must not end the run
            tried.append(f"{name} (open failed: {exc})")
            continue
        if backend is None:
            tried.append(f"{name} (not installed)")
            continue
        try:
            result = _read_pages(backend, spec)
        except Exception as exc:
            tried.append(f"{name} (read failed: {exc})")
            continue
        finally:
            backend.close()
        if first is None:
            first = result
        if result.recovered > 0:
            return result, tried
        tried.append(f"{name} (0 characters recovered)")
    return first, tried


def _images_fitz(src: Path, pages: List[int], out_dir: Path) -> Optional[int]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    doc = fitz.open(src)
    try:
        count = 0
        for i in pages:
            for img in doc[i].get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(out_dir / f"p{i + 1}_x{xref}.png")
                count += 1
    finally:
        doc.close()
    return count


def _images_pdfimages(src: Path, pages: List[int], out_dir: Path) -> Optional[int]:
    if shutil.which("pdfimages") is None:
        return None
    proc = subprocess.run(
        [
            "pdfimages", "-png",
            "-f", str(pages[0] + 1),
            "-l", str(pages[-1] + 1),
            str(src), str(out_dir / "p"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"extract.py: pdfimages failed: {proc.stderr.strip()}", file=sys.stderr)
        return None
    return len(list(out_dir.glob("p-*.png")))


_IMAGE_BACKENDS = (("fitz", _images_fitz), ("pdfimages", _images_pdfimages))


def _write_images(src: Path, pages: List[int], out_dir: Path) -> int:
    """Run the image chain. Returns an exit code (0 or 3)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pages:
        print(f"images: 0 -> {out_dir} (no pages selected)", file=sys.stderr)
        return 0
    for label, fn in _IMAGE_BACKENDS:
        count = fn(src, pages, out_dir)
        if count is not None:
            print(f"images: {count} -> {out_dir} (via {label})", file=sys.stderr)
            return 0
        print(f"extract.py: {label} image backend unavailable.", file=sys.stderr)
    print("extract.py: no usable PDF image backend.", file=sys.stderr)
    print("  Tried PyMuPDF (import fitz) and pdfimages (poppler-utils).", file=sys.stderr)
    print("  Install one inside the agent container:", file=sys.stderr)
    print("    pip install pymupdf", file=sys.stderr)
    print("    apt-get install -y poppler-utils", file=sys.stderr)
    print("  Page renders (pdftoppm) are a different artifact, so we do not", file=sys.stderr)
    print("  silently substitute them for embedded images here.", file=sys.stderr)
    return 3


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract text/images from a PDF.")
    ap.add_argument("pdf")
    ap.add_argument("--out", default="-", help="text output file, or '-' for stdout")
    ap.add_argument("--images-dir", default="", help="if set, write embedded images here")
    ap.add_argument("--pages", default="", help="1-based page range, e.g. 1-5")
    args = ap.parse_args()

    pin = (os.environ.get("WCB_PDF_BACKEND") or "auto").strip()
    known = ["auto"] + [name for name, _ in _TEXT_OPENERS]
    if pin not in known:
        print(
            f"extract.py: WCB_PDF_BACKEND='{pin}' is not one of {known}",
            file=sys.stderr,
        )
        return 4

    src = Path(args.pdf)
    if not src.is_file():
        print(f"not found: {src}", file=sys.stderr)
        return 1

    result, tried = _extract_text(src, args.pages, pin)
    if result is None:
        print("extract.py: no usable PDF text backend.", file=sys.stderr)
        for note in tried:
            print(f"  tried {note}", file=sys.stderr)
        print("  Install one inside the agent container:", file=sys.stderr)
        print("    pip install pymupdf               # preferred, also does images", file=sys.stderr)
        print("    pip install pdfplumber pypdf      # pure-python fallbacks", file=sys.stderr)
        print("    apt-get install -y poppler-utils  # pdftotext/pdfimages", file=sys.stderr)
        if pin != "auto":
            print(
                f"  WCB_PDF_BACKEND pinned this run to '{pin}';"
                " unset it to walk the whole chain.",
                file=sys.stderr,
            )
        return 2

    if result.backend != "fitz":
        why = "pinned by WCB_PDF_BACKEND" if pin != "auto" else "PyMuPDF unusable"
        print(f"extract.py: {why}; using the {result.backend} backend.", file=sys.stderr)

    if args.out == "-":
        sys.stdout.write(result.text)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(result.text, encoding="utf-8")
        print(f"text: {len(result.text)} chars -> {args.out}", file=sys.stderr)

    if result.pages and result.recovered == 0:
        print(
            f"extract.py: WARNING - no backend recovered any text from"
            f" {len(result.pages)} page(s).",
            file=sys.stderr,
        )
        for note in tried:
            print(f"  tried {note}", file=sys.stderr)
        print("  The PDF most likely has no text layer (a scan, or an export of", file=sys.stderr)
        print("  flattened images). Re-run with --images-dir to pull the images", file=sys.stderr)
        print("  out and OCR them (tesseract is installed in the image).", file=sys.stderr)

    if args.images_dir:
        code = _write_images(src, result.pages, Path(args.images_dir))
        if code != 0:
            return code

    print(
        f"pages: {result.page_count} (extracted {len(result.pages)}"
        f" via {result.backend})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
