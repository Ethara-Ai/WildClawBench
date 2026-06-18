# Injection Issues — 4 New `(trj)` Tasks (Helen, Ian, Midori, Ruth)

**Date:** 2026-06-18
**Tasks validated:** `input/Helen sexton(trj)`, `input/Ian Salazar(trj)`, `input/Midori_Kelley(trj)`, `input/Ruth Armstrong(trj)`
**Scope:** mock-API (silent / admin-plane) injection — *not* filesystem/data-file ops.
**Authoritative test:** `scripts/check_injection.py "input/<task>"` (real `InjectApplier`, spins each task's mock stack, no LLM).

---

## 0. TL;DR

| Task | Live injection | Verdict |
|---|---|---|
| **Ruth Armstrong(trj)** | **5/5 applied** | ✅ PASS — no injection fix needed |
| **Helen sexton(trj)** | 1/5 applied | ⚠️ 4 unresolved (3 mock-API + 1 filesystem) |
| **Midori_Kelley(trj)** | 1/3 applied | ⚠️ 2 unresolved (fedex, salesforce) |
| **Ian Salazar(trj)** | 0/1 applied | ❌ 1 unresolved (gmail) |

All failures are **fixable with task-file edits only** (inject JSON + one seeded row). **No code changes** —
the `inject_director.py` / `hubspot_data.py` fixes from the prior batch already cover the relevant cases.

---

## 1. Two-layer verdict — read this first

There are **two independent questions**, and they give different answers:

### (a) Spec-checklist conformance (`docs/task_format.md`)
Static validators marked **all 4 = FAIL**, dominated by **invalid `rubric.json` enums**
(`state_change`, `trajectory`, `tool use`, `agent behavior`).

**This is cosmetic — it does NOT affect runs.** Proven three ways:
1. **Grading never reads those fields.** `src/utils/grading.py` uses only `criterion` (judge prompt text)
   and `score` (weight) + the weight's sign. It never reads `evaluation_target` or rubric `type`.
2. **`evaluation_target` is barely referenced** (4 spots, none in the grading/run path): a taxonomy label
   in `task_parser.py`, `testgen/generator.py`, and `harbor/bundle.py:93` — which **defaults it to
   `"state change"`**, i.e. the codebase's own default isn't even a "valid" spec value. So it is not enforced.
3. **Empirical:** the already-run tasks Larry/Mary/Jacob carry the identical "invalid" enums and graded
   fine (Larry 20/27, Mary 17/35, Jacob 7/25 rubric criteria).

➡️ **Conclusion:** ignore the rubric-enum "FAIL" for runnability. (Optional cleanup only.)

### (b) Mock-API injection (the functional question that matters)
This is real, and it must be measured by the **live test**, not the static checklist. The static validators
were **unreliable here** — examples:
- Ruth was statically called the **worst** ("stages won't fire") → live test shows **5/5 PASS**.
- Ian/Midori were statically called **"clean"** → live test shows **0/1** and **1/3**.

---

## 2. Why the static validator was wrong on injection (two traps)

1. **False "won't fire" (Ruth).** The validator claimed Ruth's stages won't fire because they use per-op
   `fires_after_turn` with no top-level boundary. **Wrong** — the loader
   (`src/utils/inject_director.py:207–215`) falls back to scanning per-op `fires_after_turn` and fires at
   the min; the comment literally names "Ruth Armstrong" as the example. Ruth's stages fire; all 5 admin-block
   ops resolve.
2. **Checker blind-spot (the Alden lesson).** `check_injection.py` only exercises `_resolve_target` and
   `_apply_admin_op`. It does **not** exercise `_apply_batch_records` (airtable/nasa `body.records[]`),
   GraphQL, or `_apply_seed_insert` (`POST` with a `messages:[…]`/`records:[…]` **list**). So those shapes
   report a **false `UNRESOLVED`** in the checker even though the real run applies them — this is exactly what
   happened to Alden's gmail seed-insert (UNRESOLVED in checker, APPLIED in the real run).

**Important:** none of the unresolved ops in §3 below are batch/seed-list/GraphQL shapes — they are **plain
PATCH/POST** ops that go through `_resolve_target` in *both* the checker and the real run. Their shapes were
inspected individually, so the checker verdict is trustworthy for these (verified, not assumed).

---

## 3. Per-op root cause + exact fix

Common pattern: the op addresses a row by the **wrong key** (unsubstituted placeholder or wrong pk), or uses a
**POST-create / flat body** the resolver can't patch. Fix = point an explicit `admin` block at the **real
seeded pk**, or `admin upsert` for inserts.

### Helen sexton(trj) — 1/5 applied (SM-02 tmdb applied)

| Op | Service | Root cause | Fix |
|---|---|---|---|
| **SM-01** | notion-api | Targets `/v1/blocks/{ep1_sponsor_block_id}` — a **literal unsubstituted placeholder**; no sponsor block is seeded (`blocks.csv` ids: `b_ep1_h1`, `b_ep1_logline`, …) | Seed a real block `b_ep1_sponsor` (page_id `p_ep1_outline`) in `mock_data/notion-api/blocks.csv`, then add `admin:{op:patch, table:blocks, pk:b_ep1_sponsor, set:{text:…, last_edited_time:…}}` |
| **SM-05** | eventbrite-api | Targets `{premiere_event_id}/ticket_classes/{vip_class_id}` — placeholders, but the **real rows already exist** (`events:EVT_LF_PREMIERE`, `ticket_classes:TC_VIP`) | `admin:{op:patch, table:ticket_classes, pk:TC_VIP, set:{cost:…, previous_cost:…}}` — trivial, row already seeded |
| **SM-04** | figma-api | `POST .../files/lf_cover_art/versions` (create-version) — no `versions` table, POST-create isn't patchable; `files.csv` has `file_key:lf_cover_art` | `admin:{op:patch, table:files, pk:lf_cover_art, set:{last_modified:…, name:…}}` — model the new version as a file update |
| SM-03 | data-folder | **Filesystem op** (overwrites a data file) — `service:"data-folder"` isn't a mock-API; **out of mock-API scope** | (Optional) `service:"data-folder"` → `"filesystem"`; or leave as-is since it's not mock-API |

### Ian Salazar(trj) — 0/1 applied

| Op | Service | Root cause | Fix |
|---|---|---|---|
| **S2-01** | gmail-api | `POST .../users/me/messages` with a **flat body** (`from/to/cc/subject` at top level) — not a `messages:[…]` seed-insert **list**, so it never takes the insert path; falls to `_resolve_target` → no row | **Same fix as Alden:** `admin:{op:upsert, table:messages, pk_field:id, row:{id:msg_<new>, thread_id, from_addr, to_addr, cc_addr, subject, snippet, body, date, labels, is_unread:true}}`. `messages.csv` is seeded — proven pattern. |

### Midori_Kelley(trj) — 1/3 applied (SM-01 hubspot applied)

| Op | Service | Root cause | Fix |
|---|---|---|---|
| **SM-02** | fedex-api | Plain PATCH; row `FX-HND-2026-Q4` **exists** in `tracking.csv`/`shipments.csv`, but the pk column is `tracking_number` (not `id`) → a plain PATCH fails `_resolve_target` | `admin:{op:patch, table:tracking, pk:FX-HND-2026-Q4, set:{status_code:…, status_description:…}}` — the `_patch_row` registered-pk fallback (already in code) resolves it via an admin block (same as Jacob's fedex) |
| **SM-03** | salesforce-api | Targets pk `Henderson_Ranch`, but the seeded Account `Id` is **`001Ax0HND2026Q4A1`** (Name="Henderson Ranch") → **pk mismatch** | `admin:{op:patch, table:accounts, pk:001Ax0HND2026Q4A1, set:{…}}` — address the real Id. (Note: set-fields not in `accounts.csv` columns will be added; confirm the salesforce mock surfaces them if the trap needs the agent to read them.) |

### Ruth Armstrong(trj) — 5/5 applied ✅
No injection fix needed. All 5 silent ops use explicit `admin` blocks against seeded rows and applied live:
`google-calendar-api events/evt_final_consult`, `notion-api properties` (×2), `airtable-api records_tblContacts/recBIZ01`,
`gmail-api messages/msg_portal_feedback`.
(Ruth still has spec-checklist [hard] items — rubric enums, missing `## Multi-Agent Turns`, 13 missing
`mock_data/` dirs, TOOLS.md bloat — but none of those block the **mock-API injection**, which is clean.)

---

## 4. Fix recipe (mechanical)

For each unresolved op above, edit only the task's `inject/stage*/STAGE*_INJECT.json` (and, for Helen SM-01,
add one row to `mock_data/notion-api/blocks.csv`):

1. Keep the existing `http` envelope (documents intent).
2. Add a sibling `"admin"` block:
   ```json
   "admin": { "op": "patch", "table": "<store_table>", "pk": "<real_seeded_pk>", "set": { ...changed_fields... } }
   ```
   Use `"op": "upsert"` with `"pk_field"` + `"row"` for inserts (Ian gmail).
3. The `admin` block is dispatched deterministically by both the real applier (`_apply_admin_op`) and the
   checker — no fuzzy URL/body resolution, no pk-column guessing.

The pk to use comes directly from the seeded `mock_data`:
- Helen eventbrite → `TC_VIP`; figma → `lf_cover_art`; notion → seed `b_ep1_sponsor` first.
- Midori fedex → `FX-HND-2026-Q4`; salesforce → `001Ax0HND2026Q4A1`.
- Ian gmail → upsert a new `msg_*` row.

---

## 5. Verification

After fixing, re-run the live test per task and require `unresolved=0`, `state_changed == silent_ops`:
```bash
sg docker -c '.venv/bin/python scripts/check_injection.py "input/Helen sexton(trj)"'
sg docker -c '.venv/bin/python scripts/check_injection.py "input/Ian Salazar(trj)"'
sg docker -c '.venv/bin/python scripts/check_injection.py "input/Midori_Kelley(trj)"'
```
(These are plain ops, so the checker verdict is authoritative here — no batch/seed-list/GraphQL blind spot.)

---

## 6. Open item (NOT injection) — `tests_failed: 24`

Separate from injection: Alden/Mary/Lamar's prior runs show **0 pytest passed** (e.g. Alden
`tests_failed: 24`). This was hypothesized as a **grading-infra** issue (mock stack torn down before pytest
ran, or `*_API_URL` not wired into the test executor) but **NOT verified**. Needs a look at the test-executor
logs to find the real cause before trusting any pytest leg of the score.

---

## 7. Systemic note
All 4 new tasks (and the prior 6) share the same invalid-rubric-enum signature and the same
wrong-pk/placeholder injection-op signature. This strongly implies the **task-generation template** emits both
defects. Fixing the generator (valid enums + admin-block ops addressing real seeded pks) would prevent every
task in this family from needing the same hand-fixes.
