# Running Codex (ChatGPT subscription / GPT-5.6) on the harness

> **The `wcb login` analog for codex is `codex login`.** There is no `wcb`-style
> wrapper — codex uses the official Codex CLI to mint credentials into
> `~/.codex/auth.json`, and the harness starts/stops the bridge for you per run.
>
> **Authoritative reference:** [`docs/OPENAI_CODEX_SUBSCRIPTION.md`](OPENAI_CODEX_SUBSCRIPTION.md).
> This file is the run-oriented runbook; that file is the deep reference. (Note: a
> few spots in the reference still say bare `gpt-5.6` in prose — the current default
> is `gpt-5.6-sol`, used throughout below.)

## What you're doing

Routing OpenClaw's `gpt-5.6-sol` trajectory calls through a local bridge container
that swaps a stub key for your ChatGPT OAuth token and forwards to
`chatgpt.com/backend-api/codex/responses` — billed to your flat subscription, not a
metered `sk-...` key.

**Key difference from Claude-OAuth:** Claude-OAuth is a bash wrapper (`wcb`) that
writes `.env` + a Keychain pool. Codex-OAuth just needs `codex login` on the host
plus a flag at run time — no `.env` token required.

> ⚠️ **ToS caveat.** Driving a ChatGPT/Codex subscription through an automated
> bridge is a gray area of OpenAI's AUP (Codex is meant for interactive use, not
> batch benchmarks). Accounts have been suspended for similar automation. Use for
> eval/research; use a metered key for production.

---

## Step 1 — One-time: authenticate (the `codex login` step)

```bash
# Install the Codex CLI
npm install -g @openai/codex        # or: brew install codex

# Log in with your ChatGPT Pro/Team/Enterprise account (opens a browser)
codex login
```

A metered `sk-...` key will **not** work — it must be a subscription (ChatGPT-mode
`auth.json`). This writes `~/.codex/auth.json` containing `tokens.access_token` +
`tokens.account_id`.

**Verify the credentials load** (uses this repo's bridge; no network needed if the
token is valid):

```bash
PYTHONPATH=src/utils python3 -m codex_oauth --check
# → [codex-bridge] credentials OK (token prefix: eyJhbGciO..., account: 1ec6e172...)
```

If it prints `credentials error`, re-run `codex login`.

You do **not** start the bridge yourself — the harness does.

---

## Step 2 — `.env` updates

Codex needs **zero required `.env` entries** for a basic single-account run (the
flag alone works, and the bridge secret auto-generates per run). All of these are
**optional**:

```bash
# --- Codex OAuth (all optional) ---
WCB_USE_CODEX_OAUTH=1              # same as passing --use-codex-oauth
WCB_CODEX_MODEL=gpt-5.6-sol        # agent-facing model id (default already gpt-5.6-sol)
WCB_CODEX_AUTH_DIR=~/.codex        # host dir with auth.json (default ~/.codex)
WCB_CODEX_AUTO_POOL=1              # auto-accumulate multi-account pool (recommended for long runs)
WCB_CODEX_POOL_DIR=~/.codex_pool   # where the pool lives (default ~/.codex_pool)
# WCB_CODEX_BRIDGE_SECRET=...      # pin the sidecar<->bridge secret (default: random per run)
# WCB_CODEX_BRIDGE_HEALTH_TIMEOUT=60
```

Contrast with Claude: no `wcb setup`, no Keychain read, no `.env` token — the token
lives only in `~/.codex/auth.json` and is mounted into the bridge container.

---

## Step 3 — Run

Same orchestrator as always, plus `--use-codex-oauth`:

```bash
source .venv/bin/activate

python3 eval/run_batch.py \
  --task input/alden-croft_MB \
  --agent-backend openclaw \
  --model gpt-5.6-sol \
  --use-codex-oauth \
  --litellm --mock-stack \
  --generate-tests --execute-tests --judge-council \
  --parallel 1
```

`--use-codex-oauth` ≡ `WCB_USE_CODEX_OAUTH=1`. Outputs land at
`output/openclaw/<task>/trajectories/gpt-5.6-sol/run_N/`.

**What happens automatically:** build `wildclawbench-codex-bridge:v1` (first run
only) → start bridge container on the batch's LiteLLM network mounting `~/.codex` →
health-check → LiteLLM sidecar registers `gpt-5.6-sol` pointing at
`http://wcbsh-codex-bridge-<id>:8788/v1` → OpenClaw runs → bridge torn down on exit.
Your `platform.openai.com` API dashboard shows **zero** calls; ChatGPT usage ticks
up.

### Two hard constraints

1. **Cannot use `bash script/run.sh` for codex.** Shared-sidecar mode (which
   `run.sh` sets via `WCB_SHARED_*`) **hard-raises** a `RuntimeError` for codex —
   the bridge is per-process and not bootstrapped by the shared sidecar. You **must**
   use direct `python3 eval/run_batch.py --use-codex-oauth`. This is the biggest
   operational difference from Claude-OAuth (where `wcb run` →
   `run.sh --use-claude-oauth` works fine).
