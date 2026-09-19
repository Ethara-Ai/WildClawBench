"""LiteLLM proxy callback that writes real per-request usage to a JSONL log.

Mounted into the LiteLLM sidecar container at /app/litellm_usage_callback.py and
referenced from the proxy YAML as:

    litellm_settings:
      callbacks: ["litellm_usage_callback.proxy_handler_instance"]

Each successful upstream call appends one JSON row with the real provider-side
token counts and cost. The host-side reader (`extract_usage_from_litellm_log` in
`src/utils/grading.py`) filters by timestamp window per task.

This bypasses openclaw's internal LiteLLM provider, whose `chat.jsonl` usage
fields are always zero on this image build — every cost was previously coming
from an `len(text)//4` heuristic flagged as `usage_source: estimated`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass


_PATH = os.environ.get("LITELLM_USAGE_LOG_PATH", "/var/litellm_usage/usage.jsonl")
_LOCK = threading.Lock()

# Rate-limit usage-invariant warnings to once per (model, UTC date) so a
# persistent upstream mis-report cannot flood the sidecar's stderr/gateway.log.
_WARN_SEEN: set[tuple[str, str]] = set()


def _warn_once_per_day(model: Any, fmt: str, *args: Any) -> None:
    key = (str(model or ""), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if key in _WARN_SEEN:
        return
    _WARN_SEEN.add(key)
    sys.stderr.write(f"[litellm_usage_callback] WARN model={model!r}: " + (fmt % args) + "\n")


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    # ModelResponse.usage is a Pydantic Usage object; .dict()/.model_dump() both work.
    for method_name in ("model_dump", "dict"):
        meth = getattr(usage, method_name, None)
        if callable(meth):
            try:
                result = meth()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    fallback = getattr(usage, "__dict__", {}) or {}
    return fallback if isinstance(fallback, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_preflight_ping(kwargs: dict) -> bool:
    # The sidecar startup probe at src/utils/litellm_sidecar.py::
    # verify_litellm_upstream_reachable posts exactly:
    #   {"messages":[{"role":"user","content":"ping"}], "max_tokens":1, "stream":false}
    # to /v1/chat/completions. Tag it so the host-side extractor can put its
    # cost in `sources.preflight` instead of dropping it on the floor (it
    # happens BEFORE any task's run window, so the in-window agent extractor
    # filters it out).
    try:
        op = kwargs.get("optional_params") or {}
        max_tok = kwargs.get("max_tokens", op.get("max_tokens", op.get("maxTokens")))
        if max_tok not in (1, "1"):
            return False
        messages = kwargs.get("messages") or []
        if not isinstance(messages, list) or len(messages) != 1:
            return False
        msg = messages[0]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            return False
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip().lower() == "ping"
        if isinstance(content, list) and len(content) == 1:
            inner = content[0]
            if isinstance(inner, dict):
                text = inner.get("text") or inner.get("content")
                return isinstance(text, str) and text.strip().lower() == "ping"
        return False
    except Exception:
        return False


# OpenClaw makes model calls of its own on the session's credentials, so they
# reach this callback carrying the agent's run_key while producing no assistant
# message. Left unlabelled they are indistinguishable from a turn, and the
# per-message back-fill in eval/run_batch.py can only refuse to attribute
# anything (measured on the 2026-09-17 koji run: 98 rows for 87 messages, so
# all 87 shipped cost 0). Every such call is issued through the agent SDK's
# `completeSimple`, which sends ONE user message and a fixed prompt, and the
# prompts are compile-time constants of the shipped image
# (/usr/lib/node_modules/openclaw):
#
#   compaction  — context summarization, both the initial and the iterative
#     update prompt, plus the split-turn prefix and branch summaries. All four
#     paths pass SUMMARIZATION_SYSTEM_PROMPT as the system prompt
#     (node_modules/@mariozechner/pi-coding-agent/dist/core/compaction/
#     utils.js, referenced from compaction.js and branch-summarization.js).
#   summarize   — the link/media understanding summarizer (`summarizeText` in
#     the openclaw bundle), which sends no system prompt and opens its single
#     user message with the sentence below.
#
# Matching the opening sentence rather than the whole prompt keeps the test
# cheap while staying specific: both sentences are addressed to the model in
# the second person and neither appears in a task prompt. The single-user-
# message requirement is what makes a false positive implausible — a HUMAN's
# turn always carries the conversation so far. The heartbeat below is the
# standing exception to that premise and is why it is fingerprinted on its own
# terms rather than folded in here.
#
# Three more of openclaw's own call types are named by SHAPE rather than by
# prompt text, because none of them sends a prompt this callback could pin:
#
#   image       — the `image` tool's vision call. src/agents/tools/image-tool.ts
#     in the bundle builds the request itself, in buildImageContext(): exactly
#     one user message whose content is a text block followed by one image
#     block per file, and NO system prompt. Its prompt text is caller-supplied
#     — DEFAULT_PROMPT ("Describe the image.") only fills in when the agent
#     passes none — so the text is not a compile-time constant and the shape
#     is what identifies it. The absent system prompt is what separates it
#     from a first agent turn that carries an image: the agent always sends
#     one, this tool never does.
#   pdf         — the `pdf` tool's model call. It is live in every run: the
#     runner never sets agents.defaults.pdfModel, but it always writes
#     imageModel (src/agents/openclaw/runner.py), and
#     resolvePdfModelConfigForTool falls pdfModel -> imageModel -> provider
#     default, so a model always resolves and the tool always registers;
#     tools.deny carries only the browser entries. It reaches the model by
#     three request shapes, none of which sends a system prompt:
#       native         — anthropicAnalyzePdf and geminiAnalyzePdf hand-build
#         the provider body to get a document type pi-ai's content model does
#         not have, posting one user message of document blocks plus the
#         prompt. Named below by those block tags.
#       extracted text — buildPdfExtractionContext posts one user message of
#         the host-extracted page text under a literal label, plus the
#         prompt. Rasterization only runs for a PDF yielding under
#         PDF_MIN_TEXT_CHARS (200) of text, so a text-rich one carries ZERO
#         image blocks and nothing about the request is media at all; the
#         label, not the shape, is what names it.
#       extracted with images — the same builder with rasterized pages
#         interleaved, which the image test above already catches and which
#         deliberately keeps that name. See _classify_internal_purpose.
#   embeddings  — openclaw's OWN memory subsystem, not an extension. The
#     shipped default for the memory plugin slot is memory-core
#     (DEFAULT_SLOT_BY_KEY, dist/config-CO7zBdn8.js:2216), and its vector
#     calls are built in src/memory/embeddings*.ts, bundled as the
#     //#region block run in dist/manager-SGyWKeTx.js — OpenAI provider at
#     :682, DEFAULT_OPENAI_EMBEDDING_MODEL "text-embedding-3-small",
#     posting to EMBEDDING_BATCH_ENDPOINT "/v1/embeddings" (:1629). In this
#     harness the bulk of them are driven by our OWN bootstrap rather than by
#     the agent: src/agents/openclaw/runner.py:2031 runs `openclaw memory
#     index --force` inside the container before the task starts, which
#     embeds every seeded memory file in one sweep. Auto-recall and
#     memory_search add more during the run. None of them is a chat request:
#     they hit /v1/embeddings with an `input` and no messages, so they can
#     never correspond to an assistant message.
#
#     The note here previously credited the memory-lancedb extension
#     (extensions/memory-lancedb/index.ts:183, Embeddings.embed →
#     client.embeddings.create). That code ships and would do the same thing,
#     but it is NOT the slot this image runs — memory-core is the default and
#     the runner never overrides it — so no row in any measured snapshot came
#     from it. Corrected against the native-call census.
#
# Measured on the 2026-09-18 sean_callahan run, whose 156-row snapshot held 26
# rows more than its 129 assistant messages: 22 embeddings and 4 image-tool
# calls, matching that run's tool mix (memory_search 1, image 4) exactly. That
# snapshot is silent on the pdf tool only because the agent happened to shell
# out to pdftotext through exec instead of calling it; one call on either of
# the two unlabelled paths would have put the run back at failed.
#
# `openclaw.cache-ttl` is NOT in this list, though the critique that opened
# this named it as the bulk of the surplus. Reading the bundle says otherwise:
# it is a context-pruning transformer that rewrites the outgoing message list
# and appends a session entry, issuing no model call of its own, so it cannot
# put a row in this log. Session titles are likewise derived from the first
# user message rather than generated.
_COMPACTION_SYSTEM_HEAD = "You are a context summarization assistant."
_SUMMARIZE_TEXT_HEAD = (
    "You are an assistant that summarizes texts concisely while keeping the "
    "most important information."
)

# heartbeat — the gateway's own liveness turn, and the one internal call that
# defeats BOTH guards above. Every other purpose here is either a system-less
# `completeSimple` or a non-chat route; the heartbeat is a FULL agent turn the
# container issues on its own schedule: same system prompt, same tools, same
# session credentials, no human behind it. `runHeartbeatOnce`
# (dist/health-BxAgqqNt.js:302) builds an ordinary reply context
#
#     Body: appendCronStyleCurrentTimeLine(prompt, cfg, startedAt)   (:497)
#
# and hands it to getReplyFromConfig, so it arrives here carrying a system
# prompt (past the `system.strip()` guard) and, on a session with history, more
# than one message (past the `len(others) != 1` arity check). It is therefore
# checked BEFORE both, on the LAST user-role message rather than others[0].
#
# It cannot be turned off from the harness side: the runner ships openclaw as
# released, and the default interval is 30 minutes (1h only under detected
# OAuth; the harness bearer is the run_key, so 30m applies). Any run over half
# an hour gets one row per interval that no assistant message can claim.
# Measured on the 2026-09-18 willie_prince run: 155 rows for 114 assistant
# messages, the surplus row {input 2, output 263, cache_write 28463,
# reasoning 48, duration 5.368s} logged at 21:54:21.985948Z — 1.17s after the
# first idle moment past the 30-minute mark, and the run's 21st agent
# construction for 20 human turns. Unlabelled it left the gate at `failed` and
# all 114 messages shipped with no cost block.
#
# Two compile-time constants of the shipped v1.4 image pin it, either one
# sufficient:
#
#   _HEARTBEAT_PROMPT_HEAD — the head of resolveHeartbeatPrompt's default
#     (dist/reply-BCcP6j4h.js:9084), which is sent VERBATIM as the user
#     message; the full default continues "Do not infer or repeat old tasks
#     from prior chats. If nothing needs attention, reply HEARTBEAT_OK."
#     Matched at the HEAD of the message, which is where the prompt is put.
#   _HEARTBEAT_PATH_HINT — the tail of the workspace-path hint
#     appendHeartbeatWorkspacePathHint appends (dist/health-BxAgqqNt.js:390):
#         if (!/heartbeat\.md/i.test(prompt)) return prompt;
#         const hint = `When reading HEARTBEAT.md, use workspace file ${...}
#                       (exact case). Do not read docs/heartbeat.md.`;
#     It is unconditional for any prompt naming heartbeat.md, is not exposed in
#     config, and is applied to the resolved prompt — so it survives an
#     `agents.defaults.heartbeat.prompt` override that would move the head out
#     from under the first constant. Matched at the TAIL, which is where it is
#     appended.
#
# The tail match tolerates exactly one trailing line, the `Current time: ...`
# that appendCronStyleCurrentTimeLine (dist/reply-BCcP6j4h.js:36579) puts after
# the hint on the way into ctx.Body. That is the only text appended past the
# hint, it is a single line, and the function is a no-op when the prompt
# already contains "Current time:", so at most one is ever present. The line is
# matched to its full shape rather than its opening — resolveCronStyleNow
# (same file:36570) builds it as
#     `Current time: ${formattedTime} (${userTimezone}) / ${iso} UTC`
# so it always ENDS on " UTC" — which keeps the tolerance from swallowing a
# human's closing sentence and handing the tail anchor a hint that the message
# did not actually end on.
#
# Both anchors are ends, not substrings, and that is load-bearing twice over:
#
#   - A genuine task turn may discuss HEARTBEAT.md at length; only a message
#     that OPENS with the prompt or CLOSES with the hint is the gateway's.
#   - It must not shadow compaction, which is checked after it and also
#     carries a system prompt. It cannot: all four compaction paths build
#     their user message as `<conversation>\n${conversationText}\n
#     </conversation>\n\n${basePrompt}` (pi-coding-agent/dist/core/compaction/
#     compaction.js:434 and :593, branch-summarization.js:210), so a compacted
#     transcript is always WRAPPED — a heartbeat turn inside one is neither
#     first nor last, and the body opens on `<conversation>` and closes on the
#     summarization prompt.
_HEARTBEAT_PROMPT_HEAD = (
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly."
)
_HEARTBEAT_PATH_HINT = "Do not read docs/heartbeat.md."
_HEARTBEAT_TIME_LINE = re.compile(r"\n[ \t]*Current time:[^\n]* UTC[ \t]*\Z")

# The heartbeat's other three prompts. `runHeartbeatOnce` does not always send
# resolveHeartbeatPrompt's default: when the tick has pending events it swaps
# the body for an event prompt and sends everything else about the turn
# unchanged. One expression picks between all three
# (dist/health-BxAgqqNt.js:403):
#
#     prompt: appendHeartbeatWorkspacePathHint(
#         hasExecCompletion ? buildExecEventPrompt({...})
#       : hasCronEvents     ? buildCronEventPrompt(cronEvents, {...})
#       :                     resolveHeartbeatPrompt(params.cfg, params.heartbeat),
#         params.workspaceDir)
#
# so these are heartbeat turns in the only sense that matters here — same
# function, same schedule, same session, same absence of a human — and they
# take the heartbeat label rather than one of their own. They are NOT cron
# rows: a cron JOB runs on its own session key through the cron runner
# (see _CRON_MESSAGE_HEAD); a cron EVENT is delivered by the health loop into
# the session the agent is already on.
#
# Each is matched at the HEAD, which is where buildCronEventPrompt /
# buildExecEventPrompt put it, and each has a deliverToUser variant that
# differs only in the tail — so the head is the whole of the fixed part:
#
#   :82  "...no event content was found. Handle this internally and reply
#          HEARTBEAT_OK when nothing needs user-facing follow-up."
#   :83  "...no event content was found. Reply HEARTBEAT_OK."
#   :85  "...The reminder content is:\n\n" + eventText + "\n\nHandle this
#          reminder internally. ..."
#   :86  "...The reminder content is:\n\n" + eventText + "\n\nPlease relay ..."
#   :89  "...shown in the system messages above. Handle the result internally.
#          ..."
#   :90  "...shown in the system messages above. Please relay ..."
#
# The reminder head deliberately stops at the colon: everything past it is
# eventText, which is user-authored (the agent's own cron tool writes it) and
# therefore not a compile-time constant. The path hint cannot displace any of
# them — appendHeartbeatWorkspacePathHint returns the prompt untouched unless
# it already matches /heartbeat\.md/i, and none of these three mentions it.
_HEARTBEAT_EVENT_HEADS = (
    "A scheduled reminder has been triggered. The reminder content is:",
    "A scheduled cron event was triggered, but no event content was found.",
    "An async command you ran earlier has completed. The result is shown in "
    "the system messages above.",
)

# memory_flush — the pre-compaction memory dump, and the second full agent turn
# the container takes for itself. `runMemoryFlushIfNeeded`
# (dist/reply-BCcP6j4h.js:93697) fires when a session's token count crosses
#     contextWindow - reserveTokensFloor(20000) - softThresholdTokens(4000)
# (threshold :93720, predicate shouldRunMemoryFlush :93591) or its transcript
# passes forceFlushTranscriptBytes (2 MiB, :93553), and spends a whole turn
# telling the agent to write what it wants to keep into memory/<date>.md before
# compaction throws the context away.
#
# It is ON unless configured off — `const enabled = defaults?.enabled ?? true`
# in resolveMemoryFlushSettings (:93549) — and the runner never writes
# agents.defaults.compaction.memoryFlush at all, so it is default-ON in every
# run and latent in every run long enough to approach its own context window.
#
# Like the heartbeat it is a REAL turn, not a `completeSimple`: the flush is
# dispatched through runEmbeddedPiAgent with the session's tools and system
# prompt (:93798-93830), so it arrives here past the `system.strip()` guard and,
# on a session with history, past the `len(others) != 1` arity check. It is
# therefore checked BEFORE both, on the LAST user-role message.
#
# UNLIKE the heartbeat it is never pruned. The gateway ships exactly one
# transcript-truncating path — pruneHeartbeatTranscript, dist/health-BxAgqqNt.js
# — and nothing in the flush code calls it (grep of dist/ finds the symbol in
# the two health chunks only). So the flush's user+assistant pair STAYS in
# chat.jsonl and its row DOES have an assistant message behind it. That is why
# this label does not subtract: see eval/run_batch.py's
# _TRANSCRIPT_TURN_PURPOSES for the accounting and
# _count_memory_flush_turns_in_transcript for the transcript-side count, which
# reads the same fingerprints as this file so the two cannot drift apart.
#
# Two compile-time constants of the shipped v1.4 image pin it, either one
# sufficient, and they fail over to each other in both directions: an
# `agents.defaults.compaction.memoryFlush.prompt` override moves the first,
# a `.systemPrompt` override moves the second, and neither override moves both.
#
#   _MEMORY_FLUSH_PROMPT_HEAD — the head of DEFAULT_MEMORY_FLUSH_PROMPT
#     (:93495), which is the user message verbatim: "Pre-compaction memory
#     flush." then MEMORY_FLUSH_TARGET_HINT (:93487), `.join(" ")`. The hint
#     continues "YYYY-MM-DD.md (create memory/ if needed)." and
#     resolveMemoryFlushPromptForRun replaceAll()s that literal with the run's
#     date (:93536) before sending, so the anchor STOPS at "memory/" — one
#     character short of the only part of the sentence that is not fixed at
#     compile time. Matched at the HEAD, which is where the prompt is put; the
#     `Current time: ...` line resolveMemoryFlushPromptForRun appends (:93538)
#     and any hint ensureMemoryFlushSafetyHints / ensureNoReplyHint add
#     (:93552-93560) all land after it, so none of them can move it.
#   _MEMORY_FLUSH_SYSTEM_LINE — the opening sentence of
#     DEFAULT_MEMORY_FLUSH_SYSTEM_PROMPT (:93503-04), matched as a LINE rather
#     than as a head. The census called this a system head; the bundle says
#     otherwise, and the anchor is written to what the bundle does. The flush
#     system prompt does not replace the agent's, it is carried as
#     extraSystemPrompt —
#         flushSystemPrompt = [run.extraSystemPrompt,
#                              memoryFlushSettings.systemPrompt]
#                             .filter(Boolean).join("\n\n")            (:93790)
#     — and extraSystemPrompt is a SECTION of the system prompt, not the whole
#     of it:
#         if (extraSystemPrompt) lines.push(contextHeader, extraSystemPrompt,
#                                           "")                        (:38876)
#     under "## Group Chat Context" (or "## Subagent Context"), with the
#     agent's own prompt before it and further sections after. So the sentence
#     is never at offset 0. What IS invariant is that it opens a line: it
#     follows either the header line or the "\n\n" that join() inserted. Hence
#     re.M and `^`.
#
# The system anchor is the load-bearing one, and not as a fallback. A flush
# turn exists to WRITE A FILE, so it calls tools, and a tool-calling turn is
# several requests: only the FIRST carries the flush prompt as its last user
# message, every later one ends on a tool result. extraSystemPrompt is set for
# the whole run, so the system line is on every request of the turn and the
# prompt head is on one. Both are kept because either can be configured away.
_MEMORY_FLUSH_PROMPT_HEAD = (
    "Pre-compaction memory flush. Store durable memories only in memory/"
)
_MEMORY_FLUSH_SYSTEM_LINE = re.compile(r"^Pre-compaction memory flush turn\.", re.M)

# cron — a scheduled job's agent turn: the same self-issued full turn as the
# heartbeat, from the cron runner instead of the health loop.
# runCronIsolatedAgentTurn (dist/gateway-cli-BjsM6fWb.js:4196) builds its user
# message as
#     base        = `[cron:${job.id} ${job.name}] ${message}`.trim()   (:4363)
#     commandBody = `${base}\n${timeLine}`.trim()                      (:4381)
#     commandBody = appendCronDeliveryInstruction({commandBody, ...})  (:4382)
# and runs it with the session's system prompt, so like the heartbeat it
# defeats both guards below and is checked ahead of them.
#
# Its turn does NOT reach the delivered transcript, and that is structural
# rather than lucky: a cron job runs on its own session key —
# `params.sessionKey?.trim() || \`cron:${job.id}\`` (:4218) through
# resolveCronAgentSessionKey (:4056) — which resolves to its own store entry
# and its own sessionId, hence its own transcript file (:4425). So a cron row
# is an extra row against the main chat.jsonl exactly like a pruned heartbeat,
# and this label subtracts, as heartbeat's does.
#
#   _CRON_MESSAGE_HEAD — the bracket prefix, which is unconditional on this
#     path and carries no configurable text of its own: `cron:`, the job id
#     (no spaces, no `]`), a space, the job name, `]`. Matched at the HEAD.
#     A human's turn would have to open on that exact bracket to collide.
#   _CRON_DELIVERY_TAIL — appendCronDeliveryInstruction's sentence pair
#     (:4194), appended last and therefore matched at the TAIL: it goes on
#     AFTER the `Current time:` line, so unlike the heartbeat's path hint this
#     tail needs no tolerance for a trailing line. It is the weaker of the two
#     — it is added only when the job requested delivery
#     (deliveryRequested, :4192) — so it is a supplement to the head, not a
#     peer of it: it survives a job whose message text was wrapped past the
#     prefix, and it is silent on a job that delivers nothing.
#
# One cron shape is deliberately NOT fingerprinted: the external-hook path
# (:4371), which replaces the whole body with buildSafeExternalPrompt and drops
# the bracket prefix. It fires only for gmail/hook sessions
# (isExternalHookSession), which the harness does not configure, and it carries
# no constant of its own worth pinning ahead of a run that could produce one.
_CRON_MESSAGE_HEAD = re.compile(r"\A\[cron:[^\s\]]+ [^\]]*\]")
_CRON_DELIVERY_TAIL = (
    "Return your summary as plain text; it will be delivered automatically. "
    "If the task explicitly calls for messaging a specific external recipient, "
    "note who/where it should go instead of sending it yourself."
)

# subagent — a spawned child's traffic, which bills the PARENT. The subagents
# tool hands runEmbeddedPiAgent a childTaskMessage
# (dist/reply-BCcP6j4h.js:30436-30440) built as
#
#     [`[Subagent Context] You are running as a subagent (depth ${d}/${max}).
#       Results auto-announce to your requester; do not busy-poll for status.`,
#      spawnMode === "session" ? "[Subagent Context] This subagent session is
#       persistent and ..." : undefined,
#      `[Subagent Task]: ${task}`].filter(Boolean).join("\n\n")
#
# and runs it under `extraSystemPrompt: childSystemPrompt` (:30471). Nothing
# the child does appears in the PARENT's chat.jsonl, so every row it produces
# is a row with no assistant message behind it — the same accounting shape as
# the heartbeat, and the reason this label subtracts.
#
# Matched on the FIRST user message, which is the one case in this file where
# others[0] is right and the last message is wrong. childTaskMessage is what
# OPENS the child's session and it stays at position 0 for the whole of it, so
# anchoring there names every request of the run; anchoring on the last
# message would name only the first request and leave the rest of a
# tool-calling child unlabelled. The heartbeat's reasoning is the mirror image
# of this and both are correct: a heartbeat is APPENDED to someone else's
# session, a subagent task message BEGINS its own.
#
# Two anchors, because spawnMode decides whether a line sits between them:
# the context sentence opens the message, and the task label opens a LINE
# (join("\n\n") guarantees it), so the second is matched with re.M like the
# memory flush's system anchor.
_SUBAGENT_CONTEXT_HEAD = "[Subagent Context] You are running as a subagent (depth "
_SUBAGENT_TASK_LINE = re.compile(r"^\[Subagent Task\]: ", re.M)

# The two messages openclaw sends to WAKE an agent about subagent work, both
# ordinary user messages on a live session and therefore past both guards.
#
#   announce steer — buildAnnounceSteerMessage (:29714) returns
#     `formatAgentInternalEventsForPrompt(events) || "A background task
#     finished. Process the completion update now."`, i.e. this exact sentence
#     whenever the event list formats to nothing.
#   descendant wake — buildDescendantWakeMessage (:29722) joins its lines with
#     "\n" and this sentence is the first of them (:29724).
#
# Both are labelled `subagent`: they are the parent side of the same
# mechanism, they carry no human behind them, and neither produces a turn a
# human asked for.
_SUBAGENT_WAKE_HEADS = (
    "A background task finished. Process the completion update now.",
    "[Subagent Context] Your prior run ended while waiting for descendant "
    "subagent completions.",
)

# a2a — the agent-to-agent announce step. runAgentStep (:27487) posts a
# gateway `agent` call whose message is this string LITERALLY (:27625), with
# the real content carried out-of-band in extraSystemPrompt (:27626). It is
# the one fingerprint in this file matched by equality rather than by an
# anchor, because the whole message is the constant — there is no variable
# part to leave room for, and equality is what keeps a human quoting the
# sentence from taking the label.
_A2A_ANNOUNCE_MESSAGE = "Agent-to-agent announce step."

# slug — the session-title generator. generateSlugViaLLM
# (dist/llm-slug-generator.js:50) builds a prompt opening with this sentence
# (:58) and runs it through runEmbeddedPiAgent on a throwaway session
# (:68, sessionKey "temp:slug-generator"). One user message, and a system
# prompt, because runEmbeddedPiAgent always builds one.
_SLUG_PROMPT_HEAD = (
    "Based on this conversation, generate a short 1-2 word filename slug"
)

# llm_task — the llm-task extension's structured-output tool. It composes its
# instructions as a USER-message head rather than as a system prompt
# (extensions/llm-task/src/llm-task-tool.ts:175-183):
#
#     const system = ["You are a JSON-only function.",
#                     "Return ONLY a valid JSON value.",
#                     "Do not wrap in markdown fences.",
#                     "Do not include commentary.",
#                     "Do not call tools."].join(" ");
#     const fullPrompt = `${system}\n\nTASK:\n${prompt}\n\nINPUT_JSON:\n${inputJson}\n`;
#
# and passes fullPrompt as `prompt:` (:200). The anchor stops after the second
# sentence: that is as much as is fixed before the caller's task text, and the
# first two sentences together are already unmistakable.
_LLM_TASK_PROMPT_HEAD = (
    "You are a JSON-only function. Return ONLY a valid JSON value."
)

# probe — `openclaw models probe`, both of its shapes. Neither is a task turn
# and both are whole-message constants, so both are matched by equality:
#
#   :1166 PROBE_PROMPT, sent through runEmbeddedPiAgent (:1421), so it carries
#     a system prompt and must be named before the system guard.
#   :2008 probeTool's context, posted straight to complete() with tools:
#     [TOOL_PING] and no system prompt at all.
#
# Neither is expected in a harness run — the runner never shells `models
# probe` — but both bill the sidecar if anything ever does, and an unlabelled
# row is what breaks attribution.
_PROBE_MESSAGES = (
    "Reply with OK. Do not use tools.",
    "Call the ping tool with {} and nothing else.",
)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _request_body(kwargs: dict) -> dict:
    """The raw client body LiteLLM captured, or {} when it did not."""
    lp = kwargs.get("litellm_params") or {}
    psr = lp.get("proxy_server_request") if isinstance(lp, dict) else None
    body = psr.get("body") if isinstance(psr, dict) else None
    return body if isinstance(body, dict) else {}


def _prompt_shape(kwargs: dict) -> tuple[str, list[dict]]:
    """The request's system prompt and its non-system messages.

    Prefers the raw client body, which for the anthropic-messages route
    (src/agents/openclaw/runner.py registers the sidecar with
    api="anthropic-messages") carries ``system`` as a top-level field rather
    than a message. Falls back to the normalized ``messages`` LiteLLM hands
    every callback, where the same value arrives as a system-role entry.

    The non-system messages come back as the raw dicts rather than flattened
    text: the image tool is identified by the content BLOCKS it sends, which
    flattening discards.
    """
    body = _request_body(kwargs)
    system = _text_of(body.get("system"))
    messages: Any = body.get("messages")
    if not isinstance(messages, list) or not messages:
        messages = kwargs.get("messages")
    others: list[dict] = []
    for msg in messages if isinstance(messages, list) else []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system":
            if not system:
                system = _text_of(msg.get("content"))
            continue
        others.append(msg)
    return system, others


# Content-block type tags that carry an image, across the shapes this callback
# can be handed: the anthropic-messages body openclaw posts ("image"), the
# OpenAI-normalized ``messages`` LiteLLM builds from it ("image_url"), and the
# Responses-API spelling ("input_image").
_IMAGE_BLOCK_TYPES = frozenset({"image", "image_url", "input_image"})


def _has_image_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and str(block.get("type") or "") in _IMAGE_BLOCK_TYPES
        for block in content
    )


# Content-block tags that carry a PDF rather than an image, across the shapes
# this callback can be handed. The pdf tool's native path builds the provider
# body by hand precisely because pi-ai has no document content type
# (dist/plugin-sdk/agents/tools/pdf-native-providers.d.ts says so in as many
# words), so these spellings come straight off the wire:
#
#   "document"    — anthropicAnalyzePdf, dist/reply-BCcP6j4h.js:22632. One
#     {"type":"document","source":{"type":"base64","media_type":
#     "application/pdf","data":...}} per file, then one {"type":"text",
#     "text":prompt}, POSTed to {baseUrl}/v1/messages with no system field.
#   "inline_data" — geminiAnalyzePdf, same file:22678. Gemini parts carry no
#     type tag, so the KEY is the tag: {"inline_data":{"mime_type":
#     "application/pdf","data":...}}, prompt appended as a bare {"text":...}.
#   "file" / "input_file" — the OpenAI Chat-Completions and Responses
#     spellings of the same attachment, carried for the normalized-messages
#     path exactly as _IMAGE_BLOCK_TYPES carries image_url / input_image.
#
# inline_data additionally has to prove an application/pdf media type, because
# pi-ai's own google provider spells IMAGES inlineData too (node_modules/
# @mariozechner/pi-ai/dist/providers/google-shared.js:90, mimeType) and those
# rows belong on the image label, not this one.
_DOC_BLOCK_TYPES = frozenset({"document", "file", "input_file"})
_DOC_INLINE_KEYS = ("inline_data", "inlineData")
_PDF_MEDIA_TYPE = "application/pdf"


def _is_pdf_media_type(value: Any) -> bool:
    return str(value or "").split(";")[0].strip().lower() == _PDF_MEDIA_TYPE


def _has_document_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") in _DOC_BLOCK_TYPES:
            return True
        for key in _DOC_INLINE_KEYS:
            inline = block.get(key)
            if isinstance(inline, dict) and _is_pdf_media_type(
                inline.get("mime_type") or inline.get("mimeType")
            ):
                return True
    return False


# The pdf tool's non-native path extracts the document host-side and ships the
# result as ordinary text blocks, so nothing on the wire says PDF except the
# label the extractor prepends. buildPdfExtractionContext
# (dist/reply-BCcP6j4h.js:22832) writes it verbatim as
#     const label = extractions.length > 1 ? `[PDF ${i + 1} text]\n`
#                                          : "[PDF text]\n";
# and pushes label + extraction.text as one text block per file that had any
# text at all. Neither provider serializer rewrites or joins those blocks —
# anthropic.js:552 and openai-completions.js:429 both map a text block to
# {"type":"text","text":...} and keep the array — so the label survives to
# here byte for byte on both routes.
_PDF_EXTRACTION_LABEL = re.compile(r"\[PDF(?: \d+)? text\]\n")


def _has_pdf_extraction_label(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and _PDF_EXTRACTION_LABEL.match(text.lstrip()):
            return True
    return False


def _last_user_text(others: list[dict]) -> str | None:
    """The text of the LAST user-role message, or None when there is none.

    The last one, not ``others[0]``: every self-issued turn below fires into a
    session that may already carry history, so ``others[0]`` would be the run's
    first HUMAN turn. It is also why the flush prompt sitting in that history
    cannot re-label the turns that follow it.
    """
    for msg in reversed(others):
        if str(msg.get("role") or "") != "user":
            continue
        return _text_of(msg.get("content"))
    return None


def _is_heartbeat_prompt(others: list[dict]) -> bool:
    """True when the LAST user message is one of the gateway's heartbeat bodies."""
    text = _last_user_text(others)
    if text is None:
        return False
    text = text.strip()
    if text.startswith(_HEARTBEAT_PROMPT_HEAD):
        return True
    if text.startswith(_HEARTBEAT_EVENT_HEADS):
        return True
    return _HEARTBEAT_TIME_LINE.sub("", text).rstrip().endswith(
        _HEARTBEAT_PATH_HINT)


