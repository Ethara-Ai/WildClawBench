# QC Report — LAMAR_002_february_prospect_deadline (round-2 post-fix re-audit)

## Summary
- Total findings: 1
- Findings by defect class (1–19): D1:0 D2:0 D3:0 D4:0 D5:0 D6:0 D7:0 D8:0 D9:0 D10:0 D11:0 D12:0 D13:0 D14:0 D15:0 D16:0 D17:1 D18:0 D19:0
- High-severity findings: 0
- Weight scale verified: yes (magnitudes observed: `{-5,-3,1,3,5}` — subset of `{-5,-3,-1,1,3,5}`)
- pytest_positive_total: 76
- pytest_negative_total: 28
- rubric_total: 100 (sum of positive max scores R1..R3, R8..R28)
- Collected tests: 34 across 25 `Test*` classes (0 module-level tests)
- Weight keys: 34, all `ClassName::test_method_name` form, exact bijection with collected tests

## Findings

### DEFECT #17 — Minor MECE inversion (three importance-3 freshness/watch-list items on Channel B)

```
DEFECT #17 — Three "important" factuality / state_change criteria still on Channel B
  evidence: R12 (T9 FRESH Airtable draft_eligibility cut over prior week's
              snapshot, score 3, important, evaluation_target=user_facing_message)
            R13 (T12 FRESH Notion line_combinations cut, score 3, important,
              evaluation_target=user_facing_message)
            R27 (T4 Oskar Ek stride watch-list flag on day-two morning skate,
              score 3, important, evaluation_target=state_change)
  why: D17 — R12 and R13 are FRESH-vs-stale token detections that could be
       Channel A audit-log diffs. R27 is a Notion state_change that could
       be a Channel A scan of the oskar_ek_dev page; the day-five Oskar
       lineup decision is already covered by test_oskar_lineup_decision_
       recorded, but the earlier day-two watch-list flag is not.
  severity: ⚠ — all three are "important" not "critically_important". The
            previously-blocking R21 critical (week-closer reconciliation) is
            now fully covered by test_week_closer_page_reconciles_seven_items.
            Channel B can plausibly catch these three freshness/watch-list
            distinctions from the user-facing summary.
  fix-hint: Optional — TestOutcomeAirtable::test_fresh_draft_eligibility_
            cut_used, TestOutcomeNotion::test_fresh_line_combinations_used,
            TestOutcomeNotion::test_oskar_watch_list_flag_recorded (each
            weight 3) would close the residual MECE inversion.
```

## Cross-cutting (C1–C6)

- **C1 — Header template intact**: ⚠ — Header is functional (stdlib imports + 20 URL constants + helpers) but custom helper set (`_audit_summary`, `_audit_requests`, `_business_request_count`, `_endpoint_match_count`, `_request_bodies_for`, `_request_bodies_to_channel`, `_parse_body`) is not the canonical §"Required Header Template" block. Cosmetic.
- **C2 — stdlib only**: ✅ — Imports are `json`, `os`, `urllib.request`.
- **C3 — Hardcoded output folders**: ✅ — No literal output folders.
- **C4 — Class-prefix discipline**: ✅ — 25 classes: 11 `TestBehavioral*`, 8 `TestOutcome*` (now including `test_week_closer_page_reconciles_seven_items`), 6 `TestNegativeWeight*`. All 6 negative-weight tests have negative weights.
- **C5 — Distractor coverage**: ✅ — All declared distractors covered (mailchimp skip_path -5, dormant bucket -3 across the 7 dormant services).
- **C6 — Calibration sanity**: ⚠ —
  - No-op agent: 0 tests pass. 0 < 0.25 × 76 = 19.0 ✅.
  - SOTA agent: passes most positive tests. test_week_closer_page_reconciles_seven_items requires the agent to actually write 5+ of the 7 anchor tokens to a Notion week-closer page, so a SOTA may not max it. Expected band 0.55–0.70 × 76 = 41.8–53.2. SOTA at ~95% over band. ⚠ — structurally inherent.

## Defect scorecard

| #   | Defect                                            | Result | Hits | Note |
|-----|---------------------------------------------------|:------:|:----:|------|
| D1  | Inverted mutation-guard assertion                 | ✅ | 0 | All asserts positive |
| D2  | Tests against irrelevant API endpoints            | ✅ | 0 | Distractors declared in MANIFEST |
| D3  | Contradictory test pairs                          | ✅ | 0 | No pos/neg pairs on same endpoint |
| D4  | Penalty overlap on one action                     | ✅ | 0 | One -test per endpoint (cap respected) |
| D5  | Test checks the wrong field                       | ✅ | 0 | Typed audit fields used |
| D6  | Tautological / off-topic test                     | ✅ | 0 | _business_request_count excludes /audit /health |
| D7  | Always-failing / impossible test                  | ✅ | 0 | All tests reachable |
| D8  | Duplicate / redundant test functions              | ✅ | 0 | Distinct thresholds (volume vs presence) |
| D9  | Test weights vastly outweigh rubric               | ✅ | 0 | No endpoint > 0.4 × 76 = 30.4 |
| D10 | Extreme penalty stacking (suite-level)            | ✅ | 0 | -5 max per coherent rogue |
| D11 | Mis-assigned weight (wrong magnitude/API)         | ✅ | 0 | Magnitudes match rubric importance |
| D12 | Auto-generated comments/docstrings                | ✅ | 0 | No banner, no templated docs |
| D13 | Non-standalone file (bundle dependency)           | ✅ | 0 | stdlib only |
| D14 | Required APIs not fully covered                   | ✅ | 0 | R21 week-closer reconciliation now covered |
| D15 | Valid Python file                                 | ✅ | 0 | py_compile passes |
| D16 | Test broken by its own definition                 | ✅ | 0 | All names resolve at runtime |
| D17 | rubric.json + test_outputs.py follow MECE         | ⚠ | 1 | R12/R13/R27 minor (all "important") |
| D18 | Weight keys are pytest node IDs with class name   | ✅ | 0 | All 34 keys class-qualified |
| D19 | Weight-key set is 1:1 with collected tests        | ✅ | 0 | Bijection holds |

## Verdict
**PASS WITH WARNING** — Zero ❌ across the 19 scorecard rows; only D17 ⚠ remains and it covers three "important" (not "critically_important") freshness/watch-list items that the LLM judge can plausibly catch from the user-facing message. Cross-cutting: C1 ⚠ (custom header, cosmetic), C2/C3/C4/C5 ✅, C6 ⚠ (SOTA over-band, structurally inherent). No FAIL-HARD trigger.
