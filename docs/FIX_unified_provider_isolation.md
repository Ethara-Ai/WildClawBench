# Unified Fix: Provider Isolation + Sub-Agent Model Inheritance

**Resolves**: Issue #2 (OAuth/Bedrock entanglement) + Issue #3 (sub-agent failures)
**Constraints**:
1. Sub-agents MUST NOT have a separate model — they inherit whatever model the main agent runs
2. OAuth and Bedrock paths remain as-is (Claude Code OAuth bridge, Bedrock ARN routing) — the fix is ISOLATION, not removal
3. Existing CLI commands (`script/run.sh`, `eval/run_batch.py`, `eval/wcb.py`) MUST NOT change their interface or behavior

---

## Design Principle

**"One provider, one model, everywhere."**

The main agent's resolved provider (`--auth-provider` or inferred) and model (`--model`) become the SINGLE source of truth for ALL LLM calls in the pipeline:
- Trajectory generation (agent LLM)
- Test generation (Channel A)
- Judge calls (Channel B)
- Sub-agents (explore, librarian, oracle, etc.)

No component independently decides its auth path. No component reads credentials for the OTHER provider.

---

## Part 1: Sub-Agent Model Inheritance (Issue #3)

### Problem
Sub-agents have hardcoded `anthropic/claude-opus-4-7` in config, while main session uses `kiro/claude-opus-5`.

### Fix: Remove all per-agent model overrides

Sub-agents should inherit from the top-level `model` field (which is what the main session uses). When no `agent.X.model` is specified, opencode uses the top-level `model` for that agent.

#### `~/.config/opencode/config.json`

```json
{
  "model": "kiro/claude-opus-5",
  "small_model": "kiro/claude-opus-4.7",
  "agent": {}
}
```

Remove the entire `agent` block contents. By omitting per-agent models, every sub-agent inherits `model` (top-level) — the same model the main session runs on.

#### `~/.config/opencode/oh-my-openagent.json`

```json
{
  "agents": {}
}
```

Remove all per-agent model overrides. The oh-my-openagent plugin will use whatever model the session is running on.

### Why This Works

- Main session runs on `kiro/claude-opus-5` → all sub-agents run on `kiro/claude-opus-5`
- If user switches main model (e.g. via CLI flag) → sub-agents follow automatically
- No per-agent config drift possible
- Same auth path for everyone — if main works, sub-agents work

---

## Part 2: Provider Path Isolation (Issue #2)

All changes below preserve existing command interfaces. The fix is INTERNAL gating — same flags, same args, different internal behavior based on the resolved provider.

### Change 1: Gate judge OAuth bridge on resolved provider

**File**: `src/utils/judge_litellm.py:63-68`

```python
# BEFORE
def _judge_oauth_bridge_url() -> str:
    return os.environ.get("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "").strip()

# AFTER
def _judge_oauth_bridge_url() -> str:
    from src.utils.auth_provider import OAUTH, PROVIDER_ENV_VAR
    if os.environ.get(PROVIDER_ENV_VAR, "").strip().lower() != OAUTH:
        return ""
    return os.environ.get("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "").strip()
```

**Effect**: On a Bedrock run, the Sonnet judge stays on Bedrock. On an OAuth run, it uses the bridge. No cross-contamination. Commands unchanged.

### Change 2: Gate test generation auto-enable on active provider

**File**: `eval/run_batch.py:3879-3881`

```python
# BEFORE
gen_tests = args.generate_tests
if gen_tests is None:
    gen_tests = bool(config.bedrock_inference_arn and config.aws_bearer_token)

# AFTER
gen_tests = args.generate_tests
if gen_tests is None:
    gen_tests = bool(
        (config.bedrock_inference_arn and config.aws_bearer_token)
        or use_oauth  # OAuth sidecar routes through bridge; testgen works
    )
```

**Effect**: OAuth runs auto-enable test generation (opus calls route through bridge via sidecar). Explicit `--generate-tests` / `--no-generate-tests` flags still override. Commands unchanged.

### Change 3: Zero cross-provider credentials in sidecar container

**File**: `eval/run_batch.py:3230-3249`

```python
# BEFORE
start_litellm(
    ...
    aws_bearer_token=config.aws_bearer_token,
    aws_region=config.bedrock_region,
    ...
)

# AFTER
start_litellm(
    ...
    aws_bearer_token="" if use_oauth else config.aws_bearer_token,
    aws_region="" if use_oauth else config.bedrock_region,
    ...
)
```

**Effect**: OAuth sidecar has no Bedrock credentials. Bedrock sidecar unchanged. No latent cross-routing possible inside the container.

### Change 4: Add OAuth branch to upstream probe

**File**: `eval/run_batch.py:3257-3266`

```python
# BEFORE
probe_model = (
    codex_model if use_codex_oauth
    else "claude-opus-4.7" if (config.aws_bearer_token and config.bedrock_inference_arn) or config.anthropic_api_key
    else "gpt-5.5" if config.openai_api_key
    else config.meta_model if (config.meta_api_key and config.meta_model)
    else ""
)

# AFTER
probe_model = (
    codex_model if use_codex_oauth
    else "claude-opus-4.7" if use_oauth
    else "claude-opus-4.7" if (config.aws_bearer_token and config.bedrock_inference_arn) or config.anthropic_api_key
    else "gpt-5.5" if config.openai_api_key
    else config.meta_model if (config.meta_api_key and config.meta_model)
    else ""
)
```