def _is_memory_flush_prompt(others: list[dict]) -> bool:
    """True when the LAST user message is the pre-compaction flush prompt."""
    text = _last_user_text(others)
    return text is not None and text.lstrip().startswith(_MEMORY_FLUSH_PROMPT_HEAD)


def _is_cron_prompt(others: list[dict]) -> bool:
    """True when the LAST user message is a cron job's.

    The tail anchor is checked through the heartbeat's time-line tolerance,
    which is sound because the line is the same line: cron resolves it from
    ``resolveCronStyleNow`` (dist/gateway-cli-BjsM6fWb.js:4362), the same
    generator the heartbeat's comes from (dist/reply-BCcP6j4h.js:36570), so it
    ends on " UTC" identically. On the shipped ordering the strip is a no-op —
    commandBody puts the time line in the MIDDLE (:4381) and
    appendCronDeliveryInstruction appends the delivery sentence after it
    (:4382) — and it is kept anyway so the anchor still holds for a body whose
    time line lands last.
    """
    text = _last_user_text(others)
    if text is None:
        return False
    text = text.strip()
    if _CRON_MESSAGE_HEAD.match(text):
        return True
    return _HEARTBEAT_TIME_LINE.sub("", text).rstrip().endswith(
        _CRON_DELIVERY_TAIL)


