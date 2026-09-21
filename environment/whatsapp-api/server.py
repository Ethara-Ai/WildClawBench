"""FastAPI server wrapping whatsapp_data module as REST endpoints.

Loosely mirrors the WhatsApp Cloud API (Graph v17.0) surface.
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any

import whatsapp_data
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

app = FastAPI(title="WhatsApp Cloud API (Mock)", version="v17.0")
install_tracker(app)
install_admin_plane(app, store=whatsapp_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Business / phone numbers ---

@app.get("/v17.0/business")
def get_business():
    return whatsapp_data.get_business()


# --- Contacts ---

@app.get("/v17.0/contacts")
def list_contacts(opted_in_only: bool = False):
    return whatsapp_data.list_contacts(opted_in_only=opted_in_only)


@app.get("/v17.0/contacts/{wa_id}")
def get_contact(wa_id: str):
    result = whatsapp_data.get_contact(wa_id)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Templates ---

@app.get("/v17.0/message_templates")
def list_templates(status: Optional[str] = None):
    return whatsapp_data.list_templates(status=status)


@app.get("/v17.0/message_templates/{name}")
def get_template(name: str):
    result = whatsapp_data.get_template(name)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Conversations / messages ---

@app.get("/v17.0/conversations")
def list_conversations(wa_id: Optional[str] = None):
    return whatsapp_data.list_conversations(wa_id=wa_id)


@app.get("/v17.0/messages")
def list_messages(
    conversation_id: Optional[str] = None,
    wa_id: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
):
    return whatsapp_data.list_messages(conversation_id=conversation_id, wa_id=wa_id, limit=limit)


class TextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str


class TemplateLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = "en_US"


class TemplateBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    language: Optional[TemplateLanguage] = None
    components: Optional[List[Dict[str, Any]]] = None


MESSAGING_PRODUCT = "whatsapp"


def _wrong_messaging_product(value):
    """Graph's own refusal, or None when the caller named the right product.

    `messaging_product` is required on every Cloud API message send and the
    only accepted value is "whatsapp"; Graph answers 400 with code 100 when it
    is missing or anything else. The mock used to declare the field and never
    read it, so `"messaging_product": "sms"` was accepted and the message went
    out over WhatsApp anyway -- a declared field the caller could set and the
    store never saw, which is this wave's defect class on the create side.
    """
    if value == MESSAGING_PRODUCT:
        return None
    return JSONResponse(status_code=400, content={"error": {
        "message": f"(#100) Param messaging_product must be {MESSAGING_PRODUCT}",
        "type": "OAuthException", "code": 100}})


class SendMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messaging_product: str = MESSAGING_PRODUCT
    to: str
    type: str  # "text" or "template"
    text: Optional[TextMessage] = None
    template: Optional[TemplateBlock] = None


@app.post("/v17.0/messages")
def send_message(body: SendMessageBody):
    refusal = _wrong_messaging_product(body.messaging_product)
    if refusal is not None:
        return refusal
    if body.type == "text":
        if not body.text:
            return JSONResponse(status_code=400, content={"error": "text body required"})
        result = whatsapp_data.send_text(body.to, body.text.body)
    elif body.type == "template":
        if not body.template:
            return JSONResponse(status_code=400, content={"error": "template body required"})
        result = whatsapp_data.send_template(
            body.to,
            body.template.name,
            components=body.template.components,
        )
    else:
        return JSONResponse(status_code=400, content={"error": f"Unsupported type: {body.type}"})
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result


#: Graph documents this write with "read" and no other value. Threaded through
#: to the store rather than hardcoded there, so the declared field is what lands.
READ_STATUS = "read"


class ReadStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messaging_product: str = MESSAGING_PRODUCT
    status: str = READ_STATUS
    message_id: str


@app.post("/v17.0/messages/status")
def mark_read(body: ReadStatusBody):
    refusal = _wrong_messaging_product(body.messaging_product)
    if refusal is not None:
        return refusal
    if body.status != READ_STATUS:
        return JSONResponse(status_code=400, content={"error": {
            "message": f"(#100) Param status must be {READ_STATUS}",
            "type": "OAuthException", "code": 100}})
    result = whatsapp_data.mark_read(body.message_id, status=body.status)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result
