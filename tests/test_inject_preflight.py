"""Calibration for the pre-trajectory task gate.

Each case is a miniature fleet plus a miniature task, built on disk, because
the gate's whole claim is that it imports real services and replays real ops.
A fixture that stubbed the store would test the stub.

The shapes are the incidents, reduced: an upsert whose keys no getter names
(the willie linkedin counters), a patch to a live column the getter does not
serve, a patch aimed at a row the agent is supposed to create first, and a
required service whose JSON list is destroyed by its own csv coercer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.utils.inject_preflight import FATAL, WARN, gate_task  # noqa: E402

_WIDGET_DATA = '''
from pathlib import Path
import sys as _sys

DATA_DIR = Path(__file__).parent
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store, opt_csv_list, read_seed_with_ctx  # noqa: E402

_store = get_store("widget-api")


def _coerce(rows):
    out = []
    for r in rows:
        row = {k: v for k, v in r.items() if not str(k).startswith("__")}
        row["tags"] = opt_csv_list(r, "tags", sep=";")
        out.append(row)
    return out


_store.register(
    "widgets", "id",
    lambda: _coerce(read_seed_with_ctx(DATA_DIR / "widgets.csv", "widget-api", "widgets")),
)
_store.eager_load()


def get_widget(widget_id):
    row = _store.table("widgets").get(widget_id)
    if row is None:
        return {"error": "not found"}
    return {"id": row["id"], "name": row["name"], "tags": row["tags"]}


def list_widgets():
    return [get_widget(r["id"]) for r in _store.table("widgets").rows()]
'''

_WIDGET_SERVER = '''
from fastapi import FastAPI

from widget_data import get_widget, list_widgets

app = FastAPI()


@app.get("/widgets")
def _widgets():
    return {"elements": list_widgets()}


@app.get("/widgets/{widget_id}")
def _widget(widget_id: str):
    return get_widget(widget_id)
'''

# Seeds a genuine JSON list into a column the module reads with the STRICT csv
# coercer, which still stringifies what it is handed. That is the corruption
# the fleet shipped three times: ["alpha", "beta"] becomes "['alpha', 'beta']"
# split on a comma, and the members are destroyed in place.
_GADGET_DATA = '''
from pathlib import Path
import sys as _sys

DATA_DIR = Path(__file__).parent
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store, read_seed_with_ctx, strict_csv_list  # noqa: E402

_store = get_store("gadget-api")


def _coerce(rows):
    out = []
    for r in rows:
        row = {k: v for k, v in r.items() if not str(k).startswith("__")}
        row["owners"] = strict_csv_list(r, "owners")
        out.append(row)
    return out


_store.register(
    "gadgets", "id",
    lambda: _coerce(read_seed_with_ctx(DATA_DIR / "gadgets.json", "gadget-api", "gadgets")),
)
_store.eager_load()


def get_gadget(gadget_id):
    row = _store.table("gadgets").get(gadget_id)
    return row if row is not None else {"error": "not found"}
'''

_GADGET_SERVER = '''
from fastapi import FastAPI

from gadget_data import get_gadget

app = FastAPI()


@app.get("/gadgets/{gadget_id}")
def _gadget(gadget_id: str):
    return get_gadget(gadget_id)
'''


def _write_service(env: Path, api: str, port: int, data: str, server: str) -> Path:
    svc = env / api
    svc.mkdir(parents=True)
    (svc / "service.toml").write_text(
        f'[service]\nname = "{api}"\nport = {port}\n'
        f'env_var_name = "{api.upper().replace("-", "_")}_URL"\n'
        'healthcheck_path = "/health"\n')
    (svc / f"{api[:-len('-api')].replace('-', '_')}_data.py").write_text(data)
    (svc / "server.py").write_text(server)
    connector = env / "skills" / f"{api}-connector"
    connector.mkdir(parents=True)
    (connector / "SKILL.md").write_text(f"# {api}\n")
    return svc


@pytest.fixture()
def fleet(tmp_path: Path) -> Path:
    """A two-service ``environment/`` with the real store infrastructure."""
    env = tmp_path / "environment"
    env.mkdir()
    for infra in ("_mutable_store.py", "admin_plane.py", "tracking_middleware.py"):
        (env / infra).write_text((REPO / "environment" / infra).read_text())
    widget = _write_service(env, "widget-api", 9101, _WIDGET_DATA, _WIDGET_SERVER)
    (widget / "widgets.csv").write_text(
        "id,name,tags,status\nw-1,Alpha,red;blue,live\nw-2,Beta,green,live\n")
    gadget = _write_service(env, "gadget-api", 9102, _GADGET_DATA, _GADGET_SERVER)
    (gadget / "gadgets.json").write_text(
        json.dumps([{"id": "g-1", "owners": ["alpha", "beta"]}]))
    return env


def _task(root: Path, required, ops, overlays=None) -> Path:
    """A miniature bundle: one required api list, one stage, one op set."""
    task = root / "task"
    (task / "inject" / "stage1").mkdir(parents=True)
    task.joinpath("task.yaml").write_text(
        f"task_type: unit\nsystem_prompt: \"\"\n"
        f"required_apis: [{', '.join(required)}]\ndistractor_apis: []\n")
    task.joinpath("rubric.json").write_text('{"criteria": []}')
    task.joinpath("prompts.txt").write_text("--- TURN T0 ---\nhello\n")
    task.joinpath("prompts.json").write_text(json.dumps(
        {"task_id": "unit", "turns": [{"timestamp": "2026-10-19T09:00:00-04:00",
                                       "message": "hello"}]}))
    task.joinpath("inject", "stage1", "mutations.json").write_text(json.dumps({
        "stage_name": "stage1",
        "applies_between_turns": ["T0", "T1"],
        "applied_at_local_time": "2026-10-19T10:00:00-04:00",
        "mutations": {"silent": ops, "loud": [], "filesystem": []},
    }))
    for api, files in (overlays or {}).items():
        overlay = task / "mock_data" / api
        overlay.mkdir(parents=True)
        for name, body in files.items():
            (overlay / name).write_text(body)
    return task


def _upsert(op_id, row):
    return {"id": op_id, "service": "widget-api",
            "admin": {"op": "upsert", "table": "widgets", "pk_field": "id", "row": row}}


def _patch(op_id, pk, set_):
    return {"id": op_id, "service": "widget-api",
            "admin": {"op": "patch", "table": "widgets", "pk": pk, "set": set_}}


def _kinds(report, severity):
    return {f.kind for f in report.findings if f.severity == severity}


def test_clean_task_passes_every_gate(fleet, tmp_path):
    task = _task(tmp_path, ["widget-api"], [_patch("swap_name", "w-1", {"name": "Alpha Mk2"})])
    report = gate_task(task, environment_dir=fleet)
    assert report.ok, report.summary()
    assert report.findings == ()
    assert report.ops == 1


def test_orphan_keys_beside_a_served_payload_are_named_without_blocking(fleet,
                                                                        tmp_path):
    """The willie linkedin shape: an upsert carrying counters no getter names.

    The post itself arrives — id, name and tags all serve — and only the three
    engagement counters are dropped. The dead keys have to be NAMED, because an
    author who meant them to land needs to know they did not; refusing the task
    over them would refuse a scenario the agent can observe in full.
    """
    task = _task(tmp_path, ["widget-api"], [_upsert("loud_partner_post", {
        "id": "w-9", "name": "Consortium", "tags": "red",
        "like_count": "12", "comment_count": "1", "share_count": "1",
    })])
    report = gate_task(task, environment_dir=fleet)
    assert report.ok, report.summary()
    assert _kinds(report, WARN) == {"SERVES-WITH-ORPHAN"}
    finding = report.warnings[0]
    assert "loud_partner_post" in finding.subject
    for orphan in ("like_count", "comment_count", "share_count"):
        assert orphan in finding.reason


def test_an_upsert_whose_whole_payload_is_orphaned_is_still_fatal(fleet, tmp_path):
    """Nothing survives the orphan list, so nothing reaches the agent."""
    task = _task(tmp_path, ["widget-api"], [_upsert("loud_counters_only", {
        "id": "w-9", "like_count": "12", "comment_count": "1",
    })])
    report = gate_task(task, environment_dir=fleet)
    assert not report.ok
    assert _kinds(report, FATAL) == {"LANDS-BUT-INVISIBLE"}
    assert "loud_counters_only" in report.fatal[0].subject


def test_write_to_an_unserved_column_is_fatal(fleet, tmp_path):
    """``status`` is a live column, so the vocabulary accepts it — but no getter
    reads it, so the agent never sees the change."""
    task = _task(tmp_path, ["widget-api"],
                 [_patch("retire_widget", "w-1", {"status": "archived"})])
    report = gate_task(task, environment_dir=fleet)
    assert _kinds(report, FATAL) == {"LANDS-BUT-INVISIBLE"}
    assert "retire_widget" in report.fatal[0].subject


def test_missing_table_is_fatal_not_reported_as_absent_state(fleet, tmp_path):
    op = {"id": "typo_table", "service": "widget-api",
          "admin": {"op": "patch", "table": "widgetz", "pk": "w-1", "set": {"name": "x"}}}
    task = _task(tmp_path, ["widget-api"], [op])
    report = gate_task(task, environment_dir=fleet)
    assert _kinds(report, FATAL) == {"TABLE-MISSING"}


def test_agent_created_target_warns_and_never_fails(fleet, tmp_path):
    """A row neither the seeds nor an earlier stage carries is a scenario: the
    agent is expected to create it before the op fires."""
    task = _task(tmp_path, ["widget-api"],
                 [_patch("edit_agent_row", "w-created-by-agent", {"name": "Later"})])
    report = gate_task(task, environment_dir=fleet)
    assert report.ok, report.summary()
    assert "NEEDS-RUNTIME" in _kinds(report, WARN)


def test_state_accumulates_across_stages(fleet, tmp_path):
    """A patch of a row an earlier op created is valid, and only a cumulative
    replay can see that."""
    task = _task(tmp_path, ["widget-api"], [
        _upsert("create_widget", {"id": "w-3", "name": "Gamma", "tags": "red",
                                  "status": "live"}),
        _patch("rename_widget", "w-3", {"name": "Gamma Mk2"}),
    ])
    report = gate_task(task, environment_dir=fleet)
    assert report.ok, report.summary()
    assert report.ops == 2


def test_required_service_with_garbled_seeds_is_fatal(fleet, tmp_path):
    task = _task(tmp_path, ["gadget-api"], [])
    report = gate_task(task, environment_dir=fleet)
    assert _kinds(report, FATAL) == {"SEED-COERCION-LOSS"}
    assert "owners" in report.fatal[0].subject


def test_task_overlay_is_what_gets_judged_not_the_pristine_seeds(fleet, tmp_path):
    """The op targets a row only the TASK seeds. Judged against the fleet's own
    widgets.csv it would read as a missing target."""
    task = _task(tmp_path, ["widget-api"],
                 [_patch("edit_task_row", "w-7", {"name": "Task Row Mk2"})],
                 overlays={"widget-api": {
                     "widgets.csv": "id,name,tags,status\nw-7,Task Row,red,live\n"}})
    report = gate_task(task, environment_dir=fleet)
    assert report.ok, report.summary()
    assert report.findings == ()


def test_missing_connector_skill_is_fatal_for_a_required_api(fleet, tmp_path):
    import shutil

    shutil.rmtree(fleet / "skills" / "widget-api-connector")
    task = _task(tmp_path, ["widget-api"], [])
    report = gate_task(task, environment_dir=fleet)
    assert _kinds(report, FATAL) == {"CONNECTOR-MISSING"}


def test_required_service_absent_from_the_catalog_is_fatal(fleet, tmp_path):
    task = _task(tmp_path, ["pruned-api"], [])
    report = gate_task(task, environment_dir=fleet)
    assert _kinds(report, FATAL) == {"SERVICE-NOT-IN-CATALOG"}


def test_unparseable_rubric_is_named_at_the_gate(fleet, tmp_path):
    task = _task(tmp_path, ["widget-api"], [])
    (task / "rubric.json").write_text("{not json")
    report = gate_task(task, environment_dir=fleet)
    assert "RUBRIC-UNPARSEABLE" in _kinds(report, FATAL)


def test_prompts_json_without_its_companion_txt_is_fatal(fleet, tmp_path):
    task = _task(tmp_path, ["widget-api"], [])
    (task / "prompts.txt").unlink()
    report = gate_task(task, environment_dir=fleet)
    assert "PROMPTS-PAIR-INCOMPLETE" in _kinds(report, FATAL)


def test_explicit_required_list_overrides_the_declaration(fleet, tmp_path):
    """The launcher mounts a resolved list; the gate must judge THAT one."""
    task = _task(tmp_path, ["widget-api"], [])
    report = gate_task(task, required_apis=["pruned-api"], environment_dir=fleet)
    assert "SERVICE-NOT-IN-CATALOG" in _kinds(report, FATAL)


def test_report_stamp_records_a_bypass_permanently(fleet, tmp_path):
    task = _task(tmp_path, ["widget-api"],
                 [_patch("retire_widget", "w-1", {"status": "archived"})])
    report = gate_task(task, environment_dir=fleet)
    stamp = report.stamp("bypassed")
    assert stamp["status"] == "bypassed"
    assert stamp["findings"] and stamp["findings"][0]["kind"] == "LANDS-BUT-INVISIBLE"
    assert gate_task(_task(tmp_path / "clean", ["widget-api"], []),
                     environment_dir=fleet).stamp("passed") .get("findings") is None