def _first_user_text(others: list[dict]) -> str | None:
    """The text of the FIRST user-role message, or None when there is none."""
    for msg in others:
        if str(msg.get("role") or "") != "user":
            continue
        return _text_of(msg.get("content"))
    return None


def _is_subagent_request(others: list[dict]) -> bool:
    """True when this request belongs to a spawned subagent, or wakes one."""
    opening = _first_user_text(others)
    if opening is not None:
        opening = opening.lstrip()
        if opening.startswith(_SUBAGENT_CONTEXT_HEAD):
            return True
        if _SUBAGENT_TASK_LINE.search(opening):
            return True
    text = _last_user_text(others)
    return text is not None and text.strip().startswith(_SUBAGENT_WAKE_HEADS)


def _is_a2a_announce(others: list[dict]) -> bool:
    """True when the LAST user message IS the agent-to-agent announce step."""
    text = _last_user_text(others)
    return text is not None and text.strip() == _A2A_ANNOUNCE_MESSAGE


def _is_slug_prompt(others: list[dict]) -> bool:
    text = _last_user_text(others)
    return text is not None and text.lstrip().startswith(_SLUG_PROMPT_HEAD)


def _is_llm_task_prompt(others: list[dict]) -> bool:
    text = _last_user_text(others)
    return text is not None and text.lstrip().startswith(_LLM_TASK_PROMPT_HEAD)


