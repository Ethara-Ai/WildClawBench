"""Unit coverage for the talos-merge backfill scripts + coverage top-up for the
older three, driving all six script/backfill_*.py to 100% line coverage.

Covered modules (loaded via importlib because script/ is not an importable pkg):
  * script/backfill_bundle_meta.py    — report.json rubric-meta merge (FIX A) and
    score.json tests_* repair from ctrf (FIX B), every skip/dry-run branch.
  * script/backfill_subagent_meta.py  — output.json roster re-attach; the heavy
    attach_native_subagents() is monkeypatched at the module attribute so these
    stay unit tests (its own behavior is covered by trajectory-builder tests).
  * script/backfill_test_scoring.py   — weighted_test_percentage math (pins the
    CURRENT penalize-on-pass polarity — see tests/test_negative_weight_polarity.py
    for the open polarity dispute; these tests pin behavior, not the spec) and
    the bundle walk + pass_summary rebuild.
  * Gap top-up: backfill_pass_summary (combined=test-only, non-run_N rglob hits,
    backend filter, old-average print, __main__), backfill_run_data (sys.path
    guard, _load_augmenter, __main__), backfill_connector_docs (header/param row
    skips, verbose print, enrich modes, main() CLI, __main__).

__main__ guards are exercised with runpy.run_path(run_name="__main__") so the
`raise SystemExit(main())` lines execute under coverage without a subprocess.
"""
from __future__ import annotations

import importlib.util
import json
import runpy
import sys
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


@pytest.fixture(scope="module")
def bm():
    return _load_script("backfill_bundle_meta.py", "_t_bf_bundle_meta")


@pytest.fixture(scope="module")
def sa():
    return _load_script("backfill_subagent_meta.py", "_t_bf_subagent_meta")


@pytest.fixture(scope="module")
def ts():
    return _load_script("backfill_test_scoring.py", "_t_bf_test_scoring")


@pytest.fixture(scope="module")
def ps():
    return _load_script("backfill_pass_summary.py", "_t_bf_pass_summary2")


@pytest.fixture(scope="module")
def rd():
    return _load_script("backfill_run_data.py", "_t_bf_run_data2")


@pytest.fixture(scope="module")
def cd():
    return _load_script("backfill_connector_docs.py", "_t_bf_connector2")


# ======================================================================
# backfill_bundle_meta.py
# ======================================================================


RUBRIC_SRC = [
    "not-a-dict",
    {"number": "R1", "criterion": "Posts the invoice", "type": "outcome",
     "evaluation_target": "workspace", "importance": "critical"},
    {"criterion": "Writes The  Report", "type": "behavioral",
     "evaluation_target": "trajectory", "importance": ""},
]


def _mk_report(task_dir: Path, items) -> Path:
    run = task_dir / "trajectories" / "m" / "run_1"
    run.mkdir(parents=True, exist_ok=True)
    p = run / "report.json"
    p.write_text(json.dumps({"rubric": items}), encoding="utf-8")
    return p


def test_bm_load_and_norm_edges(bm, tmp_path):
    assert bm._load(tmp_path / "missing.json") is None            # OSError
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert bm._load(bad) is None                                  # JSONDecodeError
    assert bm._norm(None) == ""
    assert bm._norm("  A \n B ") == "a b"


def test_bm_fix_report_non_dict_and_no_rubric_block(bm, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps(["list"]), encoding="utf-8")
    assert bm.fix_report(p, dry_run=False) == (0, 0, "no rubric block")
    p.write_text(json.dumps({"rubric": "not-a-list"}), encoding="utf-8")
    assert bm.fix_report(p, dry_run=False) == (0, 0, "no rubric block")


def test_bm_fix_report_no_rubric_json_ancestor(bm, tmp_path):
    p = _mk_report(tmp_path / "t", [{"number": "R1"}])
    n, total, note = bm.fix_report(p, dry_run=False)
    assert (n, total, note) == (0, 1, "no rubric.json ancestor")


