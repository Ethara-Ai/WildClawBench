# New Fields Reference — Run Integrity, Cost Attribution & Turn Repairs

Every field added to harness artifacts by the 2026-08/09 fixes (usage
attribution, turn completion, eval gating, stall guard, turns_duplicated),
with the file that writes it and when it appears.

**Design invariant: every field is additive and conditional.** A clean,
complete, normally-judged run produces artifacts byte-shape-identical to
pre-fix output. Fields appear only when the condition they record actually
occurred, so legacy bundles and downstream tooling never see a schema change
they did not opt into.

---

## 1. `score.json` (per run)

Writer: `eval/run_batch.py::_augment_score_with_combined_rewards` (+ the
eval-skip stub and last-resort stub paths).

| Field | Type | Present | Meaning |
|---|---|---|---|
| `run_incomplete` | bool | every multi-turn run | `turns_completed < turns_planned` — gated purely on turn count, never on the timeout flag |
| `turns_planned` | int \| null | every multi-turn run | the schedule's turn count (null = open-ended source) |
| `turns_completed` | int | every multi-turn run | turns whose agent invocation actually finished |
| `recovery_turn_fired` | true | only when fired | the §2.2 sub-agent synthesis-recovery injection added one real extra turn (legitimate repair; run stays in averages) |
| `turns_duplicated` | list[int] | only when non-empty | turn indices whose stall-guarded retry re-sent the scripted message on the same session (§2.3; recorded at `turn_attempt == 1`, not re-derived from the transcript; run stays in averages) |
| `eval_skipped` | string | only when eval was gated | why pytest grading + LLM judge never ran (e.g. `"run incomplete: 3 of 9 scheduled turns executed"`); `overall_score` is `null` = not measured, mirroring the last-resort stub convention |

The last-resort stub (`__last_resort_stub__: true`) also carries
`run_incomplete` / `turns_planned` / `turns_completed` so a run that crashed
before any grader wrote is still excludable downstream.

`judge_model: "human_eval"` + per-criterion `human_eval: "completed"` /
`judges: ["human"]` are a data convention for manually evaluated runs
(used when every automated judge surface refused), not a schema change.

## 2. `pass_summary.json` (per task)

Writers: `eval/run_batch.py::_pass_summary_entry/_pass_summary_doc`,
`script/rebuild_pass_summary.py`, `script/merge_pass_summaries.py`,
`script/backfill_pass_summary.py`, `script/backfill_test_scoring.py`.

| Field | Level | Present | Meaning |
|---|---|---|---|
| `run_incomplete`, `turns_planned`, `turns_completed` | per_run entry | flagged runs only | entry preserved in `per_run` (never a silent disappearance) but excluded from every average |
| `turns_duplicated` | per_run entry | when present | visible repair marker; entry stays IN averages |
| `runs_used` | doc | only when exclusion fired | how many runs the averages were computed from |
| `runs_excluded_incomplete` | doc | only when exclusion fired | how many runs were excluded as incomplete |

Averaging policy (all five writers, identically): incomplete runs are
excluded by default; `WCB_INCLUDE_INCOMPLETE_RUNS=1` folds them back in for
debugging. `turns_duplicated` / `recovery_turn_fired` runs are always
included — they are repairs, not failures.

## 3. `usage.json` (per run)

Writer: `src/agents/openclaw/runner.py::collect_usage` →
`eval/run_batch.py::save_usage`.

| Field | Present | Meaning |
|---|---|---|
| `usage_source: "litellm_run_key"` | new VALUE on `sources.agent` | usage was attributed by exact per-attempt run-key match — immune to concurrent runs on the shared sidecar. Legacy `"litellm"` = time-window fallback (over-attributes under parallelism) |
| `subagent_usage_folded` | only when director spawns exist | whether spawn-ledger tokens/cost were added into run totals. `false` = a litellm-sourced extraction already counted that traffic (folding again would double-count); `true` = transcript/estimated source missed it and the fold was applied |

## 4. `usage.jsonl` (sidecar per-request log)

