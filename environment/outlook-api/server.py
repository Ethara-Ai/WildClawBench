"""FastAPI server wrapping outlook_data as REST endpoints.

Mirrors a subset of the Microsoft Graph v1.0 API for the signed-in user's
mailbox and calendar: messages, sendMail, events, and contacts. Collections
are wrapped as {"value": [...]} like the real Graph API.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

import outlook_data
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

app = FastAPI(title="Outlook API (Mock)", version="v1.0")
install_tracker(app)
install_admin_plane(app, store=outlook_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Mail messages ---

@app.get("/v1.0/me/messages")
def list_messages(isRead: Optional[bool] = None):
    return outlook_data.list_messages(is_read=isRead)


@app.get("/v1.0/me/messages/{message_id}")
def get_message(message_id: str):
    result = outlook_data.get_message(message_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class EmailAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    name: Optional[str] = None


class Recipient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emailAddress: EmailAddress


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contentType: Optional[str] = "HTML"
    content: str


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: ItemBody
    toRecipients: List[Recipient]


class SendMailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Message


@app.post("/v1.0/me/sendMail", status_code=202)
def send_mail(body: SendMailBody):
    message = body.message
    recipients = [r.emailAddress.address for r in message.toRecipients
                  if r.emailAddress.address]
    result = outlook_data.send_mail(
        subject=message.subject, content=message.body.content,
        to_recipients=recipients, content_type=message.body.contentType or "HTML",
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result


# --- Calendar events ---

@app.get("/v1.0/me/events")
def list_events():
    return outlook_data.list_events()


# --- Contacts ---

@app.get("/v1.0/me/contacts")
def list_contacts():
    return outlook_data.list_contacts()
