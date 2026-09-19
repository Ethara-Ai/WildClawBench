"""Fleet-wide write -> read-back coverage for mock services with mutating routes.

`test_service_regressions.py` pins the seven services whose write/read defects
commit 667131a actually fixed. This suite is the standing guard for the REST of
the mutating fleet: every spec writes through the real route layer, then reads
the resource back through a DIFFERENT route and asserts the mutation survived
the round trip with the right shape.

Read-back is the whole point. A handler that builds a response dict from its own
request body looks correct to any test that only inspects the write response --
it is the second, independent GET that proves the row reached the store in
`environment/_mutable_store.py` rather than being echoed and discarded.

Specs live in `_writeback_specs.py` and the engine that runs them in
`_writeback.py`; add a WriteSpec there rather than another hand-rolled
request/assert pair here. Services whose read-back is list-shaped
(Slack's `conversations.history`) don't fit the id-addressed engine and get
explicit tests at the bottom.
"""
from __future__ import annotations

import contextlib

import pytest

from ._helpers import ENV_DIR, load_app
from ._writeback import WriteSpec, check_create, check_delete, check_update
from ._writeback_specs import SPECS

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

IDS = [s.test_id for s in SPECS]


@pytest.fixture(scope="module")
def clients():
    """Lazily load each mock app once per module and close them all at teardown.

    `load_app` is expensive (it execs server.py and loads every seed table), and
    several specs share a service, so the apps are cached by directory name.
    """
    cache: dict = {}
    with contextlib.ExitStack() as stack:
        def get(api: str) -> TestClient:
            if api not in cache:
                cache[api] = stack.enter_context(TestClient(load_app(ENV_DIR / api)))
            return cache[api]
        yield get


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_create_is_readable_back(clients, spec: WriteSpec):
    check_create(clients(spec.api), spec)


@pytest.mark.parametrize("spec", [s for s in SPECS if s.update],
                         ids=[s.test_id for s in SPECS if s.update])
def test_update_persists(clients, spec: WriteSpec):
    client = clients(spec.api)
    check_update(client, spec, check_create(client, spec))


@pytest.mark.parametrize("spec", [s for s in SPECS if s.delete],
                         ids=[s.test_id for s in SPECS if s.delete])
def test_delete_is_not_served_again(clients, spec: WriteSpec):
    client = clients(spec.api)
    check_delete(client, spec, check_create(client, spec))


# ---------------------------------------------------------------------------
# slack-api -- read-back is the channel history list, not an addressable row
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def slack():
    if not (ENV_DIR / "slack-api" / "service.toml").is_file():
        pytest.skip("slack-api is not in this fleet")
    with TestClient(load_app(ENV_DIR / "slack-api")) as c:
        yield c