def _is_probe_prompt(others: list[dict]) -> bool:
    text = _last_user_text(others)
    return text is not None and text.strip() in _PROBE_MESSAGES


def _is_embeddings_request(kwargs: dict) -> bool:
    """True for a /v1/embeddings call rather than a chat completion.

    ``call_type`` is the discriminator LiteLLM itself uses (``embedding`` /
    ``aembedding``), exactly as for transcription above. The body test behind
    it is the OpenAI embeddings wire contract — ``input`` instead of
    ``messages`` — and covers proxy builds that do not forward call_type to
    callbacks, where the request would otherwise be indistinguishable from a
    chat row that happens to carry no messages.
    """
    if "embedding" in str(kwargs.get("call_type") or ""):
        return True
    body = _request_body(kwargs)
    return bool(body) and "input" in body and not body.get("messages")


def _classify_internal_purpose(kwargs: dict) -> str:
    """Name the openclaw-internal call this request is, or "" for a turn.

    The returned label is written to the row as ``purpose`` and is what lets
    the per-message back-fill subtract these rows before it compares counts,
    and what lets usage.json carry them as their own ledger line instead of
    folding them anonymously into the agent total.

    Order is precedence, and each step is placed by how much of the request it
    needs to read:

      transcription  route, from call_type — no body to inspect.
      embeddings     route — not a chat request; carries no messages at all.
      self-issued    heartbeat, subagent, memory_flush, cron and a2a are real
                     agent turns, so they are the labels that must be taken
                     before the two shape guards: each has a system prompt and
                     may have history, and both guards would drop them. Placed
                     after the two route tests because those decide on
                     call_type alone and none of the five is either route.
                     Order WITHIN the group is meaningless except at one
                     point: subagent is ahead of memory_flush, because those
                     two are the only pair that can both be true of one
                     request and they disagree about whether the row
                     subtracts. See the comment at that branch. heartbeat and
                     memory_flush cannot collide at all — `canAttemptFlush =
                     ... && !params.isHeartbeat && !isCli`,
                     dist/reply-BCcP6j4h.js:93710.
      arity          from here down every label needs exactly one user
                     message, which is what `completeSimple` and every
                     throwaway-session caller below sends.
      compaction     system prompt. Safe below heartbeat: a compaction body is
                     a WRAPPED transcript (`<conversation>...</conversation>`
                     then the base prompt), so it opens on a tag no anchor
                     above matches and closes on a summarization instruction
                     that is neither the heartbeat's path hint nor cron's
                     delivery sentence. That wrap is also why a compacted
                     transcript CONTAINING a heartbeat, a flush or a cron turn
                     keeps the compaction label: the quoted turn is neither
                     first nor last in the body.
      slug/llm_task  user prompt head, and ABOVE the system guard rather than
      probe          below it: all three reach the model through
                     runEmbeddedPiAgent or an equivalent, which always builds
                     a system prompt, so the guard would drop them. They are
                     safe this high because the arity check already ran — a
                     real agent turn carries its history, these carry one
                     message — and because each anchor is a full sentence of
                     openclaw's own. probe additionally matches by equality.
      system guard   everything past this point sends no system prompt.
      pdf/image/pdf  content blocks, then the extraction label.
      summarize      user prompt head — last, because it is the weakest test.
    """
    try:
        if "transcription" in str(kwargs.get("call_type") or ""):
            # The audio-extract skill's /v1/audio/transcriptions call. Already
            # excluded downstream when it bills by duration, but the
            # token-billed transcribe models look exactly like a chat row.
            return "transcription"
        if _is_embeddings_request(kwargs):
            return "embeddings"
        system, others = _prompt_shape(kwargs)
        if _is_heartbeat_prompt(others):
            # Ahead of both guards below on purpose: the heartbeat is a real
            # agent turn, so it carries a system prompt and may carry history.
            # See the fingerprint block above for why it cannot take a
            # compaction body with it.
            return "heartbeat"
        if _is_subagent_request(others):
            # Ahead of memory_flush on purpose. Both can be true of a single
            # request — a child session is a session, so it can flush — and
            # only one of the two answers the question the accounting asks:
            # whose transcript holds the turn. A subagent's turns are not in
            # THIS run's chat.jsonl, so its rows must subtract; a main-session
            # flush's are, so its rows must not. Naming the child first is
            # what keeps a child's flush out of _TRANSCRIPT_TURN_PURPOSES.
            return "subagent"
        if _MEMORY_FLUSH_SYSTEM_LINE.search(system) or _is_memory_flush_prompt(others):
            # The system anchor first: it is the one that holds for every
            # request of the flush turn, not just the one that carries the
            # prompt. Both are ahead of the guards for the heartbeat's reason,
            # and this label does not subtract — the flush turn is in the
            # delivered transcript.
            return "memory_flush"
        if _is_cron_prompt(others):
            return "cron"
        if _is_a2a_announce(others):
            return "a2a"
        if len(others) != 1:
            return ""
        if system.lstrip().startswith(_COMPACTION_SYSTEM_HEAD):
            return "compaction"
        if _is_slug_prompt(others):
            return "slug"
        if _is_llm_task_prompt(others):
            return "llm_task"
        if _is_probe_prompt(others):
            return "probe"
        if system.strip():
            return ""
        content = others[0].get("content")
        if _has_document_block(content):
            return "pdf"
        if _has_image_block(content):
            # The pdf tool's extracted-with-images path lands here too, and is
            # deliberately left on this label rather than split onto "pdf".
            # When the PDF yields no extractable text its message is image
            # blocks and a prompt — the image tool's shape exactly — so a
            # label-gated relabel would name one half of a single code path
            # "pdf" and the other half "image" depending on the document's
            # contents, which is worse than one honest name. "image" already
            # reads as an internal vision call on caller-supplied media, and
            # the attribution gate only needs the row named at all.
            return "image"
        if _has_pdf_extraction_label(content):
            return "pdf"
        if _text_of(content).lstrip().startswith(_SUMMARIZE_TEXT_HEAD):
            return "summarize"
    except Exception:
        pass
    return ""


