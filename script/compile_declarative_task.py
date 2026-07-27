#!/usr/bin/env python3
"""Compile a declarative task source into a native WildClawBench task bundle.

Authors write four data files (no Python) in declarative/<task_id>/:

    metadata.json   task identity: difficulty, modalities, l1, l2, task_type,
                    required_apis, distractor_apis; optional "banner" dict for
                    the prompts.txt comment header
    prompt.txt      the TURN T0 user message, verbatim
    stages.toml     [[stages]] blocks — first block is the pre-T0 world seed
                    (no "turn"), each later block carries the next turn's user
                    message plus optional silent/loud/filesystem mutations
    rubrics.json    {"judge": [...]} LLM-judge criteria and {"checks": [...]}
                    declarative deterministic checks (compiled to pytest)

plus passthrough directories copied verbatim: persona/, data/, mock_data/,
gt/. assets/ is NOT copied as a directory — each file a filesystem mutation
references via task-relative src is copied into that mutation's stage dir.

The compiler emits a complete native bundle: task.yaml, prompts.txt,
rubric.json, test_outputs.py + test_weights.json + pytest.json, and
inject/stageN/{mutations.json, verify.sh}. Turn numbering,
applies_between_turns, and fires_at_turn are generated positionally, so the
classic "stage never fires because applies_between_turns is missing" bug
cannot be authored. Validate the result with script/preflight_task.py.

Usage:
    python3 script/compile_declarative_task.py declarative/<task_id> \
        [-o OUT_DIR] [--force]

OUT_DIR defaults to declarative/_build/<task_id>. The harness itself is not
modified: compiled bundles are ordinary native tasks.
"""
from __future__ import annotations

import argparse
import json
import py_compile
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV = REPO / "environment"

ALLOWED_WEIGHTS = {5, 3, 1, -1, -3, -5}
REQUIRED_METADATA = ("difficulty", "modalities", "l1", "l2", "task_type", "required_apis")

CHECK_TYPES = {
    "any_of", "all_of", "file_exists", "file_contains", "file_valid_json",
    "audit_request", "api_get",
}
CONDITION_OPS = {"equals", "equals_any", "not_in", "contains_any", "contains_all", "regex_any"}

PASSTHROUGH_DIRS = ("persona", "data", "mock_data", "gt")


class CompileError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"compile error: {msg}")


# --------------------------------------------------------------------------- #
# Loading + validation
# --------------------------------------------------------------------------- #
def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CompileError(f"{path} missing")
    except json.JSONDecodeError as exc:
        raise CompileError(f"{path}: invalid JSON: {exc}")


def _load_toml(path: Path):
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CompileError(f"{path} missing")
    except tomllib.TOMLDecodeError as exc:
        raise CompileError(f"{path}: invalid TOML: {exc}")


def _validate_condition(cond: dict, where: str) -> None:
    if "any" in cond:
        for sub in cond["any"]:
            _validate_condition(sub, where)
        return
    if "field" not in cond:
        raise CompileError(f"{where}: condition missing 'field': {cond}")
    if not (CONDITION_OPS & cond.keys()):
        raise CompileError(f"{where}: condition on {cond['field']!r} has no operator "
                           f"(one of {sorted(CONDITION_OPS)})")


def _validate_check(spec: dict, where: str) -> None:
    ctype = spec.get("type")
    if ctype not in CHECK_TYPES:
        raise CompileError(f"{where}: unknown check type {ctype!r} (allowed: {sorted(CHECK_TYPES)})")
    if ctype in ("any_of", "all_of"):
        subs = spec.get("checks") or []
        if not subs:
            raise CompileError(f"{where}: {ctype} needs non-empty 'checks'")
        for sub in subs:
            _validate_check(sub, where)
        return
    if ctype in ("file_exists", "file_contains", "file_valid_json") and not spec.get("path"):
        raise CompileError(f"{where}: {ctype} needs 'path'")
    if ctype == "file_contains" and not (spec.get("contains_any") or spec.get("regex_any")):
        raise CompileError(f"{where}: file_contains needs contains_any or regex_any")
    if ctype in ("audit_request", "api_get"):
        if not spec.get("service"):
            raise CompileError(f"{where}: {ctype} needs 'service'")
        for cond in spec.get("where", []):
            _validate_condition(cond, where)
    if ctype == "api_get" and not spec.get("path"):
        raise CompileError(f"{where}: api_get needs 'path'")


