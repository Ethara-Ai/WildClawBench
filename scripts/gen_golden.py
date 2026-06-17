#!/usr/bin/env python3
"""Golden-trajectory generation pipeline for the RFP3 generator.

Three independently runnable stages:

  1. assemble  -- a completed run dir + the task input dir -> a `golden_input/`
     folder holding the generator's 5-input contract (events.jsonl,
     session-branch.json, prompts.json, artifacts.json, metadata.json, plus
     rubric.json, test_outputs.py, golden_steer_flow.md, persona/).
  2. run       -- feed that pack + the generator system prompt to the model
     (litellm.completion) and capture the delimited output.
  3. parse     -- split the `=== PARENT/CHILD TRAJECTORY ===` blocks, validate
     them, and write golden_trajectory.json + golden_subagents/<name>.json.

Our skoll-delivery harness does NOT emit the generator's native capture format,
so stage 1 synthesizes every input from what we do produce (chat.jsonl,
spawn_tree.jsonl, task input). The full openclaw runtime system prompt is not
captured, so metadata.json uses the generator's documented fallback: the 7
persona md files assembled into system_prompt.

CLI:
  python3 scripts/gen_golden.py --run <run_dir> --task <task_dir> \
      --system ~/Downloads/golden_oddo_generator_RFP3.md \
      [--stage all|assemble|run|parse] [--model <id>] [--max-tokens N]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

# Repo root on path so we can reuse harness helpers when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.trajectory.builder import (  # noqa: E402
    _strip_user_turn_prefix,
    _dedupe_reissued_turns,
)
from src.utils.workspace_snapshot import snapshot_persona_from_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_golden")

# Persona files the generator expects (Input 5 / injectedWorkspaceFiles).
_PERSONA_FILES = (
    "AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md",
    "USER.md", "HEARTBEAT.md", "MEMORY.md",
)

_MEDIA_TYPE_BY_EXT = {
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".flac": "audio", ".ogg": "audio",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video", ".webm": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".pdf": "pdf",
    ".xlsx": "spreadsheet", ".xls": "spreadsheet", ".csv": "spreadsheet",
    ".docx": "document", ".doc": "document", ".md": "document", ".txt": "document",
}


# ----------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _first_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")
    return ""


def _set_first_text(content: Any, new_text: str) -> Any:
    if isinstance(content, str):
        return new_text
    if isinstance(content, list):
        out = list(content)
        for i, b in enumerate(out):
            if isinstance(b, dict) and b.get("type") == "text":
                nb = dict(b)
                nb["text"] = new_text
                out[i] = nb
                return out
    return content


def _read_task_yaml_fields(task_dir: Path) -> tuple[str, str, str]:
    """Return (system_prompt, task_type, task_description) from task.yaml.

    `task_type` (a list in task.yaml) is comma-joined. Any missing field or a
    missing/unparseable task.yaml yields empty strings (caller falls back).
    """
    p = Path(task_dir) / "task.yaml"
    if not p.is_file():
        return "", "", ""
    try:
        import yaml  # project dep; lazy so the rest of assemble stays stdlib-only
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("  ! could not read task.yaml: %s", exc)
        return "", "", ""
    sp = str(raw.get("system_prompt") or "").rstrip()
    tt = raw.get("task_type")
    tt = ", ".join(str(x) for x in tt) if isinstance(tt, (list, tuple)) else str(tt or "")
    td = str(raw.get("task_description") or "").strip()
    return sp, tt, td


def _find_spawn_tree(run_dir: Path) -> Path | None:
    for cand in (
        run_dir / "task_output" / "workspace_full" / "spawn_tree.jsonl",
        run_dir / "task_output" / "artifacts" / "spawn_tree.jsonl",
        run_dir / "spawn_tree.jsonl",
    ):
        if cand.is_file():
            return cand
    return None


# ----------------------------------------------------------------------------
# STAGE 1 — assemble the 5-input pack
# ----------------------------------------------------------------------------

def build_golden_input_pack(run_dir: Path, task_dir: Path, out_dir: Path) -> Path:
    run_dir, task_dir = Path(run_dir), Path(task_dir)
    pack = Path(out_dir) / "golden_input"
    pack.mkdir(parents=True, exist_ok=True)

    chat = run_dir / "chat.jsonl"
    if not chat.is_file():
        raise FileNotFoundError(f"chat.jsonl not found in run dir: {chat}")
    # Collapse a re-issued final turn (report §11) so the generator sees the
    # canonical turn count, not the gateway-recovery duplicate.
    events = _dedupe_reissued_turns(_read_jsonl(chat))

    # --- events.jsonl: copy chat.jsonl, but CLEAN user-turn text so the
    #     generator copies it verbatim (it only strips a [<...> tz] bracket,
    #     not our boilerplate / [TURN N] headers). ---
    clean_user_turns: list[str] = []
    with (pack / "events.jsonl").open("w", encoding="utf-8") as fh:
        for ev in events:
            if (
                ev.get("type") == "message"
                and isinstance(ev.get("message"), dict)
                and ev["message"].get("role") == "user"
            ):
                msg = dict(ev["message"])
                raw = _first_text(msg.get("content"))
                clean = _strip_user_turn_prefix(raw)
                msg["content"] = _set_first_text(msg.get("content"), clean)
                ev = {**ev, "message": msg}
                clean_user_turns.append(clean)
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # --- prompts.json: the clean user turns in order ---
    (pack / "prompts.json").write_text(
        json.dumps({"submittedPrompts": clean_user_turns}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # --- session-branch.json: the captured spawn tree ---
    spawn_tree = _find_spawn_tree(run_dir)
    if spawn_tree:
        shutil.copy2(spawn_tree, pack / "session-branch.json")
    else:
        (pack / "session-branch.json").write_text("[]", encoding="utf-8")
        log.warning("  ! no spawn_tree.jsonl found -> empty session-branch.json")

    # --- metadata.json: prefer the authored system_prompt from task.yaml; fall
    #     back to assembling the 7 persona files only when task.yaml has none ---
    persona = snapshot_persona_from_path(task_dir / "persona")
    assembled_sp = "\n\n".join(
        f"===== {name} =====\n{persona[name]}"
        for name in _PERSONA_FILES if name in persona
    )
    yaml_sp, yaml_tt, yaml_td = _read_task_yaml_fields(task_dir)
    if yaml_sp:
        system_prompt = yaml_sp
        sp_source = "task.yaml"
    else:
        system_prompt = assembled_sp
        sp_source = "7 persona files (task.yaml has no system_prompt)"
    model_id = None
    provider = None
    for ev in events:
        if ev.get("type") == "model_change":
            model_id = ev.get("modelId") or model_id
            provider = ev.get("provider") or provider
        if ev.get("type") == "custom" and ev.get("customType") == "model-snapshot":
            data = ev.get("data") or {}
            model_id = data.get("modelId") or model_id
            provider = data.get("provider") or provider
    metadata = {
        "system_prompt": system_prompt,
        "task_type": yaml_tt,
        "task_description": yaml_td,
        "prompting": {
            "systemPromptReport": {
                "injectedWorkspaceFiles": [n for n in _PERSONA_FILES if n in persona],
            }
        },
        "model": {"provider": provider, "modelId": model_id},
        "_note": f"system_prompt source: {sp_source}.",
    }
    (pack / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- artifacts.json: media manifest from the task data/ dir ---
    artifacts: list[dict] = []
    data_dir = task_dir / "data"
    if data_dir.is_dir():
        for f in sorted(data_dir.iterdir()):
            if f.is_file():
                artifacts.append({
                    "type": _MEDIA_TYPE_BY_EXT.get(f.suffix.lower(), "other"),
                    "name": f.name,
                    "path": str(f),
                })
    (pack / "artifacts.json").write_text(
        json.dumps(artifacts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- copy the three task sources of truth verbatim ---
    copied = []
    for name in ("rubric.json", "test_outputs.py", "golden_steer_flow.md"):
        src = task_dir / name
        if src.is_file():
            shutil.copy2(src, pack / name)
            copied.append(name)
        else:
            log.warning("  ! task source missing: %s", name)

    # --- copy persona/ (7 md) ---
    persona_out = pack / "persona"
    persona_out.mkdir(exist_ok=True)
    for name in _PERSONA_FILES:
        if name in persona:
            (persona_out / name).write_text(persona[name], encoding="utf-8")

    total_chars = sum(p.stat().st_size for p in pack.rglob("*") if p.is_file())
    log.info("Assembled golden_input pack at %s", pack)
    log.info("  events.jsonl: %d events (%d user turns cleaned)",
             len(events), len(clean_user_turns))
    log.info("  session-branch.json: %s", "from spawn_tree" if spawn_tree else "EMPTY")
    log.info("  metadata.json: system_prompt from %s (%d chars), model=%s",
             sp_source, len(system_prompt), model_id)
    log.info("  artifacts.json: %d files", len(artifacts))
    log.info("  copied sources: %s ; persona files: %d",
             copied, len([n for n in _PERSONA_FILES if n in persona]))
    log.info("  TOTAL pack size: %d chars (mind the model context window)", total_chars)
    return pack


# ----------------------------------------------------------------------------
# STAGE 2 — run the generator
# ----------------------------------------------------------------------------

def _assemble_user_message(pack: Path) -> str:
    def _r(name: str) -> str:
        p = pack / name
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    persona_blocks = []
    for name in _PERSONA_FILES:
        p = pack / "persona" / name
        if p.is_file():
            persona_blocks.append(f"--- {name} ---\n{p.read_text(encoding='utf-8')}")

    parts = [
        "INPUT 1 - Claude trajectory (the draft to refine):",
        "metadata.json:\n" + _r("metadata.json"),
        "prompts.json:\n" + _r("prompts.json"),
        "events.jsonl:\n" + _r("events.jsonl"),
        "session-branch.json:\n" + _r("session-branch.json"),
        "artifacts.json:\n" + _r("artifacts.json"),
        "\nINPUT 2 - rubric.json:\n" + _r("rubric.json"),
        "\nINPUT 3 - test_outputs.py:\n" + _r("test_outputs.py"),
        "\nINPUT 4 - golden_steer_flow.md:\n" + _r("golden_steer_flow.md"),
        "\nINPUT 5 - persona workspace (7 md files):\n" + "\n\n".join(persona_blocks),
    ]
    return "\n\n".join(parts)


def run_generator(
    pack: Path,
    system_prompt_path: Path,
    *,
    model: str,
    max_tokens: int,
    max_continuations: int = 2,
) -> tuple[str, dict]:
    """Call the generator model. Returns (raw_text, usage). Lazy-imports litellm
    so stages 1+3 work without it."""
    import litellm  # noqa: F401  (host-side only)

    system = Path(system_prompt_path).read_text(encoding="utf-8")
    user = _assemble_user_message(Path(pack))
    log.info("Generator call: model=%s, system=%d chars, user=%d chars, max_tokens=%d",
             model, len(system), len(user), max_tokens)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    full_text = ""
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    for attempt in range(max_continuations + 1):
        # Stream with a long timeout. A large non-streaming Bedrock call drops the
        # connection (~"Connection timed out after None seconds") before it can emit
        # a multi-thousand-token trajectory, while a small max_tokens truncates and
        # forces the broken continuation path (re-emitted "=== PARENT TRAJECTORY ==="
        # headers). Streaming keeps the socket alive for the whole generation, so one
        # high-max_tokens call emits the complete trajectory in a single shot.
        resp = litellm.completion(
            model=model, messages=messages, max_tokens=max_tokens, stream=True,
            timeout=3600, stream_options={"include_usage": True},
        )
        chunk = ""
        finish = None
        last_usage = None
        for ev in resp:
            choices = getattr(ev, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                chunk += (getattr(delta, "content", None) or "") if delta else ""
                fr = getattr(choices[0], "finish_reason", None)
                if fr:
                    finish = fr
            u = getattr(ev, "usage", None)
            if u:
                last_usage = u
        full_text += chunk
        u = last_usage or {}
        usage_total["input_tokens"] += int(getattr(u, "prompt_tokens", 0) or 0)
        usage_total["output_tokens"] += int(getattr(u, "completion_tokens", 0) or 0)
        if finish != "length":
            break
        log.warning("  output truncated (finish=length); continuing (%d/%d)",
                    attempt + 1, max_continuations)
        # Ask the model to continue exactly where it stopped.
        messages = messages + [
            {"role": "assistant", "content": chunk},
            {"role": "user", "content": "Continue exactly from where you stopped. "
                                        "Do not repeat any already-emitted text."},
        ]
    return full_text, usage_total


# ----------------------------------------------------------------------------
# STAGE 3 — parse + validate the generator output
# ----------------------------------------------------------------------------

import re  # noqa: E402

_PARENT_DELIM = re.compile(r"^===\s*PARENT TRAJECTORY\s*===\s*$", re.M)
_CHILD_DELIM = re.compile(
    r"^===\s*CHILD TRAJECTORY:\s*(?P<name>.*?)\s*"
    r"(?:\(session:\s*(?P<session>[^)]*)\))?\s*===\s*$",
    re.M,
)
_DASH = re.compile("[—–]")


def _validate_trajectory(traj: dict, *, is_child: bool, label: str) -> list[str]:
    errs: list[str] = []
    msgs = traj.get("messages")
    if not isinstance(msgs, list) or not msgs:
        errs.append(f"{label}: no messages")
        return errs
    prefix = "c" if is_child else "d"
    expect = 1
    open_tool_calls: list[str] = []
    for m in msgs:
        mid = m.get("id", "")
        if not str(mid).startswith(prefix):
            errs.append(f"{label}: id {mid!r} missing '{prefix}' prefix")
        else:
            try:
                if int(mid[1:]) != expect:
                    errs.append(f"{label}: id {mid} out of sequence (want {prefix}{expect:07d})")
            except ValueError:
                errs.append(f"{label}: id {mid!r} not numeric")
        expect += 1
        body = m.get("message", {})
        role = body.get("role")
        if role not in ("user", "assistant", "toolResult"):
            errs.append(f"{label}: bad role {role!r} at {mid}")
        if role == "assistant":
            for b in body.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "toolCall":
                    open_tool_calls.append(b.get("id", ""))
        if role == "toolResult":
            tcid = body.get("toolCallId")
            if tcid in open_tool_calls:
                open_tool_calls.remove(tcid)
            else:
                errs.append(f"{label}: toolResult {mid} has no matching toolCall")
    if open_tool_calls:
        errs.append(f"{label}: {len(open_tool_calls)} toolCall(s) without a toolResult")
    last = msgs[-1].get("message", {})
    if last.get("role") != "assistant" or not any(
        isinstance(b, dict) and b.get("type") == "text" for b in last.get("content") or []
    ):
        errs.append(f"{label}: must end on an assistant text block")
    return errs


def parse_golden_output(text: str, run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    report: dict[str, Any] = {"parent": None, "children": [], "errors": []}

    # Hard reject em/en dashes (generator rule R14).
    if _DASH.search(text):
        n = len(_DASH.findall(text))
        report["errors"].append(f"FOUND {n} em/en dash(es) (R14 auto-reject)")

    pm = _PARENT_DELIM.search(text)
    if not pm:
        report["errors"].append("no '=== PARENT TRAJECTORY ===' delimiter found")
        return report

    # child delimiters partition the remainder
    child_marks = list(_CHILD_DELIM.finditer(text, pm.end()))
    parent_end = child_marks[0].start() if child_marks else len(text)
    parent_json = text[pm.end():parent_end].strip()

    try:
        parent = json.loads(parent_json)
        report["errors"] += _validate_trajectory(parent, is_child=False, label="parent")
        (run_dir / "golden_trajectory.json").write_text(
            json.dumps(parent, indent=2, ensure_ascii=False), encoding="utf-8")
        report["parent"] = "golden_trajectory.json"
    except json.JSONDecodeError as exc:
        report["errors"].append(f"parent JSON invalid: {exc}")

    if child_marks:
        children_dir = run_dir / "golden_subagents"
        children_dir.mkdir(exist_ok=True)
        for i, mark in enumerate(child_marks):
            name = (mark.group("name") or f"child_{i+1}").strip()
            seg_end = child_marks[i + 1].start() if i + 1 < len(child_marks) else len(text)
            blob = text[mark.end():seg_end].strip()
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or f"child_{i+1}"
            try:
                child = json.loads(blob)
                report["errors"] += _validate_trajectory(
                    child, is_child=True, label=f"child[{name}]")
                (children_dir / f"{safe}.json").write_text(
                    json.dumps(child, indent=2, ensure_ascii=False), encoding="utf-8")
                report["children"].append(f"golden_subagents/{safe}.json")
            except json.JSONDecodeError as exc:
                report["errors"].append(f"child[{name}] JSON invalid: {exc}")

    log.info("Parsed: parent=%s, %d child(ren), %d error(s)",
             bool(report["parent"]), len(report["children"]), len(report["errors"]))
    for e in report["errors"]:
        log.warning("  VALIDATION: %s", e)
    return report


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Golden-trajectory generation pipeline.")
    ap.add_argument("--run", required=True, type=Path, help="completed run dir")
    ap.add_argument("--task", required=True, type=Path, help="task input dir")
    ap.add_argument("--system", type=Path,
                    help="generator system-prompt md (required for stage run)")
    ap.add_argument("--stage", default="all",
                    choices=["all", "assemble", "run", "parse"])
    ap.add_argument("--out", type=Path, default=None,
                    help="pack output dir (default: <run>/golden_build)")
    ap.add_argument("--model", default="anthropic/claude-opus-4-6")
    ap.add_argument("--max-tokens", type=int, default=32000)
    args = ap.parse_args(argv)

    out = args.out or (args.run / "golden_build")
    pack = out / "golden_input"
    raw_path = out / "generator_output.txt"

    if args.stage in ("all", "assemble"):
        build_golden_input_pack(args.run, args.task, out)

    if args.stage in ("all", "run"):
        if not args.system:
            ap.error("--system is required for the 'run' stage")
        text, usage = run_generator(
            pack, args.system, model=args.model, max_tokens=args.max_tokens)
        out.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text, encoding="utf-8")
        log.info("Generator output -> %s (usage=%s)", raw_path, usage)

    if args.stage in ("all", "parse"):
        text = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""
        if not text:
            log.error("no generator output to parse at %s", raw_path)
            return 2
        report = parse_golden_output(text, args.run)
        return 0 if not report["errors"] else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
