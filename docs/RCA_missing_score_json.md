# RCA — Intermittent missing `score.json`

**Symptom (as reported):** sometimes a completed run under
`output/openclaw/<task>/trajectories/<model>/run_N/` had no `score.json`.
Reported example: `output/openclaw/Ruth Armstrong task input/trajectories/claude/run_1/`.

**Status of the symptom:** FIXED on main by commit `706db77`
("FIX:score.json not being generated fixed.", akshat.gharpure@kuberha.com,
2026-07-09): a last-resort stub (`__last_resort_stub__: true`,
`overall_score: null`) is now written in `run_single_task`'s finally block
whenever no grader produced a score.json, and the judge-stub write path was
hardened (broadened excepts, `default=str`, `exc_info=True`).

**Status of the causes:** FIXED in the accompanying change set (see
`CHANGES_missing_score_json.md`). `706db77` guarantees a *file*, not a
*score*: when a trigger fired, the run was still lost — the agent's work, the
Bedrock tokens, and the wall-clock time produced a null stub instead of a
grade. This RCA documents the two root causes with proof and confidence;
sections 2–3 describe pre-fix behavior (line refs are `main @ 706db77`).

**Review status:** both fixes ACCEPTED by TL review of 2026-07-09; all
follow-ups (F1–F7) closed — resolution details in §5 and in
`CHANGES_missing_score_json.md`.

**Date:** 2026-07-09 · **Baseline:** `main @ 706db77` · all `path:line` refs verified against this commit.

---

## 1. How a run loses its score (the chain, on current main)

```
 trigger: agent container dies mid-run          (RC-1: killed by the orphan
          or transient docker failure                  sweep — proven possible;
          at transcript-copy time                RC-2: fragile single-shot copy)
                        ▼
 src/agents/openclaw/runner.py:277-283 — agent exit code logged, IGNORED
   → execution.error stays None → error-path grading never triggers
                        ▼
 runner.py:294-320 (collect_usage) — the ONLY copy of chat.jsonl to the host
   fails; warning emitted ONLY if the LiteLLM ledger also had zero rows
   (:316-320) — with usage rows present the failure is completely silent
                        ▼
 eval/run_batch.py:861-864 — _build_trajectory: "no chat.jsonl; skipping
   trajectory" → early return → judge NEVER RUNS → no output.json, no real score
                        ▼
 result on current main: 706db77's last-resort stub (overall_score: null)
   → run wasted; generic error text; nothing prevents recurrence
```

## 2. Root cause RC-1 — the orphan sweep can kill live runs

**Confidence the defect exists: 99% (proven empirically). Confidence it caused the reported incidents: ~60%** (the broken run dirs were pruned from disk, so the trigger cannot be replayed; this is the only identified trigger consistent with "rare, silent, nothing else failed").

`script/run.sh:352` (inside `cleanup_orphans`, main @ 706db77):

```bash
containers=$(docker ps -aq --filter 'name=ll-' --filter 'name=mocks-' --filter 'name=t_' ...)
echo "$containers" | xargs -r docker rm -f
```

Three compounding defects:

1. **Docker name filters are substring regexes and `ps -aq` includes RUNNING
   containers.** Any live container whose name merely *contains* `t_`, `ll-`
   or `mocks-` is force-removed.
2. **The concurrency guard has a granularity gap.** `other_runs_active`
   (script/run.sh:323-337) detects only *other run.sh processes* via PID
   markers. `attempt_docker_recovery` (script/run.sh:537-545) calls
   `cleanup_orphans` after a docker-recoverable rep failure — but sibling
   parallel reps (`--parallel-reps`, dispatched at :685) and parallel-model
   workers share the parent's PID (`$$` is unchanged in bash subshells), so
   the guard passes and the sweep tears down a **live sibling's** stack.
3. **Direct `python3 eval/run_batch.py` invocations** (a documented RUNBOOK
   flow) register no marker at all — a run.sh preflight in another terminal
   sweeps their live containers.

### Proof (executed 2026-07-09 on this machine, docker 3 probe containers)

```
--- OLD unanchored filters match:
/ll-probe123
/t_probe_coerced
/Ruth_Armstrong_task_input_probe        ← the reported task's container name MATCHES
--- NEW anchored filters (^/?ll- ^/?mocks- ^/?t_) match:
/ll-probe123
/t_probe_coerced                        ← Ruth Armstrong container EXCLUDED
--- exited-only variant (probes not exited): matches nothing
```

The reported task "Ruth Armstrong task input" sanitizes to
`Ruth_Armstrong_task_input_claude-opus-4.7_<ts>_<hex>` (eval/run_batch.py's
container-name coercion), which contains the substring `t_` (in
`input_claude`) — i.e. **the very task from the incident report is in the
sweep's kill set**. So are `matt_garcia_*`, `chris_event_*`, and any task
name with a word ending in `t` before `_`.

## 3. Root cause RC-2 — the transcript copy is single-shot, silent, and load-bearing

**Confidence the defect exists: 99% (verified in code). Contribution to incidents: certain as the propagation step, plausible as an independent trigger** (a transient docker failure at copy time is sufficient by itself).

