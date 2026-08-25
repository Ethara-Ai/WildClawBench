# Changelog — Usage Attribution & Run Integrity (2026-08-25)

Nine commits (`3d35492..ac9691d` on `master`) fixing four defects found during
the delivery-1 audit. Verified against the full test suite (4265 passed; the
31 failures on master predate these changes) and against real delivery-1 data.

| # | Commit | Change |
|---|--------|--------|
| 1 | `3d35492` | Per-run usage attribution keys + turn completion tracking (runner) |
| 2 | `3ba5548` | Run-key tagged usage extraction with time-window fallback |
| 3 | `ba33e1a` | Keyless sidecar mode and `x-wcb-run-key` header channels |
| 4 | `cf28b23` | `run_incomplete` stamped in score.json + batch pass-summary exclusion |
| 5 | `ed5b958` | Incomplete-run exclusion ported to all summary/backfill scripts |
| 6 | `d0f4535` | Test suite for fixes 1–2 (`tests/test_turn_completion_and_run_key.py`) |
| 7 | `030ba42` | Spawn-tree fold double-count + subagent_count inflation fix |
| 8 | `ac9691d` | Eval phase (pytest grading + LLM judge) skipped for incomplete/empty runs |

---

## Fix 1 — Per-run usage attribution (commits 1–3)

### Problem

All concurrent runs share one LiteLLM sidecar and one `usage.jsonl`. Usage per
run was extracted by a ±2s-padded **time window**, so every parallel run's
traffic was swept into every other run's totals. Measured on delivery-1:
**1.4x–62.7x** per-run inflation, **$29,023 reported vs ~$5,877 actual**
(4.94x fleet-wide).

### What changed

