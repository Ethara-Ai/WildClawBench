# Three-Task Validation & Required Changes

**Date:** 2026-06-17
**Tasks:** `input/Gloria Wiggins`, `input/Ruth Armstrong`, `input/Lorraine Maddox`
**Reference (golden):** `input/Lee Stark`
**Spec:** `docs/task_format.md` (§1–§12), `docs/task_validator_prompt.md`

All findings below are **machine-verified** against the spec and the actual loader
(`src/utils/inject_director.py`). `input/Lee Stark` passes every machine check and is
the correct pattern to copy from.

## Verdict summary

| Task | Verdict | Hard blockers |
|---|---|---|
| Gloria Wiggins | **FAIL** (worst — would not run) | 3 |
| Ruth Armstrong | **FAIL** | 2 |
| Lorraine Maddox | **FAIL** (cleanest) | 2 |
| Lee Stark (ref) | baseline / passes | — |

Severity legend: **[BLOCKER]** task cannot run · **[HARD]** spec hard-fail · **[WARN]** spec warn · **[NIT]** cosmetic/content.

---

## A. Changes shared by ALL three tasks

### A1. `rubric.json` — invalid enum values **[HARD]**
The `type` and `evaluation_target` fields use vocabulary that is not in the spec.

Valid `type` ∈ `{task completion, factuality and hallucination, safety & boundaries, instruction following}`
Valid `evaluation_target` ∈ `{user_facing_message, tool_call, artifact, workspace_state}`

| Task | bad `type` found | bad `evaluation_target` found |
|---|---|---|
| Gloria | `task_completion`, `factuality_and_hallucination`, `safety_and_boundaries` (underscores) | `state_change` |
| Ruth | `tool use` | `state_change`, `trajectory` |
| Lorraine | `agent behavior` | `state_change`, `trajectory` |

**Fix (apply per task):**
- Replace underscores with spaces: `task_completion` → `task completion`, etc.
- `tool use` / `agent behavior` → `instruction following` (or `task completion`).
- `evaluation_target: "state_change"` → `workspace_state` (or `artifact` for document-deliverable criteria).
- `evaluation_target: "trajectory"` → `tool_call`.

### A2. `mock_data/MANIFEST.json` is a non-`-api` child **[WARN]** (§1.3)
Every immediate child of `mock_data/` must be a `<service>-api/` directory.
Lee Stark keeps its manifest at the **task root** as `MOCK_DATA_MANIFEST.json`.

**Fix:** move `mock_data/MANIFEST.json` → `MOCK_DATA_MANIFEST.json` (task root).

### A3. `persona/AGENTS.md` missing `## Multi-Agent Turns` section — **[WARN, low priority]**
All three lack the section despite having `Multi-Agent` turns. **Note:** the golden
reference `Lee Stark` also omits it, so this is a shared gap, not a discriminating
defect. Add the 5 standard bullets (per `task_format.md` §6) only if strict §6.2
conformance is required.

---

## B. Gloria Wiggins — `input/Gloria Wiggins` (3 BLOCKERS)

> As shipped, this task does not execute: turns don't parse, no mutation fires, and
> the mock stack starts no services. Fix B1–B3 before anything else.

### B1. `prompts.txt` turn headers are bare **[BLOCKER]** (§2.1)
0 of the headers use the required `--- TURN N (...) ---` delimiters; there are 14 bare
`TURN N (...)` lines. The `inject_director.py:233` regex
`^---\s*(TURN\s+T?(\d+)\b.*?)\s*---\s*$` matches **none** of them → **zero turns parse**.
(Cause: the "no dash in any body" punctuation rule was wrongly applied to the structural
`---` delimiters.)

**Fix:** wrap every one of the 14 turn headers:
```
TURN 1 (Day 1, 08:00, Light)            →   --- TURN 1 (Day 1, 08:00, Light) ---
TURN 2 (Day 1, 09:15, Multi-Agent)      →   --- TURN 2 (Day 1, 09:15, Multi-Agent) ---
... (all 14)
```

### B2. Inject ops are invisible to the loader **[BLOCKER]** (§8.4/§8.5)
`inject/stage1,2,3/STAGE*_INJECT.json` have two independent loader-fatal problems:
- Ops live under key **`requests:`** — the loader reads only `mutations` / `injections`
  (`inject_director.py:198, 224`). `requests` is ignored → "no recognized ops".
