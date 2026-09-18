"""Route-layer write-readback regressions: the "200 but nothing changed" class.

Every test here pins a route that used to answer 2xx while dropping part of the
caller's payload, so the lie was only visible on an independent re-read. Each
one therefore writes, then re-reads through a *different* endpoint rather than
trusting the write response body.

Own module-scoped apps, not conftest's fleet-wide `api_dir` parametrization, so
these mutations cannot leak into the smoke suite's shared session client.
"""
from __future__ import annotations

import pytest

from ._helpers import ENV_DIR, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


def _client(api_name: str) -> TestClient:
    return TestClient(load_app(ENV_DIR / api_name))


# ---------------------------------------------------------------------------
# trello-api -- query-bound write routes discarded JSON and form bodies
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trello():
    with _client("trello-api") as c:
        yield c


@pytest.fixture(scope="module")
def trello_list(trello):
    board_id = trello.get("/1/members/me/boards").json()[0]["id"]
    return trello.get(f"/1/boards/{board_id}/lists").json()[0]["id"]


@pytest.fixture()
def trello_card(trello, trello_list):
    """Throwaway card per test: the fleet-wide smoke suite mutates seed rows."""
    r = trello.post(f"/1/cards?idList={trello_list}&name=Regression%20fixture"
                    f"&desc=original%20desc")
    assert r.status_code == 200, r.text
    card_id = r.json()["id"]
    yield card_id
    trello.delete(f"/1/cards/{card_id}")


def test_trello_update_card_json_body_persists(trello, trello_card):
    r = trello.put(f"/1/cards/{trello_card}", json={"desc": "desc from json"})
    assert r.status_code == 200, r.text
    assert trello.get(f"/1/cards/{trello_card}").json()["desc"] == "desc from json"


def test_trello_update_card_form_body_persists(trello, trello_card):
    r = trello.put(f"/1/cards/{trello_card}", content="desc=desc+from+form",
                   headers=FORM_HEADERS)
    assert r.status_code == 200, r.text
    assert trello.get(f"/1/cards/{trello_card}").json()["desc"] == "desc from form"


def test_trello_update_card_json_body_leaves_other_fields_alone(trello, trello_card):
    trello.put(f"/1/cards/{trello_card}", json={"desc": "only the desc moved"})
    card = trello.get(f"/1/cards/{trello_card}").json()
    assert card["name"] == "Regression fixture"
    assert card["closed"] is False


def test_trello_update_card_json_body_coerces_typed_fields(trello, trello_card):
    r = trello.put(f"/1/cards/{trello_card}", json={"closed": True, "pos": 4096.0})
    assert r.status_code == 200, r.text
    card = trello.get(f"/1/cards/{trello_card}").json()
    assert card["closed"] is True
    assert card["pos"] == 4096.0


def test_trello_update_card_query_still_wins_over_body(trello, trello_card):
    r = trello.put(f"/1/cards/{trello_card}?desc=desc%20from%20query",
                   json={"desc": "desc from json"})
    assert r.status_code == 200, r.text
    assert trello.get(f"/1/cards/{trello_card}").json()["desc"] == "desc from query"


def test_trello_update_card_query_only_is_unchanged(trello, trello_card):
    r = trello.put(f"/1/cards/{trello_card}?name=Renamed%20by%20query")
    assert r.status_code == 200, r.text
    card = trello.get(f"/1/cards/{trello_card}").json()
    assert card["name"] == "Renamed by query"
    assert card["desc"] == "original desc"


def test_trello_create_card_json_body_persists(trello, trello_list):
    r = trello.post("/1/cards", json={"idList": trello_list, "name": "Card from json",
                                      "desc": "json desc"})
    assert r.status_code == 200, r.text
    card = trello.get(f"/1/cards/{r.json()['id']}").json()
    assert card["name"] == "Card from json"
    assert card["desc"] == "json desc"
    assert card["idList"] == trello_list


def test_trello_create_card_form_body_persists(trello, trello_list):
    r = trello.post("/1/cards", content=f"idList={trello_list}&name=Card+from+form"
                                        f"&desc=form+desc", headers=FORM_HEADERS)
    assert r.status_code == 200, r.text
    card = trello.get(f"/1/cards/{r.json()['id']}").json()
    assert card["name"] == "Card from form"
    assert card["desc"] == "form desc"


