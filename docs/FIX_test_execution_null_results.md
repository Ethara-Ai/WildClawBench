# Fix: Test Execution Returns Null Instead of Pass/Fail

**Related issue**: `docs/ISSUE_regrade_null_test_results.md`
**Example task**: `kayla_morgan_4d9b6a1f-3e57-4c82-9a06-5f1e73b8a4c2`
**Severity**: High — tests are generated and cached but never executed, yielding `test_based_reward: null`

---

## Diagnosis

### Evidence from harness_debug.log

```
13:57:01 | grade.begin ... has_rubric=True has_test_code=True agent_error=None
13:57:01 | run_batch | [...] No Automated Checks, skipping grading
13:57:01 | grade.end ... scores={} error=None
```

The harness **acknowledges `has_test_code=True`** at grade-begin, then immediately emits "No Automated Checks, skipping grading" and produces empty scores. The rubric in `score.json` was written by a **later regrade pass** — but test execution was never triggered on first run OR regrade.

### Root Cause Chain

There are **two independent bugs**, both causing the same symptom:

#### Bug A (PRIMARY): `exec_tests` resolves False under OAuth because `gen_tests` was False

At `eval/run_batch.py:3887-3889`:
```python
exec_tests = getattr(args, "execute_tests", None)
if exec_tests is None:
    exec_tests = bool(gen_tests and enable_mock_stack)
```

And `gen_tests` at line 3881-3884 (BEFORE our provider isolation fix):
```python
gen_tests = args.generate_tests
if gen_tests is None:
    gen_tests = bool(config.bedrock_inference_arn and config.aws_bearer_token)
    # Missing: or use_oauth
```

Under the **OAuth path** (which the kayla_morgan EC2 run used — `claude-opus-5` model = OAuth), `gen_tests` resolved to `False` because only Bedrock credentials were checked. This caused `exec_tests = bool(False and enable_mock_stack) = False`.

The tests were loaded via `_load_provided_tests` (task ships its own suite at `tests/test_outputs.py` + `test_weights.json`), so `task["test_code"]` was populated. But the execution gate at line 2469:
```python
if execute_tests and task.get("test_code") and not startup_failed:
```
...fails because `execute_tests` is `False`.

**Our provider isolation fix (commit c49ac85, Change 2) partially addresses this** by adding `or use_oauth` to the `gen_tests` auto-enable. However, there remains a **second gap**: when tasks ship their own tests (`_load_provided_tests`), `gen_tests` being False is correct (no generation needed), but `exec_tests` should STILL be True because tests exist and need running.

#### Bug B: `exec_tests` depends on `gen_tests` even for pre-shipped test suites

The formula `exec_tests = bool(gen_tests and enable_mock_stack)` means:
- If tests are LLM-generated → `gen_tests=True` → `exec_tests=True` (correct)
- If tests are pre-shipped AND gen_tests is False → `exec_tests=False` (BUG)

Tasks with pre-shipped tests skip LLM generation (line 1784-1792) but still need execution.

#### Bug C: Regrade never triggers test execution

`script/regrade.py` imports `_ctrf_test_result` to READ test results, but it never RUNS tests. If tests weren't executed during the initial `run_batch.py` dispatch, there's no ctrf.json to read, so it writes nulls.

### "No Automated Checks" message (RED HERRING)

The message at line 235 (`"No Automated Checks, skipping grading"`) fires for ALL native `input/` tasks because `task["automated_checks"]` is always `""` for this format. This is **benign** — it's an old grading path for legacy `tasks/*.md` format. The actual rubric grading happens later at line 1348 via `grade_with_rubric(task["rubrics"])`. The message is confusing but not the bug.

The grading flow for native tasks:
1. Line 234: "No Automated Checks" (old path, benign skip)
2. Line 1348: `grade_with_rubric(rubrics)` (new path, actually grades — this worked fine)
3. Line 2469: test execution gate — **THIS IS WHERE IT FAILS** (`execute_tests=False`)

---

## User's Key Pointers

1. The current task in output is NOT an example of this issue
2. The kayla_morgan task IS the example
3. **The issue is not only with regrading — it happens on first run too**

---

## Fix Approach

### Part 1: Decouple `exec_tests` from `gen_tests` when task ships its own test suite

**File**: `eval/run_batch.py:3887-3889`

The current formula:
```python
exec_tests = getattr(args, "execute_tests", None)
if exec_tests is None:
    exec_tests = bool(gen_tests and enable_mock_stack)
```

This fails for pre-shipped test suites under OAuth where `gen_tests=False` (correctly — no generation needed). But `exec_tests` should still be True because tests exist.

