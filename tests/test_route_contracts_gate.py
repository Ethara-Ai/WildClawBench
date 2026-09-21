"""CI gate + invariant tests for script/check_route_contracts.py.

The checker guards the INPUT half of the mock-API persistence contract, which
``script/check_lost_writes.py`` structurally cannot see: where FastAPI decided
to bind each parameter, whether pydantic will keep an unknown key, and whether a
seed row survives the loader that reads it. Three fleet sweeps found one live
silent-200 lie, 126 routes that discard unknown keys, and a seed-coercion class
that shipped three times, so the detectors are only worth having if something
runs them on every commit. That is this module.

Two layers, deliberately:

  * SYNTHETIC FIXTURES pin detector behaviour. Every assertion about what a
    detector says is made against an app or a service built inside the test, so
    the expectations cannot rot when a sibling lands a fix. The pre-fix trello
    ``PUT /1/cards`` shape is reproduced here for exactly that reason -- the
    live route was repaired before this guard landed, and the class still has to
    stay gated.
  * THE FLEET GATE runs the real detectors over ``environment/`` and demands
    zero ERROR findings outside ``script/route_contracts_allowlist.txt``. New
    services and new routes are covered the moment they exist.

The allowlist is load-bearing and is itself tested: entries must be scoped to a
single finding, so allowlisting one defect on a route cannot hide a different
one that appears there later.

This module deliberately does NOT use ``from __future__ import annotations``.
The synthetic apps declare their body models inside the test functions, and
under postponed evaluation FastAPI resolves those annotations against MODULE
globals, fails to find a function-local class, and falls back to binding the
parameter as a QUERY param -- which silently turns every body-model fixture into
a detector-1 fixture.
"""
import importlib.util
import json
import shutil
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_DIR = REPO_ROOT / "environment"
CHECKER = REPO_ROOT / "script" / "check_route_contracts.py"
ALLOWLIST = REPO_ROOT / "script" / "route_contracts_allowlist.txt"

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from fastapi import Body, FastAPI, Request  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402


