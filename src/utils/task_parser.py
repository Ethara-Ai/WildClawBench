from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv

import yaml

load_dotenv()
logger = logging.getLogger(__name__)
# Resolve task-relative paths from repository root, not src/.
ROOT_DIR = Path(__file__).resolve().parents[2]


def parse_task_md(task_file: Path) -> dict:
    """Extract task_id, prompt, workspace_path, and automated_checks from task.md."""
    content = task_file.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not fm_match:
        raise ValueError(f"YAML frontmatter not found: {task_file}")

    metadata = yaml.safe_load(fm_match.group(1))
    body     = fm_match.group(2)

    sections: dict[str, str] = {}
    current_section: Optional[str] = None
    lines: list[str] = []
    for line in body.split("\n"):
        header = re.match(r"^##\s+(.+)$", line)
        if header:
            if current_section is not None:
                sections[current_section] = "\n".join(lines).strip()
            current_section = header.group(1)
            lines = []
        else:
            lines.append(line)
    if current_section is not None:
        sections[current_section] = "\n".join(lines).strip()

    def strip_codeblock(raw: str) -> str:
        s = re.sub(r"^```[^\n]*\n?", "", raw.strip())
        s = re.sub(r"\n?```$", "", s).strip()
        return s

    prompt = sections.get("Prompt", "").strip()

    raw_workspace  = sections.get("Workspace Path", "").strip()
    workspace_path = strip_codeblock(raw_workspace)
    if not workspace_path:
        raise ValueError(f"Missing ## Workspace Path in task.md: {task_file}")

    skills_path = "skills"

    automated_checks = strip_codeblock(sections.get("Automated Checks", ""))
    env    = strip_codeblock(sections.get("Env",    ""))
    skills = strip_codeblock(sections.get("Skills",    ""))
    warmup = strip_codeblock(sections.get("Warmup", ""))

    task_id         = metadata.get("id",             task_file.stem)
    timeout_seconds = int(metadata.get("timeout_seconds", 120))

    wp = Path(workspace_path)
    if not wp.is_absolute():
        wp = (ROOT_DIR / wp).resolve()
    workspace_path = str(wp)

    sp = Path(skills_path)
    if not sp.is_absolute():
        sp = (ROOT_DIR / sp).resolve()
    skills_path = str(sp)

    return {
        "task_id":          task_id,
        "prompt":           prompt,
        "workspace_path":   workspace_path,
        "skills_path":      skills_path,
        "automated_checks": automated_checks,
        "env":              env,
        "skills":           skills,
        "warmup":           warmup,
        "timeout_seconds":  timeout_seconds,
        "file_path":        str(task_file.resolve()),
        "category":         task_file.parent.name,
    }


# ---------------------------------------------------------------------------
# Multi-format dispatcher (kensei-parity): markdown | yaml | native directory.
# run_batch.py uses load_task() for directories and falls back to parse_task_md
# for .md files so existing behavior is preserved exactly.
# ---------------------------------------------------------------------------

def load_task(path: str | Path) -> dict:
    """Load a task from a .md / .yaml file or a native task directory.

    - .md          -> parse_task_md (unchanged fork behavior), normalized superset
    - .yaml/.yml   -> _load_yaml_task
    - directory    -> task.yaml|task.yml|task.md inside, else native
                      (prompt.txt + rubric.json) layout
    """
    p = Path(path)
    if p.is_dir():
        for candidate in ("task.yaml", "task.yml", "task.md"):
            f = p / candidate
            if f.is_file():
                return _attach_drift_script(load_task(f), p)
        # Native layout accepts either prompt.txt (single prompt) or prompts.txt
        # (Talos inject-format per-turn wake-up script; see inject_director.py).
        if ((p / "prompt.txt").is_file() or (p / "prompts.txt").is_file()) and (p / "rubric.json").is_file():
            return _attach_drift_script(_load_native_task(p), p)
        raise FileNotFoundError(f"No task file found in {p}")
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _attach_drift_script(_load_yaml_task(p), p.parent)
    if suffix == ".md":
        base = parse_task_md(p)
        # Augment the md dict with the uniform superset used by the kensei flow.
        base.setdefault("initial_prompt", base.get("prompt", ""))
        base.setdefault("persona", "marcus")
        base.setdefault("persona_dir", "")
        base.setdefault("rubrics", [])
        base.setdefault("task_dir", str(p.parent))
        base.setdefault("gt_dir", str(p.parent / "gt") if (p.parent / "gt").is_dir() else "")
        base.setdefault("attachments", [])
        base.setdefault("format", "md")
        return _attach_drift_script(base, p.parent)
    raise ValueError(f"Unsupported task file format: {p.suffix}")


