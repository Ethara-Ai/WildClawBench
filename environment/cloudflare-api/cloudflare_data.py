"""Data access module for the Cloudflare API mock service."""

import csv
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import (
    read_csv_with_ctx, get_store,
    strict_int,
    strict_bool,
)

_store = get_store("cloudflare-api")
_API = "cloudflare-api"

_store.register("zones", primary_key="id",
                initial_loader=lambda: _coerce_zones(_load("zones.csv", "zones")))
_store.register("dns", primary_key="id",
                initial_loader=lambda: _coerce_dns(_load("dns_records.csv", "dns")))
_store.register("firewall", primary_key="id",
                initial_loader=lambda: _coerce_firewall(_load("firewall_rules.csv", "firewall")))
_store.register("page_rules", primary_key="id",
                initial_loader=lambda: _coerce_page_rules(_load("page_rules.csv", "page_rules")))


def _zones_rows():
    return _store.table("zones").rows()


def _dns_rows():
    return _store.table("dns").rows()


def _firewall_rows():
    return _store.table("firewall").rows()


def _page_rules_rows():
    return _store.table("page_rules").rows()



def _load(filename, table):
    return read_csv_with_ctx(DATA_DIR / filename, _API, table)


def _strip_ctx(r):
    return {k: v for k, v in r.items() if not k.startswith("__")}


def _now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def _to_bool(v):
    return str(v).strip().lower() == "true"


# ---------------------------------------------------------------------------
# Load + coerce
# ---------------------------------------------------------------------------

def _coerce_zones(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "paused": strict_bool(r, "paused"),
            "development_mode": strict_int(r, "development_mode"),
        })
    return out


def _coerce_dns(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "ttl": strict_int(r, "ttl"),
            "proxied": strict_bool(r, "proxied"),
            "priority": strict_int(r, "priority"),
        })
    return out


def _coerce_firewall(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "paused": strict_bool(r, "paused"),
            "priority": strict_int(r, "priority"),
        })
    return out


def _coerce_page_rules(rows):
    return [{**_strip_ctx(r), "priority": strict_int(r, "priority")} for r in rows]










# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------

def _ok(result):
    return {"success": True, "errors": [], "messages": [], "result": result}


def _err(message, code=1003, status=404):
    return {
        "success": False,
        "errors": [{"code": code, "message": message}],
        "messages": [],
        "result": None,
        "_status": status,
    }


def _new_id():
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


def _zone_exists(zone_id):
    return any(z["id"] == zone_id for z in _zones_rows())


def _serialize_zone(z):
    return {
        "id": z["id"],
        "name": z["name"],
        "status": z["status"],
        "paused": z["paused"],
        "type": z["type"],
        "development_mode": z["development_mode"],
        "plan": {"name": z["plan"]},
        "created_on": z["created_on"],
        "modified_on": z["modified_on"],
    }


def _serialize_dns(r):
    return {
        "id": r["id"],
        "zone_id": r["zone_id"],
        "type": r["type"],
        "name": r["name"],
        "content": r["content"],
        "ttl": r["ttl"],
        "proxied": r["proxied"],
        "priority": r["priority"],
        "created_on": r["created_on"],
        "modified_on": r["modified_on"],
    }


def _serialize_firewall(r):
    return {
        "id": r["id"],
        "description": r["description"],
        "action": r["action"],
        "filter": {"expression": r["expression"]},
        "paused": r["paused"],
        "priority": r["priority"],
        "created_on": r["created_on"],
    }


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

def list_zones(name=None, status=None):
    results = list(_zones_rows())
    if name:
        results = [z for z in results if z["name"] == name]
    if status:
        results = [z for z in results if z["status"] == status]
    return _ok([_serialize_zone(z) for z in results])


def get_zone(zone_id):
    for z in _zones_rows():
        if z["id"] == zone_id:
            return _ok(_serialize_zone(z))
    return _err(f"Zone {zone_id} not found", code=1003, status=404)


# ---------------------------------------------------------------------------
# DNS records
# ---------------------------------------------------------------------------

def list_dns_records(zone_id, type=None, name=None):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    results = [r for r in _dns_rows() if r["zone_id"] == zone_id]
    if type:
        results = [r for r in results if r["type"] == type]
    if name:
        results = [r for r in results if r["name"] == name]
    return _ok([_serialize_dns(r) for r in results])


def get_dns_record(zone_id, record_id):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    for r in _dns_rows():
        if r["zone_id"] == zone_id and r["id"] == record_id:
            return _ok(_serialize_dns(r))
    return _err(f"DNS record {record_id} not found", code=81044, status=404)


def create_dns_record(zone_id, type, name, content, ttl=1, proxied=False, priority=0):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    record = {
        "id": _new_id(),
        "zone_id": zone_id,
        "type": type,
        "name": name,
        "content": content,
        "ttl": ttl,
        "proxied": proxied,
        "priority": priority,
        "created_on": _now(),
        "modified_on": _now(),
    }
    _dns_rows().append(record)
    return _ok(_serialize_dns(record))


def update_dns_record(zone_id, record_id, type=None, name=None, content=None,
                      ttl=None, proxied=None, priority=None):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    for idx, r in enumerate(_dns_rows()):
        if r["zone_id"] == zone_id and r["id"] == record_id:
            if type is not None:
                _dns_rows()[idx]["type"] = type
            if name is not None:
                _dns_rows()[idx]["name"] = name
            if content is not None:
                _dns_rows()[idx]["content"] = content
            if ttl is not None:
                _dns_rows()[idx]["ttl"] = ttl
            if proxied is not None:
                _dns_rows()[idx]["proxied"] = proxied
            if priority is not None:
                _dns_rows()[idx]["priority"] = priority
            _dns_rows()[idx]["modified_on"] = _now()
            return _ok(_serialize_dns(_dns_rows()[idx]))
    return _err(f"DNS record {record_id} not found", code=81044, status=404)


def delete_dns_record(zone_id, record_id):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    for idx, r in enumerate(_dns_rows()):
        if r["zone_id"] == zone_id and r["id"] == record_id:
            _dns_rows().pop(idx)
            return _ok({"id": record_id})
    return _err(f"DNS record {record_id} not found", code=81044, status=404)


# ---------------------------------------------------------------------------
# Firewall rules
# ---------------------------------------------------------------------------

def list_firewall_rules(zone_id):
    if not _zone_exists(zone_id):
        return _err(f"Zone {zone_id} not found", code=1003, status=404)
    results = [r for r in _firewall_rows() if r["zone_id"] == zone_id]
    results.sort(key=lambda r: r["priority"])
    return _ok([_serialize_firewall(r) for r in results])

_store.eager_load()
