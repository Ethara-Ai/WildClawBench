# Token Inflation Analysis: OAuth/Bedrock Credential Entanglement

## Summary

WildClawBench reported **~3.75× inflated token counts** relative to actual AWS Bedrock billing. The root cause is credential entanglement: the LiteLLM sidecar container received both OAuth bridge configuration AND Bedrock credentials simultaneously, causing duplicate API calls (and duplicate `usage.jsonl` rows) when the OAuth bridge was slow or errored.

**Impact window**: July 8 – August 18, 2026 (~41 days)

**Symptom**: `usage.json` reports token counts significantly higher than Bedrock CloudWatch/billing console. Cost calculation is internally consistent (correct math applied to inflated tokens), making the issue non-obvious from WCB artifacts alone.

---

## Mechanism

### Normal Operation (Pre-OAuth)

```
Agent → LiteLLM Sidecar → Bedrock
                              ↓
                        usage.jsonl (1 row per call)
```

### Entangled State (Jul 8 – Aug 18)

```
Agent → LiteLLM Sidecar → OAuth Bridge → Anthropic (primary)
              ↓
              ├─── [Bridge slow/error] → LiteLLM internal retry → Bedrock (fallback)
              ↓
        usage.jsonl (2 rows for same logical call)
```

The sidecar's YAML correctly configured either OAuth OR Bedrock model blocks. However, the **container environment** unconditionally received `AWS_BEARER_TOKEN` and `AWS_REGION_NAME`, enabling LiteLLM's internal fallback mechanism to route failed OAuth attempts to Bedrock. Both the original attempt AND the fallback wrote rows to `usage.jsonl`. The time-window extractor (`grading.py:1842-1898`) summed all rows indiscriminately, producing inflated totals.

---

## Commit Timeline

| Date | Commit | Author | Description |
|------|--------|--------|-------------|
| **Jul 8, 2026** | `cdff015` | sachin (`sachin.gupta@ethara.ai`) | **THE CAUSE COMMIT** — "added oauth". Passed Bedrock credentials unconditionally into the sidecar container environment alongside OAuth bridge configuration. Created the coupling that enabled LiteLLM fallback. |
| **Jul 30, 2026** | `1d9bdb7` | Akshita Dixit (`akshita.dixit@ethara.ai`) | **INCOMPLETE FIX** — "explicit auth-provider isolation". Gated the primary credential variable (`aws_bearer_token="" if use_oauth else config.aws_bearer_token`) but did not cover all coupling points. Sub-agent spawns, test generation, and judge council paths still propagated credentials independently. |
| **Aug 11–12, 2026** | — | — | **Affected runs occurred** — 8 trajectory runs still exhibited full token inflation despite `1d9bdb7`. Reported cost ~$1,300 across 8 runs; actual Bedrock spend significantly lower. |
| **Aug 18, 2026** | `c49ac85` | akshatgharpure-kuberha | **THE REAL FIX** — "Bedrock and OAuth isolation w/ Sub-agents fix". Complete credential isolation across all code paths including sub-agent spawning and shared-sidecar scenarios. |
| **Aug 18, 2026** | `0e2e86e` | akshatgharpure-kuberha | "OAuth path gen_test/execute_test fix" — Extended isolation to Channel A (test generation and execution) paths which had their own credential propagation. |
| **Aug 19, 2026** | `c057b6b` | akshatgharpure-kuberha | "WildClawBench bulk fixes commit" — Final cleanup and consolidation. |

---

## Why `1d9bdb7` Was Incomplete

The Jul 30 fix addressed Coupling Point 1 (CP-1): the primary sidecar startup path. But the credential entanglement existed at multiple injection sites:

| Coupling Point | Fixed By | Location |
|----------------|----------|----------|
| CP-1: Primary sidecar env | `1d9bdb7` (Jul 30) | `litellm_sidecar.py` container env dict |
| CP-2: Sub-agent sidecar sharing | `c49ac85` (Aug 18) | `subagent_director.py` → shared sidecar reuse |
| CP-3: Test generation Bedrock calls | `0e2e86e` (Aug 18) | `testgen/bedrock.py` direct Converse API |
| CP-4: Judge council credential inheritance | `c49ac85` (Aug 18) | `judge_litellm.py` env propagation |

The Aug 11–12 runs triggered CP-2 and/or CP-4, producing the continued inflation.

---

## Evidence From Affected Runs

8 trajectory runs from Aug 11–12, 2026 (all openclaw backend, Opus model):

| Metric | Observation |
|--------|-------------|
| Cost/token ratio | **1.00×** (internally consistent — not a pricing bug) |
| Token inflation vs Bedrock | **~3.75×** (matches duplicate-row hypothesis) |
| Cache tokens present | Yes — large `cache_read_tokens` values (millions per run) |
| Sub-agent spawns | Present in some runs (compounds with CP-2) |

The 3.75× multiplier (rather than exactly 2×) is explained by:
- Not every call triggered fallback (bridge sometimes succeeded on first attempt)
- Cache-heavy subsequent turns had different duplication rates than initial turns
- Sub-agent paths had independent duplication via CP-2

---

## Verification Method

To confirm a run is affected:

1. **Count `usage.jsonl` rows** in the run's sidecar log directory
2. **Count unique request IDs** (if available) or **model echoes** — duplicated calls will show both `claude-opus-4-8` (OAuth) and `claude-opus-4-6` (Bedrock fallback) for the same logical turn
3. **Compare `usage.json` total tokens** against Bedrock CloudWatch `ModelInvocationLog` for the same time window

---

## Current Status

**RESOLVED** as of commit `c49ac85` (Aug 18, 2026). All runs after this date should show accurate token counts matching Bedrock billing.

Runs between Jul 8 and Aug 18 are permanently affected in their stored `usage.json` artifacts. Retroactive correction would require re-extracting from `usage.jsonl` with a deduplication filter on model echo or request timing.

---

## Related Issues

- `docs/RCA_token_count_inflation.md` — Full 347-line RCA document covering this and related token accounting issues
- LiteLLM Issue #26807 — Separate cache double-billing bug (affects cost calculation, not token counts)
- Sub-agent fold double-count (`runner.py:918-960`) — Separate issue where spawn_tree tokens are added on top of sidecar-captured tokens
