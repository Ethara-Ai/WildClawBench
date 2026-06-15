#!/usr/bin/env python3
"""
Golden trajectory generator for the WildClawBench / OpenClaw task framework.

Supports BOTH task layouts:

  * NEW input format (no task.py): metadata, turns and timing are derived from
    ``prompts.txt`` (header + per-turn wake-ups), the system prompt from
    ``task.yaml``, the canonical per-turn solve path from ``golden_steer_flow.md``,
    silent/loud mutations from ``inject/stageN/mutations.json`` and checker ids
    from ``test_outputs.py`` / ``test_weights.json``.
  * LEGACY format: ``task/task.py`` exposing TASK_METADATA / TURNS / ROLE_PROMPT
    / CHECKERS (used for runtime validation when present).

Task-specific response content lives in ``golden_data.json``. When that file is
absent the generator SYNTHESISES a minimal one from ``golden_steer_flow.md`` so a
golden trajectory can be produced with zero hand-authoring (then refined later).

Usage:
    python generate_golden_trajectory.py [TASK_DIR]                  # generate + validate
    python generate_golden_trajectory.py [TASK_DIR] --write-only     # generate only
    python generate_golden_trajectory.py [TASK_DIR] --scaffold       # write golden_data.json template
    python generate_golden_trajectory.py [TASK_DIR] --validate-only  # validate existing agent_state.json

    TASK_DIR defaults to the directory containing this script.
"""