def load_source(src: Path) -> dict:
    metadata = _load_json(src / "metadata.json")
    for key in REQUIRED_METADATA:
        if key not in metadata:
            raise CompileError(f"metadata.json missing required key {key!r}")

    prompt_path = src / "prompt.txt"
    if not prompt_path.is_file():
        raise CompileError("prompt.txt missing")
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise CompileError("prompt.txt is empty")

    stages = _load_toml(src / "stages.toml").get("stages")
    if not isinstance(stages, list) or not stages:
        raise CompileError("stages.toml needs at least one [[stages]] block (the seed)")
    if "turn" in stages[0]:
        raise CompileError("first [[stages]] block is the pre-T0 seed and must not have 'turn'")
    for i, stage in enumerate(stages[1:], start=1):
        if not str(stage.get("turn", "")).strip():
            raise CompileError(f"[[stages]] block {i} (non-seed) needs a 'turn' message")

    rubrics = _load_json(src / "rubrics.json")
    judge = rubrics.get("judge") or []
    checks = rubrics.get("checks") or []
    if not judge:
        raise CompileError("rubrics.json needs a non-empty 'judge' list (rubric.json is required by the harness)")
    for i, entry in enumerate(judge):
        if not str(entry.get("criterion", "")).strip():
            raise CompileError(f"judge[{i}] missing 'criterion'")
        if entry.get("weight") not in ALLOWED_WEIGHTS:
            raise CompileError(f"judge[{i}] weight {entry.get('weight')!r} not in {sorted(ALLOWED_WEIGHTS)}")
    seen_ids: set[str] = set()
    for i, entry in enumerate(checks):
        cid = entry.get("id")
        if not cid or not re.fullmatch(r"[a-z0-9_]+", cid):
            raise CompileError(f"checks[{i}] needs a snake_case 'id'")
        if cid in seen_ids:
            raise CompileError(f"duplicate check id {cid!r}")
        seen_ids.add(cid)
        if entry.get("weight") not in ALLOWED_WEIGHTS:
            raise CompileError(f"checks[{cid}] weight {entry.get('weight')!r} not in {sorted(ALLOWED_WEIGHTS)}")
        if not isinstance(entry.get("check"), dict):
            raise CompileError(f"checks[{cid}] missing 'check' object")
        _validate_check(entry["check"], f"checks[{cid}]")

    return {"metadata": metadata, "prompt": prompt, "stages": stages,
            "judge": judge, "checks": checks}


# --------------------------------------------------------------------------- #
# Emitters
# --------------------------------------------------------------------------- #
def emit_task_yaml(meta: dict) -> str:
    def yaml_list(values):
        return "[" + ", ".join(str(v) for v in values) + "]"

    distractor = meta.get("distractor_apis", "auto")
    distractor_s = distractor if isinstance(distractor, str) else yaml_list(distractor)
    lines = [
        f"difficulty: {meta['difficulty']}",
        f"modalities: {yaml_list(meta['modalities'])}",
        f"l1: {meta['l1']}",
        f"l2: {meta['l2']}",
        f"task_type: {meta['task_type']}",
        f"required_apis: {yaml_list(meta['required_apis'])}",
        f"distractor_apis: {distractor_s}",
        # metadata-only field; preflight requires the key to exist
        f"system_prompt: {json.dumps(meta.get('system_prompt', ''))}",
    ]
    return "\n".join(lines) + "\n"


def emit_prompts_txt(meta: dict, prompt: str, stages: list[dict]) -> str:
    out = []
    banner = dict(meta.get("banner") or {})
    banner.setdefault("task_id", meta.get("task_id", ""))
    banner.setdefault("Source", "compiled from declarative source by script/compile_declarative_task.py")
    for key, value in banner.items():
        if str(value).strip():
            out.append(f"# {key}: {value}")
    if out:
        out.append("")

    def header(i, stage):
        label = str(stage.get("turn_label", "")).strip()
        return f"--- TURN T{i} ({label}) ---" if label else f"--- TURN T{i} ---"

    out.append(header(0, stages[0]))  # seed block may carry turn_label for T0's header
    out.append(prompt)
    for i, stage in enumerate(stages[1:], start=1):
        out.append("")
        out.append(header(i, stage))
        out.append(str(stage["turn"]).strip())
    return "\n".join(out) + "\n"


