"""Unit coverage for script/validate_bundle.py and script/check_injection.py.

validate_bundle is pure-JSON analysis — exercised with synthetic run dirs that
reproduce each failure signature from its 2026-07-06 audit docstring.

check_injection normally spins a real per-task docker mock stack; here every
docker/network/HTTP touchpoint (subprocess.run, start_mock_stack,
wait_for_ports_healthy, get_published_ports, get_network_gateway,
stop_mock_stack, requests.get, InjectScript, InjectApplier) is monkeypatched at
the module attributes so main() runs all its decision branches offline.
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
# script/validate_bundle.py
# ======================================================================


@pytest.fixture(scope="module")
def vb():
    return _load_script("validate_bundle.py", "_t_validate_bundle")


def _amsg(content, ts=None):
    m = {"message": {"role": "assistant", "content": content}}
    if ts:
        m["timestamp"] = ts
    return m


def _tres(text, ts=None):
    m = {"message": {"role": "toolResult",
                     "content": [{"type": "text", "text": text}]}}
    if ts:
        m["timestamp"] = ts
    return m


def test_vb_ts_blocks_texts_helpers(vb):
    assert vb._ts("2026-07-01T00:00:00Z") is not None
    assert vb._ts("garbage") is None
    assert vb._ts(None) is None
    assert vb._blocks({"message": {"content": "notalist"}}) == []
    assert vb._texts(_tres("x")) == ["x"]


def test_vb_check_messages_error_signatures(vb, monkeypatch):
    cap_text = "y" * 199_500                       # inside the exec-cap band
    msgs = [
        "not-a-dict",
        _amsg([]),                                 # empty assistant content
        _amsg([{"type": "toolCall", "name": "exec", "id": "c1"}]),
        _tres(cap_text),                           # paired result, cap fingerprint
        _amsg([{"type": "toolCall", "name": "exec", "id": "c2"}, "raw-block"]),
        _amsg([{"type": "thinking", "thinking": "ends mid wor"}]),  # last msg warn
    ]
    errors, warnings = vb.check_messages(msgs, is_child=False)
    assert any("empty content" in e for e in errors)
    assert any("silent exec-cap" in e for e in errors)
    # c2's next message is not a toolResult -> flagged; c1's IS a result -> not
    assert sum("no following toolResult" in e for e in errors) == 1
    assert any("ends mid-word" in w for w in warnings)
    # clean ending: punctuation-terminated thinking + child success classification
    monkeypatch.setattr(vb, "classify_child_completion",
                        lambda msgs: ("success", "completed"))
    errors, warnings = vb.check_messages(
        [_amsg([{"type": "thinking", "thinking": "done."}])], is_child=True)
    assert errors == [] and warnings == []
    # child non-success classification
    monkeypatch.setattr(vb, "classify_child_completion",
                        lambda msgs: ("aborted", "empty final content"))
    errors, _ = vb.check_messages([_amsg([{"type": "text", "text": "x"}])],
                                  is_child=True)
    assert any("ending classified aborted" in e for e in errors)


def _mk_run(root: Path, name="run_1") -> Path:
    run = root / "t" / "trajectories" / "m" / name
    run.mkdir(parents=True, exist_ok=True)
    return run


def test_vb_check_run_full_battery(vb, tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "classify_child_completion", lambda m: ("success", "ok"))
    # missing output.json
    run = _mk_run(tmp_path / "a")
    errors, _ = vb.check_run(run)
    assert "missing output.json" in errors
    # unreadable output.json short-circuits
    run = _mk_run(tmp_path / "b")
    (run / "output.json").write_text("{bad", encoding="utf-8")
    errors, warnings = vb.check_run(run)
    assert len(errors) == 1 and "unreadable" in errors[0] and warnings == []
    # parent + subagents: unreadable child, meta mismatch, orphan child,
    # spawn_tree count mismatch
    run = _mk_run(tmp_path / "c")
    (run / "output.json").write_text(json.dumps(
        {"messages": [_amsg([{"type": "text", "text": "hi"}],
                            ts="2026-07-01T10:00:00Z")]}), encoding="utf-8")
    sub = run / "subagents"
    sub.mkdir()
    (sub / "00-bad.json").write_text("{oops", encoding="utf-8")
    (sub / "01-child.json").write_text(json.dumps({
        "meta_info": {"message_count": 5},
        "messages": [_amsg([{"type": "text", "text": "child"}],
                           ts="2026-07-01T11:00:00Z")],
    }), encoding="utf-8")
    tree = run / "spawn_tree"
    tree.mkdir()
    (tree / "parent_spawn_tree.txt").write_text(
        "  1. child-a [success] -> sessions/x (4 msgs)\n"
        "  2. child-b [success] -> sessions/y (9 msgs)\n", encoding="utf-8")
    errors, warnings = vb.check_run(run)
    assert any("unreadable" in e for e in errors)
    assert any("meta message_count=5, actual=1" in e for e in errors)
    assert any("spawn_tree lists 2 children, 2 subagent files" not in e for e in errors)
    assert any("outlived parent by 3600s" in w for w in warnings)
    # tree says 2 but only 2 files (bad + child)? sub_files counts *.json = 2 -> match;
    # force a mismatch with an extra child file
    (sub / "02-extra.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    errors, _ = vb.check_run(run)
    assert any("spawn_tree lists 2 children, 3 subagent files" in e for e in errors)
    # subagents present but no spawn tree -> warning
    run2 = _mk_run(tmp_path / "d")
    (run2 / "output.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    (run2 / "subagents").mkdir()
    (run2 / "subagents" / "00.json").write_text(json.dumps({"messages": []}),
                                                encoding="utf-8")
    _, warnings = vb.check_run(run2)
    assert any("no spawn_tree" in w for w in warnings)


def test_vb_main_quiet_strict_and_missing_root(vb, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(vb, "classify_child_completion", lambda m: ("success", "ok"))
    ok_run = _mk_run(tmp_path / "ok")
    (ok_run / "output.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    bad_run = _mk_run(tmp_path / "bad")
    (bad_run / "output.json").write_text("{nope", encoding="utf-8")
    # non-run_N dir ignored by _run_dirs
    stray = tmp_path / "ok" / "t" / "trajectories" / "m" / "notrun"
    stray.mkdir()
    (stray / "output.json").write_text("{}", encoding="utf-8")

    rc = vb.main([str(tmp_path / "ok"), str(tmp_path / "missing")])
    out = capsys.readouterr()
    assert rc == 0 and "[ok  ]" in out.out and "skip (missing)" in out.err
    # quiet hides clean runs
    rc = vb.main(["--quiet", str(tmp_path / "ok")])
    assert "[ok  ]" not in capsys.readouterr().out
    # strict + errors -> exit 1, FAIL printed
    rc = vb.main(["--strict", str(tmp_path / "bad")])
    out = capsys.readouterr().out
    assert rc == 1 and "[FAIL]" in out and "ERROR" in out
    # errors without --strict -> exit 0
    assert vb.main([str(tmp_path / "bad")]) == 0


def test_vb_warn_status_line_and_dunder_main(vb, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(vb, "classify_child_completion", lambda m: ("success", "ok"))
    run = _mk_run(tmp_path / "w")
    (run / "output.json").write_text(json.dumps(
        {"messages": [_amsg([{"type": "thinking", "thinking": "cut mid wor"}])]}),
        encoding="utf-8")
    assert vb.main([str(tmp_path / "w")]) == 0
    assert "[WARN]" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["validate_bundle.py", str(tmp_path / "w")])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "validate_bundle.py"), run_name="__main__")
    assert e.value.code == 0


# ======================================================================
# script/check_injection.py
# ======================================================================


@pytest.fixture(scope="module")
def ci():
    return _load_script("check_injection.py", "_t_check_injection")


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def test_ci_build_overlays_and_admin_get(ci, tmp_path, monkeypatch):
    task = tmp_path / "task"
    (task / "mock_data" / "gmail-api").mkdir(parents=True)
    (task / "mock_data" / "gmail-api" / "emails.csv").write_text("a,b\n")
    (task / "mock_data" / "empty-api").mkdir()          # no files -> skipped
    (task / "mock_data" / "notes.txt").write_text("")   # file, not dir -> skipped
    ov = ci._build_overlays(task)
    assert list(ov) == ["gmail-api"] and "emails.csv" in ov["gmail-api"]
    assert ci._build_overlays(tmp_path / "nomock") == {}

    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(200, {"x": 1}))
    assert ci._admin_get("http://h/", "tok", "/admin/x") == {"x": 1}
    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(500))
    assert ci._admin_get("http://h", "", "/admin/x") is None
    def _boom(url, headers=None, timeout=None):
        raise RuntimeError("net down")
    monkeypatch.setattr(ci.requests, "get", _boom)
    assert ci._admin_get("http://h", "tok", "/admin/x") is None


def test_ci_target_value_shapes(ci, monkeypatch):
    row = {"fields": {"amount": 20, "status": "open"}}
    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(200, row))
    got = ci._target_value("http://h", "t", "invoices", "r1", {"amount": 99})
    assert got == {"amount": 20}
    # nested {"fields": {...}} patch shape
    got = ci._target_value("http://h", "t", "invoices", "r1",
                           {"fields": {"status": "closed"}})
    assert got == {"status": "open"}
    # flat row without fields bag
    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(200, {"amount": 5}))
    assert ci._target_value("http://h", "t", "i", "r", {"amount": 1}) == {"amount": 5}
    # non-dict row
    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(200, ["x"]))
    assert ci._target_value("http://h", "t", "i", "r", {"amount": 1}) is None


class _Stage:
    def __init__(self, name, silent, *, is_seed=False, from_turn=1, to_turn=2):
        self.name = name
        self.silent = silent
        self.is_seed = is_seed
        self.from_turn = from_turn
        self.to_turn = to_turn


class _FakeApplier:
    """Scriptable stand-in for InjectApplier."""
    def __init__(self, *a, **k):
        pass

    def _apply_admin_op(self, api, admin, op):
        return op.get("_rec", {"ok": True, "changed": True, "table": "t",
                               "pk": "p", "status": 200})

    def _resolve_target(self, api, op):
        return op.get("_resolved")

    def _admin_patch(self, api, table, pk, fields):
        return {"ok": not fields.get("_fail"), "status": 200}


def _wire_stack(ci, monkeypatch, *, healthy=True, ports=None, gateways=("10.0.0.1", "172.17.0.1")):
    calls = {"stopped": [], "net_rm": 0}
    monkeypatch.setattr(ci.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="LOGS", stderr=""))
    gw_iter = {"vals": list(gateways)}
    monkeypatch.setattr(ci, "get_network_gateway",
                        lambda net: gw_iter["vals"].pop(0) if gw_iter["vals"] else None)
    monkeypatch.setattr(ci, "start_mock_stack", lambda *a, **k: None)
    monkeypatch.setattr(ci, "wait_for_ports_healthy", lambda *a, **k: healthy)
    monkeypatch.setattr(ci, "get_published_ports",
                        lambda c, p: ports if ports is not None else {8101: 55001})
    monkeypatch.setattr(ci, "stop_mock_stack", lambda c: calls["stopped"].append(c))
    monkeypatch.setattr(ci, "InjectApplier", _FakeApplier)
    return calls


def _mk_ci_task(tmp_path: Path, mutations=True) -> Path:
    task = tmp_path / "TASK_X"
    (task / "inject").mkdir(parents=True)
    md = task / "mock_data" / "gmail-api"
    md.mkdir(parents=True)
    (md / "emails.csv").write_text("a,b\n")
    return task


def test_ci_main_guard_paths(ci, tmp_path, capsys, monkeypatch):
    # no inject dir
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "absent")])
    assert ci.main() == 2
    assert "inject not found" in capsys.readouterr().out
    # overlays exist but service discovery yields nothing usable
    task = _mk_ci_task(tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", str(task)])
    monkeypatch.setattr(ci, "discover_services",
                        lambda env: [{"name": "other-api", "port": 8200},
                                     {"name": "gmail-api", "port": None}])
    assert ci.main() == 2
    assert "no overlaid services" in capsys.readouterr().out


def test_ci_main_unhealthy_and_no_host_ports(ci, tmp_path, capsys, monkeypatch):
    task = _mk_ci_task(tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", str(task)])
    monkeypatch.setattr(ci, "discover_services",
                        lambda env: [{"name": "gmail-api", "port": 8101}])
    monkeypatch.setattr(ci, "InjectScript",
                        types.SimpleNamespace(load=lambda p: types.SimpleNamespace(stages=[])))
    calls = _wire_stack(ci, monkeypatch, healthy=False)
    assert ci.main() == 1
    out = capsys.readouterr().out
    assert "never became healthy" in out and "LOGS" in out
    assert calls["stopped"], "stack must be stopped in the finally block"

    calls = _wire_stack(ci, monkeypatch, healthy=True, ports={})
    assert ci.main() == 1
    assert "no host ports resolved" in capsys.readouterr().out


def test_ci_main_full_pass_and_partial(ci, tmp_path, capsys, monkeypatch):
    task = _mk_ci_task(tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", str(task)])
    monkeypatch.setattr(ci, "discover_services",
                        lambda env: [{"name": "gmail-api", "port": 8101}])
    monkeypatch.setattr(ci.requests, "get",
                        lambda url, headers=None, timeout=None: _Resp(
                            200, {"fields": {"amount": 1}}))

    # PASS scenario: one admin op (ok+changed, with pk + matched) + one resolver
    # op that patches ok (before/after computed from the same stub -> NO-CHANGE
    # is avoided by making before/after differ via a mutating stub).
    vals = iter([{"amount": 1}, {"amount": 2}])
    monkeypatch.setattr(ci, "_target_value", lambda *a, **k: next(vals, {"amount": 2}))
    stages = [
        _Stage("seed", [], is_seed=True),
        _Stage("quiet", []),          # silent empty -> skipped
        _Stage("mut", [
            {"id": "m1", "service": "gmail-api",
             "admin": {"op": "patch"},
             "_rec": {"ok": True, "changed": True, "table": "inv", "pk": "r1",
                      "matched": 1, "before": {"a": 1}, "after": {"a": 2},
                      "status": 200}},
            {"id": "m2", "api": "gmail-api",
             "_resolved": ("inv", "r2", {"amount": 99})},
        ]),
    ]
    monkeypatch.setattr(ci, "InjectScript",
                        types.SimpleNamespace(load=lambda p: types.SimpleNamespace(stages=stages)))
    _wire_stack(ci, monkeypatch)
    assert ci.main() == 0
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out and "[APPLIED] m1" in out and "matched=1" in out

    # PARTIAL scenario: admin op not-ok, resolver unresolved, patch-fail,
    # and a no-change admin op — plus an op whose api has no base url.
    stages = [
        _Stage("mut", [
            {"id": "a-bad", "service": "gmail-api", "admin": {"op": "x"},
             "_rec": {"ok": False, "status": 500, "document": "doc1"}},
            {"id": "a-nochange", "service": "gmail-api", "admin": {"op": "x"},
             "_rec": {"ok": True, "changed": False, "table": "inv", "status": 200}},
            {"id": "unresolved", "api": "gmail-api", "path": "/x", "_resolved": None},
            {"id": "patch-fail", "api": "gmail-api",
             "_resolved": ("inv", "r9", {"_fail": True})},
            {"id": "no-base", "api": "unknown-api", "_resolved": None},
        ]),
    ]
    monkeypatch.setattr(ci, "InjectScript",
                        types.SimpleNamespace(load=lambda p: types.SimpleNamespace(stages=stages)))
    monkeypatch.setattr(ci, "_target_value", lambda *a, **k: {"amount": 1})
    _wire_stack(ci, monkeypatch)
    assert ci.main() == 0
    out = capsys.readouterr().out
    assert "RESULT: PARTIAL" in out
    assert "[UNRESOLVED] a-bad" in out or "UNRESOLVED" in out
    assert "[NO-CHANGE] a-nochange" in out
    assert "[UNRESOLVED] unresolved" in out
    assert "[PATCH-FAIL] patch-fail" in out


def test_ci_default_task_argv_and_dunder_main(ci, tmp_path, capsys, monkeypatch):
    # default argv path (no arg) -> input/LAYLA...; run from tmp so it's absent
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_injection.py"])
    assert ci.main() == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPT_DIR / "check_injection.py"), run_name="__main__")
    assert e.value.code == 2