def _load_provided_tests(task_dir: Path) -> tuple[str, str]:
    """Load a hand-authored test suite shipped with the task, if present.

    When a task directory provides BOTH ``test_outputs.py`` and
    ``test_weights.json`` (at the task root or under ``tests/``), the harness
    executes those verbatim instead of LLM-generating a suite. The generate-vs-
    execute gate in eval/run_batch.py only generates when ``task["test_code"]``
    is empty, so populating it here transparently skips generation and routes
    straight to src/utils/test_executor.py.

    Requiring ``test_weights.json`` as the opt-in signal is deliberate: legacy
    fixture-based ``test_outputs.py`` files (e.g. input/alden-croft/) ship no
    weights and are incompatible with the no-fixture runner, so they stay on the
    LLM-generation path.

    The suite must match the runner contract in test_executor.py: top-level
    ``Test*`` classes with ``test_*(self)`` methods, no pytest fixtures, stdlib
    only, and mock-API URLs read from ``<SERVICE>_URL`` env vars.

    The suite file may be named ``test_outputs.py`` (canonical) or
    ``test_output.py`` (a common singular-typo variant emitted by some
    generators); both are accepted so a single dropped ``s`` does not silently
    route the task to the LLM-generation fallback. ``test_outputs.py`` wins when
    both exist.

    Returns ``(test_code, test_weights_json)`` — ``test_weights_json`` is the raw
    file text (the executor consumes a JSON string). Returns ``("", "")`` when no
    complete provided suite is found.
    """
    for sub in ("", "tests"):
        base = task_dir / sub if sub else task_dir
        weights_f = base / "test_weights.json"
        if not weights_f.is_file():
            continue
        code_f = None
        for fname in ("test_outputs.py", "test_output.py"):
            cand = base / fname
            if cand.is_file():
                code_f = cand
                break
        if code_f is None:
            continue
        try:
            code = code_f.read_text(encoding="utf-8")
            weights = weights_f.read_text(encoding="utf-8")
        except OSError:
            continue
        if code.strip() and weights.strip():
            return code, weights
    return "", ""


def _load_checkers_and_conftest(task_dir: Path) -> tuple[str, str]:
    """Load the deterministic CHECKERS module (task.py) and its conftest.py.

    Fixture-based suites (``def test_x(state, task_checkers)``) import their
    deterministic checkers from a sibling ``task.py`` (``CHECKERS`` list) and
    take a ``state`` fixture from a ``conftest.py``. The harness ships task.py
    into the test sandbox so the ``task_checkers`` fixture resolves, and writes
    both into the published bundle's ``data/tests/`` (task/task.py + conftest).

    Looked up in priority order so both the canonical layout and ALDEN's
    ``Extra files/`` drop work:
      tests/task/task.py, task/task.py, Extra files/task.py, tests/task.py
    Returns ``(checkers_code, conftest_code)`` — empty strings when absent.
    """
    checkers_code = ""
    for rel in ("tests/task/task.py", "task/task.py", "Extra files/task.py", "tests/task.py"):
        cand = task_dir / rel
        if cand.is_file():
            try:
                checkers_code = cand.read_text(encoding="utf-8")
            except OSError:
                checkers_code = ""
            if checkers_code.strip():
                break
    conftest_code = ""
    for rel in ("tests/conftest.py", "conftest.py", "Extra files/conftest.py"):
        cand = task_dir / rel
        if cand.is_file():
            try:
                conftest_code = cand.read_text(encoding="utf-8")
            except OSError:
                conftest_code = ""
            if conftest_code.strip():
                break
    return checkers_code, conftest_code


