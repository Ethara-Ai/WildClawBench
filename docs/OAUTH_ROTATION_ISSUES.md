# OAuth Rotation / Failover — Code Review Findings

**Scope:** `src/utils/claude_oauth/{credentials.py, bridge.py, errors.py, recovery.py}` on `master`
(diff range `508e1c2...HEAD`, i.e. the full OAuth pathway introduced by `010c3c7` → `629b685`).

**Method:** two-axis review (standards + spec/correctness) via parallel sub-agents, followed by a
manual adversarial verification pass — every finding below was re-traced against the code by hand;
one finding's impact was downgraded and one sub-claim was discarded as a false positive
(both noted inline).

**Verdict in one line:** the core rotation loop (cap detection → mark exhausted → failover →
recovery pause/resume) works for the primary harness path (single process, file-based pool), but
refresh-token durability and account-invalidation edges can quietly take a healthy pool down on
long or concurrent runs.

---

## CRITICAL

### C1. Transient refresh failure permanently invalidates an account

- **Status: FIXED 2026-07-21** — `TransientCredentialsError` (network/5xx/non-JSON refresh
  failures) now backs the slot off `TRANSIENT_REFRESH_BACKOFF_SECONDS` (60s) instead of setting
  `invalid`; 4xx stays permanent. Tests: `tests/test_claude_oauth_rotation.py` (T1/T2/type/keep-green).
- **Where:** `src/utils/claude_oauth/credentials.py:498-507` (`MultiAccountCredentialProvider.get_access_token`)
- **Proof:**
  ```python
  try:
      return slot.provider.get_access_token()
  except CredentialsError:
      with self._lock:
          slot.invalid = True          # permanent — nothing ever resets it
      return self.get_access_token()
  ```
  `refresh_credentials()` raises `CredentialsError` after 3 network attempts (~3s of backoff,
  `credentials.py:251-256`). Grep-verified: **no caller in the run path ever invokes
  `force_reload()` on the multi-account provider**, and `force_reload()` is the only thing that
  clears `slot.invalid`. The bridge's `_apply_classification_to_provider` calls `force_reload()`
  only for the *single-account* provider (`bridge.py:582-584`).
- **Failure scenario:** a few seconds of DNS/network trouble during a token refresh mid-run →
  the account is excluded for the remainder of the process. With a 1-slot pool the bridge
  returns 401 for every subsequent request; the run is dead even though the account is healthy.
- **Why critical:** silent, permanent capacity loss triggered by an everyday transient condition;
  compounds with H1/H2 (their end state is a spurious `CredentialsError` that lands here).
- **Fix direction:** distinguish *non-retryable* refresh failures (HTTP 4xx = token revoked) from
  transient ones (network / 5xx). Only the former should set `invalid`; the latter should mark the
  slot temporarily unavailable (e.g. `exhausted_until = now + 60`) and move on.

---

## HIGH

### H1. Rotated refresh token lost across processes (stale in-memory pair used after flock)

- **Status: FIXED 2026-07-21** — after the flock re-read, the on-disk pair is adopted whenever
  `fresh.expires_at_ms >= self._creds.expires_at_ms`, so refresh always uses the newest refresh
  token (guard protects the failed-write-back case where memory is newer). Tests:
  `tests/test_claude_oauth_rotation.py` (on-disk-newer, in-memory-newer, concurrent-refresher).
- **Where:** `src/utils/claude_oauth/credentials.py:393-402` (`_FileCredentialProvider.get_access_token`)
- **Proof:** after acquiring the `.lock` flock the code re-reads the pool file, but an
  expired-yet-**newer** on-disk pair is discarded and the refresh runs on the stale in-memory pair:
  ```python
  try:
      fresh = self._load()
      if not fresh.is_expired():
          self._creds = fresh
          return self._creds.access_token
  except CredentialsError:
      pass
  _LOG.info("Refreshing OAuth token from %s", self._path)
  self._creds = refresh_credentials(self._creds)   # <-- stale pair, not `fresh`
  ```
  Anthropic rotates the refresh token on every exchange (module docstring + `credentials.py:228-236`),
  so the in-memory refresh token may already be consumed.
- **Failure scenario:** process A refreshes and rewrites the pool file (rotating the refresh
  token). More than ~1h later (both pairs now expired — e.g. overnight idle, then resume),
  process B holding the pre-rotation pair takes the lock, re-reads, discards the expired `fresh`,
  and refreshes with A's **already-consumed** refresh token → upstream 401 → `CredentialsError`
  → C1 permanently invalidates the slot. The account then needs a manual re-login.
- **Why high (not critical):** requires the specific multi-process + >1h-idle timing, but
  multi-process shared pools are exactly the documented use case the flock exists for, and the
  blast radius is a dead account, not just a failed request.
