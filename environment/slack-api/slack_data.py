"""Data access module for the Slack API mock service.

Mirrors a subset of Slack's Web API method-style endpoints (e.g. conversations.list).
"""

import csv
import json
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import (
    read_csv_with_ctx, # noqa: E402
    get_store,
    strict_int,
    strict_bool,
    opt_str,
)

_store = get_store("slack-api")
_API = "slack-api"

_store.register("users", primary_key="id",
                initial_loader=lambda: _coerce_users(_load("users.csv", "users")))
_store.register("channels", primary_key="id",
                initial_loader=lambda: _coerce_channels(_load("channels.csv", "channels")))
_store.register("messages", primary_key="ts",
                initial_loader=lambda: _coerce_messages(_load("messages.csv", "messages")))
_store.register("channel_members", primary_key="channel_id",
                initial_loader=lambda: [_strip_ctx(r) for r in _load("channel_members.csv", "channel_members")])
_store.register_document("team", initial_loader=lambda: json.load(open(DATA_DIR / "team.json", encoding="utf-8")))


def _users_rows():
    return _store.table("users").rows()


def _channels_rows():
    return _store.table("channels").rows()


def _messages_rows():
    return _store.table("messages").rows()


def _channel_members_rows():
    return _store.table("channel_members").rows()


def _team_doc():
    return _store.document("team").get()



def _load(filename, table):
    return read_csv_with_ctx(DATA_DIR / filename, _API, table)


def _strip_ctx(r):
    return {k: v for k, v in r.items() if not k.startswith("__")}


def _coerce_users(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "is_admin": strict_bool(r, "is_admin"),
            "is_bot": strict_bool(r, "is_bot"),
        })
    return out


def _coerce_channels(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "is_private": strict_bool(r, "is_private"),
            "is_archived": strict_bool(r, "is_archived"),
            "created": strict_int(r, "created"),
            "num_members": strict_int(r, "num_members"),
        })
    return out


def _coerce_messages(rows):
    out = []
    for r in rows:
        out.append({
            **_strip_ctx(r),
            "thread_ts": opt_str(r, "thread_ts", default="") or None,
            "reply_count": strict_int(r, "reply_count"),
            "reactions": _parse_reactions(opt_str(r, "reactions", default="")),
        })
    return out


def _parse_reactions(s):
    if not s:
        return []
    result = []
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            name, users = chunk.split(":", 1)
            user_list = [u.strip() for u in users.split(",") if u.strip()]
            result.append({"name": name, "users": user_list, "count": len(user_list)})
    return result







def _next_ts():
    return f"{time.time():.6f}"


# Slack-style response envelope
def _ok(payload):
    return {"ok": True, **payload}


def _err(error):
    return {"ok": False, "error": error}


# ---------------------------------------------------------------------------
# auth / team
# ---------------------------------------------------------------------------

def auth_test():
    # Authenticate as the first admin
    admin = next((u for u in _users_rows() if u["is_admin"]), _users_rows()[0])
    return _ok({
        "url": f"https://{_team_doc()['domain']}.slack.com/",
        "team": _team_doc()["name"],
        "user": admin["name"],
        "team_id": _team_doc()["id"],
        "user_id": admin["id"],
    })


def team_info():
    return _ok({"team": _team_doc()})


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def users_list():
    return _ok({"members": _users_rows()})


def users_info(user_id):
    for u in _users_rows():
        if u["id"] == user_id:
            return _ok({"user": u})
    return _err("user_not_found")


def users_set_presence(user_id, presence):
    for i, u in enumerate(_users_rows()):
        if u["id"] == user_id:
            _users_rows()[i]["presence"] = "away" if presence == "away" else "auto"
            return _ok({"presence": _users_rows()[i]["presence"]})
    return _err("user_not_found")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------

def conversations_list(types="public_channel,private_channel", exclude_archived=True):
    type_set = {t.strip() for t in types.split(",")}
    results = []
    for c in _channels_rows():
        if exclude_archived and c["is_archived"]:
            continue
        if c["is_private"] and "private_channel" not in type_set:
            continue
        if not c["is_private"] and "public_channel" not in type_set:
            continue
        results.append(c)
    return _ok({"channels": results})


def conversations_info(channel_id):
    for c in _channels_rows():
        if c["id"] == channel_id:
            return _ok({"channel": c})
    return _err("channel_not_found")


def conversations_create(name, is_private=False, user_id="U01AMELIA"):
    if any(c["name"] == name for c in _channels_rows()):
        return _err("name_taken")
    channel = {
        "id": ("G" if is_private else "C") + "01" + uuid.uuid4().hex[:8].upper(),
        "name": name,
        "is_private": bool(is_private),
        "is_archived": False,
        "topic": "",
        "purpose": "",
        "creator": user_id,
        "created": int(time.time()),
        "num_members": 1,
    }
    _channels_rows().append(channel)
    _channel_members_rows().append({"channel_id": channel["id"], "user_id": user_id})
    return _ok({"channel": channel})


