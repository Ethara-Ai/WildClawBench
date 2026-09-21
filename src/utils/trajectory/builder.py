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
from typing import Any, Callable, Iterable, List, Mapping, NamedTuple, Optional

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
    prev_user_text = ""

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
            # A user row repeating the previous one is the harness re-sending
            # a stalled turn, not a new turn: advancing here would shift every
            # later turn's feedback onto the wrong message. Compared with the
            # agent's timestamp prefix removed — that strip only runs at the
            # end of build_trajectory_from_jsonl, and a stall retry is >=600s
            # later, so the two copies never carry the same stamp.
            bare_user_text = _TURN_TS_RE.sub("", user_text, count=1).strip()
            duplicate_resend = bool(bare_user_text) and bare_user_text == prev_user_text
            if bare_user_text:
                prev_user_text = bare_user_text
            if (matched or user_text) and not duplicate_resend:
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
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cached_write_tokens": 0,
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
    # cache-write may arrive under either spelling depending on the source.
    cached_write = _int("cached_write_tokens") or _int("cache_write_tokens")
    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cached_input_tokens": _int("cached_input_tokens"),
        "cache_read_tokens": _int("cache_read_tokens"),
        "cache_write_tokens": cached_write,
        "cached_write_tokens": cached_write,
        "cost_usd": round(cost, 6),
    }


# The OpenClaw agent binary prepends a wall-clock timestamp to every user turn
# it delivers, e.g. "[Mon 2026-06-15 14:50 UTC] <prompt>". That stamp (a) uses
# the real run date, not the persona date (IAN report H2), and (b) is harness
# metadata, not part of the user's actual message. Strip a single leading
# "[Ddd YYYY-MM-DD HH:MM TZ]" token from user message text so published
# output.json shows the clean prompt the task authored.
_TURN_TS_RE = re.compile(
    r"^\s*\[[A-Za-z]{3}\s+\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?\s+[A-Za-z]{2,5}\]\s*"
)


def _strip_turn_timestamp_prefix(messages: List[dict]) -> None:
    """Remove the leading agent-stamped timestamp from user message text, in place."""
    for entry in messages or []:
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _TURN_TS_RE.sub("", content, count=1)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    block["text"] = _TURN_TS_RE.sub("", block["text"], count=1)
                    break  # only the first text block carries the prefix


# --------------------------------------------------------------------------- #
# Branch-aware message selection.
#
# chat.jsonl is append-only and the entries form a TREE, not a list: every entry
# (session / model_change / thinking_level_change / custom / compaction /
# message) carries a `parentId`. A normal run is a straight line, so walking the
# file top-to-bottom and walking the parentId chain give the same answer.
#
# They diverge when a turn is delivered twice — e.g. willie_prince run_3, where a
# gateway 1006 made the agent's embedded fallback re-deliver a prompt: two user
# rows were appended as SIBLINGS under the same parentId and the model answered
# only one of them. The unanswered sibling is an orphan branch: it is in the
# file, but it is not in the conversation. The file-order walk linearises it into
# a real turn (and rewrites its parentId to look like one), so output.json — the
# thing the judge reads — showed 21 user turns for a 20-turn task.
#
# Selection therefore follows the parentId chain from the conversation HEAD
# instead of the file. Everything else (the system-before-first-user drop, the
# parentId rewrite, timestamp-prefix stripping, turn feedback) is untouched and
# still runs over the selected entries, so a clean run is byte-identical.
#
# The chain is only ever allowed to REMOVE entries the file-order walk kept:
# `kept_chain` is built by filtering the file-order result, and the invariant is
# re-verified explicitly before use. Anything unverifiable — missing/duplicate
# id, dangling parentId, cycle, an empty result — falls back to the file-order
# walk verbatim and flags `chain_walk_fallback`. This code must never be the
# reason a run produces no trajectory.
# --------------------------------------------------------------------------- #


class _ChainWalkError(Exception):
    """The parentId graph cannot be trusted; caller must use the file order."""


class _NotASingleTree(_ChainWalkError):
    """Entries are not one threaded conversation, so `parentId` says nothing.

    Not corruption: it is what concatenated session files and synthetic entry
    lists carrying no `parentId` at all look like. Every entry is its own root,
    so branch-vs-trunk is undecidable and the file order is the only ordering
    there is. Separate from :class:`_ChainWalkError` purely so this expected
    shape is not logged as an error.
    """


class _ChainSelection(NamedTuple):
    kept: List[dict]
    pruned_ids: List[str]
    fallback: bool


def _iter_kept_message_entries(entries: List[dict]) -> Iterable[dict]:
    """Yield, in file order, the entries the trajectory walk keeps as messages.

    Single source of truth for "is this entry a message we publish" — used both
    to emit the trajectory and to compare the two walks, so the two can't drift.
    """
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
        yield entry


def _index_entries_by_id(entries: List[dict]) -> dict[str, int]:
    """Map entry id -> file position for EVERY entry type.

    All entry types interleave in the parentId chain (175 of 568 archived
    sessions thread through a `compaction` entry), so the index cannot be
    restricted to messages. A missing or duplicated id makes the graph
    unresolvable.
    """
    index: dict[str, int] = {}
    for pos, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise _ChainWalkError(
                "entry at position %d has no usable id (type=%r)"
                % (pos, entry.get("type"))
            )
        if entry_id in index:
            raise _ChainWalkError("duplicate entry id %r" % entry_id)
        index[entry_id] = pos
    return index


