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
# figma-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def figma():
    with _client("figma-api") as c:
        yield c


def test_figma_get_me_surfaces_teams(figma):
    me = figma.get("/v1/me").json()
    assert me["teams"], "team id in team.json was never surfaced on /v1/me"
    assert set(me["teams"][0]) == {"id", "name"}


def test_figma_teams_route_matches_projects_route(figma):
    teams = figma.get("/v1/teams")
    assert teams.status_code == 200, teams.text
    team_id = teams.json()["teams"][0]["id"]
    assert figma.get(f"/v1/teams/{team_id}/projects").status_code == 200


def test_figma_files_route_lists_recent_files(figma):
    r = figma.get("/v1/files")
    assert r.status_code == 200, r.text
    files = r.json()["files"]
    assert files
    assert all({"key", "name", "last_modified"} <= set(f) for f in files)
    assert [f["last_modified"] for f in files] == sorted(
        (f["last_modified"] for f in files), reverse=True)
    assert figma.get(f"/v1/files/{files[0]['key']}").status_code == 200


# ---------------------------------------------------------------------------
# shippo-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shippo():
    with _client("shippo-api") as c:
        yield c


def test_shippo_list_shipments(shippo):
    r = shippo.get("/shipments")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"count", "next", "previous", "results"}
    assert body["results"]
    assert shippo.get(f"/shipments/{body['results'][0]['object_id']}").status_code == 200


def test_shippo_list_shipments_paginates(shippo):
    body = shippo.get("/shipments", params={"page": 1, "results": 1}).json()
    assert len(body["results"]) == 1
    assert body["next"] == 2 and body["previous"] is None


def test_shippo_get_rate_by_id(shippo):
    shipment_id = shippo.get("/shipments").json()["results"][0]["object_id"]
    rate_id = shippo.get(f"/shipments/{shipment_id}/rates").json()["results"][0]["object_id"]
    r = shippo.get(f"/rates/{rate_id}")
    assert r.status_code == 200, r.text
    assert r.json()["object_id"] == rate_id
    assert shippo.get("/rates/rate-nope").status_code == 404


def test_shippo_list_transactions(shippo):
    r = shippo.get("/transactions")
    assert r.status_code == 200, r.text
    assert r.json()["results"]


def test_shippo_list_addresses(shippo):
    r = shippo.get("/addresses")
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results
    assert shippo.get(f"/addresses/{results[0]['object_id']}").status_code == 200


def test_shippo_transaction_accepts_real_async_key(shippo):
    rate_id = shippo.get("/shipments").json()["results"][0]["rates"][0]["object_id"]
    r = shippo.post("/transactions", json={"rate": rate_id, "async": False})
    assert r.status_code == 201, r.text
    txn = r.json()
    assert shippo.get(f"/transactions/{txn['object_id']}").status_code == 200
    track = shippo.get(f"/tracks/{txn['carrier']}/{txn['tracking_number']}")
    assert track.status_code == 200, track.text
    assert track.json()["tracking_status"]["status"] == "PRE_TRANSIT"


def test_shippo_address_rejects_unknown_field(shippo):
    r = shippo.post("/addresses", json={
        "name": "Noor Aziz", "street1": "22 Greenway Dr", "city": "Seattle",
        "state": "WA", "zip": "98101", "country": "US", "steet2": "typo",
    })
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# box-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def box():
    with _client("box-api") as c:
        yield c


def test_box_every_advertised_record_has_a_blob(box):
    missing = _data_module(box.app, "box_data").missing_blobs()
    assert missing == [], f"files.json advertises records with no fixture: {missing}"


def test_box_every_advertised_record_downloads(box):
    files = _walk_box_files(box)
    assert len(files) == 7
    for f in files:
        r = box.get(f"/2.0/files/{f['id']}/content")
        assert r.status_code in (200, 415), (f["name"], r.status_code, r.text)


def _walk_box_files(box):
    seen, out, queue = set(), [], ["0"]
    while queue:
        folder_id = queue.pop()
        if folder_id in seen:
            continue
        seen.add(folder_id)
        r = box.get(f"/2.0/folders/{folder_id}/items", params={"limit": 1000})
        if r.status_code != 200:
            continue
        for e in r.json()["entries"]:
            if e["type"] == "folder":
                queue.append(e["id"])
            else:
                out.append(e)
    return out


def test_box_pdf_and_yaml_fixtures_extract(box):
    arch = next(e for e in _walk_box_files(box) if e["name"] == "architecture.pdf")
    r = box.get(f"/2.0/files/{arch['id']}/content")
    assert r.status_code == 200, r.text
    assert "Architecture" in r.json()["content"]
    spec = next(e for e in _walk_box_files(box) if e["name"] == "api-spec.yaml")
    r = box.get(f"/2.0/files/{spec['id']}/content")
    assert r.status_code == 200, r.text
    assert "openapi" in r.json()["content"]


# ---------------------------------------------------------------------------
# notion-api
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def notion():
    with _client("notion-api") as c:
        yield c


def _notion_store(notion):
    return _data_module(notion.app, "notion_data")._store


def _block_row(block_id, page_id, parent_block_id, text):
    return {
        "id": block_id,
        "page_id": page_id,
        "parent_block_id": parent_block_id,
        "type": "paragraph",
        "text": text,
        "order": "99",
        "created_time": "2026-06-01T00:00:00.000Z",
        "last_edited_time": "2026-06-01T00:00:00.000Z",
        "has_children": "false",
        "checked": "",
    }


def test_notion_children_for_parent_equals_page_seed(notion):
    store = _notion_store(notion)
    page_id = "page-task-001"
    store.table("blocks").upsert(
        _block_row("block-realshape", page_id, page_id, "Real-Notion-shaped root block"))
    try:
        results = notion.get(f"/v1/blocks/{page_id}/children").json()["results"]
        assert any(b["id"] == "block-realshape" for b in results), \
            "root block spelled parent_block_id == page_id was invisible"
    finally:
        store.table("blocks").delete("block-realshape")


def test_notion_coercer_nulls_self_referential_parent(notion):
    store = _notion_store(notion)
    row = store.table("blocks").upsert(
        _block_row("block-normalised", "page-task-001", "page-task-001", "x"))
    try:
        assert row["parent_block_id"] is None
    finally:
        store.table("blocks").delete("block-normalised")


def test_notion_children_for_page_absent_from_pages_json(notion):
    store = _notion_store(notion)
    store.table("blocks").upsert(
        _block_row("block-orphan", "page-not-in-pages-json", "page-not-in-pages-json", "y"))
    try:
        results = notion.get("/v1/blocks/page-not-in-pages-json/children").json()["results"]
        assert [b["id"] for b in results] == ["block-orphan"]
    finally:
        store.table("blocks").delete("block-orphan")


def test_notion_nested_children_still_resolve(notion):
    store = _notion_store(notion)
    store.table("blocks").upsert(
        _block_row("block-nested", "page-task-001", "block-001", "nested"))
    try:
        results = notion.get("/v1/blocks/block-001/children").json()["results"]
        assert "block-nested" in [b["id"] for b in results]
        roots = notion.get("/v1/blocks/page-task-001/children").json()["results"]
        assert "block-nested" not in [b["id"] for b in roots]
    finally:
        store.table("blocks").delete("block-nested")


def test_notion_page_update_rejects_unknown_field(notion):
    r = notion.patch("/v1/pages/page-task-001", json={"titel": "typo"})
    assert r.status_code == 422, r.text


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