def _attach_drift_script(task: dict, task_dir: Path) -> dict:
    # Surface drift.yaml / drift.yml on the task dict so run_batch can start
    # a DriftDirector. Sets to None (not absent) when no script is present so
    # downstream code can use a single key check.
    task["drift_script_path"] = None
    for candidate in ("drift.yaml", "drift.yml"):
        f = task_dir / candidate
        if f.is_file():
            task["drift_script_path"] = str(f.resolve())
            break
    # stages.yaml drives the ClawMark-style multi-turn, inject-while-idle model
    # (silent injection between agent turns). Like drift it needs the admin
    # plane on the per-task mock stack, so run_batch keys admin-plane setup on
    # either key being present.
    task["stages_path"] = None
    for candidate in ("stages.yaml", "stages.yml"):
        f = task_dir / candidate
        if f.is_file():
            task["stages_path"] = str(f.resolve())
            break
    # inject/ dir drives the Talos-style staged injection (per-turn wake-up
    # script in prompts.txt + inject/stageN/mutations.json applied at turn
    # boundaries). Like drift/stages it needs the admin plane on the per-task
    # mock stack. See src/utils/inject_director.py.
    task["inject_path"] = None
    inject_dir = task_dir / "inject"
    if inject_dir.is_dir():
        task["inject_path"] = str(inject_dir.resolve())
    # multi_agent.* config opt-in (sub-agent spawning). Resolution order, highest
    # precedence first:
    #   1. task_config.yaml `multi_agent:` block (explicit, full control).
    #   2. task.yaml `multi_agent_complex_turns: [<1-indexed turn>, ...]` — the
    #      config-driven path: fan-out fires (and is scored) on the turns the
    #      author marked complex, with NO "Multi-Agent" token in the prompts.txt
    #      header required.
    #   3. Legacy fallback: scan prompts.txt for "Multi-Agent" turn-header labels
    #      so older tasks keep working.
    # All three emit the same shape: checker_id = "T<turn_index>_MA", aggregate
    # "MA_C1", min_subagents=2.
    task["multi_agent_config"] = {}
    task["multi_agent_enabled"] = False
    cfg_path = task_dir / "task_config.yaml"
    if cfg_path.is_file():
        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                ma = raw.get("multi_agent")
                if isinstance(ma, dict):
                    task["multi_agent_config"] = ma
                    task["multi_agent_enabled"] = bool(ma.get("enabled"))
        except (yaml.YAMLError, OSError):
            pass
    if not task["multi_agent_enabled"]:
        n_turns = len(task.get("turn_messages") or []) or None
        synth = _multi_agent_config_from_complex_turns(
            task.get("multi_agent_complex_turns"),
            num_turns=n_turns,
            task_id=task.get("task_id") or task.get("name"),
        )
        if synth.get("enabled"):
            task["multi_agent_config"] = synth
            task["multi_agent_enabled"] = True
    if not task["multi_agent_enabled"]:
        synth = _synthesize_multi_agent_config(task_dir)
        if synth.get("enabled"):
            task["multi_agent_config"] = synth
            task["multi_agent_enabled"] = True
    return task


def _synthesize_multi_agent_config(task_dir: Path) -> dict:
    """Derive a multi_agent config from prompts.txt "Multi-Agent" turn headers.

    Header form: "--- TURN [T]<n> (..., Multi-Agent) ---" (1-indexed in
    prompts.txt). The openclaw runner exposes 0-indexed turn_index, so we
    subtract 1 to match the expected_per_turn key contract.
    """
    prompts_path = task_dir / "prompts.txt"
    if not prompts_path.is_file():
        return {}
    try:
        text = prompts_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    pattern = re.compile(
        r"^---\s*TURN\s+T?(\d+).*?Multi-Agent.*?---\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    ma_turns = sorted({int(m) - 1 for m in pattern.findall(text)})
    if not ma_turns:
        return {}
    return {
        "enabled": True,
        "default_allowed_tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        "expected_per_turn": {
            str(idx): {"min_subagents": 2, "checker_id": f"T{idx}_MA"}
            for idx in ma_turns
        },
        "aggregate_checker_id": "MA_C1",
    }


