# OAuth Rotation Hardening + Claude Fable 5 Enablement — Full Engineering Report

**Date:** 2026-07-21 · **Branch:** `master` (all changes currently uncommitted)
**Scope:** `src/utils/claude_oauth/*`, `src/utils/litellm_sidecar.py`,
`src/utils/litellm_usage_oauth_callback.py`, `src/utils/grading.py`,
`eval/run_batch.py`, `src/agents/openclaw/runner.py`, tests, docs.

---

## 1. Executive summary

Three goals were set: **(1)** enable Claude Fable 5 on the OAuth trajectory
path, **(2)** verify account rotation behaves correctly when subscription
limits are hit, **(3)** determine Linux support. All three are complete:

- **5 bugs fixed** (1 critical, 2 high from the rotation review; 2 more —
  1 critical, 1 high — found by a second adversarial review of our own new
  code and fixed the same day). Each has a root-cause analysis below.
- **Fable 5 wired end-to-end** across 6 files, with the entitlement
  **confirmed live**: a 1-token probe through the bridge's production
  transforms returned HTTP 200 from `api.anthropic.com` with
  `"model": "claude-fable-5"` on the Max-subscription OAuth token.
- **Linux: works by design** (file-based creds, `fcntl`, Dockerized bridge);
  all 48 OAuth tests additionally **executed green inside a Linux container**
  (kernel 6.12).
- **Verification:** full suite **3,922 passed**, failure set byte-identical to
  clean master (4 known environmental failures, stash-verified) — zero
  regressions. Smoke gate 6/6 green throughout.

---

## 2. How the bugs were found (methodology)

1. **Two-axis review** (standards + spec, parallel reviewers) of the OAuth
   rotation code (`credentials.py`, `bridge.py`, `errors.py`, `recovery.py`),
   followed by a **manual adversarial verification pass** — every finding was
   re-traced by hand against the code; 1 finding's impact was downgraded and 1
   sub-claim discarded as a false positive. Full catalog with proofs:
   `docs/OAUTH_ROTATION_ISSUES.md`.
2. Fixes were **planned before written**: a compatibility sweep enumerated
   every `CredentialsError` catch-site and marking-API caller repo-wide, and a
   design validator adversarially reviewed the proposed edits — catching two
   design flaws *before implementation* (§3.2 guard, §3.3 dedupe).
3. After implementation, a **second two-axis review of the diff itself** was
   run at maximum thoroughness. It found 2 further confirmed bugs (§3.4,
   §3.5) — both fixed and regression-tested the same session.

---

## 3. Bugs fixed — root cause, proof, fix, reasoning

### 3.1 C1 (CRITICAL): transient refresh failure permanently killed an account

**Root cause.** `MultiAccountCredentialProvider.get_access_token` caught *any*
`CredentialsError` from a slot and set `slot.invalid = True`. The refresh
routine raises `CredentialsError` for **both** unrecoverable failures (HTTP
4xx = refresh token revoked) and recoverable ones (network error after ~3s of
retries, upstream 5xx). The exception type carried no severity information, so
the pool had no choice but to treat everything as fatal.

**Proof.** Grep-verified: nothing in the run path ever calls `force_reload()`
on the multi-account provider — the only API that clears `invalid`. So a
DNS/network blip lasting a few seconds during a token refresh excluded that
account for the entire process lifetime; with a 1-slot pool the bridge
returned 401 for the remainder of the run.

**Fix + reasoning.** Introduce severity into the exception hierarchy:
`TransientCredentialsError(CredentialsError)` raised for network exhaustion,
5xx exhaustion, and non-JSON responses (interposing proxies/captive portals —
environmental, recoverable). The pool backs such a slot off for 60s
(`exhausted_until`) instead of invalidating. 4xx and protocol violations keep
the base class → permanent, unchanged. The subclass is caught **before** the
base clause (ordering is load-bearing) and is invisible to every existing
catch-site (all catch the base class — verified by repo-wide sweep). The
narrow scoping was deliberate: `test_load_account_pool_ignores_nonexistent_files`
*requires* that a missing-file slot still invalidates.

```diff
+class TransientCredentialsError(CredentialsError):
+    """A refresh failure that is likely to heal on its own (network outage,
+    upstream 5xx, garbled/non-JSON proxy response). ..."""
...
             if attempt >= max_attempts:
-                raise CredentialsError(
+                raise TransientCredentialsError(
                     f"OAuth refresh network error after {attempt} attempts: {e}"
                 ) from e
...
+            except TransientCredentialsError as e:
+                with self._lock:
+                    slot.exhausted_until = max(
+                        slot.exhausted_until,
+                        time.time() + TRANSIENT_REFRESH_BACKOFF_SECONDS,
+                    )
             except CredentialsError:
                 with self._lock:
                     slot.invalid = True
```

