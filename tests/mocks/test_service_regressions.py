"""Write-readback regressions for per-service mock fixes.

Each test pins a bug that previously either dropped an agent's write on the
floor (pydantic ignored the field, the data module never persisted it) or
served a shape the real vendor API does not serve. These use their own
module-scoped apps rather than conftest's fleet-wide `api_dir` parametrization
so a mutation here cannot leak into the smoke suite's shared session client.

Admin/injection writes are simulated with `store.table(name).upsert(...)`,
which is exactly what `POST /admin/data/{table}` calls in admin_plane.py.
"""
from __future__ import annotations

import base64

import pytest

from ._helpers import ENV_DIR, data_module as _data_module, load_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


def _client(api_name: str) -> TestClient:
    return TestClient(load_app(ENV_DIR / api_name))


# ---------------------------------------------------------------------------
# monday-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def monday():
    with _client("monday-api") as c:
        yield c


@pytest.fixture()
def monday_item(monday):
    """Seed rows are mutated by the fleet-wide smoke suite (it DELETEs probe
    ids), so provision a throwaway item per test."""
    r = monday.post("/v2/items", json={"board_id": "board-101",
                                       "item_name": "Regression fixture",
                                       "group_id": "grp-todo"})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    yield item_id
    monday.delete(f"/v2/items/{item_id}")


def test_monday_update_item_persists_item_name(monday, monday_item):
    r = monday.put(f"/v2/items/{monday_item}", json={"item_name": "Renamed by agent"})
    assert r.status_code == 200, r.text
    assert monday.get(f"/v2/items/{monday_item}").json()["name"] == "Renamed by agent"


def test_monday_update_item_persists_column_values(monday, monday_item):
    r = monday.put(f"/v2/items/{monday_item}", json={
        "column_values": {"status": {"text": "Blocked"},
                          "owner": {"text": "Helena Park", "value": "usr-2"}},
    })
    assert r.status_code == 200, r.text
    cols = {c["id"]: c for c in monday.get(f"/v2/items/{monday_item}").json()["column_values"]}
    assert cols["status"]["text"] == "Blocked"
    assert cols["owner"]["text"] == "Helena Park"
    assert cols["owner"]["value"] == "usr-2"


def test_monday_update_item_name_and_columns_together(monday, monday_item):
    r = monday.put(f"/v2/items/{monday_item}", json={
        "item_name": "Both at once",
        "column_id": "status",
        "text": "Doing",
        "column_values": {"owner": {"text": "Priya Nair"}},
    })
    assert r.status_code == 200, r.text
    item = monday.get(f"/v2/items/{monday_item}").json()
    cols = {c["id"]: c for c in item["column_values"]}
    assert item["name"] == "Both at once"
    assert cols["status"]["text"] == "Doing"
    assert cols["owner"]["text"] == "Priya Nair"


def test_monday_update_item_rejects_unknown_field(monday, monday_item):
    r = monday.put(f"/v2/items/{monday_item}", json={"nmae": "typo'd key"})
    assert r.status_code == 422, r.text


def test_monday_board_groups_route(monday):
    r = monday.get("/v2/boards/board-101/groups")
    assert r.status_code == 200, r.text
    ids = [g["id"] for g in r.json()["groups"]]
    assert "grp-todo" in ids
    assert all(g["board_id"] == "board-101" for g in r.json()["groups"])


def test_monday_groups_route_lists_every_board(monday):
    r = monday.get("/v2/groups")
    assert r.status_code == 200, r.text
    assert len({g["board_id"] for g in r.json()["groups"]}) > 1


def test_monday_board_groups_unknown_board_404(monday):
    assert monday.get("/v2/boards/board-nope/groups").status_code == 404


def test_monday_update_item_moves_it_between_boards(monday, monday_item):
    """board_id was declared by create and omitted by update, so an item could
    be born on a board and never leave it -- the caller's move was accepted at
    the schema and dropped before the store."""
    r = monday.put(f"/v2/items/{monday_item}",
                   json={"board_id": "board-102", "group_id": "grp-open"})
    assert r.status_code == 200, r.text
    item = monday.get(f"/v2/items/{monday_item}").json()
    assert item["board_id"] == "board-102"
    assert item["group"]["id"] == "grp-open"


def test_monday_board_move_without_a_group_is_refused(monday, monday_item):
    """A move strands the item outside every group on the new board, so the
    target group travels with it or the move does not happen at all."""
    r = monday.put(f"/v2/items/{monday_item}", json={"board_id": "board-102"})
    assert r.status_code == 400, r.text
    assert monday.get(f"/v2/items/{monday_item}").json()["board_id"] == "board-101"


