"""End-to-end smoke test for the drift plane.

Spins up the monday-api FastAPI app via TestClient with MOCK_ADMIN_ENABLED=1
and verifies that admin-plane mutations are reflected in subsequent reads
through the public API. Run with:

    pytest tests/test_drift_plane_smoke.py -v

monday-api replaces stripe-api, which left in the newreq convergence. It is the
service src/utils/drift_director.py documents its own worked example against
(`table: items`, `pk: item-1001`, `fields: {name: ...}`), so the smoke test and
the director's docstring now exercise the same route.

The items table is registered under `item_id`, not `id` -- the admin plane keys
rows by the table's REGISTERED primary key, so the probe reads that column
rather than assuming `id`.
"""

import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "environment" / "monday-api"
ENV_DIR = REPO_ROOT / "environment"


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setenv("MOCK_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MOCK_ADMIN_ALLOWLIST", "127.0.0.1,testclient")

    monkeypatch.syspath_prepend(str(ENV_DIR))
    monkeypatch.syspath_prepend(str(SERVICE_DIR))

    for mod in [
        "monday_data", "server", "_mutable_store", "admin_plane",
        "tracking_middleware",
    ]:
        sys.modules.pop(mod, None)

    import server  # type: ignore
    from fastapi.testclient import TestClient

    yield TestClient(server.app)

    for mod in [
        "monday_data", "server", "_mutable_store", "admin_plane",
        "tracking_middleware",
    ]:
        sys.modules.pop(mod, None)


def test_admin_health_reachable(admin_client):
    r = admin_client.get("/admin/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "items" in body["tables"]


def test_admin_blocks_unallowlisted_ip(monkeypatch):
    monkeypatch.setenv("MOCK_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MOCK_ADMIN_ALLOWLIST", "10.99.99.99")
    monkeypatch.syspath_prepend(str(ENV_DIR))
    monkeypatch.syspath_prepend(str(SERVICE_DIR))
    for mod in [
        "monday_data", "server", "_mutable_store", "admin_plane",
        "tracking_middleware",
    ]:
        sys.modules.pop(mod, None)

    import server  # type: ignore
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    r = client.get("/admin/health")
    assert r.status_code == 404


def test_data_patch_visible_in_public_endpoint(admin_client):
    r = admin_client.get("/admin/data/items")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows, "expected pre-loaded items"
    target = rows[0]
    item_id = target["item_id"]

    r = admin_client.patch(
        f"/admin/data/items/{item_id}",
        json={"fields": {"name": "Blocked: vendor outage"}},
    )
    assert r.status_code == 200, r.text

    r = admin_client.get(f"/v2/items/{item_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "Blocked: vendor outage"


def test_snapshot_restore_round_trip(admin_client):
    r = admin_client.get("/admin/data/items")
    original = r.json()["rows"][0]
    item_id = original["item_id"]
    pristine_name = original["name"]

    r = admin_client.get("/admin/snapshot/__baseline__")
    if r.status_code == 404:
        r = admin_client.get("/admin/snapshot", params={"label": "pristine"})
        assert r.status_code == 200, r.text
        snap_id = r.json()["snapshot_id"]
    else:
        snap_id = "__baseline__"

    admin_client.patch(
        f"/admin/data/items/{item_id}",
        json={"fields": {"name": "Wiped"}},
    )

    r = admin_client.post("/admin/snapshot/restore",
                          json={"snapshot_id": snap_id})
    assert r.status_code == 200, r.text

    r = admin_client.get(f"/v2/items/{item_id}")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == pristine_name


def test_drift_log_records_mutations(admin_client):
    admin_client.post("/admin/drift/log/clear")

    r = admin_client.get("/admin/data/items")
    item_id = r.json()["rows"][0]["item_id"]

    admin_client.patch(
        f"/admin/data/items/{item_id}",
        json={"fields": {"name": "Logged"}},
    )

    r = admin_client.get("/admin/drift/log")
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e.get("op") == "data.patch" for e in events)


def test_audit_does_not_record_admin_calls(admin_client):
    admin_client.get("/audit/requests/clear")

    admin_client.get("/admin/data/items")
    admin_client.get("/admin/tables")

    r = admin_client.get("/audit/requests")
    assert r.status_code == 200
    paths = [e["path"] for e in r.json()["requests"]]
    assert not any(p.startswith("/admin") for p in paths), \
        f"admin paths leaked into audit log: {paths}"
