#!/usr/bin/env python3
"""Offline preflight validator for a WildClawBench task bundle.

Runs a battery of checks that need NO LLM and NO Docker, so you can confirm a
task will load, seed, inject and grade before spending a real run. Exercises the
SAME loaders the harness uses (environment/_mutable_store data modules,
inject_director.InjectScript) plus structural/cross-reference checks.

Usage:
    python3 script/preflight_task.py "input/IAN_001 -- Bhavik Jain"
    python3 script/preflight_task.py            # defaults to IAN_001

Exit code 0 when there are no FAILs (WARNs are allowed), 1 otherwise.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV = REPO / "environment"
DEFAULT_TASK = REPO / "input" / "IAN_001 -- Bhavik Jain"

# OpenClaw native tools that can appear as a loud-inject `service` but are NOT
# mock HTTP APIs (they deliver in-band to the agent, so they have no env folder).
NATIVE_SERVICES = {"message", "cron", "nodes", "canvas", "gateway", "image",
                   "sessions_send", "subagents", "agents_list"}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ICON = {PASS: "\033[32m✔\033[0m", WARN: "\033[33m⚠\033[0m", FAIL: "\033[31m✘\033[0m"}
_counts = {PASS: 0, WARN: 0, FAIL: 0}
_section = ""


def section(title: str) -> None:
    global _section
    _section = title
    print(f"\n=== {title} ===")


def rec(status: str, msg: str) -> None:
    _counts[status] += 1
    print(f"  {_ICON[status]} {msg}")


def _turn_num(t) -> int | None:
    if t is None:
        return None
    m = re.match(r"[Tt]?(\d+)", str(t))
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# 1. Bundle structure
# --------------------------------------------------------------------------- #
def check_structure(task: Path) -> None:
    section("1. Bundle structure")
    expected = ["data", "persona", "inject", "mock_data", "prompts.txt",
                "rubric.json", "task.yaml", "test_outputs.py", "test_weights.json"]
    has_json = (task / "prompts.json").is_file()
    for name in expected:
        present = (task / name).exists()
        if name == "prompts.txt" and has_json:
            # Finalized pair convention: prompts.json (trajectory) MUST ship
            # with a companion prompts.txt (published in the output bundle).
            rec(PASS if present else FAIL,
                "prompts.json + companion prompts.txt present" if present
                else "prompts.json present but companion prompts.txt MISSING "
                     "(required — the txt is what the client receives)")
            continue
        rec(PASS if present else FAIL,
            f"{name} present" if present else f"{name} MISSING")
    persona = task / "persona"
    if persona.is_dir():
        need = {"AGENTS.md", "HEARTBEAT.md", "IDENTITY.md", "MEMORY.md", "SOUL.md", "TOOLS.md", "USER.md"}
        have = {p.name for p in persona.iterdir()}
        missing = need - have
        rec(PASS if not missing else FAIL,
            "persona/ has all 7 core files" if not missing else f"persona/ missing {sorted(missing)}")
    data = task / "data"
    # corpus convention stages inputs under data/home/**, so count recursively
    n = len([p for p in data.rglob("*") if p.is_file()]) if data.is_dir() else 0
    rec(PASS if n else FAIL, f"data/ has {n} files")


# --------------------------------------------------------------------------- #
# 2. task.yaml + API ↔ environment
# --------------------------------------------------------------------------- #
def _parse_api_lists(text: str) -> tuple[list[str], list[str]]:
    def grab(key):
        m = re.search(rf"^{key}:\s*\[(.*?)\]", text, re.MULTILINE)
        if not m:
            return []
        return [x.strip() for x in m.group(1).split(",") if x.strip()]
    return grab("required_apis"), grab("distractor_apis")


def check_task_yaml(task: Path) -> tuple[list[str], list[str]]:
    section("2. task.yaml + API ↔ environment")
    y = task / "task.yaml"
    if not y.is_file():
        rec(FAIL, "task.yaml missing")
        return [], []
    text = y.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text)
        rec(PASS, "task.yaml parses as YAML")
        required = doc.get("required_apis") or []
        distractor = doc.get("distractor_apis") or []
    except Exception as exc:  # noqa: BLE001
        rec(WARN, f"PyYAML unavailable/parse issue ({exc}); falling back to regex")
        required, distractor = _parse_api_lists(text)
    for key in ("task_type", "system_prompt"):
        rec(PASS if re.search(rf"^{key}:", text, re.MULTILINE) else FAIL, f"task.yaml has {key}")
    if isinstance(distractor, str):
        # 'auto' is the documented full-catalog sentinel, not an API list
        distractor = [] if distractor.strip().lower() in ("auto", "__auto__") else [distractor]
    rec(PASS if required else FAIL, f"required_apis: {required}")
    rec(PASS, f"distractor_apis: {distractor}")
    for api in list(required) + list(distractor):
        d = ENV / f"{api}-api"
        rec(PASS if d.is_dir() else FAIL,
            f"environment/{api}-api present" if d.is_dir() else f"environment/{api}-api MISSING")
    _check_modality_assets(task, text)
    return list(required), list(distractor)


# MIME prefix that must be present for a declared non-text modality to be real.
_MODALITY_MIME_PREFIX = {
    "image": "image/",
    "audio": "audio/",
    "video": "video/",
}
_TEXT_MODALITIES = {"text", "txt", "plain"}


def _bundle_mimes(task: Path) -> set[str]:
    """Guessed MIME types of every file in the bundle (by extension)."""
    out: set[str] = set()
    for p in task.rglob("*"):
        if p.is_file():
            mt, _ = mimetypes.guess_type(p.name)
            if mt:
                out.add(mt)
    return out


def _check_modality_assets(task: Path, task_yaml_text: str) -> None:
    """Assert every declared non-text modality is backed by a real asset.

    A bundle that declares ``modalities: [text, image]`` while shipping only
    .txt placeholders still gets flagged multimodal downstream (harbor
    task.toml `[dimensions] multimodal`), so the false claim reaches delivery.
    Nothing else in the harness cross-checks the declaration against content.
    """
    m = re.search(r"^modalities:\s*\[(.*?)\]", task_yaml_text, re.MULTILINE)
    if not m:
        return
    declared = [s.strip().strip("'\"").lower() for s in m.group(1).split(",") if s.strip()]
    non_text = [d for d in declared if d and d not in _TEXT_MODALITIES]
    if not non_text:
        return
    mimes = _bundle_mimes(task)
    for mod in non_text:
        prefix = _MODALITY_MIME_PREFIX.get(mod)
        if prefix is None:
            rec(WARN, f"task.yaml declares unknown modality {mod!r}")
            continue
        hit = sorted(mt for mt in mimes if mt.startswith(prefix))
        rec(PASS if hit else FAIL,
            f"modality {mod!r} backed by {hit[:3]}" if hit else
            f"task.yaml declares modality {mod!r} but NO {prefix}* file exists in "
            f"the bundle — delivery will still mark it multimodal")


# --------------------------------------------------------------------------- #
# 3. mock_data schema match + live boot through _mutable_store
# --------------------------------------------------------------------------- #
def _csv_header(p: Path) -> list[str]:
    with open(p, newline="", encoding="utf-8") as f:
        return next(csv.reader(f), [])


def check_mock_data(task: Path) -> None:
    section("3. mock_data schema + boot (real _mutable_store loaders)")
    md = task / "mock_data"
    if not md.is_dir():
        rec(FAIL, "mock_data/ missing")
        return
    for apidir in sorted(p for p in md.iterdir() if p.is_dir()):
        api = apidir.name
        envdir = ENV / api
        if not envdir.is_dir():
            rec(FAIL, f"{api}: no environment/{api} folder")
            continue
        # schema + integrity
        bad = []
        for f in sorted(apidir.iterdir()):
            if f.suffix == ".csv":
                rows = list(csv.reader(open(f, newline="", encoding="utf-8")))
                ncol = len(rows[0]) if rows else 0
                ragged = [i for i, r in enumerate(rows) if len(r) != ncol]
                ef = envdir / f.name
                hdr_ok = (not ef.exists()) or (rows and rows[0] == _csv_header(ef))
                if ragged or not hdr_ok:
                    bad.append(f"{f.name}(hdr={'ok' if hdr_ok else 'MISMATCH'},ragged={ragged[:3]})")
            elif f.suffix == ".json":
                try:
                    json.load(open(f, encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{f.name}(bad json: {exc})")
        if bad:
            rec(FAIL, f"{api}: schema/integrity issues -> {bad}")
            continue
        # live boot: copy env folder, overlay task files, import data module
        boot_err = _boot_api(api, apidir)
        if boot_err is None:
            rec(PASS, f"{api}: schema OK + server boots")
        else:
            rec(FAIL, f"{api}: boot FAILED -> {boot_err}")


def _boot_api(api: str, overlay: Path) -> str | None:
    tmp = tempfile.mkdtemp()
    try:
        shutil.copytree(ENV / api, f"{tmp}/{api}")
        shutil.copy2(ENV / "_mutable_store.py", f"{tmp}/_mutable_store.py")
        for fn in os.listdir(overlay):
            shutil.copy2(overlay / fn, f"{tmp}/{api}/{fn}")
        dm = [f for f in os.listdir(f"{tmp}/{api}") if f.endswith("_data.py")]
        if not dm:
            return None  # no data module → nothing to boot (static-only api)
        sys.path.insert(0, tmp)
        spec = importlib.util.spec_from_file_location(f"_pf_{api}", f"{tmp}/{api}/{dm[0]}")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        store = m._store
        for t in list(store._tables):
            store.table(t).rows()
        for d in list(store._documents):
            store.document(d).get()
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    finally:
        if tmp in sys.path:
            sys.path.remove(tmp)
        for k in [k for k in sys.modules if k.startswith("_pf_")]:
            del sys.modules[k]
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 4. inject pipeline (parse + file-source + service + timing invariants)
# --------------------------------------------------------------------------- #
def check_inject(task: Path, required: list[str], distractor: list[str]) -> None:
    section("4. inject pipeline (InjectScript + file/service/timing checks)")
    inj = task / "inject"
    if not inj.is_dir():
        rec(FAIL, "inject/ missing")
        return
    sys.path.insert(0, str(REPO))
    try:
        from src.utils.inject_director import InjectScript
    except Exception as exc:  # noqa: BLE001
        rec(FAIL, f"cannot import InjectScript: {exc}")
        return
    try:
        script = InjectScript.load(inj)
        rec(PASS, f"InjectScript.load OK — {len(script.stages)} stage(s)")
    except Exception as exc:  # noqa: BLE001
        rec(FAIL, f"InjectScript.load FAILED: {exc}")
        return

    seed = [s for s in script.stages if s.is_seed]
    rec(PASS if seed else WARN, f"seed stage present (stage0)" if seed else "no seed stage (from_turn=None)")

    known_apis = {f"{a}-api" for a in (required + distractor)} | {p.name for p in ENV.iterdir() if p.is_dir()}
    boundaries = sorted(s.to_turn for s in script.stages if not s.is_seed and s.to_turn is not None)
    TOTAL = 50

    for st in script.stages:
        sd = Path(st.source).parent
        label = f"stage{st.index}({st.name})"
        # next boundary for the fires_at invariant
        nb = next((b for b in boundaries if st.to_turn is not None and b > st.to_turn), TOTAL)
        nops = len(st.filesystem) + len(st.loud) + len(st.silent)
        rec(PASS if nops else WARN,
            f"{label}: {len(st.filesystem)} fs / {len(st.loud)} loud / {len(st.silent)} silent ops")
        # filesystem src resolution + dst sanity + timing
        for op in st.filesystem:
            src = op.get("src")
            if src:
                p = (sd / src)
                rec(PASS if p.is_file() else FAIL,
                    f"{label} fs[{op.get('id')}] src exists: {src}" if p.is_file()
                    else f"{label} fs[{op.get('id')}] src MISSING: {src}")
                _check_op_modality(label, op, p)
            dst = op.get("dst", "")
            if dst and not str(dst).startswith("/"):
                rec(WARN, f"{label} fs[{op.get('id')}] dst not absolute: {dst}")
            if st.is_seed:
                _check_seed_payload_mirrored(task, label, op)
            _check_fires(label, op, st, nb)
        # loud/silent: service known, raw_eml_path resolves, timing
        for bucket in ("loud", "silent"):
            for op in getattr(st, bucket):
                svc = op.get("service")
                if svc is not None:
                    if svc in known_apis:
                        rec(PASS, f"{label} {bucket}[{op.get('id')}] service={svc}")
                    elif svc in NATIVE_SERVICES:
                        rec(PASS, f"{label} {bucket}[{op.get('id')}] service={svc} (OpenClaw native tool, not a mock API)")
                    else:
                        rec(FAIL, f"{label} {bucket}[{op.get('id')}] UNKNOWN service={svc}")
                # Bare REST-form ops (no explicit admin block) rely on fuzzy
                # row resolution at apply time: it only scans TABLES (never
                # document stores) and passes unmatched fields through
                # verbatim — a write can land in the wrong storage object and
                # still report 200 (hotsauce, 2026-07-30 audit). Warn authors
                # toward the explicit admin encoding (docs/MULTITURN.md).
                if not st.is_seed and svc not in NATIVE_SERVICES \
                        and not isinstance(op.get("admin"), dict):
                    rec(WARN, f"{label} {bucket}[{op.get('id')}] bare REST form "
                              f"(no admin block) — fuzzy resolution may write to "
                              f"the wrong table/document; use an explicit admin op")
                raw = (op.get("body") or {}).get("raw_eml_path") if isinstance(op.get("body"), dict) else None
                if raw:
                    rec(PASS if (sd / raw).is_file() else FAIL,
                        f"{label} {bucket}[{op.get('id')}] raw_eml_path OK" if (sd / raw).is_file()
                        else f"{label} {bucket}[{op.get('id')}] raw_eml_path MISSING: {raw}")
                _check_fires(label, op, st, nb)

    # boundaries monotonic
    rec(PASS if boundaries == sorted(set(boundaries)) and len(boundaries) == len(set(boundaries)) else WARN,
        f"stage boundaries (to_turn): {boundaries}")
    # verify.sh present + non-empty per stage
    for st in script.stages:
        vs = Path(st.source).parent / "verify.sh"
        rec(PASS if vs.is_file() and vs.stat().st_size > 0 else WARN,
            f"stage{st.index}/verify.sh present" if vs.is_file() else f"stage{st.index}/verify.sh missing")


def _check_op_modality(label: str, op: dict, host_src: Path) -> None:
    """Assert a filesystem op's declared ``modality`` matches its payload MIME.

    The field is authored per the kit's M-3 schema but no runtime code reads it
    (copy_file_into_workspace does a plain docker cp), so an op could declare
    modality:image and ship a .txt with nothing complaining.
    """
    declared = str(op.get("modality") or "").strip().lower()
    if not declared or declared in _TEXT_MODALITIES:
        return
    prefix = _MODALITY_MIME_PREFIX.get(declared)
    if prefix is None:
        rec(WARN, f"{label} fs[{op.get('id')}] unknown modality {declared!r}")
        return
    if not host_src.is_file():
        return  # already reported by the src-exists check
    mt, _ = mimetypes.guess_type(host_src.name)
    rec(PASS if (mt or "").startswith(prefix) else FAIL,
        f"{label} fs[{op.get('id')}] modality={declared!r} matches {mt}"
        if (mt or "").startswith(prefix) else
        f"{label} fs[{op.get('id')}] declares modality={declared!r} but src is "
        f"{mt or 'unknown'}: {op.get('src')}")


def _seed_dst_to_data_rel(dst: str) -> str | None:
    """Map a seed op's container dst to its expected ``data/`` counterpart.

    ``data/`` is staged by copying its CONTENTS into ``{TMP_WORKSPACE}/home``,
    so ``/workspace/home/<rel>`` is mounted from ``data/<rel>``.
    """
    p = str(dst or "").strip()
    for prefix in ("/workspace/home/", "/app/home/"):
        if p.startswith(prefix):
            return p[len(prefix):]
    return None


def _check_seed_payload_mirrored(task: Path, label: str, op: dict) -> None:
    """Seed fs ops never execute — assert the payload is mounted via data/.

    The pre-T0 seed stage fires before the agent container exists, so the copy
    hook cannot run and the op is recorded "skipped_container_down" (benign,
    exempted from injection defects). That exemption is only sound when the
    payload also ships in data/; otherwise the file silently never appears and
    the run still reports injection_ok=true.
    """
    if op.get("action") != "copy":
        return
    rel = _seed_dst_to_data_rel(op.get("dst", ""))
    if rel is None:
        return
    mounted = task / "data" / rel
    rec(PASS if mounted.is_file() else FAIL,
        f"{label} fs[{op.get('id')}] payload mirrored in data/{rel}"
        if mounted.is_file() else
        f"{label} fs[{op.get('id')}] NOT mirrored in data/{rel} — seed ops do not "
        f"execute (container not up), so this payload will never appear")


def _check_fires(label: str, op: dict, st, next_boundary: int) -> None:
    if st.is_seed:
        return
    f = _turn_num(op.get("fires_at_turn"))
    if f is None:
        return
    lo = st.to_turn
    if lo is not None and not (lo <= f < next_boundary):
        rec(WARN, f"{label} op[{op.get('id')}] fires_at_turn T{f} outside [T{lo}, T{next_boundary}) "
                  f"(detection-vs-application invariant)")


# --------------------------------------------------------------------------- #
# 5. prompts / turns / rubric / weights / checkers
# --------------------------------------------------------------------------- #
def check_turns_and_grading(task: Path) -> None:
    section("5. prompts / rubric / weights / checkers")
    pj = task / "prompts.json"
    pt = task / "prompts.txt"
    turns = []
    if pj.is_file():
        # JSON turn schedule: parse_prompts_json enforces contiguous T0..TN and
        # turn_count agreement; report its verdict here instead of duplicating.
        try:
            if str(REPO) not in sys.path:
                sys.path.insert(0, str(REPO))
            from src.utils.inject_director import parse_prompts_json
            messages, _meta = parse_prompts_json(pj)
            rec(PASS, f"prompts.json has {len(messages)} turns (T0..T{len(messages) - 1})")
            rec(PASS, "turn labels contiguous from T0 (validated by parser)")
        except Exception as exc:  # noqa: BLE001
            rec(FAIL, f"prompts.json invalid: {exc}")
    elif pt.is_file():
        turns = [int(m.group(1)) for m in re.finditer(r"^---\s*TURN\s+T(\d+)", pt.read_text(encoding="utf-8"), re.MULTILINE)]
        contig = turns == list(range(len(turns)))
        rec(PASS if turns else FAIL, f"prompts.txt has {len(turns)} turns (T0..T{turns[-1] if turns else '?'})")
        rec(PASS if contig else WARN, "turn indices contiguous from T0" if contig else f"turn gaps: {turns}")
    else:
        rec(FAIL, "prompts.txt missing (no prompts.json either)")

    for fn in ("rubric.json", "test_weights.json"):
        p = task / fn
        try:
            data = json.load(open(p, encoding="utf-8"))
            rec(PASS, f"{fn} valid JSON ({len(data)} top-level entries)")
        except Exception as exc:  # noqa: BLE001
            rec(FAIL, f"{fn} invalid: {exc}")

    # test_outputs.py compiles, and can it reach CHECKERS?
    to = task / "test_outputs.py"
    if to.is_file():
        try:
            compile(to.read_text(encoding="utf-8"), str(to), "exec")
            rec(PASS, "test_outputs.py compiles")
        except SyntaxError as exc:
            rec(FAIL, f"test_outputs.py syntax error: {exc}")
        if "task/task.py" in to.read_text(encoding="utf-8") or "/ \"task\"" in to.read_text(encoding="utf-8"):
            has_taskpy = (task / "task" / "task.py").is_file()
            rec(PASS if has_taskpy else WARN,
                "test_outputs.py CHECKERS source task/task.py present" if has_taskpy
                else "test_outputs.py imports CHECKERS from task/task.py which is ABSENT "
                     "(grading cannot collect checkers until task.py is supplied)")
    else:
        rec(FAIL, "test_outputs.py missing")


# --------------------------------------------------------------------------- #
def main() -> int:
    task = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_TASK
    if not task.is_dir():
        print(f"task dir not found: {task}")
        return 2
    print(f"Preflight: {task.name}")
    check_structure(task)
    required, distractor = check_task_yaml(task)
    check_mock_data(task)
    check_inject(task, required, distractor)
    check_turns_and_grading(task)
    print("\n" + "=" * 60)
    print(f"SUMMARY: {_counts[PASS]} pass · {_counts[WARN]} warn · {_counts[FAIL]} fail")
    print("=" * 60)
    return 0 if _counts[FAIL] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