def emit_rubric_json(judge: list[dict]) -> str:
    rubrics = []
    for i, entry in enumerate(judge, start=1):
        w = entry["weight"]
        rubrics.append({
            "number": f"R{i}",
            "criterion": entry["criterion"],
            "is_positive": w > 0,
            "type": entry.get("type", "task completion"),
            "evaluation_target": entry.get("evaluation_target", "final_answer"),
            "importance": entry.get("importance") or ("critically_important" if abs(w) == 5 else "important"),
            "score": w,
        })
    return json.dumps({"rubrics": rubrics}, indent=1) + "\n"


def _service_url_default(service: str) -> str:
    """Resolve the mock's default localhost URL from environment/<svc>-api/service.toml."""
    api_dir = ENV / (service if service.endswith("-api") else f"{service}-api")
    toml_path = api_dir / "service.toml"
    if toml_path.is_file():
        try:
            port = tomllib.loads(toml_path.read_text(encoding="utf-8"))["service"]["port"]
            return f"http://localhost:{port}"
        except Exception:
            pass
    return "http://localhost:8000"


def _services_in_checks(checks: list[dict]) -> set[str]:
    found: set[str] = set()

    def walk(spec: dict):
        if spec.get("service"):
            found.add(spec["service"])
        for sub in spec.get("checks", []):
            walk(sub)

    for entry in checks:
        walk(entry["check"])
    return found


_TEST_RUNTIME = '''"""Deterministic graders — GENERATED by script/compile_declarative_task.py.
Do not edit by hand; edit the task's rubrics.json and recompile.
Each test evaluates one declarative check from CHECKS against the mock-API
audit feed and the solver workspace. Hermetic: stdlib only."""
import json, os, re, urllib.request

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

{service_urls}

def _request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={{"Content-Type": "application/json", "Accept": "application/json"}},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode())


def api_get(base, path):
    return _request(base.rstrip("/") + "/" + path.lstrip("/"))


_WS_ROOTS = [
    os.environ.get("WORKSPACE_ROOT", ""),
    os.environ.get("AGENT_WORKSPACE", ""),
    "/tmp_workspace",
    "/root/workspace",
    os.path.expanduser("~/workspace"),
]


def _ws_base():
    for r in _WS_ROOTS:
        if r and os.path.isdir(r):
            return r
    return "/tmp_workspace"


def _resolve(path):
    if os.path.exists(path):
        return path
    rel = path
    for r in ("/root/workspace", "/tmp_workspace", os.path.expanduser("~/workspace")):
        if path.startswith(r + "/"):
            rel = path[len(r) + 1:]
            break
    cand = os.path.join(_ws_base(), rel.lstrip("/"))
    return cand if os.path.exists(cand) else path


def _read_file(p):
    return open(_resolve(p), encoding="utf-8", errors="replace").read()


_WS = re.compile(r"\\s+")


def _norm(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value)
    return _WS.sub(" ", value.lower().strip())


def _match_condition(row, cond):
    if "any" in cond:
        return any(_match_condition(row, sub) for sub in cond["any"])
    value = _norm(row.get(cond["field"]))
    results = []
    if "equals" in cond:
        results.append(value == _norm(cond["equals"]))
    if "equals_any" in cond:
        results.append(value in {{_norm(v) for v in cond["equals_any"]}})
    if "not_in" in cond:
        results.append(value not in {{_norm(v) for v in cond["not_in"]}})
    if "contains_any" in cond:
        results.append(any(_norm(k) in value for k in cond["contains_any"]))
    if "contains_all" in cond:
        results.append(all(_norm(k) in value for k in cond["contains_all"]))
    if "regex_any" in cond:
        # values are lowercased by _norm; IGNORECASE keeps authored patterns honest
        results.append(any(re.search(p, value, re.IGNORECASE) for p in cond["regex_any"]))
    return all(results)


def _matching(rows, where):
    return [r for r in rows if all(_match_condition(r, c) for c in where)]


def _service_url(service):
    return SERVICE_URLS[service if service.endswith("-api") else service + "-api"]


def _eval_check(spec):
    ctype = spec["type"]
    if ctype == "any_of":
        return any(_eval_check(sub) for sub in spec["checks"])
    if ctype == "all_of":
        return all(_eval_check(sub) for sub in spec["checks"])
    if ctype == "file_exists":
        return os.path.exists(_resolve(spec["path"]))
    if ctype == "file_valid_json":
        try:
            json.loads(_read_file(spec["path"]))
            return True
        except Exception:
            return False
    if ctype == "file_contains":
        try:
            content = _norm(_read_file(spec["path"]))
        except OSError:
            return False
        if any(_norm(k) in content for k in spec.get("contains_any", [])):
            return True
        return any(re.search(p, content, re.IGNORECASE) for p in spec.get("regex_any", []))
    if ctype == "audit_request":
        audit = api_get(_service_url(spec["service"]), "/audit/requests")
        hits = _matching(audit.get("requests", []), spec.get("where", []))
        return len(hits) >= spec.get("min_count", 1)
    if ctype == "api_get":
        resp = api_get(_service_url(spec["service"]), spec["path"])
        rows = resp
        if isinstance(rows, dict):
            key = spec.get("result_key")
            if key == ".":
                rows = [rows]  # the response object itself is the single row
            else:
                rows = rows.get(key) if key else next(
                    (v for v in rows.values() if isinstance(v, list)), [rows])
        if not isinstance(rows, list):
            rows = [rows]
        hits = _matching(rows, spec.get("where", []))
        return len(hits) >= spec.get("min_count", 1)
    raise ValueError("unknown check type: %r" % ctype)


CHECKS = json.loads(r\'\'\'
{checks_json}
\'\'\')

'''


