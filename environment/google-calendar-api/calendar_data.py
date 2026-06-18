"""Data access module for the Google Calendar API mock service."""

import csv
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store  # noqa: E402

_store = get_store("google-calendar-api")

_store.register("calendars", primary_key="id",
                initial_loader=lambda: _coerce_calendars(_load("calendars.csv")))
_store.register("events", primary_key="id",
                initial_loader=lambda: _coerce_events(_load("events.csv")))
_store.register_document("attendees", initial_loader=lambda: _coerce_attendees(_load("event_attendees.csv")))


def _calendars_rows():
    return _store.table("calendars").rows()


def _events_rows():
    return _store.table("events").rows()


def _attendees_doc():
    return _store.document("attendees").get()



def _load(filename):
    with open(DATA_DIR / filename, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _to_bool(v):
    return str(v).strip().lower() == "true"


def _coerce_calendars(rows):
    return [{**r, "primary": _to_bool(r["primary"])} for r in rows]


def _coerce_events(rows):
    return [{**r, "all_day": _to_bool(r["all_day"]),
             "recurrence": [r["recurrence"]] if r["recurrence"] else []}
            for r in rows]


def _coerce_attendees(rows):
    out = {}
    for r in rows:
        out.setdefault(r["event_id"], []).append({
            "email": r["email"],
            "displayName": r["display_name"],
            "responseStatus": r["response_status"],
            "optional": _to_bool(r["optional"]),
            "organizer": _to_bool(r["organizer"]),
        })
    return out








def _new_event_id():
    return f"evt-{uuid.uuid4().hex[:10]}"


def _serialize_event(e):
    out = dict(e)
    out["start"] = {"dateTime": e["start"], "timeZone": "America/Los_Angeles"} if not e["all_day"] \
        else {"date": e["start"][:10]}
    out["end"] = {"dateTime": e["end"], "timeZone": "America/Los_Angeles"} if not e["all_day"] \
        else {"date": e["end"][:10]}
    out["attendees"] = _attendees_doc().get(e["id"], [])
    return out


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------

def list_calendars():
    return {"kind": "calendar#calendarList", "items": _calendars_rows()}


def get_calendar(calendar_id):
    if calendar_id == "primary":
        calendar_id = next((c["id"] for c in _calendars_rows() if c["primary"]), None)
    for c in _calendars_rows():
        if c["id"] == calendar_id:
            return c
    return {"error": f"Calendar {calendar_id} not found"}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _resolve_calendar(calendar_id):
    if calendar_id == "primary":
        return next((c["id"] for c in _calendars_rows() if c["primary"]), None)
    return calendar_id


def list_events(calendar_id, time_min=None, time_max=None, q=None,
                single_events=True, order_by="startTime", max_results=25, page_token=None):
    resolved = _resolve_calendar(calendar_id)
    if not resolved or not any(c["id"] == resolved for c in _calendars_rows()):
        return {"error": f"Calendar {calendar_id} not found"}
    results = [e for e in _events_rows() if e["calendar_id"] == resolved]
    if time_min:
        results = [e for e in results if e["end"] >= time_min]
    if time_max:
        results = [e for e in results if e["start"] <= time_max]
    if q:
        ql = q.lower()
        results = [e for e in results
                   if ql in e["summary"].lower() or ql in (e["description"] or "").lower()
                   or ql in (e["location"] or "").lower()]
    if order_by == "startTime":
        results.sort(key=lambda e: e["start"])
    elif order_by == "updated":
        results.sort(key=lambda e: e.get("updated", e["start"]), reverse=True)
    try:
        offset = int(page_token or 0)
    except ValueError:
        offset = 0
    page = results[offset: offset + max_results]
    next_token = str(offset + max_results) if offset + max_results < len(results) else None
    return {
        "kind": "calendar#events",
        "items": [_serialize_event(e) for e in page],
        "nextPageToken": next_token,
    }


def get_event(calendar_id, event_id):
    resolved = _resolve_calendar(calendar_id)
    for e in _events_rows():
        if e["calendar_id"] == resolved and e["id"] == event_id:
            return _serialize_event(e)
    return {"error": f"Event {event_id} not found"}


def create_event(calendar_id, payload):
    resolved = _resolve_calendar(calendar_id)
    if not any(c["id"] == resolved for c in _calendars_rows()):
        return {"error": f"Calendar {calendar_id} not found"}
    start = payload.get("start") or {}
    end = payload.get("end") or {}
    all_day = "date" in start
    event = {
        "id": _new_event_id(),
        "calendar_id": resolved,
        "summary": payload.get("summary", ""),
        "description": payload.get("description", ""),
        "location": payload.get("location", ""),
        "start": start.get("dateTime") or start.get("date") or _now(),
        "end": end.get("dateTime") or end.get("date") or _now(),
        "all_day": all_day,
        "status": "confirmed",
        "creator": payload.get("creator", "amelia@orbit-labs.com"),
        "organizer": payload.get("organizer", "amelia@orbit-labs.com"),
        "recurrence": payload.get("recurrence", []) or [],
        "visibility": payload.get("visibility", "default"),
    }
    _store.table("events").upsert(event)
    if payload.get("attendees"):
        _attendees_doc()[event["id"]] = [{
            "email": a.get("email"),
            "displayName": a.get("displayName", ""),
            "responseStatus": a.get("responseStatus", "needsAction"),
            "optional": bool(a.get("optional", False)),
            "organizer": bool(a.get("organizer", False)),
        } for a in payload["attendees"]]
    return _serialize_event(event)


def update_event(calendar_id, event_id, payload):
    resolved = _resolve_calendar(calendar_id)
    row = _store.table("events").get(event_id)
    if row is None or row.get("calendar_id") != resolved:
        return {"error": f"Event {event_id} not found"}
    for field in ("summary", "description", "location", "status", "visibility"):
        if field in payload:
            row[field] = payload[field]
    if "start" in payload:
        s = payload["start"]
        row["start"] = s.get("dateTime") or s.get("date") or row["start"]
        row["all_day"] = "date" in s
    if "end" in payload:
        en = payload["end"]
        row["end"] = en.get("dateTime") or en.get("date") or row["end"]
    if "attendees" in payload:
        att_doc = _store.document("attendees").get()
        att_doc[event_id] = [{
            "email": a.get("email"),
            "displayName": a.get("displayName", ""),
            "responseStatus": a.get("responseStatus", "needsAction"),
            "optional": bool(a.get("optional", False)),
            "organizer": bool(a.get("organizer", False)),
        } for a in payload["attendees"]]
        _store.document("attendees").set(att_doc)
    _store.table("events").upsert(row)
    return _serialize_event(row)


def delete_event(calendar_id, event_id):
    resolved = _resolve_calendar(calendar_id)
    row = _store.table("events").get(event_id)
    if row is None or row.get("calendar_id") != resolved:
        return {"error": f"Event {event_id} not found"}
    _store.table("events").delete(event_id)
    _attendees_doc().pop(event_id, None)
    return {"deleted": True, "id": event_id}


# ---------------------------------------------------------------------------
# Free/busy
# ---------------------------------------------------------------------------

def freebusy(time_min, time_max, calendar_ids):
    calendars_block = {}
    for raw_id in calendar_ids:
        cid = _resolve_calendar(raw_id)
        if not cid or not any(c["id"] == cid for c in _calendars_rows()):
            calendars_block[raw_id] = {"errors": [{"reason": "notFound"}], "busy": []}
            continue
        busy = []
        for e in _events_rows():
            if e["calendar_id"] != cid:
                continue
            if e["status"] != "confirmed":
                continue
            if e["end"] < time_min or e["start"] > time_max:
                continue
            busy.append({"start": e["start"], "end": e["end"]})
        calendars_block[raw_id] = {"busy": busy}
    return {"kind": "calendar#freeBusy", "timeMin": time_min, "timeMax": time_max,
            "calendars": calendars_block}
