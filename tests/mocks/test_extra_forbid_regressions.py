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

The second half of this module covers the 25 services that arrived in the newreq
convergence. They came from a tree predating F6, so their bodies were either lax
models or bare `dict = Body(...)` mappings -- freshdesk would answer 201 with an
empty subject to a body of pure garbage. Those bodies are real forbid models
now, and the pairs below are what stops them going back. The last two probes are
the fidelity exceptions, asserted as exceptions so that a later tightening has
to change a test rather than silently change the contract.

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


# ===========================================================================
# The arriving 25. Every body below used to accept the typo silently.
# ===========================================================================

@pytest.fixture(scope="module")
def freshdesk():
    with _client("freshdesk-api") as c:
        yield c


@pytest.fixture(scope="module")
def paypal():
    with _client("paypal-api") as c:
        yield c


@pytest.fixture(scope="module")
def plaid():
    with _client("plaid-api") as c:
        yield c


@pytest.fixture(scope="module")
def klaviyo():
    with _client("klaviyo-api") as c:
        yield c


@pytest.fixture(scope="module")
def outlook():
    with _client("outlook-api") as c:
        yield c


@pytest.fixture(scope="module")
def gitlab():
    with _client("gitlab-api") as c:
        yield c


@pytest.fixture(scope="module")
def sentry():
    with _client("sentry-api") as c:
        yield c


@pytest.fixture(scope="module")
def kraken():
    with _client("kraken-api") as c:
        yield c


@pytest.fixture(scope="module")
def segment():
    with _client("segment-api") as c:
        yield c


# --- bodies that were a bare `dict = Body(...)` ----------------------------

def test_freshdesk_create_ticket_rejects_typoed_key(freshdesk):
    good = {"subject": "forbid probe", "description": "body", "priority": 2}
    assert freshdesk.post("/api/v2/tickets", json=good).status_code == 201

    typo = dict(good, prioirty=3)
    r = freshdesk.post("/api/v2/tickets", json=typo)
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "prioirty"), r.text


def test_freshdesk_create_ticket_rejects_garbage_instead_of_201ing_an_empty_subject(freshdesk):
    """The confirmed live defect: pure garbage used to mint a blank ticket."""
    r = freshdesk.post("/api/v2/tickets", json={"zzz_not_a_field": "garbage"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "subject"), r.text


def test_klaviyo_create_profile_rejects_typo_nested_in_attributes(klaviyo):
    def body(attrs):
        return {"data": {"type": "profile", "attributes": attrs}}

    good = {"email": "forbid.probe@example.com", "first_name": "Probe"}
    assert klaviyo.post("/api/profiles", json=body(good)).status_code == 201

    r = klaviyo.post("/api/profiles", json=body(dict(good, frist_name="Probe")))
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "frist_name"), r.text


def test_outlook_send_mail_rejects_typo_nested_in_message(outlook):
    def body(message):
        return {"message": message}

    good = {"subject": "forbid probe", "body": {"contentType": "HTML", "content": "hi"},
            "toRecipients": [{"emailAddress": {"address": "a@example.com"}}]}
    assert outlook.post("/v1.0/me/sendMail", json=body(good)).status_code == 202

    r = outlook.post("/v1.0/me/sendMail", json=body(dict(good, subjct="the real one")))
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "subjct"), r.text


# --- bodies that were a lax model ------------------------------------------

def test_gitlab_create_issue_rejects_typoed_key(gitlab):
    good = {"title": "forbid probe", "description": "body"}
    assert gitlab.post("/api/v4/projects/101/issues", json=good).status_code == 201

    r = gitlab.post("/api/v4/projects/101/issues", json=dict(good, descriptoin="real"))
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "descriptoin"), r.text


def test_paypal_create_payout_rejects_typo_nested_two_levels_down(paypal):
    def body(amount):
        return {"sender_batch_header": {"sender_batch_id": "Probe_01"},
                "items": [{"amount": amount, "receiver": "a@example.com"}]}

    assert paypal.post("/v1/payments/payouts",
                       json=body({"currency_code": "USD", "value": "5.00"})
                       ).status_code == 201

    r = paypal.post("/v1/payments/payouts",
                    json=body({"currency_code": "USD", "value": "5.00", "valeu": "9.99"}))
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "valeu"), r.text


def test_plaid_transactions_get_rejects_typo_nested_in_options(plaid):
    def body(options):
        return {"access_token": "access-probe", "start_date": "2026-01-01",
                "end_date": "2026-12-31", "options": options}

    assert plaid.post("/transactions/get", json=body({"count": 5})).status_code == 200

    r = plaid.post("/transactions/get", json=body({"count": 5, "ofset": 10}))
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "ofset"), r.text


def test_sentry_update_issue_rejects_typoed_key(sentry):
    route = "/api/0/organizations/orbit-labs/issues/40001/"
    assert sentry.put(route, json={"status": "resolved"}).status_code == 200

    r = sentry.put(route, json={"status": "resolved", "assignedTo0": "u1"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "assignedTo0"), r.text


def test_kraken_private_balance_rejects_an_unknown_key(kraken):
    """A read still gets an envelope: nonce is accepted, anything else is not."""
    assert kraken.post("/0/private/Balance", json={"nonce": "1699"}).status_code == 200
    assert kraken.post("/0/private/Balance").status_code == 200

    r = kraken.post("/0/private/Balance", json={"noncce": "1699"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "noncce"), r.text


# --- the fidelity exceptions, pinned AS exceptions -------------------------

def test_segment_tracking_api_still_accepts_an_undeclared_top_level_key(segment):
    """Not laxity: Segment's own writeKey example posts an undocumented `email`
    to /v1/track, and the only 400s that page documents are the size ceilings.
    Forbidding here would reject payloads the vendor answers 200 to."""
    r = segment.post("/v1/track", json={"userId": "user_1001", "event": "Probe",
                                        "email": "probe@example.org"})
    assert r.status_code == 200, r.text


def test_webflow_field_data_is_open_but_its_envelope_is_not():
    """fieldData carries the customer's own collection schema (the salesforce
    SObjectBody argument); the v2 envelope around it does not."""
    with _client("webflow-api") as c:
        route = "/v2/collections/660b2a0000000000000002b1/items"
        ok = c.post(route, json={"fieldData": {"name": "Probe", "anything": "goes"}})
        assert ok.status_code == 202, ok.text

        r = c.post(route, json={"fieldData": {"name": "Probe"}, "isDarft": True})
        assert r.status_code == 422, r.text
        assert _unknown_key_reported(r, "isDarft"), r.text
