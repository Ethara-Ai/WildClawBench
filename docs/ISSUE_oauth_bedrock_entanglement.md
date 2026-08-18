# Issue #2: OAuth and Bedrock Provider Entanglement — Full Diagnosis

**Severity**: Design debt — functional but leaky isolation
**Status**: Partially addressed by `src/utils/auth_provider.py` (provider isolation module); residual coupling documented below

---

## Executive Summary

The design INTENT is correct: `auth_provider.py` explicitly states "the two providers are independent and there is NO fallback between them." However, the implementation has **7 structural coupling points** where one provider's presence/absence affects the other's behavior, making truly independent operation impossible without both sets of credentials configured in `.env`.

---

## Coupling Point Map

### CP-1: `.env` Always Loads Both Credential Sets

**Location**: `eval/run_batch.py` line 1 (`load_dotenv()` at import time)
**Problem**: `load_dotenv()` reads ALL credentials from `.env` into `os.environ` unconditionally. Even when `--auth-provider oauth` is selected, `KENSEI_AWS_BEARER_TOKEN`, `KENSEI_BEDROCK_MODEL_ARN`, `JUDGE_COUNCIL_SONNET_ARN`, `JUDGE_COUNCIL_GLM_ARN`, `JUDGE_COUNCIL_KIMI_ARN` are all loaded into environment.
**Symptom**: `grading.council_members()` reads council ARNs live from env. The filtering at line 326-328 (`auth_provider.resolve_provider()` → `available_judge_families(provider)` → filter) was added specifically to prevent the Kimi/GLM members from billing Bedrock on an OAuth run. But this filter EXISTS only because both credential sets are always present.
**Design debt**: The provider isolation is enforced at the APPLICATION layer (filtering), not at the CREDENTIAL layer (never loading them).

### CP-2: Sidecar YAML Builder Receives Both Sets of Credentials

**Location**: `eval/run_batch.py:2860-2879` and `eval/bootstrap_sidecar.py:185-202`
**Problem**: `build_litellm_config_yaml()` is called with BOTH Bedrock and OAuth params simultaneously:
```python
build_litellm_config_yaml(
    bedrock_sonnet_arn=config.bedrock_sonnet_arn if config.aws_bearer_token else "",
    bedrock_arn=config.bedrock_inference_arn if config.aws_bearer_token else "",
    aws_region=config.bedrock_region,
    ...
    use_claude_oauth=use_oauth,
    bridge_url=cc_bridge_url,
    ...
)
```
The `if config.aws_bearer_token else ""` guards zero the Bedrock ARN when the bearer token is missing, but the **conditional branch structure inside the function** means the OAuth path (`if use_claude_oauth and bridge_url`) must win over the Bedrock path (`elif bedrock_arn`). If for any reason `bridge_url` is empty while `use_claude_oauth=True`, it falls through to the Bedrock branch (test: `test_oauth_flag_without_bridge_url_falls_through_to_bedrock`).
**Symptom**: An OAuth run where the cc-bridge fails to start silently falls back to Bedrock routing instead of failing hard. The `auth_provider="bedrock"` guard at line 230 only catches the `anthropic_api_key` branch, not this fall-through.

### CP-3: Sidecar Docker Launch Passes AWS Credentials Even Under OAuth

**Location**: `eval/run_batch.py:3230-3249`
**Problem**: `start_litellm()` is called with `aws_bearer_token=config.aws_bearer_token` and `aws_region=config.bedrock_region` unconditionally, regardless of provider selection. The sidecar container therefore has AWS Bedrock credentials injected as env vars EVEN on an OAuth run.
**Why it matters**: If any LiteLLM internal logic or fallback path inside the sidecar container discovers these env vars, it could route to Bedrock unexpectedly. The sidecar YAML controls model routing, but the env vars are a latent attack surface.

### CP-4: `bedrock_sonnet_arn` Gate for Sonnet Judge Registration

**Location**: `src/utils/litellm_sidecar.py:278`
```python
if bedrock_sonnet_arn and auth_provider != "oauth":
```
**Problem**: The Sonnet judge model (`claude-sonnet-4-6`) is registered in the sidecar model list ONLY when `bedrock_sonnet_arn` is present AND provider is not OAuth. Under OAuth, the Sonnet judge does NOT use the sidecar at all — `judge_litellm.py:562-572` dials the cc-bridge directly via `api_base` override. But this means:
- OAuth judging works ONLY because of a BYPASS (direct bridge dial), not because of proper sidecar routing
- If the bypass fails, there's no Sonnet route in the sidecar to fall back to (by design — but the error message doesn't explain this)