_RUN_KEY_PREFIX = "wcb::"


def _extract_run_key(kwargs: dict) -> str:
    """Per-run attribution key from the incoming request's credential headers.

    The runner mints ``wcb::<task_id>::<uuid4>`` per attempt and (in no-auth
    sidecar mode) sends it as the client bearer. Depending on the client API
    it arrives as ``authorization: Bearer <key>`` (openai-completions) or
    ``x-api-key: <key>`` (anthropic-messages). ``x-wcb-run-key`` is a reserved
    explicit channel for master-key deployments. Only values with the
    ``wcb::`` prefix are ever returned, so real credentials are never written
    to the usage log.

    Channel order (probed empirically on the pinned image, litellm 1.88.1):
    1. secret_fields.raw_headers — None on 1.88.1, kept for newer builds.
    2. metadata.user_api_key / user_api_key_hash — in no-auth mode the raw
       bearer passes through unhashed; THE channel for main-agent traffic
       (credential headers are redacted from every headers dict below).
    3. metadata.headers / proxy_server_request.headers — sanitized, but the
       custom x-wcb-run-key header survives redaction (subagent/audio calls).
    """
    try:
        lp = kwargs.get("litellm_params") or {}
        md = lp.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
        psr = lp.get("proxy_server_request") or {}
        header_dicts = [
            (kwargs.get("secret_fields") or {}).get("raw_headers") or {},
            md.get("headers") or {},
            (psr.get("headers") or {}) if isinstance(psr, dict) else {},
        ]
        candidates = [md.get("user_api_key"), md.get("user_api_key_hash")]
        for raw in header_dicts:
            if not isinstance(raw, dict):
                continue
            for header in ("x-wcb-run-key", "authorization", "x-api-key"):
                candidates.append(raw.get(header))
        for value in candidates:
            if not isinstance(value, str):
                continue
            if value.startswith("Bearer "):
                value = value[7:]
            if value.startswith(_RUN_KEY_PREFIX):
                return value
    except Exception:
        pass
    return ""


