"""Unit coverage for the previously zero-coverage utility scripts (tranche 1a):

  * script/grade_golden.py          — council-grade a golden trajectory (the
    LLM judge grade_with_rubric is monkeypatched; path roots are redirected to
    tmp via the module's REPO_ROOT global).
  * script/offline_audit_conftest.py — offline audit shim; loaded with a fake
    `test_outputs` module pre-seeded in sys.modules. Its module-level
    agent_state.json probe reads a sibling of the REAL script file, so those
    branches are exercised by briefly materialising script/agent_state.json
    (always cleaned up via try/finally).
  * script/regrade_trajectory.py    — offline pytest re-grade of a trajectory.
  * script/test_report.py           — weighted pytest report over trajectories.
  * script/retest_run.py            — offline retest of a finished run.

pytest subprocess invocations are monkeypatched with canned `-v` output so the
status-line parsing, weighting, and report paths run deterministically with no
child processes. __main__ guards run via runpy.run_path(run_name="__main__").
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


class _Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


# ======================================================================
# script/grade_golden.py
# ======================================================================


@pytest.fixture(scope="module")
def gg():
    return _load_script("grade_golden.py", "_t_grade_golden")


def _mk_golden_task(root: Path, task: str, *, rubric, with_prompt=True):
    g = root / "golden_trajectories" / task
    g.mkdir(parents=True)
    (g / "golden_trajectory.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    inp = root / "input" / task
    inp.mkdir(parents=True)
    (inp / "rubric.json").write_text(json.dumps(rubric), encoding="utf-8")
    if with_prompt:
        (inp / "prompt.txt").write_text("do the thing\n", encoding="utf-8")
    return g


FULL_SCORES = {
    "overall_score": 0.9,
    "rubric_weights_percentage": 90.0,
    "criteria_total": 3, "criteria_passed": 2, "criteria_failed": 1,
    "criteria_abstained": 0,
    "judge_council": {
        "surviving": [{"model": "sonnet"}],
        "failed": [{"model": "kimi", "error": "boom"}],
    },
    "criteria": [
        {"passed": True, "criterion": "ok"},
        {"passed": False, "is_positive": True, "weight": 3,
         "criterion": "posts invoice", "rationale": "missing"},
        {"passed": False, "is_positive": False, "weight": -5,
         "criterion": "no distractor", "rationale": "fired"},
    ],
}


def test_gg_success_path_with_council_and_misses(gg, tmp_path, capsys, monkeypatch):
    _mk_golden_task(tmp_path, "T1", rubric=[{"criterion": "a"}])
    monkeypatch.setattr(gg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gg, "_condense_transcript_for_judge", lambda traj: "TRANSCRIPT")
    monkeypatch.setattr(gg, "grade_with_rubric", lambda *a, **k: dict(FULL_SCORES))
    monkeypatch.setattr(sys, "argv", ["grade_golden.py", "T1"])
    assert gg.main() == 0
    out = capsys.readouterr().out
    assert "GOLDEN GRADE" in out and "council surviving = 1/2" in out
    assert "FAILED member: kimi" in out
    assert "non-passing criteria" in out and "[NEG w=-5]" in out and "[POS w=3]" in out
    written = json.loads(
        (tmp_path / "golden_trajectories" / "T1" / "score_golden.json").read_text())
    assert written["overall_score"] == 0.9


def test_gg_error_result_dict_rubric_and_default_task(gg, tmp_path, capsys, monkeypatch):
    # Default argv task + dict-shaped rubric.json + missing prompt.txt.
    _mk_golden_task(tmp_path, "ALDEN_002_haul_out_week",
                    rubric={"rubrics": [{"criterion": "a"}]}, with_prompt=False)
    monkeypatch.setattr(gg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gg, "_condense_transcript_for_judge", lambda traj: "")
    monkeypatch.setattr(gg, "grade_with_rubric", lambda *a, **k: {"error": "no creds"})
    monkeypatch.setattr(sys, "argv", ["grade_golden.py"])
    assert gg.main() == 1
    assert "ERROR: no creds" in capsys.readouterr().out


def test_gg_syspath_guard_inserts_repo_root(monkeypatch):
    """Fresh load with REPO_ROOT scrubbed from sys.path takes the guard's True
    branch (the module re-adds the repo root before its harness imports)."""
    scrubbed = [p for p in sys.path if p != str(REPO_ROOT)]
    monkeypatch.setattr(sys, "path", scrubbed)
    mod = _load_script("grade_golden.py", "_t_grade_golden_syspath")
    assert str(REPO_ROOT) in sys.path
    assert mod.REPO_ROOT == REPO_ROOT


def test_gg_dunder_main_missing_task_raises(gg, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["grade_golden.py", "___no_such_task___"])
    with pytest.raises(FileNotFoundError):
        runpy.run_path(str(SCRIPT_DIR / "grade_golden.py"), run_name="__main__")


# ======================================================================
# script/offline_audit_conftest.py
# ======================================================================


AUDIT_BLOB = {
    "audit": {
        "gmail-api": {
            "requests": [
                {"method": "GET", "path": "/messages", "status_code": 200},
                {"method": "GET", "path": "/messages", "status_code": 200},
                {"method": "POST", "path": "/send", "status_code": ""},
            ],
        },
        "sentry-api": {
            "total_requests": 7,
            "endpoints": {"GET /issues": {"count": 7, "statuses": {"200": 7}}},
        },
        "weird-api": "not-a-dict",
    }
}


def _fake_test_outputs() -> types.ModuleType:
    m = types.ModuleType("test_outputs")
    m.GMAIL_API_URL = "http://gmail.mock:9001/"
    m.SENTRY_API_URL = "http://sentry.mock:9002"
    m.WEIRD_API_URL = "http://weird.mock:9003"
    m.EMPTY_API_URL = ""            # falsy -> skipped by the URL map builder
    m.NOT_A_URL = 42                # non-str -> skipped
    m._request = None
    return m


def _load_shim(alias: str):
    sys.modules["test_outputs"] = _fake_test_outputs()
    try:
        return _load_script("offline_audit_conftest.py", alias)
    finally:
        sys.modules.pop("test_outputs", None)


def test_shim_with_saved_diary_and_all_serve_branches():
    state = SCRIPT_DIR / "agent_state.json"
    assert not state.exists(), "unexpected stray script/agent_state.json"
    state.write_text(json.dumps(AUDIT_BLOB), encoding="utf-8")
    try:
        shim = _load_shim("_t_shim_valid")
    finally:
        state.unlink()
    # URL map: only non-empty *_API_URL constants, trailing slash stripped.
    assert shim._URL2SVC == {
        "http://gmail.mock:9001": "gmail-api",
        "http://sentry.mock:9002": "sentry-api",
        "http://weird.mock:9003": "weird-api",
    }
    assert shim._svc_from_const("GOOGLE_CALENDAR_API_URL") == "google-calendar-api"
    # summary derived from the diary (statuses folded, blank status skipped)
    s = shim._fake_request("GET", "http://gmail.mock:9001/audit/summary")
    assert s["total_requests"] == 3
    assert s["endpoints"]["GET /messages"] == {"count": 2, "statuses": {"200": 2}}
    assert s["endpoints"]["POST /send"]["statuses"] == {}
    # summary passthrough when endpoints were persisted
    s = shim._fake_request("GET", "http://sentry.mock:9002/audit/summary")
    assert s == {"total_requests": 7,
                 "endpoints": {"GET /issues": {"count": 7, "statuses": {"200": 7}}}}
    # full diary; and non-dict blob -> empty
    r = shim._fake_request("GET", "http://gmail.mock:9001/audit/requests")
    assert r["total"] == 3 and len(r["requests"]) == 3
    r = shim._fake_request("GET", "http://weird.mock:9003/audit/requests")
    assert r == {"total": 0, "requests": []}
    # non-dict blob summary -> zeros
    s = shim._fake_request("GET", "http://weird.mock:9003/audit/summary")
    assert s == {"total_requests": 0, "endpoints": {}}
    # unknown endpoint + unknown service both raise URLError
    from urllib.error import URLError
    with pytest.raises(URLError):
        shim._fake_request("GET", "http://gmail.mock:9001/messages")
    with pytest.raises(URLError):
        shim._fake_request("GET", "http://other.mock:1/audit/summary")
    # the shim must have swapped the test module's network primitive
    assert shim._T._request is shim._fake_request


def test_shim_corrupt_and_missing_state_fall_back_to_empty():
    state = SCRIPT_DIR / "agent_state.json"
    state.write_text("{corrupt", encoding="utf-8")
    try:
        shim = _load_shim("_t_shim_corrupt")
    finally:
        state.unlink()
    assert shim._AUDIT == {}
    shim2 = _load_shim("_t_shim_absent")   # no file at all
    assert shim2._AUDIT == {}


# ======================================================================
# script/regrade_trajectory.py
# ======================================================================


@pytest.fixture(scope="module")
def rt():
    return _load_script("regrade_trajectory.py", "_t_regrade_traj")


def test_rt_assistant_text_shapes(rt, tmp_path):
    p = tmp_path / "output.json"
    assert rt._assistant_text(p) == ""                       # missing
    p.write_text("{bad", encoding="utf-8")
    assert rt._assistant_text(p) == ""                       # unreadable
    p.write_text(json.dumps({"messages": [
        "not-a-dict",
        {"message": {"role": "user", "content": "hi"}},
        {"message": {"role": "assistant", "content": "plain"}},
        {"message": {"role": "assistant",
                     "content": [{"text": "block"}, "raw", {"nope": 1}, 7]}},
        {"message": {"role": "assistant", "content": {"weird": True}}},
    ]}), encoding="utf-8")
    assert rt._assistant_text(p) == "plain\nblock\nraw"


def _mk_traj(root: Path, name: str, *, with_suite=True, with_snap=True) -> Path:
    traj = root / name
    (traj / "input").mkdir(parents=True)
    if with_suite:
        (traj / "input" / "test_outputs.py").write_text("def test_ok():\n    pass\n")
    run = traj / "output-raw" / "trajectories" / "m" / "run_1"
    run.mkdir(parents=True)
    (run / "output.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    if with_snap:
        (run / "snapshot" / "workspace_after" / "mock_data").mkdir(parents=True)
    return traj


def test_rt_rebuild_state_and_regrade_branches(rt, tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "build_agent_state", lambda **k: {"stores": {}})
    assert rt.rebuild_state(tmp_path / "no-such") is None            # no runs
    t_nosnap = _mk_traj(tmp_path, "nosnap", with_snap=False)
    assert rt.rebuild_state(t_nosnap) is None                        # no snapshot
    t = _mk_traj(tmp_path, "good")
    dest = rt.rebuild_state(t)
    assert dest and json.loads(dest.read_text()) == {"stores": {}}
    # regrade: SKIP branches
    t_nosuite = _mk_traj(tmp_path, "nosuite", with_suite=False)
    assert "SKIP (no test_outputs.py)" in rt.regrade(t_nosuite)
    assert "SKIP (no usable snapshot" in rt.regrade(t_nosnap)
    # regrade happy path + "(no output)" fallback via canned subprocess
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: _Proc("1 passed in 0.01s\n"))
    assert rt.regrade(t).endswith("1 passed in 0.01s")
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: _Proc(""))
    assert rt.regrade(t).endswith("(no output)")


def test_rt_main_paths(rt, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rt.main([]) == 2                                          # no dirs at all
    assert "no trajectory directories found" in capsys.readouterr().err
    monkeypatch.setattr(rt, "build_agent_state", lambda **k: {})
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: _Proc("2 passed\n"))
    t = _mk_traj(tmp_path, "traj1")
    (tmp_path / "notdir.txt").write_text("")                         # filtered out
    assert rt.main([str(t), str(tmp_path / "notdir.txt")]) == 0
    out = capsys.readouterr().out
    assert "Re-grading 1 trajectory(ies)" in out and "2 passed" in out
    # default-glob branch: cwd/trajectories/*
    (tmp_path / "trajectories").mkdir()
    _mk_traj(tmp_path / "trajectories", "t2", with_suite=False)
    assert rt.main([]) == 0
    assert "SKIP" in capsys.readouterr().out


def test_rt_dunder_main(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["regrade_trajectory.py"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "regrade_trajectory.py"), run_name="__main__")
    assert e.value.code == 2


# ======================================================================
# script/test_report.py
# ======================================================================


@pytest.fixture(scope="module")
def tr():
    return _load_script("test_report.py", "_t_test_report")


PYTEST_V_OUT = """\
test_outputs.py::TestA::test_pos_one PASSED
test_outputs.py::TestA::test_pos_two FAILED
test_outputs.py::test_guard FAILED
test_outputs.py::test_skipped SKIPPED
garbage line without status
"""


def test_tr_assistant_text_and_norm(tr, tmp_path):
    p = tmp_path / "o.json"
    assert tr._assistant_text(p) == ""
    p.write_text("{bad", encoding="utf-8")
    assert tr._assistant_text(p) == ""
    p.write_text(json.dumps({"messages": [
        "x",
        {"message": {"role": "assistant", "content": "s"}},
        {"message": {"role": "assistant", "content": [{"text": "t"}, {"no": 1}]}},
    ]}), encoding="utf-8")
    assert tr._assistant_text(p) == "s\nt"
    assert tr._norm("a.py::TestA::test_x ") == "test_x"


def _mk_report_traj(root: Path, name: str, *, weights=None, with_snap=True,
                    extra_suite=False) -> Path:
    traj = root / name
    inp = traj / "input" / "suiteA"
    inp.mkdir(parents=True)
    (inp / "test_outputs.py").write_text("def test_pos_one():\n    pass\n")
    if weights is not None:
        (inp / "test_weights.json").write_text(
            weights if isinstance(weights, str) else json.dumps(weights))
    if extra_suite:
        other = traj / "input" / "suiteB"
        other.mkdir(parents=True)
        (other / "test_outputs.py").write_text("def test_b():\n    pass\n")
    run = traj / "output-raw" / "trajectories" / "m" / "run_1"
    run.mkdir(parents=True)
    (run / "output.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    if with_snap:
        (run / "snapshot" / "workspace_after" / "mock_data").mkdir(parents=True)
    return traj


def test_tr_find_suite_prefers_weighted_and_snapshot_guards(tr, tmp_path):
    t = _mk_report_traj(tmp_path, "w", weights={"test_pos_one": 3}, extra_suite=True)
    suite = tr._find_suite(t)
    assert suite and suite.parent.name == "suiteA"        # weighted sibling wins
    t2 = _mk_report_traj(tmp_path, "nw")                  # no weights anywhere
    assert tr._find_suite(t2).parent.name == "suiteA"
    empty = tmp_path / "empty"
    (empty / "input").mkdir(parents=True)
    assert tr._find_suite(empty) is None
    assert tr._snapshot(empty) is None                    # no runs
    t3 = _mk_report_traj(tmp_path, "nosnap", with_snap=False)
    assert tr._snapshot(t3) is None


def test_tr_regrade_weighted_and_unweighted(tr, tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "build_agent_state", lambda **k: {})
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _Proc(PYTEST_V_OUT))
    weights = {"a.py::test_pos_one": 3, "test_pos_two": 5, "test_guard": -4,
               "test_bool": True}      # bool is int-instance -> coerced
    t = _mk_report_traj(tmp_path, "traj", weights=weights)
    r = tr.regrade(t)
    assert r["status"] == "ok" and r["total"] == 4
    assert (r["PASSED"], r["FAILED"], r["SKIPPED"]) == (1, 2, 1)
    assert r["pos_total"] == pytest.approx(9.0)           # 3 + 5 + True
    assert r["pos_earned"] == pytest.approx(3.0)
    assert r["neg_penalty"] == pytest.approx(4.0)         # guard ran & failed
    assert r["weighted_reward"] == pytest.approx(0.0)     # (3-4)/9 clamps
    assert r["failures"] == ["test_guard", "test_pos_two"]
    # bad weights json -> except branch; no positives -> reward 0.0 branch
    t2 = _mk_report_traj(tmp_path, "badw", weights="{nope")
    r2 = tr.regrade(t2)
    assert r2["pos_total"] == 0 and r2["weighted_reward"] == 0.0
    # skip branch
    t3 = _mk_report_traj(tmp_path, "nosnap2", with_snap=False)
    assert tr.regrade(t3)["status"] == "skip"


def test_tr_main_prints_table_and_writes_report(tr, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trajectories").mkdir()
    monkeypatch.setattr(tr, "build_agent_state", lambda **k: {})
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _Proc(PYTEST_V_OUT))
    ok = _mk_report_traj(tmp_path, "okA", weights={"test_pos_one": 2})
    skip = _mk_report_traj(tmp_path, "skipB", with_snap=False)
    (tmp_path / "afile").write_text("")                   # non-dir filtered
    assert tr.main([str(ok), str(skip), str(tmp_path / "afile")]) == 0
    out = capsys.readouterr().out
    assert "WEIGHTED TEST REPORT" in out and "SKIP (no suite or no snapshot)" in out
    data = json.loads((tmp_path / "trajectories" / "TEST_REPORT.json").read_text())
    assert {r["status"] for r in data} == {"ok", "skip"}
    # default-glob branch (empty trajectories/) still writes a report
    assert tr.main([]) == 0


def test_tr_dunder_main(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trajectories").mkdir()
    monkeypatch.setattr(sys, "argv", ["test_report.py"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "test_report.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# script/retest_run.py
# ======================================================================


@pytest.fixture(scope="module")
def rr():
    return _load_script("retest_run.py", "_t_retest_run")


RETEST_V_OUT = """\
test_outputs.py::test_a PASSED
test_outputs.py::test_b FAILED
test_outputs.py::test_c ERROR
noise
"""


def _mk_run_folder(root: Path, *, state="direct", suite_at_parent=True) -> Path:
    run = root / "parent" / "run_1"
    run.mkdir(parents=True)
    if state == "direct":
        (run / "agent_state.json").write_text("{}", encoding="utf-8")
    elif state == "alt":
        alt = run / "data" / "tests"
        alt.mkdir(parents=True)
        (alt / "agent_state.json").write_text("{}", encoding="utf-8")
    if suite_at_parent:
        inp = root / "parent" / "input"
        inp.mkdir(parents=True)
        (inp / "test_outputs.py").write_text("def test_a():\n    pass\n")
        (inp / "test_weights.json").write_text(json.dumps(
            {"test_a": 2, "test_b": -1, "test_c": 1, "bad": "x"}))
    return run


class _Args:
    tests = None
    weights = None
    keep = False


def test_rr_locate_variants(rr, tmp_path):
    run = _mk_run_folder(tmp_path / "a", state="alt")
    state, tests, weights = rr._locate(run, _Args())
    assert state and state.name == "agent_state.json" and "data" in str(state)
    assert tests and weights
    # explicit --tests/--weights override the search
    a = _Args()
    a.tests = str(tests)
    a.weights = str(weights)
    run2 = _mk_run_folder(tmp_path / "b", state="none", suite_at_parent=False)
    st, t2, w2 = rr._locate(run2, a)
    assert st is None and t2 == tests.resolve() and w2 == weights.resolve()


def test_rr_retest_error_paths(rr, tmp_path, capsys):
    run = _mk_run_folder(tmp_path / "nostate", state="none", suite_at_parent=False)
    assert rr.retest(run, _Args()) == 2
    assert "no agent_state.json" in capsys.readouterr().err
    run2 = _mk_run_folder(tmp_path / "notests", suite_at_parent=False)
    a = _Args()
    a.tests = str(tmp_path / "missing_tests.py")
    assert rr.retest(run2, a) == 2
    assert "no test_outputs.py" in capsys.readouterr().err


def test_rr_retest_happy_and_keep(rr, tmp_path, capsys, monkeypatch):
    workdirs = []

    def fake_mkdtemp(prefix=""):
        d = tmp_path / f"{prefix}{len(workdirs)}"
        d.mkdir()
        workdirs.append(d)
        return str(d)

    monkeypatch.setattr(rr.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(rr.subprocess, "run", lambda *a, **k: _Proc(RETEST_V_OUT))
    run = _mk_run_folder(tmp_path / "ok")
    assert rr.retest(run, _Args()) == 0
    out = capsys.readouterr().out
    assert "OFFLINE RETEST" in out and "1 errored — no saved diary" in out
    score = json.loads((run / "score_offline.json").read_text())
    assert score["tests_total"] == 3 and score["tests_passed"] == 1
    assert score["results"]["test_a"] == "passed"
    assert not workdirs[0].exists()                       # cleaned up
    # --keep + corrupt weights (except branch) + kept dir message
    (tmp_path / "ok" / "parent" / "input" / "test_weights.json").write_text("{bad")
    a = _Args()
    a.keep = True
    assert rr.retest(run, a) == 0
    assert "kept temp test dir" in capsys.readouterr().out
    assert workdirs[1].exists()


def test_rr_main_and_dunder_main(rr, tmp_path, capsys, monkeypatch):
    run = _mk_run_folder(tmp_path / "cli", state="none", suite_at_parent=False)
    assert rr.main([str(run)]) == 2                       # argparse -> retest error
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["retest_run.py", str(run)])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "retest_run.py"), run_name="__main__")
    assert e.value.code == 2