def test_monday_board_move_to_an_unknown_board_is_refused(monday, monday_item):
    r = monday.put(f"/v2/items/{monday_item}",
                   json={"board_id": "board-nope", "group_id": "grp-open"})
    assert r.status_code == 404, r.text
    assert monday.get(f"/v2/items/{monday_item}").json()["board_id"] == "board-101"


def test_monday_restating_the_current_board_is_a_no_op(monday, monday_item):
    """board_id is a declared field, so the route guard passes a body carrying
    only it; the data layer is what has to notice nothing moved."""
    r = monday.put(f"/v2/items/{monday_item}", json={"board_id": "board-101"})
    assert r.status_code == 200, r.text
    item = monday.get(f"/v2/items/{monday_item}").json()
    assert item["board_id"] == "board-101"
    assert item["group"]["id"] == "grp-todo"


# ---------------------------------------------------------------------------
# gmail-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gmail():
    with _client("gmail-api") as c:
        yield c


def _gmail_store(gmail):
    return _data_module(gmail.app, "gmail_data")._store


def test_gmail_string_internal_date_upsert_does_not_500(gmail):
    store = _gmail_store(gmail)
    store.table("messages").upsert({
        "id": "msg-drifted",
        "thread_id": "thr-drifted",
        "from_addr": "drift@example.com",
        "to_addr": "me@example.com",
        "cc_addr": "",
        "subject": "Injected",
        "snippet": "Injected",
        "body": "Injected body",
        "date": "2026-06-01T00:00:00Z",
        "internal_date": "1780000000000",
        "size_estimate": "13",
        "labels": "INBOX,UNREAD",
        "is_unread": "true",
        "is_starred": "false",
    })
    try:
        r = gmail.get("/gmail/v1/users/me/messages")
        assert r.status_code == 200, r.text
        assert any(m["id"] == "msg-drifted" for m in r.json()["messages"])
        t = gmail.get("/gmail/v1/users/me/threads/thr-drifted")
        assert t.status_code == 200, t.text
    finally:
        store.table("messages").delete("msg-drifted")


def test_gmail_coercer_normalises_injected_row(gmail):
    store = _gmail_store(gmail)
    row = store.table("messages").upsert({
        "id": "msg-coerced",
        "thread_id": "thr-coerced",
        "from_addr": "a@b.c",
        "to_addr": "me@example.com",
        "cc_addr": "",
        "subject": "s",
        "snippet": "s",
        "body": "b",
        "date": "2026-06-01T00:00:00Z",
        "internal_date": "1780000000001",
        "size_estimate": "1",
        "labels": "INBOX,UNREAD",
        "is_unread": "true",
        "is_starred": "false",
    })
    try:
        assert row["internal_date"] == 1780000000001
        assert row["size_estimate"] == 1
        assert row["labels"] == ["INBOX", "UNREAD"]
        assert row["is_unread"] is True and row["is_starred"] is False
    finally:
        store.table("messages").delete("msg-coerced")


def test_gmail_coercer_survives_patch(gmail):
    store = _gmail_store(gmail)
    msg_id = gmail.get("/gmail/v1/users/me/messages").json()["messages"][0]["id"]
    original = store.table("messages").get(msg_id)["internal_date"]
    try:
        patched = store.table("messages").patch(msg_id, {"internal_date": "1790000000000"})
        assert patched["internal_date"] == 1790000000000
        assert gmail.get("/gmail/v1/users/me/messages").status_code == 200
    finally:
        store.table("messages").patch(msg_id, {"internal_date": original})


def test_gmail_message_body_is_base64url(gmail):
    sent = gmail.post("/gmail/v1/users/me/messages/send", json={
        "to": "helena@orbit-labs.com",
        "subject": "Encoding check",
        "body": "line one\nline two — ünicode",
    })
    assert sent.status_code in (200, 201), sent.text
    body = gmail.get(f"/gmail/v1/users/me/messages/{sent.json()['id']}").json()["payload"]["body"]
    assert "=" not in body["data"]
    assert "+" not in body["data"] and "/" not in body["data"]
    pad = "=" * (-len(body["data"]) % 4)
    decoded = base64.urlsafe_b64decode(body["data"] + pad).decode("utf-8")
    assert decoded == "line one\nline two — ünicode"
    assert body["size"] == len(decoded.encode("utf-8"))


def test_gmail_send_rejects_unknown_field(gmail):
    r = gmail.post("/gmail/v1/users/me/messages/send",
                   json={"to": "a@b.c", "subject": "s", "body": "b", "bdoy": "typo"})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# woocommerce-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def woo():
    with _client("woocommerce-api") as c:
        yield c


