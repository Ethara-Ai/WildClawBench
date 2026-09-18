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
# message requirement is what makes a false positive implausible — an agent
# turn always carries the conversation so far.
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
#   embeddings  — the memory-lancedb extension's vector calls
#     (extensions/memory-lancedb/index.ts, Embeddings.embed →
#     client.embeddings.create, default model text-embedding-3-small). They
#     run on auto-recall, memory_search and auto-capture, and they are not
#     chat requests at all: they hit /v1/embeddings with an `input` string and
#     no messages, so they can never correspond to an assistant message.
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
        if len(others) != 1:
            return ""
        if system.lstrip().startswith(_COMPACTION_SYSTEM_HEAD):
            return "compaction"
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
