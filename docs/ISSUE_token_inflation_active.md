# Issue Report: Token and Cost Inflation vs Bedrock Console

**Status**: Issue 1 RESOLVED. Issue 2 ACTIVE (no fix applied).
**Severity**: P1 (billing and attribution integrity)
**Reported symptom**: WCB reported ~$30K for a campaign the AWS Bedrock console billed at ~$8K. Ratio 3.75x. No sub-agents spawned, pure Bedrock path, `--parallel 1`.
**Related docs**: [`RCA_token_count_inflation.md`](RCA_token_count_inflation.md) (Issue 1 in depth, plus 10 dismissed hypotheses), [`ISSUE_oauth_bedrock_entanglement.md`](ISSUE_oauth_bedrock_entanglement.md) (the 7 coupling points).

Two independent defects produce the same user-visible symptom. They are not variants of each other, and closing one does not close the other. Read the split below before attributing any historical discrepancy.

| | Issue 1 | Issue 2 |
|---|---|---|
| What inflates | Token **counts** (and cost as a consequence) | **Cost only.** Token counts are correct. |
| Trigger | OAuth auth provider active | Any Bedrock opus or sonnet route |
| Status | RESOLVED (`1d9bdb7`) | **ACTIVE** |
| Reported case fit | Does not fit (user was on pure Bedrock, no OAuth) | Fits (3.75x is inside the predicted band) |

The reported $30K/$8K case is Issue 2. Issue 1 is documented here for completeness and because historical runs in the affected window carry both.

---

## Issue 1: OAuth/Bedrock Credential Entanglement

**Status**: RESOLVED
**Fix commit**: `1d9bdb7` "explicit auth-provider isolation (oauth|bedrock) + bridge/docker preflight hardening", authored 2026-07-30 by Akshita Dixit.
**Introduced**: early July 2026, when the OAuth pathway landed. The unconditional credential passing predates it, but was inert until an OAuth route existed to fall back *from*.
**Active window**: ~46 days, early Jul to mid-Aug 2026. Note the arithmetic: `1d9bdb7` is dated 2026-07-30, so the primary gate closed on that date. The RCA's window extends to mid-Aug because the remaining coupling points (CP-4 through CP-7) were closed in follow-up commits, and any run predating the full set could still mis-route.

### Mechanism

The sidecar container received **both** the OAuth bridge URL and the Bedrock bearer token plus region, unconditionally. LiteLLM's internal retry and fallback logic resolves credentials from the environment. When the OAuth bridge was slow or transiently erroring, LiteLLM retried the same logical request against Bedrock using those env vars. Both the bridge response and the Bedrock response were logged as separate rows in `usage.jsonl`. The time-window usage extractor captured both and summed them, so affected turns were counted twice.

Full mechanism, the seven coupling points, and per-row evidence: `RCA_token_count_inflation.md` §2.

### Fix

```python
# eval/run_batch.py, sidecar startup
aws_bearer_token="" if use_oauth else config.aws_bearer_token,
aws_region="" if use_oauth else config.bedrock_region,
```

Empty Bedrock credentials when OAuth is the selected provider. LiteLLM has nothing to authenticate a fallback with, so the duplicate row cannot be produced. `1d9bdb7` also added `validate_provider_auth()` so a rotated bearer token surfaces as an auth error rather than a silent transport swap.

### Symptom signature

`cache_read_tokens` inflated far beyond the Bedrock console. All fields inflate by the same percentage, but only `cache_read_tokens` has per-request values large enough (50K+ on late turns, 100x to 1000x every other field) for the absolute difference to be visible. Reported cost was correspondingly high. See RCA §4.3.1 for why the other fields hide in the noise.

### How to Verify

1. Confirm the run used OAuth: `WCB_USE_CLAUDE_OAUTH=1` in the run env, or `usage_oauth.jsonl` present next to `usage.jsonl` in the run dir.
2. Count rows in `usage.jsonl` for the run's time window and compare against actual LLM call count in `chat.jsonl`. A 1:1 match means clean. Excess rows are duplicates.
3. Look for adjacent rows with identical `cache_read_tokens` and near-identical timestamps. That pair is the bridge response plus the Bedrock fallback.
4. Confirm the gate is present in the checkout under test:
   ```bash
   git log --oneline -1 1d9bdb7
   grep -n 'if use_oauth else config.aws_bearer_token' eval/run_batch.py
   ```
   Absent grep result means the run predates the fix and its numbers are suspect.

---

## Issue 2: LiteLLM Cache Double-Billing (upstream #26807)

