"""Schema-conformant trajectory builder.

Emits the reference output.json schema (top-level session_id / timestamp /
trajectory / input_files / output_artifacts / messages / usage). Ports
`_build_trajectory_from_jsonl` from kensei2_sandbox.py (L3707) and the
three wrap helpers from kensei2.py (L890, L914, L1000) with Odoo
recordsets replaced by plain mappings.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional

from src.utils.jsonl_reader import sanitize_jsonl_message
from src.utils.store import Task

from .multimodal_meta import (
    build_input_files_manifest,
    build_input_modalities,
    build_multimodal_metadata,
    build_output_artifacts,
    build_output_modalities,
    build_trajectory_meta_info,
    slugify_task_type,
)


logger = logging.getLogger(__name__)


MediaHandler = Callable[[List[dict], str], List[dict]]


def _wrap_trajectory_message(
    msg: dict,
    is_accepted: int = 0,
    hints: Optional[str] = None,
    is_auto_hint: bool = False,
    auto_hint_iteration: int = 0,
) -> dict:
    """Wrap assistant/toolResult messages with is_accepted/hints; pass user msgs through."""
    inner = msg.get("message", {})
    role = inner.get("role", "") if isinstance(inner, dict) else ""
    if role in ("assistant", "toolResult"):
        wrapped: dict = {"is_accepted": is_accepted, "hints": hints, "message": msg}
        if is_auto_hint:
            wrapped["is_auto_hint"] = True
            wrapped["auto_hint_iteration"] = auto_hint_iteration
        return wrapped
    return msg


def _wrap_messages_with_turn_feedback(
    messages: List[dict], turns: Iterable[Mapping]
) -> List[dict]:
    """Apply per-turn is_accepted/hints feedback by matching user-message text."""
    turn_list = list(turns or [])
    if not turn_list:
        return [_wrap_trajectory_message(m) for m in messages]

    turn_feedback = []
    for t in turn_list:
        prompt_text = (t.get("prompt") or "").strip() if isinstance(t, Mapping) else ""
        hints_text = (t.get("hints") or "").strip() if isinstance(t, Mapping) else ""
        user_text = (prompt_text or hints_text).strip()
        if hints_text:
            is_accepted = 1
            hint = hints_text
        else:
            is_accepted = 0
            hint = None
        turn_feedback.append((
            user_text,
            is_accepted,
            hint,
            bool(t.get("is_auto_hint", False)) if isinstance(t, Mapping) else False,
            int(t.get("auto_hint_iteration", 0)) if isinstance(t, Mapping) else 0,
        ))

    wrapped: List[dict] = []
    current_accepted = 0
    current_hints: Optional[str] = None
    current_is_auto_hint = False
    current_auto_hint_iteration = 0
    turn_idx = 0

    for msg in messages:
        inner = msg.get("message", {})
        role = inner.get("role", "") if isinstance(inner, dict) else ""

        if role == "user" and turn_idx < len(turn_feedback):
            content = inner.get("content", [])
            user_text = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        user_text = (block.get("text") or "").strip()
                        break
            elif isinstance(content, str):
                user_text = content.strip()

            expected = turn_feedback[turn_idx][0]
            matched = False
            if user_text and expected:
                if user_text == expected:
                    matched = True
                elif user_text in expected or expected in user_text:
                    matched = True
            if matched or user_text:
                current_accepted = turn_feedback[turn_idx][1]
                current_hints = turn_feedback[turn_idx][2]
                current_is_auto_hint = turn_feedback[turn_idx][3]
                current_auto_hint_iteration = turn_feedback[turn_idx][4]
                turn_idx += 1

        wrapped.append(
            _wrap_trajectory_message(
                msg,
                current_accepted,
                current_hints,
                current_is_auto_hint,
                current_auto_hint_iteration,
            )
        )
    return wrapped


def _unwrap_trajectory_messages(messages: List[dict]) -> List[dict]:
    """Unwrap hint-wrapper format and assign sequential turn_index."""
    unwrapped: List[dict] = []
    for msg in messages:
        if (
            "message" in msg
            and isinstance(msg["message"], dict)
            and "message" in msg["message"]
        ):
            unwrapped.append(msg["message"])
        else:
            unwrapped.append(msg)
    for idx, m in enumerate(unwrapped):
        m["turn_index"] = idx
        m.pop("parentId", None)
    return unwrapped


def _artifact_turns_from_entries(entries: List[dict]) -> List[dict]:
    """Reshape OpenClaw JSONL message entries into the {response, tool_calls}
    turn shape that build_output_artifacts consumes, so deliverables written via
    write/exec tools (whose paths live in the tool-call args, not in the
    feedback `turns`) are actually discovered."""
    out: List[dict] = []
    for e in entries or []:
        msg = e.get("message", e) if isinstance(e, dict) else {}
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        tool_calls, texts = [], []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "toolCall":
                tool_calls.append({"name": b.get("name"), "arguments": b.get("arguments")})
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                texts.append(b["text"])
        turn: dict = {}
        if tool_calls:
            turn["tool_calls"] = json.dumps(tool_calls, default=str)
        if texts:
            turn["response"] = "\n".join(texts)
        if turn:
            out.append(turn)
    return out


_ZERO_TOP_USAGE: dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_input_tokens": 0,
    "cost_usd": 0.0,
}


def _coerce_top_usage(src: Optional[Mapping]) -> dict[str, Any]:
    if not isinstance(src, Mapping):
        return dict(_ZERO_TOP_USAGE)
    def _int(k: str) -> int:
        try:
            return int(src.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0
    cost_raw = src.get("cost_usd", 0)
    try:
        cost = float(cost_raw or 0)
    except (TypeError, ValueError):
        cost = 0.0
    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cached_input_tokens": _int("cached_input_tokens"),
        "cost_usd": round(cost, 6),
    }


# Harness scaffolding prepended to user messages that we must NOT surface in the
# published output.json / delivery.json — the consumer wants the EXACT user
# prompt only. Three layers, applied in order:
#   1. The turn-0 boilerplate preamble (constant string; only the timeout digits
#      vary) injected by the openclaw bootstrap.
#   2. An optional `[<weekday date HH:MM UTC>]` wall-clock stamp (added by the
#      openclaw binary on turns >0).
#   3. An optional `[TURN N (...)]` header (added by
#      inject_director.parse_prompts_file).
# Patterns are anchored at start-of-string and match only these known tokens, so
# arbitrary user text that merely begins with `[` is left untouched.
_USER_PREAMBLE_RE = re.compile(
    r"^You are an expert in a restricted, non-interactive environment\.\s*"
    r"Solve the task efficiently before the timeout \(\d+s\)\.\s*"
    r"Run all processes in the foreground without user input or background services\.\s*"
    r"Provide a complete, functional solution in a single pass with no placeholders\.\s*",
)
_USER_UTC_PREFIX_RE = re.compile(r"^\[[^\]]*\bUTC\b[^\]]*\]\s*")
_USER_TURN_PREFIX_RE = re.compile(r"^\[TURN\b[^\]]*\]\s*")


def _strip_user_turn_prefix(text: str) -> str:
    """Strip harness scaffolding from a user message, leaving the exact prompt."""
    if not isinstance(text, str) or not text:
        return text
    s = _USER_PREAMBLE_RE.sub("", text, count=1)
    s = _USER_UTC_PREFIX_RE.sub("", s, count=1)
    s = _USER_TURN_PREFIX_RE.sub("", s, count=1)
    if s != text:
        s = s.lstrip("\n ")
    return s


def _strip_user_prefix_from_message(msg: dict) -> dict:
    """Return a copy of `msg` with the prefix trimmed off its first text block."""
    content = msg.get("content")
    if isinstance(content, str):
        out = dict(msg)
        out["content"] = _strip_user_turn_prefix(content)
        return out
    if isinstance(content, list):
        new_content = list(content)
        for i, block in enumerate(new_content):
            if isinstance(block, dict) and block.get("type") == "text":
                nb = dict(block)
                nb["text"] = _strip_user_turn_prefix(block.get("text", ""))
                new_content[i] = nb
                break
        out = dict(msg)
        out["content"] = new_content
        return out
    return msg


def _dedupe_reissued_turns(entries: List[dict]) -> List[dict]:
    """Drop a re-issued user turn (report §11).

    A gateway recovery can re-send the same user turn, leaving two ADJACENT
    user-turn segments with byte-identical prompt text (e.g. turn 15 appearing
    twice). We split the stream into segments — each a user message plus the
    non-user messages that follow it up to the next user message (a leading
    non-user preamble is its own segment) — and when two adjacent user segments
    have identical cleaned text we keep the LATER one (the completed re-run) and
    drop the earlier (interrupted) segment. Only adjacent + exact-match collapses,
    so legitimate non-adjacent repeats are untouched; a clean run is a no-op.
    """
    def _is_user(e: dict) -> bool:
        msg = e.get("message")
        return (
            e.get("type") == "message"
            and isinstance(msg, dict)
            and msg.get("role") == "user"
        )

    def _utext(e: dict) -> str:
        content = e.get("message", {}).get("content")
        if isinstance(content, str):
            return _strip_user_turn_prefix(content)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    return _strip_user_turn_prefix(b.get("text", ""))
        return ""

    # Build (text, [entries]) segments; preamble before the first user -> text None.
    segments: List[tuple[Optional[str], List[dict]]] = []
    cur: List[dict] = []
    cur_text: Optional[str] = None
    for e in entries:
        if isinstance(e, dict) and _is_user(e):
            if cur:
                segments.append((cur_text, cur))
            cur = [e]
            cur_text = _utext(e)
        else:
            cur.append(e)
    if cur:
        segments.append((cur_text, cur))

    deduped: List[tuple[Optional[str], List[dict]]] = []
    for seg in segments:
        if (
            deduped
            and seg[0]                      # non-empty user text
            and deduped[-1][0] == seg[0]    # identical to the immediately prior turn
        ):
            deduped[-1] = seg               # keep the later (re-run) segment
        else:
            deduped.append(seg)
    return [e for _, seg in deduped for e in seg]


def build_trajectory_from_jsonl(
    task: Task,
    entries: List[dict],
    attachments: Optional[Iterable[Mapping]] = None,
    turns: Optional[Iterable[Mapping]] = None,
    media_handler: Optional[MediaHandler] = None,
    s3_bucket: str = "",
    s3_prefix: str = "",
    s3_region: str = "",
    usage_top_level: Optional[Mapping] = None,
    workspace_root: Optional[Path] = None,
) -> dict:
    """Produce reference-schema delivery JSON from OpenClaw JSONL entries.

    - `entries`: parsed JSONL dicts (one per OpenClaw event line).
    - `attachments`: input file dicts (name, mimeType, storedAs, size).
    - `turns`: optional turn-feedback dicts (prompt, hints, is_auto_hint).
    - `media_handler`: callable(messages, task_id) -> messages, used to
      rewrite inline media `source` fields. Defaults to no-op.
    - `usage_top_level`: 4-key projection of agent usage. Coerced to
      `{input_tokens, output_tokens, cached_input_tokens, cost_usd}`;
      missing/malformed fields default to 0.

    Output_artifacts is initially empty (or transcript-derived from turns).
    The caller is expected to merge workspace-collected records before
    persisting the trajectory.
    """
    attachments_list = list(attachments or [])
    turns_list = list(turns or [])

    input_files = build_input_files_manifest(
        task, attachments_list, s3_bucket=s3_bucket, s3_prefix=s3_prefix,
    )
    # Detect deliverables from the actual conversation (tool calls + responses),
    # not the feedback `turns` (which carry no tool calls). Exclude the task's
    # input files so reading an attachment isn't mistaken for an output.
    artifact_turns = _artifact_turns_from_entries(entries) + turns_list
    input_filenames = [
        (a.get("storedAs") or a.get("name") or "") for a in attachments_list
    ]
    output_artifacts = build_output_artifacts(
        artifact_turns,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        s3_region=s3_region,
        task_id=task.task_id,
        input_filenames=input_filenames,
        workspace_root=workspace_root,
    )

    # Collapse a gateway-recovery re-issue of the final turn (report §11) before
    # building messages, so output.json + delivery.json show the canonical turns.
    entries = _dedupe_reissued_turns(entries)

    messages: List[dict] = []
    last_kept_id: Optional[str] = None
    seen_user_msg = False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if not role:
            continue
        if role == "user":
            seen_user_msg = True
        elif role == "system" and not seen_user_msg:
            continue

        msg = sanitize_jsonl_message(msg)
        if role == "user":
            # Surface the exact user prompt — drop harness scaffolding
            # (boilerplate preamble + [UTC] / [TURN N] headers).
            msg = _strip_user_prefix_from_message(msg)
        entry_id = entry.get("id", "")
        parent_id = last_kept_id if last_kept_id else entry.get("parentId", "")
        messages.append({
            "type": "message",
            "id": entry_id,
            "parentId": parent_id or "",
            "timestamp": entry.get("timestamp", ""),
            "message": msg,
        })
        last_kept_id = entry_id

    if turns_list:
        messages = _wrap_messages_with_turn_feedback(messages, turns_list)
    else:
        messages = [_wrap_trajectory_message(m) for m in messages]
    messages = _unwrap_trajectory_messages(messages)

    if media_handler is not None:
        messages = media_handler(messages, task.task_id or task.id)

    return {
        "session_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trajectory": {
            "meta_info": build_trajectory_meta_info(
                task, input_files, output_artifacts
            ),
            "input_modalities": build_input_modalities(input_files),
            "output_modalities": build_output_modalities(output_artifacts),
        },
        "input_files": input_files,
        "output_artifacts": output_artifacts,
        "messages": messages,
        "usage": _coerce_top_usage(usage_top_level),
    }


def _count_thinking_blocks(messages) -> tuple[int, list[dict]]:
    total = 0
    samples: list[dict] = []
    for entry in messages or []:
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                total += 1
                txt = block.get("thinking", "")
                samples.append({
                    "len": len(txt) if isinstance(txt, str) else 0,
                    "has_signature": bool(block.get("thinkingSignature")),
                })
    return total, samples