### CP-5: Test Generation Gate Requires Bedrock Credentials

**Location**: `eval/run_batch.py:3881`
```python
gen_tests = bool(config.bedrock_inference_arn and config.aws_bearer_token)
```
**Problem**: When `--generate-tests` is not explicitly passed, auto-detection defaults to `True` only when Bedrock credentials are present. On a pure OAuth run (no Bedrock creds), test generation is silently disabled unless `--generate-tests` is explicitly passed.
**Symptom**: OAuth-only runs produce no Channel A (pytest) scoring unless the user remembers to pass `--generate-tests`. The testgen LLM call itself goes through the sidecar which would route to the OAuth bridge for opus — but the GATE never fires.

### CP-6: Upstream Probe Model Selection Prefers Bedrock

**Location**: `eval/run_batch.py:3257-3266` and `eval/bootstrap_sidecar.py:399-405`
```python
probe_model = (
    codex_model if use_codex_oauth
    else "claude-opus-4.7" if (config.aws_bearer_token and config.bedrock_inference_arn) or config.anthropic_api_key
    else "gpt-5.5" if config.openai_api_key
    else config.meta_model if (config.meta_api_key and config.meta_model)
    else ""
)
```
**Problem**: The `verify_litellm_upstream_reachable` probe checks `claude-opus-4.7` when EITHER Bedrock OR Anthropic-direct creds exist, with NO OAuth-specific branch. In `bootstrap_sidecar.py:399-405`, the OAuth case is first (`if use_oauth`) and does select `claude-opus-4.7` — which is correct because the sidecar routes that model name to the bridge under OAuth. But in `run_batch.py:3257-3266`, the OAuth case is NOT LISTED. It falls into the `(config.aws_bearer_token and config.bedrock_inference_arn)` branch because on an OAuth run, Bedrock creds are typically still present in env.
**Symptom**: The upstream probe on a pure-OAuth run (no Bedrock creds) would probe `gpt-5.5` or nothing, missing the claude-opus-4.7→bridge path entirely.

### CP-7: Judge Council Composition is Provider-Dependent But Council Enable is Not

**Location**: `src/utils/grading.py:277-341`
**Problem**: `council_enabled()` reads `JUDGE_COUNCIL` env var (line 278). When enabled, `council_members()` resolves the roster from `JUDGE_COUNCIL_{SONNET,GLM,KIMI}_ARN` env vars and then FILTERS by provider (line 326-328). Under OAuth, only `sonnet` is allowed — so a 3-judge council degrades to a single-judge grading path.
**Design issue**: The user enables `--judge-council` expecting 3 judges. Under OAuth, they silently get 1 judge with no warning in the output. The code at line 330-340 raises ONLY when ALL members are filtered (zero remain), not when the council shrinks from 3 to 1.

---

## The "Presence of One Required to Run the Other" Phenomenon

The user observes that "OAuth and Bedrock always intertwine where presence of one is required to run the other." This manifests from:

### When OAuth is enabled, Bedrock is still needed for:
1. **Test generation** (CP-5): defaults off without Bedrock creds
2. **Judge council composition** (CP-7): GLM/Kimi judges require Bedrock ARNs; without them, council collapses to single-Sonnet
3. **Sidecar env vars** (CP-3): AWS creds still injected into sidecar container
4. **`.env` validation** (run.sh line 356): `preflight_env_file()` warns on missing `KENSEI_AWS_BEARER_TOKEN` regardless of provider

### When Bedrock is enabled, OAuth is still needed for:
1. **Judge Sonnet route** (CP-4): When `KENSEI_JUDGE_OAUTH_BRIDGE_URL` is set in `.env`, the Sonnet judge routes through the OAuth bridge EVEN on a Bedrock trajectory run. This is the `_judge_oauth_bridge_url()` check at `judge_litellm.py:562-563` and `grading.py:244`.
2. **Evidence budget cap** (grading.py:244-245): When the OAuth bridge URL is set, `_member_evidence_budget()` applies the 200K-token OAuth ceiling to the Sonnet judge EVEN if the trajectory ran on Bedrock.
3. **Preflight check** (run_batch.py:3065): `preflight_judge_oauth()` runs when the OAuth bridge URL is detected, adding a startup dependency on OAuth health.