**Status**: ACTIVE. No fix applied. Present on `master` as of 2026-08-19.
**Introduced**: `a1d710e8a7a35b9547c95f101198c4ca728ae3b0` "add missing cache token pricing", authored Mon 2026-07-06 18:16 +0530 by sachin. The commit touched one file and added exactly four lines: the read and write cache costs on the Bedrock opus block and the same pair on the Bedrock sonnet block.
**Location**: `src/utils/litellm_sidecar.py:200-207` (opus), `298-303` (sonnet).

### Mechanism

The opus deployment block sets a custom `input_cost_per_token` **and** a custom `cache_read_input_token_cost`:

```python
"      input_cost_per_token: 0.000005\n"          # $5.00/MTok
"      output_cost_per_token: 0.000025\n"         # $25.00/MTok
"      cache_read_input_token_cost: 0.0000005\n"  # $0.50/MTok
"      cache_creation_input_token_cost: 0.00000625"
```

Under LiteLLM upstream bug #26807, when both fields are present on a deployment, `completion_cost()` takes the custom-pricing shortcut and bills cached read tokens **twice**: once at the input rate and once at the cache-read rate. Effective rate becomes $5.00 + $0.50 = **$5.50/MTok** against a correct $0.50/MTok. That is an **11x overcharge on the cache-read component**.

Sonnet has the identical shape: $3.00 + $0.30 = $3.30/MTok against a correct $0.30/MTok. Also 11x.

**Token counts are unaffected.** `usage.jsonl` token fields are correct on this path. Only `cost_usd` is wrong. Anyone triaging this as a token-count bug will find nothing.

### The irony

`a1d710e` was a fix attempt. Before it, the Bedrock blocks carried no cache fields at all, so LiteLLM billed every cached read at the full input rate: $5.00 against $0.50, a clean **10x** over-report. The commit added the correct cache rates intending to close that gap. Because of #26807 the new rate was added to the old one instead of replacing it, so the over-report went from 10x to **11x**. The fix moved the number 10% in the wrong direction.

### Impact

Opus agent runs are cache-dominated. With `cache_control_injection_points` active, each turn re-reads the full cached prefix, so `cache_read_tokens` typically lands at 80% or more of total tokens by mid-run.

Worked example, 1M total tokens, 90% cache read, output-light (typical long-horizon agent turn mix):

| Component | Tokens | Correct rate | Correct cost | Billed rate | Billed cost |
|---|---|---|---|---|---|
| cache read | 900,000 | $0.50/MTok | $0.45 | $5.50/MTok | $4.95 |
| cache write | 50,000 | $6.25/MTok | $0.31 | $6.25/MTok | $0.31 |
| fresh input | 10,000 | $5.00/MTok | $0.05 | $5.00/MTok | $0.05 |
| output | 40,000 | $25.00/MTok | $1.00 | $25.00/MTok | $1.00 |
| **total** | | | **$1.81** | | **$6.31** |

Inflation factor **3.48x** for that mix. The factor is sensitive to the output share, since output is billed correctly and dilutes the error: the band runs roughly **2.5x to 4.5x** across realistic mixes, tightening toward 4x as output share drops. The reported $30K vs $8K is exactly 3.75x, which sits inside that band. Issue 2 alone accounts for the reported discrepancy, which is consistent with the user's report of no sub-agents and no OAuth.

Sonnet judge traffic is inflated by the same 11x on its cache component, but judge calls are a small fraction of campaign spend, so the campaign-level contribution is minor.

### Fix Recommendation

Remove **all** custom cost fields from the Bedrock opus and sonnet deployment blocks. Delete these lines:

- `litellm_sidecar.py:200-207`: `input_cost_per_token`, `output_cost_per_token`, `cache_read_input_token_cost`, `cache_creation_input_token_cost`
- `litellm_sidecar.py:298-303`: same four fields on the sonnet block

LiteLLM then resolves pricing from its own model catalog, which carries correct rates for `us.anthropic.claude-opus-4-20250514-v1:0` including the cache tiers. This works because the blocks already set `model: bedrock/anthropic.claude-opus-4-6-v1` as the recognizable catalog name, with the real inference-profile ARN carried separately in `model_id`. Catalog resolution succeeds. That name split exists for adaptive-thinking detection (see the comment block at `litellm_sidecar.py:164-173`) and must not be collapsed while doing this.

Do not attempt a partial fix. Dropping only `cache_read_input_token_cost` restores the original 10x bug. Dropping only `input_cost_per_token` leaves LiteLLM without a base rate on a partially-custom deployment. It is the *coexistence* of custom base and custom cache rates that triggers #26807, so the whole custom-pricing set has to go.

