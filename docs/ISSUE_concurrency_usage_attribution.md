# Concurrency Token Inflation — Usage Attribution Defect

Status: **confirmed, unfixed at HEAD**
Scope: every run executed concurrently with another container sharing the same LiteLLM sidecar
Impact: reported token counts and costs overstated by a factor equal to the number of co-running containers
Verified against: `greg_hargrove_54cef347`, `holly_clark_9c41f7a3` (8 runs each), and the local `output/` tree as a clean control

---

## 1. Symptom

Runs report token counts and dollar figures far above what the trajectory can account for.

Measured across two tasks, sixteen runs:

| | actual | reported | factor |
|---|---|---|---|
| tokens | 215,783,686 | 2,408,267,666 | **11.2x** |
| cost | $250.90 | $2,850.50 | **11.4x** |
| requests | 2,323 turns | 31,045 | **13.4x** |

Phantom overstatement: **$2,599.61** across the two tasks.

The figures are not mispriced. Every arithmetic step inside `usage.json` is internally consistent, and the harness's own cost model reproduces its reported dollar amounts to the cent from its reported token counts. The defect is not in the pricing. It is in **which run the tokens are attributed to**.

---

## 2. Root cause

Three facts in the source combine to produce it. All three are still true at HEAD.

### 2.1 The usage row carries no owner

`src/utils/litellm_usage_callback.py:34`

```python
_PATH = os.environ.get("LITELLM_USAGE_LOG_PATH", "/var/litellm_usage/usage.jsonl")
```

One path. The row written for every completion has exactly eleven keys:

```
ts, model, kind, input_tokens, output_tokens, total_tokens,
cache_read_tokens, cache_write_tokens, audio_seconds, cost_usd, duration_s
```

There is no `task_id`, no `run_index`, no container identifier. Nothing that says which run produced the row.

### 2.2 Attribution is by timestamp alone

`src/utils/grading.py:1842` — `extract_usage_from_litellm_log(log_path, window_start, window_end)`

```python
pad = 2.0
lo = window_start - pad
hi = window_end + pad
...
    if ts < lo or ts > hi:
        continue                     # <- the only attribution filter
    if row.get("kind") == "preflight":
        continue
    totals["request_count"] += 1
    ...
```

Two `continue` guards, then unconditional accumulation. Time is the entire attribution logic.

### 2.3 The log is shared per batch, not per run

`eval/bootstrap_sidecar.py` line 2 — *"Bootstrap a **shared** LiteLLM sidecar + docker network that survives across…"* — and line 328:

```python
usage_dir = config.work_dir / f"litellm-usage-shared-{suffix}"
```

`suffix` is pid-plus-batch-timestamp. One usage log per batch invocation. Every container in that batch appends to it.

### 2.4 The consequence

Under concurrency, each run's `usage.json` is **a measurement of the whole batch, sampled over that run's wall-clock window**. It is not a measurement of the run.

The inflation factor is therefore the number of containers sharing the window. Confirmed empirically — dividing each run's reported token rate by its own true rate recovers the container count:

| run | own tok/s | reported tok/s | implied containers | tokF |
|---|---|---|---|---|
| greg 1 | 6,602 | 34,200 | 5.2 | 5.2x |
| greg 7 | 8,854 | 83,377 | 9.4 | 9.4x |
| greg 3 | 6,918 | 102,759 | 14.9 | 14.9x |

Own throughput clusters tightly at 6.6k–10.0k tok/s across every run, which is what one Opus stream does. The reported rate is a multiple of it.

---

## 3. The defect is old; the exposure is new

**This is not a regression.** All four ingredients — untagged rows, timestamp-only attribution, a batch-scoped shared log, and reachable concurrency — landed together in the commit that introduced the shared-sidecar architecture. At that revision `eval/run_batch.py` already keyed the usage dir by `batch_id` rather than by run, and already dispatched through `ThreadPoolExecutor(max_workers=args.parallel)`. The defect was live and triggerable from the first day the shared sidecar existed.

It stayed invisible because nothing exercised it. Two later capability additions changed that:

- **`--parallel-reps`** — reps of the *same* task now overlap, so a task contaminates itself across its own run directories.
- **`--parallel-tasks`** — a second, multiplicative axis of concurrency across different tasks.

A subsequent change promoted the log to a bash-bootstrapped sidecar that *survives across invocations*, widening the window further.

