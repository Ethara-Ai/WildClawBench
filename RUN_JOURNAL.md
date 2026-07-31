# Trajectory run journal — multi-turn batch 1

**Created:** 2026-07-31
**Source:** `~/Desktop/kensei-dataset/multi-turn-samples/input/` (sparse clone of `Ethara-Ai/kensei-dataset@main`, copied into `input/` on 2026-07-31)
**Model:** `claude-opus-4.7` · **Reps:** 1 (run_1) · **Backend:** openclaw (default)
**Task format:** Talos multi-turn native (`prompts.json` turn schedule + `inject/stageN/mutations.json` + `test_output.py`/`test_weights.json` + `rubric.json` + `task.yaml`)

## Run plan

**Phase 1 — pilot (current):** run task 2 alone (task 1 discarded), verify the
multi-turn loop + scoring work end-to-end on this format before committing to the batch.

```bash
caffeinate -i bash script/run.sh input/abilene_ross_c4350ef7-4af0-4836-8499-2c45e482acb1 claude-opus-4.7 1
```

**Phase 2 — remaining:** once the pilot looks good, either bulk the rest or run
them one-by-one the same way.

```bash
caffeinate -i bash script/run.sh --bulk tasks_multiturn_batch1.txt --model claude-opus-4.7 --reps 1
```

(Bulk mode is sequential by default — `PARALLEL_TASKS=1` in run.sh; already-completed
runs land under `output/openclaw/<task_id>/trajectories/claude-opus-4.7/run_1/`.)

## Task status

| # | Task | Size | Status | Reward (A) | Judge (B) | Notes |
|---|------|------|--------|-----------|-----------|-------|
| 1 | `abilene_ross_739804de-22ab-49ae-b800-e15602c9c67c` | 55M | DISCARDED | — | — | dropped by user 2026-07-31 after failed attempt 1; dir removed from input/ (still in dataset clone) |
| 2 | `abilene_ross_c4350ef7-4af0-4836-8499-2c45e482acb1` | 52M | pilot — attempt 4 ran (9m10s); judge ✓ OAuth works, but injection STILL failed (row-resolution layer) → score 0.00 invalid again | 0/9 (invalid) | 2/15 real verdicts | 3 turns |
| 3 | `adelina_santos_reyes_335aecc7-491b-438c-b727-aba078abcb8f` | 20M | pending | — | — | 3 turns |
| 4 | `amelia_blackburn_f7627970-d881-4984-8a34-c1736fdcb849` | 14M | pending | — | — | 4 turns |
| 5 | `annika_borg_wells_4c153075-ef15-4dd4-983a-32dfe39e6d8a` | 31M | pending | — | — | 18 turns — expect a long run |

## Pre-run checklist

- [ ] Docker daemon up (`run.sh` preflight covers image presence / tag repair / orphan cleanup)
- [ ] Judge credentials valid — Bedrock judge creds were expired as of 2026-07-21; if still expired, either refresh them or rely on the Sonnet-OAuth judge path (`run.sh` sets the 3 judge env vars)
- [ ] `.env` proxy vars left EMPTY (poisoned-proxy workaround; symptom otherwise: `LLM request timed out`)
- [ ] Each task's `mock_data/` APIs present in the fleet (parser will fail loudly if not)

## Log

- 2026-07-31 ~18:15 — Pre-run seed audit found ONE more defect class: seed-stage (stage0) loud ops are skipped by harness design (`replay_loud=False`, overlay is supposed to already carry pre-T0 state) but two tasks' overlays did NOT carry it. Fixed data-side per that contract: abilene — appended `msg-49004` (Corey's forward, stage0 op row) to `mock_data/gmail-api/messages.json` (100 msgs now); annika — folded stage0 patch (`itm-bal-10.group_id: grp-awaiting → grp-marked`) into `mock_data/monday-api/items.json`. Static validator: all 4 tasks VALID. Injection confidence for attempt 5: HIGH (every op path individually verified).

