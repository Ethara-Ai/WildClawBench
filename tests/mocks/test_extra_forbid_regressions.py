"""F6: an unknown request-body key must 422, not vanish.

Every model reached by an agent-facing write route now declares
``extra="forbid"``. Before that, pydantic's default ``extra="ignore"`` dropped a
misspelled key *before* the handler ran, so the route answered 200/201 and the
agent was told its edit landed when only part of it had. These probes pin the
loud behaviour in pairs: the same request, once with a typo'd key and once
without, so a regression that re-opens a schema shows up as a 2xx on the first
half rather than as a silent change in coverage.

Two of the pairs (jira, zendesk) send the typo *nested* inside the body rather
than at the top level. The route-contract guard only ever names a top-level body
model, so nested shapes are the half of this class no route entry could describe
and a forbid on the parent does not protect.

Own module-scoped clients, matching test_route_fix_regressions.py: these writes
must not leak into the fleet-wide smoke suite's shared session client.
"""
from __future__ import annotations

import pytest

from ._helpers import ENV_DIR, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


def _client(api_name: str) -> TestClient:
    return TestClient(load_app(ENV_DIR / api_name))


@pytest.fixture(scope="module")
def slack():
    with _client("slack-api") as c:
        yield c


@pytest.fixture(scope="module")
def stripe():
    with _client("stripe-api") as c:
        yield c


@pytest.fixture(scope="module")
def jira():
    with _client("jira-api") as c:
        yield c


@pytest.fixture(scope="module")
def mailchimp():
    with _client("mailchimp-api") as c:
        yield c


@pytest.fixture(scope="module")
def pagerduty():
    with _client("pagerduty-api") as c:
        yield c


@pytest.fixture(scope="module")
def zendesk():
    with _client("zendesk-api") as c:
        yield c


@pytest.fixture(scope="module")
def spotify():
    with _client("spotify-api") as c:
        yield c


def _unknown_key_reported(response, key: str) -> bool:
    detail = response.json().get("detail")
    if not isinstance(detail, list):
        return False
    return any(key in (item.get("loc") or []) for item in detail)


# --- POST -------------------------------------------------------------------

def test_slack_post_message_rejects_typoed_key(slack):
    good = {"channel": "C01GENERAL", "text": "forbid probe"}
    assert slack.post("/api/chat.postMessage", json=good).status_code == 200

    typo = dict(good, txet="the real text the agent meant")
    r = slack.post("/api/chat.postMessage", json=typo)
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "txet"), r.text


def test_stripe_create_customer_rejects_typoed_key(stripe):
    good = {"name": "Forbid Probe", "email": "probe@example.com"}
    assert stripe.post("/v1/customers", json=good).status_code == 201

    typo = dict(good)
    typo["emial"] = typo.pop("email")
    r = stripe.post("/v1/customers", json=typo)
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "emial"), r.text


def test_jira_create_issue_rejects_typo_nested_in_fields(jira):
    fields = {"summary": "forbid probe", "project": {"key": "ENG"},
              "issuetype": {"name": "Task"}}
    assert jira.post("/rest/api/3/issue", json={"fields": fields}).status_code == 201

    r = jira.post("/rest/api/3/issue",
                  json={"fields": dict(fields, sumary="the summary it meant")})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "sumary"), r.text


# --- PATCH ------------------------------------------------------------------

def test_mailchimp_patch_member_rejects_typoed_key(mailchimp):
    list_id = mailchimp.get("/3.0/lists").json()["lists"][0]["id"]
    members = mailchimp.get(f"/3.0/lists/{list_id}/members").json()["members"]
    route = f"/3.0/lists/{list_id}/members/{members[0]['id']}"

    assert mailchimp.patch(route, json={"status": "unsubscribed"}).status_code == 200

    r = mailchimp.patch(route, json={"status": "unsubscribed", "statuss": "pending"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "statuss"), r.text


# --- PUT --------------------------------------------------------------------

def test_pagerduty_update_incident_rejects_typoed_key(pagerduty):
    incident_id = pagerduty.get("/incidents").json()["incidents"][0]["incident_id"]
    route = f"/incidents/{incident_id}"

    assert pagerduty.put(route, json={"status": "acknowledged"}).status_code == 200

    r = pagerduty.put(route, json={"status": "acknowledged", "assigned_too": "PU001"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "assigned_too"), r.text


def test_zendesk_update_ticket_rejects_typo_nested_in_ticket(zendesk):
    ticket_id = zendesk.get("/api/v2/tickets").json()["tickets"][0]["id"]
    route = f"/api/v2/tickets/{ticket_id}"

    assert zendesk.put(route, json={"ticket": {"status": "open"}}).status_code == 200

    r = zendesk.put(route, json={"ticket": {"status": "open", "prioirty": "urgent"}})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "prioirty"), r.text


def test_spotify_start_playback_rejects_typoed_key(spotify):
    assert spotify.put("/v1/me/player/play",
                       json={"context_uri": "spotify:album:probe"}).status_code == 200

    r = spotify.put("/v1/me/player/play", json={"context_url": "spotify:album:probe"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "context_url"), r.text
