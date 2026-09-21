"""FastAPI server wrapping freshdesk_data module as REST endpoints.

Mirrors a subset of the Freshdesk v2 API: tickets (list/get/create/update),
contacts, and agents. Routes live under /api/v2/...
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

import freshdesk_data
try:
    from tracking_middleware import install_tracker
    from admin_plane import install_admin_plane
except ModuleNotFoundError as _shared_plane_err:  # standalone run without the shared module on sys.path
    import logging as _logging
    _logging.error("SHARED PLANE MISSING - audit + admin disabled: %s", _shared_plane_err)
    def install_tracker(app):  # no-op fallback: audit endpoints disabled
        return None

    def install_admin_plane(app, store=None, one_shot_registry=None):
        return None

app = FastAPI(title="Freshdesk API (Mock)", version="v2")
install_tracker(app)
install_admin_plane(app, store=freshdesk_data._store)


def _nothing_to_update(body):
    """A 400 naming the writable fields, or None when the body names one.

    Without this an update whose every field parsed as absent answers 200 over
    an untouched resource, which a caller cannot tell from a successful write.
    """
    if body.model_dump(exclude_none=True):
        return None
    return JSONResponse(status_code=400, content={
        "error": "no updatable field supplied; expected one of "
                 + ", ".join(sorted(type(body).model_fields))})


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Tickets ---

@app.get("/api/v2/tickets")
def list_tickets(
    status: Optional[int] = Query(None),
    priority: Optional[int] = Query(None),
    requester_id: Optional[int] = Query(None),
):
    return freshdesk_data.list_tickets(
        status=status, priority=priority, requester_id=requester_id
    )


@app.get("/api/v2/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    result = freshdesk_data.get_ticket(ticket_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    description: Optional[str] = None
    status: Optional[int] = None
    priority: Optional[int] = None
    requester_id: Optional[int] = None
    responder_id: Optional[int] = None
    type: Optional[str] = None
    tags: Optional[List[str]] = None


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Optional[str] = None
    description: Optional[str] = None
    status: Optional[int] = None
    priority: Optional[int] = None
    requester_id: Optional[int] = None
    responder_id: Optional[int] = None
    type: Optional[str] = None
    tags: Optional[List[str]] = None


@app.post("/api/v2/tickets", status_code=201)
def create_ticket(body: TicketCreate):
    return freshdesk_data.create_ticket(body.model_dump(exclude_unset=True))


@app.put("/api/v2/tickets/{ticket_id}")
def update_ticket(ticket_id: int, body: TicketUpdate):
    refusal = _nothing_to_update(body)
    if refusal is not None:
        return refusal
    result = freshdesk_data.update_ticket(
        ticket_id, body.model_dump(exclude_unset=True))
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Contacts + agents ---

@app.get("/api/v2/contacts")
def list_contacts():
    return freshdesk_data.list_contacts()


@app.get("/api/v2/agents")
def list_agents():
    return freshdesk_data.list_agents()
