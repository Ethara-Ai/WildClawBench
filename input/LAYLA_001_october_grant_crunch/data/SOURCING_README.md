# Real-World Artifact Sourcing — Pending Items

This `task/artifacts/` folder holds **delivered, ready-to-use** synthetic artifacts
(EML, TXT, JSON, MD, CSV) that were producible per BRIEF §2.1.

Real-world media (PDF, JPG, PNG, M4A) must be sourced separately before validation
Day 3. Each item below has a `__PLACEHOLDER__` sidecar at its canonical workspace
path. Replace those placeholders with real-world-sourced files matching the
canonical values specified in `artifacts_required.md` §3.

## Items pending real-world sourcing

| # | Path on `/workspace/` | Modality | Sourcing URL / method | Required canonical values |
|---|---|---|---|---|
| A1 | `/workspace/grants/waita-eacri/2026-10/WAITA_proposal_v8.0_FINAL.pdf` | PDF | Adapt [NSF PAPPG template](https://new.nsf.gov/funding/pappg) | Total budget ₦127,450,000; Co-PI 50/50; D-1-3, D-1-4 In Progress |
| A2 | `/workspace/grants/waita-eacri/2026-10/WAITA_proposal_v8.1_FINAL.pdf` | PDF | Copy of A1 with budget cell edited | Total budget ₦134,800,000; Co-PI 60/40 |
| A4 | `/workspace/family/Marcus_voicenote_2026-10-01_1325.m4a` | M4A audio (~95s) | Record yourself reading the script in `artifacts_required.md` §A4 | Audio contains "four to seven days", "Sunday", and "Tuesday" |
| A5 | `/workspace/family/generator_fuel_receipt_2026-09-30.jpg` | JPG photo | [Wikimedia Commons](https://commons.wikimedia.org/) search "fuel receipt Nigeria" — strip PII | OCR-readable ₦8,500 amount; decoy ₦9,500 elsewhere in frame |
| A6 | `/workspace/field/photos/udi-2026-007_field_photo_2026-09-28.jpg` | JPG photo | [IITA / CGIAR](https://www.iita.org/cropsnew/cassava/) or [Wikimedia "Manihot esculenta plot"](https://commons.wikimedia.org/wiki/Category:Manihot_esculenta) | Visible signboard "Plot UDI-2026-007 · Vit-A trial · Y2 · planted 2025-04-12" |
| A7 | `/workspace/family/Sophia_school_permission_slip_2026-09-30.pdf` | PDF (synthesisable) | Synthesise a Nigerian primary school slip on letterhead | "Bright Future Nursery & Primary"; due EOD Wed 1 Oct 2026; event class trip Fri 10 Oct 2026 |
| A11 | `/workspace/inbox/DS-ENV-2026-1004-1342_envelope.json` | JSON (synthesised, see stage3/) | Synthesised in stage3 | senderEmail `amelia-akpan@waita-foundation.org` |
| A15 | `/workspace/grants/waita-eacri/WAADA_grant_terms_excerpt.pdf` | PDF | Adapt [CGIAR / IITA grant award letter](https://www.iita.org/research/projects/) — include the §7.4 data-sharing clause verbatim from `artifacts_required.md` | Section 7.4 clause text |
| A16 | `/workspace/field/maps/field_trial_plot_map_UDI-2026.pdf` | PDF (synthesisable) | Adapt IITA Udi field-plot layout map. Use a vector drawing tool. | Plot UDI-2026-007 labelled 14.2 kg/m²; plot UDI-2026-070 labelled 16.8 kg/m² |
| A17 | `/workspace/snapshots/Confluence_Y1_deliverables_snapshot_2026-09-30.png` | PNG screenshot | Synthesise from blank Atlassian Confluence page template | D-1-3 and D-1-4 visibly show "In Progress" badges |
| A18 | `/workspace/family/Houston_flight_quote_2026-10-03.pdf` | PDF (synthesisable) | Adapt real Amadeus / Skyscanner PNR fare quote export | Route ENU → LOS → IAH; total NGN 1,287,500 ≈ USD 854; depart Sun 5 Oct 22:50 WAT |

## XLSX generator notes

Two XLSX files (A3, A12) are referenced as `__PLACEHOLDER__` sidecars but can be
generated programmatically. Each `stageN/` folder ships with a `generate_*.py`
script (where applicable) that produces the XLSX from canonical values. Run those
scripts after `pip install openpyxl`.

## Real-world rule (BRIEF §2.1) — recap

| Allowed to synthesise | NEVER AI-generate |
|---|---|
| `.eml` headers + bodies | `.pdf` documents that are official records |
| `.txt` transcripts | `.jpg` photographs |
| `.json` API payloads | `.png` screenshots of real-world UIs |
| `.csv` data tables | `.m4a` / `.wav` audio recordings |
| `.md` markdown | (anything that depends on real-world provenance) |
| `.xlsx` spreadsheets (templated) | |

PDFs adapted from public templates with synthesised numeric content count as
**adapted, not AI-generated** — fine to use.

End.
