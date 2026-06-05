# Inject Staging — LAYLA_001_october_grant_crunch

This directory holds the **between-turn state mutations** that the orchestrator must apply
before advancing into the next turn group. Each `stageN/` folder is self-contained.

## Structure

```
inject/
├── stage0/   seeded BEFORE T0          → initial state before the session starts
├── stage1/   applied between T12 → T13 → overnight Wed Oct 1 → Thu Oct 2
├── stage2/   applied between T25 → T26 → overnight Thu Oct 2 → Fri Oct 3
└── stage3/   applied between T38 → T39 → overnight Fri Oct 3 → Sat Oct 4
```

Each `stageN/` contains:

| File | Purpose |
|---|---|
| `mutations.json` | Authoritative control file — all API calls + filesystem writes the orchestrator must replay. Schema mirrors `Sample_Tasks/insurance_auto_claim_settlement/inject/mutations.json`. |
| `verify.sh` | Sanity script: after the orchestrator applies the stage, this script curls `/audit/summary` on each affected API and asserts the mutation landed (or the expected file exists on `/workspace/`). Exits 0 on success, non-zero on failure. |
| `README.md` | Human-readable stage manifest — what happens, why, which checkers it feeds. |
| Inline artifacts | EML, TXT, JSON, CSV, MD files that are dropped onto `/workspace/` during this stage. |
| `__PLACEHOLDER__*` files | Stand-ins for artifacts that MUST be sourced from real-world public references (PDFs, photos, audio). The filesystem path is correct; the content is a placeholder with embedded sourcing instructions. Replace before validation. |

## Real-world sourcing rule (BRIEF §2.1)

| Allowed to synthesise | Must source from real-world public references |
|---|---|
| `.eml` files | `.pdf` documents (NSF, IITA, CGIAR templates) |
| `.txt` transcripts | `.jpg` / `.png` photos / screenshots |
| `.json` API payloads | `.m4a` / `.wav` audio |
| `.csv` data tables | (signed legal artifacts, real-form scans) |
| `.md` markdown | |
| `.xlsx` spreadsheets (templated from public examples) | |

PDFs that are **layouts of synthesised data** (e.g. plot maps, flight quotes) may be
produced by adapting a public template. PDFs that are **load-bearing official documents**
(grant terms, regulatory excerpts) must be sourced.

## Re-running the inject

The orchestrator entry-point is (informally):

```python
from task import TURNS, CHECKERS
from inject.stage0 import seed
seed(client, workspace_root)         # before T0
for turn in TURNS[0:13]:              # day 1
    run_turn(turn)
from inject.stage1 import apply
apply(client, workspace_root)         # between T12 and T13
for turn in TURNS[13:26]:             # day 2
    run_turn(turn)
...
```

Each `mutations.json` is JSON-typed and can be consumed directly by any
HTTP-replay tool (curl-based, hurl, postman-cli).

## Mutation IDs

- `M0-*`: stage0 seed mutations
- `SM1..SM9`: silent mutations (story-level), see README.md §Silent Mutations
- `RL1..RL5`: red-line trap mutations (the trap is the *inject content*, not the inject itself)
- `F0-*`: stage0 filesystem drops
- `F-T*`: per-turn filesystem drops

See `/Users/macbookpro/Downloads/Talos SFT/tasks/LAYLA_001_october_grant_crunch/artifacts_required.md`
for the full manifest.

End.