def _write_row(kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
    try:
        usage_dict = _usage_to_dict(getattr(response_obj, "usage", None))
        if not usage_dict and isinstance(response_obj, dict):
            usage_dict = _usage_to_dict(response_obj.get("usage"))

        cache_read = _int((usage_dict.get("prompt_tokens_details") or {}).get("cached_tokens"))
        if not cache_read:
            cache_read = _int(usage_dict.get("cache_read_input_tokens"))
        cache_write = _int(usage_dict.get("cache_creation_input_tokens"))
        if not cache_write:
            cache_write = _int(usage_dict.get("cacheCreationInputTokens"))
        if not cache_write:
            cache_write = _int(usage_dict.get("cacheWriteInputTokens"))

        # Audio transcription (/v1/audio/transcriptions) responses use a different
        # usage schema than chat completions. LiteLLM emits one of two shapes:
        #   token-billed (gpt-4o-transcribe / gpt-4o-mini-transcribe):
        #       {type: "tokens", input_tokens, output_tokens, total_tokens, input_token_details}
        #   duration-billed (whisper-1):
        #       {type: "duration", seconds}   -- NO token fields at all
        # Chat keys (prompt_tokens/completion_tokens) are absent in both, so fall
        # back to the transcription keys; whisper's seconds is surfaced separately.
        prompt_tokens_raw = _int(usage_dict.get("prompt_tokens"))
        if not prompt_tokens_raw:
            prompt_tokens_raw = _int(usage_dict.get("input_tokens"))
        output_tokens = _int(usage_dict.get("completion_tokens"))
        if not output_tokens:
            output_tokens = _int(usage_dict.get("output_tokens"))

        reasoning_tokens = _int(
            (usage_dict.get("completion_tokens_details") or {}).get("reasoning_tokens")
        )

        # input_tokens = NON-cached input only. Across every provider shape
        # this callback sees, prompt_tokens already folds in cache_read AND
        # cache_write whenever those exist, so the universal recovery rule is
        # `non_cached = prompt - cache_read - cache_write` (clamped to 0).
        # Verified provider shapes (litellm v1.87.x):
        #   - Bedrock-Converse — llms/bedrock/chat/converse_transformation.py
        #     _transform_usage lines 1715-1748: adds BOTH cacheReadInputTokens
        #     AND cacheWriteInputTokens to input_tokens before emitting it as
        #     prompt_tokens.
        #   - Anthropic-native /v1/messages — llms/anthropic/chat/
        #     transformation.py lines 2173-2193: adds BOTH cache_read_input_tokens
        #     AND cache_creation_input_tokens to prompt_tokens.
        #   - OpenAI Chat Completions: no cache_creation field exists in the
        #     provider response at all (grep confirms zero hits in llms/openai/),
        #     so cache_write extracted at line 96 is always 0 and the third
        #     term is a no-op.
        #   - Audio: no cache fields; both terms are 0.
        # The prior rule "subtract cache_read only" was wrong on the two
        # Anthropic paths: a 38k-cache-write opus turn over-reported input by
        # ~38,000 tokens. Diagnosed via the rohan-dasgupta trajectory against
        # CloudWatch ModelInvocationLog; do not revert.
        non_cached = prompt_tokens_raw - cache_read - cache_write
        if non_cached < 0:
            _warn_once_per_day(
                kwargs.get("model"),
                "prompt_tokens (%d) < cache_read (%d) + cache_write (%d); clamping non-cached input to 0",
                prompt_tokens_raw, cache_read, cache_write,
            )
            non_cached = 0
        input_tokens = non_cached
        total_tokens = input_tokens + output_tokens + cache_read + cache_write
        # whisper-1 (default json format) returns NO usage object at all; the audio
        # length is exposed only as the top-level TranscriptionResponse.duration
        # attribute (verified live in litellm:main-stable). Prefer usage.seconds
        # when present (verbose_json / future shapes), else fall back to .duration.
        audio_seconds = _float(usage_dict.get("seconds"))
        if not audio_seconds:
            audio_seconds = _float(getattr(response_obj, "duration", None))

        duration = 0.0
        try:
            duration = (end_time - start_time).total_seconds()
        except Exception:
            pass

        # Cost: prefer litellm.completion_cost() over the proxy-supplied
        # kwargs["response_cost"], because the latter is systematically wrong on
        # at least two upstream paths (both verified live against
        # litellm:main-stable v1.87.0):
        #   - Bedrock Anthropic streaming with prompt caching: response_cost
        #     omits cache_write (cache_creation_input_tokens) pricing entirely,
        #     under-counting opus rows ~12-14x (e.g. a 38k-cache-write turn
        #     priced at $0.0028 instead of $0.245).
        #   - OpenAI /responses path (gpt-5.5) with large outputs: response_cost
        #     comes back 0.0 on ~5/78 rows.
        # completion_cost(completion_response=, model=) reads the cache fields and
        # prices them at the correct per-token rates. We fall back to
        # response_cost ONLY when completion_cost yields <= 0, which preserves
        # whisper-1 duration billing (no tokens -> completion_cost is 0 and
        # response_cost is the only valid source). Do NOT revert to plain
        # response_cost.
        cost = 0.0
        try:
            import litellm
            cost = float(
                litellm.completion_cost(
                    completion_response=response_obj,
                    model=kwargs.get("model"),
                )
                or 0.0
            )
        except Exception as exc:
            sys.stderr.write(
                f"[litellm_usage_callback] completion_cost failed for "
                f"model={kwargs.get('model')!r}: {exc}\n"
            )
            cost = 0.0
        if cost <= 0.0:
            cost = _float(kwargs.get("response_cost"))

        run_key = _extract_run_key(kwargs)
        purpose = _classify_internal_purpose(kwargs)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": kwargs.get("model") or "",
            "kind": "preflight" if _is_preflight_ping(kwargs) else "agent",
            **({"purpose": purpose} if purpose else {}),
            **({"run_key": run_key} if run_key else {}),
            "input_tokens":       input_tokens,
            "output_tokens":      output_tokens,
            "total_tokens":       total_tokens,
            "cache_read_tokens":  cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens":   reasoning_tokens,
            "audio_seconds":      round(audio_seconds, 3),
            "cost_usd":           cost,
            "duration_s":         round(duration, 3),
        }
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception as exc:  # pragma: no cover - never crash the proxy
        try:
            sys.stderr.write(f"[litellm_usage_callback] error: {exc}\n")
        except Exception:
            pass