def test_trello_create_card_accepts_json_list_of_members(trello, trello_list):
    member_id = trello.get("/1/members/me").json()["id"]
    r = trello.post("/1/cards", json={"idList": trello_list, "name": "Card with members",
                                      "idMembers": [member_id]})
    assert r.status_code == 200, r.text
    assert trello.get(f"/1/cards/{r.json()['id']}").json()["idMembers"] == [member_id]


def test_trello_create_card_query_only_is_unchanged(trello, trello_list):
    r = trello.post(f"/1/cards?idList={trello_list}&name=Card%20from%20query")
    assert r.status_code == 200, r.text
    card = trello.get(f"/1/cards/{r.json()['id']}").json()
    assert card["name"] == "Card from query"
    assert card["desc"] == ""


def test_trello_create_card_without_required_fields_is_422(trello):
    assert trello.post("/1/cards", json={"desc": "no list, no name"}).status_code == 422


def test_trello_create_card_rejects_unknown_body_field(trello, trello_list):
    r = trello.post("/1/cards", json={"idList": trello_list, "name": "Typo card",
                                      "descriptoin": "misspelled"})
    assert r.status_code == 422, r.text


def test_trello_create_checklist_json_body_persists(trello, trello_card):
    r = trello.post("/1/checklists", json={"idCard": trello_card, "name": "Json checklist"})
    assert r.status_code == 200, r.text
    listed = trello.get(f"/1/cards/{trello_card}/checklists").json()
    assert any(c["name"] == "Json checklist" for c in listed), listed


def test_trello_create_checklist_form_body_persists(trello, trello_card):
    r = trello.post("/1/checklists", content=f"idCard={trello_card}&name=Form+checklist",
                    headers=FORM_HEADERS)
    assert r.status_code == 200, r.text
    listed = trello.get(f"/1/cards/{trello_card}/checklists").json()
    assert any(c["name"] == "Form checklist" for c in listed), listed


def test_trello_create_checklist_query_only_is_unchanged(trello, trello_card):
    r = trello.post(f"/1/checklists?idCard={trello_card}")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Checklist"


# ---------------------------------------------------------------------------
# google-calendar-api -- nested start/end timeZone was dropped, PATCH could not
# express recurrence even though create declared it
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gcal():
    with _client("google-calendar-api") as c:
        yield c


@pytest.fixture(scope="module")
def gcal_events_path():
    return "/calendar/v3/calendars/primary/events"


@pytest.fixture()
def gcal_event(gcal, gcal_events_path):
    r = gcal.post(gcal_events_path, json={
        "summary": "Regression fixture",
        "start": {"dateTime": "2026-07-01T10:00:00-04:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-07-01T11:00:00-04:00", "timeZone": "America/New_York"},
        "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"],
    })
    assert r.status_code == 201, r.text
    event_id = r.json()["id"]
    yield event_id
    gcal.delete(f"{gcal_events_path}/{event_id}")


def test_gcal_create_event_persists_time_zone(gcal, gcal_events_path, gcal_event):
    event = gcal.get(f"{gcal_events_path}/{gcal_event}").json()
    assert event["start"]["timeZone"] == "America/New_York"
    assert event["end"]["timeZone"] == "America/New_York"


def test_gcal_create_event_persists_recurrence(gcal, gcal_events_path, gcal_event):
    event = gcal.get(f"{gcal_events_path}/{gcal_event}").json()
    assert event["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=3"]


def test_gcal_patch_event_persists_time_zone(gcal, gcal_events_path, gcal_event):
    r = gcal.patch(f"{gcal_events_path}/{gcal_event}", json={
        "start": {"dateTime": "2026-07-02T10:00:00+09:00", "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": "2026-07-02T11:00:00+09:00", "timeZone": "Asia/Tokyo"},
    })
    assert r.status_code == 200, r.text
    event = gcal.get(f"{gcal_events_path}/{gcal_event}").json()
    assert event["start"]["timeZone"] == "Asia/Tokyo"
    assert event["end"]["timeZone"] == "Asia/Tokyo"
    assert event["start"]["dateTime"] == "2026-07-02T10:00:00+09:00"