def _parent_id_of(entry: Mapping, index: Mapping[str, int]) -> Optional[str]:
    """Resolved parent id, or None at the root. Dangling parents are fatal."""
    parent_id = entry.get("parentId") or ""
    if not isinstance(parent_id, str) or not parent_id:
        return None
    if parent_id not in index:
        raise _ChainWalkError(
            "entry %r points at missing parent %r" % (entry.get("id"), parent_id)
        )
    return parent_id


def _chain_depth(
    start_id: str,
    entries: List[dict],
    index: Mapping[str, int],
    memo: dict[str, int],
) -> int:
    """Number of entries from ``start_id`` up to the root, inclusive (memoised)."""
    path: List[str] = []
    on_path: set = set()
    base = 0
    cur: Optional[str] = start_id
    while cur is not None:
        if cur in memo:
            base = memo[cur]
            break
        if cur in on_path:
            raise _ChainWalkError("parentId cycle through %r" % cur)
        on_path.add(cur)
        path.append(cur)
        cur = _parent_id_of(entries[index[cur]], index)
    for offset, node in enumerate(reversed(path)):
        memo[node] = base + offset + 1
    return memo[start_id]


def _session_entry_ids(entries: List[dict]) -> set:
    """Ids of the `session` file headers.

    OpenClaw opens each session file with `{"type":"session","id":"chat",...}`.
    It is a file header, not a conversation node: the real root (a
    `model_change`) carries `parentId: null` beside it. Counting it as a root
    would make every genuine chat.jsonl look like a two-root forest.
    """
    return {
        e.get("id")
        for e in entries
        if isinstance(e, dict) and e.get("type") == "session" and e.get("id")
    }


def _require_single_tree(
    entries: List[dict], index: Mapping[str, int], session_ids: set
) -> None:
    """Refuse to walk unless every entry hangs off exactly one conversation root."""
    roots = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        if entry.get("id") in session_ids:
            continue
        parent = _parent_id_of(entry, index)
        if parent is None or parent in session_ids:
            roots.append(entry.get("id"))
    if len(roots) != 1:
        raise _NotASingleTree(
            "expected exactly one conversation root, found %d" % len(roots)
        )


def _select_head_id(
    entries: List[dict], index: Mapping[str, int], session_ids: set
) -> str:
    """The conversation HEAD: deepest leaf, ties broken by latest in file order.

    Deepest-leaf rather than "last line in the file" because a run can end on an
    unanswered re-send — then the orphan IS the last line, and taking it as head
    would publish the orphan and drop the real conversation.
    """
    has_child: set = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        parent_id = _parent_id_of(entry, index)
        if parent_id:
            has_child.add(parent_id)

    memo: dict[str, int] = {}
    best_key = (-1, -1)
    best_id = ""
    for entry_id, pos in index.items():
        if entry_id in has_child or entry_id in session_ids:
            continue
        key = (_chain_depth(entry_id, entries, index, memo), pos)
        if key > best_key:
            best_key = key
            best_id = entry_id
    if not best_id:
        raise _ChainWalkError("no leaf entry found")
    return best_id


def _ids_on_chain(head_id: str, entries: List[dict], index: Mapping[str, int]) -> set:
    """Every entry id from ``head_id`` back to the root, all entry types."""
    seen: set = set()
    cur: Optional[str] = head_id
    while cur is not None:
        if cur in seen:
            raise _ChainWalkError("parentId cycle through %r" % cur)
        seen.add(cur)
        cur = _parent_id_of(entries[index[cur]], index)
    return seen


def _verify_chain_is_a_subsequence(
    kept_linear: List[dict], kept_chain: List[dict]
) -> None:
    """Re-check the safety invariant: the chain may only DROP, never add/reorder."""
    linear_ids = [e.get("id") for e in kept_linear]
    chain_ids = [e.get("id") for e in kept_chain]
    linear_set = set(linear_ids)
    chain_set = set(chain_ids)
    if not chain_set <= linear_set:
        raise _ChainWalkError("chain walk added messages the file-order walk dropped")
    pruned = linear_set - chain_set
    if chain_ids != [i for i in linear_ids if i not in pruned]:
        raise _ChainWalkError("chain walk reordered messages")


def _describe_pruned_entry(entry: Mapping) -> str:
    """`id/role/timestamp-prefix` summary of a pruned row, for the WARNING log."""
    msg = entry.get("message") if isinstance(entry.get("message"), Mapping) else {}
    role = msg.get("role", "") if isinstance(msg, Mapping) else ""
    text = ""
    content = msg.get("content") if isinstance(msg, Mapping) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                text = block["text"]
                break
    return "id=%s role=%s delivery_prefix=%s" % (
        entry.get("id", ""),
        role or "?",
        bool(_TURN_TS_RE.match(text or "")),
    )