- **Fix direction:** when `fresh` loads successfully, always adopt it before refreshing
  (`self._creds = fresh`), so the refresh uses the newest refresh token on disk.

### H2. Cap/error attribution silently dropped after a mid-flight token refresh

- **Status: FIXED 2026-07-21** — every provider now records its last 8 distinct vended tokens
  (`knows_token`); `_find_slot_by_prefix_locked` matches the exact erred token against that
  history first, with the old prefix match as fallback. The false `slot.last_token` comment in
  bridge.py was corrected. Tests: `tests/test_claude_oauth_rotation.py` (rotation-attribution,
  dedupe-guard).
- **Where:** `src/utils/claude_oauth/credentials.py:592-599` (`_find_slot_by_prefix_locked`);
  misleading comment at `src/utils/claude_oauth/bridge.py:557-559`
- **Proof:** the bridge comment claims exact attribution via `slot.last_token` — **that field does
  not exist anywhere** (grep-verified). Actual matching compares the slot's *current* in-memory
  token's first 20 chars against the token that produced the error:
  ```python
  sp = getattr(slot.provider, "token_prefix", lambda: None)()   # first 20 chars of CURRENT token
  if sp and (sp.startswith(token_prefix) or token_prefix.startswith(sp)):
      return slot
  ```
  If the slot's token was refreshed after the failing request was issued, no slot matches
  (13 of the 20 chars are the shared `sk-ant-oat01-` literal; the remaining 7 differ), so
  `mark_account_exhausted` returns silently (`if slot is None: return`) and the cap is never
  recorded. The fresh token also defeats the `_tried_tokens` burned-slot guard in the bridge's
  failover loops, so failover re-selects the same capped account.
- **Failure scenario (verified ordering):** in the sequential case the classification lands
  *before* the slot refreshes, so marking sticks. The loss requires a **concurrent** request
  (agent + judge share the bridge) to refresh the slot's token during a long turn — realistic on
  this harness, where a single Opus extended-thinking streaming turn runs 10+ minutes — after
  which the 429 arrives and can't be attributed. Result: retries burned re-hitting a capped
  account, then a hard error and an unnecessarily long recovery pause.
- **Why high (not critical):** narrow trigger window, and recovery eventually rescues the run;
  but when it fires it defeats the entire purpose of exhaustion tracking, and the comment/code
  drift means the next maintainer will reason from a design that isn't there.
- **Discarded sub-claim (false positive):** cross-account 20-char prefix *collision* mis-marking
  the wrong slot — 7 random chars colliding is negligible in a pool of a handful of accounts.
- **Fix direction:** record the actual token each slot last vended (make the comment true:
  `slot.last_token`) and attribute on full-token equality against current *and* last-vended
  tokens.

---

## MEDIUM

### M1. `keychain:<service>` pool entries can never parse (unreachable branch)

- **Where:** `src/utils/claude_oauth/credentials.py:629,639` (`load_account_pool`)
- **Proof:** the spec is split on `":"` first, so no entry can ever contain a colon:
  ```python
  for raw in spec.split(":"):        # "keychain:Work" → ["keychain", "Work"]
      ...
      if entry.startswith("keychain:"):   # unreachable — entries never contain ':'
  ```
  `"keychain:Work"` becomes two bogus *file-path* slots (`file:keychain`, `file:Work`), which
  fail at first use and — via C1 — get marked invalid.
- **Why medium:** completely broken feature with silent misconfiguration (pool reports 2 slots
  that both die), but zero impact on the harness path: `start_bridge`
  (`litellm_sidecar.py:940-949`) builds pools exclusively from `*.json` files. No test covers it.
- **Fix direction:** split on a separator that can't appear in entries (e.g. `os.pathsep` is the
  same `:` on POSIX — better: split with a regex that keeps `keychain:` attached, or change the
  pool separator to `;`/newline).

### M2. Keychain-sourced token rotation is not durable

- **Where:** `src/utils/claude_oauth/credentials.py:447-458` (`_KeychainCredentialProvider`),
  `write_cache` at `:306-315`, load order at `:204-211`
- **Proof:** after refreshing, the rotated pair is written only to the shared cache file
  (`~/.cache/wildclawbench/claude_creds.json`). But `_KeychainCredentialProvider._load` reads
  **only** the keychain, and even the default provider's load order tries the keychain *before*
  the cache — so the rotated refresh token is never read back. Next process start refreshes with
  the keychain's consumed token → 401. Two keychain slots would additionally clobber the same
  cache file.
- **Why medium:** real durability flaw, but mitigated in practice — the `claude` CLI keeps the
  keychain fresh on developer machines, and M1 makes keychain *pool* slots unreachable anyway.
