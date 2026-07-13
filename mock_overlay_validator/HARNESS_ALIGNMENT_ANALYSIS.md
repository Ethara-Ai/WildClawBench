# Mock Overlay Validator vs. Runtime Harness — Alignment Analysis

**Scope**: Compare `/Users/apple/Documents/WildClawBench/mock_overlay_validator/validate.py` (1357 lines, static checker) against what the live mock-API harness under `/Users/apple/Documents/WildClawBench/environment/` actually enforces at load time, with special focus on **data types** and **JSON schema**.

**Verdict**: The validator is a **faithful but deliberately looser** static approximation of the runtime. Same file coverage, same top-level shape rules, and same CSV-vs-JSON overlay precedence. But at the per-cell type level, the validator is **conservative on purpose** — it lets things pass that the runtime will accept, and only rejects things the runtime will *definitely* reject. There are, however, **six concrete divergences** where the runtime is stricter or subtler than the validator recognizes; these produce false negatives (things the validator OKs but the runtime will reject). See §5.

---

## 1. Fleet coverage — perfect match

- `environment/` contains **101** `<name>-api/` directories (plus shared modules, postman collections, cache dirs).
- `mock_overlay_validator/examples/` contains **exactly 101** `<name>-api/` directories.
- `diff <(ls env | grep -- '-api$') <(ls examples)` → **empty** (all API names identical).
- Across all 101 APIs, comparing every `.csv`/`.json` file (476 files total):
  - **476/476 byte-identical** (`md5sum` match) between `environment/<api>/*.{csv,json}` and `mock_overlay_validator/examples/<api>/*.{csv,json}`.
  - **0 byte-different**.
  - Files present in `environment/` but not in `examples/` are exclusively `<api>_postman_collection.json` (API docs, not data seeds) — correct omission.
- **Zero CSV files** exist in `examples/` (all 476 examples are `.json`). This is fine — the validator explicitly matches on **stem**, so `messages.csv` overlays still match example `messages.json`, mirroring the runtime's CSV-overlay-wins behavior.

**Conclusion**: The example corpus is a byte-exact snapshot of the runtime's baseline data. Whatever schema the validator infers from `examples/<api>/foo.json`, the runtime will see the identical baseline until an overlay shadows it.

---

## 2. File-shape rules — aligned

