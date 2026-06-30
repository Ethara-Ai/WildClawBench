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


_NATIVE_SUBAGENT_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _native_slugify(value: str) -> str:
    cleaned = _NATIVE_SUBAGENT_SLUG_RE.sub("_", str(value or "").strip()).strip("_")
    return cleaned.lower() or "subagent"


def _parse_native_child_session(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    session_id = path.stem
    label = ""
    spawn_ts = ""
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    messages: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        ts = ev.get("timestamp")
        if ts and not spawn_ts:
            spawn_ts = str(ts)
        if not label:
            for k in ("label", "task_label", "agent_label", "session_label"):
                v = ev.get(k)
                if isinstance(v, str) and v:
                    label = v
                    break
            meta = ev.get("metadata")
            if not label and isinstance(meta, dict):
                for k in ("label", "task_label", "agent_label", "session_label"):
                    v = meta.get(k)
                    if isinstance(v, str) and v:
                        label = v
                        break
        usage = (ev.get("message") or {}).get("usage") or ev.get("usage")
        if isinstance(usage, dict):
            for k_in in ("input", "input_tokens", "prompt_tokens", "tokens_in"):
                val = usage.get(k_in)
                if isinstance(val, int):
                    tokens_in = max(tokens_in, val)
                    break
            for k_out in ("output", "output_tokens", "completion_tokens", "tokens_out"):
                val = usage.get(k_out)
                if isinstance(val, int):
                    tokens_out = max(tokens_out, val)
                    break
            cost_raw = usage.get("cost_usd")
            if cost_raw is None:
                cost_alt = usage.get("cost")
                if isinstance(cost_alt, (int, float)):
                    cost_raw = cost_alt
            if isinstance(cost_raw, (int, float)):
                cost_usd = max(cost_usd, float(cost_raw))
        if ev.get("type") == "message":
            messages.append(ev)
    if not messages and tokens_in == 0 and tokens_out == 0:
        return None
    return {
        "session_id": session_id,
        "label": label or session_id,
        "spawn_ts": spawn_ts,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "messages": messages,
    }


def _shape_native_subagent_delivery(child: dict) -> dict:
    tokens_in = int(child.get("tokens_in") or 0)
    tokens_out = int(child.get("tokens_out") or 0)
    return {
        "session_id": child.get("session_id"),
        "label": child.get("label"),
        "spawn_ts": child.get("spawn_ts"),
        "meta_info": {
            "task_type": "subagent",
            "task_label": child.get("label"),
            "status": "ok",
            "platform": "linux",
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
                "cost_usd": round(float(child.get("cost_usd") or 0.0), 6),
            },
        },
        "messages": child.get("messages") or [],
    }


def _render_native_spawn_tree_text(chat_name: str, children: list[dict]) -> str:
    lines = [f"root: chat ({chat_name})"]
    for idx, c in enumerate(children, start=1):
        lines.append(
            f"  \u2514\u2500 {idx:02d} {c.get('label')} [{c.get('session_id')}] "
            f"tokens={c.get('tokens_in', 0)}/{c.get('tokens_out', 0)} "
            f"cost=${c.get('cost_usd', 0.0):.4f}"
        )
    return "\n".join(lines) + "\n"


def _extract_native_spawn_labels(chat_path: Path) -> list[str]:
    if not chat_path.is_file():
        return []
    try:
        text = chat_path.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    labels: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        for c in msg.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") != "toolCall" or c.get("name") != "sessions_spawn":
                continue
            args = c.get("arguments") or c.get("input") or {}
            lab = args.get("label") if isinstance(args, dict) else None
            if isinstance(lab, str) and lab and lab not in seen:
                seen.add(lab)
                labels.append(lab)
    return labels


def _distribute_and_flatten_message_costs(child: dict) -> None:
    cost_total = float(child.get("cost_usd") or 0)
    msgs = child.get("messages") or []
    rounds: list[dict] = []
    for m in msgs:
        u = (m.get("message") or {}).get("usage") if isinstance(m, dict) else None
        if isinstance(u, dict) and isinstance(u.get("output"), int):
            rounds.append(u)
    if not rounds:
        return
    final_out = rounds[-1].get("output", 0)
    prev = 0
    assigned = 0.0
    for i, u in enumerate(rounds):
        curr = u.get("output", 0)
        delta = max(curr - prev, 0)
        share = (delta / final_out) if final_out else 0
        per = round(cost_total * share, 6) if i < len(rounds) - 1 else round(cost_total - assigned, 6)
        assigned += per
        u["cost"] = per
        prev = curr