Writer: `src/utils/litellm_usage_callback.py`.

| Field | Rows | Meaning |
|---|---|---|
| `run_key` | success AND failure rows | `wcb::<task_id>::<uuid4>` minted per ATTEMPT (never derived from task+run_N — retries/parallel reps cannot collide). Only `wcb::`-prefixed values are ever written; real credentials never reach the log. Extraction channels cover litellm 1.88.1 (`metadata.user_api_key` raw bearer in keyless mode; `x-wcb-run-key` header for subagent/audio calls under any auth) |
| `error_class` | failure rows | exception class name (e.g. `APIConnectionError`, `ProxyModelNotFoundError`) |
| `error` | failure rows | first 300 chars of the exception message — class + message only, never the request payload |

## 5. Aggregation / bundling outputs

`script/aggregate_runs.py`: excluded-incomplete counts per (backend, task,
model); stub entries when ALL runs of a task are incomplete (so the task
reads "no data", not "scored zero"); `turns_duplicated` on entries.

`script/repackage_to_bundle.py`: bundle pass_summary exclusion identical to
§2; `run_incomplete` flag in the repackage report; stderr warnings when a
bundle carries flagged runs.

## 6. `AgentExecution` (code struct, `src/agents/base.py`)

New fields: `turns_planned` (int | None), `recovery_turn_fired` (bool),
`turns_duplicated` (list[int]). Pre-existing `turns_completed` /
`timed_out_turn` were previously computed and discarded; `eval/run_batch.py`
now consumes all of them, including on the crash path (a run that raises
mid-schedule is flagged exactly like a timeout).

Known limitation: only the openclaw runner populates the turn fields.
`codex` / `claudecode` / `hermesagent` leave defaults, so incompleteness
detection is a no-op on those backends (BUGREPORT §3 item 2, open).

## 7. Environment variables

| Var | Default | Effect |
|---|---|---|
| `WCB_SIDECAR_NO_MASTER_KEY=1` | off | keyless sidecar: master key removed from yaml+env, agent bearer becomes the per-run key → exact attribution (`litellm_run_key`). Off = master-key auth, window fallback, bit-identical legacy behavior |
| `WCB_INCLUDE_INCOMPLETE_RUNS=1` | off | debug: fold incomplete runs back into all averages |
| `WCB_GRADE_INCOMPLETE_RUNS=1` | off | debug: run pytest grading + LLM judge on incomplete/empty trajectories anyway |
| `WCB_TURN_STALL_SECONDS` | unset (off) | arms the per-turn stall guard; values below 600 are floored to 600 (usage rows land at request COMPLETION, so smaller windows would misread healthy long calls as wedges). Requires run-key tagging live (keyless mode); self-disables otherwise |
| `WCB_RUN_KEY` | auto-set per attempt | injected into the agent container; `subagent_director` and `transcribe.sh` send it as the `x-wcb-run-key` header (works under master-key auth too). Never set manually |
| `JUDGE_MAX_EVIDENCE` | unset (family budgets) | pre-existing operator cap on judge evidence chars. Operationally important: judge inputs above ~270K tokens empirically collapse sonnet verdict output (empty/unparseable/partial); ≤160K tokens has been 100% clean. 500000 chars is a proven-good value for oversized runs |

---

## Commits introducing these fields

| Commit | Fields |
|---|---|
| `addb746`/`c06caa5` | run keys, turn accounting (`AgentExecution`), `usage_source: litellm_run_key` |
| `581cb3b`/`0ab7ac9` | `run_incomplete`/`turns_*` stamps, `runs_used`/`runs_excluded_incomplete`, all consumers |
| `53a44d4` | `eval_skipped` + eval gate |
| `3e78f1a` | `subagent_usage_folded` |
| `faed133` | litellm 1.88.1 run-key channels |
| `e1ff517` | failure-row `error_class`/`error`/`run_key` |
| `31756b6` | stall guard (`WCB_TURN_STALL_SECONDS`) |
| `b5c13db` | `turns_duplicated` |