def emit_tests(checks: list[dict]) -> tuple[str, str, str]:
    """Return (test_outputs.py, test_weights.json, pytest.json) contents."""
    services = _services_in_checks(checks)
    url_lines = []
    for svc in sorted(services):
        key = svc if svc.endswith("-api") else f"{svc}-api"
        env_var = key.upper().replace("-API", "_API_URL").replace("-", "_")
        url_lines.append(
            f'{env_var} = os.environ.get("{env_var}", "{_service_url_default(svc)}")'
        )
    url_lines.append("SERVICE_URLS = {")
    for svc in sorted(services):
        key = svc if svc.endswith("-api") else f"{svc}-api"
        env_var = key.upper().replace("-API", "_API_URL").replace("-", "_")
        url_lines.append(f'    "{key}": {env_var},')
    url_lines.append("}")

    checks_map = {c["id"]: c["check"] for c in checks}
    body = _TEST_RUNTIME.format(
        service_urls="\n".join(url_lines),
        checks_json=json.dumps(checks_map, indent=1),
    )

    fn_lines = []
    for entry in checks:
        cid = entry["id"]
        desc = str(entry.get("description", "")).strip()
        fn_lines.append(f"def test_{cid}():")
        if desc:
            fn_lines.append(f"    {json.dumps(desc)}")
        fn_lines.append(f'    assert _eval_check(CHECKS["{cid}"])')
        fn_lines.append("")
        fn_lines.append("")
    test_py = body + "\n".join(fn_lines)

    weights = {f"test_{c['id']}": c["weight"] for c in checks}
    weights_json = json.dumps(weights, indent=2) + "\n"

    pytest_doc = [{
        "number": f"P{i}",
        "criterion": entry.get("description", "") or entry["id"],
        "is_positive": entry["weight"] > 0,
        "score": entry["weight"],
        "test": f"test_{entry['id']}",
    } for i, entry in enumerate(checks, start=1)]
    return test_py, weights_json, json.dumps({"checks": pytest_doc}, indent=1) + "\n"


