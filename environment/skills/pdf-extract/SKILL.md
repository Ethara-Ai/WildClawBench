---
name: pdf-extract
description: Extract text and embedded images from PDF files using PyMuPDF (fitz), falling back to pdfplumber, pypdf or poppler-utils when it is unavailable.
metadata: {"clawdbot":{"emoji":"📄","requires":{"bins":["python3"]},"pip":["pymupdf","pdfplumber","pypdf"]}}
---

# PDF Extract (PyMuPDF, with fallbacks)

Pull plain text and/or embedded images out of a PDF for downstream reasoning.

## Quick start

Extract all text to stdout:

```bash
python3 {baseDir}/scripts/extract.py /path/to/doc.pdf
```

Text to a file, images to a folder, a page range:

```bash
python3 {baseDir}/scripts/extract.py /path/to/doc.pdf \
  --out /tmp_workspace/results/doc.txt \
  --images-dir /tmp_workspace/results/imgs \
  --pages 1-5
```

Outputs the page count and per-page text.

## How extraction works (so you can debug if it fails)

`extract.py` does not hard-depend on PyMuPDF. Text is taken from the first
backend that both imports and returns characters:

```
PyMuPDF (fitz) -> pdfplumber -> pypdf -> pdftotext (poppler-utils)
```

Embedded images use a shorter chain:

```
PyMuPDF (fitz) -> pdfimages (poppler-utils)
```

The fallbacks exist because the container is not guaranteed to have
PyMuPDF: the offline wheelhouse install can fail, and older images predate
it — a bare `ModuleNotFoundError: No module named 'fitz'` used to end the
run with no output. When a fallback is used, the chosen backend is named on
stderr and in the closing `pages:` line, so you always know what produced
the text.

A backend that imports but recovers zero characters is not accepted while
another rung remains — pdfminer (under `pdfplumber`) returns empty strings
for some producers that poppler reads fine. If *every* rung comes back
empty, the page skeleton is still written (an image-only scan really has no
text layer) and a loud `WARNING` is printed; extract the images with
`--images-dir` and OCR them with `tesseract`, which is in the image.

Page renders (`pdftoppm`) are deliberately **not** used as an embedded-image
fallback: that is a different artifact, and substituting it would hand you
the wrong output without saying so.

Exit codes:

- `0` — extraction succeeded.
- `1` — input PDF not found.
- `2` — no text backend at all. Treat as an image/skill-deps regression.
- `3` — `--images-dir` was requested but neither image backend exists.
- `4` — `WCB_PDF_BACKEND` names an unknown backend.

## Requires

- `python3` — installed in the image.
- `pymupdf` — preferred backend; the only one that does text *and* images.
- `pdfplumber`, `pypdf` — pure-python text fallbacks, from the offline
  wheelhouse.
- `poppler-utils` — `pdftotext`/`pdfinfo`/`pdfimages`, from the offline
  debhouse. The last rung, and the only one needing no Python package.
- `WCB_PDF_BACKEND` — optional; `auto` (default), or one of `fitz`,
  `pdfplumber`, `pypdf`, `pdftotext` to pin a single rung when debugging.
