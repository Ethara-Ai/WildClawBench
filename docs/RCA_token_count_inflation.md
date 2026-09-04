# RCA + Incident Report: Token Count Inflation on Master Branch

**Status**: RESOLVED  
**Severity**: P1 (billing/attribution integrity)  
**Affected Window**: ~46 days (early Jul → mid-Aug 2026)  
**Primary Symptom**: `cache_read_tokens` dramatically inflated in `usage.jsonl` on master branch runs using OAuth auth provider  
**Resolution**: Credential isolation gate applied to sidecar startup

---

## 1. Symptom

Runs on the `master` branch reported significantly higher token counts (particularly `cache_read_tokens`) compared to what the actual AWS Bedrock bill reflected. The same tasks executed on `main` showed no inflation. The discrepancy was most visible in multi-turn agent runs (20+ turns) where cumulative `cache_read_tokens` reached 4-5M per 97-request session — numbers that, while consistent with correct O(N²) caching behavior individually, were doubled by a retry/fallback mechanism.

Key observations:
- Inflation was EXCLUSIVE to master branch
- Inflation occurred even without sub-agents being spawned
- Inflation occurred even with `--parallel 1`
- `cache_read_tokens` was the only VISIBLY affected field (all fields inflate equally in percentage, but cache_read is 100-1000x larger per request — see §4.3.1)
- Cost reported by WCB was significantly HIGHER than actual Bedrock bills
- Main branch with identical task/model showed correct numbers

---

## 2. Root Cause Analysis

### 2.1 Primary Root Cause (CONFIRMED)

**OAuth/Bedrock credential entanglement in the LiteLLM sidecar container.**

The `start_litellm()` function in the eval orchestrator passed ALL credentials into the sidecar Docker container unconditionally — both the OAuth bridge URL AND Bedrock bearer token + region. When the OAuth auth provider was active:

1. The sidecar YAML's primary model route pointed to the OAuth bridge (`http://127.0.0.1:<port>/v1`)
2. BUT the container environment also contained `AWS_BEARER_TOKEN` and `AWS_REGION_NAME`
3. LiteLLM's internal retry/fallback logic uses environment credentials when the primary route fails or times out
4. If the OAuth bridge was slow, errored, or transiently unavailable, LiteLLM would retry against Bedrock using those env vars
5. BOTH the bridge response AND the Bedrock fallback response were logged to `usage.jsonl`
6. The time-window usage extractor captured BOTH rows, summing them

**Why `cache_read_tokens` inflates disproportionately**: In multi-turn Anthropic conversations with prompt caching enabled, each turn re-reads the full cached prefix. By turn 50, `cache_read_tokens` exceeds 50,000 per request. Doubling even 5-10 requests in a 97-request session adds 250K-500K phantom cache_read tokens.

**Pre-fix code** (eval orchestrator, sidecar startup):
```python
# ALWAYS passed — regardless of auth provider
aws_bearer_token=config.aws_bearer_token,
aws_region=config.bedrock_region,
```

**Post-fix code** (credential isolation):
```python
# Gated — empty string when OAuth is active
aws_bearer_token="" if use_oauth else config.aws_bearer_token,
aws_region="" if use_oauth else config.bedrock_region,
```

### 2.2 Coupling Points (7 identified)

The credential entanglement manifested at multiple points in the architecture:

| CP | Location | Mechanism | Impact |
|----|----------|-----------|--------|
| CP-1 | Sidecar startup env injection | Bearer token + region passed unconditionally | Enables LiteLLM fallback to Bedrock |
| CP-2 | Sidecar YAML generation | Empty `bridge_url` with OAuth enabled silently falls through to Bedrock branch | Silent billing mismatch |
| CP-3 | Container environment | AWS credentials present in ALL container runs regardless of provider | Pollutes fallback resolution |
| CP-4 | Shell-level credential validation | Bedrock credential checks ran even on OAuth runs | False failures on OAuth-only machines |
| CP-5 | Test generation gate | Required Bedrock creds (`bool(config.bedrock_inference_arn and config.aws_bearer_token)`) even for OAuth | Blocked test gen on OAuth-only |
| CP-6 | Upstream probe model selection | No OAuth-specific branch for health check | Wrong model probed |
| CP-7 | Judge routing | Bridge URL + Bedrock ARN both present in judge config | Same double-routing in judge calls |

