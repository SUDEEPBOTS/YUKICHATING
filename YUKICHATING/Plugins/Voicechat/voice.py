"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  V O I C E  C A L L
─  Routes  : /voice/*
─  Socket  : /voice/ws/{room_id}/{username}
─  Features:
─    • WebRTC Signaling (offer / answer / ICE)
─    • DTLS-SRTP E2E Encryption (WebRTC built-in)
─    • Public + Private voice rooms
─    • Invite-only rooms with invite key
─    • Mute / Unmute broadcast
─    • Online participants list
─    • Call history (MongoDB)
─    • Auto-delete empty rooms
─    • Invite link generate + regenerate
────────────────────────────────────────────────────────────────────────
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from YUKICHATING.mango.mango import (
    chatsdb,
    usersdb,
    voicedb,      # voice room records
    calllogsdb,   # call history logs
)

log = logging.getLogger("YUKICHATING.voice")

router = APIRouter(prefix="/voice", tags=["Voice Call"])

# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class VoiceManager:
    def __init__(self):
        # { room_id: { username: { "ws": WebSocket, "muted": bool } } }
        self.rooms: dict[str, dict[str, dict]] = defaultdict(dict)

    async def connect(self, ws: WebSocket, room_id: str, username: str):
        await ws.accept()
        self.rooms[room_id][username] = {"ws": ws, "muted": False}
        log.info(f"[VOICE] ✅ {username} joined voice room {room_id}")

    def disconnect(self, room_id: str, username: str):
        self.rooms[room_id].pop(username, None)
        if not self.rooms[room_id]:
            del self.rooms[room_id]
        log.info(f"[VOICE] ❌ {username} left voice room {room_id}")

    async def broadcast(self, room_id: str, payload: dict, exclude: str = None):
        dead = []
        for uname, info in self.rooms.get(room_id, {}).items():
            if uname == exclude:
                continue
            try:
                await info["ws"].send_json(payload)
            except Exception:
                dead.append(uname)
        for uname in dead:
            self.disconnect(room_id, uname)

    async def send_to(self, room_id: str, username: str, payload: dict):
        """Direct ek user ko bhejo."""
        info = self.rooms.get(room_id, {}).get(username)
        if info:
            try:
                await info["ws"].send_json(payload)
            except Exception:
                self.disconnect(room_id, username)

    def participants(self, room_id: str) -> list[dict]:
        return [
            {"username": uname, "muted": info["muted"]}
            for uname, info in self.rooms.get(room_id, {}).items()
        ]

    def online_count(self, room_id: str) -> int:
        return len(self.rooms.get(room_id, {}))

    def is_in_room(self, room_id: str, username: str) -> bool:
        return username in self.rooms.get(room_id, {})


voice_mgr = VoiceManager()

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def new_id() -> str:
    return str(uuid.uuid4())

def short_id() -> str:
    return str(uuid.uuid4())[:8]

async def _get_voice_room(room_id: str) -> dict:
    room = await voicedb.find_one({"room_id": room_id})
    if not room:
        raise HTTPException(status_code=404, detail="❌ Voice room not found!")
    return room

def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc

async def _log_call(room_id: str, event: str, username: str):
    """Call history MongoDB mein save karo."""
    await calllogsdb.insert_one({
        "log_id":    new_id(),
        "room_id":   room_id,
        "username":  username,
        "event":     event,       # joined | left | created | deleted
        "timestamp": now_iso(),
    })

# ══════════════════════════════════════════════════════════════════════════════
# PAYLOAD MODELS
# ══════════════════════════════════════════════════════════════════════════════

class CreateVoiceRoom(BaseModel):
    name:        str
    created_by:  str
    is_private:  bool = False
    invite_only: bool = False
    max_users:   int  = 10

class JoinVoiceRoom(BaseModel):
    username:   str
    invite_key: Optional[str] = None

# ══════════════════════════════════════════════════════════════════════════════
# ROOM ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/room/create")
async def create_voice_room(payload: CreateVoiceRoom):
    """Voice room banao — public ya private."""
    room_id    = short_id()
    invite_key = short_id() if payload.invite_only else None

    room = {
        "room_id":     room_id,
        "name":        payload.name.strip(),
        "created_by":  payload.created_by,
        "is_private":  payload.is_private,
        "invite_only": payload.invite_only,
        "invite_key":  invite_key,
        "max_users":   payload.max_users,
        "members":     [payload.created_by],
        "created_at":  now_iso(),
        "encryption":  "DTLS-SRTP",
    }
    await voicedb.insert_one(room)
    await _log_call(room_id, "created", payload.created_by)
    log.info(f"[VOICE] Room created: {room_id} by {payload.created_by}")

    return {
        "status":     "success",
        "room_id":    room_id,
        "invite_key": invite_key,
        "ws_url":     f"/voice/ws/{room_id}/{payload.created_by}",
        "encryption": "DTLS-SRTP (WebRTC built-in)",
        "message":    f"✅ Voice room '{payload.name}' ready!",
    }


@router.post("/room/{room_id}/join")
async def join_voice_room(room_id: str, payload: JoinVoiceRoom):
    """Voice room mein join karo."""
    room = await _get_voice_room(room_id)

    if room.get("invite_only"):
        if payload.invite_key != room.get("invite_key"):
            raise HTTPException(status_code=403, detail="❌ Invalid invite key!")

    if voice_mgr.online_count(room_id) >= room.get("max_users", 10):
        raise HTTPException(status_code=429, detail="❌ Room full!")

    if payload.username not in room.get("members", []):
        await voicedb.update_one(
            {"room_id": room_id},
            {"$push": {"members": payload.username}},
        )

    log.info(f"[VOICE] {payload.username} joined {room_id}")
    return {
        "status":  "success",
        "ws_url":  f"/voice/ws/{room_id}/{payload.username}",
        "message": "✅ Joined voice room!",
    }


@router.post("/room/{room_id}/leave")
async def leave_voice_room(room_id: str, username: str):
    await _get_voice_room(room_id)
    await voicedb.update_one(
        {"room_id": room_id},
        {"$pull": {"members": username}},
    )
    return {"status": "success", "message": "✅ Left voice room!"}


@router.delete("/room/{room_id}")
async def delete_voice_room(room_id: str, username: str):
    room = await _get_voice_room(room_id)
    if room["created_by"] != username:
        raise HTTPException(status_code=403, detail="❌ Sirf creator delete kar sakta hai!")

    await voicedb.delete_one({"room_id": room_id})
    await calllogsdb.delete_many({"room_id": room_id})

    await voice_mgr.broadcast(room_id, {
        "event":   "room_deleted",
        "room_id": room_id,
        "time":    now_iso(),
    })
    log.info(f"[VOICE] Room deleted: {room_id}")
    return {"status": "success", "message": "✅ Voice room deleted!"}


@router.get("/rooms/public")
async def get_public_voice_rooms():
    """Sab public voice rooms."""
    rooms = await voicedb.find(
        {"is_private": False}
    ).sort("created_at", -1).to_list(length=50)
    result = []
    for r in rooms:
        r = _serialize(r)
        r["online"]       = voice_mgr.online_count(r["room_id"])
        r["participants"] = voice_mgr.participants(r["room_id"])
        result.append(r)
    return {"rooms": result}


@router.get("/room/{room_id}")
async def get_voice_room_info(room_id: str):
    room = await _get_voice_room(room_id)
    room = _serialize(room)
    room["online"]       = voice_mgr.online_count(room_id)
    room["participants"] = voice_mgr.participants(room_id)
    return room


@router.get("/room/{room_id}/history")
async def get_call_history(
    room_id: str,
    limit: int = Query(default=50, le=100),
    skip:  int = Query(default=0),
):
    """Room ka call join/leave history."""
    logs = await calllogsdb.find(
        {"room_id": room_id}
    ).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=None)
    return {"logs": [_serialize(l) for l in logs], "count": len(logs)}


@router.post("/room/{room_id}/invite/regenerate")
async def regenerate_invite(room_id: str, username: str):
    """Naya invite key generate karo (sirf creator)."""
    room = await _get_voice_room(room_id)
    if room["created_by"] != username:
        raise HTTPException(status_code=403, detail="❌ Sirf creator kar sakta hai!")
    if not room.get("invite_only"):
        raise HTTPException(status_code=400, detail="❌ Room invite-only nahi hai!")

    new_key = short_id()
    await voicedb.update_one(
        {"room_id": room_id},
        {"$set": {"invite_key": new_key}},
    )
    return {
        "status":     "success",
        "invite_key": new_key,
        "message":    "✅ Naya invite key ready!",
    }

# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET — WebRTC SIGNALING ENGINE
# ══════════════════════════════════════════════════════════════════════════════
#
#  WebRTC Flow (DTLS-SRTP — server sirf relay karta hai, audio nahi sunna):
#
#  1. A joins     → server broadcasts "user_joined" to existing peers
#  2. Each peer B sends:   { "event": "offer",  "sdp": "...", "to": "A" }
#  3. A replies:           { "event": "answer", "sdp": "...", "to": "B" }
#  4. Both exchange ICE:   { "event": "ice", "candidate": {...}, "to": "X" }
#  5. DTLS-SRTP handshake peer-to-peer → audio flows E2E
#
#  Client send events:
#    offer   → { event, sdp, to }
#    answer  → { event, sdp, to }
#    ice     → { event, candidate, to }
#    mute    → { event, muted: bool }
#    ping    → { event }
#
#  Server broadcast events:
#    room_state  → { event, participants, online, encryption }
#    user_joined → { event, username, participants, online }
#    user_left   → { event, username, participants, online }
#    offer       → { event, sdp, from }        (relayed to target)
#    answer      → { event, sdp, from }        (relayed to target)
#    ice         → { event, candidate, from }  (relayed to target)
#    user_muted  → { event, username, muted }
#    room_deleted→ { event, room_id }
#    pong        → { event }

@router.websocket("/ws/{room_id}/{username}")
async def voice_websocket(ws: WebSocket, room_id: str, username: str):

    # ── Room check ─────────────────────────────────────────────────────────────
    try:
        room = await _get_voice_room(room_id)
    except HTTPException:
        await ws.close(code=4004)
        return

    # ── Private room — must be member ──────────────────────────────────────────
    if room.get("is_private") and username not in room.get("members", []):
        await ws.close(code=4003)
        return

    # ── Full room check ────────────────────────────────────────────────────────
    if voice_mgr.online_count(room_id) >= room.get("max_users", 10):
        await ws.close(code=4029)
        return

    # ── Connect ────────────────────────────────────────────────────────────────
    await voice_mgr.connect(ws, room_id, username)
    await _log_call(room_id, "joined", username)

    # Tell others someone joined
    await voice_mgr.broadcast(room_id, {
        "event":        "user_joined",
        "username":     username,
        "participants": voice_mgr.participants(room_id),
        "online":       voice_mgr.online_count(room_id),
        "time":         now_iso(),
    }, exclude=username)

    # Send room state to new joiner
    await ws.send_json({
        "event":        "room_state",
        "room_id":      room_id,
        "participants": voice_mgr.participants(room_id),
        "online":       voice_mgr.online_count(room_id),
        "encryption":   "DTLS-SRTP",
        "time":         now_iso(),
    })

    try:
        while True:
            data  = await ws.receive_json()
            event = data.get("event", "")

            # ── PING ──────────────────────────────────────────────────────────
            if event == "ping":
                await ws.send_json({"event": "pong"})

            # ── WebRTC OFFER ──────────────────────────────────────────────────
            elif event == "offer":
                target = data.get("to")
                if target and voice_mgr.is_in_room(room_id, target):
                    await voice_mgr.send_to(room_id, target, {
                        "event": "offer",
                        "sdp":   data.get("sdp"),
                        "from":  username,
                    })

            # ── WebRTC ANSWER ─────────────────────────────────────────────────
            elif event == "answer":
                target = data.get("to")
                if target and voice_mgr.is_in_room(room_id, target):
                    await voice_mgr.send_to(room_id, target, {
                        "event": "answer",
                        "sdp":   data.get("sdp"),
                        "from":  username,
                    })

            # ── ICE CANDIDATE ─────────────────────────────────────────────────
            elif event == "ice":
                target = data.get("to")
                if target and voice_mgr.is_in_room(room_id, target):
                    await voice_mgr.send_to(room_id, target, {
                        "event":     "ice",
                        "candidate": data.get("candidate"),
                        "from":      username,
                    })

            # ── MUTE / UNMUTE ─────────────────────────────────────────────────
            elif event == "mute":
                muted = data.get("muted", True)
                if username in voice_mgr.rooms.get(room_id, {}):
                    voice_mgr.rooms[room_id][username]["muted"] = muted
                await voice_mgr.broadcast(room_id, {
                    "event":    "user_muted",
                    "username": username,
                    "muted":    muted,
                    "time":     now_iso(),
                }, exclude=username)

    except WebSocketDisconnect:
        voice_mgr.disconnect(room_id, username)
        await _log_call(room_id, "left", username)

        await voice_mgr.broadcast(room_id, {
            "event":        "user_left",
            "username":     username,
            "participants": voice_mgr.participants(room_id),
            "online":       voice_mgr.online_count(room_id),
            "time":         now_iso(),
        })
        log.info(f"[VOICE] 💀 {username} disconnected from {room_id}")

    except Exception as e:
        log.error(f"[VOICE] Error — {username} in {room_id}: {e}")
        voice_mgr.disconnect(room_id, username)
                   