- 2026-07-31 ~18:05 — ADMIN-PLANE PASSTHROUGH implemented (user ruling: environment/ is the task team's source of truth, so the harness must accept ops addressed to the environment's own admin endpoints). `inject_director._replay_admin_rest`: ops with `path` starting `/admin/` are now replayed verbatim (POST `/admin/data/<table>`+`row` → upsert w/ read-back verify; PATCH `/admin/data/<table>/<pk>` → patch w/ before/after verify; other POST e.g. `/admin/inject/raw` → replayed, per-op `results` checked). `inject_validator` taught the same shapes (`_op_kind` classifies bare admin-path ops as upsert/patch/admin_raw; creates fold into snapshot; bare-PATCH pk target-checked). Fixed a 3rd stale 3-tuple stub in `test_validate_and_injection_scripts_units.py` (same Akshat-merge breakage class). Verification: 4 stubbed-HTTP unit checks of the replay path PASS (incl. raw sub-op failure detection); 74 passed/3 skipped across all inject-related suites; static validator VALID on all 4 tasks. Net effect: dataset tasks in the original bare-REST style now work WITHOUT per-task data conversion; the admin blocks added earlier remain valid (take precedence) and are kept.

- 2026-07-31 ~17:45 — FULL FIX applied (user: "Fix it"), both sides:
  **Code** (uncommitted): (1) `inject_director.py` — new `_table_pk`/`_row_pk`: row lookups now honor each table's `primary_key` as declared by `/admin/tables` (fixes quickbooks `Id`, monday `item_id`, servicenow `sys_id`, etc.) at all 3 lookup sites; (2) `inject_validator.py` — seed-snapshot id collection made pk-case/name-insensitive (same bug class, was false-flagging bill 3419 as missing); (3) `test_injection_integrity.py` — updated 2 stale 3-tuple `_resolve_target` stubs broken by Akshat's 4-tuple merge (pre-existing failure, verified not caused by our changes).
  **Data**: 9 ops converted to explicit `admin` blocks across 3 tasks — abilene ×5 (3 gmail upserts, 2 qb patches bills/3419 + vendors/9412), adelina ×1 (notion comment upsert, row built to comments schema), amelia ×3 (raw batches unwrapped: 1 gmail upsert, 2 etsy patches). Original REST body/path kept in-file for provenance; `admin` block takes precedence in the applier.
  **Verification**: 54 passed/3 skipped across smoke+inject+prompts_json suites; static authoring validator now VALID on all 4 tasks. Prior run outputs already cleaned. Pilot ready for attempt 5.

- 2026-07-31 16:12 — Pilot attempt 2 (task 2) ran end-to-end (agent OK: 3 turns, 8m33s) but score 0.00 is INVALID — two infra failures:
  1. **Injection never fired** (`injection_ok: False`, 4 ops unresolved "no admin URL for quickbooks/gmail"): dataset `mutations.json` uses bare service names (`gmail`, `quickbooks`) while the harness URL map keys are fleet names (`gmail-api`, `quickbooks-api`). Mid-run env changes never happened → all 9 Channel-A tests failed/errored legitimately-but-meaninglessly. ~~FIXED via harness fallback~~ **REVERTED** — user ruling: the `<name>-api` convention is the operational contract; task DATA must conform, not the code. Harness back to pristine (smoke + inject suites green post-revert).
  2. **Judge council Bedrock HTTP 403 on all 15 criteria** (expired Bedrock creds, known since 2026-07-21). Channel B unscored. NOT fixed — needs user to refresh AWS creds; Channel B can be re-graded post-hoc via `script/regrade.py` once creds are valid.