def _select_branch_aware_entries(
    entries: List[dict], kept_linear: List[dict]
) -> _ChainSelection:
    """Restrict ``kept_linear`` to the messages on the HEAD's parentId chain.

    Falls back to ``kept_linear`` unchanged on any doubt whatsoever.
    """
    try:
        index = _index_entries_by_id(entries)
        if not index:
            return _ChainSelection(kept_linear, [], False)
        session_ids = _session_entry_ids(entries)
        _require_single_tree(entries, index, session_ids)
        head_id = _select_head_id(entries, index, session_ids)
        chain = _ids_on_chain(head_id, entries, index)

        kept_chain = [e for e in kept_linear if e.get("id") in chain]
        pruned_ids = [
            str(e.get("id") or "") for e in kept_linear if e.get("id") not in chain
        ]
        if pruned_ids and not kept_chain:
            raise _ChainWalkError("chain walk would drop every message")
        _verify_chain_is_a_subsequence(kept_linear, kept_chain)

        if pruned_ids:
            logger.warning(
                "Trajectory: pruned %d orphaned branch message(s) absent from the "
                "answered conversation: %s",
                len(pruned_ids),
                "; ".join(
                    _describe_pruned_entry(e)
                    for e in kept_linear
                    if e.get("id") not in chain
                ),
            )
        return _ChainSelection(kept_chain, pruned_ids, False)
    except _NotASingleTree as exc:
        logger.info(
            "Trajectory: entries are not one threaded conversation (%s); using "
            "file order", exc,
        )
        return _ChainSelection(kept_linear, [], True)
    except _ChainWalkError as exc:
        logger.error(
            "Trajectory: parentId chain walk unusable (%s); falling back to "
            "file-order walk", exc,
        )
        return _ChainSelection(kept_linear, [], True)
    except Exception as exc:  # never let selection break a run
        logger.error(
            "Trajectory: parentId chain walk raised (%s); falling back to "
            "file-order walk", exc, exc_info=True,
        )
        return _ChainSelection(kept_linear, [], True)


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
    - `usage_top_level`: projection of agent usage. Coerced to
      `{input_tokens, output_tokens, cached_input_tokens, cache_read_tokens,
      cache_write_tokens, cost_usd}`; missing/malformed fields default to 0.

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

    selection = _select_branch_aware_entries(
        entries, list(_iter_kept_message_entries(entries))
    )

    messages: List[dict] = []
    last_kept_id: Optional[str] = None

    for entry in selection.kept:
        msg = sanitize_jsonl_message(entry.get("message", {}))
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

    _strip_turn_timestamp_prefix(messages)

    if media_handler is not None:
        messages = media_handler(messages, task.task_id or task.id)

    # Operator-facing only: build_published_trajectory rebuilds meta_info from a
    # fixed five-key list, so these never reach output.json or the bundle.
    meta_info = build_trajectory_meta_info(task, input_files, output_artifacts)
    meta_info["pruned_orphans"] = len(selection.pruned_ids)
    meta_info["pruned_orphan_ids"] = list(selection.pruned_ids)
    meta_info["chain_walk_fallback"] = selection.fallback

    return {
        "session_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trajectory": {
            "meta_info": meta_info,
            "input_modalities": build_input_modalities(input_files),
            "output_modalities": build_output_modalities(output_artifacts),
        },
        "input_files": input_files,
        "output_artifacts": output_artifacts,
        "messages": messages,
        "usage": _coerce_top_usage(usage_top_level),
    }


# --------------------------------------------------------------------------- #
# Published-trajectory hygiene (applied only to the slim output.json form).
#
# The published output.json is a deliverable that must match the canonical
# Golden_Trajectory.json shape. Relative to the rich internal trajectory it must:
#   * carry ONLY the canonical inner-message keys: {role, content} (+ toolCallId /
#     toolName / isError on toolResult) — no per-turn usage/cost (lives in
#     usage.json, IAN Pointer 5), api / provider / model / stopReason / timestamp /
#     details operator metadata;
#   * strip the harness routing token "[[reply_to_current]]" from assistant text;
#   * neutralize raw infra-failure noise inside tool results (curl connection
#     frames, pip DNS failures, tracebacks, missing-binary `sh:` errors, bare mock
#     404/500 probe bodies, provider errors) to a short, honest marker — the
#     failure stays visible, we do not fabricate success;
#   * redact the internal mock pod hostname (mocks-task-<slug>-<hash>) from any
#     tool-result text that survives.
# The rich in-memory trajectory is left untouched so graders/judges still see
# exactly what the agent saw. We never rewrite the model's own narrative text.
# --------------------------------------------------------------------------- #

# Canonical inner-message keys (Golden_Trajectory.json). Everything else on an
# inner message is operator/transport metadata and is dropped on publish.
_PUBLISHED_KEYS_COMMON = ("role", "content")
_PUBLISHED_KEYS_TOOLRESULT = ("toolCallId", "toolName", "isError")

# Harness routing/template token that prefixes assistant replies; never canonical.
_REPLY_TOKEN_RE = re.compile(r"\[\[reply_to_current\]\]\s*")

# Internal mock pod hostname: mocks-task-<task_slug>-<pod_hash>. Leaks the task
# slug + pod hash into curl traces; redact to a stable, non-identifying alias.
_MOCK_HOST_RE = re.compile(r"mocks-task-[a-z0-9_]+-[0-9a-f]+", re.I)

# High-precision signatures of environment/infra failures in tool-result text.
# Each maps to the neutral marker that replaces the whole noisy text block.
_INFRA_NOISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Connection refused|Failed to connect to|connect to .+? port \d+ failed", re.I),
     "upstream service unavailable"),
    (re.compile(r"Temporary failure in name resolution|"
                r"Could not find a version that satisfies the requirement|"
                r"ModuleNotFoundError: No module named", re.I),
     "package/network unavailable in sandbox"),
    (re.compile(r"^\s*sh: \d+: .+?: not found", re.M),
     "command-line tool not installed"),
    (re.compile(r"HTTP/1\.1 5\d\d|Internal Server Error"),
     "upstream service error"),
    (re.compile(r"Bedrock is unable to process your request", re.I),
     "provider error"),
]

# A tool-result whose entire body is just repeated mock "not found" / no-result
# probe responses (the agent blindly probing a down endpoint). Matched only when
# NOTHING else of substance remains, so genuine API payloads are never touched.
_PROBE_FRAGMENT_RE = re.compile(
    r'^\s*(?:\{"detail"\s*:\s*"Not Found"\}|'
    r'\{\s*"status"\s*:\s*"ZERO_RESULTS".*?\})\s*$',
    re.S,
)


