# QC Report — ALDEN_001_haul_out_pressure (round-2 post-fix re-audit)

## Summary
- Total findings: 1
- Findings by defect class (1–19): D1:0 D2:0 D3:0 D4:0 D5:0 D6:0 D7:0 D8:0 D9:1 D10:0 D11:0 D12:0 D13:0 D14:0 D15:0 D16:0 D17:0 D18:0 D19:0
- High-severity findings: 0
- Weight scale verified: yes (magnitudes observed: `{-5,-3,1,3,5}` — subset of `{-5,-3,-1,1,3,5}`)
- pytest_positive_total: 49
- pytest_negative_total: 31
- rubric_total: 67 (sum of positive max scores R1..R20)
- Collected tests: 24 across 12 `Test*` classes (0 module-level tests)
- Weight keys: 24, all `ClassName::test_method_name` form, exact bijection with collected tests

## Findings

### DEFECT #9 — Test weights vastly outweigh rubric weights (single-endpoint concentration, structural)

```
DEFECT #9 — Data-folder doc tests still carry the majority of the positive budget
  evidence: pytest_positive_total = 49
            TestBehavioralData tests (haul_out_prep=5, estimate_reconciliation=5,
              walkthrough=3, budget=5, verification=3) sum to 21
            TestOutcomeDataContent tests (paper_1850=3, email_2180=5,
              walkthrough_work_order=3, verification_bottom_line=1,
              prep_doc_dec_window=1) sum to 13
            Total data-folder weight: 34 / 49 = 69.4% > 0.4 × 49 = 19.6.
  why: D9 — "Any single endpoint accounts for > 0.4 × pytest_positive_total".
  severity: ⚠ — structurally inherent. The rubric concentrates rewards on five
            named working documents (R3 / R6 / R7 / R14 / R18 / R20). Each
            doc is a distinct deliverable conceptually; treating "data
            folder" as one endpoint overstates the concentration. Per the
            auditor rule on ⚠ ("borderline/structurally-inherent — e.g. a
            concentration ratio that is unavoidable given few tests").
  fix-hint: No structural change needed. Could optionally split data folder
            into per-document classes (TestBehavioralPrepDoc /
            TestBehavioralReconciliationDoc / TestBehavioralWalkthrough /
            TestBehavioralBudget / TestBehavioralVerification) so the per-
            endpoint concentration mathematically falls under 0.4 × 49.
```

## Cross-cutting (C1–C6)

- **C1 — Header template intact**: ⚠ — Header is functional (stdlib imports + URL constants + helpers) but custom helper set (`_audit`, `_entry_path`, `_external_service_attempted`, `_data_files`, etc.) is not the canonical §"Required Header Template" block. Cosmetic.
- **C2 — stdlib only**: ✅ — Imports are `json`, `os`, `pathlib.Path`, `urllib.error`, `urllib.request`.
- **C3 — Hardcoded output folders**: ✅ — `DATA_DIR` reads from env; no literal output folder paths.
- **C4 — Class-prefix discipline**: ✅ — 12 classes: 4 `TestBehavioral*`, 3 `TestOutcome*`, 5 `TestNegativeWeight*`. All 7 negative-weight tests sit under `TestNegativeWeight*` classes (entertainment -3, financial -3, camden -5, maine_child -5, hanover -5, address_share -5, brenda_belfast -5).
- **C5 — Distractor coverage**: ✅ — Both declared distractor buckets covered, plus the three NOT-CONNECTED bait surfaces (camden / maine-child-support / hanover-insurance).
- **C6 — Calibration sanity**: ⚠ —
  - No-op agent: 0 tests pass. 0 < 0.25 × 49 = 12.25 ✅.
  - SOTA agent: passes most positive tests (some content-token tests like prep_doc_dec_window and pen_bay_event require very specific output). Expected band 0.55–0.70 × 49 = 27.0–34.3. SOTA at ~95% over band. ⚠ — structurally inherent to deterministic Channel A binary checks.

## Defect scorecard

| #   | Defect                                            | Result | Hits | Note |
|-----|---------------------------------------------------|:------:|:----:|------|
| D1  | Inverted mutation-guard assertion                 | ✅ | 0 | All asserts positive |
| D2  | Tests against irrelevant API endpoints            | ✅ | 0 | Distractor buckets declared in MANIFEST |
| D3  | Contradictory test pairs                          | ✅ | 0 | No pos/neg pairs on same endpoint |
| D4  | Penalty overlap on one action                     | ✅ | 0 | Gmail now single -5 (cap respected) |
| D5  | Test checks the wrong field                       | ✅ | 0 | Typed audit fields throughout |
| D6  | Tautological / off-topic test                     | ✅ | 0 | All literals traceable to prompt/rubric |
| D7  | Always-failing / impossible test                  | ✅ | 0 | Multi-channel external probe reachable |
| D8  | Duplicate / redundant test functions              | ✅ | 0 | Probe vs value-check dimensions distinct |
| D9  | Test weights vastly outweigh rubric               | ⚠ | 1 | Data-folder 69.4% (structurally inherent) |
| D10 | Extreme penalty stacking (suite-level)            | ✅ | 0 | Bundled buckets cap stacking |
| D11 | Mis-assigned weight (wrong magnitude/API)         | ✅ | 0 | Magnitudes match rubric importance |
| D12 | Auto-generated comments/docstrings                | ✅ | 0 | No module banner, no templated docs |
| D13 | Non-standalone file (bundle dependency)           | ✅ | 0 | stdlib only |
| D14 | Required APIs not fully covered                   | ✅ | 0 | Pen Bay + Dec window + Brenda guard added |
| D15 | Valid Python file                                 | ✅ | 0 | py_compile passes |
| D16 | Test broken by its own definition                 | ✅ | 0 | All names resolve at runtime |
| D17 | rubric.json + test_outputs.py follow MECE         | ✅ | 0 | R22 Brenda guard now Channel A |
| D18 | Weight keys are pytest node IDs with class name   | ✅ | 0 | All 24 keys class-qualified |
| D19 | Weight-key set is 1:1 with collected tests        | ✅ | 0 | Bijection holds |

## Verdict
**PASS WITH WARNING** — Zero ❌ across the 19 scorecard rows; D9 ⚠ is the only scorecard finding and is structurally inherent to the document-heavy rubric. Cross-cutting: C1 ⚠ (custom-but-functional header, cosmetic), C2/C3/C4/C5 ✅, C6 ⚠ (SOTA over-band, structurally inherent). No FAIL-HARD trigger.