def test_gcal_patch_event_persists_recurrence(gcal, gcal_events_path, gcal_event):
    r = gcal.patch(f"{gcal_events_path}/{gcal_event}",
                   json={"recurrence": ["RRULE:FREQ=WEEKLY;COUNT=2"]})
    assert r.status_code == 200, r.text
    event = gcal.get(f"{gcal_events_path}/{gcal_event}").json()
    assert event["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=2"]


def test_gcal_event_without_time_zone_keeps_calendar_default(gcal, gcal_events_path):
    """A zone-less payload must still serve the old hardcoded default, which is
    also the shape every seed row takes: the mock's store is process-global, so
    asserting against a named seed id would race the fleet smoke suite's
    DELETEs."""
    r = gcal.post(gcal_events_path, json={
        "summary": "No zone named",
        "start": {"dateTime": "2026-08-01T10:00:00-07:00"},
        "end": {"dateTime": "2026-08-01T11:00:00-07:00"},
    })
    assert r.status_code == 201, r.text
    event_id = r.json()["id"]
    try:
        event = gcal.get(f"{gcal_events_path}/{event_id}").json()
        assert event["start"]["timeZone"] == "America/Los_Angeles"
        assert event["end"]["timeZone"] == "America/Los_Angeles"
    finally:
        gcal.delete(f"{gcal_events_path}/{event_id}")


def test_gcal_serialized_event_hides_internal_time_zone_columns(gcal, gcal_events_path,
                                                                gcal_event):
    event = gcal.get(f"{gcal_events_path}/{gcal_event}").json()
    assert "start_time_zone" not in event
    assert "end_time_zone" not in event


def test_gcal_patch_event_rejects_unknown_field(gcal, gcal_events_path, gcal_event):
    r = gcal.patch(f"{gcal_events_path}/{gcal_event}", json={"sumary": "misspelled"})
    assert r.status_code == 422, r.text


def test_gcal_create_event_rejects_unknown_time_block_field(gcal, gcal_events_path):
    r = gcal.post(gcal_events_path, json={
        "summary": "Bad block",
        "start": {"dateTime": "2026-08-01T10:00:00-07:00", "timezone": "Asia/Tokyo"},
        "end": {"dateTime": "2026-08-01T11:00:00-07:00"},
    })
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# asana-api -- POST /tasks stored data.projects but never served it back, so a
# caller re-reading the task saw it orphaned from its project
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def asana():
    with _client("asana-api") as c:
        yield c


@pytest.fixture(scope="module")
def asana_project(asana):
    return asana.get("/api/1.0/projects").json()["data"][0]["gid"]


def test_asana_create_task_projects_reads_back(asana, asana_project):
    r = asana.post("/api/1.0/tasks",
                   json={"data": {"name": "Projects regression", "projects": [asana_project]}})
    assert r.status_code == 201, r.text
    task = asana.get(f"/api/1.0/tasks/{r.json()['data']['gid']}").json()["data"]
    assert [p["gid"] for p in task["projects"]] == [asana_project]


def test_asana_created_task_is_listed_under_its_project(asana, asana_project):
    r = asana.post("/api/1.0/tasks",
                   json={"data": {"name": "Membership regression",
                                  "projects": [asana_project]}})
    assert r.status_code == 201, r.text
    gid = r.json()["data"]["gid"]
    listed = asana.get(f"/api/1.0/projects/{asana_project}/tasks").json()["data"]
    assert gid in [t["gid"] for t in listed]


def test_asana_create_task_singular_project_reads_back(asana, asana_project):
    r = asana.post("/api/1.0/tasks",
                   json={"data": {"name": "Singular project", "project": asana_project}})
    assert r.status_code == 201, r.text
    task = asana.get(f"/api/1.0/tasks/{r.json()['data']['gid']}").json()["data"]
    assert [p["gid"] for p in task["projects"]] == [asana_project]


def test_asana_task_without_project_serves_empty_projects(asana):
    r = asana.post("/api/1.0/tasks", json={"data": {"name": "No project"}})
    assert r.status_code == 201, r.text
    task = asana.get(f"/api/1.0/tasks/{r.json()['data']['gid']}").json()["data"]
    assert task["projects"] == []


