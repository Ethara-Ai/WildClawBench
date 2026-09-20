"""Update-route contract regressions for the hardened write routes.

Every route in CONTRACTS used to answer 2xx over a payload it had thrown away.
Zoom answered byte-identically; the rest moved a timestamp and nothing else,
which is strictly worse than silence -- the resource looks freshly written when
none of the requested fields landed.

Sixteen of the original twenty-one rows were pinned to services that left in the
newreq convergence (airtable, amazon-seller, etsy, google-classroom,
google-drive, linear, pinterest, typeform, wordpress); they are retired with
their services. Three arriving services join the table, and three more arriving
routes are the SAME defect still live -- see MTIME_LIARS below.

Three things are pinned per route:

  * a key the model does not declare -- the wrong-case spelling an agent reaches
    for -- is a 422 rather than a dropped field,
  * a body that names nothing writable is a refusal rather than a 200, so the
    old timestamp-only lie cannot come back as a silent no-op,
  * the fields create could set and update could not now survive a write and an
    independent re-read.

The read-back always goes through a *different* endpoint than the write: the
write response was never the thing that lied.

Own module-scoped apps rather than conftest's fleet-wide `api_dir`
parametrization, and a throwaway row per mutating test -- `_mutable_store`
keeps one store per service name for the whole process, so the fleet smoke
suite's DELETE sweep and these writes share state.
"""
from __future__ import annotations

import pytest

from ._helpers import ENV_DIR, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

CONTENTFUL = "/spaces/space-orbit/environments/master/entries"
GH_ISSUES = "/repos/orbit-labs/auth-api/issues"
SENTRY_ISSUE = "/api/0/organizations/orbit-labs/issues/40001/"
K8S_DEPLOY = "/apis/apps/v1/namespaces/prod/deployments/api-gateway"
BAMBOO_TOR = "/api/gateway.php/orbitlabs/v1/time_off/requests/tor-5001/status"
FRESHDESK_TICKET = "/api/v2/tickets/70001"
CF_DNS_RECORD = ("/client/v4/zones/zone1aaaa1111bbbb2222cccc3333dddd"
                 "/dns_records/rec0001aaaa")
GL_ISSUE = "/api/v4/projects/101/issues/1"


def _client(api_name: str) -> TestClient:
    return TestClient(load_app(ENV_DIR / api_name))


# ---------------------------------------------------------------------------
# Fleet-wide: the two properties every hardened route has to hold.
#
# Neither case reaches the data layer -- pydantic rejects the first and the
# route's own guard rejects the second -- so these need no live row and cannot
# race the smoke suite's deletes.
# ---------------------------------------------------------------------------

#: (api, METHOD, path, undeclared-key body, body naming nothing, its status)
CONTRACTS = [
    ("zoom-api", "PATCH", "/v2/meetings/85012345678",
     {"Topic": "wrong case"}, {}, 400),
    ("contentful-api", "PUT", f"{CONTENTFUL}/author-mara",
     {"Fields": {"name": "wrong case"}}, {}, 422),
    ("datadog-api", "PUT", "/api/v1/monitor/1001",
     {"Name": "wrong case"}, {}, 400),
    ("github-api", "PATCH", f"{GH_ISSUES}/142",
     {"Title": "wrong case"}, {}, 400),
    ("servicenow-api", "PATCH", "/api/now/table/incident/inc-0001001",
     {"Short_description": "wrong case"}, {}, 400),

    # The arriving 25. These three are the only converged-fleet newcomers whose
    # update route holds BOTH properties: the wrong-case key is parsed and
    # refused, and a body naming nothing writable cannot reach the data layer.
    # They get there through a REQUIRED field rather than through a route guard
    # -- a different mechanism, the same contract from the agent's side.
    ("sentry-api", "PUT", SENTRY_ISSUE,
     {"Status": "wrong case"}, {}, 422),
    ("kubernetes-api", "PATCH", f"{K8S_DEPLOY}/scale",
     {"Spec": {"replicas": 3}}, {}, 422),
    ("bamboohr-api", "PUT", BAMBOO_TOR,
     {"Status": "approved"}, {}, 422),
]

CONTRACT_IDS = [f"{api}-{method}-{path}" for api, method, path, _, _, _ in CONTRACTS]

#: (api, METHOD, path, read path, the served timestamp's dotted path)
MTIME_LIARS = [
    ("freshdesk-api", "PUT", FRESHDESK_TICKET, FRESHDESK_TICKET, "updated_at"),
    ("cloudflare-api", "PUT", CF_DNS_RECORD, CF_DNS_RECORD, "result.modified_on"),
    ("gitlab-api", "PUT", GL_ISSUE, GL_ISSUE, "updated_at"),
]


@pytest.fixture(scope="module")
def clients():
    opened: dict[str, TestClient] = {}
    for api in sorted({c[0] for c in CONTRACTS} | {c[0] for c in MTIME_LIARS}):
        client = _client(api)
        client.__enter__()
        opened[api] = client
    try:
        yield opened
    finally:
        for client in opened.values():
            client.__exit__(None, None, None)