def test_bm_fix_report_rubric_json_not_a_list(bm, tmp_path):
    task = tmp_path / "t"
    p = _mk_report(task, [{"number": "R1"}])
    (task / "rubric.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert bm.fix_report(p, dry_run=False)[2] == "rubric.json not a list"


def test_bm_fix_report_merges_by_number_and_criterion(bm, tmp_path):
    task = tmp_path / "t"
    (task).mkdir()
    (task / "rubric.json").write_text(json.dumps(RUBRIC_SRC), encoding="utf-8")
    items = [
        "not-a-dict",                                             # skipped
        {"number": "R1", "criterion": "", "type": "", "evaluation_target": ""},
        # no number -> falls back to normalized criterion text
        {"criterion": "writes the report", "type": "", "evaluation_target": ""},
        {"number": "R99", "criterion": "unknown"},                # no match
    ]
    p = _mk_report(task, items)
    n, total, note = bm.fix_report(p, dry_run=False)
    assert (n, total) == (2, 4) and note == "src=rubric.json"
    rep = json.loads(p.read_text())
    by_num = rep["rubric"][1]
    assert by_num["type"] == "outcome"
    assert by_num["evaluation_target"] == "workspace"
    assert by_num["importance"] == "critical"
    by_crit = rep["rubric"][2]
    assert by_crit["type"] == "behavioral"
    # src importance empty and item had none -> `if new_imp:` false branch
    assert "importance" not in by_crit
    # Idempotent: second pass sees no diff and does not rewrite.
    assert bm.fix_report(p, dry_run=False)[0] == 0


def test_bm_fix_report_dry_run_does_not_write(bm, tmp_path):
    task = tmp_path / "t"
    task.mkdir()
    (task / "rubric.json").write_text(json.dumps(RUBRIC_SRC), encoding="utf-8")
    p = _mk_report(task, [{"number": "R1", "type": "", "evaluation_target": ""}])
    before = p.read_text()
    assert bm.fix_report(p, dry_run=True)[0] == 1
    assert p.read_text() == before


def _mk_score(run_dir: Path, score: dict, ctrf=None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    sp = run_dir / "score.json"
    sp.write_text(json.dumps(score), encoding="utf-8")
    if ctrf is not None:
        v = run_dir / "task_output" / "logs" / "verifier"
        v.mkdir(parents=True, exist_ok=True)
        (v / "ctrf.json").write_text(json.dumps(ctrf), encoding="utf-8")
    return sp


CTRF = {"results": {"summary": {"tests": 5, "passed": 4, "failed": 1}}}


def test_bm_fix_score_branches(bm, tmp_path):
    # unreadable
    bad = tmp_path / "a" / "score.json"
    bad.parent.mkdir()
    bad.write_text("{oops", encoding="utf-8")
    assert bm.fix_score(bad, dry_run=False) == (False, "unreadable")
    # no ctrf.json
    sp = _mk_score(tmp_path / "b", {"tests_total": 9})
    assert bm.fix_score(sp, dry_run=False)[1] == "no ctrf.json (no deterministic suite ran)"
    # ctrf not a dict -> empty summary
    sp = _mk_score(tmp_path / "c", {"tests_total": 9}, ctrf=["x"])
    assert bm.fix_score(sp, dry_run=False)[1] == "empty ctrf summary"
    # changed -> written
    sp = _mk_score(tmp_path / "d", {"tests_total": 9, "tests_passed": 9, "tests_failed": 0}, ctrf=CTRF)
    changed, note = bm.fix_score(sp, dry_run=False)
    assert changed and note == "4/5 passed, 1 failed"
    got = json.loads(sp.read_text())
    assert (got["tests_total"], got["tests_passed"], got["tests_failed"]) == (5, 4, 1)
    # unchanged on the second pass
    assert bm.fix_score(sp, dry_run=False)[0] is False
    # dry-run: reports changed but does not write
    sp = _mk_score(tmp_path / "e", {"tests_total": 0}, ctrf=CTRF)
    before = sp.read_text()
    assert bm.fix_score(sp, dry_run=True)[0] is True
    assert sp.read_text() == before


def test_bm_main_walks_roots_and_skips_missing(bm, tmp_path, capsys):
    task = tmp_path / "root" / "t"
    task.mkdir(parents=True)
    (task / "rubric.json").write_text(json.dumps(RUBRIC_SRC), encoding="utf-8")
    p = _mk_report(task, [{"number": "R1", "type": "", "evaluation_target": ""}])
    _mk_score(p.parent, {"tests_total": 0}, ctrf=CTRF)
    rc = bm.main([str(tmp_path / "root"), str(tmp_path / "nope")])
    out = capsys.readouterr()
    assert rc == 0
    assert "skip (missing)" in out.err
    assert "meta filled" in out.out and "tests_* ->" in out.out
    # dry-run verb branch
    rc = bm.main(["--dry-run", str(tmp_path / "root")])
    assert rc == 0 and "would update" in capsys.readouterr().out


def test_bm_dunder_main(bm, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill_bundle_meta.py", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_bundle_meta.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# backfill_subagent_meta.py
# ======================================================================


def _mk_run(root: Path, name="run_1", output=None, sessions=True) -> Path:
    run = root / "t" / "trajectories" / "m" / name
    run.mkdir(parents=True, exist_ok=True)
    out = run / "output.json"
    if output is None:
        out.write_text(json.dumps({"meta_info": {}}), encoding="utf-8")
    else:
        out.write_text(output, encoding="utf-8")
    if sessions:
        sd = run / "task_output" / "sessions"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "sessions.json").write_text("{}", encoding="utf-8")
    return run


def test_sa_run_dirs_skips_non_run_dirs(sa, tmp_path):
    _mk_run(tmp_path)
    bad = tmp_path / "t" / "trajectories" / "m" / "notrun"
    bad.mkdir(parents=True)
    (bad / "output.json").write_text("{}", encoding="utf-8")
    assert [d.name for d in sa._run_dirs(tmp_path)] == ["run_1"]


def test_sa_backfill_run_guard_branches(sa, tmp_path):
    run = _mk_run(tmp_path / "a", output="{broken", sessions=True)
    assert sa.backfill_run(run, dry_run=False) == (False, "unreadable output.json")
    run = _mk_run(tmp_path / "b", output=json.dumps(["list"]), sessions=True)
    assert sa.backfill_run(run, dry_run=False) == (False, "output.json not an object")
    run = _mk_run(tmp_path / "c", sessions=False)
    assert sa.backfill_run(run, dry_run=False) == (
        False, "no sessions.json (single-agent or not collected)")


def test_sa_backfill_run_no_subagents_found(sa, tmp_path, monkeypatch):
    run = _mk_run(tmp_path)
    monkeypatch.setattr(sa, "attach_native_subagents",
                        lambda published, sessions_dir, run_dir: {"meta_info": {}})
    assert sa.backfill_run(run, dry_run=False) == (False, "harvester found no sub-agents")


def _fake_attach(published, sessions_dir, run_dir):
    mi = published.setdefault("meta_info", {})
    mi["subagents"] = [{"id": "s1"}, {"id": "s2"}]
    mi["subagent_count"] = 2
    return published


def test_sa_backfill_run_added_refreshed_and_dry(sa, tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "attach_native_subagents", _fake_attach)
    run = _mk_run(tmp_path)
    ok, note = sa.backfill_run(run, dry_run=False)
    assert ok and note == "ADDED (2 sub-agents)"
    assert json.loads((run / "output.json").read_text())["meta_info"]["subagent_count"] == 2
    # roster already present -> refreshed
    ok, note = sa.backfill_run(run, dry_run=False)
    assert ok and note == "already present, refreshed (2 sub-agents)"
    # dry-run leaves the file untouched
    fresh = _mk_run(tmp_path / "dry")
    before = (fresh / "output.json").read_text()
    ok, note = sa.backfill_run(fresh, dry_run=True)
    assert ok and "ADDED" in note
    assert (fresh / "output.json").read_text() == before


def test_sa_main_prints_ok_skip_and_missing_root(sa, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sa, "attach_native_subagents", _fake_attach)
    _mk_run(tmp_path / "root")                     # -> [ok ]
    _mk_run(tmp_path / "root2", sessions=False)    # -> [ -- ]
    rc = sa.main([str(tmp_path / "root"), str(tmp_path / "root2"),
                  str(tmp_path / "missing")])
    out = capsys.readouterr()
    assert rc == 0
    assert "[ok ]" in out.out and "[ -- ]" in out.out
    assert "skip (missing)" in out.err
    assert "updated 1/2 run(s)" in out.out
    rc = sa.main(["--dry-run", str(tmp_path / "root")])
    assert rc == 0 and "(dry-run — no files written)" in capsys.readouterr().out


def test_sa_dunder_main(sa, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill_subagent_meta.py", "--dry-run", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_subagent_meta.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# backfill_test_scoring.py
# ======================================================================


def test_ts_weighted_percentage_math(ts):
    # Positive-weight path with a passed guardrail penalised (CURRENT polarity —
    # behavior pin, not spec endorsement; see module docstring).
    tests = [
        {"weight": 3, "passed": True},
        {"weight": 5, "passed": False},
        {"weight": -5, "passed": True},   # penalised
        {"weight": -3, "passed": False},  # ignored
    ]
    assert ts.weighted_test_percentage(tests) == pytest.approx(0.0)  # (3-5)/8 clamps
    tests[1]["passed"] = True
    assert ts.weighted_test_percentage(tests) == pytest.approx(37.5)  # (8-5)/8
    # No positive weights -> plain pass ratio
    only_neg = [{"weight": -1, "passed": True}, {"weight": -1, "passed": False}]
    assert ts.weighted_test_percentage(only_neg) == pytest.approx(50.0)
    assert ts.weighted_test_percentage([]) == 0.0


def _mk_bundle_report(bundle: Path, *, test_pct=99.9, rubric_pct=40.0) -> Path:
    run = bundle / "task1" / "trajectories" / "opus" / "run_1"
    run.mkdir(parents=True, exist_ok=True)
    rep = {
        "run_index": 1,
        "include_multimodal": False,
        "rubric_weights_percentage": rubric_pct,
        "test_weights_percentage": test_pct,
        "final_reward": 0.0,
        "pytest": {"tests": [
            {"weight": 4, "passed": True},
            {"weight": 4, "passed": False},
            {"weight": -2, "passed": False},
        ]},
    }
    p = run / "report.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    return p


def test_ts_main_missing_bundle_dir(ts, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "nope")])
    assert ts.main() == 1
    assert "bundle dir not found" in capsys.readouterr().err


def test_ts_main_fixes_reports_and_rebuilds_pass_summary(ts, tmp_path, capsys, monkeypatch):
    bundle = tmp_path / "bundle"
    rp = _mk_bundle_report(bundle)
    monkeypatch.setattr(sys, "argv", ["x", str(bundle)])
    assert ts.main() == 0
    out = capsys.readouterr().out
    assert "report.json files changed: 1" in out
    rep = json.loads(rp.read_text())
    assert rep["test_weights_percentage"] == pytest.approx(50.0)   # 4/8
    assert rep["final_reward"] == pytest.approx(45.0)              # (50+40)/2
    summary = json.loads((rp.parent.parent / "pass_summary.json").read_text())
    assert summary["runs"] == 1
    assert summary["average_combined_score"] == pytest.approx(45.0)
    assert summary["per_run"][0]["run_index"] == 1
    # Second run: idempotent (no report change; summary rebuilt identically)
    assert ts.main() == 0
    assert "report.json files changed: 0" in capsys.readouterr().out


def test_ts_dunder_main(ts, tmp_path, monkeypatch):
    bundle = tmp_path / "b2"
    _mk_bundle_report(bundle)
    monkeypatch.setattr(sys, "argv", ["backfill_test_scoring.py", str(bundle)])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_test_scoring.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# Coverage top-up: backfill_pass_summary.py
# ======================================================================


def test_ps_entry_combined_falls_back_to_test_only(ps):
    entry = ps._entry(1, {}, {"tests_total": 3, "tests_passed": 3, "tests_failed": 0,
                              "tests_errored": 0, "tests_skipped": 0, "reward": 0.5})
    assert entry["rubric_reward"] is None
    assert entry["combined_reward"] == 0.5     # combined = test_reward branch


def test_ps_find_model_dirs_skips_files_and_nonmatching(ps, tmp_path):
    model = tmp_path / "t" / "trajectories" / "m"
    (model / "run_1").mkdir(parents=True)
    (model / "run_x").mkdir()                  # name fails RUN_DIR_RE
    (model / "run_2").write_text("")           # a FILE named run_2
    assert [d for d in ps._find_model_dirs(tmp_path)] == [model]


def _ps_tree(root: Path, backend: str) -> Path:
    run = root / backend / "task" / "trajectories" / "m" / "run_1"
    run.mkdir(parents=True)
    (run / "score.json").write_text(json.dumps(
        {"criteria_total": 2, "criteria_passed": 1, "criteria_failed": 1,
         "overall_score": 0.5}), encoding="utf-8")
    return run


def test_ps_main_backend_filter_and_old_average_print(ps, tmp_path, capsys, monkeypatch):
    _ps_tree(tmp_path, "openclaw")
    _ps_tree(tmp_path, "claudecode")           # filtered out by --backend
    # Pre-seed a stale summary so the old->new average line prints.
    stale = tmp_path / "openclaw" / "task" / "trajectories" / "m" / "pass_summary.json"
    stale.write_text(json.dumps({"average_reward": 0.99}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path), "--backend", "openclaw"])
    assert ps.main() == 0
    out = capsys.readouterr().out
    assert "average_reward: 0.99 ->" in out
    assert "rebuilt 1 pass_summary.json" in out


