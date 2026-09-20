"""Fleet-wide write -> read-back coverage for mock services with mutating routes.

`test_service_regressions.py` pins the services whose write/read defects commit
667131a and the wave-2 convergence hardening actually fixed. This suite is the
standing guard for the REST of the mutating fleet: every spec writes through the
real route layer, then reads the resource back through a DIFFERENT route and
asserts the mutation survived the round trip with the right shape.

Read-back is the whole point. A handler that builds a response dict from its own
request body looks correct to any test that only inspects the write response --
it is the second, independent GET that proves the row reached the store in
`environment/_mutable_store.py` rather than being echoed and discarded.

Specs live in `_writeback_specs.py` and the engine that runs them in
`_writeback.py`; add a WriteSpec there rather than another hand-rolled
request/assert pair here. Services whose read-back is list-shaped
(Segment's `GET /v1/events`) don't fit the id-addressed engine and get
explicit tests at the bottom.
"""
from __future__ import annotations

import contextlib

import pytest

from ._helpers import ENV_DIR, load_app
from ._writeback import WriteSpec, check_create, check_delete, check_update
from ._writeback_specs import SPECS

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

IDS = [s.test_id for s in SPECS]


@pytest.fixture(scope="module")
def clients():
    """Lazily load each mock app once per module and close them all at teardown.

    `load_app` is expensive (it execs server.py and loads every seed table), and
    several specs share a service, so the apps are cached by directory name.
    """
    cache: dict = {}
    with contextlib.ExitStack() as stack:
        def get(api: str) -> TestClient:
            if api not in cache:
                cache[api] = stack.enter_context(TestClient(load_app(ENV_DIR / api)))
            return cache[api]
        yield get


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_create_is_readable_back(clients, spec: WriteSpec):
    check_create(clients(spec.api), spec)


@pytest.mark.parametrize("spec", [s for s in SPECS if s.update],
                         ids=[s.test_id for s in SPECS if s.update])
def test_update_persists(clients, spec: WriteSpec):
    client = clients(spec.api)
    check_update(client, spec, check_create(client, spec))


@pytest.mark.parametrize("spec", [s for s in SPECS if s.delete],
                         ids=[s.test_id for s in SPECS if s.delete])
def test_delete_is_not_served_again(clients, spec: WriteSpec):
    client = clients(spec.api)
    check_delete(client, spec, check_create(client, spec))


# ---------------------------------------------------------------------------
# segment-api -- read-back is the ingest event list, not an addressable row.
#
# Re-points slack's departed section. Segment is the converged fleet's only
# many-writers/one-list-reader service: four mutating routes all land in the
# same `GET /v1/events`, which is the shape the id-addressed engine above
# cannot express. W2 already pinned `/v1/track` in
# test_service_regressions.py; the other three are here.
#
# Every probe filters the read by its OWN userId rather than reading the top of
# the list: `get_store` is a process-wide registry, so the fleet smoke suite and
# the forbid probes ingest into this same table.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def segment():
    with TestClient(load_app(ENV_DIR / "segment-api")) as c:
        yield c


def _events(client, user_id: str) -> list:
    r = client.get(f"/v1/events?userId={user_id}")
    assert r.status_code == 200, r.text
    return r.json()["events"]


def test_segment_identify_traits_land_in_the_event_list(segment):
    r = segment.post("/v1/identify", json={"userId": "u-wrb-identify",
                                           "traits": {"email": "wrb@orbit-labs.com",
                                                      "plan": "pro"}})
    assert r.status_code == 200 and r.json()["success"], r.text
    hit = _events(segment, "u-wrb-identify")
    assert [e["type"] for e in hit] == ["identify"], hit
    assert hit[0]["properties"] == {"email": "wrb@orbit-labs.com", "plan": "pro"}


def test_segment_page_properties_land_in_the_event_list(segment):
    r = segment.post("/v1/page", json={"userId": "u-wrb-page", "name": "Pricing",
                                       "properties": {"path": "/pricing"}})
    assert r.status_code == 200 and r.json()["success"], r.text
    hit = _events(segment, "u-wrb-page")
    assert [e["type"] for e in hit] == ["page"], hit
    assert hit[0]["properties"] == {"path": "/pricing", "name": "Pricing"}


def test_segment_batch_ingests_every_member_not_just_the_first(segment):
    r = segment.post("/v1/batch", json={"batch": [
        {"type": "track", "userId": "u-wrb-batch", "event": "WRB Batch A"},
        {"type": "track", "userId": "u-wrb-batch", "event": "WRB Batch B"},
    ]})
    assert r.status_code == 200 and r.json()["ingested"] == 2, r.text
    assert [e["event"] for e in _events(segment, "u-wrb-batch")] == [
        "WRB Batch A", "WRB Batch B"]


# ---------------------------------------------------------------------------
# RETIRED WITH THEIR SERVICES: the doordash/spotify `_pk` join-table probes.
#
# Their class -- a write path upserting a dict literal that carries only the
# natural key columns, which the store rejects because the table is registered
# under a synthetic composite key -- arrived again with paypal payouts
# (`batch_header.payout_batch_id`, §4 of the convergence dossier, confirmed
# live as a 500 StoreError before W2). It is pinned on the converged fleet in
# two places, so nothing is re-derived here: the `paypal-api-payout` WriteSpec
# above drives the create/read-back hop through the engine, and
# test_service_regressions.test_paypal_payout_create_persists_under_its_batch_id
# asserts the row is keyed under the batch id in the store itself.
# ---------------------------------------------------------------------------