def _write_failure_row(kwargs: dict, start_time: Any, end_time: Any) -> None:
    """Record that a call failed, for request-count accuracy and debugging."""
    try:
        duration = 0.0
        try:
            duration = (end_time - start_time).total_seconds()
        except Exception:
            pass

        exc = kwargs.get("exception")
        # Class name + truncated message only — never the request payload,
        # which could carry credentials or user content into the usage log.
        error_class = type(exc).__name__ if exc is not None else ""
        error_str = str(exc)[:300] if exc is not None else ""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": kwargs.get("model") or "",
            "kind": "failure",
            "error_class": error_class,
            "error": error_str,
            "run_key": _extract_run_key(kwargs),
            "input_tokens":       0,
            "output_tokens":      0,
            "total_tokens":       0,
            "cache_read_tokens":  0,
            "cache_write_tokens": 0,
            "reasoning_tokens":   0,
            "audio_seconds":      0.0,
            "cost_usd":           0.0,
            "duration_s":         round(duration, 3),
        }
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception:  # pragma: no cover
        pass


class UsageWriter(CustomLogger):
    # async-only on purpose: LiteLLM's streaming_handler.run_success_logging_and_
    # cache_storage and its async stream finalizer dispatch BOTH success_handler
    # (sync) AND async_success_handler on every streamed completion. The
    # litellm_logging.has_run_logging dedup early-returns for self.stream=True
    # (litellm v1.87.x line 1631), so the has_logged_sync_success / async_success
    # flags are never set and both branches run. Defining log_success_event here
    # in addition to async_log_success_event therefore writes every Bedrock
    # streaming row twice. Verified live against the rohan-dasgupta trajectory
    # vs CloudWatch ModelInvocationLog: request_count/output/cache_read/
    # cache_write all matched 2x exactly until log_success_event was removed.
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _write_row(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _write_failure_row(kwargs, start_time, end_time)


# Name expected by LiteLLM YAML config: callbacks: ["litellm_usage_callback.proxy_handler_instance"]
proxy_handler_instance = UsageWriter()