2. **Model name must be a `-sol`/`-luna`/`-terra` variant, never bare `gpt-5.6`** —
   the backend 400s on `gpt-5.6` ("model is not supported"). Default `gpt-5.6-sol`
   is correct. If your subscription exposes a different id, set both
   `--model <id>` and `WCB_CODEX_MODEL=<id>`.

---

## Step 4 (optional) — Survive quota caps with a multi-account pool

A subscription has a ~5h rolling quota. For long/parallel runs, use a pool so the
bridge rotates accounts automatically.

**Option A — auto-pool (recommended, hands-off).** Set once in `.env`:

```bash
WCB_CODEX_AUTO_POOL=1
```

Every run snapshots the currently-logged-in account into `~/.codex_pool/` (keyed by
account id, deduped). Grow it by logging into different accounts over time:

```bash
# run once as Account A (A saved to pool) -> codex logout -> codex login (Account B) -> run (B joins)
```

The bridge then rotates across A+B and auto-switches on a 429 (in-request, ~0.05s,
agent never sees a failure). Keep `--parallel <= number of accounts`.

**Option B — manual pool:**

```bash
mkdir -p ~/.codex_pool
cp ~/.codex/auth.json ~/.codex_pool/acct1.json    # SAVE current account FIRST
codex logout && codex login                       # switch to account 2
cp ~/.codex/auth.json ~/.codex_pool/acct2.json
# then run with: WCB_CODEX_POOL_DIR=~/.codex_pool python3 eval/run_batch.py ...
```

Order matters — `cp` before switching, or the un-saved account is lost. Browser
caveat: `codex login` reuses the browser's signed-in account, so both logins may
grab the **same** account; compare `tokens.account_id` across files — they must
differ (use a private window if they match).

**Cap-wait (single account, last resort):** if every account is capped the bridge
holds the turn open (SSE keep-alives), pauses `KAIJU_CODEX_CAP_WAIT_SEC`
(default 60s), reloads `~/.codex/auth.json`, and retries up to
`KAIJU_CODEX_CAP_MAX_WAITS` (default 10) — you `codex login` a fresh account during
the pause. Watch bridge logs for `codex cap hit — SWAP YOUR ACCOUNT NOW`. Set
`KAIJU_CODEX_CAP_WAIT_SEC=0` to fail fast.

---

## Optional — also route the GPT rubric judge through the same subscription

If you run with `--use-codex-oauth --litellm --agent-backend openclaw`, the harness
**auto-exports** `KENSEI_JUDGE_CODEX_BRIDGE_URL` so the Channel-B GPT judge grades on
the same bridge (billed $0). It's enabled by default when a GPT judge is configured;
force it off with `JUDGE_GPT_PRIMARY=0`. On any non-codex run this URL is
unconditionally stripped, so the judge never dials a dead bridge. (On
`--agent-backend codex`, auto-export doesn't fire — you'd set
`KENSEI_JUDGE_CODEX_BRIDGE_URL` yourself.)

---

## Troubleshooting quick reference

| Symptom | Fix |
|---|---|
| `codex-bridge did not become healthy` / *no valid subscription credentials* | `codex login` not run or `~/.codex/auth.json` expired → re-login |
| upstream `401` in bridge logs | dead OAuth token, refresh failed → `rm ~/.codex/auth.json && codex login` |
| `429` / usage cap | add accounts (`WCB_CODEX_AUTO_POOL=1` or `WCB_CODEX_POOL_DIR`); single account → cap-wait hot-swap |
| `400 model is not supported` | set `WCB_CODEX_MODEL` (agent id) and/or `KAIJU_CODEX_MODEL` (upstream id) to an accepted `-sol/-luna/-terra` variant |
| `LLM request timed out` on long reasoning | bridge sends SSE keep-alives; if it persists, lower `reasoning_effort` in the codex block of `src/utils/litellm_sidecar.py` |

Live bridge logs: `docker logs wcbsh-codex-bridge-<batch_id>`.

---

## TL;DR vs Claude

| | Claude-OAuth | Codex-OAuth |
|---|---|---|
| Authenticate | `source script/wcb login` (bash wrapper) | `codex login` (official Codex CLI) |
| Credential home | `.env` + `~/.wcb/oauth_pool/` (Keychain-sourced) | `~/.codex/auth.json` (mounted into bridge) |
| Run | `wcb run` / `run.sh --use-claude-oauth` | `python3 eval/run_batch.py --use-codex-oauth` **directly** (not `run.sh`) |
| Required `.env` | token written by `wcb setup` | none (flag alone; secret auto-generates) |
| Multi-account pool | Keychain pool | `WCB_CODEX_AUTO_POOL=1` / `WCB_CODEX_POOL_DIR` |
