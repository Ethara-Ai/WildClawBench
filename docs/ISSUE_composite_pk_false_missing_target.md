# ISSUE: Static inject validator emits false `missing-target` FATALs on every composite-PK table

**Component:** `src/utils/inject_validator.py`
**Severity:** P1 — blocks runs (`injection_ok=False`) on valid, correctly-authored tasks
**Affects:** HEAD (`0e2e86e`); partially introduced/left unfixed by `f2c16b8` "Injection false positive fixes"
**Scope:** ~25 of 41 composite-`_pk` recipes across the mock fleet
**Status:** diagnosed, fix approach agreed, not yet implemented

---

## 1. Symptom

A correctly-authored pk-addressed `patch` op against a composite-PK table is rejected by the authoring pre-flight:

```
FATAL   : 1
    loadin_pulled_forward / sil-loadin-date-1024-to-1023 -> missing-target
      target row 'item-gp-2205@col-date' not found in seed/prior-inject snapshot for monday-api
WARNINGS: 2
    label_revision_b / sil-labelrev-reva-to-revb
    loadin_slot_released / sil-slot-status-confirmed-to-released
PREDICTION: run BLOCKED / injection_ok=False
```

The op is valid. At runtime the target row exists and the mutation applies cleanly. The 1-fatal/2-warning split is the same defect three times: `is_first_boundary` (`inject_validator.py:355-358`) escalates stage 0 to FATAL and demotes later stages to WARNING.

Reproduced locally at 100% failure rate on `input/scott_lee_aa87a57f-2110-4512-8fde-7f4aabaacc67`:

```
rows=104   distinct runtime pks=104
HEAD: resolved 0/104
```

---

## 2. Root cause analysis

The validator is static — it has no `/admin/tables` to consult — so it reconstructs the seed key space from `mock_data/` on disk. Runtime loaders synthesize composite `_pk` values at load time, so the reconstruction has to mirror each loader's recipe. It cannot, and fails four independent ways.

### 2.1 Primary defect — join order is file order, not loader order

`_synthesize_composite_pk` (`inject_validator.py:129-137`, added by `f2c16b8`) joins in `row.items()` iteration order, i.e. JSON key-insertion order:

```python
id_vals = [str(v) for k, v in row.items()
           if isinstance(v, (str, int)) and (str(k).lower() == "pk" or str(k).lower().endswith("id"))]
if len(id_vals) >= 2:
    ids.add("@".join(id_vals))
```

The loader's order is fixed and unrelated (`environment/monday-api/monday_data.py:74`):

```python
"_pk": f"{r['item_id']}@{r['column_id']}"
```

Proof on the failing seed:

```
seed row keys : ['column_id', 'item_id', 'text', 'value']
runtime _pk   : item-0001@status
validator syn : status@item-0001        <- reversed
runtime pk in snapshot?  False
reversed  in snapshot?  True
```

The membership test at `inject_validator.py:350` is a flat `key not in known`, so this is structurally impossible to pass — not a probabilistic miss.

### 2.2 Why it shipped green

`manuel_noble`'s copy of `column_values.json` writes `item_id` first and passes. monday's `groups` (`board_id@group_id`) and `columns` (`board_id@column_id`) also happen to list id fields in loader order. Pure key-order luck decided correctness; the one table whose JSON writes `column_id` first is the one that breaks. No test pinned the order, so the coincidence read as a passing fix.

### 2.3 Three further defects in the same mechanism

None are order-related.

| # | Defect | Mechanism | Example recipes |
|---|---|---|---|
| 2 | Non-id-shaped components | `_row_ids` only collects keys lowercasing to `pk` or ending `id`. `date`, `time`, `country`, `price`, `address`, `pair`, `symbol` are never collected, so the composite can never be assembled. | kraken `pair@time`, amplitude `event_type@date`, airbnb `listing_id@start_date`, mailgun `list_address@address`, openweather `city_id@dt`, pinterest `pin_id@date` |
| 3 | Arity > 3 | The proposed `<= 3` permutation guard excludes them; the current `>= 2` join produces a single wrong-order string. | google-analytics `date@country@pagePath@deviceCategory`, fedex/ups `service@origin_zip@dest_zip@weight_lb` |
| 4 | Non-`@` separator | Synthesis hardcodes `@`. | kubernetes `namespace/name`, ring `device_id/channel`, datadog `metric\|tag_string` |

### 2.4 Fleet census

All 41 composite `_pk` recipes under `environment/`: 16 are all-id-shaped 2-field `@` composites; 25 hit defects 2–4. Every one of those 25 is a latent identical FATAL the moment a task pk-addresses that table.

