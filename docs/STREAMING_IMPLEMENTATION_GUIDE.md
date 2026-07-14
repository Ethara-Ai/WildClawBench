# Live LLM Streaming + TUI — Implementation Knowledge Base

**Audience:** engineers adding live LLM-response streaming and/or a terminal
dashboard to an agent-harness-style project (batch runner → LLM agent →
graders/post-processing). This is the distilled, transferable version of the
WildClawBench implementation (branch `master`, 2026-07) — validated end-to-end
on real trajectory runs. Reference implementation file map at the bottom.

---

## 1. The problem shape

You have a pipeline: `agent run → scoring/judging → artifacts → delivery`.
The agent's LLM calls already stream on the wire (SSE), but nobody sees them —
the operator stares at a silent terminal for 10–30 minutes. You want:

1. live tokens (thinking + text) on screen while the trajectory generates,
2. live grader/judge output,
3. optionally a full-screen TUI dashboard with the stream inside it,
4. **zero** change to how the trajectory is generated, scored, or delivered,
5. no race conditions between the display and the downstream pipeline.

A previous naive attempt at this class of feature failed with races (scoring
started before the stream finished). The architecture below makes that
impossible **by construction**, not by careful sequencing.

---

## 2. The core architecture: two planes

```
 AUTHORITATIVE PLANE (unchanged): transcript → grading → artifacts → delivery
      triggers on process/call COMPLETION, reads its own files, as before.

 OBSERVABILITY PLANE (new, write-only):
      taps at points where chunks physically exist in real time
          └──▶ append-only event feed (stream.jsonl)
                  └──▶ consumers: terminal renderer / TUI pane / tail -f /
                       future web frontend — NONE of which anything
                       authoritative reads or waits on.
```

**The golden rule (R1):** nothing in the authoritative plane may read the
feed or wait on any display component. Downstream steps keep triggering on
what they always triggered on (process exit, call return). Then a slow, dead,
or corrupt stream can only ever truncate the *display*, never a score or a
deliverable. This is the entire race-condition answer. Enforce it with a
static test that greps the grading/packaging sources for feed references.

### The six design rules (copy these into your plan verbatim)

- **R1** — Authoritative plane never reads/waits on the observability plane.
- **R2** — Every tap is fail-open: any exception in tap code self-disables
  the tap for the rest of that request and the chunk still flows.
- **R3** — Explicit `message_stop`/`error` sentinel per request; display
  consumers are stopped with a **bounded** join (≤5s) at teardown — teardown
  may wait briefly for the display, grading never does.
- **R4** — Where a delta loop feeds a parser (judges), keep
  accumulate-then-parse in the same loop; streaming is *emission during
  accumulation*, never a restructure.
- **R5** — **Pass-the-original-object.** Inline taps forward the *exact*
  chunk object/bytes they received; extraction only reads. Rationale: R2
  guards the *raising* bug class; the worst non-raising bug is silent chunk
  mutation/drop that degrades the agent invisibly. R5 kills that class by
  construction. Enforce with object-identity tests.
- **R6** — The gate is **batch-scoped and default-OFF**. One env flag decided
  once at batch setup controls callback registration and container mounts;
  flag off ⇒ configs and containers byte-identical to pre-feature builds
  (verify literally: build the config both ways and diff). The launcher
  script is authoritative over the flag — it *unsets* an inherited env var
  when conditions aren't met, so stale environments can't leak the feature on.

---

## 3. The event contract (`stream.jsonl`) — the portable API

One JSON object per line, append-only. This schema is the entire interface
between producers and consumers; a future web frontend consumes it unchanged.

```json
{
  "ts": 1783075200.123,          // unix seconds
  "seq": 42,                      // monotonic PER request_id (lets a web UI reorder)
  "source": "agent" | "judge:<name>" | "testgen" | ...,
  "request_id": "<opaque id, one per LLM call>",
  "model": "<model label>",
  "kind": "text" | "thinking" | "status",
  "event": "message_start" | "delta" | "message_stop" | "error" | "status",
  "delta": "<text fragment or status message>"
}
```

Writer rules that made concurrent producers safe in practice:
- single-line `O_APPEND` writes under a per-process lock — host processes and
  containers (via bind mount) can append to the same file; readers skip any
  torn line instead of erroring;
- **size cap** (e.g. 64 MiB/feed) — the writer latches off past the cap with
  one stderr warning; the feed lives in a gitignored work dir;
- **sink separation** — the feed never shares a file/schema with billing or
  telemetry logs (if your project tracks usage separately, keep it separate);
- **filter non-display traffic** at the producer: health-check pings,
  embeddings, transcription calls.

---

## 4. Taps: put them where chunks exist in real time