**Problem**: At dispatch time (line 3887), we don't yet know whether the task has pre-shipped tests — that's determined per-task inside `run_single_task`. We need a per-task override.

**Fix (two-level)**:

Level 1 — At dispatch (line 3887), expand the auto-enable:
```python
exec_tests = getattr(args, "execute_tests", None)
if exec_tests is None:
    exec_tests = bool(enable_mock_stack and (gen_tests or use_oauth))
```

This ensures OAuth tasks auto-enable execution (since our provider isolation fix already ensures `gen_tests=True` under OAuth, this is belt-and-suspenders).

Level 2 — At test execution gate (line 2469), add a per-task override:
```python
# Auto-enable test execution if task shipped its own test suite and mock
# network is available, regardless of the dispatch-level exec_tests flag.
effective_exec = execute_tests or (
    bool(task.get("test_code")) and bool(network)
)
if effective_exec and task.get("test_code") and not startup_failed:
```

This is the **definitive fix**: if a task has `test_code` (from any source — LLM-generated, cache, or pre-shipped) AND the mock network is reachable, execute the tests. This decouples test execution from the generation decision entirely.

### Part 2: Regrade guard (prevent null overwrites)

**File**: `script/regrade.py:~208`

When `ctrf.json` is absent (tests never ran), regrade MUST NOT overwrite existing Channel A fields with nulls/zeros.

```python
test_result = _ctrf_test_result(run_dir)
if test_result["tests_total"] > 0:
    # Tests actually ran — merge their results
    test_reward = test_result["reward"]
    scores["tests_total"] = test_result["tests_total"]
    scores["tests_passed"] = test_result["tests_passed"]
    scores["tests_failed"] = test_result["tests_failed"]
    scores["test_based_reward"] = test_reward
else:
    # No test execution happened — preserve original Channel A data if any
    test_reward = None
    for k in ("tests_total", "tests_passed", "tests_failed", "test_based_reward"):
        if original_scores.get(k) is not None:
            scores[k] = original_scores[k]
```

### Part 3: Document `script/rerun_tests.py` as recovery path

For existing broken runs (tests generated but never executed), the recovery is:
```bash
python3 script/rerun_tests.py --run output/openclaw/<task>/trajectories/<model>/run_N
```

This already handles mock stack startup + test execution. Add a note to `script/AGENTS.md` documenting this as the Channel-A-only re-execution path.

### Part 4 (ALREADY DONE): Provider isolation fix enables `gen_tests` under OAuth

Commit `c49ac85` (Change 2) already fixed `eval/run_batch.py:3881`:
```python
gen_tests = bool(
    (config.bedrock_inference_arn and config.aws_bearer_token)
    or use_oauth  # OAuth sidecar routes opus through bridge for testgen
)
```

This means `gen_tests=True` under OAuth, which flows into `exec_tests = bool(True and enable_mock_stack) = True` when mock stack is enabled. **This partially fixes the problem for the common case** (OAuth + mock stack). Part 1/Level 2 is needed for the remaining edge case (pre-shipped tests without OAuth or Bedrock creds).

---

## Verification Plan

1. Run `kayla_morgan` task with the fix — confirm `ctrf.json` and `reward.txt` are produced
2. Confirm `score.json` has non-null `test_based_reward`
3. Confirm `pass_summary.json` has valid `test_reward` and correct `combined_reward`
4. Run `script/rerun_tests.py` on the existing run to verify it produces correct results
5. Run regrade on a run that already has ctrf.json — confirm it doesn't overwrite with nulls
6. Run regrade on a run WITHOUT ctrf.json — confirm it doesn't write null test fields

---

## Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `eval/run_batch.py:~2469` | Per-task auto-enable: `task.get("test_code") and network` override | P0 |
| `eval/run_batch.py:~3887` | Dispatch auto-enable: `enable_mock_stack and (gen_tests or use_oauth)` | P1 |
| `script/regrade.py:~208` | `tests_total > 0` guard; preserve original Channel A data | P1 |
| `script/AGENTS.md` | Document `rerun_tests.py` as Channel-A recovery path | P2 |

---

## Priority Order

1. **Part 1 Level 2** (per-task override at line 2469) — definitive fix for all cases
2. **Part 1 Level 1** (dispatch-level belt-and-suspenders) — covers common path
3. **Part 2** (regrade guard) — prevents regrade from writing nulls
4. **Part 3** (documentation) — recovery path for existing broken runs
5. **Part 4** (already shipped in c49ac85) — OAuth `gen_tests` fix