@pytest.fixture(scope="module")
def crc():
    spec = importlib.util.spec_from_file_location("_crc_test", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _route_findings(crc, app) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for method, path, route in crc.iter_api_routes(app):
        verdict = crc.detect_query_bound_write(method, route)
        if verdict is not None:
            out.append({"method": method, "path": path, "finding": "QUERY_BOUND_WRITE",
                        "tier": verdict[0], "detail": verdict[1]})
        for kind, tier, detail in crc.detect_body_schema(method, route):
            out.append({"method": method, "path": path, "finding": kind,
                        "tier": tier, "detail": detail})
    return out


def _only(findings: List[Dict[str, str]], finding: str) -> List[Dict[str, str]]:
    return [f for f in findings if f["finding"] == finding]


# --- detector 1: the query-binding lie -------------------------------------

def _trello_pre_fix_app() -> FastAPI:
    """``PUT /1/cards/{card_id}`` exactly as it read before the class-A fix.

    Bare optional scalars, so FastAPI binds all six to the query string and the
    route declares no body at all. A client that PUTs the JSON every real Trello
    client sends gets 200 and an unchanged card.
    """
    app = FastAPI()

    @app.put("/1/cards/{card_id}")
    def update_card(card_id: str, name: Optional[str] = None, desc: Optional[str] = None,
                    idList: Optional[str] = None, due: Optional[str] = None,
                    closed: Optional[bool] = None, pos: Optional[float] = None):
        return {"id": card_id}

    return app


def test_all_optional_query_bound_mutation_is_an_error(crc):
    findings = _only(_route_findings(crc, _trello_pre_fix_app()), "QUERY_BOUND_WRITE")
    assert len(findings) == 1
    assert findings[0]["tier"] == "ERROR"
    assert findings[0]["path"] == "/1/cards/{card_id}"
    assert "200" in findings[0]["detail"]
    assert "closed, desc, due, idList, name, pos" in findings[0]["detail"]


def test_required_query_bound_mutation_is_only_a_warning(crc):
    """A required param makes FastAPI answer 422, so the caller finds out."""
    app = FastAPI()

    @app.post("/1/cards")
    def create_card(idList: str, name: str, desc: str = ""):
        return {}

    findings = _only(_route_findings(crc, app), "QUERY_BOUND_WRITE")
    assert [f["tier"] for f in findings] == ["WARN"]
    assert "422" in findings[0]["detail"]


def test_handler_that_takes_the_raw_request_is_exempt(crc):
    """The shape the class-A fix uses: the handler reads and merges the body
    itself, which no signature-level check can see, so it must not be flagged."""
    app = FastAPI()

    @app.put("/1/cards/{card_id}")
    async def update_card(card_id: str, request: Request, name: Optional[str] = None):
        await request.body()
        return {"id": card_id}

    assert _only(_route_findings(crc, app), "QUERY_BOUND_WRITE") == []


def test_body_model_route_is_never_query_bound(crc):
    app = FastAPI()

    class CardBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: Optional[str] = None

    @app.put("/1/cards/{card_id}")
    def update_card(card_id: str, body: CardBody, fields: Optional[str] = None):
        return {}

    assert _only(_route_findings(crc, app), "QUERY_BOUND_WRITE") == []


def test_read_shaped_routes_are_never_query_bound(crc):
    """GET carries its parameters in the query string by definition, and a
    mutating route with no parameters at all has no payload to lose."""
    app = FastAPI()

    @app.get("/1/cards")
    def list_cards(board: Optional[str] = None, limit: int = 50):
        return []

    @app.post("/1/cards/{card_id}/archive")
    def archive(card_id: str):
        return {}

    assert _only(_route_findings(crc, app), "QUERY_BOUND_WRITE") == []


# --- detector 2: extra laxity ----------------------------------------------

def test_body_model_without_extra_forbid_is_an_error(crc):
    app = FastAPI()

    class TicketBody(BaseModel):
        subject: Optional[str] = None

    @app.post("/tickets")
    def create(body: TicketBody):
        return {}

    findings = _only(_route_findings(crc, app), "OPEN_BODY_SCHEMA")
    assert [f["tier"] for f in findings] == ["ERROR"]
    assert "TicketBody" in findings[0]["detail"]


def test_monday_reference_shape_is_clean(crc):
    """The healthy reference: extra="forbid" plus a typed Dict field for the
    genuinely open ``column_values`` payload. It must produce nothing at all --
    a Dict-typed FIELD is not a Dict-typed BODY."""
    app = FastAPI()

    class ItemCreateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        board_id: str
        item_name: str
        group_id: Optional[str] = None
        column_values: Optional[Dict[str, Any]] = None

    @app.post("/v2/items", status_code=201)
    def create_item(body: ItemCreateBody):
        return {}

    assert _route_findings(crc, app) == []


def test_extra_allow_is_still_an_error_without_an_allowlist_entry(crc):
    """Deliberate open schemas (salesforce sObjects) are legitimate, but the
    decision belongs in the allowlist where a human signed for it -- not in a
    detector that cannot tell 'deliberate' from 'never thought about it'."""
    app = FastAPI()

    class SObjectBody(BaseModel):
        model_config = ConfigDict(extra="allow")
        fields: Optional[Dict[str, Any]] = None

    @app.post("/sobjects/{sobject}")
    def create(sobject: str, body: SObjectBody):
        return {}

    assert [f["tier"] for f in _only(_route_findings(crc, app), "OPEN_BODY_SCHEMA")] == ["ERROR"]


def test_lax_nested_model_is_caught_through_a_strict_parent(crc):
    """The parent forbids extras; the nested model does not, so a key inside
    ``parent`` is still dropped in silence."""
    app = FastAPI()

    class PageParent(BaseModel):
        database_id: Optional[str] = None

    class PageCreateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        parent: Optional[PageParent] = None

    @app.post("/v1/pages")
    def create(body: PageCreateBody):
        return {}

    findings = _only(_route_findings(crc, app), "OPEN_BODY_SCHEMA")
    assert len(findings) == 1
    assert "PageParent" in findings[0]["detail"]
    assert "PageCreateBody" not in findings[0]["detail"]


class Block(BaseModel):
    """Module scope so the forward reference to itself resolves."""

    model_config = ConfigDict(extra="forbid")
    children: Optional[List["Block"]] = None


def test_self_referential_model_terminates(crc):
    """A block tree references itself; the model walk must not recurse forever."""
    app = FastAPI()

    @app.patch("/v1/blocks/{block_id}")
    def append(block_id: str, body: Block):
        return {}

    assert _route_findings(crc, app) == []


def test_bare_mapping_bodies_warn(crc):
    app = FastAPI()

    @app.post("/1/indexes/{index}")
    def add_object(index: str, body: Dict[str, Any]):
        return {}

    @app.post("/api.xro/2.0/Invoices")
    def create_invoice(body: dict = Body(...)):
        return {}

    findings = _only(_route_findings(crc, app), "UNTYPED_BODY")
    assert sorted(f["path"] for f in findings) == ["/1/indexes/{index}", "/api.xro/2.0/Invoices"]
    assert {f["tier"] for f in findings} == {"WARN"}


def test_get_routes_are_not_body_checked(crc):
    app = FastAPI()

    class QueryBody(BaseModel):
        q: Optional[str] = None

    @app.get("/search")
    def search(q: Optional[str] = None):
        return {"body": QueryBody().model_dump()}

    assert _route_findings(crc, app) == []


# --- detector 4: create/update asymmetry -----------------------------------

def test_update_model_missing_a_create_field_warns(crc):
    app = FastAPI()

    class MeetingCreateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        topic: str
        type: int = 2

    class MeetingUpdateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        topic: Optional[str] = None

    @app.post("/v2/users/{user_id}/meetings")
    def create(user_id: str, body: MeetingCreateBody):
        return {}

    @app.patch("/v2/meetings/{meeting_id}")
    def update(meeting_id: str, body: MeetingUpdateBody):
        return {}

    findings = crc.detect_update_asymmetry(crc.iter_api_routes(app))
    assert len(findings) == 1
    assert findings[0]["tier"] == "WARN"
    assert findings[0]["path"] == "/v2/meetings/{meeting_id}"
    assert "omits type" in findings[0]["detail"]


def test_symmetric_create_update_pair_is_clean(crc):
    app = FastAPI()

    class IssueCreateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: str

    class IssueUpdateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: Optional[str] = None
        sortOrder: Optional[float] = None

    @app.post("/issues")
    def create(body: IssueCreateBody):
        return {}

    @app.patch("/issues/{issue_id}")
    def update(issue_id: str, body: IssueUpdateBody):
        return {}

    assert crc.detect_update_asymmetry(crc.iter_api_routes(app)) == []


def test_update_model_no_route_binds_is_not_reported(crc):
    """A finding nobody can act on is noise: only models a mutating route
    actually accepts are paired."""
    app = FastAPI()

    class TaskCreateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str
        notes: Optional[str] = None

    class TaskUpdateBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: Optional[str] = None

    @app.post("/tasks")
    def create(body: TaskCreateBody):
        return {}

    assert crc.detect_update_asymmetry(crc.iter_api_routes(app)) == []


# --- detector 3: seed round-trip -------------------------------------------

_DATA_MODULE = '''\
from pathlib import Path
import sys as _sys

DATA_DIR = Path(__file__).resolve().parent
_sys.path.insert(0, str(DATA_DIR.parent))

from _mutable_store import get_store, read_seed_with_ctx

_API = "demo-api"
_store = get_store(_API)


def _coerce(rows):
    out = []
    for r in rows:
        row = {{k: v for k, v in r.items() if not k.startswith("__")}}
{coercer}
        out.append(row)
    return out


_store.register(
    "rows",
    primary_key="id",
    initial_loader=lambda: _coerce(read_seed_with_ctx(DATA_DIR / "rows.json", _API, "rows")),
)
_store.eager_load()
'''

_SERVER_MODULE = '''\
from fastapi import FastAPI

import demo_data

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "rows": len(demo_data._store.table("rows").rows())}
'''


def _build_service(root: Path, seed_rows: List[Dict[str, Any]], coercer: str) -> Path:
    """A minimal but REAL service: real store, real seed reader, real loader.

    The coercion itself is written by the test rather than borrowed from
    ``_mutable_store``'s helpers, so these assertions keep holding when the
    shared coercers are hardened.
    """
    env = root / "environment"
    env.mkdir(parents=True, exist_ok=True)
    shutil.copy(ENV_DIR / "_mutable_store.py", env / "_mutable_store.py")
    api_dir = env / "demo-api"
    api_dir.mkdir()
    (api_dir / "rows.json").write_text(json.dumps(seed_rows), encoding="utf-8")
    (api_dir / "demo_data.py").write_text(
        _DATA_MODULE.format(coercer=textwrap.indent(textwrap.dedent(coercer), " " * 8)),
        encoding="utf-8")
    (api_dir / "server.py").write_text(_SERVER_MODULE, encoding="utf-8")
    return api_dir


def _seed_findings(crc, api_dir: Path) -> List[Dict[str, Any]]:
    return [f for f in crc.check_service(api_dir) if f["finding"] == "SEED_COERCION_LOSS"]


def test_stringified_json_list_is_an_error(crc, tmp_path):
    """The member_ids corruption that shipped three times: a JSON list reaches a
    csv-list coercer, ``str(v).split(sep)`` stringifies it, and every id is gone
    behind a python repr the API then serves as data."""
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "member_ids": ["5f1a-a1", "5f1a-a2"]}],
        'row["member_ids"] = [p for p in str(r["member_ids"]).split(";") if p]',
    )
    findings = _seed_findings(crc, api_dir)
    assert len(findings) == 1
    assert findings[0]["tier"] == "ERROR"
    assert findings[0]["path"] == "rows.member_ids"
    assert "python" in findings[0]["detail"] and "repr" in findings[0]["detail"]