def _multi_agent_config_from_complex_turns(
    complex_turns: Any,
    num_turns: int | None = None,
    *,
    task_id: str | None = None,
    min_subagents: int = 2,
) -> dict:
    """Derive a multi_agent config from task.yaml ``multi_agent_complex_turns``.

    ``complex_turns`` are 1-indexed turn numbers matching the prompts.txt
    ``--- TURN T<n> ---`` headers; the openclaw runner exposes 0-indexed
    ``turn_index`` (and spawn_tree.jsonl rows carry that), so we subtract 1. The
    returned shape is identical to :func:`_synthesize_multi_agent_config` so
    downstream scoring (``spawn_tree_checks.build_checker_state``) is unchanged —
    only the *source* of the config differs (config key vs. prompts.txt token).

    When ``num_turns`` is known, a configured turn that exceeds the actual turn
    count is kept (so the author's intent is honoured) but logged loudly: such a
    turn can never spawn, so its ``T<idx>_MA`` checker and the ``MA_C1``
    aggregate will fail until task.yaml / prompts.txt / the config agree.
    """
    if not isinstance(complex_turns, (list, tuple)):
        return {}
    idxs: set[int] = set()
    for n in complex_turns:
        try:
            idx = int(n) - 1
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        if num_turns is not None and idx >= num_turns:
            logger.warning(
                "[%s] multi_agent_complex_turns lists turn %s but prompts.txt "
                "has only %d turn(s); turn_index %d can never spawn, so checker "
                "T%d_MA and the MA_C1 aggregate will fail. Align task.yaml "
                "`turns` / prompts.txt / `multi_agent_complex_turns`.",
                task_id or "?", n, num_turns, idx, idx,
            )
        idxs.add(idx)
    if not idxs:
        return {}
    return {
        "enabled": True,
        "default_allowed_tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        "expected_per_turn": {
            str(idx): {"min_subagents": min_subagents, "checker_id": f"T{idx}_MA"}
            for idx in sorted(idxs)
        },
        "aggregate_checker_id": "MA_C1",
    }


def _derive_taxonomy_for_native_task(
    task_dir: Path, rubrics: list, attachments: list
) -> tuple[str, str]:
    # Persona-format native tasks (input/<task>/{prompt.txt,rubric.json,persona/,data/,mock_data/})
    # carry no task.toml — the reference trajectory still expects non-empty
    # taxonomy_l1/l2. Optional `<task_dir>/taxonomy.json` overrides; otherwise
    # we derive L1 from the dominant rubric evaluation_target and L2 from the
    # combination of attachment MIME families + per-task mock_data/<api>/ dirs.
    override = task_dir / "taxonomy.json"
    if override.is_file():
        try:
            data = json.loads(override.read_text(encoding="utf-8")) or {}
            l1 = str(data.get("l1") or data.get("taxonomy_l1") or "")
            l2 = str(data.get("l2") or data.get("taxonomy_l2") or "")
            if l1 or l2:
                return l1, l2
        except (json.JSONDecodeError, OSError):
            pass

    targets: dict[str, int] = {}
    for r in rubrics if isinstance(rubrics, list) else []:
        if isinstance(r, dict):
            t = str(r.get("evaluation_target") or "").strip()
            if t:
                targets[t] = targets.get(t, 0) + 1
    dominant_target = max(targets, key=lambda k: targets[k]) if targets else ""
    l1 = {
        "final_answer": "Information Synthesis",
        "workspace_artifact": "File Generation",
        "tool_call_audit": "Tool Use Audit",
    }.get(dominant_target, "Multimodal Reasoning")

    mime_families: set[str] = set()
    for att in attachments if isinstance(attachments, list) else []:
        if isinstance(att, dict):
            mime = str(att.get("mimeType") or "")
            if mime.startswith("image/"):
                mime_families.add("image")
            elif mime in ("text/csv", "application/vnd.ms-excel"):
                mime_families.add("tabular")
            elif mime == "application/pdf":
                mime_families.add("pdf")
            elif mime.startswith(("audio/", "video/")):
                mime_families.add("media")
    api_dirs: list[str] = []
    mock_root = task_dir / "mock_data"
    if mock_root.is_dir():
        api_dirs = sorted(d.name.replace("-api", "") for d in mock_root.iterdir() if d.is_dir())

    parts: list[str] = []
    if "image" in mime_families and "tabular" in mime_families:
        parts.append("receipt_reconciliation")
    elif "image" in mime_families:
        parts.append("image_grounded_analysis")
    elif "tabular" in mime_families:
        parts.append("tabular_data_analysis")
    elif "pdf" in mime_families:
        parts.append("pdf_extraction")
    elif "media" in mime_families:
        parts.append("media_processing")
    else:
        parts.append("text_analysis")
    if api_dirs:
        parts.append("with_" + "_".join(api_dirs[:3]) + "_apis")
    l2 = "__".join(parts) if parts else "general"
    return l1, l2