def _neutralize_infra_text(text: str) -> Optional[str]:
    """Return a neutral marker if ``text`` is dominated by infra-failure noise,
    else ``None`` (keep the original). Conservative by construction: only fires
    on the high-precision signatures above or an all-probe-noise body."""
    if not isinstance(text, str) or not text.strip():
        return None
    for rx, label in _INFRA_NOISE_PATTERNS:
        if rx.search(text):
            return f"[tool output omitted — {label}]"
    # Split on the `---` probe delimiter only (NOT newlines), so a multi-line
    # JSON probe body stays one fragment and matches under re.S.
    fragments = [f for f in re.split(r"-{2,}", text) if f.strip()]
    if fragments and all(_PROBE_FRAGMENT_RE.match(f.strip()) for f in fragments):
        return "[tool output omitted — endpoint returned no data]"
    return None


def sanitize_tool_result_text(text: str) -> str:
    """Publish-safe form of one tool-result text: neutralize infra-failure noise
    to a short marker, else redact the internal mock pod hostname. Shared by the
    published-trajectory emitter AND the golden generator (which copies real-run
    tool results verbatim), so both surfaces get identical hygiene."""
    if not isinstance(text, str) or not text:
        return text
    replacement = _neutralize_infra_text(text)
    if replacement is not None:
        return replacement
    return _MOCK_HOST_RE.sub("mock-services", text)


def _clean_published_text(text: str, role: str) -> str:
    """Apply role-appropriate text hygiene to one text block, in priority order:
    strip the assistant routing token; neutralize infra noise / redact the mock
    hostname in tool results. Model narrative text is only ever touched to remove
    the harness routing token — never reworded."""
    if not isinstance(text, str) or not text:
        return text
    if role == "assistant":
        return _REPLY_TOKEN_RE.sub("", text)
    if role == "toolResult":
        return sanitize_tool_result_text(text)
    return text


def _scrub_published_message(inner: dict) -> dict:
    """Return a publish-safe copy of one inner message: keep only the canonical
    keys for its role, then apply text hygiene to each text block. Copies only
    what it needs so the caller's rich trajectory (kept for grading + the harbor
    bundle) is never altered."""
    role = inner.get("role")
    keep = set(_PUBLISHED_KEYS_COMMON)
    if role == "toolResult":
        keep.update(_PUBLISHED_KEYS_TOOLRESULT)
    out = {k: v for k, v in inner.items() if k in keep}

    content = out.get("content")
    if isinstance(content, list):
        new_content = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                cleaned = _clean_published_text(block.get("text", ""), role)
                if cleaned != block.get("text", ""):
                    block = {**block, "text": cleaned}
                    changed = True
            new_content.append(block)
        if changed:
            out["content"] = new_content
    return out


def _task_attr(task: Any, key: str) -> str:
    """Read a field from a Task dataclass OR a plain dict, returning "" if absent."""
    if task is None:
        return ""
    if isinstance(task, Mapping):
        val = task.get(key, "")
    else:
        val = getattr(task, key, "")
    return val if isinstance(val, str) else ("" if val is None else str(val))


def _load_spawn_tree(spawn_tree_path: Optional[Path]) -> list[dict]:
    """Read the ``spawn_tree.jsonl`` ledger (one NDJSON row per sub-agent spawn).

    Returns [] if the path is missing or unreadable. Mirrors the june-7
    delivery pipeline so sub-agent trajectories embed in spawn order.
    """
    rows: list[dict] = []
    if spawn_tree_path is None or not spawn_tree_path.is_file():
        return rows
    try:
        with spawn_tree_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _load_sub_agent_trajectories(
    subagents_dir: Optional[Path],
    spawn_rows: list[dict] | None = None,
) -> dict[str, dict]:
    """Embed every captured sub-agent delivery, keyed by spawn_id.

    Reads ``{spawn_id}.delivery.json`` files written by the spawn runtime
    (``src/utils/subagent_director.py``). Ordering follows ``spawn_tree.jsonl``
    (preserving per-turn spawn sequence), then any remaining delivery files on
    disk. Returns {} when there are no sub-agents (so the published schema is
    unchanged for non-multi-agent runs).
    """
    if subagents_dir is None or not subagents_dir.is_dir():
        return {}

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for r in spawn_rows or []:
        if not isinstance(r, dict):
            continue
        sid = r.get("spawn_id")
        if sid and sid not in seen:
            seen.add(sid)
            ordered_ids.append(sid)
    for f in sorted(subagents_dir.glob("*.delivery.json")):
        sid = f.name[: -len(".delivery.json")]
        if sid and sid not in seen:
            seen.add(sid)
            ordered_ids.append(sid)

    out: dict[str, dict] = {}
    for spawn_id in ordered_ids:
        f = subagents_dir / f"{spawn_id}.delivery.json"
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out[spawn_id] = data
    return out


def _project_published_messages(raw_messages: List[Any]) -> List[Any]:
    """Project raw trajectory messages into the published wrapper shape.

    Canonical wrapper (Golden_Trajectory.json): ``type, id, parentId, timestamp,
    message`` with ``parentId`` threaded to the previous message's id. Internal
    bookkeeping (turn_index) is dropped and per-message scrubbing applied. Shared
    by the parent trajectory and each native sub-agent (child) trajectory so both
    have identical message schemas.
    """
    messages: List[Any] = []
    prev_id = ""  # root message's parentId is "" (no parent); else previous id.
    for m in raw_messages:
        if not isinstance(m, dict):
            messages.append(m)
            continue
        mid = m.get("id", "")
        inner = m.get("message")
        messages.append({
            "type": m.get("type", "message"),
            "id": mid,
            "parentId": prev_id,
            "timestamp": m.get("timestamp", ""),
            "message": _scrub_published_message(inner) if isinstance(inner, dict) else inner,
        })
        prev_id = mid
    return messages


