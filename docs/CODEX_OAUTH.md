# Codex ChatGPT-plan OAuth (MITM proxy)

Run the **codex** backend on a ChatGPT/Codex **subscription** instead of an
OpenRouter API key, with **multi-account rotation** on subscription caps.

## Why a proxy (and not a bridge like Claude)

The Claude OAuth path (`src/utils/claude_oauth/`) points the client at a local
Anthropic-compatible HTTP **bridge** via `ANTHROPIC_API_BASE`. That trick does
**not** work for codex 0.121:

- Codex inference uses a **WebSocket** (`wss://chatgpt.com/backend-api/codex/responses`), not HTTP/SSE.
- `chatgpt_base_url` redirects only auxiliary endpoints (`wham/apps`) — **not**
  `codex/models` or `codex/responses`. There is no env override for the
  inference base URL.

But two things make a proxy work: codex routes **all** traffic through
`HTTPS_PROXY` (verified: models + the responses WebSocket + token refresh), and
it **trusts a system-installed CA**. So `src/utils/codex_oauth/` is a
**forward `CONNECT` proxy that MITMs `chatgpt.com`**: it terminates TLS with a
CA-signed leaf, swaps the container's stub `Authorization`/`ChatGPT-Account-Id`
for a pooled account's real token, forwards to the real ChatGPT backend, and
rotates accounts on a 429 cap. Everything else is tunneled untouched.

Codex gets a **non-expiring stub `auth.json`** so it enters ChatGPT mode and
never refreshes its own token; the proxy owns the real tokens, refresh, and
rotation.

## Setup

1. Sign in to codex once per account so it writes `~/.codex/auth.json`:
   ```bash
   codex login          # completes ChatGPT OAuth, writes tokens{...}
   cp ~/.codex/auth.json /secure/pool/account_a.json   # one file per account
   ```
2. Point the harness at the pool and enable the path:
   ```bash
   export WCB_CX_ACCOUNT_POOL=/secure/pool/account_a.json:/secure/pool/account_b.json
   bash script/run.sh --backend codex --use-codex-oauth          # or WCB_USE_CODEX_OAUTH=1
   ```

`--use-codex-oauth` (or `WCB_USE_CODEX_OAUTH=1`) is a no-op unless
`WCB_CX_ACCOUNT_POOL` is set (same gate as the Claude path).

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `WCB_USE_CODEX_OAUTH` | `0` | Enable the codex OAuth path (needs a pool). |
| `WCB_CX_ACCOUNT_POOL` | — | Colon-separated codex `auth.json` paths (one per account). |
| `WCB_CX_PROXY_PORT` | `8770` | Port the MITM proxy container listens on. |
| `WCB_CX_PROXY_URL` | — | Explicit proxy URL override (else the proxy is reached by container name). |
| `DOCKER_IMAGE_CX_PROXY` | `wildclawbench-cx-proxy:v1` | Proxy image (auto-built from `docker/cx-proxy/Dockerfile`). |
| `WCB_CX_MODEL` | — | Override the codex model (else codex's ChatGPT default). |
| `CODEX_REFRESH_TOKEN_URL_OVERRIDE` | `https://auth.openai.com/oauth/token` | OAuth refresh endpoint. |

Available Codex models depend on the ChatGPT plan (e.g. Pro exposes `gpt-5.4`,
`gpt-5.4-mini`, `gpt-5.3-codex-spark`). An unavailable model name gets a 400
"model is not supported when using Codex with a ChatGPT account".

## What the harness wires (codex backend only)

- Runs the proxy as a **container** (`wildclawbench-cx-proxy:v1`, auto-built) on
  a per-run docker network, reached by container name — portable across
  Linux/macOS, no `host.docker.internal`. The proxy mints an ephemeral CA
  (only the public cert is written to a host-mounted dir) and mounts the pool
  read-write so refreshed tokens persist.
- The codex container joins the same network; `HTTPS_PROXY` → the proxy by name,
  `SSL_CERT_FILE`/`SSL_CERT_DIR` set, no `OPENROUTER_*`.
- Installs the proxy CA into the container trust store (`update-ca-certificates`)
  and writes the stub `auth.json`.
- `config.toml`: `forced_login_method="chatgpt"`, `cli_auth_credentials_store="file"`.
- Account rotation triggers only on the **inference** endpoints
  (`codex/responses`, `codex/models`) for a 429 cap or 401 token-invalid; a
  403/402 on an auxiliary endpoint (apps/connectors/MCP) is passed through and
  does **not** invalidate the account.

Run the proxy standalone for debugging:
```bash
WCB_CX_ACCOUNT_POOL=... python -m src.utils.codex_oauth --port 8770 --ca-out /tmp/ca.pem --check
```

## ToS caveat

MITM'ing ChatGPT/Codex subscription traffic for automated benchmark runs is a
**gray zone** of OpenAI's Acceptable Use Policy (interactive coding is the
intended use), and is *more* invasive than a base-url redirect. Use for
evaluation/research only; accounts have been suspended for similar automation.
For production, prefer the metered API-key path (codex + OpenRouter, no proxy).
