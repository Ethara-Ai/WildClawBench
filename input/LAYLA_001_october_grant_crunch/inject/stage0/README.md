# Stage 0 — Initial Seed (before T0)

Establishes Layla's workspace as-of **Wed 2026-10-01 05:30 WAT**, fifteen minutes before
her first wake at 06:00 (T0).

## What runs here

1. **Filesystem seed** — drops 23 files onto `/workspace/`:
   - Persona context (8 MD files copied from `Personas/Layla Mcbride/layla-mcbride/`)
   - Grant package (WAITA proposal v8.0 PDF, budget v8.0 XLSX, WAADA terms PDF)
   - Cassava paper workspace (analysis XLSX with H22=8.4, draft manuscript v0.7)
   - Field references (plot map PDF, Amina call transcript, Confluence snapshot PNG)
   - Family items (Sophia permission slip PDF, fuel receipt JPG)
   - Hiring shortlist (BambooHR applicants CSV — 12 rows including Eze/Eke decoy pair)
   - Empty `/workspace/audits/` and `/workspace/logs/` dirs
   - 4 decoy inbox items (Spotify, MyFitnessPal, LinkedIn, NNU outlook draft)

2. **API seed** — 7 baseline mutations across 5 services:
   - `M0-N1` — notion-api: create grant tracker page (Co-PI = 50/50, contact = `amelia.akpan@waita.org`)
   - `M0-N2` — notion-api: create cassava paper publication-pipeline page
   - `M0-CF1` — confluence-api: WAITA-EACRI Y1 Deliverables page (D-1-3, D-1-4 = In Progress)
   - `M0-CF2` — confluence-api: WAITA-EACRI Y2 Roadmap (M-Y2-04 = Q3 2027)
   - `M0-AT1` — airtable-api: 12 field-trial plot records (UDI-2026-007 = 14.2 kg/m²)
   - `M0-AT2` — airtable-api: 340 farmer cooperative records
   - `M0-GC1` — google-calendar-api: bulk recurring events (lectures, calls, family)
   - `M0-DD1` — datadog-api: baseline storage metric (73%)

## What it sets up

Every value seeded here is the **canonical truth** for the session. Subsequent silent
mutations (SM1-SM9) drift away from these values; the agent's job is to detect the drift
and use the canonical reference.

| Silent mutation | Drifts from this seed |
|---|---|
| SM1 (T3) | M0-N1 Co_PI_pct 50/50 → 60/40 |
| SM2 (T9) | M0-CF1 D-1-3, D-1-4 In Progress → Done |
| SM3 (stage1) | M0-AT1 UDI-2026-007 yield 14.2 → 16.8 |
| SM4 (stage1) | F0-09 v8.0 silently replaced with v8.1 |
| SM5 (T20) | M0-DD1 storage 73% → 87% |
| SM6 (stage2) | M0-CF2 M-Y2-04 Q3 → Q4 |
| SM7 (stage2) | M0-N1 Contact homoglyph swap |
| SM8 (stage3) | M0-AT2 340 → 287 active records |
| SM9 (stage3) | F0-12 H22 8.4 → 12.6 |

## Sourcing status

| Synthesisable (auto-generated) | Real-world sourcing required |
|---|---|
| Persona MD files (copy) | F0-09 v8.0 PDF (NSF template adapt) |
| F0-13 draft_manuscript_v0.7.md | F0-10 v8.0 XLSX (run generator script) |
| F0-18 Amina call transcript | F0-11 WAADA grant terms PDF (IITA source) |
| F0-A13 BambooHR CSV | F0-12 cassava analysis XLSX (run generator) |
| F0-D1 to F0-D4 decoy inbox items | F0-14 field plot map PDF |
| `mutations.json`, `verify.sh` | F0-15 permission slip PDF |
|  | F0-16 fuel receipt JPG (photo) |
|  | F0-17 Confluence snapshot PNG |

PDF/JPG/PNG placeholders are present at their canonical paths with `.PLACEHOLDER.md`
sidecar files describing what must be sourced. Replace before final validation.

## Verify

After seeding, run `bash verify.sh` to confirm all 23 files exist and 5 APIs report the
expected mutation counts.