- Boundary uses **`fires_before_turn`** — the loader recognizes only
  `applies_between_turns` / `applied_between` / top-level `fires_after_turn` / per-op
  `fires_after_turn` (`inject_director.py:178–204`). `fires_before_turn` is ignored →
  the stage resolves to `[None, None]` and is treated as seed, never firing.

Net effect: every QuickBooks / calendar / Box silent mutation is dropped; the trap
design is inert.

**Fix (each stage file):**
- Rename the `"requests"` array → `"mutations"`.
- Convert `"fires_before_turn": N` → `"fires_after_turn": N-1` (fires between TN-1 and TN).
  - stage1 baseline seed before T1 → `fires_after_turn: 0`
  - stage2 QuickBooks op before T2 → `fires_after_turn: 1`; calendar before T6 → `fires_after_turn: 5`
  - stage3 Box op before T11 → `fires_after_turn: 10`
- Add `"silent": true` to each silent mutation op (currently missing).
- Prefer lifting the boundary to **top-level** `fires_after_turn` per stage (Lee Stark style).

### B3. `task.yaml` API keys are not honored **[BLOCKER]** (§4.1/§10.3)
Uses singular `required_api:` / `distractor_api:` with a nested custom schema. The
whitelist in `_overlay_yaml_metadata` honors only the **plural** `required_apis` /
`required_mock_apis` and `distractor_apis` / `distractor_mock_apis`. As written, no APIs
register → the mock stack starts nothing.

**Fix:** rename to `required_apis:` / `distractor_apis:` as flat string lists
(copy the shape from `input/Lee Stark/task.yaml`).

### B4. Distractor services have no mock_data **[HARD]** (§10.4)
Scored distractors `coinbase-api, binance-api, kraken-api, alpaca-api` have no
`mock_data/<svc>-api/` dir, but `test_outputs.py` probes them and asserts
`total_requests == 0`. Without a running mock, the probe has no server.

**Fix:** add `mock_data/{coinbase,binance,kraken,alpaca}-api/` seed dirs (or confirm the
harness auto-starts unseeded distractors).

### B5. `test_outputs.py` import hygiene **[WARN]** (§5.3/§5.4)
Has a `try: import pytest / except ImportError` block and an unused `pytest` import.
**Fix:** delete the try/except and the unused import (`pytest` is always available).

### B6. Apply A1, A2, A3.

---

## C. Ruth Armstrong — `input/Ruth Armstrong` (2 BLOCKERS)

### C1. `required_apis` ↔ `mock_data/` mismatch **[HARD]** (§10.3)
`task.yaml` declares 20 `required_apis`; only 10 have a `mock_data/<svc>-api/` dir.
**Missing:** `asana, microsoft-teams, monday, mixpanel, typeform, mailchimp, figma,
reddit, nasa, openweather` (all `-api`).
Critically, **weighted tests probe `asana` and `microsoft-teams`**
(`test_asana_writeback`, `test_teams_*` in `test_outputs.py`) — they cannot pass and the
decoy cannot be detected without seeded services.

**Fix:** seed `mock_data/` for the actively-probed services (at minimum
`microsoft-teams-api`, `asana-api`), or trim `required_apis` to the 10 actually seeded.

