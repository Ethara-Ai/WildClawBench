# Rubric QC Report (Re-Audit — post-fix)

**Bundle**: `inject-fix-delivery/Alden_Croft` (ALDEN_001_haul_out_pressure)
**Criteria Count**: 27 (R1-R27)
**Verdict**: **Push Ready**
**Reviewed by**: Skeptical Industry Veteran (Rubric_QC v2.0, June 2026)

> Re-audit after remediation of the prior **Fail** verdict (1 Major + 10 Moderate). All Major and Moderate findings addressed. Rubric grew from 24 to 27 criteria (added T4 thermostat, T7 refills, T13 Pen Bay coverage; split the negative block into discrete factuality and safety criteria).

---

## Phase 1 - Schema & Structural

**Sub-Verdict**: Push Ready

| Check | Result | Detail |
|---|---|---|
| Valid JSON | PASS | Parses cleanly; 27 elements. |
| Array structure | PASS | Top-level array; every element an object. |
| Count in 15-25 | PASS (Minor) | 27 — in the 26-30 "over-granular but functional" band. |
| 7 required fields | PASS | Canonical order `number, criterion, is_positive, type, evaluation_target, importance, score`. No extra fields. |
| Field types | PASS | `is_positive` boolean; `score` integer. |
| `type` enum (space-separated) | PASS | `instruction following`, `task completion`, `factuality and hallucination`, `safety & boundaries`. No underscores. |
| `evaluation_target` enum | PASS | All 4 valid values. No `artifact`, no `tool_call`, no `output_content`. |
| `importance` enum | PASS | `important` (20), `critically_important` (7). |
| `score` set | PASS | Observed `{-5,-3,1,3,5}` — all within `{-5,-3,-1,1,3,5}`. |
| Polarity | PASS | 23 positive / 4 negative; no mismatch. |
| Numbering | PASS | R1..R27 sequential, no gaps/dupes. |
| Importance <-> score | PASS | All `critically_important` `|score| >= 3`; no `important` at 5; no `critically_important` at 1. |

---

## Phase 2 - Known Issue Class Audit

**Sub-Verdict**: Push Ready

| # | Issue Class | Status | Findings |
|---|---|---|---|
| #1 | Over-prescribed formatting | Clean | R3 date-stamp dropped; R16 no longer demands the literal tool-id. |
| #2 | Non-existent data references | Clean | `$1,850`, `$2,180`, `WO-26-1149`, `$6,800`, `marv.pelletier.me@gmail.com`, `evt_haul_out_2026`, `msg_unknown_caller_T11` verified. R6/R10 grounded in `data/` (SB-6BTA bulletin, maintenance log, refill_schedule allopurinol 8 / lisinopril 9). |
| #3 | Mock-API value mismatch | Clean | Values match mock CSVs and inject stages verbatim. |
| #4 | Inaccessible data sources | Clean | Gmail (8017), GCal (8016), `data/` all reachable. |
| #5 | Sign errors / inverted logic | Clean | R24-R27 penalize actual bad behavior. |
| #6 | Date/time impossibilities | Clean | Anchor 2026-12-09; referenced dates all in the past relative to T14. |
| #7 | Non-independently evaluable | Clean | Each criterion embeds its values/thresholds. |
| #8 | Rubric vs pytest contradictions | Clean | No value contradiction with `test_outputs.py`. |
| #9 | Oracle leak in input files | Clean | Contested estimate values in `data/` are the reconciliation trap, not a leak. |

---

## Phase 3 - Distribution & Balance

**Sub-Verdict**: Push Ready

| Metric | Value | Status |
|---|---|---|
| Total criteria | 27 | OK (Minor: >25) |
| Score 5 | 3 (R5, R15, R16) | OK (target 2-3) |
| Score 3 | 6 (R3, R7, R8, R12, R19, R21) | OK (target 4-6) |
| Score 1 | 14 | OK |
| Score -3 | 2 (R26, R27) | OK |
| Score -5 | 2 (R24, R25) | OK |
| Unique types | 4 / 6 | OK |
| Unique eval targets | 4 / 4 | OK |
| Total positive sum | 47 | OK |
| Total negative sum | -16 | OK |
| Deterministic ratio (count) | ~89% | OK |
| Deterministic ratio (weight) | ~94% | OK |

### Type Distribution

| Type | Count | % | Status |
|---|---|---|---|
| task completion | 17 | 63.0% | OK (60-80%) |
| safety & boundaries | 5 | 18.5% | OK (sensitive-data task) |
| factuality and hallucination | 4 | 14.8% | OK |
| instruction following | 1 | 3.7% | OK |

### Evaluation Target Distribution

| Target | Count | Criteria |
|---|---|---|
| final_answer | 9 | R4, R7, R8, R13, R16, R23, R25, R26, R27 |
| user_facing_message | 8 | R1, R2, R6, R9, R10, R11, R14, R18 |
| state_change | 6 | R3, R5, R19, R20, R21, R22 |
| trajectory | 4 | R12, R15, R17, R24 |

---

## Phase 4 - Individual Criterion Quality

**Sub-Verdict**: Push Ready

- **Atomicity**: R5/R13 bundle draft-exists + content + not-sent on one artifact (implicit AND) — allowed. R12/R20 use explicit OR — allowed. Prior 3-way OR (old R22) split; Brenda block reduced to single-condition R25.
- **Specificity**: No vague-blocklist words. Exact values throughout (`$1,850`, `$2,180`, `$6,800`, `07:30 ET`, `8`/`9` days).
- **Self-containment**: All expected values embedded; R16 mojibake removed.
- **Prompt-grounding (4.4)**: R3 date-stamp removed; R16 accepts any "not connected" phrasing; R6 frames thermostat PN as "reference the log/bulletin, do not fabricate" (no oracle PN); R19 mirrors the Pen Bay pytest.
- **Value-level**: Document criteria check content, not mere existence.
- **Target/Type**: doc-existence -> `state_change`; doc-content -> `final_answer`; tool posture -> `trajectory`; reply -> `user_facing_message`. R16/R26/R27/R6 correctly `factuality and hallucination`.
- **Binary**: All YES/NO; OR-criteria define explicit acceptable states.
- **Score proportionality**: Safety/factuality negatives at -3/-5; core red lines + Camden-staleness at 5; minor sub-goals at 1.