**Tests:** transient → slot NOT invalid, backoff set, failover to slot 2,
selectable again after the window; 4xx → invalid (preserved); missing file →
invalid (preserved). (`tests/test_claude_oauth_rotation.py`)

---

### 3.2 H1 (HIGH): rotated refresh token lost across processes

**Root cause.** Anthropic **rotates the refresh token on every exchange** — a
consumed refresh token is dead. In `_FileCredentialProvider`, after acquiring
the cross-process flock the code re-read the pool file but adopted the on-disk
pair **only if it was non-expired**. An expired-but-*newer* on-disk pair
(written by another process that had rotated the token) was discarded, and the
refresh ran on the stale in-memory pair — whose refresh token the other
process had already consumed → upstream `invalid_grant` → account dead until a
manual re-login.

**Proof.** Traced directly in the pre-fix code:

```python
fresh = self._load()
if not fresh.is_expired():      # expired-but-newer pair discarded here
    self._creds = fresh
    return ...
...
self._creds = refresh_credentials(self._creds)   # <-- stale in-memory pair
```

Trigger: process A refreshes and rewrites the file; >1h later (both pairs now
expired — e.g. overnight idle) process B refreshes with A's consumed token.
Multi-process shared pools are exactly the documented use case the flock
exists for. *Corroborating live evidence:* the pool file on this machine was
found in exactly this dead state (`invalid_grant` from
`console.anthropic.com`) — consistent with the pool sharing a credential with
the local `claude` CLI, whichever refreshed first consuming the other's token.

**Fix + reasoning.** Adopt the on-disk pair **iff it is at least as new**
(`fresh.expires_at_ms >= self._creds.expires_at_ms`), then refresh whatever is
newest. The naive fix ("always adopt disk") was **rejected by the design
validator** because it re-introduces the same bug through the opposite door:
the write-back at the end of refresh swallows `OSError`, so after a failed
write-back *memory* holds the only live pair and disk is stale — unconditional
adoption would burn the token again. Guard analysis: if memory is expired and
disk is not, disk is necessarily newer (same clock/leeway) → the
concurrent-refresher fast path always still adopts; the guard bites only when
both are expired and memory is strictly newer — precisely the failed-write-back
case.

```diff
                 try:
                     fresh = self._load()
-                    if not fresh.is_expired():
-                        self._creds = fresh
-                        return self._creds.access_token
                 except CredentialsError:
                     pass
+                else:
+                    if fresh.expires_at_ms >= self._creds.expires_at_ms:
+                        self._creds = fresh
+                if not self._creds.is_expired():
+                    # Another process already rotated -- no second refresh.
+                    return self._vend_locked()
                 _LOG.info("Refreshing OAuth token from %s", self._path)
                 self._creds = refresh_credentials(self._creds)
```

**Tests:** refresh grant captured by fake httpx must carry the **on-disk**
refresh token when disk is newer (T3); the **in-memory** token when memory is
newer — the failed-write-back scenario (T3b); non-expired disk pair adopted
with **zero** refresh calls (concurrent-refresher fast path).

---

### 3.3 H2 (HIGH): rate-limit attribution silently dropped after token rotation

