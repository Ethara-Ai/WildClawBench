"""Integration tests for the /admin/apply_as_api drift-replay hatch.

Proves the universal fix: mutations authored against the real API contract
(path-action endpoints, body-embedded identifiers) apply correctly through the
mock's own endpoint, stay OUT of the agent-visible audit, and cannot be forged
by the agent. Exercises the REAL admin_plane + tracking_middleware.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request

ENV_DIR = Path(__file__).resolve().parents[2] / "environment"
if str(ENV_DIR) not in sys.path:
    sys.path.insert(0, str(ENV_DIR))

TOKEN = "test-admin-secret"


@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient
    # monkeypatch auto-restores these after the test so admin env never leaks
    # into other suites (e.g. the drift smoke gate).
    monkeypatch.setenv("MOCK_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MOCK_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("MOCK_ADMIN_ALLOWLIST", "")  # empty -> no IP gate, token-only

    from tracking_middleware import install_tracker
    from admin_plane import install_admin_plane
    from _mutable_store import Store

    app = FastAPI()
    state = {
        "users": {"u1": {"id": "u1", "status": "ACTIVE"}},
        # message keyed by (channel_id, ts) — identifier lives in the BODY
        "messages": {("C1", "100.1"): {"channel_id": "C1", "ts": "100.1", "text": "orig"}},
    }

    # path-action endpoint (no body), mirrors okta /lifecycle/suspend
    @app.post("/api/users/{uid}/suspend")
    def suspend(uid: str):
        if uid not in state["users"]:
            return {"error": "not found"}
        state["users"][uid]["status"] = "SUSPENDED"
        return state["users"][uid]

    @app.get("/api/users/{uid}")
    def get_user(uid: str):
        return state["users"].get(uid, {"error": "not found"})

    # body-identifier endpoint, mirrors slack chat.update (channel_id+ts locate row)
    @app.post("/api/chat.update")
    async def chat_update(request: Request):
        b = await request.json()
        key = (b.get("channel_id"), b.get("ts"))
        if key not in state["messages"]:
            return {"ok": False, "error": "message not found"}
        state["messages"][key]["text"] = b.get("text", "")
        return {"ok": True, **state["messages"][key]}

    # A store-backed endpoint that ACTUALLY persists (via store.patch) — used to
    # prove the persistence-verification 'changed' flag fires on real mutations,
    # vs the dict-mutating endpoints above which mimic the okta copy-bug (200 but
    # nothing persisted → changed must be False).
    store = Store("test")
    store.register("widgets", primary_key="id",
                   initial_loader=lambda: [{"id": "w1", "status": "NEW"}])

    @app.post("/api/widgets/{wid}/ship")
    def ship(wid: str):
        store.table("widgets").patch(wid, {"status": "SHIPPED"})
        return {"ok": True, "id": wid, "status": "SHIPPED"}

    install_tracker(app)
    install_admin_plane(app, store)

    return TestClient(app), state


def _apply(client, method, path, body=None, token=TOKEN):
    headers = {"X-Admin-Token": token} if token else {}
    return client.post("/admin/apply_as_api",
                       json={"method": method, "path": path, "body": body},
                       headers=headers)


def test_path_action_empty_body_applies(client):
    client, state = client
    # okta-shaped: POST /suspend with empty body must flip status via the endpoint
    r = _apply(client, "POST", "/api/users/u1/suspend", body={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert state["users"]["u1"]["status"] == "SUSPENDED"


def test_body_identifier_update_applies(client):
    client, state = client
    # slack-shaped: identifier (channel_id+ts) is in the body, only text mutates
    r = _apply(client, "POST", "/api/chat.update",
               body={"channel_id": "C1", "ts": "100.1", "text": "MUTATED"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert state["messages"][("C1", "100.1")]["text"] == "MUTATED"


def test_replay_is_audit_suppressed(client):
    client, state = client
    _apply(client, "POST", "/api/users/u1/suspend", body={})
    diary = client.get("/audit/requests").json()
    reqs = diary.get("requests", diary if isinstance(diary, list) else [])
    paths = [r.get("path") for r in reqs]
    assert "/api/users/u1/suspend" not in paths, "silent replay leaked into audit"


def test_agent_cannot_forge_suppression(client):
    client, state = client
    # A direct call carrying the suppress header but NO admin token stays audited.
    client.post("/api/users/u1/suspend", headers={"x-wcb-suppress-audit": "wrong"})
    diary = client.get("/audit/requests").json()
    reqs = diary.get("requests", diary if isinstance(diary, list) else [])
    assert any(r.get("path") == "/api/users/u1/suspend" for r in reqs)


def test_changed_true_when_store_actually_persists(client):
    client, state = client
    r = _apply(client, "POST", "/api/widgets/w1/ship", body={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["changed"] is True  # store.patch persisted -> verified


def test_changed_false_when_mutation_not_persisted(client):
    client, state = client
    # /suspend mutates a plain dict, NOT the admin store (the okta copy-bug shape):
    # returns 200 but nothing persisted -> the guard must report changed=False so
    # the injector does NOT falsely mark it applied.
    r = _apply(client, "POST", "/api/users/u1/suspend", body={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["changed"] is False


def test_apply_as_api_rejects_admin_target(client):
    client, state = client
    r = _apply(client, "GET", "/admin/tables")
    assert r.status_code == 400


def test_requires_admin_token(client):
    client, state = client
    r = _apply(client, "POST", "/api/users/u1/suspend", body={}, token="")
    assert r.status_code == 404  # admin gate hides the endpoint
