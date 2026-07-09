# Code changes — score.json root-cause fixes + review follow-ups

**Status:** complete, verified, committed on `main` directly on top of
`706db77`. Companion doc: `RCA_missing_score_json.md` (root causes, proof,
confidence). The commit's own diff (`git show <commit>` / `git format-patch`)
is the canonical audit artifact.

## Review-status summary (TL review of 2026-07-09 — all items closed)

| Review item | Status |
|---|---|
| Fix A | ACCEPTED — implemented |
| Fix A gate (a): patch for numeric-claims audit | the commit diff itself (`git show` / `git format-patch` of this change set) |
| Fix A gate (b): `test_shared_sidecar_invariants.py` green | **17 passed** (branch copy run against the modified `script/run.sh`) |
| Fix B | ACCEPTED — implemented |
| F1 snapshot atomicity (author-question) | ANSWERED + FIXED — `docker cp` writes in place (not atomic); truncation concern confirmed real; tmp+rename + partial-file guard shipped |
| F2 `__snapshot_recovered__` provenance | DONE — output.json + score.json + usage.json, preserved by regrade, rate in aggregators |
| F3 RC-2 scope | ANSWERED — not openclaw-specific (hermes `runner.py:148-156`, codex `runner.py:283-292` share the pattern); extension deferred, openclaw is the production path |
| F4 exit-code diagnostics | DONE — reinstated per review (reverses earlier skip decision) |
| F5 docs test-count reconciliation | DONE — "9" labeled historical; shipped counts below |
| F6 auditable artifacts | DONE — the commit diff is the canonical patch; the FAILED-set comparison is reproducible with the exact commands in §Verification |
| F7 residuals documented | DONE — RCA "Accepted residuals" (setup-window gap, BaseException-through-finally) |

## Change inventory

```
 eval/run_batch.py             |  25 +++++++-   (F2 stamps, F4 plumb + stub weave)
 script/aggregate_runs.py      |  15 ++++++   (F2 fleet-wide recovery rate)
 script/regrade.py             |   7 ++++    (F2 marker preservation)
 script/run.sh                 |  43 +++++--   (Fix A)
 src/agents/base.py            |   5 ++++    (F4 field)
 src/agents/openclaw/runner.py | 100 ++++++--   (Fix B + F1 + F2 + F4)
 tests/test_transcript_snapshot_fallback.py     (new, 7 tests)
 tests/test_snapshot_provenance_and_exitcode.py (new, 5 tests)
```

---

## Change set A — orphan sweep can no longer kill live runs (RC-1)

**File:** `script/run.sh` (`cleanup_orphans`, `attempt_docker_recovery`).

**A1. Container name filters anchored:**

```diff
-containers=$(docker ps -aq --filter 'name=ll-' --filter 'name=mocks-' --filter 'name=t_' ...)
+local name_filters=(
+    --filter 'name=^/?ll-'
+    --filter 'name=^/?mocks-'
+    --filter 'name=^/?t_'
+)
+# no empty-array expansion — bash 3.2 (macOS default) errors on "${empty[@]}"
+# under `set -u`; caught in review by executing the function on /bin/bash 3.2
+if (( exited_only == 1 )); then
+    containers=$(docker ps -aq "${name_filters[@]}" --filter 'status=exited' --filter 'status=dead' ...)
+else
+    containers=$(docker ps -aq "${name_filters[@]}" ...)
+fi
```