def emit_inject(stages: list[dict], src_dir: Path, out_dir: Path) -> int:
    """Write inject/stageN dirs. Returns number of stage dirs emitted."""
    emitted = 0
    for i, stage in enumerate(stages):
        buckets = {b: list(stage.get(b) or []) for b in ("filesystem", "loud", "silent")}
        nops = sum(len(v) for v in buckets.values())
        if nops == 0 and i > 0:
            continue  # turn-only stage: contributes to prompts.txt, no inject dir
        stage_dir = out_dir / "inject" / f"stage{emitted}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        is_seed = i == 0
        applies = [None, "T0"] if is_seed else [f"T{i - 1}", f"T{i}"]
        fires = "T0" if is_seed else f"T{i}"

        for bucket, ops in buckets.items():
            for k, op in enumerate(ops):
                op.setdefault("id", f"s{emitted}-{bucket}-{k}")
                op.setdefault("fires_at_turn", fires)
                svc = op.get("service")
                if svc and not svc.endswith("-api") and (ENV / f"{svc}-api").is_dir():
                    # runtime admin-URL map and preflight both key services by
                    # their service.toml name (e.g. "gmail-api", not "gmail")
                    op["service"] = f"{svc}-api"
                if bucket == "filesystem":
                    op.setdefault("action", "copy")
                    src_rel = op.get("src")
                    if not src_rel:
                        raise CompileError(f"stage {i} filesystem op {op['id']} needs 'src'")
                    src_file = src_dir / src_rel
                    if not src_file.is_file():
                        raise CompileError(f"stage {i} filesystem op {op['id']}: src not found: {src_rel}")
                    shutil.copy2(src_file, stage_dir / src_file.name)
                    op["src"] = src_file.name
                    if not str(op.get("dst", "")).startswith("/"):
                        raise CompileError(f"stage {i} filesystem op {op['id']}: dst must be absolute")

        doc = {
            "stage": emitted,
            "stage_name": stage.get("name", "seed" if is_seed else f"stage{emitted}"),
            "description": stage.get("description", ""),
            "applies_between_turns": applies,
            "applied_at_local_time": stage.get("applied_at", ""),
            "mutations": buckets,
        }
        (stage_dir / "mutations.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        verify = stage.get("verify", "")
        (stage_dir / "verify.sh").write_text(
            verify.rstrip() + "\n" if verify.strip()
            else f'#!/bin/sh\n# stage{emitted} ({doc["stage_name"]}): no scripted verification; '
                 f'inspect inject_timeline.jsonl\nexit 0\n',
            encoding="utf-8")
        emitted += 1
    return emitted


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def compile_task(src_dir: Path, out_dir: Path, force: bool) -> None:
    source = load_source(src_dir)
    meta = source["metadata"]
    meta.setdefault("task_id", src_dir.name)

    if out_dir.exists():
        if not force:
            raise CompileError(f"output dir exists: {out_dir} (use --force to overwrite)")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    (out_dir / "task.yaml").write_text(emit_task_yaml(meta), encoding="utf-8")
    prompts = emit_prompts_txt(meta, source["prompt"], source["stages"])
    (out_dir / "prompts.txt").write_text(prompts, encoding="utf-8")
    (out_dir / "prompt.txt").write_text(source["prompt"] + "\n", encoding="utf-8")
    (out_dir / "rubric.json").write_text(emit_rubric_json(source["judge"]), encoding="utf-8")

    test_py, weights_json, pytest_json = emit_tests(source["checks"])
    (out_dir / "test_outputs.py").write_text(test_py, encoding="utf-8")
    (out_dir / "test_output.py").write_text(test_py, encoding="utf-8")  # loader accepts either name
    (out_dir / "test_weights.json").write_text(weights_json, encoding="utf-8")
    (out_dir / "pytest.json").write_text(pytest_json, encoding="utf-8")

    n_stages = emit_inject(source["stages"], src_dir, out_dir)

    for name in PASSTHROUGH_DIRS:
        src_sub = src_dir / name
        if src_sub.is_dir():
            shutil.copytree(src_sub, out_dir / name)

    # Self-check: generated python must compile
    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as tmp:
        py_compile.compile(str(out_dir / "test_outputs.py"), cfile=tmp.name, doraise=True)

    n_turns = len(re.findall(r"^--- TURN T\d+", prompts, re.MULTILINE))
    print(f"compiled {meta['task_id']}: {n_turns} turn(s), {n_stages} inject stage(s), "
          f"{len(source['judge'])} judge criteria, {len(source['checks'])} deterministic checks")
    print(f"  -> {out_dir}")
    print(f"validate: python3 script/preflight_task.py {out_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="declarative task source dir (metadata.json, prompt.txt, stages.toml, rubrics.json)")
    ap.add_argument("-o", "--out", help="output bundle dir (default: declarative/_build/<task_id>)")
    ap.add_argument("--force", action="store_true", help="overwrite output dir")
    args = ap.parse_args()

    src_dir = Path(args.source).resolve()
    if not src_dir.is_dir():
        print(f"source dir not found: {src_dir}", file=sys.stderr)
        return 2
    out_dir = Path(args.out).resolve() if args.out else REPO / "declarative" / "_build" / src_dir.name
    compile_task(src_dir, out_dir, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
