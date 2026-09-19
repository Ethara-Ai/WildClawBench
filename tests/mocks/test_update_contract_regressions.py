"""Update-route contract regressions for the twenty-one hardened write routes.

Every route below used to answer 2xx over a payload it had thrown away. Two
answered byte-identically (zoom, airtable); the other nineteen moved a
timestamp and nothing else, which is strictly worse than silence -- the resource
looks freshly written when none of the requested fields landed.

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

AIRTABLE = "/v0/appNW1studio0001/tblProjects00001"
CONTENTFUL = "/spaces/space-orbit/environments/master/entries"
CLASSROOM = "/v1/courses/course_001"
GH_ISSUES = "/repos/orbit-labs/auth-api/issues"
WP_POSTS = "/wp-json/wp/v2/posts"


def _client(api_name: str) -> TestClient:
    # HarnessV2 ships a different 50-service fleet; skip services not on disk.
    if not (ENV_DIR / api_name / "service.toml").is_file():
        pytest.skip(f"{api_name} is not in this fleet")
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
    ("airtable-api", "PATCH", f"{AIRTABLE}/recProj0000000001",
     {"records": [{"fields": {"Name": "create shape"}}]}, {}, 422),
    ("amazon-seller-api", "PATCH",
     "/listings/2021-08-01/items/A3EXAMPLE1SELLER/FN-SOFA-RVT01",
     {"Title": "wrong case"}, {}, 400),
    ("contentful-api", "PUT", f"{CONTENTFUL}/author-mara",
     {"Fields": {"name": "wrong case"}}, {}, 422),
    ("datadog-api", "PUT", "/api/v1/monitor/1001",
     {"Name": "wrong case"}, {}, 400),
    ("etsy-api", "PUT", "/v3/application/listings/1001",
     {"Title": "wrong case"}, {}, 400),
    ("etsy-api", "PUT", "/v3/application/shops/1/receipts/2001",
     {"Tracking_code": "1Z999"}, {}, 400),
    ("github-api", "PATCH", f"{GH_ISSUES}/142",
     {"Title": "wrong case"}, {}, 400),
    ("google-classroom-api", "PATCH", CLASSROOM,
     {"Name": "wrong case"}, {}, 400),
    ("google-classroom-api", "PATCH", f"{CLASSROOM}/courseWork/cw_101",
     {"Title": "wrong case"}, {}, 400),
    ("google-classroom-api", "PATCH", f"{CLASSROOM}/topics/topic_101",
     {"Name": "wrong case"}, {}, 400),
    ("google-classroom-api", "PATCH",
     f"{CLASSROOM}/courseWork/cw_101/studentSubmissions/sub_001",
     {"AssignedGrade": 95}, {}, 400),
    ("google-classroom-api", "PATCH", f"{CLASSROOM}/announcements/ann_001",
     {"Text": "wrong case"}, {}, 400),
    ("google-drive-api", "PATCH", "/drive/v3/files/folder-eng",
     {"Name": "wrong case"}, {}, 400),
    ("linear-api", "PUT", "/v1/issues/BUG-201",
     {"Title": "wrong case"}, {}, 400),
    ("linear-api", "PUT", "/v1/projects/PROJ-PORTAL",
     {"Name": "wrong case"}, {}, 400),
    ("pinterest-api", "PATCH", "/v5/boards/board_1001",
     {"Name": "wrong case"}, {}, 400),
    ("pinterest-api", "PATCH", "/v5/pins/pin_3001",
     {"Title": "wrong case"}, {}, 400),
    ("servicenow-api", "PATCH", "/api/now/table/incident/inc-0001001",
     {"Short_description": "wrong case"}, {}, 400),
    ("typeform-api", "PUT", "/forms/frm-csat-01",
     {"Title": "wrong case"}, {}, 400),
    ("wordpress-api", "PUT", f"{WP_POSTS}/101",
     {"Title": "wrong case"}, {}, 400),
]

CONTRACT_IDS = [f"{api}-{method}-{path}" for api, method, path, _, _, _ in CONTRACTS]


@pytest.fixture(scope="module")
def clients():
    opened: dict[str, TestClient] = {}
    for api in sorted({c[0] for c in CONTRACTS}):
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
# airtable-api -- every shape that was not {"fields": ...} was discarded
# ---------------------------------------------------------------------------

@pytest.fixture()
def airtable_record(clients):
    air = clients["airtable-api"]
    r = air.post(AIRTABLE, json={"records": [{"fields": {"Name": "Contract fixture",
                                                         "Status": "Active"}}]})
    assert r.status_code == 200, r.text
    record_id = r.json()["records"][0]["id"]
    yield record_id
    air.delete(f"{AIRTABLE}/{record_id}")


@pytest.mark.parametrize("body", [
    {"Fields": {"Name": "wrong case"}},
    {"column_values": {"Name": "monday shape"}},
    {"Name": "bare top level"},
    {"fields": {"Name": "ok"}, "typo": 1},
])
def test_airtable_patch_rejects_every_shape_it_used_to_drop(clients, airtable_record,
                                                            body):
    air = clients["airtable-api"]
    assert air.patch(f"{AIRTABLE}/{airtable_record}", json=body).status_code == 422
    assert air.get(f"{AIRTABLE}/{airtable_record}").json()["fields"]["Name"] == \
        "Contract fixture"


def test_airtable_patch_with_empty_fields_is_refused(clients, airtable_record):
    air = clients["airtable-api"]
    r = air.patch(f"{AIRTABLE}/{airtable_record}", json={"fields": {}})
    assert r.status_code == 400, r.text


def test_airtable_patch_still_merges_the_documented_shape(clients, airtable_record):
    air = clients["airtable-api"]
    r = air.patch(f"{AIRTABLE}/{airtable_record}", json={"fields": {"Name": "Renamed"}})
    assert r.status_code == 200, r.text
    fields = air.get(f"{AIRTABLE}/{airtable_record}").json()["fields"]
    assert fields["Name"] == "Renamed"
    assert fields["Status"] == "Active"


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

def test_amazon_patch_persists_product_type(clients):
    api = clients["amazon-seller-api"]
    path = "/listings/2021-08-01/items/A3EXAMPLE1SELLER/FN-SOFA-RVT01"
    if api.get(path).status_code != 200:
        pytest.skip("seed listing already removed by the fleet smoke sweep")
    assert api.patch(path, json={"productType": "SECTIONAL"}).status_code == 200
    assert api.get(path).json()["listing"]["productType"] == "SECTIONAL"


def test_etsy_receipt_put_persists_status(clients):
    etsy = clients["etsy-api"]
    path = "/v3/application/shops/1/receipts/2001"
    if etsy.get(path).status_code != 200:
        pytest.skip("seed receipt already removed by the fleet smoke sweep")
    assert etsy.put(path, json={"status": "open"}).status_code == 200
    assert etsy.get(path).json()["receipt"]["status"] == "open"


def test_classroom_patch_persists_owner_id(clients):
    gc = clients["google-classroom-api"]
    created = gc.post("/v1/courses", json={"name": "Contract fixture",
                                           "ownerId": "teacher_001"})
    assert created.status_code == 201, created.text
    course_id = created.json()["course"]["id"]
    assert gc.patch(f"/v1/courses/{course_id}",
                    json={"ownerId": "teacher_002"}).status_code == 200
    course = gc.get(f"/v1/courses/{course_id}").json()["course"]
    assert course["ownerId"] == "teacher_002"
    assert course["name"] == "Contract fixture"


def test_classroom_patch_persists_work_type(clients):
    gc = clients["google-classroom-api"]
    created = gc.post(f"{CLASSROOM}/courseWork",
                      json={"title": "Contract fixture", "workType": "ASSIGNMENT"})
    assert created.status_code == 201, created.text
    cw_id = created.json()["courseWork"]["id"]
    try:
        r = gc.patch(f"{CLASSROOM}/courseWork/{cw_id}",
                     json={"workType": "SHORT_ANSWER_QUESTION"})
        assert r.status_code == 200, r.text
        work = gc.get(f"{CLASSROOM}/courseWork/{cw_id}").json()["courseWork"]
        assert work["workType"] == "SHORT_ANSWER_QUESTION"
        assert work["title"] == "Contract fixture"
    finally:
        gc.delete(f"{CLASSROOM}/courseWork/{cw_id}")


@pytest.fixture()
def drive_file(clients):
    drive = clients["google-drive-api"]
    r = drive.post("/drive/v3/files", json={"name": "contract.txt",
                                            "mimeType": "text/plain",
                                            "parents": ["folder-root"]})
    assert r.status_code == 201, r.text
    file_id = r.json()["id"]
    yield file_id
    drive.delete(f"/drive/v3/files/{file_id}")


def test_drive_patch_persists_parents_under_the_create_name(clients, drive_file):
    drive = clients["google-drive-api"]
    r = drive.patch(f"/drive/v3/files/{drive_file}", json={"parents": ["folder-eng"]})
    assert r.status_code == 200, r.text
    assert drive.get(f"/drive/v3/files/{drive_file}").json()["parents"] == ["folder-eng"]


def test_drive_patch_persists_mime_type(clients, drive_file):
    drive = clients["google-drive-api"]
    r = drive.patch(f"/drive/v3/files/{drive_file}", json={"mimeType": "application/pdf"})
    assert r.status_code == 200, r.text
    assert drive.get(f"/drive/v3/files/{drive_file}").json()["mimeType"] == \
        "application/pdf"


def test_drive_patch_add_parents_still_wins_over_parents(clients, drive_file):
    """Precedence is load-bearing: addParents was the only name this route ever
    honoured, so a caller already using it must be unaffected by the new one."""
    drive = clients["google-drive-api"]
    r = drive.patch(f"/drive/v3/files/{drive_file}",
                    json={"addParents": "folder-root", "parents": ["folder-eng"]})
    assert r.status_code == 200, r.text
    assert drive.get(f"/drive/v3/files/{drive_file}").json()["parents"] == \
        ["folder-root"]


def test_linear_put_persists_team_id(clients):
    linear = clients["linear-api"]
    created = linear.post("/v1/issues", json={"title": "Contract fixture",
                                              "teamId": "team-backend",
                                              "description": "seed"})
    assert created.status_code == 201, created.text
    issue_id = created.json()["issue"]["id"]
    try:
        r = linear.put(f"/v1/issues/{issue_id}", json={"teamId": "team-frontend"})
        assert r.status_code == 200, r.text
        issue = linear.get(f"/v1/issues/{issue_id}").json()["issue"]
        assert issue["teamId"] == "team-frontend"
        assert issue["description"] == "seed"
    finally:
        linear.delete(f"/v1/issues/{issue_id}")


@pytest.fixture()
def pinterest_pin(clients):
    pin = clients["pinterest-api"]
    r = pin.post("/v5/pins", json={"board_id": "board_1001", "title": "Contract fixture",
                                   "description": "seed", "media_type": "image",
                                   "dominant_color": "#FFFFFF"})
    assert r.status_code == 201, r.text
    pin_id = r.json()["pin"]["pin_id"]
    yield pin_id
    pin.delete(f"/v5/pins/{pin_id}")


def test_pinterest_patch_persists_dominant_color_and_media_type(clients, pinterest_pin):
    pin = clients["pinterest-api"]
    r = pin.patch(f"/v5/pins/{pinterest_pin}",
                  json={"dominant_color": "#123456", "media_type": "video"})
    assert r.status_code == 200, r.text
    served = pin.get(f"/v5/pins/{pinterest_pin}").json()["pin"]
    assert served["dominant_color"] == "#123456"
    assert served["media_type"] == "video"
    assert served["description"] == "seed"


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


def test_typeform_put_persists_workspace(clients):
    tf = clients["typeform-api"]
    created = tf.post("/forms", json={"title": "Contract fixture",
                                      "workspace": "ws-orbit-labs"})
    assert created.status_code == 201, created.text
    form_id = created.json()["id"]
    r = tf.put(f"/forms/{form_id}", json={"workspace": "ws-archive"})
    assert r.status_code == 200, r.text
    form = tf.get(f"/forms/{form_id}").json()
    assert form["workspace"]["href"].endswith("/ws-archive")
    assert form["title"] == "Contract fixture"


def test_wordpress_put_persists_author(clients):
    wp = clients["wordpress-api"]
    created = wp.post(WP_POSTS, json={"title": "Contract fixture", "content": "seed",
                                      "author": 1})
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    r = wp.put(f"{WP_POSTS}/{post_id}", json={"author": 2})
    assert r.status_code == 200, r.text
    post = wp.get(f"{WP_POSTS}/{post_id}").json()
    assert post["author"] == 2
    assert post["title"]["rendered"] == "Contract fixture"


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


def test_etsy_put_listing_partial_update_keeps_siblings(clients):
    etsy = clients["etsy-api"]
    created = etsy.post("/v3/application/shops/1/listings",
                        json={"title": "Contract fixture", "description": "seed",
                              "price": 42.0, "quantity": 3, "who_made": "i_did",
                              "when_made": "2020_2025", "taxonomy_id": 1})
    assert created.status_code == 201, created.text
    listing_id = created.json()["listing"]["listing_id"]
    try:
        r = etsy.put(f"/v3/application/listings/{listing_id}", json={"quantity": 7})
        assert r.status_code == 200, r.text
        listing = etsy.get(f"/v3/application/listings/{listing_id}").json()["listing"]
        assert listing["quantity"] == 7
        assert listing["title"] == "Contract fixture"
        assert listing["price"] == 42.0
    finally:
        etsy.delete(f"/v3/application/listings/{listing_id}")


def test_linear_put_project_partial_update_keeps_siblings(clients):
    linear = clients["linear-api"]
    created = linear.post("/v1/projects", json={"name": "Contract fixture",
                                                "state": "planned",
                                                "description": "seed"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["id"]
    r = linear.put(f"/v1/projects/{project_id}", json={"state": "started"})
    assert r.status_code == 200, r.text
    project = linear.get(f"/v1/projects/{project_id}").json()["project"]
    assert project["state"] == "started"
    assert project["name"] == "Contract fixture"
    assert project["description"] == "seed"


def test_classroom_patch_topic_and_announcement_still_write(clients):
    gc = clients["google-classroom-api"]
    topic = gc.post(f"{CLASSROOM}/topics", json={"name": "Contract fixture"})
    assert topic.status_code == 201, topic.text
    topic_id = topic.json()["topic"]["topicId"]
    ann = gc.post(f"{CLASSROOM}/announcements", json={"text": "Contract fixture",
                                                      "state": "PUBLISHED"})
    assert ann.status_code == 201, ann.text
    ann_id = ann.json()["announcement"]["id"]
    try:
        assert gc.patch(f"{CLASSROOM}/topics/{topic_id}",
                        json={"name": "Renamed"}).status_code == 200
        assert gc.get(f"{CLASSROOM}/topics/{topic_id}").json()["topic"]["name"] == \
            "Renamed"
        assert gc.patch(f"{CLASSROOM}/announcements/{ann_id}",
                        json={"text": "Edited"}).status_code == 200
        served = gc.get(f"{CLASSROOM}/announcements/{ann_id}").json()["announcement"]
        assert served["text"] == "Edited"
        assert served["state"] == "PUBLISHED"
    finally:
        gc.delete(f"{CLASSROOM}/topics/{topic_id}")
        gc.delete(f"{CLASSROOM}/announcements/{ann_id}")


def test_classroom_patch_submission_grades_still_write(clients):
    gc = clients["google-classroom-api"]
    path = f"{CLASSROOM}/courseWork/cw_101/studentSubmissions/sub_001"
    if gc.get(path).status_code != 200:
        pytest.skip("seed submission already removed by the fleet smoke sweep")
    r = gc.patch(path, json={"assignedGrade": 91.0, "draftGrade": 88.0})
    assert r.status_code == 200, r.text
    served = gc.get(path).json()["studentSubmission"]
    assert served["assignedGrade"] == 91.0
    assert served["draftGrade"] == 88.0


def test_pinterest_patch_board_partial_update_keeps_siblings(clients):
    pin = clients["pinterest-api"]
    created = pin.post("/v5/boards", json={"name": "Contract fixture",
                                           "description": "seed", "privacy": "PUBLIC"})
    assert created.status_code == 201, created.text
    board_id = created.json()["board"]["board_id"]
    r = pin.patch(f"/v5/boards/{board_id}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    board = pin.get(f"/v5/boards/{board_id}").json()["board"]
    assert board["name"] == "Renamed"
    assert board["description"] == "seed"
    assert board["privacy"] == "PUBLIC"


def test_wordpress_put_post_partial_update_keeps_siblings(clients):
    wp = clients["wordpress-api"]
    created = wp.post(WP_POSTS, json={"title": "Contract fixture", "content": "seed",
                                      "excerpt": "blurb", "status": "draft"})
    assert created.status_code == 201, created.text
    post_id = created.json()["id"]
    r = wp.put(f"{WP_POSTS}/{post_id}", json={"status": "publish"})
    assert r.status_code == 200, r.text
    post = wp.get(f"{WP_POSTS}/{post_id}").json()
    assert post["status"] == "publish"
    assert post["title"]["rendered"] == "Contract fixture"
    assert post["excerpt"]["rendered"] == "blurb"


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