def build_published_trajectory(
    traj: Mapping[str, Any],
    task: Any,
    completion_status: str = "",
    *,
    subagents_dir: Optional[Path] = None,
    spawn_tree_path: Optional[Path] = None,
) -> dict:
    """Project the rich internal trajectory dict into the published
    ``{"messages": [...], "meta_info": {...}}`` schema.

    The internal dict (from :func:`build_trajectory_from_jsonl`) carries
    session_id / trajectory / input_files / usage etc. for the harbor + grading
    pipeline; this slim form is what is written to ``output.json`` on disk and
    into the harbor bundle. ``meta_info`` holds exactly five keys, in the
    reference Golden_Trajectory.json order: task_type, task_description,
    task_completion_status, system_prompt, platform. Per-message ``turn_index``
    (internal bookkeeping) is stripped so the message shape matches the
    reference trajectory exactly.

    Published-only hygiene (see :func:`_scrub_published_message`): the per-turn
    ``usage``/cost block is dropped (cost lives in usage.json), and infra-failure
    noise inside tool results (connection-refused curl frames, pip/DNS failures,
    tracebacks, bare mock 404/500 probe bodies) is replaced with a short neutral
    marker. The rich in-memory ``traj`` is left untouched so grading still sees
    the agent's real tool output.
    """
    messages = _project_published_messages(traj.get("messages") or [])

    # The caller's status is run-level ("no fatal error" => success), which is
    # blind to a parent killed by the exec approval gate: the run exits clean
    # while its last word is an /approve plea, and the trajectory publishes as
    # success, poisoning downstream scoring and delivery triage. Child lanes
    # have been derived from that ending since the 2026-07-06 audit; parents
    # take the SAME signal here. Only that signal — the classifier's `aborted`
    # verdicts are child-lane shape checks, and only a rubber-stamp status is
    # overridden, so an explicit failure verdict always wins.
    completion_status = completion_status or ""
    if completion_status in ("", "success") and ends_with_approval_plea(messages):
        completion_status = "blocked_on_approval"

    inner_meta = (traj.get("trajectory") or {}).get("meta_info") or {}
    platform = inner_meta.get("platform") or "Linux"
    # task_type prefers the explicit field, else falls back to the L2 taxonomy
    # slug the internal meta already computed (snake_case, e.g. research_and_analysis).
    task_type = _task_attr(task, "task_type") or inner_meta.get("taxonomy_l2") or ""

    # Key order matches the reference Golden_Trajectory.json exactly:
    # [task_type, task_description, task_completion_status, system_prompt, platform].
    meta_info = {
        "task_type": task_type,
        "task_description": _task_attr(task, "task_description"),
        "task_completion_status": completion_status or "",
        "system_prompt": _task_attr(task, "system_prompt"),
        "platform": platform,
    }
    published = {"meta_info": meta_info, "messages": messages}

    # Multi-agent: embed captured sub-agent trajectories (keyed by spawn_id).
    # Only attach the key when there is at least one sub-agent, so the published
    # schema for ordinary (single-agent) runs stays byte-identical.
    sub_trajs = _load_sub_agent_trajectories(
        subagents_dir, _load_spawn_tree(spawn_tree_path)
    )
    if sub_trajs:
        published["sub_agent_trajectories"] = sub_trajs

    return published


# ---------------------------------------------------------------------------
# Golden layout (native sessions_spawn): parent.json + children/ + spawn_tree/
#
# Replaces the slim output.json form when a task runs with native multi-agent
# spawning. The on-disk shape mirrors the reference goldens (Larry_Bates /
# dawn_mitchell):
#
#   <run_dir>/parent.json                      (meta_info + messages)
#   <run_dir>/children/01_<name>.json          (one per native subagent)
#   <run_dir>/spawn_tree/parent_spawn_tree.txt (human-readable tree)
#
# The serializer below is pure/deterministic (testable with fixtures). The
# correlation of an on-disk child session to its parent sessions_spawn call is
# done by spawn order and is the one piece pending live-run validation.
# ---------------------------------------------------------------------------


def extract_spawn_calls(parent_messages: List[Any]) -> List[dict]:
    """Pull native ``sessions_spawn`` calls from the parent's messages, in order.

    Returns ``[{"name": ..., "prompt": ...}, ...]`` — one per spawn. ``name``
    titles the child file; ``prompt`` becomes the child's task_description.
    """
    calls: List[dict] = []
    for m in parent_messages or []:
        inner = m.get("message") if isinstance(m, dict) else None
        content = inner.get("content") if isinstance(inner, dict) else None
        if not isinstance(content, list):
            continue
        for p in content:
            if isinstance(p, dict) and p.get("type") == "toolCall" \
                    and p.get("name") == "sessions_spawn":
                args = p.get("arguments") or {}
                calls.append({
                    "name": str(args.get("name", "") or ""),
                    "prompt": str(args.get("prompt", "") or ""),
                })
    return calls


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "subagent"


# Sub-agent lanes killed mid-run (gateway teardown quiesce race, upstream
# stream aborts, approval-gate deadlock) still land on disk with whatever
# messages made it — previously all stamped "success" unconditionally.
# Audited 2026-07-06 across 194 bundled child trajectories: 6 damaged lanes,
# every one labeled success. Each failure mode leaves a distinct fingerprint
# in the FINAL message, so completion is derived from the ending instead.
_APPROVE_PLEA_RE = re.compile(r"^\s*/approve\b")

