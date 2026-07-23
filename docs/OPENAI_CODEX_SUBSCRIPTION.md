# Running trajectories on a ChatGPT / Codex subscription (GPT‑5.6)

This lets you generate **OpenClaw** trajectories where the driving LLM is
**GPT‑5.6 billed to your ChatGPT/Codex subscription** (a flat monthly fee)
instead of a metered OpenAI API key.

> **The agent does not change.** You keep using the OpenClaw backend exactly as
> today. Only the *LLM provider* changes: `gpt-5.6` calls are routed through a
> small local **bridge** that swaps a stub key for your ChatGPT OAuth token and
> forwards to `chatgpt.com/backend-api/codex/responses`.

It is the direct sibling of the existing Claude‑subscription path
(`--use-claude-oauth` / `wcbsh-cc-bridge`). See `RUNBOOK.md` / `NOMENCLATURE.md`
for the rest of the harness.

> ⚠️ **ToS caveat.** Driving a ChatGPT/Codex subscription through an automated
> bridge is a gray area of OpenAI's Acceptable Use Policy (Codex is meant for
> interactive coding, not batch benchmark runs). Use it for evaluation/research;
> for production, use a metered API key. Accounts have been suspended for similar
> automation.

---

## How it fits together

```
OpenClaw agent (container, no egress)
        │  /v1/chat/completions  (model: gpt-5.6)
        ▼
LiteLLM sidecar  ──►  wcbsh-codex-bridge  ──►  chatgpt.com/backend-api/codex/responses
   (routes gpt-5.6)      (injects OAuth + codex_cli_rs headers)   (your subscription)
```

* The bridge runs as a **container on the LiteLLM network**, dual‑homed to the
  default bridge for internet egress — exactly like the Claude cc‑bridge. It is
  built automatically from `docker/codex-bridge/Dockerfile` (image
  `wildclawbench-codex-bridge:v1`); the bridge code lives in
  `src/utils/codex_oauth/`.
* Your ChatGPT credentials never enter the agent container. They stay in
  `~/.codex/auth.json` on the host and are mounted read‑only‑ish into the bridge
  container only.
* This works the same on **macOS and Linux** — everything is container‑to‑container
  over the Docker network by name, so there is no `host.docker.internal` or
  host‑port dependency.

---

## Prerequisites (one‑time)

