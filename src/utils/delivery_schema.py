"""Convert openclaw `output.json` into the delivery `delivery.json` schema.

Target shape (vacuum_researcher exemplar):

    {
      "meta_info": {
        "task_type", "task_description", "task_completion_status",
        "system_prompt", "platform"
      },
      "messages": [
        {"type":"message","id","parentId","timestamp",
         "message":{"role":"user|assistant","content":[
            {"type":"text","text"},
            {"type":"thinking","thinking","thinkingSignature"},
            {"type":"toolCall","id","name","arguments"}
         ]}},
        {"type":"message","id","parentId","timestamp",
         "message":{"role":"toolResult","toolCallId","toolName",
                    "isError","content":[{"type":"text","text"}]}}
      ]
    }

Sub-agent calls are surfaced as paired `sessions_spawn` / `sessions_yield`
tool entries: the openclaw runner spawns sub-agents via an `exec` toolCall
whose `command` field heredocs a JSON spec into `/tmp/spec.json` and pipes it
into `scripts/spawn_subagent.py`. We detect that pattern, parse the embedded
spec, and join it with the corresponding row in
`task_output/workspace_full/spawn_tree.jsonl` (file-order, since both are
append-only and produced in the same parent's call sequence).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


_SPAWN_SCRIPT_HINT = "spawn_subagent.py"
_SPEC_HEREDOC_RE = re.compile(
    r"cat\s*<<\s*['\"]?EOF['\"]?\s*>\s*/tmp/spec\.json\s*\n(.*?)\nEOF",
    re.DOTALL,
)


def _load_chat_parents(chat_jsonl_path: Path) -> dict[str, str | None]:
    parents: dict[str, str | None] = {}
    if not chat_jsonl_path.is_file():
        return parents
    try:
        with chat_jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "message":
                    continue
                mid = row.get("id")
                if mid:
                    parents[mid] = row.get("parentId")
    except OSError:
        pass
    return parents


def _load_spawn_tree(spawn_tree_path: Path) -> list[dict]:
    rows: list[dict] = []
    if not spawn_tree_path.is_file():
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


def _is_spawn_command(command: str) -> bool:
    return _SPAWN_SCRIPT_HINT in command


def _parse_spawn_spec(command: str) -> dict | None:
    m = _SPEC_HEREDOC_RE.search(command)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _convert_content_blocks(
    blocks: list[dict],
    spawn_rows: list[dict],
    cursor: list[int],
    spawn_id_map: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    for b in blocks or []:
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif t == "thinking":
            out.append({
                "type": "thinking",
                "thinking": b.get("thinking", ""),
                "thinkingSignature": b.get("thinkingSignature", ""),
            })
        elif t == "toolCall":
            name = b.get("name", "")
            args = b.get("arguments") or {}
            tool_id = b.get("id", "")
            command = (args.get("command") or "") if name == "exec" else ""
            if name == "exec" and _is_spawn_command(command):
                spec = _parse_spawn_spec(command) or {}
                # A single `exec` may pipe multiple spawn_subagent.py
                # invocations (e.g. parallel `... &` chains), each emitting
                # its own spawn_tree row. Count occurrences of the hint and
                # consume that many rows; register every consumed row in
                # spawn_id_map keyed by a synthetic id so `sessions_yield`
                # rewrite finds the first one and `_load_sub_agent_trajectories`
                # surfaces all of them.
                count = max(1, command.count(_SPAWN_SCRIPT_HINT))
                first_consumed = None
                for n in range(count):
                    if cursor[0] >= len(spawn_rows):
                        break
                    sr = spawn_rows[cursor[0]]
                    cursor[0] += 1
                    if n == 0:
                        spawn_id_map[tool_id] = sr
                        first_consumed = sr
                    else:
                        sid = sr.get("spawn_id") or f"{tool_id}:n{n}"
                        spawn_id_map[f"{tool_id}:n{n}"] = sr
                out.append({
                    "type": "toolCall",
                    "id": tool_id,
                    "name": "sessions_spawn",
                    "arguments": spec,
                })
            else:
                out.append({
                    "type": "toolCall",
                    "id": tool_id,
                    "name": name,
                    "arguments": args,
                })
        else:
            out.append(dict(b))
    return out


def _convert_tool_result(
    msg_body: dict,
    spawn_id_map: dict[str, dict],
) -> dict:
    tc_id = msg_body.get("toolCallId", "")
    tool_name = msg_body.get("toolName", "")
    is_error = bool(msg_body.get("isError", False))
    content = msg_body.get("content") or []
    text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]

    if tc_id in spawn_id_map:
        sr = spawn_id_map[tc_id]
        spawn_id = sr.get("spawn_id", "")
        status = sr.get("status", "ok")
        # Normalize: subagent_director writes "ok"; delivery exemplar uses
        # "completed". Other statuses (timeout/error/blocked) pass through.
        delivered_status = "completed" if status == "ok" else status
        text = "\n".join(t for t in text_blocks if t) or sr.get("output_preview", "")
        return {
            "role": "toolResult",
            "toolCallId": tc_id,
            "toolName": "sessions_yield",
            "isError": bool(is_error or status != "ok"),
            "content": [{
                "type": "text",
                "text": json.dumps(
                    {
                        "session_id": spawn_id,
                        "status": delivered_status,
                        "output": text,
                    },
                    ensure_ascii=False,
                ),
            }],
        }

    return {
        "role": "toolResult",
        "toolCallId": tc_id,
        "toolName": tool_name,
        "isError": is_error,
        "content": [
            {"type": "text", "text": c.get("text", "")}
            for c in content
            if c.get("type") == "text"
        ],
    }


def _convert_message(
    msg: dict,
    parents: dict[str, str | None],
    spawn_rows: list[dict],
    cursor: list[int],
    spawn_id_map: dict[str, dict],
) -> dict:
    mid = msg.get("id", "")
    body = msg.get("message") or {}
    role = body.get("role", "")
    base: dict = {
        "type": "message",
        "id": mid,
        "parentId": parents.get(mid),
        "timestamp": msg.get("timestamp", ""),
    }
    if role == "toolResult":
        base["message"] = _convert_tool_result(body, spawn_id_map)
    else:
        base["message"] = {
            "role": role,
            "content": _convert_content_blocks(
                body.get("content") or [], spawn_rows, cursor, spawn_id_map,
            ),
        }
    return base


def _derive_task_description(messages: list[dict]) -> str:
    for m in messages:
        body = m.get("message") or {}
        if body.get("role") != "user":
            continue
        for c in body.get("content") or []:
            if c.get("type") == "text":
                return (c.get("text") or "").strip()[:4000]
    return ""


def _derive_completion_status(score: Mapping[str, Any] | None) -> str:
    if score is None:
        return "completed"
    reward = score.get("combined_reward")
    if isinstance(reward, (int, float)):
        return "completed" if reward >= 0.5 else "partial"
    if score.get("error"):
        return "partial"
    return "completed"


def _load_sub_agent_trajectories(
    subagents_dir: Path | None,
    spawn_id_map: dict[str, dict],
    spawn_rows: list[dict] | None = None,
) -> dict[str, dict]:
    """Embed EVERY captured sub-agent, not just the ones whose parent ``exec``
    call was detected as a spawn.

    The agent often fans out via wrapper scripts (``run_all*.sh``) that call
    ``spawn_subagent.py`` internally, so those spawns never surface as detectable
    parent ``exec`` toolCalls (report §10) — yet they DO append a ``spawn_tree``
    row and write a ``<spawn_id>.delivery.json``. We therefore embed by what was
    actually recorded on disk: every spawn_tree spawn_id (in turn order) plus any
    remaining ``*.delivery.json`` files, rather than only ``spawn_id_map``.
    """
    if subagents_dir is None or not subagents_dir.is_dir():
        return {}

    ordered_ids: list[str] = []
    seen: set[str] = set()
    # 1. spawn_tree order (preserves per-turn spawn sequence).
    for r in spawn_rows or []:
        if not isinstance(r, dict):
            continue
        sid = r.get("spawn_id")
        if sid and sid not in seen:
            seen.add(sid)
            ordered_ids.append(sid)
    # 2. Any per-spawn delivery file on disk not already listed.
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


def build_delivery_trajectory(
    traj: Mapping[str, Any],
    *,
    chat_jsonl_path: Path | None = None,
    spawn_tree_path: Path | None = None,
    subagents_dir: Path | None = None,
    score: Mapping[str, Any] | None = None,
    task_type: str | None = None,
    task_description: str | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Reshape an openclaw `output.json` dict into the delivery schema.

    `chat_jsonl_path` supplies the `parentId` for each message via id-join.
    `spawn_tree_path` supplies sub-agent ids/status/output for rewriting the
    parent's spawn invocations as `sessions_spawn`/`sessions_yield` pairs.
    `subagents_dir` is the directory holding per-spawn delivery files written
    by `write_subagent_delivery`; if present each `{spawn_id}.delivery.json`
    is embedded under the top-level `sub_agent_trajectories` key keyed by
    `spawn_id`, mirroring the parent shape recursively. Any input may be
    omitted (parentId becomes None, spawn invocations stay as raw `exec`
    toolCalls, `sub_agent_trajectories` becomes an empty dict).
    """
    parents = _load_chat_parents(chat_jsonl_path) if chat_jsonl_path else {}
    spawn_rows = _load_spawn_tree(spawn_tree_path) if spawn_tree_path else []
    cursor = [0]
    spawn_id_map: dict[str, dict] = {}

    src_msgs = list(traj.get("messages") or [])
    out_msgs = [
        _convert_message(m, parents, spawn_rows, cursor, spawn_id_map)
        for m in src_msgs
    ]

    traj_meta = (traj.get("trajectory") or {}).get("meta_info") or {}
    meta_info = {
        # task_type / task_description / system_prompt come from task.yaml (the
        # authoritative authored values); fall back to taxonomy / first-user-turn
        # only when the caller didn't supply them.
        "task_type": task_type or traj_meta.get("taxonomy_l2") or "",
        "task_description": (task_description or "").strip()
        or _derive_task_description(src_msgs),
        "task_completion_status": _derive_completion_status(score),
        "system_prompt": system_prompt or "",
        "platform": traj_meta.get("platform") or "linux",
    }
    sub_trajs = _load_sub_agent_trajectories(subagents_dir, spawn_id_map, spawn_rows)
    # Carry the agent-produced deliverables (and task input files) through to the
    # delivery schema. `output.json` already builds these via
    # multimodal_meta.build_output_artifacts / build_input_files_manifest, so we
    # just surface them rather than recomputing. Missing -> empty lists.
    artifacts = list(traj.get("output_artifacts") or [])
    input_files = list(traj.get("input_files") or [])
    return {
        "meta_info": meta_info,
        "messages": out_msgs,
        "artifacts": artifacts,
        "input_files": input_files,
        "sub_agent_trajectories": sub_trajs,
    }


