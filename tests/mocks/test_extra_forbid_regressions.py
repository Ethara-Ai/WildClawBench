"""F6: an unknown request-body key must 422, not vanish.

Every model reached by an agent-facing write route now declares
``extra="forbid"``. Before that, pydantic's default ``extra="ignore"`` dropped a
misspelled key *before* the handler ran, so the route answered 200/201 and the
agent was told its edit landed when only part of it had. These probes pin the
loud behaviour in pairs: the same request, once with a typo'd key and once
without, so a regression that re-opens a schema shows up as a 2xx on the first
half rather than as a silent change in coverage.

Several pairs (jira, zendesk, klaviyo, outlook, paypal, plaid, kubernetes) send
the typo *nested* inside the body rather than at the top level. The
route-contract guard only ever names a top-level body model, so nested shapes
are the half of this class no route entry could describe and a forbid on the
parent does not protect.

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

from ._helpers import ENV_DIR, data_module, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


def _client(api_name: str) -> TestClient:
    return TestClient(load_app(ENV_DIR / api_name))


@pytest.fixture(scope="module")
def jira():
    with _client("jira-api") as c:
        yield c


@pytest.fixture(scope="module")
def pagerduty():
    with _client("pagerduty-api") as c:
        yield c


@pytest.fixture(scope="module")
def zendesk():
    with _client("zendesk-api") as c:
        yield c


def _unknown_key_reported(response, key: str) -> bool:
    detail = response.json().get("detail")
    if not isinstance(detail, list):
        return False
    return any(key in (item.get("loc") or []) for item in detail)


# --- POST -------------------------------------------------------------------

def test_jira_create_issue_rejects_typo_nested_in_fields(jira):
    fields = {"summary": "forbid probe", "project": {"key": "ENG"},
              "issuetype": {"name": "Task"}}
    assert jira.post("/rest/api/3/issue", json={"fields": fields}).status_code == 201

    r = jira.post("/rest/api/3/issue",
                  json={"fields": dict(fields, sumary="the summary it meant")})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "sumary"), r.text


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


@pytest.fixture(scope="module")
def kubernetes():
    with _client("kubernetes-api") as c:
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


def test_kubernetes_scale_patch_rejects_typo_nested_in_spec(kubernetes):
    """Carries the PATCH verb for the whole module: mailchimp's PATCH pair left
    with the convergence and no other converged write route is a PATCH whose
    body is a closed model. Nested, so it also re-points the nested half."""
    route = "/apis/apps/v1/namespaces/prod/deployments/api-gateway/scale"
    assert kubernetes.patch(route, json={"spec": {"replicas": 4}}).status_code == 200

    r = kubernetes.patch(route, json={"spec": {"replicas": 4, "replcias": 9}})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "replcias"), r.text


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


# --- action routes that bound no body at all -------------------------------
#
# These four take no field this mock implements, so they declared no body --
# which meant an unknown key was never PARSED, let alone dropped. Garbage got a
# 2xx. An empty forbid envelope leaves the bodyless call working and makes
# anything else loud. The bodyless half of each pair is the half that would
# break if someone "simplified" the envelope back to a required model.
#
# Three of them act on a row whose state the action consumes -- an application
# advances to Hired, a payroll becomes processed, a merge request becomes
# merged -- and `get_store` is a process-wide registry, so the fleet-wide smoke
# suite reaches the same rows first. Each reseeds its row through the store the
# way test_service_regressions.py does, which is what POST /admin/data/{table}
# calls, so the accepted half asserts a status rather than a suite ordering.

@pytest.fixture(scope="module")
def greenhouse():
    with _client("greenhouse-api") as c:
        yield c


@pytest.fixture(scope="module")
def gusto():
    with _client("gusto-api") as c:
        yield c


def _reseed(client, module, table, row):
    data_module(client.app, module)._store.table(table).upsert(row)


def _application(app_id):
    return {"id": app_id, "candidate_id": "cand-7001", "job_id": "job-3001",
            "status": "active", "current_stage": "Application Review",
            "applied_at": "2026-04-02T10:05:00Z",
            "last_activity_at": "2026-04-03T09:00:00Z"}


def test_greenhouse_advance_takes_no_body_but_rejects_one(greenhouse):
    _reseed(greenhouse, "greenhouse_data", "applications", _application("app-forbid-1"))
    assert greenhouse.post("/v1/applications/app-forbid-1/advance").status_code == 200

    _reseed(greenhouse, "greenhouse_data", "applications", _application("app-forbid-2"))
    assert greenhouse.post("/v1/applications/app-forbid-2/advance",
                           json={}).status_code == 200

    r = greenhouse.post("/v1/applications/app-forbid-2/advance",
                        json={"from_stage_id": "s1"})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "from_stage_id"), r.text


def test_gusto_submit_payroll_takes_no_body_but_rejects_one(gusto):
    _reseed(gusto, "gusto_data", "payrolls",
            {"id": "pay-forbid-1", "company_id": "comp-001",
             "pay_period_start": "2026-06-01", "pay_period_end": "2026-06-15",
             "check_date": "2026-06-20", "processed": False, "gross_pay": 100.0,
             "net_pay": 80.0, "employee_count": 1})
    assert gusto.put("/v1/payrolls/pay-forbid-1/submit").status_code == 200

    r = gusto.put("/v1/payrolls/pay-forbid-1/submit", json={"verison": 2})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "verison"), r.text


def test_paypal_capture_takes_an_empty_body_but_rejects_an_unknown_key(paypal):
    def new_order():
        return paypal.post("/v2/checkout/orders", json={
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "5.00"}}],
        }).json()["id"]

    assert paypal.post(f"/v2/checkout/orders/{new_order()}/capture",
                       json={}).status_code == 201

    r = paypal.post(f"/v2/checkout/orders/{new_order()}/capture",
                    json={"payment_sauce": {}})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "payment_sauce"), r.text


def _merge_request(**overrides):
    row = {"id": 6099, "iid": 99, "project_id": 101, "title": "Forbid probe",
           "description": "", "state": "opened", "source_branch": "feature/probe",
           "target_branch": "main", "author": "amelia-ortega",
           "assignee": "jonas-pereira", "merge_status": "can_be_merged",
           "draft": False, "created_at": "2026-05-18T11:00:00.000Z",
           "updated_at": "2026-05-25T14:00:00.000Z", "merged_at": ""}
    row.update(overrides)
    return row


def test_gitlab_merge_takes_no_body_but_rejects_one(gitlab):
    # ints, not strings: merge_requests registers no row_coercer, and the
    # lookup compares `m["iid"] == int(mr_iid)`, so a RAW upsert -- the
    # un-tolerant path this helper uses -- must still match the coerced shape.
    # The admin plane no longer requires that of an author; see the test below.
    _reseed(gitlab, "gitlab_data", "merge_requests", _merge_request())
    route = "/api/v4/projects/101/merge_requests/99/merge"

    r = gitlab.put(route, json={"squash": True})
    assert r.status_code == 422, r.text
    assert _unknown_key_reported(r, "squash"), r.text

    assert gitlab.put(route).status_code == 200


def test_gitlab_merge_request_injected_in_seed_shape_is_still_reachable(gitlab):
    """The int trap the test above works around, proven closed on the admin path.

    `merge_requests` carries no row_coercer, so before this contract an author
    injecting `iid: "99"` -- the shape the seed CSV holds and the shape a task
    is written in -- put a string in the column `merge_merge_request` compares
    with `int(mr_iid)`. The row existed, every read served it, and the merge
    route answered "not found in project" forever. That is the authoring-side
    defect, not an agent-facing one, so it is closed here and nowhere else:
    the route's own 404 for a genuinely absent iid is asserted unchanged.
    """
    store = data_module(gitlab.app, "gitlab_data")._store
    table = store.table("merge_requests")

    row, notes = table.admin_upsert(_merge_request(
        id="6099", iid="99", project_id="101", draft="false"))

    assert row["iid"] == 99 and isinstance(row["iid"], int)
    assert row["project_id"] == 101
    assert row["draft"] is False
    assert row["id"] == 6099, "the stored row keeps the pk type it was loaded with"
    assert [n["kind"] for n in notes] == ["coercion"] * 4

    assert gitlab.put("/api/v4/projects/101/merge_requests/99/merge").status_code == 200
    assert table.get(6099)["state"] == "merged"


def test_gitlab_merge_still_refuses_an_iid_that_is_not_there(gitlab):
    _reseed(gitlab, "gitlab_data", "merge_requests", _merge_request())

    r = gitlab.put("/api/v4/projects/101/merge_requests/4242/merge")

    assert "not found" in r.text.lower(), r.text


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