- NEXT: user refreshes Bedrock creds (optional for trajectory; required for judge), then reruns pilot task 2.
- 2026-07-31 — Root-cause audit of service naming across all 4 tasks' `inject/*/mutations.json`: the DATASET is internally inconsistent. `amelia_blackburn` uses correct fleet names (`etsy-api`, `gmail-api`); `abilene_ross_c4350ef7` (`gmail`, `quickbooks`), `adelina` (`notion`), `annika` (`google-classroom`, `monday`) use bare names → authoring defect in those 3 tasks. Harness fallback reverted; pending decision: patch the 3 tasks' mutations.json locally (bare → `-api`) and/or report upstream to the task team. Judge plan: `--use-claude-oauth` (Sonnet-over-OAuth, no Bedrock creds needed).
- 2026-07-31 — Fresh start for pilot: deleted invalid attempt-2 output (`output/openclaw/<task>` 216M incl. cached tests, `output_bundle/<task>` 160M). Logs under `logs/` kept for history. Attempt 3 = fixed data + `--use-claude-oauth` judge.
- 2026-07-31 ~16:40 — Attempt 3 (txt-path) KILLED mid-run at user request: running on the txt path was futile since delivery requires json-driven runs. Leaked containers/network cleaned (agent, per-task mocks, shared mocks, wcbsh net). `stash@{0}` APPLIED (stash kept as backup) → prompts.json support restored; verified `load_task` now json-driven (`turn_schedule` present, 3 turns). Partial attempt-3 output deleted. Smoke 6/6 green. All 4 tasks confirmed to have the prompts.json + prompts.txt pair. Attempt 4 = json path + fixed inject names + OAuth judge.
- 2026-07-31 ~16:58 — Attempt 4 results: OAuth judge WORKS (15 real verdicts, $0 cost, no 403s); naming fix WORKS (admin URLs resolved); but all 4 mid-run inject ops now fail one layer deeper: "could not locate target row in live store". Root causes found by code+data audit:
  (a) **Bare REST POST creates are unsupported by harness contract** — in-code doc (inject_director ~L456): inserts MUST use explicit `admin` block with `op: upsert`; bare POSTs log `unresolved` by design. Affects: abilene gmail ×3 (well, 2 mid-run), adelina notion comment ×1, amelia `/admin/inject/raw` ×3 (endpoint exists in admin plane but applier has no raw passthrough).
  (b) **Fuzzy resolver pk lookup is lowercase-only** (`row.get("id") or row.get("pk")`) while quickbooks registers `primary_key="Id"` → abilene's 2 quickbooks PATCHes can never match. (Resolver ignores the `primary_key` that `/admin/tables` advertises — harness limitation, but explicit admin-block form sidesteps it.)
  Per-task prognosis: abilene 4/4 mid-run ops broken; adelina 1/3 broken (2 notion PATCHes fine, pk="id"); amelia 3/3 broken (raw-batch form); annika likely 0 broken (classroom pk="id"; monday op is seed-stage, skipped by design). DECISION PENDING: convert op data to explicit admin blocks (per user's data-conforms-to-harness ruling) vs extend harness.
- 2026-07-31 — DATA FIX applied (user approved): 11 `service` values corrected to `-api` form across the 3 defective tasks — abilene_c4350ef7 ×5 (stage0/1/2), adelina ×3 (stage1/2), annika ×3 (stage0/1/2). JSON-aware rewrite, verified zero bare names remain in our 4 tasks. NOTE for ops team: same defect exists in pre-existing local tasks (`davis_lab_worry` inject uses bare `gmail`; `davis_hotsauce_skufix` inject no-op previously observed) — not touched, outside batch scope.

- 2026-07-31 — 5 tasks copied from sparse clone into `input/`; batch file `tasks_multiturn_batch1.txt` created. Not yet run.
- 2026-07-31 — Plan changed to pilot-first: task 1 (`abilene_ross_739804de`) will be run alone by the user via terminal before batching the other 4. Verified: images all present locally, no containers running, 183 GB disk free, bulk mode confirmed sequential (PARALLEL_TASKS=1).
- 2026-07-31 15:51 — Pilot attempt 1 FAILED at task-parse (rc=1, ~42s): dataset tasks ship `prompts.json` without the companion `prompts.txt` that `task_parser._load_native_task` hard-requires (txt = bundle passthrough, json = authoritative trajectory). Preflight itself was healthy (docker OK, agent image re-tagged, mock stack 101 APIs healthy, sidecar up, auth=OAuth Claude Max, judge council=sonnet).
- 2026-07-31 — Commit `e36c8ea` (prompts.json turn schedules + inject passthrough) uncommitted and stashed as `stash@{0}` at user's request; master now at `19c353a`. Tasks load via the legacy `prompts.txt` path (generated companions carry identical turn text) — verified `load_task` OK post-stash.
- 2026-07-31 — Task 1 (`abilene_ross_739804de`) DISCARDED at user's request; removed from `input/` and commented out of the batch file. Pilot moves to task 2 (`abilene_ross_c4350ef7`, 3 turns).
- 2026-07-31 — FIX: auto-generated companion `prompts.txt` from `prompts.json` for all 5 tasks (`--- TURN Tn (day D, HH:MM) ---` format); validated turn-count + text parity with the repo's own `parse_prompts_file`/`parse_prompts_json` (zero drift warnings expected), and `load_task()` on the pilot now succeeds. Turn counts: abilene×2 = 4 & 3, adelina = 3, amelia = 4, annika = **18 turns** (expect a long run for that one).
