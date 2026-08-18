# Issue #3: Sub-Agents Failing with "OAuth authentication is currently not allowed"

**Severity**: Critical — all sub-agent delegation (explore, librarian, oracle, etc.) is broken
**Observed error**: `OAuth authentication is currently not allowed for this organization`
**Affected**: All `task()` calls with `run_in_background=true` or `false` — explore, librarian, oracle, plan, metis, momus agents

---

## Root Cause Analysis

### The Problem

The main session runs on `kiro/claude-opus-5` (routed through the Kiro provider plugin), but ALL sub-agents are configured to use `anthropic/claude-opus-4-7` (which routes through Anthropic's OAuth). The Anthropic OAuth path is failing because the organization/account does not have OAuth authentication enabled.

### Evidence Chain

1. **Main session model**: `kiro/claude-opus-5` — works because it routes through the `kiro-acp-ai-provider` (Kiro's own infrastructure)
2. **Sub-agent models**: ALL set to `anthropic/claude-opus-4-7` in TWO configuration locations:
   - `~/.config/opencode/config.json` → `agent.explore.model`, `agent.librarian.model`, `agent.oracle.model`, etc.
   - `~/.config/opencode/oh-my-openagent.json` → `agents.explore.model`, `agents.librarian.model`, etc.
3. **Plugin state**: `opencode-anthropic-oauth` is listed in `config.json:plugin[]` but is NOT installed in `node_modules/`
4. **Observed failure**: All 5 explore agents in the Issue #2 investigation returned: `OAuth authentication is currently not allowed for this organization`

### The Routing Mismatch

```
Main Session:  kiro/claude-opus-5 → Kiro provider (kiro-acp-ai-provider) → WORKS
Sub-Agents:    anthropic/claude-opus-4-7 → Anthropic provider (opencode-anthropic-oauth) → FAILS
```

The `anthropic/` prefix model ID tells opencode to route through the Anthropic provider. Without a working API key or OAuth token, this fails. The `kiro/` prefix routes through Kiro's own provider plugin which handles auth differently (presumably via the kiro-cli session auth).

---

## Configuration State

### `~/.config/opencode/config.json`

```json
{
  "plugin": [
    "opencode-antigravity-auth@latest",
    "@tarquinen/opencode-dcp@latest",
    "oh-my-openagent@latest",
    "opencode-anthropic-oauth",  // ← Listed but NOT installed
    "opencode-kiro"               // ← Working provider for main session
  ],
  "model": "anthropic/claude-opus-4-7",      // ← Default model uses Anthropic
  "small_model": "anthropic/claude-opus-4-7",
  "agent": {
    "build":     { "model": "anthropic/claude-opus-4-7" },  // ALL sub-agents
    "oracle":    { "model": "anthropic/claude-opus-4-7" },  // use Anthropic
    "explore":   { "model": "anthropic/claude-opus-4-7" },  // prefix →
    "librarian": { "model": "anthropic/claude-opus-4-7" },  // OAuth failure
    // ... all others same
  }
}
```

### `~/.config/opencode/oh-my-openagent.json`

Same pattern — all agents configured as `anthropic/claude-opus-4-7`.

### `~/.config/opencode/package.json`

```json
{
  "dependencies": {
    "@opencode-ai/plugin": "1.3.13"
    // opencode-anthropic-oauth NOT listed here
    // oh-my-openagent NOT listed here
    // These plugins are referenced but not properly npm-installed
  }
}
```

---

## Why Only Main Session Works

The system prompt reveals: `You are powered by the model named claude-opus-5. The exact model ID is kiro/claude-opus-5`. This means opencode's session startup overrides the `config.json` model with `kiro/claude-opus-5` (likely via CLI flag, kiro-cli integration, or the `opencode-kiro` plugin selecting its own model). But this override does NOT propagate to sub-agent spawning, which reads the `agent.*` configuration.

---

## Fix

### Option A: Change all sub-agent models to use the Kiro provider (Recommended)

In `~/.config/opencode/config.json`, change ALL agent model references from `anthropic/claude-opus-4-7` to `kiro/claude-opus-4.7` (or `kiro/auto`):

```json
{
  "model": "kiro/claude-opus-5",
  "small_model": "kiro/claude-sonnet-4.6",
  "agent": {
    "build":     { "model": "kiro/claude-opus-4.7" },
    "oracle":    { "model": "kiro/claude-opus-4.7" },
    "explore":   { "model": "kiro/claude-opus-4.7" },
    "librarian": { "model": "kiro/claude-opus-4.7" },
    "general":   { "model": "kiro/claude-opus-4.7" },
    "plan":      { "model": "kiro/claude-opus-4.7" },
    "pipeline-planner": { "model": "kiro/claude-opus-4.7" },
    "pipeline-builder": { "model": "kiro/claude-opus-4.7" },
    "pipeline-reviewer": { "model": "kiro/claude-opus-4.7" },
    "pipeline-tester": { "model": "kiro/claude-opus-4.7" }
  }
}
```

AND in `~/.config/opencode/oh-my-openagent.json`, change all entries similarly.

### Option B: Fix the Anthropic OAuth authentication

Install and configure `opencode-anthropic-oauth` properly with a valid API key or OAuth token. This requires:
1. Actually npm-installing the plugin: `cd ~/.config/opencode && npm install opencode-anthropic-oauth`
2. Setting `ANTHROPIC_API_KEY` in the environment, OR configuring the OAuth flow

### Option C: Use Amazon Bedrock provider for sub-agents

Change sub-agent models to use the `amazon-bedrock` provider already configured:

```json
"agent": {
  "explore": { "model": "amazon-bedrock/Ethara-Claude-Opus-4-6" }
}
```

---

## Recommendation

**Option A is the correct fix.** The main session already works through Kiro — sub-agents should use the same provider. This ensures consistent auth, no additional credentials needed, and matches the user's actual paid subscription.

The oh-my-openagent.json also needs updating to match, as it may override the global config.

---

## Additional Observations

1. **All three Bedrock models point to the same ARN** (`tkx60pqgep4l`) — Opus-4-6, Sonnet-4-6, and Haiku-4-5 are all the same inference profile. This is likely misconfigured but unrelated to this issue.
2. **Plugin installation is broken** — `package.json` only lists `@opencode-ai/plugin` as a dependency, not the actual plugins referenced in `config.json:plugin[]`. The plugins are loaded by name at runtime but their npm packages aren't resolved.
3. **No ANTHROPIC_API_KEY in environment** — there's no direct Anthropic API key set, confirming the `anthropic/` prefix model has no working auth path.
