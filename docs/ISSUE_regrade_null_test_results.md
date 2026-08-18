# Issue: Regrade Writes Null Test Results to score.json

**Commit**: `28c95e734295f21e414eb8ce12548fc998bd7003` ("Regrade file fix")
**Affected file**: `script/regrade.py`
**Severity**: High — silently corrupts Channel A test data in `score.json`
**Fixed by**: `7091e6c` ("FIX: remove root causes behind lost/null score.json runs")

---

## Summary

Commit `28c95e7` adds Channel A (pytest) data merging to `script/regrade.py` by importing `_ctrf_test_result` and `rebuild_model_dir` from `script.backfill_pass_summary`. The implementation has two bugs that cause all test results to appear as `null`/zero in `score.json` after regrading.

---

## Root Cause

### Bug 1: Missing `tests_total > 0` Guard

The canonical implementation in `run_batch.py:_augment_score_with_combined_rewards` (line 1142) guards test reward assignment:

```python
if ... and te.get("tests_total"):  # Only set test_reward when tests actually ran
    test_reward = float(raw_test)
```

The regrade commit reads reward **unconditionally**:

```python
test_result = _ctrf_test_result(run_dir)
test_reward = test_result["reward"]  # No tests_total guard
```

When `ctrf.json` is absent (run executed without `--execute-tests`), `_ctrf_test_result` returns:

```python
{"tests_total": 0, "tests_passed": 0, "tests_failed": 0, "tests_errored": 0, "tests_skipped": 0, "reward": None}
```

This writes `test_based_reward: null` and zeroes for all test counts into `score.json`.

### Bug 2: Unconditional Overwrite of Existing Channel A Data

The regrade flow:

1. `grade_with_rubric()` returns rubric-only scores (Channel B)
2. Line 206 writes these to `score.json` — **overwrites** the original file
3. Lines 199-204 preserve only `injection_ok` / `injection_defects` from the original
4. Lines 208-222 attempt to re-read Channel A data from `ctrf.json`

The original `score.json` may have contained valid Channel A per-test data (`test_scores`, `test_function_outputs`) from a prior execution. The first write at line 206 **destroys** this data. The subsequent read from `ctrf.json` only recovers aggregate counts, not per-test detail.

---

## Manifestation

### Scenario A: Run without `--execute-tests` (no ctrf.json)

```json
{
  "tests_total": 0,
  "tests_passed": 0,
  "tests_failed": 0,
  "test_based_reward": null,
  "rubric_based_reward": "<value or null>",
  "combined_reward": null
}
```

### Scenario B: Run with tests but rubric grading fails

```json
{
  "tests_total": 12,
  "tests_passed": 6,
  "tests_failed": 6,
  "test_based_reward": 0.0,
  "rubric_based_reward": null,
  "combined_reward": 0.0
}
```

### Propagation

`rebuild_model_dir(run_dir.parent)` is called after the corrupted write, propagating null/zero values into `pass_summary.json` for the entire model directory.

---

## Affected Components

| Component | Impact |
|-----------|--------|
| `score.json` | Test fields overwritten with null/zero |
| `pass_summary.json` | Rebuilt from corrupted score.json |
| `output_bundle/` deliveries | Bundles repackaged from corrupted summaries |

---

## Fix Direction

1. Replicate the `tests_total > 0` guard from `run_batch.py:1142`
2. Preserve existing Channel A fields from original `score.json` before overwriting
3. Only merge Channel A data when `ctrf.json` exists AND has non-zero `tests_total`

```python
# Correct pattern:
test_result = _ctrf_test_result(run_dir)
if test_result["tests_total"] > 0:
    test_reward = test_result["reward"]
    scores["tests_total"] = test_result["tests_total"]
    scores["tests_passed"] = test_result["tests_passed"]
    scores["tests_failed"] = test_result["tests_failed"]
    scores["test_based_reward"] = test_reward
else:
    test_reward = None
    # Preserve any existing Channel A data from original score.json
```

---

## Verification

Confirmed via inspection:
- All existing `ctrf.json` files have valid `overall_score` (not null)
- `_ctrf_test_result` path (`run_dir/task_output/logs/verifier/ctrf.json`) matches `run_batch.py` write path
- No module-level side effects from the import
- `run_batch.py` never imports `regrade.py` — normal execution path unaffected
- Bug is isolated to the regrade path only

---

## Related

- AGENTS.md critical convention #8: `tests_*` in `score.json` are DEPRECATED Channel-B criteria aliases — do not conflate with Channel A pytest counts
- AGENTS.md critical convention #17: `final_reward` in bundles is a PERCENTAGE 0-100; internal `score.json.combined_reward` remains 0-1
