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

from ._helpers import ENV_DIR, data_module, load_app

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
# microsoft-teams-api -- the membership class asana's departed block held.
#
# asana's defect was a task's `projects` membership stored on create and never
# served back, so a re-read saw the row orphaned from its parent. The converged
# fleet's spelling of that class is `teams.member_ids`: a list-valued column
# nobody serves directly, read only by the `_ME in member_ids` filter behind
# GET /v1.0/me/joinedTeams. §4 of the convergence dossier calls it a clone of
# the trello bug and predicts the shared `opt_csv_list` hoist auto-fixes it;
# these are that prediction's pinning proof, which the hoist shipped without.
#
# Both seed spellings are exercised because that is where the trello bug lived:
# a per-service `.split(";")` handles the ';'-joined string and turns a
# JSON-array seed into garbage, and `_ME in <string>` then degrades to a
# SUBSTRING match, so the positive half alone would pass over the defect. The
# negative half is what makes the pair load-bearing.
#
# Each case writes the membership through the store -- which is what
# POST /admin/data/{table} calls -- and restores the seed afterwards, so the
# fleet-wide smoke suite sharing this process-global store cannot see drift.
# ---------------------------------------------------------------------------

TEAM_ENG = "19:team-eng0001@thread.tacv2"


@pytest.fixture(scope="module")
def teams():
    with _client("microsoft-teams-api") as c:
        yield c


@pytest.fixture()
def teams_membership(teams):
    table = data_module(teams.app, "microsoft_teams_data")._store.table("teams")
    seeded = dict(table.get(TEAM_ENG))

    def write(member_ids):
        table.upsert({**seeded, "member_ids": member_ids})

    yield write
    table.upsert(seeded)


def _joined(teams):
    r = teams.get("/v1.0/me/joinedTeams")
    assert r.status_code == 200, r.text
    return [t["id"] for t in r.json()["value"]]


def _seed_row(member_ids):
    return {"id": TEAM_ENG, "display_name": "Engineering", "description": "d",
            "visibility": "private", "is_archived": "false",
            "web_url": "https://example.invalid/t", "member_ids": member_ids}


@pytest.mark.parametrize("member_ids", [
    ["user-001", "user-009"],
    "user-001;user-009",
])
def test_teams_coercer_normalises_either_seed_spelling_to_a_list(teams, member_ids):
    """Runs the LOADER, not an upsert: `opt_csv_list` lives in the coercer, so a
    post-load store write goes in verbatim and never reaches it. A per-service
    `.split(";")` bypass would stringify the JSON-array spelling into a single
    junk element here, which is the trello bug in its original form."""
    coerce = data_module(teams.app, "microsoft_teams_data")._coerce_teams
    assert coerce([_seed_row(member_ids)])[0]["member_ids"] == [
        "user-001", "user-009"]


@pytest.mark.parametrize("member_ids", [
    ["user-001", "user-009"],
    "user-001;user-009",
])
def test_teams_membership_survives_either_seed_spelling(teams, teams_membership,
                                                        member_ids):
    teams_membership(member_ids)
    assert TEAM_ENG in _joined(teams)


@pytest.mark.parametrize("member_ids", [
    ["user-002", "user-003"],
    "user-002;user-003",
])
def test_teams_membership_filter_actually_reads_the_list(teams, teams_membership,
                                                         member_ids):
    """The negative control: drop the caller from the membership in either
    spelling and the team must leave the joined list. Without this, a filter
    that had degraded to a substring match over the raw column would keep the
    positive half green."""
    teams_membership(member_ids)
    assert TEAM_ENG not in _joined(teams)


def test_teams_seeded_membership_is_served_as_a_list_not_a_string(teams):
    table = data_module(teams.app, "microsoft_teams_data")._store.table("teams")
    assert table.get(TEAM_ENG)["member_ids"] == [
        "user-001", "user-002", "user-003", "user-004"]


def test_teams_post_channel_message_rejects_unknown_field(teams):
    channel = teams.get(f"/v1.0/teams/{TEAM_ENG}/channels").json()["value"][0]["id"]
    r = teams.post(f"/v1.0/teams/{TEAM_ENG}/channels/{channel}/messages",
                   json={"body": {"contentType": "html", "content": "Typo probe"},
                         "importnace": "high"})
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


# ---------------------------------------------------------------------------
# contentful-api -- GET /spaces/{space_id} threw the id away and answered 200
# with the one seeded space for any id, and there was no list route to find the
# real id with
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def contentful():
    with _client("contentful-api") as c:
        yield c


@pytest.fixture(scope="module")
def contentful_space_id(contentful):
    return contentful.get("/spaces").json()["items"][0]["id"]


def test_contentful_list_spaces_uses_the_collection_envelope(contentful):
    body = contentful.get("/spaces").json()
    assert body["sys"] == {"type": "Array"}
    assert body["total"] == len(body["items"]) == 1
    assert (body["skip"], body["limit"]) == (0, 1)


def test_contentful_listed_space_is_the_seeded_one(contentful, contentful_space_id):
    assert contentful_space_id == "space-orbit-cms"
    listed = contentful.get("/spaces").json()["items"][0]
    assert listed == contentful.get(f"/spaces/{contentful_space_id}").json()


def test_contentful_get_space_honours_the_seeded_id(contentful, contentful_space_id):
    r = contentful.get(f"/spaces/{contentful_space_id}")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Orbit Labs CMS"


def test_contentful_get_space_rejects_an_unknown_id(contentful):
    r = contentful.get("/spaces/rimrock")
    assert r.status_code == 404, r.text
    assert r.json() == {"error": "Space rimrock not found"}


def test_contentful_nested_routes_still_take_any_space_id(contentful):
    """Only the space route was given an identity. The environment-scoped routes
    stay id-agnostic, which is what the seeded corpora and the update-contract
    suite already address them with."""
    r = contentful.get("/spaces/space-orbit/environments/master/entries")
    assert r.status_code == 200, r.text
    assert r.json()["total"] > 0
