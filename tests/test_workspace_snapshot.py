from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.workspace_snapshot import (  # noqa: E402
    capture_workspace_snapshot,
    snapshot_mock_data,
    snapshot_persona,
    snapshot_persona_from_path,
    write_workspace_snapshot_json,
)


def _fake_subprocess_result(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = rc
    r.stdout = stdout
    r.stderr = stderr
    return r


def _persona_payload(files: dict[str, str]) -> str:
    parts = []
    for name, body in files.items():
        parts.append(f"<<<WS_FILE>>>/root/{name}<<<WS_FILE_END>>>\n{body}\n<<<WS_FILE_BODY_END>>>\n")
    return "".join(parts)


def test_snapshot_persona_parses_sentinel_payload():
    payload = _persona_payload({
        "AGENTS.md": "# agents\nrules",
        "MEMORY.md": "# memory\nfacts",
    })
    with patch("src.utils.workspace_snapshot.subprocess.run",
               return_value=_fake_subprocess_result(0, payload)):
        out = snapshot_persona("container_id")
    assert out == {
        "AGENTS.md": "# agents\nrules",
        "MEMORY.md": "# memory\nfacts",
    }


def test_snapshot_persona_returns_empty_on_docker_failure():
    with patch("src.utils.workspace_snapshot.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=15)):
        out = snapshot_persona("container_id")
    assert out == {}


def test_snapshot_persona_returns_empty_on_nonzero_exit():
    with patch("src.utils.workspace_snapshot.subprocess.run",
               return_value=_fake_subprocess_result(1, "", "no such container")):
        out = snapshot_persona("container_id")
    assert out == {}


def test_snapshot_persona_from_path_reads_md_files(tmp_path: Path):
    persona = tmp_path / "persona"
    persona.mkdir()
    (persona / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (persona / "MEMORY.md").write_text("memory body", encoding="utf-8")
    (persona / "ignore.txt").write_text("not md", encoding="utf-8")
    out = snapshot_persona_from_path(persona)
    assert out == {"AGENTS.md": "agents body", "MEMORY.md": "memory body"}


def test_snapshot_persona_from_path_returns_empty_for_missing_dir(tmp_path: Path):
    out = snapshot_persona_from_path(tmp_path / "does_not_exist")
    assert out == {}
    assert snapshot_persona_from_path(None) == {}
    assert snapshot_persona_from_path("") == {}


def _make_urlopen_router(responses: dict[str, object]):
    def _opener(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        payload = responses.get(url)
        if payload is None:
            import urllib.error
            raise urllib.error.URLError(f"no canned response for {url}")
        body = json.dumps(payload).encode("utf-8")
        ctx = MagicMock()
        ctx.__enter__ = lambda s: s
        ctx.__exit__ = lambda *args: None
        ctx.read = lambda: body
        return ctx
    return _opener


def test_snapshot_mock_data_iterates_each_api_and_table():
    responses = {
        "http://127.0.0.1:9001/admin/tables": {
            "tables": [{"name": "records"}, "wheelsets"],
            "documents": ["page_main"],
        },
        "http://127.0.0.1:9001/admin/data/records": {"rows": [{"id": 1}, {"id": 2}]},
        "http://127.0.0.1:9001/admin/data/wheelsets": [{"id": "ws-1"}],
        "http://127.0.0.1:9001/admin/doc/page_main": {"title": "hello"},
        "http://127.0.0.1:9002/admin/tables": {"tables": [{"name": "orders"}]},
        "http://127.0.0.1:9002/admin/data/orders": {"rows": []},
    }
    with patch("src.utils.workspace_snapshot.urllib.request.urlopen",
               side_effect=_make_urlopen_router(responses)):
        out = snapshot_mock_data(
            host_api_to_url={
                "airtable": "http://127.0.0.1:9001",
                "muller": "http://127.0.0.1:9002",
            },
            admin_token="tok",
        )
    assert set(out.keys()) == {"airtable", "muller"}
    assert out["airtable"]["records"] == [{"id": 1}, {"id": 2}]
    assert out["airtable"]["wheelsets"] == [{"id": "ws-1"}]
    assert out["airtable"]["_doc:page_main"] == {"title": "hello"}
    assert out["muller"]["orders"] == []


def test_snapshot_mock_data_skips_failing_table_but_keeps_others():
    import urllib.error

    def _router(req, timeout=None):  # noqa: ARG001
        url = req.full_url
        if url.endswith("/admin/tables"):
            body = json.dumps({"tables": [{"name": "good"}, {"name": "bad"}]}).encode("utf-8")
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = lambda *a: None
            ctx.read = lambda: body
            return ctx
        if url.endswith("/admin/data/good"):
            body = json.dumps({"rows": [{"id": 1}]}).encode("utf-8")
            ctx = MagicMock()
            ctx.__enter__ = lambda s: s
            ctx.__exit__ = lambda *a: None
            ctx.read = lambda: body
            return ctx
        raise urllib.error.URLError("server gone")

    with patch("src.utils.workspace_snapshot.urllib.request.urlopen", side_effect=_router):
        out = snapshot_mock_data(
            host_api_to_url={"airtable": "http://127.0.0.1:9001"}, admin_token=None,
        )
    assert out == {"airtable": {"good": [{"id": 1}]}}


def test_snapshot_mock_data_empty_when_no_apis():
    out = snapshot_mock_data(host_api_to_url={}, admin_token=None)
    assert out == {}
    out = snapshot_mock_data(host_api_to_url=None, admin_token=None)  # type: ignore[arg-type]
    assert out == {}


def test_write_workspace_snapshot_json_round_trips_on_disk(tmp_path: Path):
    persona = {"AGENTS.md": "rules"}
    mock = {"airtable": {"records": [{"id": 1}]}}
    p = write_workspace_snapshot_json(
        tmp_path, phase="initial", task_id="task_x",
        persona=persona, mock_data=mock,
    )
    assert p.name == "workspace_snapshot_initial.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["phase"] == "initial"
    assert payload["task_id"] == "task_x"
    assert payload["persona"] == persona
    assert payload["mock_data"] == mock
    assert "captured_at" in payload and payload["captured_at"].startswith("20")


def test_write_workspace_snapshot_json_preserves_null_sections(tmp_path: Path):
    p = write_workspace_snapshot_json(
        tmp_path, phase="final", task_id="task_x",
        persona=None, mock_data=None,
    )
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["persona"] is None
    assert payload["mock_data"] is None


def test_write_workspace_snapshot_json_rejects_bad_phase(tmp_path: Path):
    with pytest.raises(ValueError):
        write_workspace_snapshot_json(
            tmp_path, phase="bogus", task_id="t", persona=None, mock_data=None,
        )


def test_capture_initial_uses_persona_dir_and_writes_file(tmp_path: Path):
    persona = tmp_path / "persona"
    persona.mkdir()
    (persona / "AGENTS.md").write_text("body", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()

    with patch("src.utils.workspace_snapshot.snapshot_mock_data", return_value={"x": {}}):
        out_path = capture_workspace_snapshot(
            run, phase="initial", task_id="t",
            host_api_to_url={"x": "http://127.0.0.1:1"},
            admin_token="tok", persona_dir=persona,
        )
    assert out_path is not None and out_path.name == "workspace_snapshot_initial.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["persona"] == {"AGENTS.md": "body"}
    assert payload["mock_data"] == {"x": {}}


def test_capture_final_uses_docker_exec_for_persona(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()

    payload = _persona_payload({"AGENTS.md": "after"})
    with patch("src.utils.workspace_snapshot.subprocess.run",
               return_value=_fake_subprocess_result(0, payload)), \
         patch("src.utils.workspace_snapshot.snapshot_mock_data",
               return_value={"airtable": {"records": []}}):
        out_path = capture_workspace_snapshot(
            run, phase="final", task_id="task_x",
            host_api_to_url={"airtable": "http://127.0.0.1:9001"},
            admin_token=None,
        )
    assert out_path is not None
    payload_json = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload_json["phase"] == "final"
    assert payload_json["persona"] == {"AGENTS.md": "after"}
    assert payload_json["mock_data"] == {"airtable": {"records": []}}
