"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  VIDEO CALL
─  WebRTC Signaling + DTLS-SRTP E2E Encryption
─  Path: YUKICHATING/Plugins/Videochat/video.py
────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import secrets
import uuid
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger("YUKICHATING.Video")

router = APIRouter(prefix="/video", tags=["Video Call"])

# ── In-Memory Store ───────────────────────────────────────────────────────────
# { room_id: { participants: {username: {ws, video_on, audio_on, sharing_screen}} } }
video_rooms: Dict[str, dict] = {}

# ── Models ────────────────────────────────────────────────────────────────────
class CreateVideoRoom(BaseModel):
    username:    str
    room_name:   str
    max_users:   int = 8
    private:     bool = False
    video_on:    bool = True
    audio_on:    bool = True

class JoinVideoRoom(BaseModel):
    username:   str
    invite_key: Optional[str] = None

# ── Helpers ───────────────────────────────────────────────────────────────────
def generate_invite_key() -> str:
    return secrets.token_urlsafe(12)

def participant_state(room: dict) -> list:
    return [
        {
            "username":       uname,
            "video_on":       info["video_on"],
            "audio_on":       info["audio_on"],
            "sharing_screen": info["sharing_screen"],
        }
        for uname, info in room["participants"].items()
    ]

def room_info(room_id: str) -> dict:
    room = video_rooms.get(room_id)
    if not room:
        return {}
    return {
        "room_id":      room_id,
        "room_name":    room["room_name"],
        "host":         room["host"],
        "online":       len(room["participants"]),
        "max_users":    room["max_users"],
        "private":      room["private"],
        "participants": participant_state(room),
        "invite_link":  f"/video/join/{room_id}?key={room['invite_key']}" if room["private"] else f"/video/join/{room_id}",
        "created_at":   room["created_at"].isoformat(),
    }

async def broadcast(room_id: str, data: dict, exclude: str = None):
    room = video_rooms.get(room_id)
    if not room:
        return
    dead = []
    for uname, info in room["participants"].items():
        if uname == exclude:
            continue
        try:
            await info["ws"].send_json(data)
        except Exception:
            dead.append(uname)
    for d in dead:
        room["participants"].pop(d, None)

# ── REST Endpoints ────────────────────────────────────────────────────────────

@router.post("/create")
async def create_video_room(body: CreateVideoRoom):
    """Video room banao"""
    room_id    = str(uuid.uuid4())[:8]
    invite_key = generate_invite_key()

    video_rooms[room_id] = {
        "room_name":    body.room_name,
        "host":         body.username,
        "participants": {},
        "invite_key":   invite_key,
        "max_users":    body.max_users,
        "private":      body.private,
        "created_at":   datetime.utcnow(),
    }
    log.info(f"📹 Video room created: {room_id} by {body.username}")

    return {
        "status":      "created",
        "room_id":     room_id,
        "invite_key":  invite_key,
        "invite_link": f"/video/join/{room_id}?key={invite_key}" if body.private else f"/video/join/{room_id}",
        "ws_url":      f"/video/ws/{room_id}/{body.username}",
        "encryption":  "DTLS-SRTP (WebRTC built-in)",
    }


@router.get("/rooms")
async def list_video_rooms():
    """Sab public video rooms"""
    public = [room_info(rid) for rid, r in video_rooms.items() if not r["private"]]
    return {"rooms": public, "total": len(public)}


@router.get("/room/{room_id}")
async def get_video_room(room_id: str):
    if room_id not in video_rooms:
        raise HTTPException(404, "Video room not found")
    return room_info(room_id)


@router.delete("/room/{room_id}")
async def delete_video_room(room_id: str, username: str):
    room = video_rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if room["host"] != username:
        raise HTTPException(403, "Sirf host delete kar sakta hai")

    await broadcast(room_id, {"event": "room_closed", "message": "Host ne room band kar diya"})
    video_rooms.pop(room_id)
    return {"status": "deleted", "room_id": room_id}