### C2. Distractor services unseeded **[HARD per checklist]** (§10.4)
`salesforce-api, linkedin-api, hubspot-api` are listed as distractors with no
`mock_data/` dir. This is **documented intent** (`mock_data/MANIFEST.json` "unseeded /
zero-call invariant"), but conflicts with §10.4.
**Fix:** either seed minimal dirs so the zero-call negative tests have a running server,
or formally accept the exception and confirm the harness still starts unseeded distractors.

### C3. `rubric.json` invalid enums — see **A1**. **[HARD]**

### C4. Inject boundary convention **[WARN]** (§8.4)
`stage1` is empty (`mutations: []`); `stage2`/`stage3` carry only **per-op**
`fires_after_turn` (6 / 12) with no top-level boundary. These **do** load via the post-L
per-op scan, so it is non-fatal — but diverges from Lee Stark's top-level
`fires_after_turn`.
**Fix (optional):** lift `fires_after_turn` to the stage top level; remove or document
the empty `stage1`.

### C5. `persona/TOOLS.md` bloat **[WARN]** (§6.3/§10.2)
Lists ~90 services vs the 20 required + 3 distractor.
**Fix:** trim to `required_apis ∪ distractor_apis` only.

### C6. Apply A2, A3.

### Status of previously-known issues (`docs/ruth_armstrong_task_issues.md`)
| # | Issue | Status |
|---|---|---|
| 2 | Drop `test_behavioral_`/`test_negative_weight_` prefixes | **FIXED** |
| 3 | Clean imports (no try/except pytest, unused subprocess/sqlite3) | **FIXED** |
| 4 | `pass_at_k = 1` | **FIXED** (no task.toml → default) |
| 5 | prompts.txt carries all turns | **FIXED** |
| 6 | rubric enum coverage | **REGRESSED** → now invalid enums (C3) |
| 7 | TOOLS.md = required ∪ distractor only | **STILL OPEN** (C5) |
| 1 | Lift per-op `fires_after_turn` to top-level | **STILL OPEN** (C4, non-fatal) |
| 9 | `solve.sh` golden trajectory | **N/A** (absent) |
| 10 | Rubric justifications | **STILL OPEN** |

---

## D. Lorraine Maddox — `input/Lorraine Maddox` (2 BLOCKERS — closest to passing)

### D1. `rubric.json` invalid enums — see **A1**. **[HARD]**

### D2. Inject `stage1` uses `fires_before_turn` **[HARD]** (§8.4)
`stage1` resolves no recognized boundary (uses `fires_before_turn`) → silently degrades
to a seed and loses its before-turn-8/14 timing (voice note → T8, docusign → T14).
`stage2`/`stage3` are fine (per-op `fires_after_turn`).

**Fix:** move the `stage1` seed ops into `stage0/STAGE0_INJECT.json` (Lee Stark
convention), **or** replace `fires_before_turn: N` with `fires_after_turn: N-1`.

### D3. WhatsApp decoy data gap **[NIT / content]**
`test_whatsapp_decoy_number_used` checks for `555-6604`, but
`whatsapp-api/contacts.csv` has only `6609/6605/6601/6607` — no `6604` decoy row exists,
so the T9 read-back trap has no tempting adjacent contact next to Christine (6605).
**Fix:** add a `555-6604` contact row adjacent to Christine so the trap has real bait.

### D4. Apply A2, A3.

### Already clean (no change needed)
- All `required_apis` (8) and `distractor_apis` (4) have `mock_data/` dirs.
- All 19 `test_weights.json` keys resolve 1:1 to real tests.
- All CSV/JSON parse; prompt headers all match the regex; 7 flat persona MDs present.

---

## E. Recommended fix order

1. **Gloria B1 → B2 → B3** (the three blockers — until fixed the task can't execute).
2. **Rubric enums (A1) in all three** — mechanical find/replace.
3. **Ruth C1** — seed the actively-probed missing APIs (`microsoft-teams`, `asana`).
4. **Lorraine D2** — relocate `stage1` seed to `stage0`.
5. **Gloria B4 / Ruth C2** — distractor mock_data parity.
6. Cosmetic/shared: A2 (manifest location), C5 (TOOLS.md trim), B5 (imports), D3 (decoy row), A3 (optional Multi-Agent section).

## F. Post-fix verification

After applying changes, run (Python-capable, non-sandboxed env):
```bash
# inject pipeline dry-run (no LLM, ~30-60s)
python3 scripts/check_injection.py "input/Gloria Wiggins"
python3 scripts/check_injection.py "input/Ruth Armstrong"
python3 scripts/check_injection.py "input/Lorraine Maddox"

# test discovery sanity
pytest --collect-only "input/<task>/test_outputs.py"
```
Re-run the rubric-enum / weight-resolution / mock-data-parse checks to confirm green.

---

## What is already correct across all three (no change)
- `test_weights.json` keys all resolve 1:1 (Gloria 27, Ruth 15, Lorraine 19 — 0 unresolved).
- All `mock_data` CSV/JSON parse cleanly (125 CSV + 28 JSON, 0 errors).
- 7 flat persona MDs present in each; `prompts.txt` headers pass regex for Ruth & Lorraine.