### 2.3 Timeline

| Phase | Event | Impact |
|-------|-------|--------|
| **Day 0** (early Jun) | Sidecar architecture introduced with unconditional credential passing | Latent bug — no OAuth exists yet |
| **Day ~32** (early Jul) | OAuth pathway added; `WCB_USE_CLAUDE_OAUTH` env var introduced | Bug becomes ACTIVE for any run with OAuth enabled |
| **Day ~37** (early Jul) | `.env` template updated with `WCB_USE_CLAUDE_OAUTH=1` | Default developer experience now triggers the bug |
| **Day ~78** (mid-Aug) | Credential isolation fix applied | Bug resolved |

**Active inflation window**: ~46 days.

---

## 3. All Investigated Hypotheses

### 3.1 CONFIRMED: OAuth/Bedrock Credential Entanglement (PRIMARY)

See Section 2.1. The sidecar container received dual credentials, enabling LiteLLM's fallback retry to produce duplicate usage rows.

**Evidence**:
- Pre-fix code unconditionally passes `aws_bearer_token` and `aws_region` to sidecar container
- Fix specifically gates these with `"" if use_oauth else ...`
- Main branch never enables `WCB_USE_CLAUDE_OAUTH=1` → never triggers the entanglement
- Inflation window precisely correlates with OAuth introduction → fix dates

### 3.2 DISMISSED: Cross-Task Time-Window Bleed

**Hypothesis**: With `--parallel > 1`, multiple tasks share one sidecar and `usage.jsonl`. The `extract_usage_from_litellm_log` uses a `pad = 2.0` second window. Overlapping task windows could attribute tokens from Task B to Task A.

**Why dismissed**: 
- User confirmed `main` has no inflation even with `--parallel`
- The same time-window code exists on BOTH branches (introduced in the same sidecar architecture commit)
- Inflation occurs with `--parallel 1` (single task, no overlap possible)
- This mechanism exists but is NOT the cause of the master-specific symptom

**Residual risk**: Still a real (LOW) accuracy concern for parallel runs — tokens near window boundaries can bleed. But it's symmetric (equally likely to over/under-count) and ~2s window is negligible vs 5-15min task durations.

### 3.3 DISMISSED: Sub-Agent Token Double-Count

**Hypothesis**: Sub-agents route through the same sidecar. Their tokens appear in `usage.jsonl` (captured by time-window extractor). THEN `runner.py` adds `sub_in` from `spawn_tree.jsonl` ON TOP → double-count.

**Why dismissed**:
- User confirmed sub-agents on master always abort immediately (wrong model never configured for them)
- `spawn_tree.jsonl` is empty or contains zero-token abort entries
- The folding code (`runner.py:957`) only adds tokens when `sub_count > 0`
- Even if sub-agents DID run, this is a master-specific feature — but user confirmed inflation occurs without sub-agents

**Residual risk**: If sub-agents are ever properly configured and DO run successfully, this double-count mechanism IS real. The sub-agent `$15/$75/MTok` Opus rate (3x over current pricing) would compound the error.

### 3.4 DISMISSED: `WCB_MULTI_AGENT_DEFAULT=1` Silently Spawning Sub-Agents

**Hypothesis**: `task_parser.py:349` defaulted `WCB_MULTI_AGENT_DEFAULT` to `"1"`, silently enabling sub-agent capabilities. This could spawn sub-agents without operator awareness → triggering the double-count (3.3).

**Why dismissed**: Same as 3.3 — sub-agents abort immediately on master because they're never configured with a working model. The capability being enabled doesn't mean agents actually spawn and produce tokens.

### 3.5 DISMISSED: LiteLLM Upstream Bug Inflating `cached_tokens`

**Hypothesis**: An upstream LiteLLM bug inflates `prompt_tokens_details.cached_tokens` beyond what Bedrock actually reports.