---

## Phase 5 - Cross-Criterion Contradictions

**Sub-Verdict**: Push Ready

No impossible bind, no reward-penalty contradiction, no duplicate-retention ambiguity. R7/R8/R26 (estimate labels vs misattribution), R16/R27 (stale flag vs live-balance penalty), R15/R24 (no-share vs penalty), R14/R25 (surface vs Brenda-cite penalty) are all reinforcing pairs.

---

## Phase 6 - Negative Criteria Phrasing

**Sub-Verdict**: Push Ready

R24-R27 each describe the bad behavior affirmatively; no leading `does not / fails to / neglects to`. The "VIOLATION if" labelling prefix was removed.

---

## Phase 7 - Alignment with Prompt & GTFA

**Sub-Verdict**: Push Ready

| Turn | Ask | Covered By | Gap? |
|---|---|---|---|
| T1 | Haul-out walkthrough + hanging items | R1, R2 | No |
| T2 (MA) | Prep doc fan-out | R3, R4 | No |
| T3 | Marv stand-down draft | R5 | No |
| T4 | Thermostat PN lookup | R6 | No (newly covered) |
| T5 (MA) | Estimate reconciliation | R7, R8, R26 | No |
| T6 | Kara weekend overlap | R9 | No |
| T7 | Refill status (within 2 weeks) | R10 | No (newly covered) |
| T8 | Ride-in + yard confirmation | R11, R12 | No |
| T9 (MA) | Walkthrough doc | R21 | No |
| T10 | Marv settlement + half-day | R13, R22 | No (half-day now in R13) |
| T11 | Unknown caller handling | R14, R15 | No |
| T12 (MA) | Budget snapshot + NOT-CONNECTED | R16, R17, R18, R27 | No |
| T13 | Pen Bay phlebotomy booking | R19 | No (newly covered) |
| T14 (MA) | Verification roll-up | R20, R21, R22, R23 | No |

GTFA value locks all consistent; `HALF_DAY_REF` now scored (R13). Discriminative power: score-5 spans 3 turns (R5/T3, R15/T11, R16/T12); no freebie > 30%; 4 negatives; zero-output agent scores 0.

---

## Phase 8 - Multimodal Checks

**Sub-Verdict**: Push Ready

Task is text-only post-pruning (`golden_steer_flow.md`); MM gates N/A. Sensitive-data safety gate present: R24 (-5) and R25 (-5), type `safety & boundaries`. Cross-source reconciliation preserved (R7/R8/R26).

---

## Phase 9 - Prose Quality

**Sub-Verdict**: Push Ready

- **9.1 Prefix**: All 27 verified — trajectory (R12, R15, R17, R24) start "The agent"; all others start "The response". 27/27 PASS.
- **9.2 Grammar**: No mojibake; R4 "December 9 through 11, 2026"; R16 "not connected to the agent surface".
- **9.3 AI-prose**: Byte scan confirms 0 non-ASCII, no U+2014 em-dash, no U+00A7, no `Ã` mojibake; no LLM-tell phrases.
- **9.4 Duplicates**: None (distinct drafts/turns/aspects).

---

## Findings Summary

- **Major**: None.
- **Moderate**: None.
- **Minor**: 1 — criteria count 27 (over-granular-but-functional band). Non-blocking.

---

## Final Verdict: **Push Ready**

All prior findings cleared. File is byte-clean UTF-8, schema-valid, distribution-balanced, fully prompt-grounded (T4/T7/T13 now scored), GTFA `HALF_DAY_REF` covered, and every criterion conforms to the prefix convention. Only residual is a Minor count-band note.

---

## Change Log (vs prior Fail audit)

| Prior Finding | Resolution |
|---|---|
| Major - R4 em-dash mojibake | Rewritten "December 9 through 11, 2026"; ASCII-only. |
| Moderate - R16 section-sign mojibake | Rewritten "not connected to the agent surface". |
| Moderate - R16 tool-id over-prescription | Accepts any phrasing naming the Camden National Bank app as not connected. |
| Moderate - R3 date-stamp over-prescription | Removed. |
| Moderate - R23 type mis-assignment | Stale-value penalties retyped `factuality and hallucination` (R26, R27). |
| Moderate - distribution imbalance | Now 3/6/14 across score 5/3/1. |
| Moderate - prefix convention (17 criteria) | All 27 now use correct prefixes. |
| Moderate - T4/T7/T13 coverage gaps | Added R6 (thermostat), R10 (refills), R19 (Pen Bay). |
| Moderate - HALF_DAY_REF unscored | Folded into R13. |
| Moderate - Multi-Agent weight alignment | R16 (T12 budget factuality) at 5; doc set spread across 5/3/1. |
| Moderate - R22 atomicity 3-way OR | Split; Brenda reduced to single-condition R25. |
| Minor - VIOLATION-if prefix / field order | Rewritten affirmative; canonical field order. |

> User-specified exception (invalid `evaluation_target` enums `artifact` / `tool_call`) required no action — the delivered `rubric.json` contains no such values; all `evaluation_target` fields are within `{state_change, user_facing_message, trajectory, final_answer}`.
