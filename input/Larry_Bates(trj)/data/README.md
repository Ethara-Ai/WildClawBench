# Data -- `task/data/`

This directory holds the four real media artifacts (alongside the surrounding decoy and stale files) that the harness exposes to the agent through the local filesystem for this task. The folder is bundled as part of the harness input; the agent reads from it through standard file-read tools and never needs to know the folder's name.

## Current state

| Artifact                                              | Modality    | State         | Size        | Notes                                                                              |
|-------------------------------------------------------|-------------|---------------|-------------|------------------------------------------------------------------------------------|
| `caldwell_voicemail_harvest_update.mp3`               | audio       | real binary   | 385,388 B   | Spoken voicemail; contains "seven thousand two hundred bushels".                  |
| `imperial_stout_2026_competition.pdf`                 | document    | real binary   | 4,550 B     | A4, 2 pages, extractable text. Carries stale flagship ABV 8.4% (cross-modal trap). |
| `bates_brewery_2026_batches.xlsx`                     | spreadsheet | real binary   | 6,227 B     | Sheet `Batches`, 14 data rows + header. Flagship row 007 carries stale ABV 8.4.    |
| `gabf_entry_label_imperial_stout_2026.jpg`            | image       | real binary   | 119,656 B   | sRGB JPEG. Carries `BBC-2026-007`, no ABV (label-format constraint).               |

All four files are real, parseable binaries that yield the expected extracted values through standard media-extraction tooling (pdfplumber / openpyxl / mutagen / PIL).

## Hard constraints (for any future replacement)

- Audio: 44.1 kHz mono mp3, 28-42 s, < 1.5 MB, clean speech, ONE numerical value spoken aloud ("seven thousand two hundred bushels").
- PDF: A4, 2 pages, < 200 KB, extractable text (NOT scanned image-only). Title page must visibly contain "Bates Brewing Company / 2026 Competition Entry / Imperial Stout / Batch BBC-2026-007 / ABV 8.4%".
- Spreadsheet: < 50 KB, one sheet `Batches`, 14 data rows + header. Columns: Batch ID, Style, ABV%, IBU, Volume_bbl, Status, Yeast Strain, Notes. Flagship row BBC-2026-007 MUST carry ABV 8.4 (stale on purpose — cross-modal trap CM-01).
- Image: 1200x1800 sRGB JPEG, < 350 KB. OCR-readable text. MUST omit ABV.

## Source candidates (CC-licensed or public-domain only) — for any future replacement under strict gate 11

| Artifact                                              | Source candidate                                                                                                                                |
|-------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| `caldwell_voicemail_harvest_update.mp3`               | Mozilla Common Voice (CC0) clip of a single male speaker, trimmed to ~30 s with the spoken number "seven thousand two hundred bushels" overdubbed in the same voice profile. |
| `imperial_stout_2026_competition.pdf`                 | Public-domain BJCP style guideline re-titled with the brewery's spec header; OR a CC-BY academic fermentation-science paper repurposed with an overlaid title page. |
| `bates_brewery_2026_batches.xlsx`                     | Public US Alcohol & Tobacco Tax & Trade Bureau (TTB) production CSV reformatted into the 14-row sheet; OR Open Data Commons brewery production dataset. |
| `gabf_entry_label_imperial_stout_2026.jpg`            | Wikimedia Commons public-domain antique beer label with text re-overlaid in GIMP/Photoshop; OR Figma Community CC-BY label mockup with overlaid text. |

## Gate 11 note

Gate 11 prefers real-world-sourced multimodal evidence. The current PDF and XLSX are programmatically constructed (reportlab + openpyxl) to satisfy the constraint set above — they parse correctly and carry the right trap values, but they are not CC-licensed source documents. For a strict-gate-11 Talos run, swap them with the source-candidate options in the table above while preserving the required text content; the rest of the pipeline (checker assertions and any cached size metadata) will need their size fields updated if the new binaries are materially larger.