def test_asana_seed_task_serves_projects(asana):
    task = asana.get("/api/1.0/tasks/1205000000004001").json()["data"]
    assert [p["gid"] for p in task["projects"]] == ["1203000000002001"]
    assert task["memberships"][0]["project"]["gid"] == "1203000000002001"


def test_asana_create_task_rejects_unknown_field(asana, asana_project):
    r = asana.post("/api/1.0/tasks",
                   json={"data": {"name": "Typo task", "notez": "misspelled"}})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# linkedin-api -- the engagement counters were nested into socialDetail at load
# time and the flat columns dropped, so the shape a post was seeded and written
# in was not the shape it was served in
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def linkedin():
    with _client("linkedin-api") as c:
        yield c


def test_linkedin_seed_post_serves_counts_flat_and_nested(linkedin):
    post = linkedin.get("/v2/posts/6001").json()
    assert post["like_count"] == 318
    assert post["comment_count"] == 42
    assert post["share_count"] == 57
    assert post["socialDetail"] == {"likeCount": 318, "commentCount": 42, "shareCount": 57}


def test_linkedin_listed_posts_all_carry_the_engagement_columns(linkedin):
    for post in linkedin.get("/v2/posts").json()["elements"]:
        assert {"like_count", "comment_count", "share_count"} <= set(post)
        assert post["socialDetail"]["likeCount"] == post["like_count"]


def test_linkedin_create_post_counts_read_back(linkedin):
    r = linkedin.post("/v2/posts", json={"commentary": "Counted on create",
                                         "like_count": 9, "comment_count": 4,
                                         "share_count": 2})
    assert r.status_code == 201, r.text
    post = linkedin.get(f"/v2/posts/{r.json()['id']}").json()
    assert (post["like_count"], post["comment_count"], post["share_count"]) == (9, 4, 2)
    assert post["socialDetail"] == {"likeCount": 9, "commentCount": 4, "shareCount": 2}


def test_linkedin_create_post_without_counts_starts_at_zero(linkedin):
    r = linkedin.post("/v2/posts", json={"commentary": "No counts named"})
    assert r.status_code == 201, r.text
    post = linkedin.get(f"/v2/posts/{r.json()['id']}").json()
    assert (post["like_count"], post["comment_count"], post["share_count"]) == (0, 0, 0)
    assert post["socialDetail"] == {"likeCount": 0, "commentCount": 0, "shareCount": 0}


def test_linkedin_create_post_rejects_unknown_field(linkedin):
    r = linkedin.post("/v2/posts", json={"commentary": "Typo post", "likes": 3})
    assert r.status_code == 422, r.text


def test_linkedin_injected_post_counts_reach_the_public_get(monkeypatch):
    """The willie R9 op, replayed verbatim: an admin-plane upsert of a post row
    naming all three counters. It used to land on keys no getter read, so the
    injector's serving-shape check called all three orphans and the agent never
    saw them. Cleans up after itself -- the store is per-process and shared."""
    monkeypatch.setenv("MOCK_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MOCK_ADMIN_ALLOWLIST", "")
    row = {
        "id": "urn:li:share:c105",
        "author_id": "urn:li:organization:5005",
        "commentary": "The cross border notification corridor campaign continues.",
        "created_at": "2026-10-19T04:55:00+00:00",
        "like_count": "12",
        "comment_count": "1",
        "share_count": "1",
        "visibility": "PUBLIC",
    }
    with _client("linkedin-api") as c:
        assert c.post("/admin/data/posts", json={"row": row}).status_code == 200
        try:
            stored = c.get(f"/admin/data/posts/{row['id']}").json()
            assert {"like_count", "comment_count", "share_count"} <= set(stored)
            assert stored["like_count"] == "12", "the store keeps what was written"

            # ... and the projection serves it as the int every seeded post
            # serves, so an injected row is not tellable from a seeded one.
            served = next(p for p in c.get("/v2/posts").json()["elements"]
                          if p["id"] == row["id"])
            assert (served["like_count"], served["comment_count"],
                    served["share_count"]) == (12, 1, 1)
            assert served["socialDetail"] == {"likeCount": 12, "commentCount": 1,
                                              "shareCount": 1}
        finally:
            c.delete(f"/admin/data/posts/{row['id']}")