Two guardrails to add with the fix:

1. Pin `litellm` in `requirements.txt`. It is currently in the loose top tier. Catalog rates and the `completion_cost()` code path both move between versions, and this class of bug is silent.
2. Add a post-run assertion that recomputed cost matches `sum(usage.jsonl.cost_usd)` within a tolerance. See the recipe below.

### How to Verify

The signature is exact and arithmetic, not statistical. The excess equals `cache_read_tokens * input_cost_per_token`.

1. Recompute expected cost from the token counts in `usage.jsonl`, per row:
   ```
   expected_usd = (input_tokens       * 0.000005
                 + output_tokens      * 0.000025
                 + cache_read_tokens  * 0.0000005
                 + cache_write_tokens * 0.00000625)
   ```
2. Compare against the row's logged `cost_usd`. If Issue 2 is active:
   ```
   cost_usd - expected_usd  ==  cache_read_tokens * 0.000005
   ```
   That identity holding to floating-point tolerance across rows is conclusive. It rules out both the 10x pre-`a1d710e` behavior (where `cost_usd` would instead equal expected plus `cache_read_tokens * 0.0000045`) and any token-count defect.
3. Cross-check against the AWS Bedrock console for the same window. Console total should match the recomputed `expected_usd` sum, not the logged sum.
4. Confirm the defect is still present in the checkout:
   ```bash
   grep -n 'cache_read_input_token_cost\|input_cost_per_token' src/utils/litellm_sidecar.py
   ```
   Both appearing inside the same deployment block means active. Expect hits at 200, 206 and 298, 302.
5. Inspect the rendered sidecar YAML in `work/` for the run to confirm both fields reached the live deployment config rather than being stripped somewhere downstream.

---

## Impact on Historical Runs

Three regimes. Determine which applies from the run's commit date and auth provider before trusting any reported cost.

| Window | Cache-read billing | Token counts | Notes |
|---|---|---|---|
| Before 2026-07-06 (`a1d710e`) | 10x over-report ($5.00 vs $0.50) | correct | No cache fields on Bedrock blocks. LiteLLM billed cache reads at full input rate. |
| 2026-07-06 onward | **11x over-report** ($5.50 vs $0.50) | correct | ACTIVE. #26807 double-bill. |
| early Jul to 2026-07-30 (`1d9bdb7`), **OAuth runs only** | 11x, applied to doubled counts | **inflated** | Both issues stack. Duplicate `usage.jsonl` rows also violate the three-sink invariant, since fallback traffic landed in `usage.jsonl` instead of `usage_oauth.jsonl`. |

Practical consequences:

- **Every** Bedrock opus or sonnet cost figure in `output/`, in delivered harbor bundles, and in `finance_usage.jsonl` is inflated by 2.5x to 4.5x. There is no clean window on the cost axis. Only the magnitude of the error changes across regimes.
- Token counts are trustworthy outside the OAuth window. Since the cost error is a pure function of the token counts, historical runs are **fully recoverable** by recomputing from `usage.jsonl` with the recipe above. No re-run needed.
- Relative model comparisons within a single regime survive, since the multiplier is a function of cache share and every opus run has a similar profile. Cross-regime comparisons and any absolute dollar figure do not.
- OAuth-window runs are not recoverable by arithmetic. Duplicate rows have to be de-duplicated against `chat.jsonl` call counts first, and the duplicate detection is heuristic. Treat those runs as approximate.
- `run_batch.py:1020-1021` still writes `cache_read_tokens` into two field names (`cached_input_tokens` and `cache_read_tokens`). Any recompute script that iterates all `*_tokens` keys will double the cache component a third time. Read `cache_read_tokens` only. See RCA §4.3.2.

---

## Priority

Issue 2 should be fixed before the next campaign. It is a four-line deletion with no behavioral risk to routing, thinking, or trajectory shape, and it currently makes every dollar figure the pipeline publishes wrong by roughly 4x. The recompute path recovers historical data, so the fix is not blocked on a backfill.

---

*Verified against source at `src/utils/litellm_sidecar.py:200-207` and `:298-303`, git history for `a1d710e8a7a35b9547c95f101198c4ca728ae3b0` and `1d9bdb71c3b854c3659386c7f8c315bcd0d099e6`, and the prior analysis in `RCA_token_count_inflation.md`. Cost figures are derived from the rates in the deployment blocks plus LiteLLM's documented custom-pricing path; the 3.48x worked example is arithmetic, not measured.*