def test_non_empty_seed_coerced_to_empty_is_an_error(crc, tmp_path):
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "member_ids": "5f1a-a1;5f1a-a2"}],
        'row["member_ids"] = []',
    )
    findings = _seed_findings(crc, api_dir)
    assert len(findings) == 1
    assert findings[0]["tier"] == "ERROR"
    assert "the seeded value is gone" in findings[0]["detail"]


def test_a_list_of_empties_is_not_a_coercion_loss(crc, tmp_path):
    """A blank CSV cell reaches a list coercer as ``[""]`` and leaves it as
    ``[]``. Both hold nothing, so reporting the round trip as a lost value
    blames a faithful loader for reading the seed correctly."""
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "member_ids": [""]}],
        'row["member_ids"] = [p for p in r["member_ids"] if p]',
    )
    assert _seed_findings(crc, api_dir) == []


def test_correct_round_trip_is_clean(crc, tmp_path):
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "member_ids": "5f1a-a1;5f1a-a2", "closed": "false"}],
        'row["member_ids"] = [p for p in str(r["member_ids"]).split(";") if p]\n'
        'row["closed"] = r["closed"] == "true"',
    )
    assert _seed_findings(crc, api_dir) == []


def test_deliberately_projected_away_field_is_not_reported(crc, tmp_path):
    """A loader that DROPS a column is making a projection; only a column it
    kept and then emptied is a loss."""
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "internal_note": "not served by this API"}],
        'row.pop("internal_note", None)',
    )
    assert _seed_findings(crc, api_dir) == []