| Rule | Validator | Runtime | Aligned? |
|---|---|---|---|
| CSV must be UTF-8 (BOM allowed) | `utf-8-sig` (`validate.py:149`) | `utf-8-sig` in `read_csv_with_ctx` (`_mutable_store.py:129-181`) | ✅ |
| JSON encoding | `utf-8-sig` (`validate.py:992`) — BOM-tolerant | Plain `utf-8` in `read_json_with_ctx` — **BOM-intolerant** | ⚠️ **See §5.1** |
| Duplicate CSV headers | ERROR `CSV_DUPLICATE_HEADER` (`validate.py:214`) | Fatal `CoerceError` in reader | ✅ |
| Ragged CSV rows (extra fields) | ERROR `CSV_RAGGED_ROW` (`validate.py:239`) | Fatal `CoerceError` via `restkey="__ragged__"` | ✅ |
| Short CSV rows (missing trailing fields) | Padded silently with `""` (`validate.py:249`) | Allowed — missing keys become `None`, absorbed by `opt_*` or raised by `strict_*` | ✅ |
| Malformed JSON | ERROR `JSON_MALFORMED` | Fatal `CoerceError` | ✅ |
| JSON table must be top-level array | ERROR `JSON_NOT_ARRAY` unless enveloped | Fatal `CoerceError` unless data module has its own envelope handler | ⚠️ **See §5.2** |
| JSON document must be top-level object | ERROR `JSON_NOT_OBJECT` | Documents use plain `json.load` with no shape check (`register_document` accepts any JSON) | Validator is **stricter** — a document that's not a top-level object would still load at runtime, though no shipped API relies on it |
| Every JSON table row must be an object | ERROR `JSON_ROW_NOT_OBJECT` | Fatal `CoerceError` from `read_json_with_ctx` | ✅ |
| CSV-vs-JSON overlay precedence | Stem-based match ignores extension (validator accepts either) | Sibling `.csv` always wins over `.json` (`_mutable_store.py:230-269`) | ✅ (validator doesn't enforce a direction because it inspects each file independently) |
| Wrapped-envelope tables (QuickBooks-style) | Detected by `_peel_wrapped_table` (`validate.py:635-657`) | Handled per-API in `<api>_data.py` (QuickBooks uses `_load_qbo_envelope`) | ⚠️ **See §5.3** |

---

## 3. Data-type inference — deliberately looser than runtime

### 3.1 What the validator infers (`validate.py:287-323`)

The validator uses a coarse label taxonomy: `null`, `bool`, `int`, `float`, `json_list`, `json_dict`, `blank`, `int_str`, `float_str`, `str`.

Type equivalence groups (`validate.py:342-346`):
```python
_TYPE_GROUPS = [
    {"int", "float", "int_str", "float_str", "str"},   # all numerics ↔ strings interchangeable
    {"json_list", "json_dict"},                        # nested containers interchangeable at column level
    {"bool"},                                          # bool is a strict singleton
]
```

Consequences:
- `1`, `1.0`, `"1"`, `"3.14"`, `"forty-two"` are all **column-compatible** with each other. Rationale documented at lines 336–341: *"a CSV cell is always read as str by csv.reader, so column-level 'drift' between '42' and 'forty-two' is just a per-row coercion problem, not a schema problem."*
- Python `bool` is **not** compatible with `"true"` (str) or `1` (int). The only strictly-typed column class.
- Nulls (`None`) and blanks (`""`) are **dropped** from type sets — a fully-null example column places no constraint on the overlay.

### 3.2 What the runtime does at load time

The runtime does **zero** type coercion during raw read. Every CSV cell is `str` or `None`; JSON values pass through as-is. Coercion happens **later**, when each `<api>_data.py`'s `_coerce_*` function invokes the `strict_*` / `opt_*` helpers on specific columns.

`strict_*` helpers (`_mutable_store.py:272-322`):
- `strict_int` → `int(str(v).strip())`; raises `CoerceError` on blank or unparseable.
- `strict_float` → same, plus rejects NaN/Inf.
- `strict_bool` → `str(v).strip().lower()` must be in `{"true","1","yes","t","y"}` or `{"false","0","no","f","n"}`. Nothing else.
- `strict_str` → `str(v)`; `None` becomes `""`. Never raises on type.
- `strict_csv_list` → `str(v).split(sep)`, no strip on items.

`opt_*` variants silently default to caller-supplied `default` instead of raising.

### 3.3 Alignment verdict

The validator's type comparison is **deliberately weaker** than the runtime coercers. This is a design choice explicitly documented at `validate.py:292-295`. The tradeoff:

- ✅ **No false positives** on prose-in-strings, comma-lists, ISO datetimes, mixed int/float representations — the runtime would happily accept these.
- ⚠️ **False negatives** where the runtime's `strict_*` helpers will reject values the validator's coarse groups accept. See §5 for the specific gaps.

---

## 4. JSON schema comparison — nested keys, ragged rows, envelopes

### 4.1 Column / key discovery

- **CSV columns**: from header row, checked for duplicates and blanks (`validate.py:161-262`).
- **JSON table columns**: intersection of row key-sets = "required"; union = "known" (`validate.py:399-411`). Missing required → `SCHEMA_MISSING_COLUMNS` error; extras → `SCHEMA_EXTRA_COLUMNS` warn.
- **Nested JSON documents**: recursive `_deep_compare` (`validate.py:447-576`) walks paths like `identity.json.owners.acc_chk_001` and emits `KEY_MISSING` / `KEY_EXTRA` / `TYPE_MISMATCH`. Ragged object keys inside arrays reported as `RAGGED_OBJECT_KEYS` info.
- **Wrapped envelopes** (QuickBooks `{"QueryResponse":{"Customer":[...]}}`): `_peel_wrapped_table` (`validate.py:635-657`) requires exactly one outer key, then either shape A (`{wrap: [rows]}`) or shape B (`{wrap: {inner: [rows], ...scalars}}`).

### 4.2 Runtime's actual behavior

- Runtime does **not** distinguish "required" from "optional" columns by union/intersection. It only fails when a **coercer** calls `strict_*(row, col)` and the row doesn't have that column. This is a per-API contract encoded in Python, not derivable from example data.
- Runtime silently keeps **extra columns** if the coercer uses `**_strip_ctx(r)` (majority pattern, e.g. `gmail_data.py:65-71`), or silently drops them if the coercer uses a whitelist dict (e.g. `box_data.py`, `quickbooks_data.py:56-72`). Behavior is **per-API**.
- Runtime document loading (`register_document`) uses plain `json.load` — no shape enforcement.

### 4.3 Alignment verdict

- The validator's union/intersection heuristic is a **reasonable proxy** for "what columns must be present," but it's not a perfect stand-in for the actual `strict_*` calls inside coercers. A column present in every example row but never accessed by a `strict_*` call would be flagged missing by the validator even though the runtime doesn't care.
- The validator's document-must-be-object rule is **stricter than runtime**. In practice all shipped documents (`profile.json`, `workspace.json`, etc.) are objects, so this rule is harmless.
- Envelope handling: the validator has a **generic** envelope detector, but the runtime has **per-API envelope logic**. Both accept the QuickBooks case; edge cases outside QuickBooks may diverge. See §5.3.

---

## 5. Concrete divergences (potential false negatives / positives)

These are the actionable gaps. All are **cases where the validator OKs an overlay the runtime will reject** unless otherwise noted.

### 5.1 JSON BOM handling
- **Validator**: opens JSON with `utf-8-sig` (`validate.py:992`) → BOM tolerated.
- **Runtime**: opens JSON with plain `utf-8` (`_mutable_store.py:184-227`) → BOM-prefixed JSON raises `UnicodeDecodeError` → `CoerceError`.
- **Impact**: A JSON overlay saved from Notepad/Excel with a BOM will pass the validator but crash the container at eager-load time. Low frequency but real.

### 5.2 Envelope tables — generic vs per-API
- **Validator**: applies its generic envelope detector uniformly to any `.json` example whose top level is a single-key object.
- **Runtime**: only QuickBooks (and any future API that hand-rolls a similar loader) actually unwraps envelopes. Every other API's `read_json_with_ctx` requires a **top-level list**.
- **Impact**: If a non-QuickBooks example happens to be an enveloped shape, the validator treats it as a table and accepts overlays in the same enveloped form — but the runtime's generic reader will raise `CoerceError("expected a JSON array of row objects")`. Currently no shipped example beyond QuickBooks uses enveloping, so this is dormant, but adding an enveloped seed to any other API would silently misalign.

### 5.3 String-encoded booleans in CSV vs JSON-native booleans — RESOLVED in contract mode
- **Validator (legacy inference)**: bool was a singleton group; a CSV `"true"` against a JSON native `true` example produced `SCHEMA_TYPE_DRIFT`.
- **Validator (contract mode, §6)**: when a `strict_bool`/`opt_bool` contract is derived from `<api>_data.py`, both Python `True`/`False` and any string in `{true,1,yes,t,y,false,0,no,f,n}` are accepted — matching the runtime `_TRUE_TOKENS`/`_FALSE_TOKENS` allow-list exactly.
- **Runtime**: `strict_bool` accepts both — `str(True).strip().lower() == "true"` ∈ `_TRUE_TOKENS`.
- **Impact**: ✅ No more false positive when contract is available. Legacy behavior retained for APIs without a discoverable coercer.

### 5.4 `opt_csv_list` separators
- **Validator**: does not model per-column list separators. A column that `_data.py` splits with `;` (e.g. github issues `labels` uses `opt_csv_list(r, "labels", sep=";")`, `github_data.py`) has no `;`-vs-`,` awareness in the validator.
- **Runtime**: splits by the coercer-specified separator. A comma-separated overlay for a semicolon column will yield the wrong list contents.
- **Impact**: Silent runtime data corruption not caught by the validator. Since the validator classifies list-string values as `"str"`, no type drift is reported.

### 5.5 Raw `r["col"]` accessors in coercers (unmodeled required columns)
- **Validator**: assumes required = "present in every example row."
- **Runtime**: some coercers use raw `r["col"]` (not `strict_*`), which raises `KeyError` instead of `CoerceError` on missing column. Examples: `gmail_data.py:80` (`r["body"]`), several `quickbooks_data.py` accessors.
- **Impact**: The validator's `SCHEMA_MISSING_COLUMNS` **does** cover these (because they're present in every example row → in the required set), so alignment is coincidentally OK. But the *reason* differs, which matters if the example ever changes to be ragged on that column.

