"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  SCREEN SHARE
─  WebRTC Screen Share Signaling
─  Path: YUKICHATING/Plugins/Screenshare/screenshare.py
────────────────────────────────────────────────────────────────────────

HOW IT WORKS:
  1. Sharer  → POST /screen/start         → gets share_id
  2. Viewers → GET  /screen/sessions      → dekho kaun share kar raha
  3. Sharer  → WS   /screen/ws/{share_id}/host
  4. Viewer  → WS   /screen/ws/{share_id}/view/{username}
  5. WebRTC offer/answer/ice relay server karega
  6. Actual screen stream peer-to-peer (server media nahi dekhta — E2E)
  7. Sharer  → POST /screen/stop/{share_id}  → session khatam
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger("YUKICHATING.ScreenShare")

router = APIRouter(prefix="/screen", tags=["Screen Share"])

# ── In-Memory Store ───────────────────────────────────────────────────────────
# { share_id: { host_ws, viewers: {username: ws}, active: bool, ... } }
screen_sessions: Dict[str, dict] = {}

# ── Models ────────────────────────────────────────────────────────────────────
class StartScreenShare(BaseModel):
    username:  str
    title:     str = "Screen Share"
    room_id:   Optional[str] = None   # optional — link to voice/video room
    private:   bool = False

# ── Helpers ───────────────────────────────────────────────────────────────────
def session_info(sid: str) -> dict:
    s = screen_sessions.get(sid)
    if not s:
        return {}
    return {
        "share_id":   sid,
        "title":      s["title"],
        "host":       s["host"],
        "viewers":    list(s["viewers"].keys()),
        "viewer_count": len(s["viewers"]),
        "active":     s["active"],
        "room_id":    s["room_id"],
        "private":    s["private"],
        "started_at": s["started_at"].isoformat(),
        "ws_host":    f"/screen/ws/{sid}/host",
        "ws_view":    f"/screen/ws/{sid}/view/{{your_username}}",
    }

async def notify_host(share_id: str, data: dict):
    s = screen_sessions.get(share_id)
    if s and s.get("host_ws"):
        try:
            await s["host_ws"].send_json(data)
        except Exception:
            pass

async def broadcast_to_viewers(share_id: str, data: dict, exclude: str = None):
    s = screen_sessions.get(share_id)
    if not s:
        return
    dead = []
    for uname, ws in s["viewers"].items():
        if uname == exclude:
            continue
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(uname)
    for d in dead:
        s["viewers"].pop(d, None)

# ── REST Endpoints ────────────────────────────────────────────────────────────

@router.post("/start")
async def start_screen_share(body: StartScreenShare):
    """Screen share session shuru karo"""
    share_id = str(uuid.uuid4())[:8]

    screen_sessions[share_id] = {
        "title":      body.title,
        "host":       body.username,
        "host_ws":    None,
        "viewers":    {},
        "active":     False,          # True jab host WS se connect kare
        "room_id":    body.room_id,
        "private":    body.private,
        "started_at": datetime.utcnow(),
    }
    log.info(f"🖥️ Screen share session created: {share_id} by {body.username}")

    return {
        "status":    "created",
        "share_id":  share_id,
        "ws_host":   f"/screen/ws/{share_id}/host",
        "ws_view":   f"/screen/ws/{share_id}/view/{{username}}",
        "view_url":  f"/screen/session/{share_id}",
    }