**Root cause.** When the bridge classifies an upstream 429 as a subscription
cap, it attributes it to a pool slot by matching the erred token against each
provider's **current** token's first 20 characters. All OAuth tokens share the
13-char literal `sk-ant-oat01-`. If the slot's token was refreshed between the
request being issued and its 429 arriving (realistic: single Fable/Opus turns
run 10+ minutes, and the agent + judge share the bridge), the current token no
longer matches → `mark_account_exhausted` returned silently → the cap was
never recorded → failover re-selected the same capped account (the fresh token
also defeated the bridge's burned-slot guard). A comment claimed matching on
`slot.last_token` — **a field that did not exist anywhere** (grep-verified
comment/code drift).

**Fix + reasoning.** Give every provider a deduped history of its last 8
*distinct* vended tokens, populated through a single `_vend_locked()` helper
that every vend return-path routes through (structural guarantee — no
scattered remember-calls to forget). Attribution does an **exact** match
against the history first; the old prefix match remains as fallback (required
by existing tests using short fake tokens and duck-typed fake providers). The
**dedupe is load-bearing** (validator-caught): a plain `deque(maxlen=8)`
appended on every vend would be flushed by 8 rapid requests on the same token,
evicting the erred token before its 429 arrives — silently re-introducing the
bug under load. The false bridge comment was rewritten to describe the real
mechanism.

```diff
+        self._recent_tokens: deque[str] = deque(maxlen=_TOKEN_HISTORY_SIZE)
+
+    def _remember_locked(self, token: str) -> None:
+        if token and token not in self._recent_tokens:   # dedupe: load-bearing
+            self._recent_tokens.append(token)
+
+    def _vend_locked(self) -> str:
+        token = self._creds.access_token
+        self._remember_locked(token)
+        return token
+
+    def knows_token(self, token: str) -> bool:
+        with self._lock:
+            return bool(token) and token in self._recent_tokens
...
     def _find_slot_by_prefix_locked(self, token_prefix):
+        for slot in self._slots:                          # exact pass first
+            knows = getattr(slot.provider, "knows_token", None)
+            if callable(knows) and knows(token_prefix):
+                return slot
         for slot in self._slots:                          # prefix fallback (verbatim)
```

**Tests:** vend t1 → real refresh to t2 → `mark_account_exhausted(t1)` still
lands (only the exact pass can match — asserted); 10 same-token vends + one
rotation → both tokens still known (dedupe guard).

---

### 3.4 R1 (CRITICAL, found by the post-implementation review): Fable 5 was silently unreachable

**Root cause.** `src/agents/openclaw/runner.py` rewrites **every** Anthropic
model to the internal id `claude-opus-4-6`:

```python
is_anthropic_model = "claude" in model_id.lower()
openclaw_model_id = "claude-opus-4-6" if is_anthropic_model else model_id
```

This is a documented workaround for openclaw 2026.3.11's *hardcoded*
extended-thinking allowlist (only recognized ids get a thinking directive).
But it meant `--model claude-fable-5` would pass our new harness allowlist,
then the agent would request `anthropic/claude-opus-4-6` from the sidecar →
the **opus** OAuth block → **the run executes Opus while the output directory,
score.json and usage accounting all claim Fable 5.** Our sidecar/audit wiring
was correct but unreachable. No test caught it because the rewrite lives in
the container-launch path.

**Proof.** Claim from the review was independently re-verified by reading
`runner.py:908-935` before fixing. The rewrite is unconditional on `"claude" in
model_id`.

**Fix + reasoning.** Exempt fable from the rewrite — the rewrite exists
*solely* to trigger openclaw's thinking directive, and Fable does not need
one: its thinking is always-on server-side. This exposed a knock-on we also
fixed: with no thinking directive from openclaw, Fable's `display` defaults to
`"omitted"` → every recorded thinking block would have **empty text** (the
exact trajectory-quality failure the opus dash-shim was invented to solve).
The bridge's fable branch therefore now **injects**
`{"type": "adaptive", "display": "summarized"}` when the field is absent.

```diff
-            openclaw_model_id = "claude-opus-4-6" if is_anthropic_model else model_id
+            if is_anthropic_model and "fable" not in model_id.lower():
+                openclaw_model_id = "claude-opus-4-6"
+            else:
+                openclaw_model_id = model_id
```

```diff
         if isinstance(thinking, dict):
             ...
             body["thinking"] = {"type": "adaptive", "display": display}
+        else:
+            body["thinking"] = {"type": "adaptive", "display": "summarized"}
         return body
```

**Residual risk (honest):** openclaw's behavior with an allowlist-unknown
model id is only fully provable by the first real Fable run — the API side is
probe-verified, the openclaw side is reasoning from its documented gating.

---

### 3.5 R2 (HIGH, found by the post-implementation review): the C1 fix could loop forever

**Root cause.** My original C1 fix retried via **recursion**, relying on
"every level marks its slot unavailable" for termination. But the 60s
transient backoff is **shorter** than a worst-case refresh attempt (~93s: 3 ×
30s httpx timeouts + 1s+2s sleeps). Under a persistent slow-timeout outage
with ≥2 slots, slot A's backoff expires while slot B is still failing → A is
selectable again → unbounded ping-pong recursion; the "all accounts exhausted"
raise never fires. My own tests missed it because they simulate *fast*
`ConnectError` failures with a no-op sleep.

**Fix + reasoning.** Replace recursion with an iterative loop carrying a
per-call tried-set keyed by `id(slot)` (labels can collide on pathological
duplicate pool entries): each slot gets at most **one attempt per call**,
guaranteeing termination bounded by pool size regardless of backoff/refresh
timing arithmetic. Chosen over "raise the backoff above 93s" because it is
timing-independent — it cannot be un-fixed by a future timeout change.

```diff
-        try:
-            return slot.provider.get_access_token()
-        except CredentialsError:
-            ...
-            return self.get_access_token()          # unbounded recursion
+        tried: set[int] = set()
+        while True:
+            with self._lock:
+                slot, idx = self._select_slot_locked()
+                if id(slot) in tried:
+                    raise CredentialsError(
+                        f"all {len(tried)} available accounts failed this call")
+                tried.add(id(slot))
+            try:
+                return slot.provider.get_access_token()
+            except TransientCredentialsError: ...    # mark + loop
+            except CredentialsError: ...             # invalidate + loop
```

**Test:** a clock-warping regression test advances `time.time()` by 40s per
call so each slot's backoff has always "expired" by the time the other fails —
the exact ping-pong scenario. Asserts termination via `CredentialsError` and
that neither slot was permanently invalidated.

---

## 4. Claude Fable 5 enablement (goal 1)

Model facts used (from the Anthropic API reference): id `claude-fable-5`,
$10/$50 per MTok (cache read $1, cache write $12.50), 1M context, thinking
always-on **adaptive-only** — `enabled+budget_tokens` and `disabled` both
return 400; `display` defaults to `omitted`; can return
`stop_reason: "refusal"`; requires 30-day data retention.

| # | Change | File | Why (what breaks without it) |
|---|--------|------|------------------------------|
| 1 | `claude-fable-5` model block on the OAuth branch, **no `thinking` key** | `litellm_sidecar.py` | No route → LiteLLM 400. A thinking directive copied from opus would 400 upstream on every request |
| 2 | Model-aware thinking normalization (fable → adaptive; inject `summarized` when absent) | `bridge.py` | The blanket opus rewrite to `enabled+budget_tokens` would 400 every fable request; absent-field default records empty reasoning |
| 3 | `"claude-fable-5"` added to `LITELLM_MODEL_IDS` | `eval/run_batch.py` | The harness **silently rewrites** unknown models to `claude-opus-4.7` — fable would run as opus with no error |
| 4 | Audit gate widened + fable price table | `litellm_usage_oauth_callback.py` | Gate was literally `"opus" in model` — fable rows would never be written to `usage_oauth.jsonl` (invisible cost audit); rates would otherwise be priced at opus levels |
| 5 | Fable exempted from the openclaw dash-shim rewrite | `src/agents/openclaw/runner.py` | §3.4 — without it, "fable" runs are opus runs |
| 6 | Fallback cost entry `(1e-5, 5e-5)` | `grading.py` | chat.jsonl heuristic path would flag fable usage `cost_unpriced` |

Key diffs (full diffs available via `git diff HEAD`):

```diff
 LITELLM_MODEL_IDS = {"claude-opus-4.8", "claude-opus-4.7", "gpt-5.5"}
+LITELLM_MODEL_IDS = {"claude-opus-4.8", "claude-opus-4.7", "claude-fable-5", "gpt-5.5"}
```

```diff
 def _is_oauth_route(model: str) -> bool:
     if not model:
         return False
-    return "opus" in model.lower()
+    m = model.lower()
+    return "opus" in m or "fable" in m

+_ANTHROPIC_FABLE_PRICE = {
+    "input": 1e-5, "output": 5e-5,
+    "cache_read": 1e-6, "cache_creation": 1.25e-5,
+}
```

```diff
+    model = body.get("model")
+    if isinstance(model, str) and "fable" in model.lower():
+        thinking = body.get("thinking")
+        if isinstance(thinking, dict):
+            display = thinking.get("display")
+            if display not in ("summarized", "omitted"):
+                display = "summarized"
+            body["thinking"] = {"type": "adaptive", "display": display}
+        else:
+            body["thinking"] = {"type": "adaptive", "display": "summarized"}
+        return body
```

### Live entitlement proof

A probe (`max_tokens: 1`, request built with the bridge's own production
transforms — system prefix, billing attribution, CLI-disguise headers — sent
with the real pool credential):

```
{"model": "claude-opus-4-8", "status": 200, "resp_model": "claude-opus-4-8", "stop_reason": "max_tokens"}
{"model": "claude-fable-5",  "status": 200, "resp_model": "claude-fable-5",  "stop_reason": "max_tokens"}
```

The Max subscription **is entitled to Fable 5** and the transforms are
accepted for it. Operational caveats (pricing 2× opus, refusal stop-reason,
retention requirement, minutes-long turns) are documented in
`docs/FABLE5_OAUTH.md`.

---

## 5. Rotation under account limits (goal 2)

Verified correct (traced in code + covered by tests): 429 classification
(cap iff `Retry-After ≥ 60s` or tokens-remaining = 0), per-slot exhaustion
marking with reset timestamps, first-available selection skipping capped
slots, transparent mid-request failover, structured error when the whole pool
is capped, and `recovery.py` pause-and-resume with pool-scaled retries. The
five bugs in §3 were the reliability edges of this machinery; with them fixed,
rotation is dependable for single- and multi-account pools. Remaining known
non-blocking issues (M1/M2/L1–L4 — keychain-only or low severity) are
cataloged with proofs in `docs/OAUTH_ROTATION_ISSUES.md`, deliberately
deferred.

---

## 6. Linux (goal 3)

**Works by design** — evidence table and EC2 checklist in
`docs/OAUTH_LINUX.md`. No code changes were needed (Linux support pre-exists:
plaintext credential file path, `fcntl`, Darwin-gated keychain no-ops, commit
`0d6624a`). Empirical backing: **all 48 OAuth tests executed green inside a
`python:3.12-slim` container** (Linux kernel 6.12) — exercising real `fcntl`
locking and the no-keychain fallbacks. Remaining: one end-to-end smoke run on
the target EC2 box.

---

## 7. Verification matrix

| Check | Result |
|---|---|
| OAuth suites (rotation, credentials, bridge, sidecar-config, usage-callbacks) | **193 passed** |
| Smoke gate `test_drift_plane_smoke.py` (CLAUDE.md ship gate) | **6 passed** |
| Full unit suite | **3,922 passed**, 7 skipped, 66 xfailed |
| Failure-set diff vs clean master (stash/rerun) | **Identical** — same 4 known environmental failures, zero regressions |
| OAuth tests on Linux (Docker, kernel 6.12) | **48 passed** |
| Live entitlement probe (opus control + fable) | **Both HTTP 200**, correct `resp_model` |
| Post-implementation adversarial review of the diff | 2 confirmed bugs found (§3.4, §3.5) → fixed → all checks re-run green |

New tests added: **20** (10 rotation incl. the ping-pong termination test;
10 fable: sidecar block shape, no-thinking invariant, audit gate, price
table, 4 normalization cases).

---

## 8. Outstanding items / recommendations

1. **First real Fable-5 run** — validates openclaw's handling of an
   allowlist-unknown model id (§3.4 residual risk) and the fable rows in
   `usage_oauth.jsonl`. Recommended before any batch.
2. **Merge `aa8b792`** ("judge council all-abstain on the OAuth pathway",
   unmerged on `claude_oauth_pathway`) before *scored* Fable runs, or
   trajectories may grade rubric-zero.
3. **Pre-existing gap (not introduced by this work):** `claude-opus-4.8` is in
   `LITELLM_MODEL_IDS` but has **no OAuth sidecar block** — non-openclaw
   backends selecting it on the OAuth path would 400.
4. **Credential hygiene:** the pool sharing an account with the local `claude`
   CLI means either side's refresh consumes the other's token (this bit us
   live — §3.2). Recommend a dedicated Max account for the pool.
5. Deferred rotation issues M1/M2/L1–L4 (`docs/OAUTH_ROTATION_ISSUES.md`).
6. EC2 end-to-end smoke run (`docs/OAUTH_LINUX.md` checklist).
7. Style nits acknowledged from review, not blocking: `"fable"` substring
   check in 3 places (2 are separate processes); fable price in 2 tables
   (grading fallback + audit callback) — cross-referenced here.

## 9. Change manifest

**Modified (11):** `eval/run_batch.py`, `src/agents/openclaw/runner.py`,
`src/utils/claude_oauth/{__init__,bridge,credentials}.py`,
`src/utils/{grading,litellm_sidecar,litellm_usage_oauth_callback}.py`,
`tests/{test_claude_oauth_bridge,test_litellm_sidecar_config,test_usage_callbacks}.py`
**New (5):** `tests/test_claude_oauth_rotation.py`,
`docs/{OAUTH_ROTATION_ISSUES,OAUTH_LINUX,FABLE5_OAUTH,SESSION_REPORT_OAUTH_FABLE5}.md`
Total: **291 insertions / 30 deletions** in source+tests. All uncommitted,
pending commit approval.