### 5.6 `EXAMPLE_BROKEN` severity mismatch
- **README** documents `EXAMPLE_BROKEN` as `warn`.
- **Code** emits it as `SEV_ERROR` (`validate.py:780`, `:966`).
- **Impact**: Cosmetic/documentation bug in the validator itself, not a runtime-alignment issue. Users hitting this will see an "error" exit code even though the README implies a warning.

### 5.7 Numeric strictness — `int` column receiving JSON native `true`
- **Validator**: JSON `true` → label `"bool"`. `bool` group `{"bool"}` doesn't intersect the numeric group, so an int-typed column with a bool overlay-cell trips `SCHEMA_TYPE_DRIFT` (correct behavior).
- **Runtime**: `strict_int(row, col)` calls `int(str(v).strip())`. `str(True) == "True"`, and `int("True")` raises `ValueError` → `CoerceError`.
- **Impact**: ✅ Both sides reject. Aligned.

---

## 6. Coercer-contract mode — the validator inherits the runtime's own type expectations

The validator ships with a **baked-in** per-table contract for every API in the fleet, stored in `_baked_contracts.py`. Nothing outside `mock_overlay_validator/` is read at runtime.

The bake is produced by `tools/rebake_contracts.py`, which parses `environment/<api>-api/<api>_data.py` with the standard-library `ast` module and derives, for each `_store.register(...)` call:

