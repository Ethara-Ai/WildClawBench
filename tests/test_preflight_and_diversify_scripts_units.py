"""Unit coverage for script/preflight_task.py and script/diversify_golden_tooluse.py.

preflight_task: the module's ENV global is monkeypatched to a synthetic
environment tree (fake APIs with tiny _data.py store modules), the inject
checker gets a fake src.utils.inject_director via sys.modules, and each check_*
section is driven by a purpose-built task fixture.

diversify_golden_tooluse is a one-off migration script (no functions to call —
it rewrites ./Golden_Trajectory.json at import). It is executed with
runpy.run_path from a tmp cwd against a synthetic trajectory crafted to walk
every branch: the cron-create rewrites (one per shape), both combined-update
splits, the four doc-write summary markers, calendar-result reminder stripping,
the cron-list insertions, prose fix, and envelope re-threading.
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
    assert "prompts.txt missing" in out and "test_outputs.py missing" in out
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
    # fully green task -> exit 0
    task = _mk_task(tmp_path)
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
# script/diversify_golden_tooluse.py
# ======================================================================


def _th(t="hm"):
    return {"type": "thinking", "thinking": t, "thinkingSignature": ""}


def _tx(t):
    return {"type": "text", "text": t}


def _tc(cid, name, args):
    return {"type": "toolCall", "id": cid, "name": name, "arguments": args}


def _asst(blocks, ts=None):
    m = {"type": "message", "message": {"role": "assistant", "content": blocks}}
    if ts:
        m["timestamp"] = ts
    return m


def _tr(text="ok", ts=None):
    m = {"type": "message",
         "message": {"role": "toolResult", "content": [{"type": "text", "text": text}]}}
    if ts:
        m["timestamp"] = ts
    return m


def _user(t, ts=None):
    m = {"type": "message", "message": {"role": "user", "content": t}}
    if ts:
        m["timestamp"] = ts
    return m


GRAND_OLD = ("**Grand total this session:** 22 one-off events + 2 recurring series "
             "(13 weekly planning + 8 monthly audits) = **43 calendar entries created/modified**.")


def _mk_golden(tmp_path: Path) -> Path:
    rid = "rpfjk6ejbhepd4us8qu16vk4fs"
    cal_query = json.dumps({"command": "gog calendar events primary"})
    msgs = [
        _user("hello", ts="2026-05-06T10:00:00Z"),
        _tr("stray result"),                                     # standalone toolResult
        # four doc-write summary turns + one plain + one thinking-only
        _asst([_th(), _tx("Adobe Dispute & Resolution Timeline\n## body A")]),
        _asst([_tx("Client Deadlines & Deliveries\n## body B")]),
        _asst([_tx("Adobe Overcharge Dispute — Full Resolution Plan\n## body C")]),
        _asst([_tx("Adobe Overcharge Impact\n## body D")]),
        _asst([_tx("plain summary\n" + GRAND_OLD)], ts="2026-05-06T11:00:00Z"),
        _asst([_th("only thinking")]),
        # cron creates: with thinking+text, without thinking, recurring, verify(+edit)
        _asst([_th(), _tx("booking R2"),
               _tc("tooluse_wKy8WbT7pvaXROQ6W2L1Np", "exec", {"command": "gog calendar create"})]),
        _tr("created event"),
        _asst([_tc("tooluse_P2ocjaP7Ro9ecMnaR52JiK", "exec", {"command": "gog calendar create"})]),
        _tr("created event"),
        _asst([_tc("tooluse_spb0V2Jew7CA4u08gQ9JVa", "exec", {"command": "gog calendar create"})]),
        _tr("created recurring"),
        _asst([_th(), _tc("tooluse_XnjAyDGXIfU3GLDTxCnIyx", "exec", {"command": "gog calendar create"})]),
        _tr("created event"),
        # combined updates
        _asst([_th(), _tc("tooluse_xyRjIbmzDVAeihw6gJBKFO", "exec", {"command": "gog calendar update"})]),
        _tr("updated"),
        _asst([_th(), _tc("tooluse_cV3eFqQ85E23tlV1jfdNGo", "exec", {"command": "gog calendar update"})]),
        _tr("updated"),
        # generic think-rewrite: with a thinking block, and without one
        _asst([_th("templated"), _tc("tooluse_yPJzWUyj6lYIHBMXVPXaBU", "exec",
                                     {"command": "gog calendar create x"})]),
        _tr("event out"),
        _asst([_tc("tooluse_uHp5DJm2JLcDWwDUMA1T9t", "exec", {"command": "date checks"})]),
        _tr("dates ok"),
        # calendar query whose result keeps one row and strips reminder + next-page
        _asst([_th(), _tc("tcal1", "exec", {"command": "gog calendar events primary list"})]),
        _tr("ID  summary\nabc123 Real event row\n" + rid + " reminder row\n# Next page: tok"),
        # calendar query where everything strips away -> "No events"
        _asst([_tc("tcal2", "exec", {"command": "gog calendar events primary list"})]),
        _tr("ID  summary\n" + rid + " reminder row\n"),
        # Sept 1-11 window query (cron-list insertion A fires). The final
        # full-pull query (2026-10-02..2026-12-31) is deliberately ABSENT so
        # insertion B exhausts the scan and returns False — covering the
        # no-match path of insert_after_result.
        _asst([_tc("tsept", "exec",
                   {"command": cal_query,
                    "window": "2026-09-01T00:00:00-06:00 .. 2026-09-11"})]),
        _tr("ID\nsept events"),
        # dispute-call create -> post-call DRAFT write
        _asst([_th(), _tc("tooluse_3urIYrKpqA4Jm4Fvalvs9I", "exec",
                          {"command": "gog calendar create dispute call"})]),
        _tr("created call event"),
        # trailing assistant toolCall with NO result (result-None path)
        _asst([_tc("ttail", "exec", {"command": "echo bye"})]),
    ]
    doc = {"messages": msgs}
    (tmp_path / "Golden_Trajectory.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "Golden_Trajectory.json"


def test_diversify_rewrites_every_branch(tmp_path, monkeypatch, capsys):
    src = _mk_golden(tmp_path)
    monkeypatch.chdir(tmp_path)
    g = runpy.run_path(str(SCRIPT_DIR / "diversify_golden_tooluse.py"))
    out = capsys.readouterr().out
    assert "messages:" in out and "wrote Golden_Trajectory.json" in out

    d = json.loads(src.read_text(encoding="utf-8"))
    msgs = d["messages"]
    text_dump = json.dumps(msgs, ensure_ascii=False)

    # envelope re-threading: linear id/parent chain, timestamps filled
    assert msgs[0]["id"] == "d0000001" and msgs[0]["parentId"] == "d0000000"
    for a, b in zip(msgs, msgs[1:]):
        assert b["parentId"] == a["id"]
        assert b["timestamp"]
    # doc writes for all four summaries + saved-note prefixes
    for marker in ("adobe_dispute_timeline.md", "client_deadlines_may-sep_2026.md",
                   "adobe_overcharge_dispute_plan.md", "adobe_overcharge_impact.md"):
        assert marker in text_dump
    assert text_dump.count("Saved to `") >= 4
    # cron rewrites: one-off + recurring + updates + plan edit after verify
    assert "Scheduled wake job job_a17f93c0" in text_dump
    assert "fires monthly on the 15th" in text_dump
    assert "Updated wake job job_f6b8c0d2" in text_dump
    assert "Updated wake job job_b2c4d6e8" in text_dump
    assert "Applied 1 edit to /root/workspace/adobe_overcharge_dispute_plan.md" in text_dump
    # generic think rewrites landed
    assert "Third of the batch" in text_dump and "verify the weekdays" in text_dump
    # reminder rows stripped from calendar results, empty result collapsed
    assert "reminder row" not in text_dump and "# Next page" not in text_dump
    assert "Real event row" in text_dump and '"No events"' in text_dump
    # cron-list insertion A fired (Sept window); B found no match by design
    assert "JOB ID" in text_dump
    assert "Send Desert Bloom deposit invoice" in text_dump
    # prose fix swapped the grand total
    assert "43 calendar entries" not in text_dump
    assert "11 cron wake reminders" in text_dump
    # post-call draft write
    assert "Adobe_billing_dispute_DRAFT.md" in text_dump

    # helpers exposed by run_path globals: cover the non-list branches directly
    assert g["block_types"]({"message": {"content": "nope"}}) == []
    assert g["block_types"]({"message": {"content": [_th()]}}) == ["thinking"]
    assert g["first_toolcall"]({"message": {"content": "nope"}}) is None
