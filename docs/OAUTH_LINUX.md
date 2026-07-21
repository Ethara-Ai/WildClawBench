# OAuth Trajectory Path on Linux — Verification Notes

**Answer: yes, the OAuth path works on Linux by design.** The harness's production
configuration (file-based credential pool + Dockerized bridge) has no macOS-only
dependency. Evidence, by component:

## What works, and why

| Component | Linux status | Evidence |
|---|---|---|
| Credential loading | ✅ | `credentials.py` load order tries `~/.claude/.credentials.json` — the file the `claude` CLI writes **on Linux** — before any keychain. `WCB_CC_CREDS_PATH` / `CLAUDE_CODE_CREDENTIALS` overrides are OS-agnostic. |
| File-based account pool (`WCB_CC_ACCOUNT_POOL`) | ✅ | Pure file I/O + `fcntl.flock` (native Linux). This is the only pool form the harness itself builds (`start_bridge` globs `*.json`). |
| Token refresh + rotation persistence | ✅ | `httpx` POST + file write-back under flock; no OS dependency. |
| The bridge (`wcbsh-cc-bridge`) | ✅ | Runs inside a Linux container regardless of host OS; on a Linux host, the Docker Desktop-specific network-attach-ordering workaround in `start_bridge` (`litellm_sidecar.py`) is simply unnecessary but harmless. |
| Rotation / failover / recovery | ✅ | Pure Python (`threading`, `fcntl`, `httpx`); covered by `tests/test_claude_oauth_rotation.py`. |
| Harness support commit | ✅ | `0d6624a` "Linux wcb support" (2026-07-10) landed Linux-specific wiring. |

## What does NOT work on Linux, and why

1. **macOS Keychain credential sources** (`keychain:<service>` slots, the
   Keychain read in the default provider): the `security` binary doesn't exist —
   these are Darwin-gated no-ops (`platform.system() != "Darwin"` guards). Not
   used by the harness pool path, so no impact.
2. **Secret Service lookup on headless Linux/EC2**: `secret-tool` needs a D-Bus
   session and an *unlocked* keyring, absent on servers — it silently no-ops and
   the loader falls through to the credentials file. Only matters for
   desktop-Linux setups whose `claude` CLI stored the token in GNOME
   Keyring/KWallet.
3. Note: `keychain:` pool entries are additionally broken on *every* OS by the
   parsing bug M1 in `docs/OAUTH_ROTATION_ISSUES.md` — moot on Linux either way.

## Setup checklist for a Linux box (e.g. EC2)

1. Sign in once with the `claude` CLI (writes `~/.claude/.credentials.json`), or
   copy kaiju-format credential JSONs into a pool dir.
2. Point `WCB_CC_ACCOUNT_POOL` at the pool file(s); set `WCB_CC_BRIDGE_SECRET`;
   `WCB_USE_CLAUDE_OAUTH=1`.
3. Ensure outbound HTTPS to `api.anthropic.com` and
   `console.anthropic.com` (token refresh) from the bridge container's egress
   network.
4. TLS note for host-side Python scripts (probes, recovery): on minimal
   installs, ensure a CA store is available (`certifi` +
   `SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')`).
5. Smoke: `bash script/run.sh input/<task> claude-opus-4.7 1` with OAuth env
   set; verify `usage_oauth.jsonl` rows appear.

Remaining open item: an actual smoke run on the target Linux box (cannot be
performed from this macOS machine).