_APPROVAL_ENDED_REASON = "ended pleading for exec approval (no channel in headless runs)"


class _FinalTurn(NamedTuple):
    """The parsed ending of a projected message list. A non-empty
    ``abort_reason`` means it does not end on a readable assistant turn, and
    ``text``/``has_tool_call`` carry no signal."""

    text: str = ""
    has_tool_call: bool = False
    abort_reason: str = ""


def _parse_final_turn(messages: List[Any]) -> _FinalTurn:
    if not messages:
        return _FinalTurn(abort_reason="no messages recorded")
    last = messages[-1] if isinstance(messages[-1], dict) else {}
    inner = last.get("message") if isinstance(last.get("message"), dict) else {}
    role = inner.get("role")
    if role != "assistant":
        return _FinalTurn(abort_reason=f"ends on role={role!r}, not an assistant report")
    content = inner.get("content")
    if isinstance(content, str):
        return _FinalTurn(text=content)
    if isinstance(content, list):
        if not content:
            return _FinalTurn(abort_reason="final assistant turn has empty content (stream cut)")
        return _FinalTurn(
            text="".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ),
            has_tool_call=any(
                isinstance(b, dict) and b.get("type") in ("toolCall", "tool_use")
                for b in content
            ),
        )
    return _FinalTurn(abort_reason="final assistant turn has no content")


def _turn_is_approval_plea(final: _FinalTurn) -> bool:
    return not final.abort_reason and bool(_APPROVE_PLEA_RE.match(final.text))


def ends_with_approval_plea(messages: List[Any]) -> bool:
    """True when the FINAL message is an assistant turn opening with an
    ``/approve <id>`` plea — the fingerprint of a lane the exec approval gate
    killed (headless runs have no channel to answer it; the obfuscation
    detector is NOT covered by exec.security=full).

    The single detector for both lanes: classify_child_completion routes its
    ``blocked_on_approval`` verdict through it, and build_published_trajectory
    stamps the parent from it. Matching is anchored to the start of the final
    turn, so an agent that merely mentions approval — or pleaded mid-run and
    then recovered — is not flagged.
    """
    return _turn_is_approval_plea(_parse_final_turn(messages))


def classify_child_completion(messages: List[Any]) -> tuple[str, str]:
    """Derive (completion_status, ended_reason) from a child's projected messages.

    Statuses:
      * ``success`` — ends with a text-bearing assistant turn (the report).
      * ``aborted`` — ends mid-flight: empty assistant content (stream cut),
        thinking-only final turn, a toolCall with no toolResult after it, or
        a non-assistant final message.
      * ``blocked_on_approval`` — final text is an ``/approve <id>`` plea
        (see :func:`ends_with_approval_plea`).
    """
    final = _parse_final_turn(messages)
    if final.abort_reason:
        return "aborted", final.abort_reason
    if _turn_is_approval_plea(final):
        return "blocked_on_approval", _APPROVAL_ENDED_REASON
    if final.has_tool_call:
        # A toolResult always lands as the NEXT message; a trailing toolCall
        # means the lane died waiting for it.
        return "aborted", "final turn issues a toolCall with no toolResult"
    if not final.text.strip():
        return "aborted", "final assistant turn is thinking-only (no report text)"
    return "success", "ends with assistant report text"


def build_child_trajectory(
    *,
    session_key: str,
    raw_messages: List[Any],
    name: str,
    description: str,
    parent_session: str,
    platform: str = "Linux",
    completion_status: str = "success",
) -> dict:
    """Project one native sub-agent session into the golden child schema.

    Matches the reference ``children/NN_*.json`` meta_info exactly:
    ``task_name, task_description, task_completion_status, parent_session,
    session_key, platform, message_count``.

    ``completion_status="success"`` (the default) is treated as a rubber
    stamp and re-derived from the ending via classify_child_completion; an
    explicit non-success status from the caller is preserved as-is. No
    ``ended_reason`` key here — this meta_info is an exact reference-schema
    contract (see test_golden_layout key-set assertion).
    """
    messages = _project_published_messages(raw_messages)
    if completion_status == "success":
        completion_status, _ = classify_child_completion(messages)
    return {
        "meta_info": {
            "task_name": name,
            "task_description": description,
            "task_completion_status": completion_status,
            "parent_session": parent_session,
            "session_key": session_key,
            "platform": platform,
            "message_count": len(messages),
        },
        "messages": messages,
    }


def render_spawn_tree_text(
    *,
    root_session: str,
    task_type: str,
    completion_status: str,
    platform: str,
    cluster: str,
    n_messages: int,
    children: List[Mapping[str, Any]],
) -> str:
    """Render the human-readable parent_spawn_tree.txt summary."""
    bar = "=" * 72
    lines = [
        bar,
        "  SPAWN TREE -- parent.json",
        bar,
        "",
        "META",
        f"  Root:      {root_session}",
        f"  Type:      {task_type} -> {completion_status}",
        f"  Platform:  {platform} | Cluster: {cluster}",
        f"  Messages:  {n_messages} | Children: {len(children)}",
        "",
        "CHILDREN",
    ]
    if not children:
        lines.append("  (none)")
    for i, c in enumerate(children, 1):
        meta = c.get("meta_info", {})
        lines.append(
            f"  {i:02d}. {meta.get('task_name', '?')} "
            f"[{meta.get('task_completion_status', '?')}] "
            f"-> {meta.get('session_key', '?')} "
            f"({meta.get('message_count', 0)} msgs)"
        )
    return "\n".join(lines) + "\n"


