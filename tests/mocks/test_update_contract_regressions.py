"""Update-route contract regressions for the hardened write routes.

Every route in CONTRACTS used to answer 2xx over a payload it had thrown away.
Zoom answered byte-identically; the rest moved a timestamp and nothing else,
which is strictly worse than silence -- the resource looks freshly written when
none of the requested fields landed.

Sixteen of the original twenty-one rows were pinned to services that left in the
newreq convergence (airtable, amazon-seller, etsy, google-classroom,
google-drive, linear, pinterest, typeform, wordpress); they are retired with
their services. Three arriving services join the table, and three more arriving
routes carried the SAME defect -- freshdesk, cloudflare and gitlab, held here in
MTIME_LIARS. Those three were pinned as strict xfails when W3 found them and are
now hardened the way the original nineteen were, so the rows assert the fixed
behaviour rather than the defect.

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

from ._helpers import ENV_DIR, data_module, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

CONTENTFUL = "/spaces/space-orbit/environments/master/entries"
GH_ISSUES = "/repos/orbit-labs/auth-api/issues"
SENTRY_ISSUE = "/api/0/organizations/orbit-labs/issues/40001/"
K8S_DEPLOY = "/apis/apps/v1/namespaces/prod/deployments/api-gateway"
BAMBOO_TOR = "/api/gateway.php/orbitlabs/v1/time_off/requests/tor-5001/status"
FRESHDESK_TICKET = "/api/v2/tickets/70001"
CF_ZONE = "/client/v4/zones/zone1aaaa1111bbbb2222cccc3333dddd"
CF_DNS_RECORD = f"{CF_ZONE}/dns_records/rec0001aaaa"
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

    # The three W3 mtime-liars. These reach the contract through a route guard
    # rather than a required field, which is the mechanism the original
    # nineteen got -- their bodies are all-Optional, so nothing but the guard
    # can tell an empty body from a partial one.
    ("freshdesk-api", "PUT", FRESHDESK_TICKET,
     {"Status": 3}, {}, 400),
    ("cloudflare-api", "PUT", CF_DNS_RECORD,
     {"Content": "203.0.113.88"}, {}, 400),
    ("gitlab-api", "PUT", GL_ISSUE,
     {"Title": "wrong case"}, {}, 400),
]

CONTRACT_IDS = [f"{api}-{method}-{path}" for api, method, path, _, _, _ in CONTRACTS]

#: The three W3 mtime-liars, with everything needed to stand a throwaway row up
#: and drag its timestamp into the past first -- see `_pinned_row`.
#: (api, data module, store table, create path, create body, dotted id in the
#:  create response, the dotted value the URL wants, read/write path template,
#:  the served timestamp's dotted path, a past stamp in that service's own
#:  format, an update that lands, where it lands, the value it lands)
MTIME_LIARS = [
    ("freshdesk-api", "freshdesk_data", "tickets",
     "/api/v2/tickets",
     {"subject": "Contract fixture", "description": "seed body", "priority": 1},
     "id", "id", "/api/v2/tickets/{id}",
     "updated_at", "2026-01-05T09:00:00Z",
     {"priority": 4}, "priority", 4),
    ("cloudflare-api", "cloudflare_data", "dns",
     f"{CF_ZONE}/dns_records",
     {"type": "A", "name": "mtime.orbit-labs.com", "content": "203.0.113.10",
      "ttl": 300, "proxied": False},
     "result.id", "result.id", CF_ZONE + "/dns_records/{id}",
     "result.modified_on", "2026-01-05T09:00:00.000000Z",
     {"content": "203.0.113.88"}, "result.content", "203.0.113.88"),
    ("gitlab-api", "gitlab_data", "issues",
     "/api/v4/projects/101/issues",
     {"title": "Contract fixture", "description": "seed body"},
     "id", "iid", "/api/v4/projects/101/issues/{id}",
     "updated_at", "2026-01-05T09:00:00.000Z",
     {"title": "Contract fixture renamed"}, "title", "Contract fixture renamed"),
]

MTIME_PARAMS = ("api,module,table,create_path,create_body,pk_at,path_at,"
                "read_tmpl,stamp,pinned,writes,at,value")


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


def _pinned_row(clients, api, module, table, create_path, create_body, pk_at,
                path_at, read_tmpl, stamp, pinned):
    """Create a throwaway row and drag its timestamp into the past.

    Both hazards this file already records are live on these three routes at
    once. `_now()` has second resolution, so a row created and re-read inside
    the same second cannot tell a spurious bump from no bump -- which is why
    the contentful and github cases above assert against seed rows. But the
    fleet smoke sweep DELETEs seed rows, and cloudflare's DNS record is the one
    resource of these three that has a DELETE route to be swept by, so a seed
    row is not available either. Writing the stamp back to a fixed past value
    settles both: the row is this test's own, and the bump is unambiguous.
    """
    client = clients[api]
    created = client.post(create_path, json=create_body)
    assert created.status_code in (200, 201), created.text
    body = created.json()
    table_ = data_module(client.app, module)._store.table(table)
    assert table_.patch(_dig(body, pk_at),
                        {stamp.rsplit(".", 1)[-1]: pinned}) is not None
    return client, read_tmpl.format(id=_dig(body, path_at))


@pytest.mark.parametrize(MTIME_PARAMS, MTIME_LIARS,
                         ids=[c[0] for c in MTIME_LIARS])
def test_arriving_update_does_not_touch_its_timestamp_on_an_empty_body(
        clients, api, module, table, create_path, create_body, pk_at, path_at,
        read_tmpl, stamp, pinned, writes, at, value):
    """The W3 pin, inverted: an update naming nothing writable must leave the
    resource byte-identical, timestamp included."""
    client, read = _pinned_row(clients, api, module, table, create_path,
                               create_body, pk_at, path_at, read_tmpl, stamp,
                               pinned)
    before = client.get(read)
    assert before.status_code == 200, before.text
    assert _dig(before.json(), stamp) == pinned
    client.put(read, json={})
    after = client.get(read)
    assert _dig(after.json(), stamp) == pinned
    assert after.json() == before.json()


@pytest.mark.parametrize(MTIME_PARAMS, MTIME_LIARS,
                         ids=[c[0] for c in MTIME_LIARS])
def test_arriving_update_still_moves_its_timestamp_when_a_field_lands(
        clients, api, module, table, create_path, create_body, pk_at, path_at,
        read_tmpl, stamp, pinned, writes, at, value):
    """The other half of the lock: refusing the empty body must not have cost
    these routes the bump a real write is supposed to produce."""
    client, read = _pinned_row(clients, api, module, table, create_path,
                               create_body, pk_at, path_at, read_tmpl, stamp,
                               pinned)
    r = client.put(read, json=writes)
    assert r.status_code == 200, r.text
    after = client.get(read)
    assert _dig(after.json(), at) == value
    assert _dig(after.json(), stamp) != pinned


def test_gitlab_put_restating_the_same_state_does_not_move_updated_at(clients):
    """gitlab's second mtime lie, found sweeping the three services' other
    mutating routes. `state_event` is a declared field, so the route guard
    passes it through; only the data layer can tell that reopening an already
    open issue collects no change. Same shape as the github case above."""
    gl = clients["gitlab-api"]
    before = gl.get(GL_ISSUE)
    if before.status_code != 200:
        pytest.skip("seed issue already removed by the fleet smoke sweep")
    seeded = before.json()
    restate = "close" if seeded["state"] == "closed" else "reopen"
    r = gl.put(GL_ISSUE, json={"state_event": restate})
    assert r.status_code == 200, r.text
    after = gl.get(GL_ISSUE).json()
    assert after["state"] == seeded["state"]
    assert after["updated_at"] == seeded["updated_at"]


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