**Multi-turn execution amplified it independently of concurrency.** A long multi-turn task holds a large cached prefix and re-reads it on every call, so a single run legitimately accumulates tens of millions of cache-read tokens. That makes each container's contribution to the shared log large, so pooling N containers produces a headline number in the hundreds of millions rather than the thousands. Multi-turn did not cause the mis-attribution — it made the mis-attributed quantity big enough to notice.

Neither capability introduced the bug. They made a latent defect reachable, then made its output impossible to ignore.

### 3.1 Sequential runs are correct

The local `output/` tree is the control. Eight runs, each with its own batch key, none overlapping:

```
tokens  actual 83,079,481   reported 84,069,621   1.0x
```

Every run reconciles at **1.0x**. A run that has the proxy to itself gets a clean number. This is why the defect survived so long: the common single-run path is unaffected.

---

## 4. Evidence

Four independent lines, each ruling out a different alternative explanation.

**Transcripts are complete, so nothing is hidden.** Reconstructing from the per-message `usage` blocks reproduces every per-run figure exactly, and the context chain is unbroken across compaction events.

**Identical windows produce identical bills regardless of work done.** greg run_7 and run_8 have fully overlapping windows and near-identical durations. Their reported totals differ by **0.013%**. Their actual work differs by **8.5%**.

**Reported tokens are a perfect rank-ordered function of window duration; actual work is not.** Sorting the seven concurrent greg runs by `elapsed_time` gives a monotonically increasing reported total (Spearman rho = 1.0). The same ordering applied to actual work is not monotonic.

**A single run reports 99% of an entire fourteen-container batch.** Joining greg and holly on batch key, the 16:49–16:51 window holds exactly fourteen containers whose combined actual consumption is **197,116,220 tokens**. Against that:

| run | reports | share of entire batch |
|---|---|---|
| holly run_7 | 195,788,094 | **99.3%** |
| greg run_7 | 193,690,904 | **98.3%** |
| greg run_8 | 193,382,954 | 98.1% |
| holly run_3 | 183,575,296 | 93.1% |
| greg run_4 | 183,544,015 | 93.1% |

Six runs each independently bill for 91–99% of what all fourteen consumed. That is a containment violation, not a statistical argument: one process cannot account for 99% of work done by fourteen.

### 4.1 What the defect is *not*

- **Not the sub-agent token fold.** No `subagent_*` keys appear in any of the sixteen `usage.json` files; `sources` contains only `agent`, `judge`, `preflight`. Zero contribution from that path.
- **Not a judge-side problem.** The judge line is computed in-process from the grading response, never scraped from the shared log. It is correct, and its correctness is itself diagnostic — it is the only figure that does not scale with window length.
- **Not fixable by redistribution.** Batches pool separately, and each batch over-counts its own total by its container count. The sixteen runs report 2.41B against 216M actual. No reallocation of the reported values lands on the truth. The figures must be rebuilt from transcripts.

---

## 5. Solution: transcript-derived reconstruction

Until the writer stamps identity onto the row and the reader filters on it, reported usage cannot be trusted for any concurrent run. The remedy is to stop reading `usage.json`'s agent line and rebuild each run's usage from its own transcripts, which are correctly scoped by construction.

Two scripts implement this.

### 5.1 `extract_actual_usage.py` — single task

```bash
python3 extract_actual_usage.py <task_dir> [--write]
```

Rebuilds each run's usage and prints actual-vs-reported. `--write` emits a `usage_actual.json` beside each run's `usage.json`. It never modifies `usage.json`.

### 5.2 `audit_usage_fleet.py` — campaign-wide

```bash
python3 audit_usage_fleet.py output --csv audit.csv --json audit.json
python3 audit_usage_fleet.py <single_task_dir>          # auto-detected
```

Walks `<root>/<backend>/<task>/trajectories/<model>/run_*`, so it covers every backend and model without configuration.

Exit codes make it CI-usable:

| code | meaning |
|---|---|
| 0 | every parsed run within `--threshold` (default 1.25x) |
| 1 | at least one run over threshold |
| 2 | at least one run could not be parsed |

### 5.3 How the reconstruction works

**Step 1 — read the API's own accounting.** Every assistant message in `chat.jsonl` carries a `usage` block returned by the API for that completion:

```json
"usage": { "input": 2, "output": 157, "cacheRead": 40384, "cacheWrite": 965 }
```