import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_MAP = {
    "America/Chicago": (-5, "CDT"),
    "America/New_York": (-4, "EDT"),
    "America/Denver": (-6, "MDT"),
    "America/Los_Angeles": (-7, "PDT"),
    "America/Phoenix": (-7, "MST"),
    "US/Central": (-5, "CDT"),
    "US/Eastern": (-4, "EDT"),
    "US/Mountain": (-6, "MDT"),
    "US/Pacific": (-7, "PDT"),
    "Europe/London": (1, "BST"),
    "Europe/Berlin": (2, "CEST"),
    "Europe/Paris": (2, "CEST"),
    "Europe/Warsaw": (2, "CEST"),
    "Europe/Madrid": (2, "CEST"),
    "Europe/Rome": (2, "CEST"),
    "Europe/Amsterdam": (2, "CEST"),
    "Europe/Prague": (2, "CEST"),
    "Europe/Stockholm": (2, "CEST"),
    "Europe/Helsinki": (3, "EEST"),
    "Europe/Athens": (3, "EEST"),
    "Europe/Bucharest": (3, "EEST"),
    "Europe/Istanbul": (3, "TRT"),
    "Europe/Moscow": (3, "MSK"),
    "Asia/Dubai": (4, "GST"),
    "Asia/Kolkata": (5, "IST"),
    "Asia/Shanghai": (8, "CST"),
    "Asia/Tokyo": (9, "JST"),
    "Asia/Seoul": (9, "KST"),
    "Australia/Sydney": (11, "AEDT"),
    "Pacific/Auckland": (13, "NZDT"),
    "UTC": (0, "UTC"),
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# --------------------------------------------------------------------------- #
# LEGACY task.py loader (kept for back-compat)
# --------------------------------------------------------------------------- #
def _load_legacy_task_module(task_dir):
    for candidate in [task_dir / "task" / "task.py", task_dir / "task.py"]:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("_task_mod", str(candidate))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


# --------------------------------------------------------------------------- #
# NEW input-format parsers
# --------------------------------------------------------------------------- #
def _parse_prompts(task_dir):
    """Return {turn_index: wake_up_text} from prompts.txt."""
    prompts_file = task_dir / "prompts.txt"
    if not prompts_file.exists():
        return {}
    txt = prompts_file.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(
        r"--- TURN T(\d+) \(Day \d+, \d+:\d+\) ---\n(.+?)(?=--- TURN|# --- END|\Z)",
        txt,
        re.DOTALL,
    ):
        out[int(m.group(1))] = m.group(2).strip()
    return out


def _parse_prompts_header(task_dir):
    """Derive metadata (start date, timezone, days, turns, persona, variant)
    from the comment header + turn headers of prompts.txt."""
    pf = task_dir / "prompts.txt"
    txt = pf.read_text(encoding="utf-8") if pf.exists() else ""
    meta = {"id": task_dir.name}

    m = re.search(r"#\s*Persona\s*:\s*(.+)", txt)
    if m:
        meta["persona_name"] = m.group(1).strip()
    m = re.search(r"#\s*Variant\s*:\s*(.+)", txt)
    if m:
        meta["variant"] = m.group(1).strip().split()[0]

    # "Window : Wed Oct 14 - Sat Oct 17 2026  (4 days, 50 turns)"
    m = re.search(
        r"#\s*Window\s*:\s*\S+\s+([A-Za-z]{3,})\s+(\d+)\s*[-–]\s*\S+\s+\S+\s+\d+\s+(\d{4})",
        txt,
    )
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower(), 1)
        meta["dates"] = {"start": f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"}
    m = re.search(r"\((\d+)\s*days?,\s*(\d+)\s*turns?\)", txt)
    if m:
        meta["days"] = int(m.group(1))
        meta["turns"] = int(m.group(2))

    m = re.search(r"#\s*Timezone\s*:\s*(\S+)", txt)
    if m:
        meta["timezone"] = m.group(1).strip()

    meta.setdefault("dates", {"start": "2026-01-01"})
    meta.setdefault("timezone", "America/Chicago")
    return meta


def _parse_turns_from_prompts(task_dir):
    """Return an ordered list of turn dicts {day, time, wake_up_message}."""
    pf = task_dir / "prompts.txt"
    if not pf.exists():
        return []
    txt = pf.read_text(encoding="utf-8")
    turns = {}
    for m in re.finditer(
        r"--- TURN T(\d+) \(Day (\d+), (\d+:\d+)\) ---\n(.+?)(?=--- TURN|# --- END|\Z)",
        txt,
        re.DOTALL,
    ):
        turns[int(m.group(1))] = {
            "day": int(m.group(2)),
            "time": m.group(3),
            "wake_up_message": m.group(4).strip(),
        }
    if not turns:
        return []
    return [turns.get(i, {"day": 1, "time": "06:00", "wake_up_message": ""})
            for i in range(max(turns) + 1)]


def _load_role_prompt(task_dir):
    """System prompt from task.yaml (string or block scalar)."""
    yf = task_dir / "task.yaml"
    if not yf.exists():
        return ""
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        return str(doc.get("system_prompt") or "")
    except Exception:
        # regex fallback: capture the system_prompt block/scalar
        txt = yf.read_text(encoding="utf-8")
        m = re.search(r'^system_prompt:\s*"(.*?)"\s*$', txt, re.DOTALL | re.MULTILINE)
        if m:
            return m.group(1).encode().decode("unicode_escape")
        m = re.search(r"^system_prompt:\s*\|\s*\n(.*?)(?=^\S)", txt, re.DOTALL | re.MULTILINE)
        return m.group(1) if m else ""


def _parse_steer_flow(task_dir):
    """Return {turn_index: action_text} from the golden_steer_flow.md per-turn
    table (rows like ``| T0 | 1 | Pull calendar ... | F10 |``)."""
    sf = task_dir / "golden_steer_flow.md"
    if not sf.exists():
        return {}
    out = {}
    for line in sf.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*\|\s*T(\d+)\s*\|\s*\d+\s*\|\s*(.+?)\s*\|(.*)\|\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _inject_mutations_by_turn(task_dir):
    """Map {turn_index: [mutation summary strings]} from inject/stageN/mutations.json
    using each op's fires_at_turn / delivery_turn."""
    out = {}
    inj = task_dir / "inject"
    if not inj.is_dir():
        return out
    for sd in sorted(inj.glob("stage*")):
        mf = sd / "mutations.json"
        if not mf.is_file():
            continue
        try:
            raw = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        muts = raw.get("mutations") or {}
        buckets = ([("silent", muts.get("silent"))] if isinstance(muts, dict) else [])
        if isinstance(muts, dict):
            buckets += [("loud", muts.get("loud")), ("filesystem", muts.get("filesystem"))]
        for kind, ops in buckets:
            for op in (ops or []):
                t = op.get("fires_at_turn") or op.get("delivery_turn")
                tm = re.match(r"[Tt]?(\d+)", str(t)) if t else None
                if not tm:
                    continue
                idx = int(tm.group(1))
                desc = op.get("rationale") or op.get("description") or op.get("id") or ""
                out.setdefault(idx, []).append(f"[{kind}:{op.get('id', '?')}] {desc}"[:200])
    return out


def _checker_ids_by_turn(task_dir):
    """Map {turn_index: [checker_id,...]} from test_outputs.py docstrings
    (``[T<turn>_...]``). turn_index -1 collects cross-turn checkers."""
    to = task_dir / "test_outputs.py"
    out = {}
    if not to.is_file():
        return out
    for cid in re.findall(r"\[([A-Za-z0-9_]+)\]", to.read_text(encoding="utf-8")):
        m = re.match(r"T(\d+)_", cid)
        idx = int(m.group(1)) if m else (-1 if cid.upper().startswith(("CROSS", "RL", "C")) else -1)
        out.setdefault(idx, [])
        if cid not in out[idx]:
            out[idx].append(cid)
    return out


def load_task_context(task_dir):
    """Return (meta, turns, role_prompt, checkers, source).

    ``checkers`` carry runnable ``check`` callables ONLY in legacy task.py mode;
    in new-format mode they are annotation-only ({id, description, weight}).
    """
    mod = _load_legacy_task_module(task_dir)
    if mod is not None:
        meta = dict(getattr(mod, "TASK_METADATA", {}) or {})
        meta.setdefault("id", task_dir.name)
        return (
            meta,
            list(getattr(mod, "TURNS", []) or []),
            getattr(mod, "ROLE_PROMPT", "") or _load_role_prompt(task_dir),
            list(getattr(mod, "CHECKERS", []) or []),
            "legacy",
        )
    # new input format
    meta = _parse_prompts_header(task_dir)
    turns = _parse_turns_from_prompts(task_dir)
    role_prompt = _load_role_prompt(task_dir)
    # annotation-only checkers from test_weights.json (ids + weights)
    checkers = []
    tw = task_dir / "test_weights.json"
    if tw.is_file():
        try:
            wmap = json.loads(tw.read_text(encoding="utf-8")) or {}
            for node_id, weight in (wmap.items() if isinstance(wmap, dict) else []):
                cid = node_id.split("::")[-1]
                checkers.append({"id": cid, "description": "", "weight": weight})
        except (OSError, json.JSONDecodeError):
            pass
    return meta, turns, role_prompt, checkers, "new"


# --------------------------------------------------------------------------- #
# golden_data.json: load, or synthesise from the steer flow
# --------------------------------------------------------------------------- #
def _load_or_synthesise_golden_data(task_dir, turns):
    gd_path = task_dir / "golden_data.json"
    if gd_path.exists():
        return json.loads(gd_path.read_text(encoding="utf-8")), False
    # Synthesise a minimal golden_data from the canonical solve path so a
    # trajectory can be produced with zero hand-authoring.
    steer = _parse_steer_flow(task_dir)
    responses = {}
    for i in range(len(turns)):
        action = steer.get(i, "")
        responses[str(i)] = action if action else f"[TODO T{i}: author golden response]"
    return {"responses": responses, "files": {}, "turn_specs": {}, "audit": {}}, True


# --------------------------------------------------------------------------- #
# timing helpers
# --------------------------------------------------------------------------- #
def _compute_day_map(meta, turns):
    start = meta.get("dates", {}).get("start", "2026-01-01")
    y, mo, d = int(start[:4]), int(start[5:7]), int(start[8:10])
    base = datetime(y, mo, d)
    day_map = {}
    for day_num in sorted(set(t.get("day", 1) for t in turns)) or [1]:
        dt = base + timedelta(days=day_num - 1)
        day_map[day_num] = (dt.strftime("%a"), dt.strftime("%Y-%m-%d"))
    return day_map


def _compute_turn_timing(turns):
    timing = {}
    for i, t in enumerate(turns):
        day = t.get("day", 1)
        parts = str(t.get("time", "06:00")).split(":")
        timing[i] = (day, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    return timing


def _mk_tool_id(prefix, turn, call_idx):
    h = hashlib.sha256(f"{prefix}{turn:02d}{call_idx:02d}".encode()).digest()
    a = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "tooluse_" + "".join(a[b % 62] for b in h[:22])


def _mk_ts(day_map, day, h, m, tz_offset, off=0):
    _, date_str = day_map[day]
    y, mo, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    tz = timezone(timedelta(hours=tz_offset))
    dt = datetime(y, mo, d, h, m, tzinfo=tz) + timedelta(seconds=off)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


# --------------------------------------------------------------------------- #
# state + trajectory builders (format-agnostic; consume golden_data)
# --------------------------------------------------------------------------- #
def build_state(golden_data, turn_count):
    responses = golden_data.get("responses", {})
    files_data = golden_data.get("files", {})
    audit = golden_data.get("audit", {})
    svc_overrides = golden_data.get("service_states", {})

    transcript = [
        {"turn_idx": i, "agent_response": responses.get(str(i), "")}
        for i in range(turn_count)
    ]

    agent_response = {}
    for k, v in responses.items():
        try:
            agent_response[int(k)] = v
        except (ValueError, TypeError):
            pass

    fs_files = {}
    for path, content in files_data.items():
        fs_files[path] = content if isinstance(content, dict) else {"content": content}

    defaults = {
        "gmail": {"sent": [], "inbox": []},
        "whatsapp": {"sent": [], "conversations": {}},
        "twilio": {"sent": []},
        "slack": {"channels": {}},
        "notion": {"pages": {}},
        "airtable": {"bases": {}},
        "google-calendar": {"events": []},
        "google-drive": {"files": {}},
        "google-docs": {"documents": {}},
        "google-sheets": {"spreadsheets": {}},
        "google-contacts": {"contacts": {}},
        "quickbooks": {"journal_entries": [], "invoices": [], "purchase_orders": []},
        "docusign": {"envelopes": {}},
        "linear": {"issues": {}},
        "trello": {"cards": {}},
        "hubspot": {"contacts": {}},
        "box": {"files": {}},
        "dropbox": {"files": {}},
        "bamboohr": {"employees": {}},
        "gusto": {"payrolls": []},
        "obsidian": {"notes": {}},
        "confluence": {"pages": {}},
        "fedex": {"shipments": {}},
        "eventbrite": {"events": {}},
        "amadeus": {"bookings": {}},
        "square": {"orders": [], "payments": []},
    }

    state = {
        "transcript": transcript,
        "agent_response": agent_response,
        "filesystem": {"files": fs_files},
        "audit": audit,
        "api_log": golden_data.get("api_log", []),
        "agent_actions": golden_data.get("agent_actions", []),
        "confirmations_requested": golden_data.get("confirmations_requested", []),
    }
    for svc, default_val in defaults.items():
        state[svc] = svc_overrides.get(svc, default_val)
    for svc, val in svc_overrides.items():
        if svc not in state:
            state[svc] = val
    return state


def build_golden_trajectory(golden_data, meta, turns, role_prompt, prompts,
                            day_map, turn_timing, tz_offset, tz_abbrev):
    responses = golden_data.get("responses", {})
    files_data = golden_data.get("files", {})
    turn_specs = golden_data.get("turn_specs", {})
    task_id = meta.get("id", "task")

    meta_info = {
        "task_type": golden_data.get("task_type", meta.get("variant", "multi_service_workflow")),
        "task_description": golden_data.get("task_description", meta.get("name", "")),
        "task_completion_status": "success",
        "system_prompt": role_prompt,
        "platform": "macOS",
    }

    messages = []
    msg_idx = 0
    id_prefix = task_id[:4] if len(task_id) >= 4 else task_id.ljust(4, "_")

    for turn in range(len(turns)):
        day, hour, minute = turn_timing[turn]
        dow, _ = day_map[day]

        msg_idx += 1
        prompt_text = prompts.get(turn, turns[turn].get("wake_up_message", ""))
        user_text = (
            "Sender (untrusted metadata):\n```json\n"
            '{"label": "openclaw-control-ui", "id": "openclaw-control-ui"}\n'
            "```\n\n"
            f"[{dow} {day_map[day][1]} {hour:02d}:{minute:02d} {tz_abbrev}] {prompt_text}"
        )
        messages.append({
            "type": "message",
            "id": f"d{msg_idx:07d}",
            "parentId": f"d{msg_idx - 1:07d}",
            "timestamp": _mk_ts(day_map, day, hour, minute, tz_offset),
            "message": {"role": "user", "content": [{"type": "text", "text": user_text}]},
        })

        spec = turn_specs.get(str(turn), {})
        think_text = spec.get("thinking", "")
        tool_list = spec.get("tools", [])

        if tool_list:
            msg_idx += 1
            content = []
            if think_text:
                content.append({"type": "thinking", "thinking": think_text, "thinkingSignature": ""})

            resolved = []
            tids = []
            for ci, tspec in enumerate(tool_list):
                tid = _mk_tool_id(id_prefix, turn, ci)
                tids.append(tid)
                tool_type = tspec.get("type", "exec")
                tool_args = dict(tspec.get("args", {}))
                tool_result = tspec.get("result", "")
                if tool_type == "write" and "path" in tool_args and "content" not in tool_args:
                    fpath = tool_args["path"]
                    fcontent = files_data.get(fpath, "")
                    if isinstance(fcontent, dict):
                        fcontent = fcontent.get("content", "")
                    tool_args["content"] = fcontent
                    tool_result = tool_result or f"Successfully wrote {len(fcontent)} bytes to {fpath}"
                resolved.append((tool_type, tool_args, tool_result))
                content.append({"type": "toolCall", "id": tid, "name": tool_type, "arguments": tool_args})

            messages.append({
                "type": "message",
                "id": f"d{msg_idx:07d}",
                "parentId": f"d{msg_idx - 1:07d}",
                "timestamp": _mk_ts(day_map, day, hour, minute, tz_offset, off=5),
                "message": {"role": "assistant", "content": content},
            })

            for ci, (tname, _targs, tresult) in enumerate(resolved):
                msg_idx += 1
                messages.append({
                    "type": "message",
                    "id": f"d{msg_idx:07d}",
                    "parentId": f"d{msg_idx - 1:07d}",
                    "timestamp": _mk_ts(day_map, day, hour, minute, tz_offset, off=6 + ci),
                    "message": {
                        "role": "toolResult",
                        "toolCallId": tids[ci],
                        "toolName": tname,
                        "isError": False,
                        "content": [{"type": "text", "text": tresult}],
                    },
                })

        msg_idx += 1
        response = responses.get(str(turn), "")
        asst_content = [{"type": "text", "text": response}]
        if not tool_list and think_text:
            asst_content.insert(0, {"type": "thinking", "thinking": think_text, "thinkingSignature": ""})

        messages.append({
            "type": "message",
            "id": f"d{msg_idx:07d}",
            "parentId": f"d{msg_idx - 1:07d}",
            "timestamp": _mk_ts(day_map, day, hour, minute, tz_offset, off=15),
            "message": {"role": "assistant", "content": asst_content},
        })

    return {"meta_info": meta_info, "messages": messages}


# --------------------------------------------------------------------------- #
# validation (legacy task.py only — needs runnable check callables)
# --------------------------------------------------------------------------- #
def validate(state, checkers):
    runnable = [c for c in checkers if callable(c.get("check"))]
    if not runnable:
        print("\n[validate] No runnable CHECKERS (new-format task has no task/task.py).")
        print("[validate] Skipping deterministic validation — golden_trajectory still written.")
        return True

    passed = failed = skipped = 0
    failures = []
    for c in runnable:
        cid = c["id"]
        try:
            result = c["check"](state)
            if result is None:
                skipped += 1
            elif result:
                passed += 1
            else:
                failed += 1
                failures.append((cid, c.get("description", ""), c.get("weight", 0)))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            failures.append((cid, f"EXCEPTION: {exc}", c.get("weight", 0)))

    total = passed + failed + skipped
    print(f"\n{'=' * 70}")
    print(f"  GOLDEN TRAJECTORY VALIDATION")
    print(f"{'=' * 70}")
    print(f"  Passed:  {passed}/{total}")
    print(f"  Failed:  {failed}/{total}")
    if skipped:
        print(f"  Skipped: {skipped}/{total}")
    if failures:
        print(f"\n  FAILURES:")
        for cid, desc, weight in failures:
            w_label = "RED-LINE" if (isinstance(weight, (int, float)) and weight < 0) else f"w={weight}"
            print(f"    [{cid}] ({w_label}) {desc}")
    print(f"{'=' * 70}\n")
    return failed == 0


# --------------------------------------------------------------------------- #
# scaffold (golden_data.json template) — seeded from the new-format sources
# --------------------------------------------------------------------------- #
def generate_scaffold(task_dir, meta, turns, checkers):
    prompts = _parse_prompts(task_dir)
    steer = _parse_steer_flow(task_dir)
    mut_by_turn = _inject_mutations_by_turn(task_dir)
    chk_by_turn = _checker_ids_by_turn(task_dir)

    responses = {}
    turn_specs = {}
    for i, t in enumerate(turns):
        action = steer.get(i, "")
        prompt_preview = prompts.get(i, t.get("wake_up_message", ""))[:200]
        responses[str(i)] = action or f"[TODO T{i}] Prompt: {prompt_preview}..."
        turn_specs[str(i)] = {
            "_annotation": {
                "day": t.get("day"),
                "time": t.get("time"),
                "canonical_action": action,
                "mutations": mut_by_turn.get(i, []),
                "checkers": chk_by_turn.get(i, []),
            },
            "thinking": "",
            "tools": [],
        }

    return {
        "_README": (
            "Golden data for this task. Fill in responses, files, turn_specs and audit. "
            "Fields prefixed with _ are annotations, ignored by the generator. "
            "responses[] are pre-seeded from golden_steer_flow.md; refine to verbatim "
            "ideal assistant text. Add tool calls under turn_specs[i].tools."
        ),
        "task_type": meta.get("variant", "multi_service_workflow"),
        "task_description": meta.get("name", f"Golden trajectory for {meta.get('id', task_dir.name)}"),
        "responses": responses,
        "files": {},
        "turn_specs": turn_specs,
        "audit": {"_annotation": "API audit entries the checkers verify were called."},
        "api_log": [],
        "agent_actions": [],
        "confirmations_requested": [],
        "service_states": {"_annotation": "Override per-service default state if a checker reads it."},
        "_cross_checkers": chk_by_turn.get(-1, []),
    }


# --------------------------------------------------------------------------- #
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    task_dir = Path(args[0]).resolve() if args else Path(__file__).resolve().parent

    print(f"Task directory: {task_dir}")

    meta, turns, role_prompt, checkers, source = load_task_context(task_dir)
    prompts = _parse_prompts(task_dir)
    turn_count = len(turns)
    tz_name = meta.get("timezone", "America/Chicago")
    tz_offset, tz_abbrev = TZ_MAP.get(tz_name, (-5, "CDT"))

    print(f"Source : {source}-format")
    print(f"Task   : {meta.get('id', task_dir.name)}")
    print(f"Turns  : {turn_count}  Days: {meta.get('days', '?')}  Start: {meta.get('dates', {}).get('start')}")
    print(f"TZ     : {tz_name} ({tz_abbrev}, UTC{tz_offset:+d})")
    print(f"Checkers (annotation{'+runnable' if source == 'legacy' else ' only'}): {len(checkers)}")

    if turn_count == 0:
        print("ERROR: no turns parsed (need prompts.txt or task.py TURNS).")
        sys.exit(2)

    if "--scaffold" in flags:
        scaffold = generate_scaffold(task_dir, meta, turns, checkers)
        out_path = task_dir / "golden_data.json"
        out_path.write_text(json.dumps(scaffold, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nScaffold written: {out_path}")
        print(f"  {turn_count} responses pre-seeded from golden_steer_flow.md")
        print(f"  {turn_count} turn_spec annotations (mutations + checkers per turn)")
        return

    if "--validate-only" in flags:
        for candidate in [task_dir / "tests" / "agent_state.json", task_dir / "agent_state.json"]:
            if candidate.exists():
                state = json.loads(candidate.read_text(encoding="utf-8"))
                sys.exit(0 if validate(state, checkers) else 1)
        print(f"ERROR: No agent_state.json found in {task_dir}")
        sys.exit(1)

    golden_data, synthesised = _load_or_synthesise_golden_data(task_dir, turns)
    if synthesised:
        print("golden_data.json absent — synthesised responses from golden_steer_flow.md "
              "(run --scaffold then edit for a richer trajectory).")

    day_map = _compute_day_map(meta, turns)
    turn_timing = _compute_turn_timing(turns)

    state = build_state(golden_data, turn_count)
    (task_dir / "agent_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Written: {task_dir / 'agent_state.json'}")
    tests_state = task_dir / "tests" / "agent_state.json"
    tests_state.parent.mkdir(parents=True, exist_ok=True)
    tests_state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Written: {tests_state}")

    trajectory = build_golden_trajectory(
        golden_data, meta, turns, role_prompt, prompts,
        day_map, turn_timing, tz_offset, tz_abbrev,
    )
    traj_path = task_dir / "golden_trajectory.json"
    traj_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written: {traj_path}  ({len(trajectory['messages'])} messages)")

    if "--write-only" not in flags:
        sys.exit(0 if validate(state, checkers) else 1)


if __name__ == "__main__":
    main()
