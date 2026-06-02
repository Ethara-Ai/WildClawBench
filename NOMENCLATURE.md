# Output Nomenclature

Two independent scoring channels run per task per run. Historically both used `tests_*` keys; that caused confusion. As of b71 the rubric channel uses canonical `criteria_*` keys with `tests_*` kept only as deprecated aliases for back-compat.

## At-a-glance map

| What you want | Where to find it | Field name |
| --- | --- | --- |
| Rubric judge verdict for one run | `output/<backend>/<task>/trajectories/<model>/run_N/score.json` | `overall_score`, `rubric_weights_percentage` |
| Rubric pass counts for one run | same file | `criteria_total`, `criteria_passed`, `criteria_failed` |
| Per-criterion judge breakdown | same file | `criteria[]` |
| Pytest reward for one run | `output/<backend>/<task>/trajectories/<model>/run_N/task_output/logs/verifier/reward.txt` | scalar in `[0,1]` |
| Pytest detailed report | same dir | `ctrf.json`, `test_function_outputs.json`, `test_output.log` |
| Generated test code | `output/<backend>/<task>/data/tests/test_outputs.py` (shared across runs) | — |
| Generated test weights | `output/<backend>/<task>/data/tests/test_weights.json` | — |
| Per-run summary across many runs | `output/<backend>/<task>/pass_summary.json` | `runs[].rubric_weights_percentage` |
| Aggregate average across runs of a model | same file | `average_rubric_weights_percentage` |
| Cross-task model rollup | run `python3 scripts/aggregate_runs.py` | `output/<backend|all>_aggregate_summary.json` |

## Channel A — Pytest test executor

**Owner:** `src/utils/test_executor.py:_compute_reward`
**Triggered when:** `--execute-tests` is on and generated/inline tests run against the agent workspace.
**Reward formula (user m1420 line 1):**

```
reward = max(0, (Σ passed_positive_weights − Σ |triggered_negative_weights|) / Σ all_positive_weights)
```

A "triggered" negative-weight test is a guardrail that FIRED (failure mode actually occurred). Its absolute weight is subtracted from the numerator.

**Output files:**
- `task_output/logs/verifier/reward.txt` — scalar reward in `[0,1]`
- `task_output/logs/verifier/ctrf.json` — Common Test Report Format
- `task_output/logs/verifier/test_function_outputs.json` — per-test return values
- `task_output/logs/verifier/test_output.log` — pytest stdout

**Authoritative test count keys** (used by harbor bundle's `test_result`):
- `tests_total`, `tests_passed`, `tests_failed` — these ARE the real pytest counts here.

## Channel B — Rubric judge

**Owner:** `src/utils/grading.py:_grade_council` (and single-judge fallback)
**Triggered when:** rubric.json exists for the task. Runs once per task per run.
**Aggregator:** judge council member-mean per criterion (b43), binary-quantized at threshold 0.5 (b51).
**Score formula:** same as Channel A — applies the user m1420 line 1 formula over rubric weights instead of pytest weights. Per-criterion judge verdicts collapse to weighted pass/triggered tallies.

**Output file:** `output/<backend>/<task>/trajectories/<model>/run_N/score.json`

**Canonical keys (b71):**
- `overall_score` — float in `[0,1]`
- `rubric_weights_percentage` — `overall_score * 100`, 2 dp (user m1420 line 2)
- `criteria_total`, `criteria_passed`, `criteria_failed` — counts of rubric criteria
- `criteria[]` — per-criterion breakdown (id, weight, score, judges, scores_by_judge, stddev, etc.)
- `judge_model` — `'council'` if 2+ council members survived, else ARN string
- `judge_council` — present only when council ran (member list, surviving, failed, per_member_user_chars)
- `disagreement_flags` — criterion ids with stddev > threshold (b18)

**Deprecated aliases (b71, kept for back-compat with old tooling):**
- `tests_total` = `criteria_total`
- `tests_passed` = `criteria_passed`
- `tests_failed` = `criteria_failed`

Do not depend on the deprecated aliases for new code. They will be removed in a future release.

## Channel boundary

The deprecated `tests_*` rubric aliases LOOK like Channel A counts but are NOT. The harbor bundle's `tr_meta` adapter at `eval/run_batch.py:706` is the bridge — when no real pytest ran, it reads the rubric-channel `tests_*` keys (which are aliases of `criteria_*`) to populate the harbor `test_result` block. Channel A keys (when pytest ran) take precedence.

## Per-run summary file

`output/<backend>/<task>/pass_summary.json` written by `eval/run_batch.py:_write_pass_summary`:

```json
{
  "runs": [
    {
      "run_index": 1,
      "reward": 0.983,
      "rubric_weights_percentage": 98.3,
      "criteria_total": 23, "criteria_passed": 23, "criteria_failed": 0,
      "tests_total": 23, "tests_passed": 23, "tests_failed": 0,   // deprecated aliases
      "elapsed_time": 412.5,
      ...
    },
    ...
  ],
  "average_reward": 0.961,
  "average_rubric_weights_percentage": 96.10,   // user m1420 line 3 (per-task, per-model mean)
  "run_count": 3
}
```

## Cross-task / cross-model aggregator

`scripts/aggregate_runs.py` walks `output/<backend>/*/trajectories/<model>/run_*/score.json` and emits:

- `output/<backend>_aggregate_summary.json` (with `--backend openclaw`)
- `output/all_aggregate_summary.json` (default)

Both have `by_task_model` (per (backend, task, model) tuple) and `by_model` (per (backend, model) — the broadest rollup, which is the closest match to user m1420 line 3 across multiple tasks).

## Formula reference (user m1420)

```
final_reward = max(0, (Σ passed_positive_weights − Σ |triggered_negative_weights|) / Σ all_positive_weights)
rubric_weights_percentage = final_reward × 100
average_rubric_weights_percentage = mean(rubric_weights_percentage across all runs for a model)
```

| Line | Where enforced |
| --- | --- |
| 1 (pytest reward) | `src/utils/test_executor.py:_compute_reward` |
| 1 (rubric overall_score) | `src/utils/grading.py:_grade_council` and single-judge — algebraically equivalent because binary scores × signed weights / positive-only denominator collapses to the user formula |
| 2 (percentage) | `src/utils/grading.py` return dict (`rubric_weights_percentage`), `eval/run_batch.py:_write_pass_summary` (per-run), `scripts/aggregate_runs.py` (cross-run) |
| 3 (mean over runs of a model) | `eval/run_batch.py:_write_pass_summary` (per-task `average_rubric_weights_percentage`), `scripts/aggregate_runs.py` (cross-task `by_model.average_rubric_weights_percentage`) |