- `runner.py:294-298`: `collect_usage`'s one `docker cp` is the **only** source
  of the host `chat.jsonl` — the sole input to trajectory building and rubric
  judging. No retry. No fallback.
- `runner.py:316-320`: its failure is logged only when the LiteLLM usage
  ledger *also* recorded zero rows. An agent that made LLM calls and whose
  container then died gets a silent copy failure.
- A frozen snapshot mechanism already exists (`prepare_grading_transcript`,
  runner.py:105-122, writes `/tmp/chat-snap-<task_id>.jsonl`) but is invoked
  only for `automated_checks` tasks — **never** for the fork's native rubric
  tasks, so the snapshot that could save the run is never taken.
- Supporting evidence that the swallow-path is real: `runner.py:277-283`
  returns `error=None` regardless of the agent's exit code, so none of the
  above is ever escalated. (Fixing this escalation was proposed as a separate
  diagnostics-only change and **deliberately skipped** by team decision —
  see scope note below.)

## 4. Why we fix causes when the symptom is already patched

| | symptom patch only (`706db77`) | + these fixes |
|---|---|---|
| score.json always exists | ✅ | ✅ |
| run killed by sweep | ❌ still dies → null stub, tokens wasted | ✅ cannot happen (anchored filters, dead-only recovery sweep) |
| transient cp failure | ❌ run graded as null stub | ✅ retry + snapshot → run judged normally |
| operator sees why | ❌ generic "no grader wrote it" | ✅ copy failures logged unconditionally |

## 5. Scope decisions (agreed 2026-07-09)

- **DO — Fix A (was "Change 4"):** anchor the sweep filters; recovery sweep
  removes dead containers only. Removes the destructive trigger.
- **DO — Fix B (was "Change 3"):** transcript copy retry + unconditional
  logging + snapshot fallback; take the snapshot for native tasks too.
- **DONE (TL review follow-up F4, reversing the earlier skip):** agent exit
  code recorded on `AgentExecution` (diagnostic only — never sets `error`,
  never gates grading; timeout kills excluded) and woven into the last-resort
  stub's error text, e.g. `[agent exit code: 137]`.
- **DONE (TL review follow-up F2):** `__snapshot_recovered__: true`
  provenance marker on output.json + score.json when the graded transcript
  came from the /tmp snapshot; preserved by `script/regrade.py`; rolled up by
  `script/aggregate_runs.py` as `snapshot_recovered_runs` /
  `snapshot_recovered_rate`.
- **ALREADY DONE — "Change 1"** (safety-net stub): shipped in `706db77`.
- **DONE (TL review follow-up F1):** snapshot writes are atomic
  (tmp + rename) and a partial run-dir `chat.jsonl` left by a failed cp is
  removed rather than graded — `docker cp` writes in place, so the truncation
  concern was confirmed real. See CHANGES §"Review follow-up F1".
- **ANSWERED (TL review follow-up F3):** RC-2 is NOT openclaw-specific.
  Verified: `hermesagent/runner.py:148-156` has the identical single-shot
  silent `docker cp` of chat.jsonl; codex copies its session file the same
  single-shot way (`codex/runner.py:283-292`); claudecode converts its
  transcript differently. Extending Fix B to those backends is a known
  follow-up — deprioritized because the fork's production path is openclaw.

### Accepted residuals (explicitly out of scope — TL review follow-up F7)

1. **Setup-window gap** (`eval/run_batch.py` — `_start_drift_director` /
   `_start_mock_health_logger` are invoked after `_claim_run_dir` but BEFORE
   the protective `try/finally`): an exception raised there would leave a
   claimed run_N dir where even 706db77's last-resort stub never fires,
   because the finally block is never entered. Narrow in practice — both
   helpers catch their own exceptions internally and return None on failure —
   but the structural gap exists for any future code added to that window.
2. **BaseException through the finally block:** a `KeyboardInterrupt`/
   `SIGTERM` during the minutes-long judge council skips every stub writer
   including 706db77's (BaseException unwinds the finally block).
   Fingerprint: run dir with `output.json` + `chat.jsonl` but no
   `score.json`/`usage.json`.

## 6. Non-regression evidence (from the earlier full implementation, 2026-07-09)

The same two fixes (plus the since-skipped diagnostics) were fully implemented
and tested earlier today before being reverted for rebase onto `706db77`:

- Smoke gate `tests/test_drift_plane_smoke.py`: **6 passed** with fixes applied.
- Full suite: **identical 69 pre-existing failures with and without the fixes**
  (all in `test_connector_ssrf_guard.py` + 3 order-dependent overlay flakes);
  zero regressions attributable to the fixes; 9 new tests passed in that
  (since fully reverted) implementation. **F5 reconciliation:** the shipped
  implementation's test counts are the ones in CHANGES §Verification —
  12 new tests (`test_transcript_snapshot_fallback.py` ×7,
  `test_snapshot_provenance_and_exitcode.py` ×5), 24 targeted
  (12 new + 6 smoke + 6 last-resort); the "9" here is historical only.
- Docker probe (section 2) validated the anchored filters against real
  containers.
- Design invariant: every changed line either executes only after a failure
  already occurred, or only makes container deletion MORE conservative — there
  is no code path on which a healthy run behaves differently.