def conversations_archive(channel_id):
    for i, c in enumerate(_channels_rows()):
        if c["id"] == channel_id:
            _channels_rows()[i]["is_archived"] = True
            return _ok({})
    return _err("channel_not_found")


def conversations_members(channel_id):
    members = [m["user_id"] for m in _channel_members_rows() if m["channel_id"] == channel_id]
    if not members and not any(c["id"] == channel_id for c in _channels_rows()):
        return _err("channel_not_found")
    return _ok({"members": members})


def conversations_invite(channel_id, user_id):
    if not any(c["id"] == channel_id for c in _channels_rows()):
        return _err("channel_not_found")
    if not any(u["id"] == user_id for u in _users_rows()):
        return _err("user_not_found")
    if any(m["channel_id"] == channel_id and m["user_id"] == user_id for m in _channel_members_rows()):
        return _err("already_in_channel")
    _channel_members_rows().append({"channel_id": channel_id, "user_id": user_id})
    for i, c in enumerate(_channels_rows()):
        if c["id"] == channel_id:
            _channels_rows()[i]["num_members"] += 1
    return _ok({"channel": next(c for c in _channels_rows() if c["id"] == channel_id)})


def conversations_history(channel_id, limit=20, oldest=None, latest=None):
    if not any(c["id"] == channel_id for c in _channels_rows()):
        return _err("channel_not_found")
    msgs = [m for m in _messages_rows() if m["channel_id"] == channel_id and m["thread_ts"] is None]
    if oldest:
        msgs = [m for m in msgs if float(m["ts"]) >= float(oldest)]
    if latest:
        msgs = [m for m in msgs if float(m["ts"]) <= float(latest)]
    msgs.sort(key=lambda m: float(m["ts"]), reverse=True)
    return _ok({"messages": msgs[:limit], "has_more": len(msgs) > limit})


def conversations_replies(channel_id, ts):
    parent = next((m for m in _messages_rows()
                   if m["channel_id"] == channel_id and m["ts"] == ts), None)
    if not parent:
        return _err("thread_not_found")
    replies = [m for m in _messages_rows()
               if m["channel_id"] == channel_id and m["thread_ts"] == ts]
    replies.sort(key=lambda m: float(m["ts"]))
    return _ok({"messages": [parent] + replies})


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

def chat_post_message(channel_id, user_id, text, thread_ts=None):
    if not any(c["id"] == channel_id for c in _channels_rows()):
        return _err("channel_not_found")
    ts = _next_ts()
    msg = {
        "ts": ts,
        "channel_id": channel_id,
        "user_id": user_id,
        "text": text,
        "thread_ts": thread_ts,
        "reply_count": 0,
        "reactions": [],
    }
    _messages_rows().append(msg)
    if thread_ts:
        for i, m in enumerate(_messages_rows()):
            if m["channel_id"] == channel_id and m["ts"] == thread_ts:
                _messages_rows()[i]["reply_count"] += 1
    return _ok({"channel": channel_id, "ts": ts, "message": msg})


def chat_update(channel_id, ts, text):
    for i, m in enumerate(_messages_rows()):
        if m["channel_id"] == channel_id and m["ts"] == ts:
            _messages_rows()[i]["text"] = text
            return _ok({"channel": channel_id, "ts": ts, "text": text})
    return _err("message_not_found")


def chat_delete(channel_id, ts):
    for i, m in enumerate(_messages_rows()):
        if m["channel_id"] == channel_id and m["ts"] == ts:
            _messages_rows().pop(i)
            return _ok({"channel": channel_id, "ts": ts})
    return _err("message_not_found")


# ---------------------------------------------------------------------------
# reactions
# ---------------------------------------------------------------------------

def reactions_add(channel_id, ts, name, user_id):
    for i, m in enumerate(_messages_rows()):
        if m["channel_id"] == channel_id and m["ts"] == ts:
            for r in m["reactions"]:
                if r["name"] == name:
                    if user_id not in r["users"]:
                        r["users"].append(user_id)
                        r["count"] = len(r["users"])
                    return _ok({})
            m["reactions"].append({"name": name, "users": [user_id], "count": 1})
            return _ok({})
    return _err("message_not_found")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def search_messages(query):
    q = query.lower()
    matches = [m for m in _messages_rows() if q in m["text"].lower()]
    return _ok({"messages": {"total": len(matches), "matches": matches}})


_store.eager_load()