def test_woo_order_carries_line_items(woo):
    r = woo.post("/wp-json/wc/v3/orders", json={
        "customer_id": 301,
        "billing": {"first_name": "Emma", "last_name": "Wright", "email": "emma@example.com"},
        "line_items": [{"product_id": 201, "quantity": 2}],
    })
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["line_items"], "line_items were discarded on create"
    line = order["line_items"][0]
    assert line["product_id"] == 201
    assert line["quantity"] == 2
    assert line["sku"] == "WC-MUG-201"
    assert line["name"]
    readback = woo.get(f"/wp-json/wc/v3/orders/{order['id']}").json()
    assert readback["line_items"] == order["line_items"]


def test_woo_tax_is_not_a_hardcoded_ten_percent(woo):
    r = woo.post("/wp-json/wc/v3/orders",
                 json={"line_items": [{"product_id": 201, "quantity": 2}]})
    order = r.json()
    subtotal = float(order["subtotal"])
    assert float(order["total_tax"]) == 0.0
    assert float(order["total"]) == pytest.approx(subtotal)


def test_woo_tax_follows_seeded_settings_rate(woo):
    settings = _data_module(woo.app, "woocommerce_data")._store.document("settings")
    original = settings.get()
    settings.set({**original, "tax_rate": "0.08"})
    try:
        order = woo.post("/wp-json/wc/v3/orders",
                         json={"line_items": [{"product_id": 201, "quantity": 2}]}).json()
        assert float(order["total_tax"]) == pytest.approx(round(float(order["subtotal"]) * 0.08, 2))
    finally:
        settings.set(original)


def test_woo_accepts_total_overrides(woo):
    order = woo.post("/wp-json/wc/v3/orders", json={
        "line_items": [{"product_id": 201, "quantity": 1}],
        "total": "99.99",
        "total_tax": "4.50",
    }).json()
    assert order["total"] == "99.99"
    assert order["total_tax"] == "4.50"


def test_woo_seeded_orders_still_serialize(woo):
    orders = woo.get("/wp-json/wc/v3/orders").json()
    assert orders
    assert all("line_items" in o for o in orders)


# ---------------------------------------------------------------------------
# RETIRED WITH THEIR SERVICES (newreq convergence)
#
# figma, shippo, box and notion left the fleet in the convergence, and the four
# 667131a defect classes they pinned left with them:
#
#   * figma      -- seed data loaded but never surfaced on the route that owns
#                   it (/v1/me dropping teams), and a list route missing beside
#                   the by-id route it feeds. Same class now locked on the
#                   arriving side by the reddit/webflow "created row is listed
#                   back" probes and sentry's by-id read below.
#   * shippo     -- the `{count,next,previous,results}` pagination envelope and
#                   a by-id read that 404s an unknown id. Locked now by
#                   test_sentry_issue_update_persists_and_unknown_ids_404.
#   * box        -- every advertised record must have a fixture blob behind it.
#                   No arriving service ships binary blob fixtures, so the class
#                   has no subject on the converged fleet.
#   * notion     -- self-referential parent_block_id coercion. The coercion
#                   helper itself is shared (`_mutable_store`) and is covered by
#                   the seed round-trip suite; the notion-shaped spelling of it
#                   is gone with the service.
#
# The pattern stays documented in this module's header: pin the defect, write
# through the route layer, read back through a DIFFERENT endpoint.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The arriving 25 -- defects the newreq convergence brought in with them.
# Each block pins one finding from the wave-2 hardening; the write-read-back
# specs in _writeback_specs.py cover the id-addressable half, and these cover
# the rest: list-shaped read-backs, and fields that were declared but dropped.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def coinbase():
    with _client("coinbase-api") as c:
        yield c


@pytest.fixture(scope="module")
def plaid():
    with _client("plaid-api") as c:
        yield c


@pytest.fixture(scope="module")
def posthog():
    with _client("posthog-api") as c:
        yield c


@pytest.fixture(scope="module")
def segment():
    with _client("segment-api") as c:
        yield c


@pytest.fixture(scope="module")
def teams():
    with _client("microsoft-teams-api") as c:
        yield c


@pytest.fixture(scope="module")
def kubernetes():
    with _client("kubernetes-api") as c:
        yield c


@pytest.fixture(scope="module")
def webflow():
    with _client("webflow-api") as c:
        yield c


@pytest.fixture(scope="module")
def reddit():
    with _client("reddit-api") as c:
        yield c


@pytest.fixture(scope="module")
def sentry():
    with _client("sentry-api") as c:
        yield c