- `src/agents/openclaw/runner.py` — every attempt mints a unique key
  `wcb::<task_id>::<uuid4>`. When the run-key bearer is live (see runtime
  section) the key replaces the master key as the agent's bearer token
  (`_set_model` apiKey sites, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`,
  `LITELLM_API_KEY`). `WCB_RUN_KEY` is also exported into the container env.
- `src/utils/litellm_usage_callback.py` — each usage row records the run key
  found in the request headers (`x-wcb-run-key`, `authorization` bearer, or
  `x-api-key`). Only `wcb::`-prefixed values are ever written; real
  credentials never touch the log.
- `src/utils/grading.py::extract_usage_from_litellm_log` — attribution order:
  1. exact run-key match (`usage_source: "litellm_run_key"`) — immune to
     concurrency; 2. legacy time window, only when no tagged rows exist
     (loud warning when a key was expected but unmatched).
- `src/utils/litellm_sidecar.py` — `WCB_SIDECAR_NO_MASTER_KEY=1` starts the
  sidecar with no `master_key` in yaml or env (auth off), which is what lets
  the bearer carry the run key instead.
- `src/utils/subagent_director.py` + `environment/skills/audio-extract/
  scripts/transcribe.sh` — always send `x-wcb-run-key: $WCB_RUN_KEY`; this
  header channel works even under master-key auth.

### Why the extraction is gated

Under default master-key auth, only subagent/audio helper requests would be
tagged (they send the header explicitly); matching on that subset would
undercount worse than the window over-counts. So `collect_usage` passes the
run key to the extractor **only when the bearer is live**. Result: with
default config, behavior is bit-for-bit identical to before.

---

## Fix 2 — Turn-completion integrity (commits 1, 4, 5)

### Problem

(`BUGREPORT_turn_completion.md`) Runs that executed fewer scripted turns than
the task defines (delivery-1: 39 short runs of 800) were averaged into pass@K
as if they measured the full scenario, silently skewing scores — e.g.
michael_lee 0.6168 reported vs 0.7833 on complete runs only.

### What changed

- `src/agents/base.py` / runner — `AgentExecution` carries `turns_planned`,
  `turns_completed`, `recovery_turn_fired`; populated on the happy path AND
  the crash path (a run dying at turn 4/9 is flagged identically).
- `eval/run_batch.py` — `run_incomplete = turns_completed < turns_planned`
  (count only, never the timeout flag), stamped into score.json next to
  `injection_ok`, with a loud `RUN INCOMPLETE` warning.
- Every summary producer excludes incomplete runs from averages while keeping
  them visible in `per_run`, and reports `runs_used` /
  `runs_excluded_incomplete` when exclusion fired: `eval/run_batch.py`,
  `script/rebuild_pass_summary.py`, `script/aggregate_runs.py`,
  `script/merge_pass_summaries.py`, `script/repackage_to_bundle.py`,
  `script/backfill_pass_summary.py`, `script/backfill_test_scoring.py`.
- Legacy score files (no flags) produce byte-identical output.

---

## Fix 3 — Subagent cost double-count (commit 7)

### Problem

`collect_usage` folded `spawn_tree.jsonl` (director subagent ledger) tokens +
cost into run totals unconditionally. Director spawns call the same sidecar,
so any litellm-sourced extraction already counted that traffic — the fold
double-counted it. Additionally, ledger `kind: "summary"` rows were counted
as spawns, inflating `subagent_count` (~2x). Latent in delivery-1 (all
ledgers empty — the director tool was never invoked), fatal once used.

### What changed

- Summary rows are skipped outright when reading the ledger.
- Ledger totals fold into `input_tokens`/`output_tokens`/`cost_usd` **only**
  when `usage_source` is not litellm-based (`litellm`, `litellm_run_key`,
  `litellm_oauth` already include spawn traffic; transcript/estimated/none
  miss it). New field `subagent_usage_folded` records which case applied.
- Note: openclaw-native subagents (`meta_info.subagents` — 351/568 delivery-1
  runs) were never affected; they only appear via the sidecar log and are
  correctly attributed by the run key.

---

## Fix 4 — Eval-phase cost gate (commit 8)

### Problem

Broken or incomplete trajectories still went through the full eval phase:
deterministic pytest grading AND the LLM rubric judge. Judge tokens were
burned grading partial garbage whose score is excluded from averages anyway.
An empty trajectory (zero assistant messages, no `error` flag) also went
straight to the judge.

### What changed (`eval/run_batch.py`)

- `_eval_skip_reason(result, messages)` — shared gate: skip when
  `run_incomplete`, or (judge site) when the trajectory has no assistant
  messages.
- `grade_the_task` — incomplete runs never reach `run_grading`; a stub
  score.json is written with `overall_score: null` ("not measured", same
  convention as the last-resort stub), the skip reason under `eval_skipped`,
  and the `run_incomplete`/`turns_*` stamps so aggregators exclude it.
- `_build_trajectory` — same gate before `grade_with_rubric`; zero judge
  tokens spent; `EVAL SKIPPED` warning in the log.

---

## Runtime / operations

### Env switches

| Variable | Default | Effect |
|----------|---------|--------|
| `WCB_SIDECAR_NO_MASTER_KEY=1` | off | Keyless sidecar: no `master_key` in yaml/env, agent bearer becomes the per-run key → exact per-run usage attribution (`usage_source: litellm_run_key`). Security tradeoff: sidecar accepts unauthenticated requests — only for isolated/EC2-internal deployments. |
| `WCB_INCLUDE_INCOMPLETE_RUNS=1` | off | Debug: include incomplete runs in pass@K averages again (all summary + backfill scripts). |
| `WCB_GRADE_INCOMPLETE_RUNS=1` | off | Debug: run pytest grading + LLM judge on incomplete/empty trajectories anyway. |
| `WCB_RUN_KEY` | auto | Set by the runner per attempt; consumed by `subagent_director` and `transcribe.sh` to send the `x-wcb-run-key` header. Never set manually. |

### What to do at runtime

1. **Nothing, for identical-to-before behavior.** With no env changes, every
   fix is either pure bookkeeping (flags, stamps, exclusion of runs that were
   invalid anyway) or dormant (run-key bearer inactive under master-key auth).
2. **To activate exact per-run cost attribution**: launch batches with
   `WCB_SIDECAR_NO_MASTER_KEY=1`. Verify the first run's usage.json shows
   `"usage_source": "litellm_run_key"`.
3. **Reprocessing old bundles**: rerun `script/rebuild_pass_summary.py` /
   `script/repackage_to_bundle.py` — incomplete runs are then excluded and
   `runs_excluded_incomplete` appears in the summaries. Legacy runs without
   flags are untouched.
4. **Pending one-time smoke check (needs amd64 infra — image does not run on
   this ARM host)**:
   - one keyless-mode run to confirm LiteLLM v1.99.0 exposes
     `secret_fields.raw_headers` to the callback (code degrades to the window
     if not);
   - check whether openclaw's provider config accepts a custom headers field
     — that would enable main-agent tagging WITHOUT the keyless tradeoff.

### New fields reference

| File | Field | Meaning |
|------|-------|---------|
| score.json / pass_summary per_run | `run_incomplete`, `turns_planned`, `turns_completed`, `recovery_turn_fired` | Turn-completion verdict |
| score.json | `eval_skipped` | Why the eval phase was skipped (fix 4) |
| pass_summary.json | `runs_used`, `runs_excluded_incomplete` | Present only when exclusion fired |
| usage.json | `usage_source: litellm_run_key` | Exact tagged attribution was used |
| usage.json | `subagent_usage_folded` | Whether spawn-ledger totals were added to run totals (fix 3) |
| usage.jsonl (sidecar log) | `run_key` | `wcb::<task_id>::<uuid4>` tag per request |

### Known items deliberately out of scope

- OAuth usage path still window-based (pre-existing).
- Transcript-JSONL fallback misses native-subagent trajectory files
  (fallback fires only when the sidecar log is unavailable).
