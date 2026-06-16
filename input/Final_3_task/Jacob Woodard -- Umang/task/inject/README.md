# inject/ — Stage-System Schema

> Mutation injection framework for JACOB_001_krasicki_delivery_crisis

---

## Responsibility Split

| Layer | Timing | Owner | Examples |
|---|---|---|---|
| **Baseline** | Before any turn | `mock_data/` + `task/artifacts/` | API seed data, file-system artifacts |
| **stage0** | After baseline, before T0 | `inject/stage0/` | Seed file drops (F0-01 … F0-27) |
| **In-turn** | During a turn's execution | `task.py` turn mutations | Loud mutations delivered via wake-up message |
| **stage1** | Between Day 1 → Day 2 | `inject/stage1/` | SM1 (Airtable qty 3→5), SM2 (portal QA 5→7 days) |
| **stage2** | Between Day 2 → Day 3 | `inject/stage2/` | SM4 (portal on_hold), SM3-bx (Box renames), SM6-at (wrong row), SM8-bx (unauthorized assessment) |
| **stage3** | Between Day 3 → Day 4 | `inject/stage3/` | SM9 (stale dates across Notion/Linear/Airtable), SM10 (Gniezno 1798→1802) |

---

## Run Order

1. **Load baseline** — mock-data CSVs and JSONs populate API services.
2. **Apply stage0** — seed files dropped into the workspace filesystem.
3. **Execute Day 1 turns** (T0–T12) — in-turn loud mutations via wake-up messages.
4. **Apply stage1** — silent mutations SM1, SM2 applied overnight.
5. **Execute Day 2 turns** (T13–T25) — in-turn mutations SM3, SM5.
6. **Apply stage2** — silent mutations SM4, SM6, SM8 applied overnight.
7. **Execute Day 3 turns** (T26–T37) — in-turn mutations SM7.
8. **Apply stage3** — silent mutations SM9, SM10 applied overnight.
9. **Execute Day 4 turns** (T38–T49) — final turn execution.

---

## Per-Stage File Layout

Each `stage{N}/` directory contains:

```
stage{N}/
├── mutations.json      # Machine-readable mutation specs
├── verify.sh           # Post-application verification script
├── README.md           # Human-readable stage description
└── files/              # Any files to inject (e.g., emails, PDFs)
```

---

## ID Conventions

| Prefix | Meaning | Example |
|---|---|---|
| `M0-*` | Stage-0 API seed mutation | `M0-GC1` (Google Calendar seed) |
| `F0-*` | Stage-0 filesystem seed file | `F0-01` (Krasicki parts manifest) |
| `SM*` | Silent mutation (stages 1–3) | `SM1` (Airtable qty change) |
| `RL*` | Red-line constraint | `RL1` (Sophie authentication) |
| `F-T*` | In-turn injected file/email | `F-T1` (Stefan order email) |

---

## Sourcing Rules

- **Synthesised:** All task-specific data (emails, portal PDFs, CSV exports)
  is author-created. No external data sources.
- **Sourced:** Media files (photos, audio) in `task/artifacts/` must have
  CC/PD licences documented in `task/ATTRIBUTIONS.md`.

---

## Detection-vs-Application Invariant

Every silent mutation (`SM*`) must have at least one corresponding checker
in `task.py` that verifies the agent **detected** the change, not merely
that the change was **applied**. The checker tests agent behaviour
(response content, API calls, writeback actions), not mutation state.
