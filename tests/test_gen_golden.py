"""Tests for scripts/gen_golden.py (golden-trajectory pipeline)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "gen_golden", Path(__file__).resolve().parents[1] / "scripts" / "gen_golden.py"
)
gen_golden = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_golden)  # type: ignore[union-attr]


# ----------------------------------------------------------------- fixtures --

def _make_run_and_task(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run_1"
    (run / "task_output" / "workspace_full").mkdir(parents=True)
    # chat.jsonl: session event + 2 user turns (with headers) + assistant
    events = [
        {"type": "session", "id": "s1"},
        {"type": "model_change", "id": "m1", "provider": "anthropic",
         "modelId": "claude-opus-4-6"},
        {"type": "message", "id": "u1", "timestamp": "t0", "message": {
            "role": "user", "content": [{"type": "text",
                "text": "You are an expert in a restricted, non-interactive environment. "
                        "Solve the task efficiently before the timeout (1800s). Run all "
                        "processes in the foreground without user input or background "
                        "services. Provide a complete, functional solution in a single "
                        "pass with no placeholders. \n[TURN 1 (Day 1, 06:45, Multi-Agent)]"
                        "\n\n\nFirst real prompt."}]}},
        {"type": "message", "id": "a1", "timestamp": "t1", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
        {"type": "message", "id": "u2", "timestamp": "t2", "message": {
            "role": "user", "content": [{"type": "text",
                "text": "[Mon 2026-06-15 17:48 UTC] [TURN 2 (Day 1, 08:30, Light)]"
                        "\n\n\nSecond real prompt."}]}},
    ]
    (run / "chat.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (run / "task_output" / "workspace_full" / "spawn_tree.jsonl").write_text(
        json.dumps({"spawn_id": "spw_1", "status": "ok"}) + "\n", encoding="utf-8")

    task = tmp_path / "task"
    (task / "persona").mkdir(parents=True)
    for name in gen_golden._PERSONA_FILES:
        (task / "persona" / name).write_text(f"{name} body", encoding="utf-8")
    (task / "rubric.json").write_text("[]", encoding="utf-8")
    (task / "test_outputs.py").write_text("# tests", encoding="utf-8")
    (task / "golden_steer_flow.md").write_text("# steer", encoding="utf-8")
    (task / "data").mkdir()
    (task / "data" / "doc.pdf").write_text("PDF", encoding="utf-8")
    (task / "data" / "chart.png").write_text("PNG", encoding="utf-8")
    return run, task


# ------------------------------------------------------------------ stage 1 --

def test_assemble_builds_full_pack(tmp_path: Path):
    run, task = _make_run_and_task(tmp_path)
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    for f in ("events.jsonl", "prompts.json", "session-branch.json",
              "artifacts.json", "metadata.json", "rubric.json",
              "test_outputs.py", "golden_steer_flow.md"):
        assert (pack / f).is_file(), f"missing {f}"
    assert len(list((pack / "persona").iterdir())) == 7


def test_assemble_cleans_user_turns(tmp_path: Path):
    run, task = _make_run_and_task(tmp_path)
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    prompts = json.loads((pack / "prompts.json").read_text())["submittedPrompts"]
    assert prompts == ["First real prompt.", "Second real prompt."]
    # events.jsonl user turns are cleaned too
    for line in (pack / "events.jsonl").read_text().splitlines():
        ev = json.loads(line)
        if ev.get("message", {}).get("role") == "user":
            t = ev["message"]["content"][0]["text"]
            assert "UTC]" not in t and "[TURN" not in t and "non-interactive" not in t


def test_assemble_metadata_and_artifacts(tmp_path: Path):
    run, task = _make_run_and_task(tmp_path)
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    meta = json.loads((pack / "metadata.json").read_text())
    assert meta["prompting"]["systemPromptReport"]["injectedWorkspaceFiles"] == \
        list(gen_golden._PERSONA_FILES)
    assert "AGENTS.md body" in meta["system_prompt"]
    assert meta["model"]["modelId"] == "claude-opus-4-6"
    arts = json.loads((pack / "artifacts.json").read_text())
    by_name = {a["name"]: a["type"] for a in arts}
    assert by_name["doc.pdf"] == "pdf" and by_name["chart.png"] == "image"


# ------------------------------------------------------------------ stage 2 --

def test_run_generator_assembles_inputs_and_calls_model(tmp_path: Path, monkeypatch):
    run, task = _make_run_and_task(tmp_path)
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    sysmd = tmp_path / "gen.md"
    sysmd.write_text("GENERATOR SYSTEM PROMPT", encoding="utf-8")

    captured = {}

    class _Msg:
        def __init__(self, c): self.content = c
    class _Choice:
        def __init__(self, c, f): self.message = _Msg(c); self.finish_reason = f
    class _Resp:
        def __init__(self, c, f="stop"):
            self.choices = [_Choice(c, f)]
            self.usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=20)

    fake = types.ModuleType("litellm")
    def _completion(model, messages, max_tokens, stream):
        captured["model"] = model
        captured["messages"] = messages
        return _Resp("=== PARENT TRAJECTORY ===\n{}")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)

    text, usage = gen_golden.run_generator(
        pack, sysmd, model="anthropic/claude-opus-4-6", max_tokens=1000)
    assert text.startswith("=== PARENT TRAJECTORY ===")
    assert usage == {"input_tokens": 10, "output_tokens": 20}
    assert captured["messages"][0]["content"] == "GENERATOR SYSTEM PROMPT"
    user = captured["messages"][1]["content"]
    for marker in ("INPUT 1", "INPUT 2", "INPUT 3", "INPUT 4", "INPUT 5",
                   "test_outputs.py", "golden_steer_flow.md"):
        assert marker in user


# ------------------------------------------------------------------ stage 3 --

_GOOD = """=== PARENT TRAJECTORY ===
{"meta_info": {}, "messages": [
  {"type":"message","id":"d0000001","parentId":"d0000000","message":{"role":"user","content":[{"type":"text","text":"hi"}]}},
  {"type":"message","id":"d0000002","parentId":"d0000001","message":{"role":"assistant","content":[{"type":"toolCall","id":"tc1","name":"exec","input":{}}]}},
  {"type":"message","id":"d0000003","parentId":"d0000002","message":{"role":"toolResult","toolCallId":"tc1","toolName":"exec","isError":false,"content":[{"type":"text","text":"r"}]}},
  {"type":"message","id":"d0000004","parentId":"d0000003","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}
]}

=== CHILD TRAJECTORY: research (session: agent:main:subagent:x) ===
{"meta_info": {}, "messages": [
  {"type":"message","id":"c0000001","parentId":"c0000000","message":{"role":"user","content":[{"type":"text","text":"sub"}]}},
  {"type":"message","id":"c0000002","parentId":"c0000001","message":{"role":"assistant","content":[{"type":"text","text":"child done"}]}}
]}
"""


def test_parse_good_output(tmp_path: Path):
    rep = gen_golden.parse_golden_output(_GOOD, tmp_path)
    assert rep["errors"] == []
    assert (tmp_path / "golden_trajectory.json").is_file()
    assert (tmp_path / "golden_subagents" / "research.json").is_file()
    assert rep["children"] == ["golden_subagents/research.json"]


def test_parse_rejects_em_dash(tmp_path: Path):
    rep = gen_golden.parse_golden_output(_GOOD.replace("done", "do—ne"), tmp_path)
    assert any("dash" in e for e in rep["errors"])


def test_parse_flags_unbalanced_toolcall(tmp_path: Path):
    bad = _GOOD.replace(
        '{"type":"message","id":"d0000003","parentId":"d0000002","message":{"role":"toolResult","toolCallId":"tc1","toolName":"exec","isError":false,"content":[{"type":"text","text":"r"}]}},\n  ',
        "")
    # also fix the id sequence break it introduces -> still should flag the toolCall
    rep = gen_golden.parse_golden_output(bad, tmp_path)
    assert any("toolCall" in e for e in rep["errors"])


def test_parse_flags_bad_id_sequence(tmp_path: Path):
    bad = _GOOD.replace('"id":"d0000002"', '"id":"d0000009"')
    rep = gen_golden.parse_golden_output(bad, tmp_path)
    assert any("sequence" in e for e in rep["errors"])


def test_assemble_dedupes_reissued_final_turn(tmp_path: Path):
    # report §11: chat.jsonl has an adjacent re-issued final turn -> golden pack
    # must show the canonical count (turn collapsed, keep the re-run).
    run, task = _make_run_and_task(tmp_path)
    events = [json.loads(l) for l in (run / "chat.jsonl").read_text().splitlines()]
    # append a duplicate of the last user turn (the "Second real prompt." turn)
    events.append({"type": "message", "id": "u2b", "timestamp": "t3", "message": {
        "role": "user", "content": [{"type": "text",
            "text": "[Mon 2026-06-15 18:00 UTC] [TURN 2 (Day 1, 09:00, Light)]"
                    "\n\n\nSecond real prompt."}]}})
    events.append({"type": "message", "id": "a2b", "timestamp": "t4", "message": {
        "role": "assistant", "content": [{"type": "text", "text": "re-run"}]}})
    (run / "chat.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    prompts = json.loads((pack / "prompts.json").read_text())["submittedPrompts"]
    assert prompts == ["First real prompt.", "Second real prompt."]  # 2, not 3


def _write_task_yaml(task: Path, body: str):
    (task / "task.yaml").write_text(body, encoding="utf-8")


def test_metadata_system_prompt_from_task_yaml(tmp_path: Path):
    run, task = _make_run_and_task(tmp_path)
    _write_task_yaml(task,
        "system_prompt: |\n  You are the authored assistant.\n"
        "task_type:\n  - Search & Retrieval\n  - Productivity Flow\n"
        "task_description: Authored description here.\n")
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    meta = json.loads((pack / "metadata.json").read_text())
    assert meta["system_prompt"].startswith("You are the authored assistant.")
    assert meta["task_type"] == "Search & Retrieval, Productivity Flow"
    assert meta["task_description"] == "Authored description here."
    assert "task.yaml" in meta["_note"]


def test_metadata_falls_back_to_persona_without_task_yaml(tmp_path: Path):
    run, task = _make_run_and_task(tmp_path)  # _make_run_and_task writes no task.yaml
    pack = gen_golden.build_golden_input_pack(run, task, tmp_path / "out")
    meta = json.loads((pack / "metadata.json").read_text())
    assert "AGENTS.md body" in meta["system_prompt"]      # persona-assembled fallback
    assert "persona files" in meta["_note"]