def _append_workspace_hint(prompt: str, attachments: list[dict]) -> str:
    if not attachments:
        return prompt
    names = sorted({str(a.get("storedAs") or a.get("name") or "") for a in attachments if a})
    names = [n for n in names if n]
    if not names:
        return prompt
    # List every staged input in full. A prior 30-item cap left a literal
    # "... (N more)" truncation marker in the agent's first user message (and
    # thus in the published trajectory), and hid real inputs from the agent.
    listing = "\n".join(f"- {n}" for n in names)
    # Output-location contract pinned to two harness-side enforcers:
    # (a) `collect_output_from_container` snapshots `/root/workspace/`
    # before the agent runs and copies every new-or-modified file out
    # into `task_output/artifacts/`; (b) openclaw's media tools enforce a
    # localRoots allowlist (`assertLocalMediaAllowed` in
    # src/media/local-media-access.ts) that admits `/root/workspace/` and
    # rejects `/tmp/*` plus `/root/<other>/*`. Deliverables written
    # anywhere else are invisible to the grader at collection time and
    # may also fail the image tool at read time.
    hint = (
        "\n\n---\n"
        "Workspace inputs (already staged on disk at `/root/workspace/`):\n"
        f"{listing}\n"
        "Save EVERY output artifact you produce under `/root/workspace/`. "
        "Files written anywhere else (including `/tmp/` and elsewhere "
        "under `/root/`) will NOT be collected as deliverables."
    )
    return prompt + hint