This is the load-bearing analysis step. Trace where response bytes physically
flow, and tap the *earliest* point you own that sees them live:

### Pattern A — LLM proxy callback (our main path: LiteLLM proxy)
If your agent talks through a LiteLLM proxy, `CustomLogger.
async_post_call_streaming_iterator_hook` wraps every streamed response —
including the `/v1/messages` (anthropic-messages) route. The tap:

```python
async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
    live = _should_emit(request_data)     # filter pings/etc; any error -> live=False
    async for chunk in response:
        if live:
            try:    _extract_and_write(chunk)   # reads only
            except Exception: live = False      # R2: self-disable
        yield chunk                              # R5: ALWAYS the original object
```

Mechanics (mirror your proxy's existing callback pattern): mount the module
read-only into the container, mount a writable feed dir, register in the
proxy config's callbacks list behind the R6 flag.

**Chunk-shape caveat:** on the anthropic route chunks are event dicts
(`content_block_delta` → `delta.type` ∈ `text_delta|thinking_delta`,
`message_start/stop`); on OpenAI-style routes they're `ModelResponseStream`
(`choices[0].delta.content` / `.reasoning_content`). Parse both defensively;
unknown shapes yield through untouched. Also dedupe `message_start`: emit it
once per request at the hook level and *suppress* the chunk-derived one.

**Verification recipe (before writing code):** (1) `hasattr(CustomLogger,
'async_post_call_streaming_iterator_hook')` inside your exact pinned proxy
image; (2) grep the image's own proxy source to confirm your route's
streaming return path wraps responses in that hook; (3) zero-cost live smoke:
a proxy config with a `mock_response` model streams fake chunks through the
REAL pipeline including your callback — no upstream call, proves
registration → hook → parse → feed end-to-end. (Note: mock models do NOT
stream on the anthropic route, only on `/v1/chat/completions` — the anthropic
route needs one real streamed request or source-level verification.)

### Pattern B — tee inside a buffering middlebox
If any component **buffers** the stream before the client sees it (we had an
OAuth bridge with buffer-and-retry: it collects the whole upstream SSE and
replays a complete response so the client never sees a truncated turn), then
a proxy-level tap behind it only sees an **end-of-turn burst**. Real-time
tokens exist only *inside* that component — tee there:

- feed the tee each chunk beside the existing bookkeeping (observe-only, R5);
- incremental SSE frame parser with a carry buffer (frames split on `\n\n`;
  a `data:` line split across chunks must still parse);
- **retry semantics:** when the middlebox re-issues the upstream request, the
  partial turn the feed saw is void — emit `error("retrying (attempt N)")`,
  reset, and let the new attempt re-emit `message_start`. The renderer
  replaces the partial turn. The client-facing contract stays byte-identical;
- an upstream SSE `error` frame is terminal for the display request — latch
  it so a later cleanup can't append a `message_stop` after the `error`;
- **do not register the proxy tap on this path** — you'd double-emit the
  burst. One real-time tap per path.

### Pattern C — host-side delta loops (judges / any direct SSE consumer)
Graders that already consume SSE and accumulate text need ~3 lines per delta:
emit `message_start` after the connection succeeds (not before — avoids
phantom starts on connection failure), one `delta` per chunk in the existing
loop, `message_stop` after, `error` on the raise paths. R4: the joined string
still goes to the parser exactly as before. If members run in parallel
threads, the feed writer's lock keeps lines atomic.

---

## 5. The renderer (terminal + TUI)

One consumer thread with **one classification brain** and **two output
sinks** — never two renderers:

- **State/classification** (shared by both sinks): main-session vs
  concurrent sub-agent requests (first agent request = main; others render as
  one status line each — token-interleaving parallel calls is unreadable);
  per-source line-buffering for judges (whole verdict lines, never
  interleaved mid-line); a thinking on/off toggle env var.
- **tty sink** (no dashboard): write raw deltas; open `/dev/tty` FIRST —
  under a launcher that pipes stdout through `tee` into logs, stdout is not a
  tty; writing to the controlling terminal keeps logs free of token spew.
  When neither is a tty (headless), degrade to one summary line every ~5s.
- **bus sink** (dashboard active): see §6.

**Lifecycle:** start the renderer thread right before the agent phase; stop it
AFTER grading (so judge deltas render) with `stop()` = flag + `join(≤5s)`
(R3). The renderer is a daemon thread whose `run()` swallows everything.

**The waiting-state lesson (we shipped this bug — don't):** the first design
fell back to tailing the agent's turn-level log after a 30s "feed staleness"
window, one-way. The 30s clock started at renderer start — during container
setup, minutes before the first LLM call — so a perfectly healthy token feed
was never rendered. Correct design: **no clock, no one-way door.** The loop
watches feed + narration log simultaneously; narration renders only while no
agent token has arrived; the FIRST agent feed row promotes to token rendering
permanently (narration duplicates each turn's final text — showing both
doubles content). Non-agent feed rows (grader status etc.) must not end the
narration phase. Encode the exact scenario as a regression test.

---

## 6. TUI integration (Textual)

A full-screen TUI **owns every character cell**. Any other writer printing to
the same terminal shreds the canvas. Rules:

1. **Detect dashboard ownership** via a process-global flag the TUI sets on
   mount/unmount (`is_dashboard_active()`), checked fail-open (absent/broken
   UI package ⇒ no dashboard ⇒ render to tty normally).
2. Under the dashboard, the renderer switches its sink to the TUI's **event
   bus** — same thread, same tail loops, same classification; only the final
   write differs. Bus contract: emitting with no subscriber is a no-op, so
   non-TUI runs are untouched by construction.
3. **Flush-buffer agent deltas for the bus** (a `RichLog` appends entries, not
   characters): flush on newline, ~80 chars at a word break, or ~0.4s idle.
   Judge/status lines emit immediately. First publish failure latches the
   sink off (R2).
4. Dashboard side: one new event kind (`EV_TOKEN`, payload `{style, text}` —
   producer sends display-ready text), one `RichLog` pane, one handler in the
   existing event dispatch. Events from worker threads marshal onto the UI
   thread via `call_from_thread` (Textual's standard pattern).
5. **Escape model output** before writing to the pane (a pure
   `token_markup()` fn): otherwise LLM text containing `[bold red]...`
   injects live markup into your canvas. Unit-test with a hostile payload.
6. TUI activation should require a real tty (`stdout.isatty()`), so launcher
   scripts that tee output silently fall back to plain logging — document
   that the TUI needs direct invocation, not the tee'd wrapper.

---

## 7. Gating & ops

- One master env flag (`WCB_STREAM=1` style) + a launcher flag (`--stream`),
  **default off**. Decided once per batch (R6) *before* any shared
  infrastructure boots (it controls container mounts).
- **Single-run gate:** token-level rendering only for 1 task × 1 model ×
  1 rep foreground; under fan-out, one shared feed interleaves runs
  unreadably — degrade to per-run narration. Launcher warns + **unsets** the
  env var when downgrading.
- Feed dirs live in the gitignored work area; verify your bundler/delivery
  never picks them up (ours copies only named files — we added a test that a
  feed file in a run dir never reaches a bundle).
- Thinking deltas dominate volume on high-reasoning models — default-visible
  but dim, with an env toggle to hide.

---

## 8. Testing strategy (what actually caught bugs)

| Test class | What it proves |
|---|---|
| **Object-identity passthrough** (healthy / broken-writer / opaque-chunk) | R5 — taps can't mutate or drop what they forward |
| **Faked-upstream e2e of the buffering tee** | client bytes byte-identical with tee on/off/across a mid-stream drop+retry; feed shows error→fresh start on retry |
| **Fail-open** (unwritable feed, raising publisher) | R2 — never raises into the pipeline, self-disables |
| **Static R1 assertion** | grading/packaging sources contain no feed references |
| **Config byte-identity, flag off** | R6 — build proxy config with and without the flag; the only diff is the callback entry |
| **Grader parse parity** | R4 — verdict parse result identical with emitter on/off |
| **Renderer**: bounded stop, torn-line skip, sub-agent split, thinking gate, waiting-state handover | display correctness incl. the shipped-bug regression |
| **Headless TUI e2e** (`app.run_test()`) | worker-thread event → marshal → pane receives entries; tokens never leak into the log pane |
| **Zero-token live smoke** (`mock_response` model in the real proxy image) | registration → hook fires → parse → feed, no upstream calls |
| **Full-suite failure-set parity** | run the whole suite on a pristine baseline worktree and on your tree; diff the *names* of failures — zero new = zero regressions (never trust the count alone) |

Test-harness pitfalls we hit (avoid re-discovering):
- **`call_from_thread` AB-BA deadlock:** in a Textual `run_test`, never block
  the event-loop thread (e.g. `thread.join()`) while a worker is inside
  `call_from_thread` — loop waits for thread, thread waits for loop. Await
  first, join after. Diagnose hangs with `faulthandler.dump_traceback_later`.
- **Textual eats stdout** during `run_test` — pytest's terminal output goes
  missing; use `--junitxml` for trustworthy results.
- Environment-dependent tests (e.g. "repo has no .env") differ between a
  pristine worktree and a working checkout — always baseline-compare on the
  same machine state.

## 8b. Validation on the first real run — measurable criteria

- **Live vs burst:** per request, `ts(last row) − ts(first row)` must be
  comparable to the call's duration (a spread near 0s on a long turn = an
  end-burst, i.e. you tapped behind a buffer — see Pattern B).
- Post-run: all artifacts present and schema-valid; scores computed normally.
- **Grading-parity control that survives nondeterminism:** you cannot rerun
  an agent and expect equal scores. Instead re-run *only* the grading step on
  the SAME run dir with the flag on and off → identical verdicts.
- Kill-switch: run without the flag → inspect containers → no stream mounts,
  no callback, no feed dir.
- Chaos: make the feed dir read-only mid-run → run completes, scores normal.

---

## 9. Battle scars (every bug we actually hit, with the fix)

| Bug | Symptom | Fix |
|---|---|---|
| Premature one-way fallback | pane showed narration; 1,748 token rows invisible | waiting-state design (§5); regression test |
| Duplicate `message_start` (hook + chunk both emit) | renderer mislabeled main turn as a sub-agent | suppress chunk-derived start; renderer idempotent on duplicate starts |
| Tee `error` frame not latched terminal | `message_stop` after `error` → phantom request in display | latch stopped on SSE error |
| Stale env flag leaked into fan-out | token mode on for parallel batches | launcher **unsets** the env var whenever conditions fail (R6) |
| Tokens spewing into tee'd logs | multi-MB run logs | `/dev/tty`-first output; summary mode when no tty |
| TUI + renderer both writing the terminal | shredded canvas | `is_dashboard_active()` + bus sink (§6) |
| Model output injecting Rich markup | styled garbage in the pane | escape in `token_markup()`; hostile-payload test |
| Proxy behind a buffering middlebox | "live" tap only saw end-of-turn bursts | tap inside the buffer (Pattern B); disable the proxy tap on that path |
| Test-only `call_from_thread` deadlock | headless TUI test hung forever | never block the UI loop on a worker; faulthandler to find it |
| (env, not streaming) macOS python.org Python has no CA store | host-side graders die `CERTIFICATE_VERIFY_FAILED` while curl/containers work | `SSL_CERT_FILE` → certifi bundle in the env file |

---

## 10. Porting checklist for a new project

1. Map every AI call site; for each, find where response chunks exist in
   real time (proxy? middlebox? host loop?) → choose Pattern A/B/C per path.
   One real-time tap per path, never two.
2. Write the plan with R1–R6 verbatim; get the "worst case = degraded
   display, never a broken run" property agreed before coding.
3. Verify your proxy hook exists in your *exact pinned* image + fires on
   *your* route (source grep + mock_response smoke) before building on it.
4. Implement: feed schema (§3) → emitter helper (fail-open, seq, cap) →
   taps → renderer (tty sink, waiting-state, degrade rules) → TUI bus sink +
   pane. Default-off flag at every layer.
5. Tests from the §8 table — identity, fail-open, static R1, byte-identity,
   parity method. Baseline-diff the full suite.
6. First real run: measure timestamp spread (§8b), keep the feed as your
   diagnostic (we debugged a "blank pane" live, mid-run, purely by reading
   the feed — the run was never at risk).
7. Only after the real run converts your last assumptions into facts, enable
   by default / build the web consumer (the feed schema is already the API).

---

## 11. Reference implementation (WildClawBench `master`)

| Piece | File |
|---|---|
| Feed emitter (host) | `src/utils/stream_events.py` |
| Proxy tap (Pattern A) | `src/utils/litellm_stream_callback.py` (+ registration/mounts in `src/utils/litellm_sidecar.py`) |
| Buffering-middlebox tee (Pattern B) | `src/utils/claude_oauth/stream_tee.py` + hooks in `src/utils/claude_oauth/bridge.py` (oauth branch/master) |
| Grader delta emits (Pattern C) | `src/utils/grading.py` (`_call_judge_bedrock`, `_call_judge_openai`); heartbeats in `src/utils/testgen/generator.py` |
| Renderer (tty + bus + waiting state) | `src/utils/stream_renderer.py` |
| TUI: bus, pane, markup | `src/utils/ui/events.py` (`EV_TOKEN`), `src/utils/ui/tui.py` (`#stream` pane, `token_markup`) |
| Wiring & gate | `eval/run_batch.py` (`_setup_litellm_and_mocks`, `run_single_task`), `eval/bootstrap_sidecar.py`, `script/run.sh` (`--stream` gate) |
| Tests | `tests/test_stream_events.py`, `test_litellm_stream_callback.py`, `test_stream_renderer.py`, `test_tui_stream_pane.py`, `test_claude_oauth_stream_tee.py` |
| Full design + decisions log | `docs/STREAMING_PLAN.md` |