def test_ps_main_skips_model_dir_that_rebuilds_to_none(ps, tmp_path, capsys, monkeypatch):
    """Defensive branch: _find_model_dirs only yields dirs with run_N children,
    so rebuild_model_dir returning None is reachable only via a racing delete —
    simulate it by stubbing the rebuilder."""
    _ps_tree(tmp_path, "openclaw")
    monkeypatch.setattr(ps, "rebuild_model_dir", lambda d: None)
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path)])
    assert ps.main() == 0
    assert "rebuilt 0 pass_summary.json" in capsys.readouterr().out


def test_ps_dunder_main(ps, tmp_path, monkeypatch):
    _ps_tree(tmp_path, "openclaw")
    monkeypatch.setattr(sys, "argv", ["backfill_pass_summary.py", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_pass_summary.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# Coverage top-up: backfill_run_data.py
# ======================================================================


def test_rd_syspath_guard_inserts_repo_root(monkeypatch):
    """Load a fresh copy with REPO_ROOT scrubbed from sys.path so the
    `if str(REPO_ROOT) not in sys.path:` guard takes its True branch."""
    scrubbed = [p for p in sys.path if p != str(REPO_ROOT)]
    monkeypatch.setattr(sys, "path", scrubbed)
    mod = _load_script("backfill_run_data.py", "_t_bf_run_data_syspath")
    assert str(REPO_ROOT) in sys.path
    assert mod.REPO_ROOT == REPO_ROOT


def test_rd_load_augmenter_imports_orchestrator(rd):
    augment = rd._load_augmenter()
    assert callable(augment)
    assert augment.__name__ == "_augment_task_with_mocks"


def test_rd_dunder_main_help(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill_run_data.py", "--help"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_run_data.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# Coverage top-up: backfill_connector_docs.py
# ======================================================================


THIN_SKILL = """# Widget API (Mock)

Base URL in `WIDGET_API_URL`.

| Method | Path |
|--------|------|
| METHOD | /header-echo |
| GET | --- |
| GET | not-absolute |
| GET | `/v1/{id}//widgets` |
| GET | /widgets |
| POST | /widgets |
| DELETE | /widgets/{widget_id} |
"""


def _mk_thin_connector(skills_root: Path, name="widget") -> Path:
    d = skills_root / f"{name}-api-connector"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(THIN_SKILL, encoding="utf-8")
    return d


def _mk_env(env_root: Path, name="widget", port="8123"):
    api = env_root / f"{name}-api"
    api.mkdir(parents=True)
    (api / "service.toml").write_text(
        f'[service]\nname = "{name}-api"\nport = {port}\n'
        f'env_var_name = "WIDGET_API_URL"\n', encoding="utf-8")


def test_cd_parse_skill_skips_header_separator_and_relative_rows(cd, tmp_path):
    d = _mk_thin_connector(tmp_path / "skills")
    info = cd.parse_skill(d / "SKILL.md")
    paths = [p for _, p in info["endpoints"]]
    assert "/header-echo" not in paths        # METHOD header row skipped (110)
    assert "---" not in paths                 # separator row skipped
    assert "not-absolute" not in paths        # relative path skipped
    assert "/v1/{id}//widgets" in paths and "/widgets" in paths


def test_cd_resource_of_skips_empty_and_param_segments(cd):
    assert cd._resource_of("/v1/{id}//widgets") == "widgets"   # param + empty (121)
    assert cd._resource_of("/{only_param}") == "root"


def test_cd_process_connector_verbose_prints(cd, tmp_path, capsys):
    skills = tmp_path / "skills"
    env = tmp_path / "env"
    d = _mk_thin_connector(skills)
    _mk_env(env)
    status = cd.process_connector(d, env, force=False, verbose=True)
    assert status == "written"
    assert "endpoint(s) -> references/ + scripts/" in capsys.readouterr().out


def test_cd_enrich_one_all_branches(cd, tmp_path, capsys):
    live = tmp_path / "live"
    envr = tmp_path / "env"
    live_conn = _mk_thin_connector(live)
    _mk_env(envr)
    assert cd.process_connector(live_conn, envr, force=False, verbose=False) == "written"

    bundle = tmp_path / "bundle" / "task" / "data" / "environment" / "skills"
    # no live counterpart
    orphan = bundle / "ghost-api-connector"
    orphan.mkdir(parents=True)
    assert cd.enrich_one(orphan, live, force=False, dry_run=False, verbose=True) == "skipped-no-live"
    assert "no live connector" in capsys.readouterr().err
    # live exists but has no references/scripts
    thin_live = _mk_thin_connector(live, name="empty")
    b2 = bundle / "empty-api-connector"
    b2.mkdir()
    assert cd.enrich_one(b2, live, force=False, dry_run=False, verbose=False) == "skipped-empty"
    assert thin_live.is_dir()
    # real copy
    b3 = bundle / "widget-api-connector"
    b3.mkdir()
    assert cd.enrich_one(b3, live, force=False, dry_run=False, verbose=True) == "copied"
    assert (b3 / "references").is_dir() and (b3 / "scripts").is_dir()
    # already rich, not forcing
    assert cd.enrich_one(b3, live, force=False, dry_run=False, verbose=False) == "skipped-rich"
    # force + dry_run: reports copied, removes nothing, writes nothing
    marker = b3 / "references" / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    assert cd.enrich_one(b3, live, force=True, dry_run=True, verbose=True) == "copied"
    assert marker.exists()
    # force for real: wipes and recopies
    assert cd.enrich_one(b3, live, force=True, dry_run=False, verbose=False) == "copied"
    assert not marker.exists()


def test_cd_main_generate_bundle_and_error_paths(cd, tmp_path, capsys):
    skills = tmp_path / "skills"
    env = tmp_path / "env"
    _mk_thin_connector(skills)
    _mk_env(env)
    # rich-listed connector is skipped without --include-rich
    (skills / "quickbooks-api-connector").mkdir()
    (skills / "quickbooks-api-connector" / "SKILL.md").write_text(THIN_SKILL, encoding="utf-8")
    # connector without SKILL.md and one with no endpoints
    (skills / "noskill-api-connector").mkdir()
    (skills / "empty-api-connector").mkdir()
    (skills / "empty-api-connector" / "SKILL.md").write_text("# Empty\n", encoding="utf-8")

    # bad skills-root -> rc 2
    assert cd.main(["--skills-root", str(tmp_path / "nope")]) == 2
    # bundle mode with bad bundle-root -> rc 2
    assert cd.main(["--skills-root", str(skills), "--bundle-root", str(tmp_path / "nope")]) == 2
    capsys.readouterr()

    # dry-run generate (verbose): would-write + no-endpoints branches
    rc = cd.main(["--skills-root", str(skills), "--env-root", str(env), "--dry-run", "-v"])
    out = capsys.readouterr().out
    assert rc == 0 and "would-write" in out and "skip-no-endpoints" in out

    # real generate with --only filter (skips others via skip-filter)
    rc = cd.main(["--skills-root", str(skills), "--env-root", str(env), "--only", "widget"])
    out = capsys.readouterr().out
    assert rc == 0 and "written     : 1" in out
    # second pass: skip-exists; then --force + --include-rich regenerates all
    rc = cd.main(["--skills-root", str(skills), "--env-root", str(env), "--only", "widget"])
    assert "exists=1" in capsys.readouterr().out
    rc = cd.main(["--skills-root", str(skills), "--env-root", str(env),
                  "--force", "--include-rich"])
    out = capsys.readouterr().out
    assert rc == 0 and "written     : 2" in out

    # bundle enrich mode end-to-end (dry run then real)
    bundle = tmp_path / "bundle" / "t" / "data" / "environment" / "skills" / "widget-api-connector"
    bundle.mkdir(parents=True)
    rc = cd.main(["--skills-root", str(skills), "--bundle-root",
                  str(tmp_path / "bundle"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "(dry-run: no files written)" in out
    rc = cd.main(["--skills-root", str(skills), "--bundle-root", str(tmp_path / "bundle")])
    out = capsys.readouterr().out
    assert rc == 0 and "copied=1" in out


def test_cd_dunder_main_help(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill_connector_docs.py", "--help"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "backfill_connector_docs.py"), run_name="__main__")
    assert e.value.code == 0