def _load_golden_trajectory(task_dir: Path) -> str:
    """Return the raw JSON text of ``<task_dir>/golden_trajectory.json`` if present.

    Stored verbatim on Task.golden_trajectory (a TEXT column) and emitted into the
    Harbor bundle by write_bundle via ``_trajectory_entries`` (which json-parses
    the string). Produced by system_prompts/generate_golden_trajectory.py.
    """
    p = task_dir / "golden_trajectory.json"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _load_native_task(task_dir: Path) -> dict:
    # kensei-native task dir: prompt.txt + rubric.json + persona/ + data/ + mock_data/ + gt/.
    # workspace_path is left empty here; run_batch stages a workspace from
    # `attachments` so task solutions/tests under data/ are never exposed.
    turn_messages: list[str] = []
    if (task_dir / "prompt.txt").is_file():
        prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    elif (task_dir / "prompts.txt").is_file():
        # Talos inject-format task: prompts.txt holds the per-turn wake-up script.
        # Turn 0 is the initial prompt; later turns drive the multi-turn silent-
        # injection loop applied at stage boundaries (see inject_director.py).
        from src.utils.inject_director import parse_prompts_file
        turn_messages = parse_prompts_file(task_dir / "prompts.txt")
        prompt = (turn_messages[0] if turn_messages else "").strip()
    else:
        prompt = ""
    try:
        rubrics = json.loads((task_dir / "rubric.json").read_text(encoding="utf-8")) or []
        if isinstance(rubrics, dict):
            rubrics = rubrics.get("rubrics") or []
    except (json.JSONDecodeError, OSError):
        rubrics = []

    parts = task_dir.name.split("__")
    persona = parts[0] if parts and parts[0] else "marcus"

    attachments: list[dict] = []
    data_dir = task_dir / "data"
    if data_dir.is_dir():
        for f in sorted(data_dir.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            # never expose graders/solutions/bundle-scaffolding to the agent workspace
            rel = f.relative_to(data_dir).as_posix()
            if rel.startswith(("tests/", "solution/", "environment/")) or rel in ("task.toml", "instruction.md"):
                continue
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            attachments.append({
                "name": f.name,
                "mimeType": mime,
                "path": str(f.resolve()),
                "size": f.stat().st_size,
                "storedAs": rel,
                "role": "primary",
                "description": "",
            })

    persona_dir = task_dir / "persona"
    derived_l1, derived_l2 = _derive_taxonomy_for_native_task(task_dir, rubrics, attachments)
    prompt_with_inputs = _append_workspace_hint(prompt, attachments)
    provided_test_code, provided_test_weights = _load_provided_tests(task_dir)
    checkers_code, conftest_code = _load_checkers_and_conftest(task_dir)
    return {
        "task_id": task_dir.name,
        "prompt": prompt_with_inputs,
        "initial_prompt": prompt_with_inputs,
        "test_code": provided_test_code,
        "test_weights": provided_test_weights,
        # CHECKERS module + conftest for fixture-based suites (state,
        # task_checkers). Shipped into the test sandbox and the bundle.
        "checkers_code": checkers_code,
        "conftest_code": conftest_code,
        "persona": persona,
        "persona_dir": str(persona_dir) if persona_dir.is_dir() else "",
        "system_prompt": "",
        "task_description": prompt,
        "rubrics": rubrics,
        "automated_checks": "",
        "difficulty": "medium",
        "l1": derived_l1,
        "l2": derived_l2,
        "task_type": "",
        "timeout_seconds": 1800,
        "category": "",
        "file_path": str(task_dir),
        "task_dir": str(task_dir),
        "gt_dir": str(task_dir / "gt") if (task_dir / "gt").is_dir() else "",
        "attachments": attachments,
        "workspace_path": "",
        "skills": "",
        "skills_path": "",
        "warmup": "",
        "env": "",
        "format": "native",
        # Full per-turn wake-up script for Talos inject-format tasks (empty for
        # single-prompt tasks). run_batch feeds these to the multi-turn runner.
        "turn_messages": turn_messages,
        # Reference golden trajectory (optional). Flows to the Harbor bundle's
        # golden_trajectory.json via write_bundle.
        "golden_trajectory": _load_golden_trajectory(task_dir),
    }


def _collect_data_attachments(task_dir: Path) -> list[dict]:
    """Stage the native ``data/`` dir as agent workspace inputs, excluding
    graders/solutions/scaffolding (same rule as the native loader)."""
    attachments: list[dict] = []
    data_dir = task_dir / "data"
    if not data_dir.is_dir():
        return attachments
    for f in sorted(data_dir.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = f.relative_to(data_dir).as_posix()
        if rel.startswith(("tests/", "solution/", "environment/")) or rel in ("task.toml", "instruction.md"):
            continue
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        attachments.append({
            "name": f.name,
            "mimeType": mime,
            "path": str(f.resolve()),
            "size": f.stat().st_size,
            "storedAs": rel,
            "role": "primary",
            "description": "",
        })
    return attachments


def _load_yaml_task(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    task_dir = path.parent
    task_id = str(raw.get("task_id") or task_dir.name)
    provided_test_code, provided_test_weights = _load_provided_tests(task_dir)
    checkers_code, conftest_code = _load_checkers_and_conftest(task_dir)

    # HYBRID yaml+native: the new task.yaml format carries metadata (system_prompt,
    # required_apis, task_type) but keeps the conversation in prompts.txt, the
    # grading rubric in rubric.json, and workspace inputs under data/. Load those
    # from disk when task.yaml does not provide them inline.
    prompt = str(raw.get("initial_prompt") or raw.get("prompt") or "")
    turn_messages: list[str] = []
    if not prompt:
        if (task_dir / "prompt.txt").is_file():
            prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8").strip()
        elif (task_dir / "prompts.txt").is_file():
            # Talos inject-format: prompts.txt is the per-turn wake-up script.
            from src.utils.inject_director import parse_prompts_file
            turn_messages = parse_prompts_file(task_dir / "prompts.txt")
            prompt = (turn_messages[0] if turn_messages else "").strip()

    rubrics = raw.get("rubrics") or []
    if not rubrics and (task_dir / "rubric.json").is_file():
        try:
            _rj = json.loads((task_dir / "rubric.json").read_text(encoding="utf-8")) or []
            rubrics = (_rj.get("rubrics") or []) if isinstance(_rj, dict) else _rj
        except (json.JSONDecodeError, OSError):
            rubrics = []

    attachments = _load_attachments_yaml(raw, task_dir)
    if not attachments:
        attachments = _collect_data_attachments(task_dir)
    if prompt:
        prompt = _append_workspace_hint(prompt, attachments)
    return {
        "task_id": task_id,
        "prompt": prompt,
        "initial_prompt": prompt,
        "test_code": provided_test_code,
        "test_weights": provided_test_weights,
        "checkers_code": checkers_code,
        "conftest_code": conftest_code,
        "persona": str(raw.get("persona") or "marcus"),
        # Hybrid yaml+native persona tasks (IAN, ALDEN) ship a persona/ dir on
        # disk; point at it so the workspace_before snapshot captures the
        # persona files instead of an empty dir (IAN report Pointer 4).
        "persona_dir": str(task_dir / "persona") if (task_dir / "persona").is_dir() else "",
        "system_prompt": str(raw.get("system_prompt") or ""),
        "task_description": str(raw.get("task_description") or ""),
        "rubrics": rubrics or [],
        "automated_checks": str(raw.get("automated_checks") or ""),
        "difficulty": str(raw.get("difficulty") or "medium"),
        "l1": str(raw.get("l1") or raw.get("taxonomy_l1") or ""),
        "l2": str(raw.get("l2") or raw.get("taxonomy_l2") or ""),
        "task_type": str(raw.get("task_type") or ""),
        "timeout_seconds": int(raw.get("timeout_seconds") or 1800),
        "category": str(raw.get("category") or ""),
        "file_path": str(path),
        "task_dir": str(task_dir),
        "gt_dir": str(task_dir / "gt") if (task_dir / "gt").is_dir() else "",
        "attachments": attachments,
        "workspace_path": "",
        "skills": str(raw.get("skills") or ""),
        "skills_path": "",
        "warmup": str(raw.get("warmup") or ""),
        "env": str(raw.get("env") or ""),
        # Explicit API declarations (new task.yaml format). Kept raw here; the
        # mock-stack augmenter normalizes them to <name>-api dir names and uses
        # them in preference to inference so only the task's APIs spin up.
        "required_apis_declared": raw.get("required_apis"),
        "distractor_apis_declared": raw.get("distractor_apis"),
        # 1-indexed turn numbers the author marked as needing sub-agent fan-out.
        # _attach_drift_script turns this into the multi_agent_config / scoring
        # checkers — no "Multi-Agent" token in the prompts.txt header required.
        "multi_agent_complex_turns": raw.get("multi_agent_complex_turns"),
        # Per-turn wake-up script (Talos inject-format) loaded from prompts.txt;
        # empty for single-prompt tasks. run_batch feeds these to the multi-turn
        # runner / inject director.
        "turn_messages": turn_messages,
        # Reference golden trajectory (optional). Flows to the Harbor bundle's
        # golden_trajectory.json via write_bundle.
        "golden_trajectory": _load_golden_trajectory(task_dir),
        "format": "yaml",
    }


def _load_attachments_yaml(raw: dict, task_dir: Path) -> list[dict]:
    att_dir = task_dir / "attachments"
    declared: list = raw.get("attachments") or []
    attachments: list[dict] = []
    for spec in declared:
        if isinstance(spec, str):
            spec = {"path": spec}
        p = Path(spec["path"])
        if not p.is_absolute():
            p = (att_dir / spec["path"]).resolve()
        if not p.is_file():
            continue
        mime = spec.get("mimeType") or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        attachments.append({
            "name": spec.get("name") or p.name,
            "mimeType": mime,
            "path": str(p),
            "size": p.stat().st_size,
            "storedAs": p.name,
                "role": spec.get("role") or "primary",
            "description": spec.get("description") or "",
        })
    if not declared and att_dir.is_dir():
        for f in sorted(att_dir.iterdir()):
            if not f.is_file():
                continue
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            attachments.append({
                "name": f.name,
                "mimeType": mime,
                "path": str(f),
                "size": f.stat().st_size,
                "storedAs": f.name,
                "role": "primary",
                "description": "",
            })
    return attachments
