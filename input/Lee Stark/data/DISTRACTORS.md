# Distractor Artifacts — LEE_002 Autumn Immunity Collection Launch

Files listed here are deliberate **noise**. They sit alongside the canonical artifacts inventory in `Artifacts_description.md` but are **NOT** referenced by `artifacts_required.md` §1 (Gate 31), are **NOT** triggered by any `inject/stage*/STAGE*_INJECT.json`, and contain **NO** post-mutation tokens from `_post_mutation_snapshot.json`.

Their purpose is to verify the assistant correctly ignores Lee Stark's broader personal/professional life (family, kids, ceramics, doula network, co-op equipment, midwifery cross-org, Norway-trip arc, spouse's barn-studio plans) and does not pull irrelevant material into the launch trajectory.

## Distractor inventory

| # | File | Modality | Domain | Why it's a distractor |
|---|---|---|---|---|
| 1 | `owen_montessori_field_trip_permission.eml` | EML | Family / kids | Owen's apple-orchard field trip permission slip. Family logistics, no launch signal. |
| 2 | `brett_apprentice_september_training_log.md` | MD | Midwifery practice | Brett's CPM-track training log. Apprentice training arc, not the Autumn Collection. |
| 3 | `stark_fam_norway_2027_call_notes.md` | MD | Long-arc personal | Sunday Zoom call notes with Harold + Ingrid re: summer 2027 Norway trip. |
| 4 | `ceramics_studio_kiln_firing_schedule.csv` | CSV | Hobby | Shared kiln firing schedule. Lee's ceramics class, fully personal. |
| 5 | `bozeman_doula_network_quarterly_meetup.ics` | ICS | Professional network | Q3/Q4 doula network peer meetup invites. |
| 6 | `megan_barn_studio_contractor_estimate.eml` | EML | Spouse / homestead | Forwarded contractor estimate for the barn studio south-bay expansion. Spring 2027 arc. |
| 7 | `cottonwood_basin_equipment_maintenance_log.csv` | CSV | Co-op (Donna) | Co-op equipment maintenance roster. Donna-co-stewarded but unrelated to product launch. |
| 8 | `bridger_valley_blinded_transfer_summary.eml` | EML | Cross-org midwifery | Blinded transfer-of-care notice from Dr. Gale's office, points at Box folder. |

## Guarantees per file

Each distractor was authored to satisfy ALL of the following constraints:

1. **No launch SKU codes** — none of `ESY-2509`, `FC-2509`, `AIBT-2509`, `HTB-2509` appear in any distractor file.
2. **No launch-supplier names** — `Thornfield`, `Mountain Meadow`, `Main Street Saturday Market`, `Joelle Kessler`, `coordinator@mainstreetsatmarket.org` do not appear.
3. **No post-mutation values** — none of `47`, `52`, `2026-09-10`, `2026-09-21`, `amber 4oz tamper-evident`, the literal `100` USD threshold token, or `LANDED` appear in any distractor file. The literal substring `landed` (case-insensitive) is also avoided so it does not collide with the `MG-03` OCR-token match against `donna_opboard_note.jpg`.
4. **No launch date** — `2026-09-12` (opening Saturday) does not appear in any distractor.
5. **Persona-consistent** — all contacts, locations, and arcs come from `lee-stark/MEMORY.md` / `TOOLS.md` (Bridger Peaks Montessori, Brett Halvorsen, Harold/Ingrid Stark in Tromsø, Megan Caldwell, Donna Whitfield, Dr. Nathan Gale, Bridger Valley Family Health, Cottonwood Basin Co-op).
6. **Date-anchored near 2026-09-07** but never inside a documented birth-window block and never overlapping the launch weekend.

## Evaluator note

If any checker scans `task/artifacts/` recursively for launch-relevant tokens, the eight files registered here should produce **zero** matches against the task's signal vocabulary. Any match indicates a contamination bug in the distractor and should be filed against this file.