def test_empty_seed_value_is_not_reported(crc, tmp_path):
    api_dir = _build_service(
        tmp_path,
        [{"id": "1", "member_ids": ""}],
        'row["member_ids"] = []',
    )
    assert _seed_findings(crc, api_dir) == []


def test_unloadable_service_is_reported_rather_than_skipped(crc, tmp_path):
    api_dir = _build_service(tmp_path, [{"id": "1"}], "pass")
    (api_dir / "server.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    findings = crc.check_service(api_dir)
    assert [f["finding"] for f in findings] == ["SERVICE_UNLOADABLE"]
    assert findings[0]["tier"] == "WARN"
    assert "boom" in findings[0]["detail"]


# --- CLI + allowlist --------------------------------------------------------

def test_main_exits_one_on_an_unallowlisted_error(crc, tmp_path, capsys):
    _build_service(
        tmp_path, [{"id": "1", "member_ids": ["a", "b"]}],
        'row["member_ids"] = [p for p in str(r["member_ids"]).split(";") if p]',
    )
    assert crc.main([str(tmp_path / "environment")]) == 1
    assert "SEED_COERCION_LOSS" in capsys.readouterr().out


def test_main_exits_zero_once_the_finding_is_allowlisted(crc, tmp_path):
    _build_service(
        tmp_path, [{"id": "1", "member_ids": ["a", "b"]}],
        'row["member_ids"] = [p for p in str(r["member_ids"]).split(";") if p]',
    )
    allow = tmp_path / "allow.txt"
    allow.write_text("# known\ndemo-api::SEED rows.member_ids::SEED_COERCION_LOSS\n",
                     encoding="utf-8")
    assert crc.main([str(tmp_path / "environment"), "--allowlist", str(allow)]) == 0


def test_allowlist_scoped_to_another_finding_does_not_suppress(crc, tmp_path):
    """Scoping is the whole point: silencing one class on a route must leave
    every other class on that route armed."""
    _build_service(
        tmp_path, [{"id": "1", "member_ids": ["a", "b"]}],
        'row["member_ids"] = [p for p in str(r["member_ids"]).split(";") if p]',
    )
    allow = tmp_path / "allow.txt"
    allow.write_text("demo-api::SEED rows.member_ids::OPEN_BODY_SCHEMA\n", encoding="utf-8")
    assert crc.main([str(tmp_path / "environment"), "--allowlist", str(allow)]) == 1


def _allowlist_entries() -> List[str]:
    lines = ALLOWLIST.read_text(encoding="utf-8").splitlines()
    return [e for e in (line.split("#", 1)[0].strip() for line in lines) if e]


def test_every_allowlist_entry_is_scoped_to_one_finding():
    unscoped = [e for e in _allowlist_entries() if e.count("::") != 2]
    assert unscoped == [], (
        "route-level entries hide every future defect class on that route: "
        f"{unscoped}")


def test_allowlist_has_no_duplicate_entries():
    entries = _allowlist_entries()
    assert len(entries) == len(set(entries))


def test_allowlist_pending_fix_section_is_labelled():
    """Open debt has to stay legible as debt, not blend into the blessed
    entries. The marker is what tells a reader which half they are in."""
    text = ALLOWLIST.read_text(encoding="utf-8")
    assert "PENDING-FIX" in text
    assert "VERIFIED BENIGN" in text
    assert text.index("VERIFIED BENIGN") < text.index("PENDING-FIX")


def test_every_finding_name_is_documented(crc):
    doc = crc.__doc__ or ""
    for name in ("QUERY_BOUND_WRITE", "OPEN_BODY_SCHEMA", "UNTYPED_BODY",
                 "SEED_COERCION_LOSS", "UPDATE_DROPS_FIELD", "SERVICE_UNLOADABLE"):
        assert name in doc


# --- the fleet gate ---------------------------------------------------------

def test_environment_fleet_has_no_unallowlisted_errors(crc):
    """THE GATE. Any new service or route that regresses one of the guarded
    classes fails here, and the only way to pass is to fix it or to write a
    signed allowlist entry for it."""
    findings, scanned = crc.collect([str(ENV_DIR)])
    assert scanned >= 1, f"no services discovered under {ENV_DIR}"
    kept, suppressed = crc.partition(findings, crc.load_allowlist(ALLOWLIST))
    errors = [f for f in kept if f["tier"] == "ERROR"]
    assert errors == [], "\n".join(
        f"{f['api']} {f['method']} {f['path']} [{f['finding']}] {f['detail']}"
        for f in errors)
    assert suppressed > 0, "the allowlist stopped matching anything -- it is stale"


def test_fleet_scan_covers_every_service_directory(crc):
    """A service that stops importing must not silently leave the scan."""
    findings, scanned = crc.collect([str(ENV_DIR)])
    expected = sum(1 for p in sorted(ENV_DIR.glob("*-api"))
                   if p.is_dir() and (p / "server.py").exists())
    assert scanned == expected
    assert [f for f in findings if f["finding"] == "SERVICE_UNLOADABLE"] == []