def _btc_account(coinbase):
    accounts = coinbase.get("/v2/accounts").json()["data"]
    return next(a for a in accounts if a["currency"]["code"] == "BTC")["id"]


def test_coinbase_buy_denominated_in_the_crypto_uses_amount_as_quantity(coinbase):
    r = coinbase.post(f"/v2/accounts/{_btc_account(coinbase)}/buys",
                      json={"amount": "0.5", "currency": "BTC"})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["amount"] == {"amount": "0.50000000", "currency": "BTC"}
    assert data["total"] == {"amount": "32500.00", "currency": "USD"}


def test_coinbase_buy_denominated_in_fiat_converts_at_the_spot_price(coinbase):
    """`currency` used to be declared and never read, so "buy $1000 of BTC"
    silently bought 1000 BTC. 1000 USD / 65000 USD-per-BTC = 0.01538462."""
    r = coinbase.post(f"/v2/accounts/{_btc_account(coinbase)}/buys",
                      json={"amount": "1000.00", "currency": "USD"})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["amount"] == {"amount": "0.01538462", "currency": "BTC"}
    assert data["total"] == {"amount": "1000.00", "currency": "USD"}


def test_coinbase_buy_rejects_a_currency_the_account_cannot_be_denominated_in(coinbase):
    r = coinbase.post(f"/v2/accounts/{_btc_account(coinbase)}/buys",
                      json={"amount": "1", "currency": "EUR"})
    assert r.status_code == 400, r.text
    assert "EUR" in r.json()["error"]