- Every column reached by a `strict_int`/`strict_float`/`strict_bool`/`strict_str`/`strict_csv_list` or `opt_*` call — the second positional argument is the column name.
- Every column reached by a raw `r["col"]` / `r.get("col")` in the coercer body (treated as required, no type gate — matches the runtime's `KeyError` / `.get()` semantics).
- The declared `primary_key=`, distinguishing real seed columns from synthesized-in-the-lambda `_pk` composites (via list-comprehension AST detection).

At runtime, `_validate_table` then:

1. Adds contract-required columns to the required set (union with example-derived requirements).
2. For every column with a coercer contract, runs a **coercer-equivalent** parse check on each overlay row and emits `SCHEMA_COERCE_MISMATCH` on failure. This supersedes the coarse `SCHEMA_TYPE_DRIFT` label for that column.
3. Enforces PK uniqueness with `SCHEMA_PK_DUPLICATE` (except when the PK is synthetic — e.g. `ring-api/motion_zones`).
4. Falls back to the legacy example-inference path for APIs absent from `BAKED_CONTRACTS`.

**Coverage as of this change**: 92 of 101 shipping APIs are baked in. The remaining APIs either have no `_store.register(...)` call (docs-only APIs) or use patterns the walker doesn't yet recognise — they fall back to the legacy path, so **no regression**. Full-fleet self-validation (476 example files) returns zero errors.

**Regeneration workflow** when `environment/` coercers change:

```bash
python3 tools/rebake_contracts.py > _baked_contracts.py
python3 tools/regenerate_examples.py    # optional: coerce example values to match
```

Examples now ship with values in the **coerced form** the runtime holds in memory (`beds: 1` int, `price_per_night: 189.0` float, `instant_book: true` bool). Overlays with either coerced or string-form values validate identically, because the runtime coercers accept both.

**Resolutions**: §5.3 (native-vs-string bool) and §5.5 (raw `r["col"]`) are now first-class handled. §5.4 (`csv_list` separator) is partially modelled (the `sep` value is captured on the `ColumnContract`, but semantic list-splitting comparison isn't wired yet).

---

## 7. Registered filenames — validator uses a static proxy for a runtime that has no registry

- The runtime has **no** registry of "known filenames." A `<api>_data.py` just calls `_load("labels.json", "labels")` inside an `initial_loader=` lambda; any file on disk that no such call references is **silently ignored**.
- The validator's `UNREGISTERED_FILENAME` warning is a **static approximation** — it checks whether the overlay's stem matches a file in `mock_overlay_validator/examples/<api>/`, which is a curated proxy for the set of stems some `_data.py` actually loads.
- Since `examples/` is byte-identical to `environment/`, the proxy is **exact for shipping APIs**. If a new API is added and `_data.py` references a filename that wasn't snapshotted into `examples/`, the validator will falsely warn.

---

## 8. Diagnostic-code inventory (29 codes total)

Grouped by category. Every code emitted by `validate.py`:

**File / IO**: `FILE_UNREADABLE` (err), `NOT_UTF8` (err), `PATH_NOT_FOUND` (err), `DIR_NOT_FOUND` (err).

**Directory / API layout**: `UNKNOWN_API` (err), `API_UNDETECTABLE` (err), `UNKNOWN_EXTENSION` (warn), `UNREGISTERED_FILENAME` (warn), `DIR_NAMING` (err), `OVERLAY_DIR_EMPTY` (warn), `EMPTY_MOCK_DATA` (warn), `NO_TASKS` (info).

**CSV structural**: `CSV_EMPTY` (warn), `CSV_MALFORMED` (err), `CSV_DUPLICATE_HEADER` (err), `CSV_BLANK_HEADER` (warn), `CSV_RAGGED_ROW` (err).

**JSON structural**: `JSON_MALFORMED` (err), `JSON_NOT_ARRAY` (err), `JSON_NOT_OBJECT` (err), `JSON_ROW_NOT_OBJECT` (err), `DOCUMENT_BAD_EXTENSION` (err).

**Table schema**: `SCHEMA_MISSING_COLUMNS` (err), `SCHEMA_EXTRA_COLUMNS` (warn), `SCHEMA_TYPE_DRIFT` (err), `OVERLAY_EMPTY` (warn).

**Deep JSON**: `KEY_MISSING` (err), `KEY_EXTRA` (warn), `TYPE_MISMATCH` (err), `RAGGED_OBJECT_KEYS` (info).

**Validator meta**: `EXAMPLE_BROKEN` (**emitted as err** despite README saying warn — see §5.6), `EXAMPLE_EMPTY` (info).

---

## 9. Manual QA — evidence

```
$ python3 mock_overlay_validator/validate.py --list-apis | wc -l
101

$ python3 mock_overlay_validator/validate.py mock_overlay_validator/examples/gmail-api
OK: no issues found

  files_checked=4 apis_seen=1 errors=0 warnings=0 infos=0
```

- Validator lists exactly 101 APIs — matches both `environment/` API count and `examples/` API count.
- Validating `examples/gmail-api` against itself: 0 errors, 0 warnings.
- 476/476 data files byte-identical between `environment/` and `examples/` (md5-verified).

---

## 10. Summary verdict

**Does the validator use the same data-types and JSON schema as the runtime harness?**

- **Same file coverage**: ✅ Yes — 101 APIs, byte-identical seed files.
- **Same top-level shape rules**: ✅ Yes for tables (list-of-objects). ⚠️ Slightly stricter for documents (validator requires top-level object; runtime accepts any JSON).
- **Same CSV parsing semantics**: ✅ Yes (empty cell → null, headers-mandatory, no coercion).
- **Same JSON parsing semantics**: ✅ Mostly — except **BOM tolerance** (validator: yes, runtime: no) and **envelope handling** (validator: generic, runtime: per-API).
- **Same overlay-precedence rule**: ✅ CSV-overlay-wins, mirrored on both sides.
- **Same type inference at cell level**: ❌ **Deliberately not** — the validator uses a conservative coarse taxonomy (5 groups) whereas the runtime uses per-column `strict_*` helpers with specific token whitelists (booleans) and `int(str(v).strip())` semantics (numerics). The validator is a *superset* of what the runtime accepts, on purpose.

**Actionable gaps** (fix these if you want the validator to reject exactly what the runtime rejects):
1. Switch JSON reader to `utf-8` (not `utf-8-sig`) to align with runtime BOM intolerance. (§5.1)
2. Restrict envelope detection to a per-API allowlist, or drop it and let QuickBooks be the only exception. (§5.2)
3. Optionally recognize string-encoded booleans (`"true"`/`"false"`/…) as compatible with the `bool` type group to eliminate the CSV-vs-JSON-native-bool false positive. (§5.3)
4. Model per-column list separators (semicolon for github labels). (§5.4)
5. Fix `EXAMPLE_BROKEN` severity to match README (warn, not error). (§5.6)

None of these are correctness-blocking for the current shipping snapshot; they're precision fixes for future overlays.
