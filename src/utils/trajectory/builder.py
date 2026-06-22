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
    messages: List[Any] = []
    prev_id = ""  # root message's parentId is "" (no parent); else previous id.
    for m in traj.get("messages") or []:
        if not isinstance(m, dict):
            messages.append(m)
            continue
        # Canonical wrapper shape (Golden_Trajectory.json): type, id, parentId,
        # timestamp, message — with parentId threaded to the previous message's
        # id. Internal bookkeeping (turn_index) and the upstream-dropped parentId
        # are reconstructed here so threading is well-formed regardless of source.
        mid = m.get("id", "")
        inner = m.get("message")
        pm: dict[str, Any] = {
            "type": m.get("type", "message"),
            "id": mid,
            "parentId": prev_id,
            "timestamp": m.get("timestamp", ""),
            "message": _scrub_published_message(inner) if isinstance(inner, dict) else inner,
        }
        prev_id = mid
        messages.append(pm)

    inner_meta = (traj.get("trajectory") or {}).get("meta_info") or {}
    platform = inner_meta.get("platform") or "linux"
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