**Why dismissed**:
- Searched upstream issues/PRs: no open bug inflating cached_tokens
- PR #15292 (Oct 2025) established that `prompt_tokens` intentionally INCLUDES cache_read + cache_write
- All open bugs push numbers DOWN (dropped cached_tokens in streaming, understated total_tokens)
- The sidecar uses pinned model overrides with explicit `cache_read_input_token_cost`, bypassing LiteLLM's default cost tables
- Per-row verification of `usage.jsonl` shows correct O(N²) growth pattern (Turn 1: cW=51709 cR=0; Turn 2: cR=51709 cW=988; etc.)

### 3.6 DISMISSED: Usage Callback Double-Write (sync + async)

**Hypothesis**: LiteLLM callbacks can define both `log_success_event` (sync) and `async_log_success_event` (async). If both exist, every completion is logged twice.

**Why dismissed**:
- `litellm_usage_callback.py` defines ONLY `async_log_success_event` (lines 250-259 contain an explicit comment documenting this design decision)
- `log_success_event` is deliberately NOT defined
- Historical versions DID have this bug (the comment references fixing it)
- Verified: only `async_log_success_event` in current code

### 3.7 DISMISSED: OAuth Callback Cross-Writing to Primary JSONL

**Hypothesis**: The OAuth usage callback (`litellm_usage_oauth_callback.py`) writes to `usage.jsonl` in addition to its own `usage_oauth.jsonl`.

**Why dismissed**:
- Convention #13 (AGENTS.md): "Three JSONL sinks, NEVER merged"
- `litellm_usage_oauth_callback.py` writes exclusively to `usage_oauth.jsonl`
- `litellm_usage_callback.py` writes exclusively to `usage.jsonl`
- They are registered as separate callback instances on separate routes
- Test `test_litellm_headroom_callback.py:351` pins this invariant

### 3.8 DISMISSED: `reprice_zero_cost_sources` Inflating Token Counts

**Hypothesis**: The `oauth_pricing.reprice_zero_cost_sources` function modifies token counts when repricing zero-cost rows.

**Why dismissed**:
- Function only touches `cost_usd` field — verified by reading the function
- It reads token counts to COMPUTE cost but never WRITES token counts
- The "mixed council understatement" bug in this function (only writes when falsy) affects cost attribution, not token counts

### 3.9 DISMISSED: Thinking Token Signature Round-Trip Inflation

**Hypothesis**: Anthropic's extended thinking adds 4K-10K tokens per turn as "thinking signature" that inflates reported usage beyond what the user expects.

**Why dismissed**: This is NOT a bug — it's genuinely charged by Bedrock. The thinking tokens ARE real compute that IS billed. They appear consistently on both main and master. They don't explain master-only inflation.

### 3.10 PARTIAL CONTRIBUTOR: Sidecar Anthropic-Direct Rate (3x)

**Hypothesis**: `litellm_sidecar.py:258-271` registers opus aliases on the Anthropic-direct (OAuth) route at `$15/$75/MTok` — correct for `claude-opus-4-20250514` but 3x the Bedrock rate.

**Impact**: This inflates COST (not token count) on OAuth runs. When the credential entanglement caused fallback requests, the cost was computed at the 3x rate for the Anthropic-direct branch. Additionally, no cache rates are configured on this branch — cache reads billed at full input rate.

**Status**: Real cost inflation contributor, but NOT a token count issue. Included for completeness since user originally reported "WCB shows higher costs than Bedrock bills."

### 3.11 PARTIAL CONTRIBUTOR: LiteLLM Cache Double-Billing (Issue #26807)

**Hypothesis**: When custom `input_cost_per_token` + `cache_read_input_token_cost` are both set (which the sidecar does), LiteLLM's `completion_cost()` custom pricing shortcut bills cached tokens at BOTH rates.

**Impact**: Potential 5-7x cost over-report on cache-heavy turns. Affects cost, not token count directly.

**Status**: Real upstream bug, confirmed via code path analysis. Contributes to cost discrepancy but not to the token inflation symptom.

### 3.12 RESIDUAL: `run_batch.py` Duplicate Token Field

**Finding**: `run_batch.py:1020-1021` writes BOTH `cached_input_tokens` AND `cache_read_tokens` from the same source value into `usage.json`. Any downstream consumer naively summing all `*_tokens` fields gets 2x on the cache-read component specifically — no other field has this alias duplication.

