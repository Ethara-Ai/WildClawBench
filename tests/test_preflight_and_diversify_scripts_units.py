"""Unit coverage for script/preflight_task.py.

preflight_task: the module's ENV global is monkeypatched to a synthetic
environment tree (fake APIs with tiny _data.py store modules), the inject
checker gets a fake src.utils.inject_director via sys.modules, and each check_*
section is driven by a purpose-built task fixture.
"""
from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "script"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_script(filename: str, mod_alias: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(mod_alias, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ======================================================================
# script/preflight_task.py
# ======================================================================


@pytest.fixture()
def pf(monkeypatch, tmp_path):
    mod = _load_script("preflight_task.py", "_t_preflight_task")
    env = tmp_path / "environment"
    env.mkdir()
    (env / "_mutable_store.py").write_text("# stub store lib\n", encoding="utf-8")
    # widget-api: boots cleanly through a tiny fake _store
    w = env / "widget-api"
    w.mkdir()
    (w / "items.csv").write_text("id,name\n", encoding="utf-8")
    (w / "widget_data.py").write_text(
        "class _T:\n"
        "    def rows(self): return []\n"
        "class _D:\n"
        "    def get(self): return {}\n"
        "class _S:\n"
        "    _tables = {'items': 1}\n"
        "    _documents = {'doc': 1}\n"
        "    def table(self, n): return _T()\n"
        "    def document(self, n): return _D()\n"
        "_store = _S()\n", encoding="utf-8")
    # static-api: no *_data.py -> nothing to boot
    (env / "static-api").mkdir()
    # badcsv-api / badjson-api: schema-check targets
    b = env / "badcsv-api"
    b.mkdir()
    (b / "rows.csv").write_text("x,y,z\n", encoding="utf-8")
    (env / "badjson-api").mkdir()
    # bootfail-api: data module that explodes on import
    bf = env / "bootfail-api"
    bf.mkdir()
    (bf / "bootfail_data.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ENV", env)
    monkeypatch.setattr(mod, "_counts", {"PASS": 0, "WARN": 0, "FAIL": 0})
    return mod


def _mandated_truth() -> str:
    """A TRUTH.md carrying whatever section skeleton the standard mandates."""
    from src.utils.task_standard import TRUTH_SECTIONS

    return "# TRUTH\n\n" + "".join(
        f"## {i}. {name}\n\nbody\n\n"
        for i, name in enumerate(TRUTH_SECTIONS, start=1))


def _mk_task(root: Path, *, full=True) -> Path:
    task = root / "TASK"
    task.mkdir(parents=True, exist_ok=True)
    if not full:
        return task
    (task / "data").mkdir()
    (task / "data" / "notes.md").write_text("n", encoding="utf-8")
    persona = task / "persona"
    persona.mkdir()
    for n in ("AGENTS.md", "HEARTBEAT.md", "IDENTITY.md", "MEMORY.md",
              "SOUL.md", "TOOLS.md", "USER.md"):
        (persona / n).write_text("p", encoding="utf-8")
    (task / "inject").mkdir()
    (task / "mock_data").mkdir()
    (task / "prompts.txt").write_text(
        "--- TURN T0\nhi\n--- TURN T1\nbye\n", encoding="utf-8")
    (task / "rubric.json").write_text(json.dumps([{"criterion": "a"}]), encoding="utf-8")
    (task / "task.yaml").write_text(
        "task_type: ops\nsystem_prompt: be helpful\n"
        "required_apis: [widget]\ndistractor_apis: []\n", encoding="utf-8")
    (task / "test_outputs.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    (task / "test_weights.json").write_text(json.dumps({"test_a": 1}), encoding="utf-8")
    return task


def test_pf_turn_num_and_parse_api_lists(pf):
    assert pf._turn_num(None) is None
    assert pf._turn_num("T7") == 7
    assert pf._turn_num("weird") is None
    req, dis = pf._parse_api_lists("required_apis: [a, b]\ndistractor_apis: []\n")
    assert req == ["a", "b"] and dis == []
    assert pf._parse_api_lists("nothing here") == ([], [])


def test_pf_check_structure_pass_and_fail(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    pf.check_structure(task)
    assert pf._counts["FAIL"] == 0
    # missing everything
    pf.check_structure(_mk_task(tmp_path / "empty", full=False))
    assert pf._counts["FAIL"] > 0
    # persona present but incomplete
    part = _mk_task(tmp_path / "part", full=False)
    (part / "persona").mkdir()
    (part / "persona" / "SOUL.md").write_text("s", encoding="utf-8")
    pf.check_structure(part)
    assert "persona/ missing" in capsys.readouterr().out


def test_the_generated_test_pair_is_a_note_when_neither_half_ships(pf, tmp_path,
                                                                   capsys):
    # The pair is opt-in: the parser reads it only whole and only when a run
    # asks for generated tests, and a rubric-only run is the documented default.
    task = _mk_task(tmp_path)
    for name in pf.TEST_PAIR:
        (task / name).unlink(missing_ok=True)
    pf.check_structure(task)
    assert pf._counts["FAIL"] == 0 and pf._counts["WARN"] >= 1
    assert "rubric-only" in capsys.readouterr().out


def test_half_the_generated_test_pair_is_still_a_failure(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    (task / "test_weights.json").unlink()
    pf.check_structure(task)
    assert pf._counts["FAIL"] >= 1
    assert "only ever read whole" in capsys.readouterr().out


def test_asking_for_generated_tests_makes_the_absent_pair_a_failure(pf, tmp_path,
                                                                    monkeypatch):
    task = _mk_task(tmp_path)
    for name in pf.TEST_PAIR:
        (task / name).unlink(missing_ok=True)
    monkeypatch.setattr(pf, "GENERATE_TESTS", True)
    pf.check_structure(task)
    assert pf._counts["FAIL"] >= 1


def test_the_task_yaml_key_check_says_which_way_it_went(pf, tmp_path, capsys):
    # The pass and fail branches printed the same sentence, so `✘ task.yaml has
    # system_prompt` read as the opposite of what it meant.
    task = _mk_task(tmp_path)
    (task / "task.yaml").write_text(
        "task_type: ops\nrequired_apis: [widget]\ndistractor_apis: []\n",
        encoding="utf-8")
    pf.check_task_yaml(task)
    out = capsys.readouterr().out
    assert "task.yaml has task_type" in out
    assert "task.yaml lacks system_prompt" in out
    assert "task.yaml has system_prompt" not in out


def test_an_absent_system_prompt_does_not_block_a_run(pf, tmp_path, capsys):
    # task_parser lists it among the tolerated metadata keys, defaults it to ""
    # and never feeds it to the agent; it is a packaging ask, not a blocker.
    task = _mk_task(tmp_path)
    (task / "task.yaml").write_text(
        "task_type: ops\nrequired_apis: [widget]\ndistractor_apis: []\n",
        encoding="utf-8")
    pf.check_task_yaml(task)
    assert pf._counts["FAIL"] == 0
    assert "Skoll" in capsys.readouterr().out


def test_an_absent_task_type_still_blocks(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    (task / "task.yaml").write_text(
        "system_prompt: be helpful\nrequired_apis: [widget]\ndistractor_apis: []\n",
        encoding="utf-8")
    pf.check_task_yaml(task)
    assert pf._counts["FAIL"] >= 1
    assert "task.yaml lacks task_type" in capsys.readouterr().out


def test_pf_check_task_yaml_variants(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    req, dis = pf.check_task_yaml(task)
    assert req == ["widget"] and dis == []
    # missing task.yaml
    assert pf.check_task_yaml(_mk_task(tmp_path / "e", full=False)) == ([], [])
    # unparseable YAML -> regex fallback WARN; unknown api -> env MISSING FAIL
    t2 = _mk_task(tmp_path / "y2", full=False)
    (t2 / "task.yaml").write_text(
        "required_apis: [widget, ghost]\ndistractor_apis: [dud]\n\t: {bad yaml\n",
        encoding="utf-8")
    req, dis = pf.check_task_yaml(t2)
    out = capsys.readouterr().out
    assert req == ["widget", "ghost"] and dis == ["dud"]
    assert "falling back to regex" in out
    assert "environment/ghost-api MISSING" in out


def test_pf_check_mock_data_all_branches(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    md = task / "mock_data"
    # good overlay -> boots
    g = md / "widget-api"
    g.mkdir()
    (g / "items.csv").write_text("id,name\n1,a\n", encoding="utf-8")
    (g / "extra.json").write_text("{}", encoding="utf-8")
    # unknown env folder
    (md / "missing-api").mkdir()
    # ragged csv + header mismatch
    bc = md / "badcsv-api"
    bc.mkdir()
    (bc / "rows.csv").write_text("x,y\n1\n", encoding="utf-8")
    # bad json
    bj = md / "badjson-api"
    bj.mkdir()
    (bj / "data.json").write_text("{nope", encoding="utf-8")
    # boot failure
    bf = md / "bootfail-api"
    bf.mkdir()
    (bf / "seed.json").write_text("{}", encoding="utf-8")
    # pre-seed a stale _pf_ module so _boot_api's finally-cleanup loop runs
    sys.modules["_pf_stale"] = types.ModuleType("_pf_stale")
    # static (no data module)
    st = md / "static-api"
    st.mkdir()
    (st / "note.json").write_text("{}", encoding="utf-8")

    pf.check_mock_data(task)
    assert "_pf_stale" not in sys.modules       # cleanup loop reaped it
    out = capsys.readouterr().out
    assert "widget-api: schema OK + server boots" in out
    assert "missing-api: no environment/missing-api folder" in out
    assert "badcsv-api: schema/integrity issues" in out and "MISMATCH" in out
    assert "badjson-api: schema/integrity issues" in out and "bad json" in out
    assert "bootfail-api: boot FAILED -> RuntimeError: boom" in out
    assert "static-api: schema OK + server boots" in out
    # no mock_data dir at all
    pf.check_mock_data(_mk_task(tmp_path / "nomd", full=False))
    assert "mock_data/ missing" in capsys.readouterr().out


class _FStage:
    def __init__(self, index, name, source, *, is_seed=False, from_turn=None,
                 to_turn=None, filesystem=(), loud=(), silent=()):
        self.index = index
        self.name = name
        self.source = str(source)
        self.is_seed = is_seed
        self.from_turn = from_turn
        self.to_turn = to_turn
        self.filesystem = list(filesystem)
        self.loud = list(loud)
        self.silent = list(silent)


def _fake_inject_module(stages=None, load_exc=None):
    m = types.ModuleType("src.utils.inject_director")

    class InjectScript:
        @staticmethod
        def load(path):
            if load_exc:
                raise load_exc
            return types.SimpleNamespace(stages=stages or [])

    m.InjectScript = InjectScript
    return m


def _fake_gate_module(findings=()):
    """Stand in for the world-correctness gate (section 7).

    These are wiring tests: they assert what preflight_task PRINTS and what it
    exits with, against fixtures whose services are made up. The gate imports
    and runs real ones, and has its own calibration suite in
    tests/test_inject_preflight.py.
    """
    m = types.ModuleType("src.utils.inject_preflight")
    m.FATAL, m.WARN = "FATAL", "WARN"
    m.gate_task = lambda task, **kw: types.SimpleNamespace(
        findings=tuple(findings),
        fatal=tuple(f for f in findings if f.severity == "FATAL"),
        ops=0, elapsed_ms=0)
    return m


def test_pf_check_inject_full_battery(pf, tmp_path, capsys, monkeypatch):
    task = _mk_task(tmp_path)
    # stage source dirs with/without verify.sh
    s0 = task / "inject" / "stage0"
    s1 = task / "inject" / "stage1"
    s2 = task / "inject" / "stage2"
    for d in (s0, s1, s2):
        d.mkdir(parents=True)
    (s0 / "verify.sh").write_text("echo ok\n", encoding="utf-8")
    (s1 / "verify.sh").write_text("echo ok\n", encoding="utf-8")
    (s1 / "attach.eml").write_text("eml", encoding="utf-8")
    (s1 / "seed.csv").write_text("a\n", encoding="utf-8")

    stages = [
        _FStage(0, "seed", s0 / "mutations.json", is_seed=True,
                filesystem=[{"id": "f0", "src": "seed.csv", "dst": "relative/x"}]),
        _FStage(1, "mid", s1 / "mutations.json", from_turn=1, to_turn=3,
                filesystem=[
                    {"id": "f1", "src": "seed.csv", "dst": "/abs/ok",
                     "fires_at_turn": "T3"},
                    {"id": "f2", "src": "missing.bin", "fires_at_turn": "T99"},
                ],
                loud=[
                    {"id": "l1", "service": "widget-api"},
                    {"id": "l2", "service": "message"},
                    {"id": "l3", "service": "mystery"},
                    {"id": "l4", "service": None,
                     "body": {"raw_eml_path": "attach.eml"}},
                    {"id": "l5", "body": {"raw_eml_path": "gone.eml"}},
                ],
                silent=[{"id": "s1", "body": "not-a-dict"}]),
        _FStage(2, "dup", s2 / "mutations.json", from_turn=3, to_turn=3),
    ]
    fake = _fake_inject_module(stages)
    monkeypatch.setitem(sys.modules, "src.utils.inject_director", fake)
    pf.check_inject(task, ["widget"], [])
    out = capsys.readouterr().out
    assert "InjectScript.load OK — 3 stage(s)" in out
    assert "seed stage present" in out
    assert "src exists: seed.csv" in out and "src MISSING: missing.bin" in out
    assert "dst not absolute: relative/x" in out
    assert "fires_at_turn T99 outside" in out
    assert "service=widget-api" in out and "OpenClaw native tool" in out
    assert "UNKNOWN service=mystery" in out
    assert "raw_eml_path OK" in out and "raw_eml_path MISSING: gone.eml" in out
    assert "0 fs / 0 loud / 0 silent" in out          # dup stage nops WARN
    assert "stage2/verify.sh missing" in out
    # inject/ dir missing
    pf.check_inject(_mk_task(tmp_path / "noinj", full=False), [], [])
    assert "inject/ missing" in capsys.readouterr().out
    # InjectScript.load raising
    monkeypatch.setitem(sys.modules, "src.utils.inject_director",
                        _fake_inject_module(load_exc=ValueError("bad script")))
    pf.check_inject(task, [], [])
    assert "InjectScript.load FAILED" in capsys.readouterr().out
    # import failure (None sentinel in sys.modules -> ImportError)
    monkeypatch.setitem(sys.modules, "src.utils.inject_director", None)
    pf.check_inject(task, [], [])
    assert "cannot import InjectScript" in capsys.readouterr().out


def test_pf_check_turns_and_grading(pf, tmp_path, capsys):
    task = _mk_task(tmp_path)
    (task / "task").mkdir()
    (task / "task" / "task.py").write_text("CHECKERS = []\n", encoding="utf-8")
    (task / "test_outputs.py").write_text(
        "from pathlib import Path\n# loads task/task.py checkers\n"
        "def test_a():\n    pass\n", encoding="utf-8")
    pf.check_turns_and_grading(task)
    out = capsys.readouterr().out
    assert "prompts.txt has 2 turns" in out and "contiguous" in out
    assert "CHECKERS source task/task.py present" in out
    # gaps + invalid weights + syntax error + absent task.py
    t2 = _mk_task(tmp_path / "g", full=False)
    (t2 / "prompts.txt").write_text("--- TURN T0\n--- TURN T2\n", encoding="utf-8")
    (t2 / "rubric.json").write_text("[]", encoding="utf-8")
    (t2 / "test_weights.json").write_text("{bad", encoding="utf-8")
    (t2 / "test_outputs.py").write_text("def broken(:\n", encoding="utf-8")
    pf.check_turns_and_grading(t2)
    out = capsys.readouterr().out
    assert "turn gaps" in out and "test_weights.json invalid" in out
    assert "syntax error" in out
    # missing prompts + missing test_outputs; CHECKERS referenced but absent
    t3 = _mk_task(tmp_path / "m", full=False)
    (t3 / "rubric.json").write_text("[]", encoding="utf-8")
    (t3 / "test_weights.json").write_text("[]", encoding="utf-8")
    pf.check_turns_and_grading(t3)
    out = capsys.readouterr().out
    assert "prompts.txt missing" in out
    # The generated-test pair gets ONE verdict, in section 1, because its
    # severity depends on whether both halves are absent or only one.
    assert "test_outputs.py" not in out
    t4 = _mk_task(tmp_path / "c", full=False)
    (t4 / "prompts.txt").write_text("--- TURN T0\n", encoding="utf-8")
    (t4 / "rubric.json").write_text("[]", encoding="utf-8")
    (t4 / "test_weights.json").write_text("[]", encoding="utf-8")
    (t4 / "test_outputs.py").write_text(
        'SRC = "task/task.py"\ndef test_a():\n    pass\n', encoding="utf-8")
    pf.check_turns_and_grading(t4)
    assert "which is ABSENT" in capsys.readouterr().out


def test_pf_main_missing_green_and_red(pf, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["preflight_task.py", str(tmp_path / "nope")])
    assert pf.main() == 2
    capsys.readouterr()
    # fully green task -> exit 0. "Green" now includes the three task-format
    # standards (derived date, TRUTH.md sections, prompt header block), so the
    # fixture has to declare a window and carry both files.
    task = _mk_task(tmp_path)
    (task / "task.yaml").write_text(
        "task_type: ops\nsystem_prompt: be helpful\n"
        "required_apis: [widget]\ndistractor_apis: []\n"
        "window: 2026-10-06 to 2026-10-11\ntimezone: America/Chicago\n",
        encoding="utf-8")
    (task / "TRUTH.md").write_text(_mandated_truth(), encoding="utf-8")
    (task / "prompts.txt").write_text(
        "# task_id: TASK\n# persona: Widget Tester\n# timezone: America/Chicago\n"
        "# window: 2026-10-06 to 2026-10-11 (6 days)\n# turn_count: 2\n\n"
        "--- TURN T0\nhi\n--- TURN T1\nbye\n", encoding="utf-8")
    md = task / "mock_data" / "widget-api"
    md.mkdir()
    (md / "items.csv").write_text("id,name\n1,a\n", encoding="utf-8")
    s0 = task / "inject" / "stage0"
    s0.mkdir()
    (s0 / "verify.sh").write_text("echo ok\n", encoding="utf-8")
    stages = [_FStage(0, "seed", s0 / "mutations.json", is_seed=True,
                      loud=[{"id": "l0", "service": "widget-api"}])]
    monkeypatch.setitem(sys.modules, "src.utils.inject_director",
                        _fake_inject_module(stages))
    monkeypatch.setitem(sys.modules, "src.utils.inject_preflight", _fake_gate_module())
    monkeypatch.setattr(sys, "argv", ["preflight_task.py", str(task)])
    assert pf.main() == 0
    assert "SUMMARY" in capsys.readouterr().out
    # one FAIL flips the exit code
    (task / "rubric.json").unlink()
    pf._counts.update({"PASS": 0, "WARN": 0, "FAIL": 0})
    assert pf.main() == 1


def test_pf_default_task_and_dunder_main(pf, monkeypatch, tmp_path):
    # default DEFAULT_TASK path (argv without task) — point it at a missing dir
    monkeypatch.setattr(pf, "DEFAULT_TASK", tmp_path / "absent")
    monkeypatch.setattr(sys, "argv", ["preflight_task.py"])
    assert pf.main() == 2
    monkeypatch.setattr(sys, "argv", ["preflight_task.py", str(tmp_path / "also-absent")])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "preflight_task.py"), run_name="__main__")
    assert e.value.code == 2


# ======================================================================
# _seed_dst_to_data_rel — must recognise every workspace alias the runtime
# mapper normalizes, or the mirrored-payload check silently never runs.
# ======================================================================

@pytest.mark.parametrize("dst,expected", [
    ("/workspace/home/Pictures/x.png", "Pictures/x.png"),
    ("/app/home/Pictures/x.png", "Pictures/x.png"),
    ("/root/workspace/home/Pictures/x.png", "Pictures/x.png"),
    ("/root/.openclaw/workspace/home/Pictures/x.png", "Pictures/x.png"),
    ("~/workspace/home/Pictures/x.png", "Pictures/x.png"),
    ("/data/home/Pictures/x.png", "Pictures/x.png"),
    ("data/home/Pictures/x.png", "Pictures/x.png"),
])
def test_seed_dst_to_data_rel_recognises_every_alias(pf, dst, expected):
    assert pf._seed_dst_to_data_rel(dst) == expected


@pytest.mark.parametrize("dst", ["/etc/passwd", "/workspace/notes/x.txt", "", None])
def test_seed_dst_to_data_rel_ignores_non_data_dsts(pf, dst):
    assert pf._seed_dst_to_data_rel(dst) is None


def test_seed_dst_alias_payload_mirror_check_now_fires(pf, tmp_path, monkeypatch):
    """An aliased seed dst must still be checked against its data/ counterpart."""
    task = tmp_path / "aliased_task"
    (task / "data").mkdir(parents=True)
    results = []
    monkeypatch.setattr(pf, "rec", lambda level, msg: results.append((level, msg)))

    pf._check_seed_payload_mirrored(
        task, "seed", {"id": "fs-1", "action": "copy",
                       "dst": "/root/workspace/home/Pictures/x.png"})

    assert results and results[0][0] == pf.FAIL
    assert "data/Pictures/x.png" in results[0][1]
