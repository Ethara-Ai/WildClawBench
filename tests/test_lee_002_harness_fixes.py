"""Regression tests for the LEE_002 harness fixes (H1 + H3 + H4 + harness_health).

Bundled per the precedent set by ``test_skoll_delivery_fixes.py``. Each suite
locks one verified root cause from the LEE_002 0.1667 reward post-mortem so the
fix cannot silently regress:

* ``TestH1AFilesystemRouting``  — bucket classifier MUST route ops with
  ``service="filesystem"`` to the fs bucket even when ``silent=true``; the
  defensive guard in ``_apply_api_mutation`` MUST divert filesystem ops away
  from the admin-plane path; ``_apply_filesystem`` MUST unwrap the Talos
  ``op.fs.{op,dest,source}`` envelope.
* ``TestH1BHttpEnvelope``       — ``_extract_fields`` MUST read ``op.http.body``;
  ``_extract_key_from_op`` MUST extract the URL tail VERBATIM (NO ``rec`` strip)
  for Talos http envelopes; ``_SERVICE_RESOLUTION`` MUST carry the woocommerce +
  SKU/RecordID/sku keys the LEE_002 stages depend on.
* ``TestH3TrackingMiddleware``  — ``/admin/*`` requests MUST NOT appear in
  ``/audit/summary`` — distractor tests check ``len(_business_endpoints(URL))>0``
  and admin-plane bookkeeping leaking into the summary is what cost LEE_002
  10 penalty points.
* ``TestH4AgentOutputDir``      — ``execute_tests`` MUST accept the
  ``agent_output_dir`` kwarg AND emit ``-e AGENT_OUTPUT_DIR=/agent_outputs``
  + a read-only mount when given a real directory. The ``run_batch`` call site
  MUST pass it.
* ``TestHarnessHealthSummary``  — ``summarize_inject_timeline`` MUST honour the
  jsonl record shape (``inject.fs`` / ``inject.api``, ``ok``, ``status``,
  ``reason``) and ignore lifecycle records.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.utils.inject_director import (  # noqa: E402
    InjectApplier,
    _SERVICE_RESOLUTION,
    _coerce_mutation_buckets,
)
from src.utils.harness_health import summarize_inject_timeline  # noqa: E402


def _make_applier(tmp_path: Path) -> InjectApplier:
    fs_root = tmp_path / "ws"
    fs_root.mkdir(parents=True, exist_ok=True)
    inject_root = tmp_path / "inject"
    inject_root.mkdir(parents=True, exist_ok=True)

    def _copy_hook(src, dst, mkdir=False):
        target = fs_root / dst
        if mkdir:
            target.mkdir(parents=True, exist_ok=True)
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        if src is None:
            target.touch()
            return True
        import shutil
        shutil.copy(str(src), str(target))
        return True

    return InjectApplier(
        host_api_to_url={"airtable-api": "http://x", "woocommerce-api": "http://y"},
        admin_token=None,
        timeline_path=tmp_path / "timeline.jsonl",
        inject_root=inject_root,
        copy_into_workspace=_copy_hook,
        replay_loud=False,
    )


def _make_minimal_applier() -> InjectApplier:
    """Applier with no fs hook — for tests that only exercise field/key extraction."""
    return InjectApplier(
        host_api_to_url={"airtable-api": "http://x", "woocommerce-api": "http://y"},
        admin_token=None,
        timeline_path=Path("/tmp/_wcb_test_timeline.jsonl"),
    )


class _StubStage:
    def __init__(self, op):
        self.seed_filesystem = []
        self.filesystem = [op]
        self.silent = []
        self.loud = []
        self.name = "stub"
        self.source = "/dev/null"


class TestH1AFilesystemRouting:
    def test_filesystem_op_with_silent_flag_routes_to_fs_bucket(self):
        ops = [
            {"id": "SM-01", "silent": True, "service": "filesystem",
             "fs": {"op": "patch_csv", "dest": "data/x.csv"}},
            {"id": "SM-02", "silent": True, "service": "woocommerce-api",
             "http": {"method": "PUT", "url": "http://w/products/1"}},
        ]
        fs, loud, silent = _coerce_mutation_buckets(ops)
        ids_in = lambda bucket: [o["id"] for o in bucket]
        assert ids_in(fs) == ["SM-01"], "filesystem service must land in fs bucket even with silent=true"
        assert ids_in(silent) == ["SM-02"], "API silent mutation must remain in silent bucket"
        assert ids_in(loud) == []

    def test_apply_api_mutation_diverts_filesystem_ops(self, tmp_path):
        applier = _make_applier(tmp_path)
        op = {"id": "CM-01", "service": "filesystem",
              "fs": {"op": "mkdir", "dest": "newdir"}}
        rec = applier._apply_api_mutation(op, _StubStage(op), turn_index=0, silent=True)
        assert rec.get("status") == "mkdir", (
            f"defensive guard must route filesystem op to _apply_filesystem (got {rec})"
        )
        assert rec.get("action") == "mkdir"
        assert "no admin URL" not in (rec.get("reason") or "")

    def test_apply_filesystem_unwraps_talos_envelope_mkdir(self, tmp_path):
        applier = _make_applier(tmp_path)
        op = {"id": "TR-01", "service": "filesystem",
              "fs": {"op": "mkdir", "dest": "data/new"}}
        rec = applier._apply_filesystem(op, _StubStage(op))
        assert rec.get("ok") is True, f"mkdir on Talos envelope failed: {rec}"
        assert rec.get("status") == "mkdir"
        assert (tmp_path / "ws" / "data" / "new").is_dir()

    def test_apply_filesystem_diagnoses_patch_csv_unsupported(self, tmp_path):
        applier = _make_applier(tmp_path)
        op = {"id": "SM-01", "service": "filesystem",
              "fs": {"op": "patch_csv", "dest": "data/x.csv",
                     "rows": [{"row_match": {"lot_id": "L1"}, "set": {"qty": 5}}]}}
        rec = applier._apply_filesystem(op, _StubStage(op))
        assert rec.get("ok") is False
        assert (rec.get("status") or "").lower() == "unsupported"
        assert "patch_csv" in (rec.get("reason") or "").lower()
        assert rec.get("rows") == 1


class TestH1BHttpEnvelope:
    def test_service_resolution_carries_required_keys(self):
        airtable_prefixes, airtable_keys = _SERVICE_RESOLUTION["airtable-api"]
        assert any(k in airtable_keys for k in ("SKU", "sku"))
        assert any(k in airtable_keys for k in ("record_id", "RecordID"))

        wc_prefixes, wc_keys = _SERVICE_RESOLUTION["woocommerce-api"]
        assert "sku" in wc_keys or "SKU" in wc_keys
        assert wc_prefixes

    def test_extract_fields_reads_http_body(self):
        applier = _make_minimal_applier()
        op = {"id": "SM-03", "service": "airtable-api",
              "http": {"method": "PATCH",
                       "url": "http://x/v0/app/tblInventory/recInvESY00000001",
                       "body": {"fields": {"Count": 52, "UpdateReason": "manual"}}}}
        fields = applier._extract_fields(op)
        assert isinstance(fields, dict)
        assert fields.get("Count") == 52
        assert fields.get("UpdateReason") == "manual"

    def test_extract_key_from_op_strips_template_and_preserves_rec_prefix(self):
        applier = _make_minimal_applier()
        op = {"id": "SM-03",
              "http": {"url": "{AIRTABLE_API_URL}/v0/appNRC25090000001/tblInventory/recInvESY00000001"}}
        key = applier._extract_key_from_op(op)
        assert key == "recInvESY00000001", (
            f"http envelope key must be the URL tail VERBATIM (no rec-prefix strip), got {key!r}"
        )

    def test_extract_key_from_op_numeric_wc_id(self):
        applier = _make_minimal_applier()
        op = {"id": "SM-02",
              "http": {"url": "{WOOCOMMERCE_API_URL}/wp-json/wc/v3/products/2501"}}
        key = applier._extract_key_from_op(op)
        assert key == "2501"


class TestH3TrackingMiddleware:
    def _build_app(self):
        fastapi = pytest.importorskip("fastapi")
        sys.path.insert(0, str(ROOT / "environment"))
        import tracking_middleware as tm  # noqa: WPS433
        tm._request_log.clear()
        app = fastapi.FastAPI()
        tm.install_tracker(app)

        @app.get("/products")
        def _products():
            return {"items": []}

        @app.get("/admin/tables")
        def _admin_tables():
            return {"tables": ["a"]}

        @app.get("/admin/data/a")
        def _admin_data():
            return {"rows": []}

        return app, tm

    def test_admin_traffic_does_not_leak_into_audit_summary(self):
        starlette = pytest.importorskip("starlette.testclient")
        app, tm = self._build_app()
        client = starlette.TestClient(app)

        client.get("/admin/tables")
        client.get("/admin/data/a")
        client.get("/health")
        client.get("/audit/summary")

        summary = client.get("/audit/summary").json()
        endpoints = summary.get("endpoints", {})
        for path in endpoints:
            assert not path.endswith(" /admin/tables"), endpoints
            assert not path.endswith(" /admin/data/a"), endpoints
            assert not path.endswith(" /health"), endpoints
            assert "/audit/summary" not in path, endpoints
        assert summary["total_requests"] == 0, (
            f"only admin/audit/health traffic was sent — total_requests must be 0, got {summary}"
        )

    def test_business_endpoint_still_logged(self):
        starlette = pytest.importorskip("starlette.testclient")
        app, tm = self._build_app()
        client = starlette.TestClient(app)

        client.get("/products")
        summary = client.get("/audit/summary").json()
        keys = list(summary.get("endpoints", {}).keys())
        assert any(k.endswith(" /products") for k in keys), (
            f"business endpoint must still be tracked: {keys}"
        )


class TestH4AgentOutputDir:
    def test_execute_tests_signature_accepts_agent_output_dir(self):
        from src.utils.test_executor import execute_tests
        sig = inspect.signature(execute_tests)
        assert "agent_output_dir" in sig.parameters
        param = sig.parameters["agent_output_dir"]
        assert param.default is None, "agent_output_dir must default to None for back-compat"

    def test_run_batch_call_site_passes_artifacts_dir(self):
        text = (ROOT / "eval" / "run_batch.py").read_text(encoding="utf-8")
        assert "agent_output_dir=artifacts_dir" in text, (
            "eval/run_batch.py must pass agent_output_dir to execute_tests"
        )
        assert 'output_dir / "task_output" / "artifacts"' in text, (
            "artifacts_dir must resolve to task_output/artifacts"
        )

    def test_execute_tests_mounts_agent_output_dir(self, tmp_path, monkeypatch):
        import src.utils.test_executor as te

        ws = tmp_path / "ws"; ws.mkdir()
        ao = tmp_path / "ao"; ao.mkdir()
        (ao / "report.md").write_text("ok", encoding="utf-8")

        captured = {}

        class _FakeProc:
            stdout = json.dumps({"results": {}, "collected": []})
            stderr = ""
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(te.subprocess, "run", _fake_run)

        te.execute_tests(
            test_code="def test_noop(): assert True",
            test_weights_json="{}",
            workspace_dir=ws,
            agent_output_dir=ao,
            timeout=5,
        )
        cmd = captured["cmd"]
        joined = " ".join(cmd)
        assert "/agent_outputs:ro" in joined, f"agent_output_dir must be mounted ro, cmd={cmd}"
        assert "AGENT_OUTPUT_DIR=/agent_outputs" in joined, (
            f"AGENT_OUTPUT_DIR env must be exported into container, cmd={cmd}"
        )

    def test_execute_tests_omits_mount_when_agent_output_dir_missing(self, tmp_path, monkeypatch):
        import src.utils.test_executor as te

        ws = tmp_path / "ws"; ws.mkdir()

        captured = {}

        class _FakeProc:
            stdout = json.dumps({"results": {}, "collected": []})
            stderr = ""
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(te.subprocess, "run", _fake_run)

        te.execute_tests(
            test_code="def test_noop(): assert True",
            test_weights_json="{}",
            workspace_dir=ws,
            agent_output_dir=None,
            timeout=5,
        )
        joined = " ".join(captured["cmd"])
        assert "/agent_outputs" not in joined
        assert "AGENT_OUTPUT_DIR" not in joined


class TestHarnessHealthSummary:
    def _write_timeline(self, tmp_path: Path, records):
        p = tmp_path / "inject_timeline.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return p

    def test_missing_file_returns_zero_summary(self, tmp_path):
        out = summarize_inject_timeline(tmp_path / "does_not_exist.jsonl")
        assert out["inject"]["total"] == 0
        assert out["failed_ids"] == []

    def test_counts_fs_and_api_buckets(self, tmp_path):
        p = self._write_timeline(tmp_path, [
            {"type": "inject.seed.start"},
            {"type": "inject.fs", "id": "TR-01", "ok": True, "action": "place_file"},
            {"type": "inject.fs", "id": "SM-01", "ok": False,
             "status": "unsupported", "reason": "patch_csv not implemented"},
            {"type": "inject.api", "id": "SM-02", "ok": False,
             "status": "unresolved", "reason": "no row matched key"},
            {"type": "inject.api", "id": "SM-03", "ok": True},
            {"type": "inject.stage.applied"},
            {"type": "inject.api", "id": "CM-02", "ok": False,
             "status": "skipped", "reason": "expected_status check failed"},
        ])
        out = summarize_inject_timeline(p)
        assert out["inject"]["total"] == 5
        assert out["fs"] == {"total": 2, "ok": 1, "failed": 1}
        assert out["api"] == {"total": 3, "ok": 1, "failed": 2}
        assert out["inject"]["unresolved"] == 1
        assert out["inject"]["unsupported"] == 1
        assert out["inject"]["skipped"] == 1
        ids = {f["id"]: f for f in out["failed_ids"]}
        assert set(ids) == {"SM-01", "SM-02", "CM-02"}
        assert ids["SM-02"]["status"] == "unresolved"

    def test_malformed_lines_are_skipped(self, tmp_path):
        p = tmp_path / "inject_timeline.jsonl"
        p.write_text("not-json\n{\"type\": \"inject.fs\", \"ok\": true}\n", encoding="utf-8")
        out = summarize_inject_timeline(p)
        assert out["fs"]["total"] == 1
        assert out["fs"]["ok"] == 1