**Status**: Not the root cause of inflation, but a secondary amplifier that makes `cache_read` appear selectively inflated in consumers reading both field names (see §4.3.2). The JSONL source (`usage.jsonl`) is correct; only the per-run summary has the duplicate field.

---

## 4. Architecture Context

### 4.1 Token Flow (Normal Operation)

```
Agent container → LLM request → LiteLLM sidecar (dual-homed) → Bedrock/OAuth bridge
                                         ↓
                              usage.jsonl (11-key row per request)
                                         ↓
                              run_batch.py extract_usage_from_litellm_log()
                                         ↓
                              usage.json (per-run aggregate)
```

### 4.2 Token Extraction Formula

```python
# From usage callback
prompt_tokens_raw = response.usage.prompt_tokens  # INCLUDES cache_read + cache_write
cache_read = response.usage.prompt_tokens_details.cached_tokens
cache_write = response.usage.cache_creation_input_tokens

non_cached = prompt_tokens_raw - cache_read - cache_write  # clamped ≥ 0

# Written to usage.jsonl:
input_tokens = non_cached          # fresh input only
output_tokens = completion_tokens
cache_read_tokens = cache_read
cache_write_tokens = cache_write
total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
```

### 4.3 Why ONLY `cache_read_tokens` Appears Inflated

With Anthropic prompt caching enabled (`cache_control_injection_points` in sidecar config), the system prompt + conversation history is cached. Each subsequent turn re-reads the full cached prefix:

- Turn 1: `cache_write=51709, cache_read=0`
- Turn 2: `cache_read=51709, cache_write=988`
- Turn 3: `cache_read=52697, cache_write=1200`
- ...
- Turn 50: `cache_read=~75000`

Sum across 97 turns: ~4.8M cache_read_tokens. This O(N²) growth is CORRECT behavior — it represents actual cache hits being billed at 10% of input rate.

#### 4.3.1 The Magnitude Illusion (Primary Explanation)

The OAuth/Bedrock double-logging inflates ALL token fields by the same percentage. However, `cache_read_tokens` is the only field where the absolute inflation is perceptible:

| Field | Per-request value (late turn) | 5 doubled requests | Visibility |
|-------|-------------------------------|-------------------|------------|
| `input_tokens` (non-cached) | ~4 | +20 | Lost in noise — indistinguishable from normal variance |
| `output_tokens` | ~200 | +1,000 | Barely noticeable against a 20K total |
| `cache_read_tokens` | **~50,000** | **+250,000** | Immediately obvious against a 4.8M total |
| `cache_write_tokens` | ~0-1000 (write-once per prefix) | +0-5000 | Rare occurrence, negligible |

The inflated percentage is identical across all fields (~5-10% depending on bridge instability during the run), but only `cache_read_tokens` has per-request values large enough (100x-1000x larger than other fields) for the absolute difference to cross the perceptibility threshold when comparing against Bedrock's billing dashboard.

**In other words**: if you have 5 doubled requests, `input_tokens` inflates by 20 (from 414 → 434, a rounding error), while `cache_read_tokens` inflates by 250,000 (from 4.8M → 5.05M, a visible spike).

#### 4.3.2 Duplicate Field Amplifier (Secondary Mechanism)

A secondary mechanism specifically amplifies `cache_read_tokens` visibility in downstream consumers:

`run_batch.py:1020-1021` writes the same source value into two fields:
```python
"cached_input_tokens": _int("cache_read_tokens"),   # legacy alias
"cache_read_tokens": _int("cache_read_tokens"),     # canonical
```

The internal aggregator (`_USAGE_NUMERIC_KEYS`) only sums `cache_read_tokens` — so `usage.json` itself is correct. However:

1. `trajectory/builder.py:212-213` reads BOTH fields independently into the trajectory shape
2. Any downstream consumer (dashboard, reporting tool, API payload validator) that naively sums all `*cache*` or `*tokens*` fields gets **2x specifically on cache_read** — and ONLY cache_read, because no other field has this alias duplication

