"""FastAPI server wrapping microsoft_teams_data as REST endpoints.

Mirrors a subset of the Microsoft Graph v1.0 API for Teams: joined teams,
teams, channels, and channel messages. Collections are wrapped as
{"value": [...]} like the real Graph API.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import Optional

import microsoft_teams_data
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

app = FastAPI(title="Microsoft Teams API (Mock)", version="v1.0")
install_tracker(app)
install_admin_plane(app, store=microsoft_teams_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Joined teams (me) ---

@app.get("/v1.0/me/joinedTeams")
def joined_teams():
    return microsoft_teams_data.list_joined_teams()


# --- Teams ---

@app.get("/v1.0/teams/{team_id}")
def get_team(team_id: str):
    result = microsoft_teams_data.get_team(team_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Channels ---

@app.get("/v1.0/teams/{team_id}/channels")
def list_channels(team_id: str):
    result = microsoft_teams_data.list_channels(team_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Channel messages ---

@app.get("/v1.0/teams/{team_id}/channels/{channel_id}/messages")
def list_messages(team_id: str, channel_id: str):
    result = microsoft_teams_data.list_messages(team_id, channel_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contentType: Optional[str] = "html"
    content: str


class ChatMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: ItemBody
    importance: Optional[str] = "normal"


@app.post("/v1.0/teams/{team_id}/channels/{channel_id}/messages", status_code=201)
def send_message(team_id: str, channel_id: str, body: ChatMessageBody):
    result = microsoft_teams_data.send_message(
        team_id, channel_id,
        content=body.body.content,
        content_type=body.body.contentType or "html",
        importance=body.importance or "normal",
    )
    if isinstance(result, dict) and "error" in result:
        status = 400 if result.get("error") == "invalid request" else 404
        return JSONResponse(status_code=status, content=result)
    return result
