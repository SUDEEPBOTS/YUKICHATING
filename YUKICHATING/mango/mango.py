"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  C H A T  E N G I N E
─  Routes  : /chat/*
─  Socket  : /chat/ws/{room_id}/{username}
─  Features:
─    • WebSocket real-time messaging
─    • Public + Private rooms
─    • Typing indicator
─    • Online/Offline status
─    • Read receipts
─    • File/Image share (base64)
─    • Message history (MongoDB)
─    • Room create / join / leave / delete
─    • Invite-only private rooms
────────────────────────────────────────────────────────────────────────
"""

import base64
import logging
import mimetypes
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from YUKICHATING.mango.mango import (
    chatsdb,
    msgsdb,
    usersdb,
)

log = logging.getLogger("YUKICHATING.chat")

router = APIRouter(prefix="/chat", tags=["Chat"])

MAX_FILE_MB   = 10
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
HISTORY_LIMIT  = 50

# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        # { room_id: { username: WebSocket } }
        self.rooms:   dict[str, dict[str, WebSocket]] = defaultdict(dict)
        # { username: room_id }  — track where each user is
        self.user_room: dict[str, str] = {}

    async def connect(self, ws: WebSocket, room_id: str, username: str):
        await ws.accept()
        self.rooms[room_id][username]  = ws
        self.user_room[username]       = room_id
        log.info(f"[WS] ✅ {username} joined room {room_id}")

    def disconnect(self, room_id: str, username: str):
        self.rooms[room_id].pop(username, None)
        self.user_room.pop(username, None)
        if not self.rooms[room_id]:
            del self.rooms[room_id]
        log.info(f"[WS] ❌ {username} left room {room_id}")

    async def broadcast(self, room_id: str, payload: dict, exclude: str = None):
        """Send to everyone in a room (optionally exclude sender)."""
        dead = []
        for uname, ws in self.rooms.get(room_id, {}).items():
            if uname == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(uname)
        for uname in dead:
            self.disconnect(room_id, uname)

    async def send_to(self, username: str, payload: dict):
        """Send directly to one user."""
        room_id = self.user_room.get(username)
        if not room_id:
            return
        ws = self.rooms.get(room_id, {}).get(username)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(room_id, username)

    def online_users(self, room_id: str) -> list[str]:
        return list(self.rooms.get(room_id, {}).keys())

    def is_online(self, username: str) -> bool:
        return username in self.user_room


manager = ConnectionManager()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def new_id() -> str:
    return str(uuid.uuid4())

async def _get_room(room_id: str) -> dict:
    room = await chatsdb.find_one({"room_id": room_id})
    if not room:
        raise HTTPException(status_code=404, detail="❌ Room not found!")
    return room

async def _save_message(msg: dict):
    await msgsdb.insert_one(msg)

async def _mark_read(room_id: str, reader: str):
    """Mark all messages in room as read by this user."""
    await msgsdb.update_many(
        {"room_id": room_id, "read_by": {"$ne": reader}},
        {"$push": {"read_by": reader}},
    )

def _serialize(doc: dict) -> dict:
    """Remove MongoDB _id for JSON response."""
    doc.pop("_id", None)
    return doc

# ══════════════════════════════════════════════════════════════════════════════
# PAYLOAD MODELS
# ══════════════════════════════════════════════════════════════════════════════

class CreateRoomPayload(BaseModel):
    name:        str
    created_by:  str
    is_private:  bool = False
    invite_only: bool = False

class JoinRoomPayload(BaseModel):
    username:   str
    invite_key: Optional[str] = None   # required for invite-only rooms

class SendMessagePayload(BaseModel):
    room_id:  str
    sender:   str
    content:  str
    msg_type: str = "text"   # text | image | file

# ══════════════════════════════════════════════════════════════════════════════
# ROOM ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/room/create")
async def create_room(payload: CreateRoomPayload):
    """Public ya private room banao."""
    room_id    = new_id()
    invite_key = new_id()[:8] if payload.invite_only else None

    room = {
        "room_id":    room_id,
        "name":       payload.name.strip(),
        "created_by": payload.created_by,
        "is_private": payload.is_private,
        "invite_only": payload.invite_only,
        "invite_key": invite_key,
        "members":    [payload.created_by],
        "created_at": now_iso(),
    }
    await chatsdb.insert_one(room)
    log.info(f"[room] Created: {room_id} by {payload.created_by}")

    return {
        "status":     "success",
        "room_id":    room_id,
        "invite_key": invite_key,   # None if not invite_only
        "message":    f"✅ Room '{payload.name}' created!",
    }


@router.post("/room/{room_id}/join")
async def join_room(room_id: str, payload: JoinRoomPayload):
    """Room mein join karo."""
    room = await _get_room(room_id)

    # Invite-only check
    if room.get("invite_only"):
        if payload.invite_key != room.get("invite_key"):
            raise HTTPException(status_code=403, detail="❌ Invalid invite key!")

    if payload.username in room.get("members", []):
        return {"status": "success", "message": "Already a member!"}

    await chatsdb.update_one(
        {"room_id": room_id},
        {"$push": {"members": payload.username}},
    )
    log.info(f"[room] {payload.username} joined {room_id}")

    # Notify online members
    await manager.broadcast(room_id, {
        "event":    "user_joined",
        "username": payload.username,
        "room_id":  room_id,
        "time":     now_iso(),
    })

    return {"status": "success", "message": f"✅ Joined room!"}


@router.post("/room/{room_id}/leave")
async def leave_room(room_id: str, username: str):
    room = await _get_room(room_id)
    await chatsdb.update_one(
        {"room_id": room_id},
        {"$pull": {"members": username}},
    )
    await manager.broadcast(room_id, {
        "event":    "user_left",
        "username": username,
        "room_id":  room_id,
        "time":     now_iso(),
    })
    log.info(f"[room] {username} left {room_id}")
    return {"status": "success", "message": "✅ Left room!"}


@router.delete("/room/{room_id}")
async def delete_room(room_id: str, username: str):
    room = await _get_room(room_id)
    if room["created_by"] != username:
        raise HTTPException(status_code=403, detail="❌ Only room creator delete kar sakta hai!")

    await chatsdb.delete_one({"room_id": room_id})
    await msgsdb.delete_many({"room_id": room_id})

    await manager.broadcast(room_id, {
        "event":   "room_deleted",
        "room_id": room_id,
        "time":    now_iso(),
    })
    log.info(f"[room] Deleted: {room_id} by {username}")
    return {"status": "success", "message": "✅ Room deleted!"}


@router.get("/rooms/public")
async def get_public_rooms():
    """Sab public rooms list."""
    rooms = await chatsdb.find(
        {"is_private": False}
    ).sort("created_at", -1).to_list(length=50)
    return {"rooms": [_serialize(r) for r in rooms]}


@router.get("/room/{room_id}")
async def get_room_info(room_id: str):
    room = await _get_room(room_id)
    room = _serialize(room)
    room["online_users"] = manager.online_users(room_id)
    return room

# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/room/{room_id}/history")
async def get_history(
    room_id: str,
    limit:   int = Query(default=50, le=100),
    skip:    int = Query(default=0),
):
    msgs = await msgsdb.find(
        {"room_id": room_id}
    ).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=None)
    msgs = [_serialize(m) for m in reversed(msgs)]
    return {"messages": msgs, "count": len(msgs)}


@router.post("/room/{room_id}/read")
async def mark_read(room_id: str, username: str):
    """Mark all messages as read."""
    await _mark_read(room_id, username)
    return {"status": "success"}

# ══════════════════════════════════════════════════════════════════════════════
# FILE UPLOAD ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/room/{room_id}/upload")
async def upload_file(room_id: str, sender: str, file: UploadFile):
    """
    File / image upload.
    Max 10MB. Returns base64 + mime_type for WebSocket broadcast.
    """
    await _get_room(room_id)

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"❌ File too large! Max {MAX_FILE_MB}MB allowed.",
        )

    mime      = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    b64       = base64.b64encode(data).decode()
    msg_type  = "image" if mime.startswith("image/") else "file"
    msg_id    = new_id()
    timestamp = now_iso()

    msg = {
        "msg_id":    msg_id,
        "room_id":   room_id,
        "sender":    sender,
        "content":   b64,
        "filename":  file.filename,
        "mime_type": mime,
        "msg_type":  msg_type,
        "timestamp": timestamp,
        "read_by":   [sender],
    }
    await _save_message(msg)

    # Broadcast to room
    await manager.broadcast(room_id, {
        "event":     "message",
        "msg_id":    msg_id,
        "sender":    sender,
        "content":   b64,
        "filename":  file.filename,
        "mime_type": mime,
        "msg_type":  msg_type,
        "timestamp": timestamp,
    }, exclude=sender)

    log.info(f"[upload] {sender} uploaded {file.filename} in {room_id}")
    return {"status": "success", "msg_id": msg_id, "msg_type": msg_type}

# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET — REAL-TIME ENGINE
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(ws: WebSocket, room_id: str, username: str):
    """
    WebSocket connection.

    Client sends JSON events:
      { "event": "message",  "content": "hello" }
      { "event": "typing",   "is_typing": true }
      { "event": "read" }
      { "event": "ping" }

    Server broadcasts JSON events:
      { "event": "message",    "sender": "x", "content": "...", ... }
      { "event": "typing",     "sender": "x", "is_typing": true }
      { "event": "user_joined","username": "x" }
      { "event": "user_left",  "username": "x" }
      { "event": "online_list","users": [...] }
      { "event": "read_receipt","reader": "x" }
      { "event": "pong" }
    """
    # Room must exist
    try:
        room = await _get_room(room_id)
    except HTTPException:
        await ws.close(code=4004)
        return

    # Private room — must be a member
    if room.get("is_private") and username not in room.get("members", []):
        await ws.close(code=4003)
        return

    await manager.connect(ws, room_id, username)

    # Announce join
    await manager.broadcast(room_id, {
        "event":       "user_joined",
        "username":    username,
        "online_list": manager.online_users(room_id),
        "time":        now_iso(),
    }, exclude=username)

    # Send current online list to the new user
    await ws.send_json({
        "event": "online_list",
        "users": manager.online_users(room_id),
    })

    try:
        while True:
            data  = await ws.receive_json()
            event = data.get("event", "message")

            # ── PING ──────────────────────────────────────────────────────
            if event == "ping":
                await ws.send_json({"event": "pong"})

            # ── TYPING INDICATOR ──────────────────────────────────────────
            elif event == "typing":
                await manager.broadcast(room_id, {
                    "event":     "typing",
                    "sender":    username,
                    "is_typing": data.get("is_typing", False),
                }, exclude=username)

            # ── READ RECEIPT ──────────────────────────────────────────────
            elif event == "read":
                await _mark_read(room_id, username)
                await manager.broadcast(room_id, {
                    "event":  "read_receipt",
                    "reader": username,
                    "time":   now_iso(),
                }, exclude=username)

            # ── TEXT MESSAGE ──────────────────────────────────────────────
            elif event == "message":
                content = str(data.get("content", "")).strip()
                if not content:
                    continue

                msg_id    = new_id()
                timestamp = now_iso()

                msg = {
                    "msg_id":    msg_id,
                    "room_id":   room_id,
                    "sender":    username,
                    "content":   content,
                    "msg_type":  "text",
                    "timestamp": timestamp,
                    "read_by":   [username],
                }
                await _save_message(msg)

                payload = {
                    "event":     "message",
                    "msg_id":    msg_id,
                    "sender":    username,
                    "content":   content,
                    "msg_type":  "text",
                    "timestamp": timestamp,
                }

                # Echo back to sender (with msg_id)
                await ws.send_json({**payload, "self": True})

                # Broadcast to others
                await manager.broadcast(room_id, payload, exclude=username)

    except WebSocketDisconnect:
        manager.disconnect(room_id, username)

        # ── Last seen update (Profile.py ke liye) ─────────────────────────
        try:
            from YUKICHATING.Plugins.profile.Profile import update_last_seen
            await update_last_seen(username)
        except Exception as e:
            log.warning(f"[WS] last_seen update failed for {username}: {e}")

        await manager.broadcast(room_id, {
            "event":       "user_left",
            "username":    username,
            "online_list": manager.online_users(room_id),
            "time":        now_iso(),
        })
        log.info(f"[WS] 💀 {username} disconnected from {room_id}")

    except Exception as e:
        log.error(f"[WS] Error — {username} in {room_id}: {e}")
        manager.disconnect(room_id, username)
    