def write_golden_layout(
    output_dir: Path,
    *,
    parent_published: Mapping[str, Any],
    children_sessions: List[Mapping[str, Any]],
    root_session: str,
    cluster: str = "",
    platform: str = "Linux",
) -> dict:
    """Write parent.json + children/NN_<name>.json + spawn_tree/ to ``output_dir``.

    ``parent_published`` is the slim {meta_info, messages} from
    :func:`build_published_trajectory`. ``children_sessions`` is a list of
    ``{"session_key", "raw_messages", "completion_status"}`` harvested from the
    native session store (see jsonl_reader.read_sessions_grouped), in spawn
    order. Child names/descriptions are taken from the parent's sessions_spawn
    calls, correlated by order.

    Returns the augmented parent dict (also written to parent.json).
    """
    output_dir = Path(output_dir)
    parent_messages = list(parent_published.get("messages") or [])
    spawn_calls = extract_spawn_calls(parent_messages)

    # Build child trajectories, correlating each session to its spawn call by order.
    children: List[dict] = []
    spawned_keys: List[str] = []
    for idx, sess in enumerate(children_sessions):
        skey = str(sess.get("session_key", "") or f"child-{idx+1}")
        call = spawn_calls[idx] if idx < len(spawn_calls) else {}
        name = call.get("name") or f"subagent-{idx+1}"
        child = build_child_trajectory(
            session_key=skey,
            raw_messages=list(sess.get("raw_messages") or []),
            name=name,
            description=call.get("prompt", ""),
            parent_session=root_session,
            platform=platform,
            completion_status=str(sess.get("completion_status", "success")),
        )
        children.append(child)
        spawned_keys.append(skey)

    # Parent meta_info: golden superset (cluster + agents added).
    base_meta = dict(parent_published.get("meta_info") or {})
    parent_meta = {
        "cluster": cluster,
        "task_type": base_meta.get("task_type", ""),
        "task_description": base_meta.get("task_description", ""),
        "task_completion_status": base_meta.get("task_completion_status", ""),
        "system_prompt": base_meta.get("system_prompt", ""),
        "platform": base_meta.get("platform", platform),
        "agents": {"root": root_session, "spawned": spawned_keys},
    }
    parent_doc = {"meta_info": parent_meta, "messages": parent_messages}

    # Write files.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "parent.json").write_text(
        json.dumps(parent_doc, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    if children:
        children_dir = output_dir / "children"
        children_dir.mkdir(exist_ok=True)
        for i, child in enumerate(children, 1):
            fname = f"{i:02d}_{_slug(child['meta_info']['task_name'])}.json"
            (children_dir / fname).write_text(
                json.dumps(child, indent=2, ensure_ascii=False), encoding="utf-8",
            )
    tree_dir = output_dir / "spawn_tree"
    tree_dir.mkdir(exist_ok=True)
    (tree_dir / "parent_spawn_tree.txt").write_text(
        render_spawn_tree_text(
            root_session=root_session,
            task_type=parent_meta["task_type"],
            completion_status=parent_meta["task_completion_status"],
            platform=parent_meta["platform"],
            cluster=cluster,
            n_messages=len(parent_messages),
            children=children,
        ),
        encoding="utf-8",
    )
    return parent_doc


# ---------------------------------------------------------------------------
# Native multi-agent harvest from the collected OpenClaw session store.
#
# Unlike write_golden_layout (which took hand-fed children_sessions), this reads
# the REAL on-disk artifacts a native run leaves at
# ``<run>/task_output/sessions/``:
#   * sessions.json   — index: {canonical_subagent_key: {sessionId, label,
#                       spawnedBy, ...}}  (verified against RUTH/JAE run_5)
#   * <sessionId>.jsonl — one child session transcript per spawned sub-agent
#   * chat.jsonl      — the parent session
# and emits the Larry_Bates layout: agents block on output.json +
# subagents/NN_<label>.json + spawn_tree/parent_spawn_tree.txt.
# ---------------------------------------------------------------------------


def _read_session_message_entries(path: Path) -> List[dict]:
    """Return only the ``type=="message"`` rows from a session .jsonl file."""
    if not path.is_file():
        return []
    out: List[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict) and e.get("type") == "message":
            out.append(e)
    return out


def extract_spawn_label_tasks(parent_messages: List[Any]) -> dict:
    """Map each native ``sessions_spawn`` call's ``label`` -> its ``task`` text.

    Used to give child trajectories a clean one-line description. Handles this
    build's arg names (``label``/``task``) and original-OpenClaw's
    (``name``/``prompt``).
    """
    out: dict = {}
    for m in parent_messages or []:
        inner = m.get("message") if isinstance(m, dict) else None
        content = inner.get("content") if isinstance(inner, dict) else None
        if not isinstance(content, list):
            continue
        for p in content:
            if not (isinstance(p, dict) and p.get("type") in ("toolCall", "tool_use")
                    and p.get("name") == "sessions_spawn"):
                continue
            args = p.get("arguments") or p.get("input") or {}
            label = args.get("label") or args.get("name")
            task = args.get("task") or args.get("prompt")
            if label and task:
                out[str(label)] = str(task)
    return out


def _extract_spawn_results(messages: List[Any]) -> dict:
    """session_key -> {run_id, tool_call_id} from sessions_spawn tool RESULTS.

    The native ``sessions_spawn`` tool result carries the accepted child's
    ``childSessionKey`` and ``runId`` in its text payload; we key by session_key
    so the parent meta_info roster can link each child to its run_id and the
    spawning tool call.
    """
    out: dict = {}
    for m in messages or []:
        inner = m.get("message") if isinstance(m, dict) else None
        if not isinstance(inner, dict):
            continue
        if inner.get("role") == "toolResult" and inner.get("toolName") == "sessions_spawn":
            txt = "".join(
                c.get("text", "") for c in (inner.get("content") or [])
                if isinstance(c, dict) and c.get("type") == "text"
            )
            csk = re.search(r'"childSessionKey":\s*"([^"]+)"', txt)
            rid = re.search(r'"runId":\s*"([^"]+)"', txt)
            if csk:
                out[csk.group(1)] = {
                    "run_id": rid.group(1) if rid else None,
                    "tool_call_id": inner.get("toolCallId"),
                }
    return out


def attach_native_subagents(
    published: dict,
    sessions_dir: Path,
    output_dir: Path,
    *,
    cluster: str = "",
) -> dict:
    """Harvest native sub-agent sessions into the Larry_Bates layout.

    Reads ``<sessions_dir>/sessions.json`` + each child ``<sessionId>.jsonl`` and:
      * adds ``meta_info.agents = {root, spawned:[canonical keys]}`` (+ optional
        ``cluster``) to ``published`` (the parent ``output.json``),
      * writes ``<output_dir>/subagents/NN_<label>.json`` — one per child, in the
        reference child schema (task_name, task_description,
        task_completion_status, parent_session, session_key, platform,
        message_count, messages),
      * writes ``<output_dir>/spawn_tree/parent_spawn_tree.txt``.

    No-op (returns ``published`` unchanged, no files written) when there is no
    ``sessions.json`` or no subagent entries — so single-agent runs are
    unaffected.
    """
    sessions_dir = Path(sessions_dir)
    idx_path = sessions_dir / "sessions.json"
    if not idx_path.is_file():
        return published
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return published
    # Keep only true subagent sessions (the index may also list the parent).
    subs = {
        k: v for k, v in (idx.items() if isinstance(idx, dict) else [])
        if isinstance(v, dict) and ":subagent:" in str(k)
    }
    if not subs:
        return published

    label_to_task = extract_spawn_label_tasks(published.get("messages") or [])
    platform = (published.get("meta_info") or {}).get("platform") or "Linux"

    # Spawn order: updatedAt ascending (stable for ties).
    ordered = sorted(subs.items(), key=lambda kv: kv[1].get("updatedAt", 0))

    root = ""
    children_docs: List[dict] = []
    spawned_keys: List[str] = []
    for canon_key, meta in ordered:
        root = root or str(meta.get("spawnedBy", "") or "")
        label = str(meta.get("label") or "subagent")
        session_file = sessions_dir / f"{meta.get('sessionId', '')}.jsonl"
        child_msgs = _project_published_messages(
            _read_session_message_entries(session_file)
        )
        task_text = label_to_task.get(label, "")
        desc = task_text.strip().splitlines()[0][:200] if task_text.strip() else ""
        completion_status, ended_reason = classify_child_completion(child_msgs)
        children_docs.append({
            "meta_info": {
                "task_name": label,
                "task_description": desc,
                "task_completion_status": completion_status,
                "ended_reason": ended_reason,
                "parent_session": root,
                "session_key": canon_key,
                "subagent_id": str(meta.get("sessionId", "") or ""),
                "platform": platform,
                "message_count": len(child_msgs),
            },
            "messages": child_msgs,
        })
        spawned_keys.append(canon_key)

    # Augment parent meta_info: cluster (first, golden order) + agents block.
    base_meta = published.get("meta_info") or {}
    new_meta: dict = {}
    if cluster:
        new_meta["cluster"] = cluster
    new_meta.update(base_meta)
    new_meta["agents"] = {"root": root, "spawned": spawned_keys}
    # Per-subagent roster on the parent meta_info so spawned children are
    # discoverable directly from output.json (label -> session_key -> run_id ->
    # trajectory_file), not only via spawn_tree/ + subagents/. run_id and
    # spawn_tool_call_id are recovered from the parent's sessions_spawn tool
    # results (childSessionKey -> runId), keyed by session_key.
    _spawn_res = _extract_spawn_results(published.get("messages") or [])
    new_meta["subagent_count"] = len(children_docs)
    new_meta["subagent_session_keys"] = list(spawned_keys)
    new_meta["subagents"] = [
        {
            "label": c["meta_info"].get("task_name"),
            "session_key": c["meta_info"].get("session_key"),
            "subagent_id": c["meta_info"].get("subagent_id"),
            "run_id": _spawn_res.get(c["meta_info"].get("session_key"), {}).get("run_id"),
            "spawn_tool_call_id": _spawn_res.get(c["meta_info"].get("session_key"), {}).get("tool_call_id"),
            "trajectory_file": f"{i:02d}_{_slug(c['meta_info']['task_name'])}.json",
            "task_completion_status": c["meta_info"].get("task_completion_status"),
            "message_count": c["meta_info"].get("message_count"),
        }
        for i, c in enumerate(children_docs, 1)
    ]
    published["meta_info"] = new_meta

    out = Path(output_dir)
    subdir = out / "subagents"
    subdir.mkdir(parents=True, exist_ok=True)
    for i, child in enumerate(children_docs, 1):
        fname = f"{i:02d}_{_slug(child['meta_info']['task_name'])}.json"
        (subdir / fname).write_text(
            json.dumps(child, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    tree_dir = out / "spawn_tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    (tree_dir / "parent_spawn_tree.txt").write_text(
        render_spawn_tree_text(
            root_session=root,
            task_type=new_meta.get("task_type", ""),
            completion_status=new_meta.get("task_completion_status", ""),
            platform=platform,
            cluster=cluster,
            n_messages=len(published.get("messages") or []),
            children=children_docs,
        ),
        encoding="utf-8",
    )
    return published


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