- **Fix direction:** prefer the cache when it holds a newer `expiresAt` than the keychain payload,
  and key the cache file per keychain service.

---

## LOW

### L1. Buffered streaming can replay partial content before the terminal error frame

- **Where:** `src/utils/claude_oauth/bridge.py:1080-1088` (`_stream_buffered_with_retry._capture`)
- **Proof:** after retries are exhausted, if the final attempt saw an SSE `event: error` (but no
  `message_stop`), the whole buffer — partial content deltas **plus** the error frame — is
  returned as `("ok", buf)` and replayed to the client. This contradicts the function's own
  contract: *"the client only ever receives a COMPLETE response (or a clean error), never a
  truncated one."*
- **Why low:** `buf` is reset per attempt (only the final attempt's bytes are replayed), and the
  stream still terminates in upstream's own `event: error`, so a compliant client raises and does
  not record the turn as complete. Matches passthrough-proxy semantics; letter-of-contract
  violation only.

### L2. Buffered-path errors reach the client as HTTP 200 without structured cap metadata

- **Where:** `src/utils/claude_oauth/bridge.py:1098-1146`
- **Proof:** the buffer-and-retry path always responds `200` + `X-WCB-Bridge-Mode` header; the
  `X-WCB-Bridge-Error`, `Retry-After`, `X-WCB-Reset-At` headers and the `wcb_bridge` JSON block
  (produced by `_build_error_response` on the non-streaming/incremental paths) are never sent —
  only a bare SSE error frame with type + message.
- **Why low — impact claim from the initial review was WRONG and is corrected here:**
  `recovery.py` does not depend on those headers. It detects rate limits by exception class plus
  string match on `"rate_limit_error"` / `"subscription_cap"` (`recovery.py:119-131`) and gets
  the wait duration from the bridge's `/quota` endpoint, which is populated by the same
  classification (`last_cap_reset_at` / slot `exhausted_until` are set in `_capture` before the
  error is relayed). Pause/resume works. Remaining cost is observability/consistency only.

### L3. Per-request failover budget (3) is smaller than a large pool

- **Where:** `bridge.py` failover loops (`_forward_non_streaming:737`, `_stream_with_failover:951`,
  `_capture:1033`); `DEFAULT_MAX_INLINE_RETRIES = 3`
- **Proof:** each fresh (unmarked) cap consumes one retry, so a single request can traverse at
  most ~4 accounts. A 5-account pool where 4 accounts hit *previously unrecorded* caps in one
  request fails even though account 5 is healthy.
- **Why low:** once caps are marked, slot selection skips them at zero retry cost, so this only
  bites on a cold bridge with several simultaneously-capped accounts; `recovery.py` then retries
  with a pool-scaled budget (`_effective_max_retries`, `recovery.py:90-106`) and succeeds one
  pause later. Tunable via `WCB_CC_MAX_INLINE_RETRIES`.

### L4. Dead rotation API with a concurrency-stale index

- **Where:** `src/utils/claude_oauth/credentials.py:550-565` (`mark_current_exhausted`,
  `mark_current_invalid`, `_last_used_index`)
- **Proof:** grep across `src/`, `eval/`, `tests/`, `scripts/` finds **zero callers** of either
  method; `_last_used_index` is read nowhere else. Under concurrent requests the index is stale
  by design (last selection wins), so if anyone ever *did* call these they could mark the wrong
  account.
- **Why low:** no runtime impact today; delete or fix before first use.

---

## Cross-cutting: test coverage gap

`tests/test_claude_oauth_credentials.py` and `tests/test_claude_oauth_bridge.py` cover happy paths
only. There are **no tests** for: account failover, exhaustion attribution, refresh-token rotation
persistence (multi-process/flock), keychain pool parsing, or streaming truncation/replay. Every
issue above except L4 would have been caught by a targeted test. Recommended first additions:

1. C1 — refresh raises transient `CredentialsError` → slot must NOT become permanently invalid.
2. H1 — two providers sharing one pool file; assert the second refresh uses the on-disk (rotated)
   refresh token.
3. H2 — 429 arriving after the slot's token rotated → exhaustion must still be recorded.
4. M1 — `load_account_pool("keychain:Work")` must yield one keychain slot, not two file slots.

## Suggested fix order

| Order | Issue | Reason |
|-------|-------|--------|
| 1 | C1 | Everyday trigger, permanent damage, amplifies H1/H2 |
| 2 | H1 | One-line fix; prevents unrecoverable account loss |
| 3 | H2 | Restores the exhaustion-tracking invariant + fixes false comment |
| 4 | M1 | Small parser fix + test |
| 5+ | M2, L1–L4 | Opportunistic |