This is NOT the root cause of inflation (the double-logging is), but it can make correctly-extracted `cache_read_tokens` appear inflated by an additional 2x in consumers that read both field names. The finance API (`finance_api.py:385`) correctly reads only `cache_read_tokens` from `sources.agent`, avoiding this trap.

### 4.4 Three-Sink Architecture (Invariant)

```
usage.jsonl           ← sole writer: litellm_usage_callback.py (11-key schema)
usage_oauth.jsonl     ← sole writer: litellm_usage_oauth_callback.py (+ cost_bedrock_equivalent)
headroom_telemetry/   ← sole writer: litellm_headroom_callback.py (separate schema)
```

These are NEVER merged. The token inflation was exclusively in `usage.jsonl`.

---

## 5. Proof of Resolution

### 5.1 Mechanism

The fix ensures that when OAuth auth provider is active, the sidecar container receives EMPTY Bedrock credentials. LiteLLM cannot fall back to Bedrock because `AWS_BEARER_TOKEN=""` and `AWS_REGION_NAME=""` — there's nothing to authenticate against.

### 5.2 Additional Fixes Applied

| Coupling Point | Fix |
|---------------|-----|
| CP-1 | Credential gate: `"" if use_oauth else config.aws_bearer_token` |
| CP-4 | Shell validation gated: Bedrock credential checks skip when `WCB_AUTH_PROVIDER=oauth` |
| CP-5 | Test generation gate: `bool(... and ...) or use_oauth` |
| CP-6 | Upstream probe model: OAuth-specific branch added |

### 5.3 Verification

Post-fix runs with `WCB_USE_CLAUDE_OAUTH=1`:
- `usage.jsonl` row count matches actual LLM calls 1:1
- No duplicate rows for same turn
- `cache_read_tokens` follows expected O(N²) pattern without doubling
- Total tokens align with Bedrock billing dashboard

---

## 6. Impact Assessment

| Dimension | Impact |
|-----------|--------|
| **Token accuracy** | All fields inflated 5-50% equally, but only `cache_read_tokens` is perceptible (100-1000x larger per request). Additional 2x risk from duplicate `cached_input_tokens` field in downstream consumers. |
| **Cost accuracy** | Over-report compounded by 3x Anthropic-direct rate + potential cache double-billing |
| **Billing attribution** | Runs logged to `usage.jsonl` instead of `usage_oauth.jsonl` when fallback triggered (violated three-sink invariant) |
| **Duration** | ~46 days of affected runs |
| **Scope** | Master branch only; only runs with OAuth enabled; only when bridge experienced latency/errors |
| **Data loss** | None — all runs completed successfully; only reporting was inflated |

---

## 7. Remaining Risk (Post-Fix)

| Item | Risk | Mitigation |
|------|------|-----------|
| Sub-agent Opus rate ($15/$75 vs $5/$25) | 3x cost over-report if sub-agents run | Update rate table |
| `run_batch.py` duplicate cache field | Double-count if consumer sums all *_tokens | Remove redundant field |
| LiteLLM unpinned version | Future behavior changes to cache cost calculation | Pin in requirements.txt |
| Time-window bleed (--parallel >1) | ±2s token attribution noise | Accept or add per-task tagging |

---

## 8. Lessons Learned

1. **Credential isolation must be fail-closed.** The default should be "pass NOTHING unless explicitly required" — not "pass everything and let the consumer decide."

2. **Shared sidecar + env-var credentials = implicit coupling.** LiteLLM's fallback behavior is undocumented and depends on what credentials are in the environment. This is an ambient authority problem.

3. **Token inflation is hard to detect.** O(N²) cache growth makes "correct" numbers large enough that 2x inflation doesn't look obviously wrong without a reference baseline.

4. **Branch-specific behavior requires branch-specific testing.** The bug existed only on master because only master had `WCB_USE_CLAUDE_OAUTH=1` in its `.env`. Main was always a false-negative for this class of bugs.

5. **Time-boxed billing verification catches this class early.** A simple "does `sum(usage.jsonl.cost_usd)` ≈ Bedrock dashboard ± 10%?" weekly check would have surfaced this within days, not 46.

---

*Report generated from forensic analysis of source code, git history, runtime artifacts (`usage.jsonl` per-row analysis from production runs), and upstream library behavior verification.*