@pytest.mark.parametrize("api,method,path,wrong_case,_empty,_status",
                         CONTRACTS, ids=CONTRACT_IDS)
def test_undeclared_key_is_rejected(clients, api, method, path, wrong_case,
                                    _empty, _status):
    r = clients[api].request(method, path, json=wrong_case)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("api,method,path,_wrong,empty,status",
                         CONTRACTS, ids=CONTRACT_IDS)
def test_body_naming_nothing_writable_is_refused(clients, api, method, path,
                                                 _wrong, empty, status):
    r = clients[api].request(method, path, json=empty)
    assert r.status_code == status, r.text
    assert "expected" in r.text or "required" in r.text, r.text


# ---------------------------------------------------------------------------
# zoom-api -- PATCH could not restate the meeting type create had set
# ---------------------------------------------------------------------------

@pytest.fixture()
def zoom_meeting(clients):
    zoom = clients["zoom-api"]
    r = zoom.post("/v2/users/me/meetings",
                  json={"topic": "Contract fixture", "type": 2, "duration": 30,
                        "agenda": "original agenda"})
    assert r.status_code == 201, r.text
    meeting_id = r.json()["id"]
    yield meeting_id
    zoom.delete(f"/v2/meetings/{meeting_id}")


def test_zoom_patch_persists_meeting_type(clients, zoom_meeting):
    zoom = clients["zoom-api"]
    assert zoom.patch(f"/v2/meetings/{zoom_meeting}",
                      json={"type": 8}).status_code == 200
    assert zoom.get(f"/v2/meetings/{zoom_meeting}").json()["type"] == 8


def test_zoom_patch_leaves_sibling_fields_alone(clients, zoom_meeting):
    zoom = clients["zoom-api"]
    zoom.patch(f"/v2/meetings/{zoom_meeting}", json={"topic": "Renamed"})
    meeting = zoom.get(f"/v2/meetings/{zoom_meeting}").json()
    assert meeting["topic"] == "Renamed"
    assert meeting["agenda"] == "original agenda"
    assert meeting["duration"] == 30


# ---------------------------------------------------------------------------
# The mtime lie: a timestamp may only move when a field actually landed
# ---------------------------------------------------------------------------

def test_contentful_put_does_not_touch_updated_at_when_refused(clients):
    cf = clients["contentful-api"]
    before = cf.get(f"{CONTENTFUL}/author-mara")
    if before.status_code != 200:
        pytest.skip("seed entry already removed by the fleet smoke sweep")
    cf.put(f"{CONTENTFUL}/author-mara", json={"Fields": {"name": "wrong case"}})
    after = cf.get(f"{CONTENTFUL}/author-mara").json()
    assert after["sys"]["updatedAt"] == before.json()["sys"]["updatedAt"]


def test_contentful_put_merges_fields_and_moves_updated_at(clients):
    cf = clients["contentful-api"]
    created = cf.post(CONTENTFUL, json={"content_type": "author",
                                        "fields": {"name": "Contract fixture"}})
    assert created.status_code == 201, created.text
    entry_id = created.json()["sys"]["id"]
    try:
        r = cf.put(f"{CONTENTFUL}/{entry_id}", json={"fields": {"bio": "added"}})
        assert r.status_code == 200, r.text
        entry = cf.get(f"{CONTENTFUL}/{entry_id}").json()
        assert entry["fields"] == {"name": "Contract fixture", "bio": "added"}
    finally:
        cf.delete(f"{CONTENTFUL}/{entry_id}")


def test_github_patch_restating_the_same_state_does_not_move_updated_at(clients):
    """Asserted against a seed row on purpose. `_now()` has second resolution,
    so a row created inside the test carries a timestamp that a spurious bump
    would reproduce exactly and the assertion would hold either way."""
    gh = clients["github-api"]
    before = gh.get(f"{GH_ISSUES}/142")
    if before.status_code != 200:
        pytest.skip("seed issue already removed by the fleet smoke sweep")
    seeded = before.json()
    r = gh.patch(f"{GH_ISSUES}/142", json={"state": seeded["state"]})
    assert r.status_code == 200, r.text
    assert gh.get(f"{GH_ISSUES}/142").json()["updated_at"] == seeded["updated_at"]


def _dig(body, dotted):
    for part in dotted.split("."):
        body = body[part]
    return body


@pytest.mark.xfail(
    reason="W3 finding, NOT fixed in this wave: these three arriving update "
           "routes are the nineteen mtime-liars' defect class, alive on the "
           "converged fleet. Their bodies are all-Optional forbid models, so an "
           "empty {} parses, reaches the data layer, writes nothing, and still "
           "moves the served timestamp -- the resource reads as freshly written "
           "when no field landed. W2's F6 pass closed the unknown-key axis on "
           "these models and did not touch the empty-body axis. Service-side "
           "fix (a route guard naming what the body can act on, as the 21 "
           "hardened routes got) is deliberately out of W3's scope.",
    strict=True)