@router.post("/stop/{share_id}")
async def stop_screen_share(share_id: str, username: str):
    """Screen share band karo"""
    s = screen_sessions.get(share_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if s["host"] != username:
        raise HTTPException(403, "Sirf host share stop kar sakta hai")

    await broadcast_to_viewers(share_id, {
        "event":   "share_stopped",
        "message": f"{username} ne screen share rok di",
    })
    screen_sessions.pop(share_id)
    return {"status": "stopped", "share_id": share_id}


@router.get("/sessions")
async def list_sessions():
    """Sab active screen share sessions"""
    active = [
        session_info(sid)
        for sid, s in screen_sessions.items()
        if s["active"] and not s["private"]
    ]
    return {"sessions": active, "total": len(active)}


@router.get("/session/{share_id}")
async def get_session(share_id: str):
    """Single session info"""
    if share_id not in screen_sessions:
        raise HTTPException(404, "Session not found")
    return session_info(share_id)


# ── WebSocket — HOST (sharer) ─────────────────────────────────────────────────
@router.websocket("/ws/{share_id}/host")
async def screen_host_ws(websocket: WebSocket, share_id: str):
    """
    Host ka WebSocket — ye screen stream initiate karega.
    Har viewer ke liye alag WebRTC offer bhejega.
    """
    s = screen_sessions.get(share_id)
    if not s:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    s["host_ws"] = websocket
    s["active"]  = True
    log.info(f"🖥️ Host {s['host']} connected to screen share {share_id}")

    # Notify existing viewers ki host aa gaya
    await broadcast_to_viewers(share_id, {
        "event":    "host_connected",
        "host":     s["host"],
        "share_id": share_id,
    })

    try:
        while True:
            raw        = await websocket.receive_text()
            data       = json.loads(raw)
            event_type = data.get("type")

            # ── Relay offer to specific viewer ────────────────────────────────
            if event_type == "offer":
                target    = data.get("to")
                target_ws = s["viewers"].get(target)
                if target_ws:
                    data["from"] = s["host"]
                    await target_ws.send_json(data)

            # ── Relay ICE to specific viewer ──────────────────────────────────
            elif event_type == "ice":
                target    = data.get("to")
                target_ws = s["viewers"].get(target)
                if target_ws:
                    data["from"] = s["host"]
                    await target_ws.send_json(data)

            # ── Ping ──────────────────────────────────────────────────────────
            elif event_type == "ping":
                await websocket.send_json({"event": "pong"})

            # ── Pause Share ───────────────────────────────────────────────────
            elif event_type == "pause":
                await broadcast_to_viewers(share_id, {
                    "event": "share_paused",
                    "host":  s["host"],
                })

            # ── Resume Share ──────────────────────────────────────────────────
            elif event_type == "resume":
                await broadcast_to_viewers(share_id, {
                    "event": "share_resumed",
                    "host":  s["host"],
                })

    except WebSocketDisconnect:
        s["host_ws"] = None
        s["active"]  = False
        log.info(f"🖥️ Host {s['host']} disconnected from {share_id}")

        await broadcast_to_viewers(share_id, {
            "event":   "share_stopped",
            "message": "Host disconnected",
        })
        screen_sessions.pop(share_id, None)


# ── WebSocket — VIEWER ────────────────────────────────────────────────────────
@router.websocket("/ws/{share_id}/view/{username}")
async def screen_viewer_ws(websocket: WebSocket, share_id: str, username: str):
    """
    Viewer ka WebSocket — screen dekho.
    Host se WebRTC answer bhejega.
    """
    s = screen_sessions.get(share_id)
    if not s:
        await websocket.close(code=4004, reason="Session not found")
        return

    if not s["active"]:
        await websocket.close(code=4002, reason="Host abhi connected nahi")
        return

    await websocket.accept()
    s["viewers"][username] = websocket
    log.info(f"👁️ {username} joined screen share {share_id}")

    # Notify host ki naya viewer aaya — host usse offer bhejega
    await notify_host(share_id, {
        "event":    "viewer_joined",
        "username": username,
        "viewers":  list(s["viewers"].keys()),
    })

    # Tell viewer current state
    await websocket.send_json({
        "event":    "session_state",
        "share_id": share_id,
        "host":     s["host"],
        "title":    s["title"],
        "viewers":  list(s["viewers"].keys()),
    })

    try:
        while True:
            raw        = await websocket.receive_text()
            data       = json.loads(raw)
            event_type = data.get("type")

            # ── Relay answer to host ──────────────────────────────────────────
            if event_type == "answer":
                data["from"] = username
                await notify_host(share_id, data)

            # ── Relay ICE to host ─────────────────────────────────────────────
            elif event_type == "ice":
                data["from"] = username
                await notify_host(share_id, data)

            # ── Ping ──────────────────────────────────────────────────────────
            elif event_type == "ping":
                await websocket.send_json({"event": "pong"})

            # ── Chat during screen share ──────────────────────────────────────
            elif event_type == "chat":
                await broadcast_to_viewers(share_id, {
                    "event":    "chat",
                    "username": username,
                    "message":  data.get("message", ""),
                }, exclude=username)
                await notify_host(share_id, {
                    "event":    "chat",
                    "username": username,
                    "message":  data.get("message", ""),
                })

    except WebSocketDisconnect:
        s["viewers"].pop(username, None)
        log.info(f"👁️ {username} left screen share {share_id}")

        await notify_host(share_id, {
            "event":    "viewer_left",
            "username": username,
            "viewers":  list(s["viewers"].keys()),
        })