*Why:* docker name filters are substring regexes and `ps -aq` includes RUNNING
containers — the old form matched (and `docker rm -f`'d) live agent containers
whose task-derived names merely contain `t_`, e.g.
`Ruth_Armstrong_task_input_claude-…` via `input_claude`. Proven with probe
containers (RCA §2). The anchored form still matches every real convention
(`ll-<batch>`, `mocks-<batch>`/`mocks-task-…`, coerced `t_…` ids).

**A2. `--exited-only` mode, used by the recovery path:**

```diff
-    cleanup_orphans
+    cleanup_orphans --exited-only     # in attempt_docker_recovery
```

Reaps only `status=exited|dead` containers and never touches networks. *Why:*
the recovery sweep fires mid-batch after a docker-recoverable rep failure;
parallel reps (`--parallel-reps`) and per-model workers are backgrounded
subshells of the SAME run.sh (same `$$` → same PID marker), so the
`other_runs_active` registry cannot protect them from the full sweep.
Dead-only reaping is safe by construction. The batch-start preflight sweep is
unchanged (full sweep, registry-guarded).

**A3. Network filter anchored:** `name=k3net-` → `name=^k3net-`.

**Accepted trade-off:** crash leftovers whose names don't start with
`t_`/`ll-`/`mocks-` are no longer auto-purged at preflight (manual `docker rm`).

## Change set B — transcript collection resilient + loud (RC-2)

**File:** `src/agents/openclaw/runner.py`.

**B1. Freeze a /tmp snapshot right after the agent finishes** (`run_task`):
best-effort `prepare_grading_transcript` call inside `try/except Exception` —
the transcript survives even if the container dies/is removed before teardown.
Mechanism already existed for `automated_checks` grading; now taken for native
rubric tasks too. Cost on a healthy run: one read-only `docker cp` (~ms).

**B2. `collect_usage` copy is retried, loud, and snapshot-backed:**

```
docker cp container→run_dir/chat.jsonl
  └─ failed? → WARN (unconditional — old warning was hidden whenever LiteLLM
               usage rows existed) → sleep 1s → retry once
      └─ still failed / file absent?
         → snapshot exists & non-empty: restore it (+WARN, +F2 marker)
         → else: WARN; if a PARTIAL chat.jsonl was left by the failed cp,
           remove it (see F1 below)
```

Downstream usage-source check relaxed from `returncode == 0 and exists()` to
`exists()` so a snapshot-restored transcript counts.

*Why:* this single `docker cp` was the ONLY source of the host `chat.jsonl` —
the sole input to trajectory building and rubric judging
(`run_batch._build_trajectory` returns early without it, leaving only
706db77's null stub). One silent failure cost the run its entire score.

## F1 — snapshot atomicity (TL author-question: answered + fixed)

**Answer: `docker cp` writes the destination IN PLACE — not atomic.** The
truncation concern was real. Two guards:

- `prepare_grading_transcript`: copy to `chat-snap-<id>.jsonl.tmp`, then
  `Path.replace()` (atomic rename, same filesystem) only after the copy fully
  succeeded; `.tmp` unlinked on any failure. The final snap name can never
  hold partial bytes.
- `collect_usage`: when the direct cp failed AND no snapshot exists, a partial
  `chat.jsonl` left in the run dir is removed (with WARN). A truncated
  transcript would be silently graded as if complete — under-scoring the run;
  a missing one yields the honest diagnostic stub instead.

## F2 — `__snapshot_recovered__: true` provenance marker

Set by `collect_usage` only when the graded transcript came from the snapshot.
Flow: usage dict → `_build_trajectory` stamps **output.json** (regrade's
source of truth) and **score.json** (judged and stub paths) → `usage.json`
carries it via save_usage's existing extra-key hoist (same mechanism as
`elapsed_time`/`usage_source`) → `script/regrade.py` re-stamps it from
output.json on every regrade (a regrade can never launder a recovered run) →
`script/aggregate_runs.py` reports per-task `snapshot_recovered_runs` and
per-model `snapshot_recovered_runs` + `snapshot_recovered_rate`. A rising rate
= containers dying before transcript copy, visible fleet-wide without logs.

## F4 — agent exit-code diagnostics (reinstated per TL review)

- `src/agents/base.py`: `AgentExecution.agent_exit_code: int | None = None`.
- `openclaw/runner.py`: records the exit code after the agent finishes;
  **timeout kills excluded** (our own `kill()`'s -9 is not a crash signal —
  partial-credit design untouched); WARN on nonzero. **Never sets `error`,
  never gates grading.**
- `eval/run_batch.py`: plumbs `result["__agent_exit_code__"]`; the last-resort
  stub's error text becomes e.g.
  `"score.json missing after finally; no grader wrote it [agent exit code: 137]"`
  — "agent OOM-killed" vs "scoring lost its inputs" readable from the file.

## New tests (12 total, no docker / no LLM needed)

`tests/test_transcript_snapshot_fallback.py` (7): snapshot restore works and
sets the F2 marker; retry-success does NOT set it; exactly one retry; nothing
partial ever lands at the final snap name (interrupted cp); successful snap is
complete; partial run-dir transcript removed; AST guard for the best-effort
snapshot call.

`tests/test_snapshot_provenance_and_exitcode.py` (5): exit-code field defaults
+ never-implies-error; last-resort stub weaves `__agent_exit_code__` (AST);
runner records without error; marker stamped in run_batch + preserved in
regrade (source checks); **functional aggregator test** — synthetic output
tree, 1 of 2 runs flagged → `snapshot_recovered_runs == 1`, `rate == 0.5`.

## QC disclosures — exact behavioral deltas on a NORMAL run

Full adversarial pass performed (every changed line, happy path traced
end-to-end):

1. **Output files byte-identical** on a healthy run: no F2 marker (flag only
   set on snapshot restore), no F4 text (stub not written when score exists),
   no stamps in output.json/score.json/usage.json. Verified
   `_project_agent_usage_top_level` projects a fixed whitelist — the marker
   cannot leak into output.json's usage block.
2. **One new operation per run:** the B1 snapshot `docker cp` to /tmp
   (read-only, ~ms, try/except-guarded, cannot fail the run).
3. **`aggregate_runs.py` summary gains always-present fields**
   (`snapshot_recovered_runs`, `snapshot_recovered_rate` — 0/0.0 on healthy
   fleets). Additive; `_print_table` and all known readers consume named keys.
4. **Deletion scope only shrinks** (Fix A): anchored matches ⊂ old substring
   matches; recovery sweep dead-only ⊂ full. Nothing new is ever deleted.
5. All `AgentExecution` constructions across all four backends are
   keyword-only — the new field (default None) breaks none.

## Verification results (final, 2026-07-09)

| Gate | Result |
|---|---|
| `bash -n script/run.sh` / `py_compile` all touched files | OK |
| Real `cleanup_orphans` executed on `/bin/bash` 3.2 against live+dead probe containers and probe networks | exited-only reaped ONLY the dead container; full sweep spared `Ruth_Armstrong_task_input_probe` and `not-k3net-*`; **caught + fixed a bash-3.2 `set -u` empty-array crash in our own first version** |
| Docker probe: anchored vs unanchored filters | task-named container EXCLUDED; `t_`/`ll-`/`mocks-task-` probes matched |
| Targeted tests (12 new + 6 smoke + 706db77's 6 last-resort) | **24 passed** |
| `test_shared_sidecar_invariants.py` (branch copy vs modified run.sh — Fix A merge gate) | **17 passed** |
| Full suite vs `main @ 706db77` baseline | **identical 69 pre-existing failures** (FAILED-set diff empty) — zero regressions, +12 passed |

(F5 note: RCA §6's "9 new tests" refers to the earlier, fully-reverted
pre-review implementation; shipped counts are the 12 above.)

## Reproducing the verification (F6)

The zero-regression claim is independently reproducible:

```bash
# FAILED-set baseline on the parent commit, then on this commit — diff is empty
git stash && pytest tests/ -q 2>&1 | grep '^FAILED' | sort > /tmp/base.txt && git stash pop
pytest tests/ -q 2>&1 | grep '^FAILED' | sort > /tmp/after.txt
diff /tmp/base.txt /tmp/after.txt   # expect: no output (69 identical lines each)

pytest tests/test_drift_plane_smoke.py -q            # ship gate: 6 passed
pytest tests/test_transcript_snapshot_fallback.py \
       tests/test_snapshot_provenance_and_exitcode.py \
       tests/test_score_json_last_resort.py -q       # 18 passed
```

The canonical patch for numeric-claims audit is the commit itself
(`git show <commit>` / `git format-patch -1 <commit>`).

## Deliberately NOT changed

`706db77`'s last-resort stub (only its error text is enriched), judge council,
reward formulas, timeout partial-credit semantics, preflight full-sweep
behavior, schemas of all existing fields, delivery/bundle flows, other
backends (F3 extension deferred).