def write_delivery_json(
    run_dir: Path,
    traj: Mapping[str, Any],
    *,
    score: Mapping[str, Any] | None = None,
    task_type: str | None = None,
    task_description: str | None = None,
    system_prompt: str | None = None,
) -> Path:
    """Write `delivery.json` next to `output.json` inside a run directory.

    Reads sidecar files (`chat.jsonl`, `task_output/workspace_full/
    spawn_tree.jsonl`, and per-spawn `.delivery.json` files under
    `task_output/workspace_full/subagents/`) when present. Returns the
    destination path.
    """
    chat_jsonl = run_dir / "chat.jsonl"
    spawn_tree = run_dir / "task_output" / "workspace_full" / "spawn_tree.jsonl"
    subagents_dir = run_dir / "task_output" / "workspace_full" / "subagents"
    delivery = build_delivery_trajectory(
        traj,
        chat_jsonl_path=chat_jsonl if chat_jsonl.is_file() else None,
        spawn_tree_path=spawn_tree if spawn_tree.is_file() else None,
        subagents_dir=subagents_dir if subagents_dir.is_dir() else None,
        score=score,
        task_type=task_type,
        task_description=task_description,
        system_prompt=system_prompt,
    )
    dest = run_dir / "delivery.json"
    dest.write_text(
        json.dumps(delivery, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return dest