**Effect**: OAuth probe validates claude-opus-4.7 → bridge path. Bedrock probe validates claude-opus-4.7 → Bedrock path. Same model name, different upstream. Commands unchanged.

### Change 5: Gate preflight_judge_oauth on provider

**File**: `eval/run_batch.py` (around line 3065)

The existing preflight block should only fire when provider is OAuth:

```python
# BEFORE (fires whenever bridge URL is set)
from src.utils.judge_litellm import preflight_judge_oauth
_ok, _detail = preflight_judge_oauth()

# AFTER (only fires on OAuth provider)
if use_oauth:
    from src.utils.judge_litellm import preflight_judge_oauth
    _ok, _detail = preflight_judge_oauth()
    if not _ok:
        raise RuntimeError(...)
```

**Effect**: Bedrock runs don't depend on OAuth bridge health. OAuth runs still get the preflight. Commands unchanged.

### Change 6: Provider-aware run.sh credential check

**File**: `script/run.sh:355-363`

```bash
# BEFORE
for key in KENSEI_AWS_BEARER_TOKEN KENSEI_AWS_REGION; do
    if ! grep -qE "^${key}=.+" .env; then
        missing+=("$key")
    fi
done

# AFTER
if [[ "${WCB_AUTH_PROVIDER:-bedrock}" != "oauth" ]]; then
    for key in KENSEI_AWS_BEARER_TOKEN KENSEI_AWS_REGION; do
        if ! grep -qE "^${key}=.+" .env; then
            missing+=("$key")
        fi
    done
fi
```

**Effect**: OAuth runs don't warn about missing Bedrock creds. Bedrock runs still validate. `script/run.sh` interface completely unchanged — same flags, same positional args.

### Change 7: Warn on judge council degradation

**File**: `src/utils/grading.py` (after line 328)

```python
filtered = [m for m in out if m.family in allowed]

# ADD
if out and filtered and len(filtered) < len(out):
    import logging
    logging.getLogger(__name__).warning(
        "Judge council: %d→%d members under provider %r (dropped: %s)",
        len(out), len(filtered), provider,
        sorted({m.family for m in out} - {m.family for m in filtered}),
    )
```

**Effect**: User sees explicit log when council shrinks. No behavior change. Same grading semantics.

---

## What Does NOT Change

| Concern | Status |
|---------|--------|
| `script/run.sh` CLI interface | Unchanged — same positional args, same flags |
| `eval/run_batch.py` CLI interface | Unchanged — same `--auth-provider`, `--model`, `--generate-tests` |
| `--use-claude-oauth` flag behavior | Unchanged — still selects OAuth provider |
| OAuth bridge (cc-bridge) architecture | Unchanged — still routes through `wcbsh-cc-bridge-*` |
| Bedrock ARN routing | Unchanged — still uses `bedrock/anthropic.claude-opus-4-6-v1` + `model_id` |
| `eval/wcb.py` TUI | Unchanged |
| `eval/bootstrap_sidecar.py` stdout contract | Unchanged — still emits `key=value` lines |
| Judge council 3-member composition on Bedrock | Unchanged |
| `usage.jsonl` / `usage_oauth.jsonl` / headroom JSONL separation | Unchanged |

---

## Execution Order

1. **Part 1** (config-only, immediate): Remove per-agent model overrides from both JSON files. Sub-agents inherit main model. Zero risk — revert = re-add the entries.

2. **Part 2 Changes 1-7** (code): Apply in order. Each is independently safe.

3. **Validate**:
   ```bash
   # Smoke gate
   pytest tests/test_drift_plane_smoke.py -q

   # Auth provider tests
   pytest tests/test_auth_provider.py -q

   # Judge tests
   pytest tests/test_judge_litellm.py -q

   # Sidecar config tests
   pytest tests/test_litellm_sidecar_config.py -q

   # Full suite
   pytest tests/ -q
   ```

4. **E2E validation** (both paths):
   ```bash
   # Pure Bedrock
   WCB_AUTH_PROVIDER=bedrock bash script/run.sh input/alden-croft_MB claude-opus-4.7 1

   # Pure OAuth
   WCB_AUTH_PROVIDER=oauth bash script/run.sh input/alden-croft_MB claude-opus-4.7 1
   ```

---

## Summary Table

| LLM Call Site | Bedrock Run | OAuth Run | Gate Mechanism |
|---------------|-------------|-----------|----------------|
| Agent trajectory | Bedrock ARN via sidecar | OAuth bridge via sidecar | `litellm_sidecar.py` if/elif chain (existing) |
| Test generation | Bedrock ARN via sidecar | OAuth bridge via sidecar | **Change 2** (auto-enable gate) |
| Judge Sonnet | Bedrock direct (`_call_judge_bedrock`) | OAuth bridge (`_judge_oauth_bridge_url`) | **Change 1** (provider-gate bridge URL) |
| Judge GLM/Kimi | Bedrock direct | N/A (filtered out) | `auth_provider.available_judge_families` (existing) |
| Upstream probe | Bedrock `claude-opus-4.7` | OAuth bridge `claude-opus-4.7` | **Change 4** (OAuth branch) |
| Sub-agents (opencode) | Same model as main | Same model as main | **Part 1** (inherit, no override) |
| Preflight | Skipped | `preflight_judge_oauth()` | **Change 5** (provider-gate) |
