# Stage 0 — Pre-task seed (before T0)

Bootstrap stage. Applies BEFORE T0. Seeds mock_data baseline into the orchestrator world and places all canonical static artifacts on /workspace.

Drift mutations begin at stage 1. Stage 0 is silent-mutation-free.

## Filesystem drops (`mutations.filesystem`)

| ID | Source | Destination on /workspace | Modality |
|----|--------|---------------------------|----------|
| F0-P01..P07 | `persona/alden-croft/*.md` | `/workspace/persona/alden-croft/*` | text/markdown |
| F0-E01 | `emails/Yard_pre_haul_instructions_2026-12-04.eml` | `/workspace/inbox/2026-12-04_yard_pre_haul.eml` | message/rfc822 |
| F0-E02 | `emails/Coop_weekly_settlement_2026-12-06.eml` | `/workspace/inbox/2026-12-06_coop_weekly.eml` | message/rfc822 |
| F0-E03 | `emails/Cummins_monthly_bulletin_2026-11-30.eml` | `/workspace/inbox/2026-11-30_cummins_bulletin.eml` | message/rfc822 |
| F0-C01 | `contracts/Cummins_TSB-247B.pdf` | `/workspace/inbox/Cummins_TSB-247B.pdf` | application/pdf |
| F0-C02 | `contracts/Hamilton_order_confirmation_2026-12-07.pdf` | `/workspace/inbox/Hamilton_order_confirmation_2026-12-07.pdf` | application/pdf |
| F0-T01 | `templates/Rockland_Marine_work_order_template.docx` | `/workspace/templates/Rockland_Marine_work_order_template.docx` | application/vnd.openxmlformats-officedocument.wordprocessingml.document |
| F0-T02 | `templates/yard_prep_instructions.md` | `/workspace/templates/yard_prep_instructions.md` | text/markdown |
| F0-D01 | `data/Alden_catch_log_2026-11-30_to_12-06.csv` | `/workspace/data/Alden_catch_log_2026-11-30_to_12-06.csv` | text/csv |
| F0-D02 | `data/Alden_engine_log_2026.csv` | `/workspace/data/Alden_engine_log_2026.csv` | text/csv |
| F0-D03 | `data/Alden_finances_seed_2026-12.csv` | `/workspace/data/Alden_finances_seed_2026-12.csv` | text/csv |
| F0-D04 | `data/Eileen_C_vessel_engine_spec.md` | `/workspace/data/Eileen_C_vessel_engine_spec.md` | text/markdown |

## Loud API seeds (`mutations.loud`)

| ID | Service | What it seeds |
|----|---------|---------------|
| M0-G01..G03 | gmail-api | Co-op weekly settlement, yard pre-haul, Cummins monthly bulletin (3 messages) |
| M0-C01..C02 | google-calendar-api | Haul-out event 12/9 08:00 + Pratt ortho 12/9 14:00 |
| M0-N01 | google-contacts-api | 9 pre-existing contacts (Kara, Eddie, Marv, Donna, Dr. Harrigan, Dr. Pratt, Dr. Eaton, Dr. Taniguchi, Co-op) |
| M0-H01 | hamilton-marine-api | Impeller P/N 3071 inventory @ $23.45 |
| M0-B01 | cummins-service-bulletins-api | TSB-247B record (original range 1700001-1899999, $145 Cummins list price) |

## Silent mutations

None at stage 0. First drift fires at stage 1 (T15→T16 overnight).

## How to apply

The orchestrator reads `mutations.json` and applies each entry against the mock environment. Run `./verify.sh` after application to confirm all filesystem drops landed and the API audit summary matches the expected POST/PATCH/GET counts.
