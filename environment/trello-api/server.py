"""FastAPI server wrapping trello_data module as REST endpoints.

Mirrors a subset of the Trello REST API. Base path: /1
Like the real Trello API, write operations take their fields as query params,
as form fields, or as a JSON body. The query string keeps precedence.
"""

import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from typing import Any, Dict, List, Optional, Type, Union

import trello_data
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

app = FastAPI(title="Trello API (Mock)", version="1")
install_tracker(app)
install_admin_plane(app, store=trello_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Request bodies ---
#
# Write routes take their fields from the query string, a form body, or a JSON
# body, in that order of precedence. `await request.form()` is NOT usable here:
# Starlette routes it through python-multipart, which this mock does not ship.

_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


def _parse_body(raw: bytes, content_type: str):
    if content_type != _FORM_CONTENT_TYPE:
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            pass
    try:
        return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
    except UnicodeDecodeError:
        return None


async def _body_fields(request: Request, model: Type[BaseModel]) -> BaseModel:
    """Validate the body against ``model``, minus names the query string supplied.

    Dropping query-supplied names before validation is what keeps a query-only
    caller behaving exactly as it did before bodies were read at all.
    """
    raw = await request.body()
    if not raw.strip():
        return model()
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    parsed = _parse_body(raw, content_type)
    if not isinstance(parsed, dict):
        raise RequestValidationError([{
            "type": "dict_type", "loc": ("body",),
            "msg": "Input should be a valid dictionary", "input": None,
        }])
    supplied = {k: v for k, v in parsed.items() if k not in request.query_params}
    try:
        return model.model_validate(supplied)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())


def _declares_body(model: Type[BaseModel]) -> Dict[str, Any]:
    """OpenAPI ``requestBody`` for a route whose body ``_body_fields`` reads.

    The schema is generated FROM the model the handler validates against, so
    the document and the enforcement cannot drift apart. It goes on the route
    as ``openapi_extra`` rather than as a handler parameter because a declared
    parameter would make FastAPI parse the body itself, and FastAPI parses only
    JSON: the form-encoded shape this module's docstring promises, and
    ``_parse_body`` honours, would start answering 422. Declaring it here
    changes what the route SAYS and nothing about what it does -- a query-only
    caller sends no body and is unaffected, which is why both spellings stay
    supported for the connector guide that teaches them.
    """
    schema = model.model_json_schema()
    return {"requestBody": {"required": False, "content": {
        "application/json": {"schema": schema},
        _FORM_CONTENT_TYPE: {"schema": schema},
    }}}


def _pick(*values):
    """First value that was actually supplied; ``""`` and ``False`` count."""
    return next((v for v in values if v is not None), None)


def _require(**fields):
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        raise RequestValidationError([{
            "type": "missing", "loc": ("body", name),
            "msg": "Field required", "input": None,
        } for name in missing])


def _member_ids(id_members):
    """Accept Trello's comma-separated `idMembers` or a JSON list."""
    if id_members is None:
        return None
    if isinstance(id_members, str):
        id_members = id_members.split(",")
    return [m for m in (str(m).strip() for m in id_members) if m] or None


# --- Members ---

@app.get("/1/members/me")
def get_me():
    return trello_data.get_me()


@app.get("/1/members/me/boards")
def list_my_boards():
    return trello_data.list_my_boards()


# --- Boards ---

@app.get("/1/boards/{board_id}")
def get_board(board_id: str):
    result = trello_data.get_board(board_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@app.get("/1/boards/{board_id}/lists")
def list_board_lists(board_id: str):
    result = trello_data.list_board_lists(board_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Lists -> cards ---

@app.get("/1/lists/{list_id}/cards")
def list_cards(list_id: str):
    result = trello_data.list_cards(list_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Cards ---

@app.get("/1/cards/{card_id}")
def get_card(card_id: str):
    result = trello_data.get_card(card_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class CardCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idList: Optional[str] = None
    name: Optional[str] = None
    desc: Optional[str] = None
    due: Optional[str] = None
    idMembers: Optional[Union[str, List[str]]] = None


@app.post("/1/cards", status_code=200,
          openapi_extra=_declares_body(CardCreateBody))
async def create_card(
    request: Request,
    idList: Optional[str] = None,
    name: Optional[str] = None,
    desc: Optional[str] = None,
    due: Optional[str] = None,
    idMembers: Optional[str] = None,
):
    body = await _body_fields(request, CardCreateBody)
    idList = _pick(idList, body.idList)
    name = _pick(name, body.name)
    _require(idList=idList, name=name)
    result = trello_data.create_card(
        id_list=idList,
        name=name,
        desc=_pick(desc, body.desc, ""),
        due=_pick(due, body.due),
        member_ids=_member_ids(_pick(idMembers, body.idMembers)),
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class CardUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    desc: Optional[str] = None
    idList: Optional[str] = None
    due: Optional[str] = None
    closed: Optional[bool] = None
    pos: Optional[float] = None


@app.put("/1/cards/{card_id}",
         openapi_extra=_declares_body(CardUpdateBody))
async def update_card(
    request: Request,
    card_id: str,
    name: Optional[str] = None,
    desc: Optional[str] = None,
    idList: Optional[str] = None,
    due: Optional[str] = None,
    closed: Optional[bool] = None,
    pos: Optional[float] = None,
):
    body = await _body_fields(request, CardUpdateBody)
    result = trello_data.update_card(
        card_id,
        name=_pick(name, body.name),
        desc=_pick(desc, body.desc),
        id_list=_pick(idList, body.idList),
        due=_pick(due, body.due),
        closed=_pick(closed, body.closed),
        pos=_pick(pos, body.pos),
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@app.delete("/1/cards/{card_id}")
def delete_card(card_id: str):
    result = trello_data.delete_card(card_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Checklists ---

@app.get("/1/cards/{card_id}/checklists")
def list_card_checklists(card_id: str):
    result = trello_data.list_card_checklists(card_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class ChecklistCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idCard: Optional[str] = None
    name: Optional[str] = None


@app.post("/1/checklists", status_code=200,
          openapi_extra=_declares_body(ChecklistCreateBody))
async def create_checklist(
    request: Request,
    idCard: Optional[str] = None,
    name: Optional[str] = None,
):
    body = await _body_fields(request, ChecklistCreateBody)
    idCard = _pick(idCard, body.idCard)
    _require(idCard=idCard)
    result = trello_data.create_checklist(
        id_card=idCard, name=_pick(name, body.name, "Checklist"),
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result