---

## Why Full Independence Doesn't Exist Today

The architecture has a **split-personality design**:

| Concern | Trajectory (agent LLM) | Judging (Channel B) | Test Generation (Channel A) |
|---------|----------------------|---------------------|---------------------------|
| OAuth run | OAuth bridge | OAuth bridge (Sonnet only) | Falls to Bedrock sidecar (!) |
| Bedrock run | Bedrock | Bedrock (3-judge council) | Bedrock |
| Mixed (current default) | Per `--auth-provider` | Sonnet→OAuth bridge; GLM/Kimi→Bedrock | Bedrock always |

The judge Sonnet member has its OWN routing independent of the trajectory provider. `KENSEI_JUDGE_OAUTH_BRIDGE_URL` being set in `.env` makes the Sonnet judge use OAuth regardless of `--auth-provider`. This is the single biggest entanglement: **the judge's transport is decoupled from the trajectory's transport**.

---

## Ideal "Pure" State (What Independence Would Look Like)

### Pure OAuth Run
- Trajectory: OAuth bridge → claude-opus (via cc-bridge)
- Judging: Sonnet-only via OAuth bridge (single-judge, NOT council)
- Test generation: OAuth bridge → opus for testgen LLM calls
- Upstream probe: `claude-opus-4.7` → OAuth bridge
- No Bedrock env vars loaded/needed
- No `JUDGE_COUNCIL_GLM_ARN`/`JUDGE_COUNCIL_KIMI_ARN` read

### Pure Bedrock Run
- Trajectory: Bedrock → claude-opus (via ARN)
- Judging: 3-judge council (Sonnet/GLM/Kimi all via Bedrock)
- Test generation: Bedrock → opus for testgen LLM calls
- Upstream probe: `claude-opus-4.7` → Bedrock
- No OAuth env vars loaded/needed
- No `KENSEI_JUDGE_OAUTH_BRIDGE_URL` consulted
- No `preflight_judge_oauth()` called

---

## Blocking Dependencies for Achieving Independence

1. **`KENSEI_JUDGE_OAUTH_BRIDGE_URL` must be provider-gated**: judge_litellm.py's `_judge_oauth_bridge_url()` reads the env var unconditionally. It should check `WCB_AUTH_PROVIDER == "oauth"` before returning the URL.

2. **Test generation gate needs OAuth awareness**: The `gen_tests = bool(config.bedrock_inference_arn and config.aws_bearer_token)` default must also enable when OAuth is active (the sidecar routes opus through the bridge, so testgen LLM calls would work).

3. **`start_litellm()` should not pass AWS creds under OAuth**: Zero `aws_bearer_token` when provider is OAuth.

4. **`preflight_judge_oauth()` should only run when provider is OAuth**: Currently it fires whenever `KENSEI_JUDGE_OAUTH_BRIDGE_URL` is set, regardless of selected provider.

5. **run.sh `preflight_env_file()` should be provider-aware**: Stop warning about `KENSEI_AWS_BEARER_TOKEN` on OAuth runs.

6. **Upstream probe in `run_batch.py:3257-3266` needs an OAuth branch**: Currently missing, falls through to Bedrock check.

7. **Council shrinkage should warn**: When a 3-judge council request degrades to 1-judge under OAuth, emit a visible log warning.

---

## Environment Variables That Create Cross-Provider Coupling

| Env Var | Read By | Effect Under Wrong Provider |
|---------|---------|---------------------------|
| `KENSEI_JUDGE_OAUTH_BRIDGE_URL` | judge_litellm.py | Makes Bedrock-run Sonnet judge use OAuth |
| `JUDGE_COUNCIL_GLM_ARN` | grading.py | Filtered out under OAuth, but presence triggers resolution logic |
| `JUDGE_COUNCIL_KIMI_ARN` | grading.py | Same |
| `KENSEI_AWS_BEARER_TOKEN` | run_batch.py, sidecar | Injected into sidecar even under OAuth |
| `KENSEI_BEDROCK_MODEL_ARN` | run_batch.py | Gates test generation even under OAuth |
| `WCB_CC_ACCOUNT_POOL` | bootstrap_sidecar.py | Needed for OAuth; presence triggers inference under Bedrock |