### 2.5 Secondary structural concern

`_snap(svc)` is a cumulative per-service set mutated across stages (`inject_validator.py:361-362`). Any key manufactured into it is visible to every later stage's membership check. Widening the synthesis therefore widens the blast radius of every wrong guess — this is the same pollution class that produced the earlier `test_output.py` null regression when the synthesis briefly lived inside `_row_ids()` (which also feeds `_created_ids` at lines 102/106).

---

## 3. Why the obvious fix is rejected

The proposal under review is `itertools.permutations(id_vals)` for `2 <= len <= 3`:

- Fixes defect 1 only. Leaves 25/41 recipes broken (~40% coverage).
- Grows the snapshot from 104 to 208 manufactured keys on this one table, all of which enter the cumulative `_snap(svc)`.
- Manufactures keys that correspond to no real runtime pk at any arity (HEAD already does this — e.g. `board-1@backlog@item-0001` from a 3-id-field `items` row whose runtime pk is `item_id` alone).

It converts a reproducible failure into an order-dependent one, which is worse to diagnose than the current deterministic break.

---

## 4. Fix approach

Invert the direction. Stop reconstructing keys into the snapshot; decompose the key under test instead.

**4.1** Delete `_synthesize_composite_pk` (`:129-137`) and its two call sites (`:161`, `:166`). `_row_ids` stays byte-identical — `_created_ids` must remain unpolluted.

**4.2** Collect each row's **scalar values** (not just id-shaped ones) into a per-service value set, populated in `_seed_ids_for_service`'s existing JSON and CSV loops so both seed formats are covered. Expose it via a `_vals(svc)` memo mirroring the existing `_snap(svc)` closure.

**4.3** Add a component-membership fallback at the check site (`:350`):

```python
if key not in known and not _composite_components_present(key, _vals(resolved)):
    # ... existing missing-target defect, unchanged
```

```python
_PK_SEPARATORS = r"[@|/]"

def _composite_components_present(key: str, values: Set[str]) -> bool:
    """True when `key` decomposes into >=2 non-empty components all seeded.

    Runtime loaders synthesize composite _pk from arbitrary row columns in a
    per-table order the static validator cannot know (monday item_id@column_id,
    kraken pair@time, GA date@country@pagePath@deviceCategory, kubernetes
    namespace/name). Checking components against the row value space is
    order-, separator- and arity-independent.
    """
    parts = re.split(_PK_SEPARATORS, key)
    if len(parts) < 2 or not all(parts):
        return False
    return all(p in values for p in parts)
```