def test_coinbase_sell_honours_the_currency_field_too(coinbase):
    r = coinbase.post(f"/v2/accounts/{_btc_account(coinbase)}/sells",
                      json={"amount": "650.00", "currency": "USD"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["amount"] == {"amount": "0.01000000", "currency": "BTC"}


def test_plaid_institution_lookup_is_scoped_by_country_codes(plaid):
    """`country_codes` was declared on the body and never read."""
    body = {"institution_id": "ins_109512"}
    assert plaid.post("/institutions/get_by_id",
                      json=dict(body, country_codes=["US"])).status_code == 200
    assert plaid.post("/institutions/get_by_id",
                      json=dict(body, country_codes=["GB"])).status_code == 404
    assert plaid.post("/institutions/get_by_id", json=body).status_code == 200


def test_posthog_capture_event_is_served_back_by_the_events_read(posthog):
    assert posthog.post("/capture", json={
        "project_id": 1, "distinct_id": "u-probe", "event": "wrb_capture",
        "properties": {"plan": "pro"}}).status_code == 200
    hit = posthog.get("/api/projects/1/events?event=wrb_capture").json()["results"]
    assert [e["distinct_id"] for e in hit] == ["u-probe"], hit
    assert hit[0]["properties"] == {"plan": "pro"}


def test_posthog_decide_evaluates_flags_without_persisting_anything(posthog):
    before = posthog.get("/api/projects/1/events").json()["count"]
    r = posthog.post("/decide", json={"project_id": 1, "distinct_id": "u-probe"})
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["featureFlags"], dict)
    assert posthog.get("/api/projects/1/events").json()["count"] == before


def test_segment_track_is_served_back_by_the_events_read(segment):
    assert segment.post("/v1/track", json={
        "userId": "u-probe", "event": "WRB Event",
        "properties": {"revenue": 1}}).status_code == 200
    hit = segment.get("/v1/events?userId=u-probe").json()["events"]
    assert [e["event"] for e in hit] == ["WRB Event"], hit


def test_microsoft_teams_message_lands_in_the_channel(teams):
    team = "19:team-eng0001@thread.tacv2"
    channel = teams.get(f"/v1.0/teams/{team}/channels").json()["value"][0]["id"]
    route = f"/v1.0/teams/{team}/channels/{channel}/messages"
    assert teams.post(route, json={"body": {"contentType": "html",
                                            "content": "WRB teams message"},
                                   "importance": "high"}).status_code == 201
    hit = [m for m in teams.get(route).json()["value"]
           if m["body"]["content"] == "WRB teams message"]
    assert len(hit) == 1 and hit[0]["importance"] == "high", hit


def test_kubernetes_scale_patch_persists_the_replica_count(kubernetes):
    route = "/apis/apps/v1/namespaces/prod/deployments/api-gateway"
    assert kubernetes.patch(f"{route}/scale",
                            json={"spec": {"replicas": 7}}).status_code == 200
    assert kubernetes.get(route).json()["spec"]["replicas"] == 7


def test_webflow_created_item_is_listed_back(webflow):
    route = "/v2/collections/660b2a0000000000000002b1/items"
    r = webflow.post(route, json={"fieldData": {"name": "WRB item", "slug": "wrb-item"}})
    assert r.status_code == 202, r.text
    item_id = r.json()["id"]
    listed = {i["id"]: i for i in webflow.get(route).json()["items"]}
    assert item_id in listed, listed.keys()
    assert listed[item_id]["fieldData"]["name"] == "WRB item"


def test_reddit_submitted_post_is_listed_back(reddit):
    r = reddit.post("/api/submit", json={"sr": "homelab", "title": "WRB post",
                                         "kind": "self", "text": "seed body"})
    assert r.status_code == 200, r.text
    post_id = r.json()["json"]["data"]["id"]
    children = reddit.get("/r/homelab/new").json()["data"]["children"]
    hit = [c["data"] for c in children if c["data"]["id"] == post_id]
    assert len(hit) == 1 and hit[0]["title"] == "WRB post", children[:2]


def test_sentry_issue_update_persists_and_unknown_ids_404(sentry):
    route = "/api/0/organizations/orbit-labs/issues/40001/"
    assert sentry.put(route, json={"status": "resolved"}).status_code == 200
    assert sentry.get(route).json()["status"] == "resolved"
    assert sentry.put("/api/0/organizations/orbit-labs/issues/zzz-nope-404/",
                      json={"status": "resolved"}).status_code == 404


def test_paypal_payout_create_persists_under_its_batch_id(paypal_svc):
    """The _pk defect: the write path never lifted batch_header.payout_batch_id
    to the row's registered primary key, so this route raised StoreError."""
    r = paypal_svc.post("/v1/payments/payouts", json={
        "sender_batch_header": {"sender_batch_id": "Regression_01"},
        "items": [{"amount": {"currency_code": "USD", "value": "42.00"},
                   "receiver": "payee@orbit-labs.com"}]})
    assert r.status_code == 201, r.text
    batch_id = r.json()["batch_header"]["payout_batch_id"]
    store = _data_module(paypal_svc.app, "paypal_data")._store
    assert store.table("payouts").get(batch_id) is not None
    back = paypal_svc.get(f"/v1/payments/payouts/{batch_id}")
    assert back.status_code == 200, back.text
    assert back.json()["batch_header"]["amount"]["value"] == "42.00"


@pytest.fixture(scope="module")
def paypal_svc():
    with _client("paypal-api") as c:
        yield c


# ---------------------------------------------------------------------------
# The UPDATE_DROPS_FIELD wave: a field a resource can be born with has to be
# reachable through its update route. Either it lands and serves back, or the
# route reads it and refuses a change the vendor calls immutable -- what it may
# not do is accept the key and drop it.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def confluence():
    with _client("confluence-api") as c:
        yield c


@pytest.fixture()
def confluence_page(confluence):
    r = confluence.post("/wiki/rest/api/content",
                        json={"title": "Regression fixture", "space": {"key": "ENG"},
                              "body": {"storage": {"value": "seed",
                                                   "representation": "storage"}}})
    assert r.status_code == 201, r.text
    yield r.json()["id"]


def test_confluence_update_moves_a_page_to_another_space(confluence, confluence_page):
    read = f"/wiki/rest/api/content/{confluence_page}"
    assert confluence.put(read, json={"space": {"key": "PROD"}}).status_code == 200
    assert confluence.get(read).json()["space"]["key"] == "PROD"


def test_confluence_update_reparents_a_page(confluence, confluence_page):
    parent = confluence.post("/wiki/rest/api/content",
                             json={"title": "Parent fixture",
                                   "space": {"key": "ENG"}}).json()["id"]
    read = f"/wiki/rest/api/content/{confluence_page}"
    assert confluence.put(read, json={"ancestors": [{"id": parent}]}).status_code == 200
    assert confluence.get(read).json()["ancestors"][0]["id"] == parent


def test_confluence_update_reattributes_the_author(confluence, confluence_page):
    read = f"/wiki/rest/api/content/{confluence_page}"
    assert confluence.put(read, json={"created_by": "helena"}).status_code == 200
    assert confluence.get(read).json()["history"]["createdBy"]["username"] == "helena"


def test_confluence_page_cannot_be_its_own_ancestor(confluence, confluence_page):
    read = f"/wiki/rest/api/content/{confluence_page}"
    r = confluence.put(read, json={"ancestors": [{"id": confluence_page}]})
    assert r.status_code == 400, r.text
    assert "ancestor" in r.text


def test_confluence_update_rejects_an_unknown_space(confluence, confluence_page):
    read = f"/wiki/rest/api/content/{confluence_page}"
    assert confluence.put(read, json={"space": {"key": "NOPE"}}).status_code == 404
    assert confluence.get(read).json()["space"]["key"] == "ENG"


def test_confluence_create_honours_the_declared_content_type(confluence):
    """`type` was declared on the create model and never read, so every body
    became a page no matter what it asked for."""
    r = confluence.post("/wiki/rest/api/content",
                        json={"title": "Blog fixture", "type": "blogpost",
                              "space": {"key": "ENG"}})
    assert r.status_code == 201, r.text
    assert confluence.get(f"/wiki/rest/api/content/{r.json()['id']}").json()["type"] == "blogpost"


@pytest.fixture(scope="module")
def contentful():
    with _client("contentful-api") as c:
        yield c


def test_contentful_update_refuses_to_change_an_entrys_content_type(contentful):
    base = "/spaces/space-orbit/environments/master/entries"
    made = contentful.post(base, json={"content_type": "author",
                                       "fields": {"name": "Regression fixture"}})
    assert made.status_code == 201, made.text
    entry_id = made.json()["sys"]["id"]
    try:
        ok = contentful.put(f"{base}/{entry_id}",
                            json={"fields": {"bio": "x"}, "content_type": "author"})
        assert ok.status_code == 200, ok.text
        bad = contentful.put(f"{base}/{entry_id}",
                             json={"fields": {"bio": "y"}, "content_type": "blogPost"})
        assert bad.status_code == 400, bad.text
        entry = contentful.get(f"{base}/{entry_id}").json()
        assert entry["sys"]["contentType"]["sys"]["id"] == "author"
        assert entry["fields"]["bio"] == "x"
    finally:
        contentful.delete(f"{base}/{entry_id}")


@pytest.fixture(scope="module")
def datadog():
    with _client("datadog-api") as c:
        yield c


def test_datadog_update_persists_the_monitor_type(datadog):
    made = datadog.post("/api/v1/monitor", json={
        "name": "Regression fixture", "type": "metric alert",
        "query": "avg(last_5m):avg:system.cpu.user{*} > 90"})
    assert made.status_code == 201, made.text
    monitor_id = made.json()["id"]
    r = datadog.put(f"/api/v1/monitor/{monitor_id}", json={"type": "service check"})
    assert r.status_code == 200, r.text
    assert datadog.get(f"/api/v1/monitor/{monitor_id}").json()["type"] == "service check"


@pytest.fixture(scope="module")
def docusign():
    with _client("docusign-api") as c:
        yield c


DS_ENVELOPES = "/restapi/v2.1/accounts/acct-1/envelopes"


def _envelope(docusign, subject, signer, doc):
    r = docusign.post(DS_ENVELOPES, json={
        "emailSubject": subject, "status": "created",
        "recipients": {"signers": [{"name": signer, "email": f"{signer}@orbit-labs.com"}]},
        "documents": [{"name": doc, "pages": 1}]})
    assert r.status_code == 201, r.text
    return r.json()["envelopeId"]


def test_docusign_update_persists_the_fields_create_could_set(docusign):
    envelope = _envelope(docusign, "Original subject", "ann", "a.pdf")
    r = docusign.put(f"{DS_ENVELOPES}/{envelope}", json={
        "status": "sent", "emailSubject": "Edited subject", "templateId": "tpl-77",
        "senderName": "Priya Nair", "senderEmail": "priya.nair@orbit-labs.com"})
    assert r.status_code == 200, r.text
    got = docusign.get(f"{DS_ENVELOPES}/{envelope}").json()
    assert got["emailSubject"] == "Edited subject"
    assert got["templateId"] == "tpl-77"
    assert got["sender"]["userName"] == "Priya Nair"
    assert got["sender"]["email"] == "priya.nair@orbit-labs.com"


def test_docusign_update_replaces_recipients_and_documents(docusign):
    envelope = _envelope(docusign, "Set fixture", "bob", "b.pdf")
    r = docusign.put(f"{DS_ENVELOPES}/{envelope}", json={
        "status": "created",
        "recipients": {"signers": [{"name": "Carol", "email": "carol@orbit-labs.com"}]},
        "documents": [{"name": "c.pdf", "pages": 4}]})
    assert r.status_code == 200, r.text
    signers = docusign.get(f"{DS_ENVELOPES}/{envelope}/recipients").json()["signers"]
    docs = docusign.get(f"{DS_ENVELOPES}/{envelope}/documents").json()["envelopeDocuments"]
    assert [s["name"] for s in signers] == ["Carol"]
    assert [(d["name"], d["pages"]) for d in docs] == [("c.pdf", 4)]


def test_docusign_created_envelopes_do_not_share_recipient_keys(docusign):
    """The synthesized recipient key used to be a bare ``str(i)``, which is the
    table's primary key -- so the second envelope created through the API
    upserted its signer "1" on top of the first envelope's signer "1" and took
    the row. Wiring the recipient set into update made the collision reachable
    twice over, so the key is now scoped to its envelope."""
    first = _envelope(docusign, "First", "dana", "d.pdf")
    second = _envelope(docusign, "Second", "erik", "e.pdf")
    names = lambda e: [s["name"] for s in
                       docusign.get(f"{DS_ENVELOPES}/{e}/recipients").json()["signers"]]
    assert names(first) == ["dana"]
    assert names(second) == ["erik"]
    docusign.put(f"{DS_ENVELOPES}/{second}", json={
        "status": "created",
        "recipients": {"signers": [{"name": "frank", "email": "frank@orbit-labs.com"}]}})
    assert names(first) == ["dana"]
    assert names(second) == ["frank"]


@pytest.fixture(scope="module")
def gcal():
    with _client("google-calendar-api") as c:
        yield c


def test_google_calendar_patch_persists_creator_and_organizer(gcal):
    base = "/calendar/v3/calendars/amelia@orbit-labs.com/events"
    made = gcal.post(base, json={"summary": "Regression fixture",
                                 "start": {"dateTime": "2026-04-01T10:00:00Z"},
                                 "end": {"dateTime": "2026-04-01T11:00:00Z"}})
    assert made.status_code == 201, made.text
    event_id = made.json()["id"]
    try:
        r = gcal.patch(f"{base}/{event_id}",
                       json={"creator": "helena@orbit-labs.com",
                             "organizer": "jonas@orbit-labs.com"})
        assert r.status_code == 200, r.text
        event = gcal.get(f"{base}/{event_id}").json()
        assert event["creator"] == "helena@orbit-labs.com"
        assert event["organizer"] == "jonas@orbit-labs.com"
        assert event["summary"] == "Regression fixture"
    finally:
        gcal.delete(f"{base}/{event_id}")


@pytest.fixture(scope="module")
def pagerduty():
    with _client("pagerduty-api") as c:
        yield c


def test_pagerduty_update_persists_title_and_urgency(pagerduty):
    made = pagerduty.post("/incidents", json={"title": "Regression fixture",
                                              "service_id": "PS001", "urgency": "high"})
    assert made.status_code == 201, made.text
    incident = made.json()["incident_id"]
    r = pagerduty.put(f"/incidents/{incident}",
                      json={"title": "Retitled by agent", "urgency": "low"})
    assert r.status_code == 200, r.text
    got = pagerduty.get(f"/incidents/{incident}").json()
    assert got["title"] == "Retitled by agent"
    assert got["urgency"] == "low"


def test_pagerduty_moving_a_service_rederives_the_escalation_policy(pagerduty):
    """escalation_policy_id is denormalized off the service, so a move that did
    not re-derive it would leave the incident escalating to the team it left."""
    made = pagerduty.post("/incidents", json={"title": "Move fixture",
                                              "service_id": "PS001"})
    incident = made.json()["incident_id"]
    services = {s["service_id"]: s for s in
                pagerduty.get("/services").json()["services"]}
    target = next(sid for sid in services if sid != "PS001")
    r = pagerduty.put(f"/incidents/{incident}", json={"service_id": target})
    assert r.status_code == 200, r.text
    got = pagerduty.get(f"/incidents/{incident}").json()
    assert got["service_id"] == target
    assert got["escalation_policy_id"] == services[target]["escalation_policy_id"]


def test_pagerduty_update_rejects_an_unknown_service(pagerduty):
    made = pagerduty.post("/incidents", json={"title": "Reject fixture",
                                              "service_id": "PS001"})
    incident = made.json()["incident_id"]
    r = pagerduty.put(f"/incidents/{incident}", json={"service_id": "PS-nope"})
    assert r.status_code in (400, 404), r.text
    assert pagerduty.get(f"/incidents/{incident}").json()["service_id"] == "PS001"


@pytest.fixture(scope="module")
def zendesk():
    with _client("zendesk-api") as c:
        yield c


@pytest.fixture()
def zendesk_ticket(zendesk):
    r = zendesk.post("/api/v2/tickets", json={"ticket": {
        "subject": "Regression fixture", "description": "seed body",
        "requester_id": 1, "organization_id": 1}})
    assert r.status_code == 201, r.text
    yield r.json()["ticket"]["id"]


def test_zendesk_update_persists_subject_requester_and_organization(zendesk,
                                                                    zendesk_ticket):
    read = f"/api/v2/tickets/{zendesk_ticket}"
    r = zendesk.put(read, json={"ticket": {"subject": "Retitled by agent",
                                           "requester_id": 4, "organization_id": 2}})
    assert r.status_code == 200, r.text
    got = zendesk.get(read).json()["ticket"]
    assert got["subject"] == "Retitled by agent"
    assert got["requester_id"] == 4
    assert got["organization_id"] == 2
    assert got["description"] == "seed body"


def test_zendesk_description_is_read_only(zendesk, zendesk_ticket):
    """Zendesk's description IS the ticket's first comment, not a column, so
    the honest answer to an edit is a refusal rather than a silent drop."""
    read = f"/api/v2/tickets/{zendesk_ticket}"
    r = zendesk.put(read, json={"ticket": {"description": "rewritten history"}})
    assert r.status_code == 400, r.text
    assert "read-only" in r.text
    assert zendesk.get(read).json()["ticket"]["description"] == "seed body"


def test_zendesk_restating_the_description_is_not_an_edit(zendesk, zendesk_ticket):
    read = f"/api/v2/tickets/{zendesk_ticket}"
    r = zendesk.put(read, json={"ticket": {"description": "seed body",
                                           "priority": "urgent"}})
    assert r.status_code == 200, r.text
    assert zendesk.get(read).json()["ticket"]["priority"] == "urgent"


def test_zendesk_a_body_naming_nothing_does_not_move_updated_at(zendesk,
                                                                zendesk_ticket):
    """Found sweeping this route while wiring its dropped fields: updated_at was
    stamped outside the `if changes` branch, so an empty ticket envelope moved
    the clock over an untouched row. A comment still earns the stamp -- it is a
    write against the ticket even when no column moved."""
    read = f"/api/v2/tickets/{zendesk_ticket}"
    mod = _data_module(zendesk.app, "zendesk_data")
    mod._store.table("tickets").patch(zendesk_ticket,
                                      {"updated_at": "2026-01-05T09:00:00Z"})
    before = zendesk.get(read).json()["ticket"]
    assert before["updated_at"] == "2026-01-05T09:00:00Z"
    assert zendesk.put(read, json={"ticket": {}}).status_code == 200
    assert zendesk.get(read).json()["ticket"] == before
    assert zendesk.put(read, json={"ticket": {"comment": {"body": "a note"}}}).status_code == 200
    assert zendesk.get(read).json()["ticket"]["updated_at"] != before["updated_at"]


@pytest.fixture(scope="module")
def whatsapp():
    with _client("whatsapp-api") as c:
        yield c


def test_whatsapp_mark_read_writes_the_status_it_was_given(whatsapp):
    """`status` was declared on the body and never read -- the handler wrote a
    hardcoded "read" -- so the field an agent set was accepted and dropped."""
    message = whatsapp.get("/v17.0/messages").json()["data"][0]["message_id"]
    r = whatsapp.post("/v17.0/messages/status",
                      json={"messaging_product": "whatsapp", "status": "read",
                            "message_id": message})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "read"
    served = [m for m in whatsapp.get("/v17.0/messages").json()["data"]
              if m["message_id"] == message]
    assert served and served[0]["status"] == "read"


def test_whatsapp_mark_read_refuses_a_status_graph_does_not_document(whatsapp):
    message = whatsapp.get("/v17.0/messages").json()["data"][0]["message_id"]
    r = whatsapp.post("/v17.0/messages/status",
                      json={"messaging_product": "whatsapp", "status": "delivered",
                            "message_id": message})
    assert r.status_code == 400, r.text


def test_whatsapp_refuses_a_messaging_product_that_is_not_whatsapp(whatsapp):
    """messaging_product is required on every Cloud API send and the only value
    Graph accepts is "whatsapp". The mock declared it and never read it, so
    "sms" was accepted and the message went out over WhatsApp anyway."""
    contact = whatsapp.get("/v17.0/contacts").json()["data"][0]["wa_id"]
    before = len(whatsapp.get("/v17.0/messages").json()["data"])
    r = whatsapp.post("/v17.0/messages", json={
        "messaging_product": "sms", "to": contact, "type": "text",
        "text": {"body": "should not send"}})
    assert r.status_code == 400, r.text
    assert "messaging_product" in r.text
    assert len(whatsapp.get("/v17.0/messages").json()["data"]) == before

    message = whatsapp.get("/v17.0/messages").json()["data"][0]["message_id"]
    bad = whatsapp.post("/v17.0/messages/status",
                        json={"messaging_product": "sms", "status": "read",
                              "message_id": message})
    assert bad.status_code == 400, bad.text