1. **A ChatGPT Pro/Team/Enterprise subscription** (a metered `sk-...` key will
   *not* work here — that's the whole point).

2. **Log in with the Codex CLI so `~/.codex/auth.json` exists:**

   ```bash
   npm install -g @openai/codex     # or: brew install codex
   codex login                      # opens a browser; sign in with ChatGPT
   ```

3. **Confirm the credentials load** (uses this repo's bridge, no network needed
   if the token is still valid):

   ```bash
   PYTHONPATH=src/utils python3 -m codex_oauth --check
   # [codex-bridge] credentials OK (token prefix: eyJhbGciO..., account: 1ec6e172...)
   ```

   If it prints `credentials error`, re‑run `codex login`.

That's it. You do **not** start the bridge yourself — the harness starts and
stops it per run.

---

## Run one trajectory

Use the normal orchestrator, add `--use-codex-oauth`, and pick the model.

> **Model name matters.** The Codex/ChatGPT backend rejects a bare `gpt-5.6`
> ("model is not supported"). The real GPT‑5.6 family it serves is the
> `-luna` / `-sol` / `-terra` variants. The harness defaults to **`gpt-5.6-sol`**.
> To see exactly what your subscription exposes:
> ```bash
> python3 -c 'import json,pathlib;d=json.loads(pathlib.Path.home().joinpath(".codex/models_cache.json").read_text());import re;print(sorted({v for _ in [d] for v in re.findall(r"\"(gpt-[0-9.a-z-]+)\"", pathlib.Path.home().joinpath(".codex/models_cache.json").read_text())}))'
> ```
> Then run `--model <that-id>` (and set `WCB_CODEX_MODEL=<that-id>` if it isn't the default).

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

What happens:

1. The harness builds the `wildclawbench-codex-bridge:v1` image (first run only).
2. It starts the bridge container on the batch's LiteLLM network, mounting
   `~/.codex`, and health‑checks it.
3. The LiteLLM sidecar is configured with a `gpt-5.6` model that points at the
   bridge (`api_base: http://wcbsh-codex-bridge-<id>:8788/v1`).
4. OpenClaw runs the task as usual; every `gpt-5.6` call is billed to your
   subscription. Your `platform.openai.com` API dashboard should show **zero**
   calls; your ChatGPT usage ticks up instead.
5. On exit, the bridge container is torn down automatically.

`--use-codex-oauth` is equivalent to `WCB_USE_CODEX_OAUTH=1` in the environment.

Outputs land in the usual place:
`output/openclaw/<task>/trajectories/gpt-5.6/run_N/`.

---

## Configuration knobs (all optional)

| Env var | Default | Purpose |
|---|---|---|
| `WCB_USE_CODEX_OAUTH` | unset | `1` = enable (same as `--use-codex-oauth`). |
| `WCB_CODEX_MODEL` | `gpt-5.6-sol` | Sidecar model id / name shown to the agent. Set this if your subscription exposes a different id, e.g. `gpt-5.6-luna` / `gpt-5.6-terra` (then run `--model <that-id>`). |
| `WCB_CODEX_AUTH_DIR` | `~/.codex` | Host dir containing `auth.json` (mounted into the bridge). |
| `WCB_CODEX_BRIDGE_SECRET` | random per run | Shared secret between the sidecar and the bridge. Set it to reuse a fixed value. |
| `WCB_CODEX_BRIDGE_HEALTH_TIMEOUT` | `60` | Seconds to wait for the bridge to report healthy. |
| `KAIJU_CODEX_MODEL` | unset | Force the exact model name sent **upstream** to the codex backend (the bridge otherwise forwards what LiteLLM sent, minus any trailing date snapshot). |
| `WCB_CODEX_POOL_DIR` | unset | Host dir of several `auth.json` files (one per ChatGPT account). The bridge round‑robins across them and auto‑switches when one caps. See **Surviving quota limits** below. |
| `WCB_CODEX_AUTO_POOL` | unset | `1` = automatically snapshot the account you're logged into on each run into `WCB_CODEX_POOL_DIR` (default `~/.codex_pool`), keyed by account id, so the pool accumulates every account you use over time. |
| `KAIJU_CODEX_CAP_WAIT_SEC` | `60` | When every account is capped, seconds to pause (with keep‑alives) so you can hot‑swap an account before retrying. `0` disables. |
| `KAIJU_CODEX_CAP_MAX_WAITS` | `10` | How many pause‑and‑retry cycles before surfacing the cap. |
| `KAIJU_CODEX_ACCOUNT_POOL` | unset | (Advanced) Colon‑separated list of **container‑path** `auth.json` files. Normally set for you from `WCB_CODEX_POOL_DIR`; only set this directly if you mount the files yourself. |
| `KAIJU_CODEX_KEEPALIVE_SEC` | `15` | SSE keep‑alive interval while the model is reasoning (keeps long turns from timing out). |

---

## Surviving quota limits (multi‑account + hot‑swap)

A ChatGPT/Codex subscription has a per‑window quota (~5 hours). A single big
trajectory usually fits, but long or parallel runs will hit the cap. Three
layers keep a run alive when that happens — you can use any or all:

```
account caps mid-trajectory
  ①  another account free?  → bridge rotates to it INSTANTLY (in-request)  ── automatic
  ②  no accounts left?      → bridge PAUSES, you swap an account, it retries ── cap-wait
```

### Mental model — a keyring 🔑

- `codex login` makes **one key** (`~/.codex/auth.json`) — the CLI only holds one at a time.
- A **key is just a file**; once made it works on its own.
- A **pool** is a folder of several key files. The **bridge is the doorman**: it holds the whole keyring and tries the next key whenever one stops working.
- So you **collect keys one at a time, but use them all together.**

### Option A — auto‑pool (recommended, hands‑off)

Turn it on once, in `.env`:

```
WCB_CODEX_AUTO_POOL=1
```

Now, every run **snapshots the account you're logged into** into `~/.codex_pool/`
(keyed by account id). Over time:

1. Run with Account A → A is saved to the pool.
2. Later: `codex logout`, `codex login` (Account B), run → **B joins the pool**.
3. The pool now has A + B → the bridge **rotates across them** and auto‑switches
   when one caps. You run no `cp` commands.

Dedup is by account id, so re‑running with the same account never duplicates it —
the pool only grows when you actually log into a *different* account.

### Option B — manual pool (explicit control)

Build the folder yourself:

```bash
mkdir -p ~/.codex_pool
cp ~/.codex/auth.json ~/.codex_pool/acct1.json     # save the CURRENT account first
codex logout && codex login                        # switch to a second account
cp ~/.codex/auth.json ~/.codex_pool/acct2.json     # save it too
```

> **Order matters:** `cp` (save) **before** you switch — logging out/in overwrites
> the single `~/.codex/auth.json`, so an un‑saved account is lost.

Then run with:
```bash
WCB_CODEX_POOL_DIR=~/.codex_pool  ...normal command...
```

> **Browser caveat:** `codex login` reuses whichever ChatGPT account is signed in
> in your browser, so both logins may grab the *same* account. After setup,
> compare the `account_id` field inside each file — they must differ. If they
> match, sign out of ChatGPT in the browser (or use a private window) and redo.

### How to check what's in the pool

- Folder `~/.codex_pool/` — each `.json` = one account (filename is the account id under auto‑pool).
- Compare the `tokens.account_id` field across files — all should be **distinct**.
- The bridge logs it at startup:
  `codex multi-account pool: 2 account(s) from ~/.codex_pool`

### Automatic rotation (how switching works)

With a pool, when Account A returns a cap (429), the bridge cools A for its
`retry_after` window and **rotates to the next available account in the same
request** (~0.05s, up to `KAIJU_CODEX_MAX_INLINE_RETRIES`). The agent never sees a
failure; the trajectory continues on Account B. A re‑enters rotation once its
cooldown expires. This lets you safely raise `--parallel` — keep it ≤ the number
of accounts so no single account is overloaded.

### Cap‑wait (last resort — hot‑swap while paused)

If **every** account is capped (or you run a single account), the bridge doesn't
just fail: it **holds the trajectory open** (emitting SSE keep‑alives so OpenClaw
doesn't time out), pauses `KAIJU_CODEX_CAP_WAIT_SEC` seconds (default 60), then
**reloads `~/.codex/auth.json` from disk and retries** — up to
`KAIJU_CODEX_CAP_MAX_WAITS` cycles (default 10). During each pause you swap the
account (`codex login` another account, or replace the file) and the run resumes
on the fresh one. Watch for this line in the bridge logs:

```
codex cap hit — SWAP YOUR ACCOUNT NOW (`codex login` with another account, or
replace ~/.codex/auth.json). Holding the turn open, waiting 60s ... (cycle 1/10).
```

Set `KAIJU_CODEX_CAP_WAIT_SEC=0` to disable and fail fast instead.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `codex-bridge ... did not become healthy` and logs show *no valid subscription credentials* | `codex login` not run, or `~/.codex/auth.json` expired. Re‑login, then retry. |
| `401` in the bridge logs from the client side | Sidecar and bridge secret mismatch — normally impossible (the harness sets both), but check `WCB_CODEX_BRIDGE_SECRET` isn't pinned to different values. |
| `401` from the **upstream** (bridge log shows codex backend 401) | The OAuth access token is dead and refresh failed. `rm ~/.codex/auth.json && codex login`. |
| `429` / usage cap | You hit the subscription's Codex quota. Add accounts so the bridge rotates — see **Surviving quota limits** (`WCB_CODEX_AUTO_POOL=1` or `WCB_CODEX_POOL_DIR`). With a single account the bridge pauses (cap‑wait) for you to hot‑swap. |
| `400 model is not supported` | The name in `gpt-5.6` isn't what your subscription's codex backend expects. Set `WCB_CODEX_MODEL` (agent‑facing id) and/or `KAIJU_CODEX_MODEL` (upstream id) to the accepted value. |
| Agent logs `LLM request timed out` on long reasoning turns | OpenClaw has its own ~22s ceiling on a stalled stream. The bridge emits SSE keep‑alives to mitigate this; if it persists, lower `reasoning_effort` in the `gpt-5.6` block of `src/utils/litellm_sidecar.py` (e.g. `"medium"`). |

**Bridge logs:** `docker logs wcbsh-codex-bridge-<batch_id>` while a run is in
flight.

---

## What was added (for maintainers)

Nothing in the OpenClaw agent path or the existing `codex` agent backend was
changed. The feature is additive and gated on `--use-codex-oauth`:

* `src/utils/codex_oauth/` — the bridge (FastAPI proxy: credentials, error
  taxonomy, Chat↔Responses translation, account‑pool rotation). Relative
  imports so it runs both as `python -m codex_oauth` (in the image) and as
  `src.utils.codex_oauth` (on the host).
* `docker/codex-bridge/Dockerfile` — packages that module (sibling of
  `docker/cc-bridge/Dockerfile`).
* `src/utils/litellm_sidecar.py` — a `gpt-5.6` model block in
  `build_litellm_config_yaml` (only emitted when `codex_bridge_url` is set), plus
  `start_codex_bridge` / `wait_for_codex_bridge_healthy` / `ensure_codex_bridge_image`
  (mirrors the cc‑bridge helpers), and the sidecar env carries
  `WCB_CODEX_BRIDGE_SECRET`.
* `eval/run_batch.py` — brings the bridge up (and tears it down) inside
  `_setup_litellm_and_mocks` when `--use-codex-oauth` is set; registers `gpt-5.6`
  as a valid sidecar model id; `_harvest_codex_account()` implements auto‑pool.
* `src/utils/cli_args.py` — the `--use-codex-oauth` flag.

**Quota‑resilience additions (all opt‑in):**

* `src/utils/codex_oauth/credentials.py` — `reload()` on every provider
  (single/file/multi) so a hot‑swapped `auth.json` is re‑read mid‑request.
* `src/utils/codex_oauth/bridge.py` — **cap‑wait**: `_stream_with_cap_wait` /
  `_open_unary_with_cap_wait` hold the turn open with keep‑alives, reload the
  credential, and retry on a cap (`KAIJU_CODEX_CAP_WAIT_SEC` / `_MAX_WAITS`).
* `src/utils/litellm_sidecar.py::start_codex_bridge` — mounts `WCB_CODEX_POOL_DIR`
  and builds the `KAIJU_CODEX_ACCOUNT_POOL` spec (container paths); injects the
  cap‑wait env (default 60s / 10 cycles). The rotation itself lives in
  `MultiAccountCredentialProvider`.
* `eval/run_batch.py::_harvest_codex_account` — snapshots the active account into
  the pool (keyed by account id) when `WCB_CODEX_AUTO_POOL=1`.

When `--use-codex-oauth` is off, the generated LiteLLM config and every container
are byte‑identical to before. With it on but no pool/cap‑wait env set, behaviour
is a plain single‑account bridge.