def attach_native_subagents(
    published: dict,
    sessions_dir: Path,
    output_dir: Path,
    total_agent_cost: float = 0.0,
) -> None:
    if not isinstance(published, dict) or not sessions_dir.is_dir():
        return
    chat_path = sessions_dir / "chat.jsonl"
    child_paths = sorted(
        p for p in sessions_dir.glob("*.jsonl") if p.name != "chat.jsonl"
    )
    if not child_paths:
        return
    children: list[dict] = []
    for cp in child_paths:
        rec = _parse_native_child_session(cp)
        if rec is not None:
            children.append(rec)
    if not children:
        return
    children.sort(key=lambda c: c.get("spawn_ts") or "")

    spawn_labels = _extract_native_spawn_labels(chat_path)
    for idx, child in enumerate(children):
        if idx < len(spawn_labels):
            child["label"] = spawn_labels[idx]

    sum_tokens_out = sum(int(c.get("tokens_out") or 0) for c in children)
    if total_agent_cost > 0 and sum_tokens_out > 0:
        for c in children:
            c["cost_usd"] = round(
                total_agent_cost * int(c.get("tokens_out") or 0) / sum_tokens_out, 6
            )
            _distribute_and_flatten_message_costs(c)

    subagents_out = output_dir / "subagents"
    subagents_out.mkdir(parents=True, exist_ok=True)
    delivery_paths: list[str] = []
    for idx, child in enumerate(children, start=1):
        slug = _native_slugify(child.get("label") or f"subagent_{idx}")
        rel = f"subagents/{idx:02d}_{slug}.json"
        delivery_paths.append(rel)
        (output_dir / rel).write_text(
            json.dumps(_shape_native_subagent_delivery(child), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    tree_dir = output_dir / "spawn_tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    (tree_dir / "parent_spawn_tree.txt").write_text(
        _render_native_spawn_tree_text(chat_path.name, children),
        encoding="utf-8",
    )

    meta_info = published.get("meta_info")
    if not isinstance(meta_info, dict):
        meta_info = {}
        published["meta_info"] = meta_info
    meta_info["agents"] = {
        "root": {
            "session_id": "chat",
            "transcript": "task_output/sessions/chat.jsonl",
        },
        "spawned": [
            {
                "ordinal": idx,
                "label": c.get("label") or f"subagent_{idx}",
                "session_id": c.get("session_id"),
                "spawn_ts": c.get("spawn_ts"),
                "tokens_in": int(c.get("tokens_in") or 0),
                "tokens_out": int(c.get("tokens_out") or 0),
                "cost_usd": round(float(c.get("cost_usd") or 0.0), 6),
                "transcript": f"task_output/sessions/{c.get('session_id')}.jsonl",
                "delivery": delivery_paths[idx - 1],
            }
            for idx, c in enumerate(children, start=1)
        ],
    }

    spawn_tree_path = output_dir / "task_output" / "workspace_full" / "spawn_tree.jsonl"
    spawn_tree_path.parent.mkdir(parents=True, exist_ok=True)
    with spawn_tree_path.open("a", encoding="utf-8") as fh:
        for idx, c in enumerate(children, start=1):
            tokens_in = int(c.get("tokens_in") or 0)
            tokens_out = int(c.get("tokens_out") or 0)
            row = {
                "kind": "spawn",
                "spawn_id": c.get("session_id"),
                "label": c.get("label") or f"subagent_{idx}",
                "role": c.get("label") or f"subagent_{idx}",
                "turn_index": -1,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cost_usd": round(float(c.get("cost_usd") or 0.0), 6),
                "status": "ok",
                "source": "native_sessions_spawn",
            }
            fh.write(json.dumps(row) + "\n")

    logger.info(
        "Attached %d native sub-agent(s) under %s",
        len(children), output_dir,
    )


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