def _history(client, channel: str, limit: int = 5) -> list:
    r = client.get(f"/api/conversations.history?channel={channel}&limit={limit}")
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def test_slack_post_message_lands_in_history(slack):
    r = slack.post("/api/chat.postMessage", json={"channel": "C01ENG", "text": "WRB posted"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    ts = r.json()["ts"]
    top = _history(slack, "C01ENG")[0]
    assert top["ts"] == ts and top["text"] == "WRB posted", top


def test_slack_update_message_persists(slack):
    ts = slack.post("/api/chat.postMessage",
                    json={"channel": "C01ENG", "text": "WRB before"}).json()["ts"]
    r = slack.post("/api/chat.update", json={"channel": "C01ENG", "ts": ts, "text": "WRB after"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    hit = next(m for m in _history(slack, "C01ENG") if m["ts"] == ts)
    assert hit["text"] == "WRB after", hit


def test_slack_reaction_persists_on_the_message(slack):
    ts = slack.post("/api/chat.postMessage",
                    json={"channel": "C01ENG", "text": "WRB reactable"}).json()["ts"]
    r = slack.post("/api/reactions.add",
                   json={"channel": "C01ENG", "timestamp": ts, "name": "tada"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    hit = next(m for m in _history(slack, "C01ENG") if m["ts"] == ts)
    assert [x["name"] for x in hit["reactions"]] == ["tada"], hit


def test_slack_delete_removes_message_from_history(slack):
    ts = slack.post("/api/chat.postMessage",
                    json={"channel": "C01ENG", "text": "WRB doomed"}).json()["ts"]
    r = slack.post("/api/chat.delete", json={"channel": "C01ENG", "ts": ts})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert all(m["ts"] != ts for m in _history(slack, "C01ENG", limit=20))


@pytest.mark.xfail(
    strict=True, raises=Exception,
    reason="slack_data.conversations_create upserts channel_members (primary_key='_pk') "
           "without synthesizing _pk, so the route 500s. Same defect 667131a fixed for "
           "shippo tracking; report-only in this pass, fix needs separate review.",
)
def test_slack_conversations_create_persists(slack):
    r = slack.post("/api/conversations.create", json={"name": "wrb-new-channel"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    listed = [c["name"] for c in slack.get("/api/conversations.list").json()["channels"]]
    assert "wrb-new-channel" in listed


@pytest.mark.xfail(
    strict=True, raises=Exception,
    reason="slack_data.conversations_invite hits the same unsynthesized '_pk' upsert on "
           "channel_members whenever the invitee is not already a member; report-only.",
)
def test_slack_conversations_invite_persists(slack):
    r = slack.post("/api/conversations.invite",
                   json={"channel": "C01DEPLOY", "users": "U01NOOR"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    members = slack.get("/api/conversations.members?channel=C01DEPLOY").json()["members"]
    assert "U01NOOR" in members


# ---------------------------------------------------------------------------
# Remaining unsynthesized-'_pk' upserts, surfaced by
# `script/check_lost_writes.py --allowlist ...` (UNKEYED_UPSERT). Same defect as
# the two Slack routes above and as the shippo `tracking` bug 667131a fixed.
# Locked xfail-strict so whichever pass fixes them turns these green loudly.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True, raises=Exception,
    reason="doordash_data.checkout upserts order_items (primary_key='_pk') from a dict "
           "literal with no _pk, so every checkout 500s; report-only.",
)
def test_doordash_checkout_persists_order():
    with TestClient(load_app(ENV_DIR / "doordash-api")) as c:
        store_id = c.get("/v1/stores").json()["stores"][0]["store_id"]
        item_id = c.get(f"/v1/stores/{store_id}/menu").json()["items"][0]["item_id"]
        cart_id = c.post("/v1/carts", json={"store_id": store_id}).json()["cart_id"]
        c.post(f"/v1/carts/{cart_id}/items", json={"item_id": item_id, "quantity": 1})
        r = c.post(f"/v1/carts/{cart_id}/checkout", json={"customer_name": "WRB"})
        assert r.status_code == 200, r.text
        order_id = r.json()["order_id"]
        assert c.get(f"/v1/orders/{order_id}").status_code == 200


@pytest.mark.xfail(
    strict=True, raises=Exception,
    reason="spotify_data.add_tracks_to_playlist upserts playlist_tracks "
           "(primary_key='_pk') from a dict literal with no _pk; report-only.",
)
def test_spotify_add_tracks_persists():
    with TestClient(load_app(ENV_DIR / "spotify-api")) as c:
        playlist_id = c.get("/v1/me/playlists").json()["items"][0]["id"]
        uri = c.get("/v1/search?q=a&type=track").json()["tracks"]["items"][0]["uri"]
        r = c.post(f"/v1/playlists/{playlist_id}/tracks", json={"uris": [uri]})
        assert r.status_code in (200, 201), r.text
        listed = c.get(f"/v1/playlists/{playlist_id}/tracks").json()
        assert any(t["track"]["uri"] == uri for t in listed["items"])
