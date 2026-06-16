# `task/inject/` — between-day mutation batches

Each `stageN/` directory is a batch of mutations the orchestrator applies between
specific turn boundaries to drift the canonical `mock_data` baseline forward in
time across Gloria's 4-day (T0–T49) Deep Roots grant-crunch session.

## Responsibility split

| Responsibility | File |
|---|---|
| Baseline mock-API state at boot | `../../mock_data/<api>/*` (see `mock_data/MANIFEST.json`) |
| Boot-time baseline + persona + canonical artifacts | `stage0/mutations.json` |
| Day-1 in-turn mutations (SM1, SM2 + atmospheric loud events) | `../task.py` turn entries |
| Day-2 between-day batch (SM3, SM4, SM5) | `stage1/mutations.json` |
| Day-3 between-day batch (SM6, SM7) | `stage2/mutations.json` |
| Day-4 between-day batch (SM8) | `stage3/mutations.json` |
| Turn wake_ups + allowed_tools + checkers | `../task.py` |

The six connected mock APIs for this task are:

- `gmail-api`
- `google-calendar-api`
- `google-contacts-api`
- `google-docs-api`
- `google-drive-api`
- `google-sheets-api`

(Note: `notion`, `airtable`, and `canvas` are **not** connected for this
workspace — see rubric criterion R19.)

The orchestrator follows this run order at task launch:

1. Boot all six mock APIs with persona-baseline state from `mock_data/<api>/*`.
2. Apply `stage0/mutations.json` (pre-session baseline: all pre-T0 artifacts,
   persona OS snapshot under `stage0/persona/gloria-wiggins/`, and canonical
   curated assets).
3. Begin `task.py` turn loop (T0–T49):
   - Apply any in-turn loud/silent mutations **before** the wake_up.
   - Deliver the turn wake_up message.
   - Score against the checkers referencing that turn.
4. At each day boundary, apply the next `stageN/mutations.json`:
   - Before Day 2 (Mon evening → Tue morning): apply `stage1` (SM3, SM4, SM5).
   - Before Day 3 (Tue evening → Wed morning): apply `stage2` (SM6, SM7).
   - Before Day 4 (Wed evening → Thu morning): apply `stage3` (SM8).
5. After the final turn completes, score the `CROSS_C*` cross-turn aggregates.

The authoritative timing and payload for every mutation lives in each stage's
`mutations.json`; this README is a human-readable map only.

## Per-stage file layout

| File | Purpose |
|---|---|
| `mutations.json` | Authoritative control file for the stage (silent + loud + filesystem mutations and their payloads). |
| asset files | The `.eml` / `.ics` / `.xlsx` / `.docx` / `.vcf` payloads referenced by `mutations.json`, named `<MUTATION-ID>_<artifact>` (e.g. `SM3_S4_Soil_Analysis_Mutated.xlsx`). |

`stage0/` additionally contains:

- `persona/gloria-wiggins/` — snapshot of the seven persona OS files
  (`AGENTS.md`, `HEARTBEAT.md`, `IDENTITY.md`, `MEMORY.md`, `SOUL.md`,
  `TOOLS.md`, `USER.md`) applied to the agent workspace at boot.
- `calendar/`, `contacts/`, `docs/`, `emails/`, `pdfs/`, `sheets/` — the pre-T0
  baseline artifacts grouped by source type.

The canonical, deduplicated pool of every curated asset used by any stage lives
in `../artifacts/` (flat, meaningful filenames).

## Silent-mutation index

| ID | Stage | Drift |
|---|---|---|
| SM1 | day-1 in-turn | Fieldstone submission guidelines update (E9) |
| SM2 | day-1 in-turn | Henderson farm visit moved 9:00 → 10:00 AM (CAL1) |
| SM3 | stage1 | Mitchell Road soil nitrogen 42 → 38 ppm (S4) |
| SM4 | stage1 | Indirect cost rate 15% → 12% / IDC cap (S1, E14) |
| SM5 | stage1 | Darnell Washington email address change (CT2) |
| SM6 | stage2 | Evaluation rubric now a required appendix (E11) |
| SM7 | stage2 | Narrative V6 scope narrowing Piedmont → Durham (D1v6) |
| SM8 | stage3 | Workshop materials pricing change (S6) |

> Parity note: the reference task ships a per-stage `verify.sh` and per-stage
> `README.md`. Those are intentionally **not** generated here because they encode
> task-specific orchestrator ports and `/workspace` destination paths that must
> be authored against this task's actual runtime configuration. Add them once the
> port map and workspace destination mapping are finalized.