@router.get("/invite/{room_id}")
async def regenerate_invite(room_id: str, username: str):
    room = video_rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if room["host"] != username:
        raise HTTPException(403, "Sirf host invite regenerate kar sakta hai")

    new_key = generate_invite_key()
    room["invite_key"] = new_key
    return {
        "new_invite_key":  new_key,
        "new_invite_link": f"/video/join/{room_id}?key={new_key}",
    }


# ── WebSocket Signaling ───────────────────────────────────────────────────────
# WebRTC Signaling flow (same as voice but tracks = audio + video):
#   offer / answer / ice  →  relayed peer-to-peer
#   video_toggle          →  broadcast to all
#   audio_toggle          →  broadcast to all
#   screen_share_start    →  broadcast (screenshare.py handles the stream itself)
#   screen_share_stop     →  broadcast

@router.websocket("/ws/{room_id}/{username}")
async def video_ws(
    websocket: WebSocket,
    room_id:   str,
    username:  str,
    key:       Optional[str] = None,
):
    room = video_rooms.get(room_id)

    if not room:
        await websocket.close(code=4004, reason="Room not found")
        return

    if room["private"] and key != room["invite_key"]:
        await websocket.close(code=4003, reason="Invalid invite key")
        return

    if len(room["participants"]) >= room["max_users"]:
        await websocket.close(code=4029, reason="Room full")
        return

    await websocket.accept()
    room["participants"][username] = {
        "ws":             websocket,
        "video_on":       True,
        "audio_on":       True,
        "sharing_screen": False,
    }
    log.info(f"📹 {username} joined video room {room_id}")

    # Notify others
    await broadcast(room_id, {
        "event":        "user_joined",
        "username":     username,
        "participants": participant_state(room),
        "online":       len(room["participants"]),
    }, exclude=username)

    # Send room state to new joiner
    await websocket.send_json({
        "event":        "room_state",
        "room_id":      room_id,
        "participants": participant_state(room),
        "online":       len(room["participants"]),
        "encryption":   "DTLS-SRTP",
    })

    try:
        while True:
            raw        = await websocket.receive_text()
            data       = json.loads(raw)
            event_type = data.get("type")

            # ── WebRTC Peer Signaling ─────────────────────────────────────────
            if event_type in ("offer", "answer", "ice"):
                target    = data.get("to")
                target_ws = room["participants"].get(target, {}).get("ws")
                if target_ws:
                    data["from"] = username
                    await target_ws.send_json(data)

            # ── Video On/Off ──────────────────────────────────────────────────
            elif event_type == "video_toggle":
                state = data.get("video_on", True)
                room["participants"][username]["video_on"] = state
                await broadcast(room_id, {
                    "event":    "video_toggled",
                    "username": username,
                    "video_on": state,
                })

            # ── Audio Mute/Unmute ─────────────────────────────────────────────
            elif event_type == "audio_toggle":
                state = data.get("audio_on", True)
                room["participants"][username]["audio_on"] = state
                await broadcast(room_id, {
                    "event":    "audio_toggled",
                    "username": username,
                    "audio_on": state,
                })

            # ── Screen Share Start ────────────────────────────────────────────
            elif event_type == "screen_share_start":
                room["participants"][username]["sharing_screen"] = True
                await broadcast(room_id, {
                    "event":    "screen_share_started",
                    "username": username,
                }, exclude=username)

            # ── Screen Share Stop ─────────────────────────────────────────────
            elif event_type == "screen_share_stop":
                room["participants"][username]["sharing_screen"] = False
                await broadcast(room_id, {
                    "event":    "screen_share_stopped",
                    "username": username,
                })

            # ── Ping ──────────────────────────────────────────────────────────
            elif event_type == "ping":
                await websocket.send_json({"event": "pong"})

    except WebSocketDisconnect:
        room["participants"].pop(username, None)
        log.info(f"📹 {username} left video room {room_id}")

        await broadcast(room_id, {
            "event":        "user_left",
            "username":     username,
            "participants": participant_state(room),
            "online":       len(room["participants"]),
        })

        if not room["participants"]:
            video_rooms.pop(room_id, None)
            log.info(f"🗑️ Empty video room {room_id} auto-deleted")