Measured: 104/104 resolved, 88 value keys (fewer than HEAD's 104 manufactured keys), and it generalises — verified `kraken` `XXBTZUSD@1780223400` resolves. Coverage ~35/41.

**Separator set.** `@`, `|`, `/` only. `#` is deliberately excluded: its sole use is `_mutable_store.py:763` (`f"{pk_value}#{i}"`), where `i` is a positional index absent from the seed, so including it buys nothing and would split legitimate pks containing `#`.

**Consistency with existing contract.** `_row_ids`'s docstring (`:117`) already states the governing principle: *"over-collecting only relaxes the missing-target check, never breaks a valid op."* This change relaxes only the single key under test rather than the global cumulative snapshot — strictly narrower than both HEAD and the permutations proposal.

**Accepted signal cost.** An op that writes a composite pk with components in the wrong order now passes pre-flight and fails at runtime. Identical in kind to the permutations proposal, narrower in blast radius. Single-component pks are unaffected — `len(parts) < 2` short-circuits — so the primary typo'd-pk case this check exists to catch keeps full strength.

**Residual, out of scope.** 6 recipes remain unreproducible statically: mailchimp `md5(email)`, instacart positional index, discord nested `user['id']`, datadog joined tag string, google-classroom computed `_suffix`. These keep the documented `update_where` / explicit-admin-block escape hatch per `src/utils/AGENTS.md`. To be named in a comment at the check site so the next reader doesn't retry this.

**Rejected alternative.** Reordering `mock_data/monday-api/column_values.json` to put `item_id` first unblocks today but encodes an invisible authoring constraint that the next `column_id`-first task silently re-triggers. Acceptable only as a stopgap alongside the harness fix, never instead of it.

---

## 5. Edge cases and test plan

New file `tests/test_inject_validator_composite_pk.py`, synthetic seeds via `tmp_path`.

### 5.1 Must now PASS (false positives eliminated)

| # | Edge case | Fixture | Assertion |
|---|---|---|---|
| 1 | Reversed 2-id order — the reported bug | `column_id` before `item_id` | `fatal == []` |
| 2 | Canonical 2-id order — regression guard | `item_id` before `column_id` | `fatal == []` |
| 3 | No id-shaped component | kraken `pair@time` | `fatal == []` |
| 4 | Mixed id / non-id | airbnb `listing_id@start_date` | `fatal == []` |
| 5 | 3 components | binance `symbol@interval@open_time` | `fatal == []` |
| 6 | 4 components (past the old `<=3` guard) | GA `date@country@pagePath@deviceCategory` | `fatal == []` |
| 7 | `/` separator | kubernetes `namespace/name` | `fatal == []` |
| 8 | `\|` separator | datadog-shaped `metric\|tag` with both seeded | `fatal == []` |
| 9 | Numeric component stringification | int `open_time` in JSON vs str in pk | `fatal == []` |
| 10 | CSV seed path | same row shape as `.csv` | `fatal == []` |

### 5.2 Must still FAIL (no signal loss)

| # | Edge case | Assertion |
|---|---|---|
| 11 | Single-component pk genuinely absent | `status == "missing-target"` |
| 12 | Composite with one component absent | `status == "missing-target"` |
| 13 | Composite with all components absent | `status == "missing-target"` |
| 14 | Trailing/empty component (`"item-1@"`, `"@a"`, `"a@@b"`) | `missing-target` — `all(parts)` guard holds, empty string never accepted |
| 15 | Wholly unseeded service | `missing-target` |
| 16 | Unresolvable service slug | `status == "unresolved"` (untouched path) |
| 17 | Op extracting zero fields | `status == "empty"` (untouched path) |

### 5.3 Structural invariants

| # | Invariant | Assertion |
|---|---|---|
| 18 | `_row_ids` unchanged | no `@`-joined key in its output for a 2-id row |
| 19 | `_created_ids` unpolluted | no composite key in `pending_creates` for an `upsert` with 2+ id fields — pins the `test_output.py` regression class |
| 20 | Snapshot not inflated | `len(_seed_ids_for_service(...)) <=` HEAD's count for the same seed |
| 21 | `_synthesize_composite_pk` gone | `not hasattr(inject_validator, "_synthesize_composite_pk")` |
| 22 | FATAL/WARNING split preserved | same defect → `fatal` at stage 0, `warnings` at stage 1+ |
| 23 | `doc_merge` unaffected by `/` splitting | doc-path ops stay outside `targets_row = kind in ("rest","patch")` |
| 24 | Non-scalar values skipped | nested `user: {"id": ...}` contributes nothing; discord-shaped op still `missing-target` |
| 25 | Malformed seed does not raise | unparseable JSON / unreadable file → partial set, no exception (`OSError, ValueError` swallow at `:167`) |
| 26 | Service slug fallback | `<service>` and `<service>-api` dirs both resolve |

### 5.4 Documented permissiveness

Assert current behaviour so a future tightening is a deliberate, visible change.

| # | Case | Assertion |
|---|---|---|
| 27 | Cross-row component combination — flat value set accepts components sourced from different rows | passes; docstring + test name record it as accepted |
| 28 | Value collision — component matches an unrelated column's value | passes; recorded as accepted |

### 5.5 Regression suites

```bash
pytest tests/test_inject_validator_composite_pk.py -q     # new, expect all green
pytest tests/test_injection_integrity.py tests/test_inject_validator.py \
       tests/test_inject_director.py -q                   # 57 existing, expect 57 passed
```

### 5.6 Smoke gate

```bash
pytest tests/test_drift_plane_smoke.py -q                 # 6 passed expected
pytest tests/ -q                                          # full unit suite, no new failures
```

### 5.7 End-to-end validation on real corpora

```
# scott_lee (column_id-first, the failing shape): expect fatal=0 warnings=0
# manuel_noble (item_id-first, the lucky shape):  expect no regression vs HEAD
```

Then one live task run confirming `injection_ok: true` with `injection_defects: []` in `score.json`.

---

## 6. Verified as already fixed — no action

`0e2e86e` correctly decoupled test execution from test generation (`eval/run_batch.py:2473-2476`):

```python
effective_exec_tests = execute_tests or (bool(task.get("test_code")) and bool(network))
if effective_exec_tests and task.get("test_code") and not startup_failed:
```

Pre-shipped suites now run whenever the mock network is up, independent of the generation decision. No Bedrock ARN required.

---

## 7. Rollback

Single-file change (`src/utils/inject_validator.py`) plus one new test file. `git revert` restores HEAD behaviour, which is a deterministic FATAL rather than a silent misgrade — safe to back out mid-campaign.