The four fields are summed across all messages. Note the shape is **camelCase**, not Anthropic's snake_case; the normaliser accepts both, because a reader that only looks for `input_tokens` finds nothing and silently returns zero.

**Step 2 — add sub-agent sessions, deduplicated by content hash.** Each distinct file under `task_output/sessions/` is added, including the `*.jsonl.deleted.*` files, which are real sub-agent sessions. Critically, `sessions/` also contains a **byte-identical copy of `chat.jsonl`**; a naive glob double-counts the entire main thread. Each file is SHA-256'd and skipped if it matches the main transcript.

**Step 3 — price at the harness's own rates.** `$5 / $25 / $0.50 / $6.25` per MTok for input / output / cache-read / cache-write. Validated to reproduce the harness's own arithmetic before being relied on.

**Step 4 — reuse the judge line verbatim.** `sources.judge.cost_usd` is trustworthy and is added to the transcript-derived agent cost.

**Step 5 — group by batch to expose the mechanism.** The `_YYYYMMDD_HHMM_` stamp is parsed from `finance_usage.json`'s `trajectory_id`, falling back to `harness_debug.log`. Any batch with more than one member shared a `usage.jsonl` and is suspect by construction.

**Step 6 — fail loudly rather than quietly.** Runs with no `usage.json` are reported as `SKIPPED (skeleton)`. Runs whose transcript yields no usage blocks are reported as `UNPARSED` and force exit code 2. An unparsed run means the number is **missing, not zero**.

### 5.4 Interpreting the output

- **`tokF`** — reported agent tokens divided by reconstructed tokens. Clean baseline **1.0x**. This is the primary signal.
- **`reqF`** — reported `request_count` divided by transcript turns. Baseline is **1.1–1.5x**, not 1.0x: `request_count` counts tool-loop rounds and continuations while turns counts messages carrying a usage block. The signal is the excess above that floor.
- **`$F` below 1.0** — an OAuth marker, not a finding. The OAuth path writes `cost_usd: 0.00` because no price entry exists for the subscription route, so OAuth runs *under*-report cost. Read `tokF` for attribution; ignore `$F` on OAuth runs.

### 5.5 Known limits

- **Backend coverage.** The reader understands openclaw's transcript shape. `claudecode` and `codex` write different formats and are unvalidated — no `claude_code_log/` or `codex_sessions/` directories were available to test against. Those backends will surface as `UNPARSED`. Adding one means adding a reader, not adjusting a glob.
- **Batch grouping is indicative, not authoritative.** Minute-granularity keys split runs straddling a minute boundary, so a genuinely concurrent run can appear as a lone "sequential" batch. Its factor still exposes it. Keying on the full trajectory-id suffix, or a time-window join, would make the grouping reliable.
- **Cross-task pooling is invisible unless you supply both tasks.** The grouping only sees task directories handed to it. The early-window pair illustrates the gap: two known containers with 18,667,466 combined actual, yet one run reports 298% of that — proving unseen co-runners. The factor sees everything; the grouping sees only what you give it.
- **This is a reconstruction, not an invoice.** Token counts are the API's own per-completion accounting, so they are solid; residual uncertainty is in cents from rate rounding. For a true invoice, reconcile against CloudWatch `ModelInvocationLog`.

---

## 6. Recommended remediation

1. **Stamp identity on the row.** Add task and run identifiers to `_write_row`. The identity must ride the **request** — per-call `metadata` surfaced through `kwargs` — not a sidecar environment variable. `_write_row` executes inside the shared sidecar, where any `os.environ` value is batch-scoped and identical for every run, so it cannot discriminate.
2. **Filter on that identity** in `extract_usage_from_litellm_log`, keeping the timestamp window only as a secondary guard.
3. **Add an end-of-run invariant.** Compare `request_count` against transcript turn count and fail loudly past tolerance. A 13x divergence would have been caught on the first affected run.
4. **Re-state anything already posted.** Records accepted at `trajectory_usage/create` are overstated by each run's concurrency factor and need reissuing from transcript-derived figures.
5. **Interim mitigation.** `--parallel-tasks 1` with `--parallel-reps` off produces correct usage. Sequential runs are unaffected.

A single change — request-scoped identity plus filtering on it — closes this and also lets the sub-agent accounting become a correct partition of the log rather than an addition on top of it.
