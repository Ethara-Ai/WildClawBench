# Claude Fable 5 on the OAuth Trajectory Path

Status (2026-07-21): **wired end-to-end, entitlement CONFIRMED** — live probe
(max_tokens=1 through the bridge's own transforms, Max-subscription OAuth
token) returned 200 from `api.anthropic.com` with `"model": "claude-fable-5"`;
the `claude-opus-4-8` control also 200'd on the same credential.

## How to run

```bash
bash script/run.sh input/<task> claude-fable-5 1     # with OAuth env enabled
```

Requires the usual OAuth setup (`WCB_USE_CLAUDE_OAUTH=1`, `WCB_CC_ACCOUNT_POOL`,
`WCB_CC_BRIDGE_SECRET`). `claude-fable-5` is only registered on the OAuth
branch — without `--use-claude-oauth` there is no Bedrock route for it and
LiteLLM will 400.

## What was wired (all guarded by the OAuth branch; opus behavior untouched)

| Change | Where | Why |
|---|---|---|
| `claude-fable-5` model block → bridge | `src/utils/litellm_sidecar.py` (OAuth branch) | Routes fable through the same cc-bridge; zero-cost params (subscription billing); **no `thinking` directive** in litellm_params |
| Model-aware thinking normalization | `src/utils/claude_oauth/bridge.py::normalize_body_for_anthropic_direct` | Fable 5 accepts ONLY the adaptive thinking shape; the opus `enabled+budget_tokens` rewrite (and `disabled`) 400 on it. Fable requests are normalized to `{type: adaptive, display: summarized}`; absent thinking stays absent (always-on adaptive default) |
| `claude-fable-5` in `LITELLM_MODEL_IDS` | `eval/run_batch.py` | Without it the harness silently rewrites the model to `claude-opus-4.7` |
| Audit-route gate widened + fable rates | `src/utils/litellm_usage_oauth_callback.py` | `_is_oauth_route` was `"opus" in model` — fable rows would silently never be written. Fable priced at public $10/$50 per MTok (cache read $1, cache write $12.50) in `cost_bedrock_equivalent` |
| Fallback pricing entry | `src/utils/grading.py::_MODEL_COST_PER_TOKEN` | chat.jsonl heuristic path would otherwise mark fable usage `cost_unpriced` |
| Tests | `test_litellm_sidecar_config.py`, `test_usage_callbacks.py`, `test_claude_oauth_bridge.py` | Pin the block shape, the no-thinking-directive invariant, route gate, fable rates, and all four normalization cases |

## Operational caveats (from the Anthropic model docs)

- **Pricing:** $10/$50 per MTok — 2× Opus. The `usage_oauth.jsonl` audit trail
  reflects this; subscription quota drains faster per token.
- **`stop_reason: "refusal"`:** Fable 5 runs safety classifiers (cyber/bio);
  benign adjacent work can occasionally false-positive. On this harness a
  refusal surfaces as a failed/empty agent turn — expect occasional retries on
  security-flavored tasks.
- **30-day data retention required:** orgs configured for zero data retention
  get `400 invalid_request_error` on *every* fable request. If the entitlement
  probe 400s with a valid payload, check the org's retention setting first.
- **Long turns:** single fable turns can run many minutes; the bridge's
  streaming read timeouts (`WCB_BRIDGE_STREAM_READ_TIMEOUT`, default 600s
  per-chunk, no total cap) already accommodate this.
- **Sampling params** (`temperature`/`top_p`/`top_k`) 400 on fable; openclaw
  does not send them (verified by the working opus-4-8 route, which has the
  same restriction).

## Pending

1. First real fable run + verify `usage_oauth.jsonl` rows carry fable pricing.

## Operational note: pool credential vs the `claude` CLI

The pool file (`~/.wcb/oauth_pool/account_a.json`) was found dead once
(`invalid_grant`): when the pool shares an account with the local `claude` CLI,
whichever side refreshes first **consumes the refresh token the other holds**
(Anthropic rotates it on every exchange). Re-copy from the Keychain
(`security find-generic-password -s 'Claude Code-credentials' -w > <pool file>`)
to recover, or use a dedicated Max account for the pool to avoid the conflict
entirely.