@pytest.mark.parametrize("api,method,path,read,stamp", MTIME_LIARS,
                         ids=[c[0] for c in MTIME_LIARS])
def test_arriving_update_does_not_touch_its_timestamp_on_an_empty_body(
        clients, api, method, path, read, stamp):
    client = clients[api]
    before = client.get(read)
    assert before.status_code == 200, before.text
    was = _dig(before.json(), stamp)
    client.request(method, path, json={})
    assert _dig(client.get(read).json(), stamp) == was


def test_github_patch_applies_title_and_state_and_keeps_body(clients):
    gh = clients["github-api"]
    created = gh.post(GH_ISSUES, json={"title": "Contract fixture", "body": "seed"})
    assert created.status_code == 201, created.text
    number = created.json()["number"]
    r = gh.patch(f"{GH_ISSUES}/{number}", json={"title": "Renamed", "state": "closed"})
    assert r.status_code == 200, r.text
    issue = gh.get(f"{GH_ISSUES}/{number}").json()
    assert issue["title"] == "Renamed"
    assert issue["state"] == "closed"
    assert issue["body"] == "seed"


# ---------------------------------------------------------------------------
# Create-only fields that the resource served but no update route could reach
# ---------------------------------------------------------------------------

def test_servicenow_patch_persists_opened_by(clients):
    snow = clients["servicenow-api"]
    created = snow.post("/api/now/table/incident",
                        json={"short_description": "Contract fixture",
                              "opened_by": "usr-noor"})
    assert created.status_code == 201, created.text
    sys_id = created.json()["result"]["sys_id"]
    r = snow.patch(f"/api/now/table/incident/{sys_id}", json={"opened_by": "usr-raj"})
    assert r.status_code == 200, r.text
    incident = snow.get(f"/api/now/table/incident/{sys_id}").json()["result"]
    assert incident["opened_by"] == "usr-raj"
    assert incident["short_description"] == "Contract fixture"


# ---------------------------------------------------------------------------
# Happy paths stay byte-identical: the correct-shape callers these routes
# already had must not notice the hardening.
# ---------------------------------------------------------------------------

def test_datadog_put_monitor_partial_update_keeps_siblings(clients):
    dd = clients["datadog-api"]
    created = dd.post("/api/v1/monitor", json={"name": "Contract fixture",
                                               "type": "metric alert",
                                               "query": "avg(last_5m):x > 1",
                                               "message": "seed", "tags": ["env:test"]})
    assert created.status_code == 201, created.text
    monitor_id = created.json()["id"]
    r = dd.put(f"/api/v1/monitor/{monitor_id}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    monitor = dd.get(f"/api/v1/monitor/{monitor_id}").json()
    assert monitor["name"] == "Renamed"
    assert monitor["message"] == "seed"
    assert monitor["tags"] == ["env:test"]
    assert monitor["query"] == "avg(last_5m):x > 1"


# ---------------------------------------------------------------------------
# monday-api -- the residual from the kayla incident: a PUT that parses but
# reaches no write branch is now a 400 naming what the route can act on.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def monday():
    with _client("monday-api") as c:
        yield c


@pytest.fixture()
def monday_item(monday):
    r = monday.post("/v2/items", json={"board_id": "board-101",
                                       "item_name": "Contract fixture",
                                       "group_id": "grp-todo"})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    yield item_id
    monday.delete(f"/v2/items/{item_id}")


@pytest.mark.parametrize("body", [
    {},
    {"text": "Clear to print"},
    {"value": "Clear to print"},
    {"column_values": {}},
])
def test_monday_put_without_a_write_branch_is_400(monday, monday_item, body):
    r = monday.put(f"/v2/items/{monday_item}", json=body)
    assert r.status_code == 400, r.text
    for field in ("column_values", "column_id", "item_name", "group_id"):
        assert field in r.json()["error"]


@pytest.mark.parametrize("body,expect", [
    ({"item_name": "Renamed"}, "Renamed"),
    ({"item_name": ""}, ""),
])
def test_monday_put_that_reaches_a_write_branch_still_applies(monday, monday_item,
                                                              body, expect):
    r = monday.put(f"/v2/items/{monday_item}", json=body)
    assert r.status_code == 200, r.text
    assert monday.get(f"/v2/items/{monday_item}").json()["name"] == expect


def test_monday_put_column_values_and_column_id_still_apply(monday, monday_item):
    assert monday.put(f"/v2/items/{monday_item}",
                      json={"column_values": {"status": {"text": "Clear to print"}}}
                      ).status_code == 200
    assert monday.put(f"/v2/items/{monday_item}",
                      json={"column_id": "owner", "text": "Priya Nair"}
                      ).status_code == 200
    cols = {c["id"]: c for c in
            monday.get(f"/v2/items/{monday_item}").json()["column_values"]}
    assert cols["status"]["text"] == "Clear to print"
    assert cols["owner"]["text"] == "Priya Nair"
