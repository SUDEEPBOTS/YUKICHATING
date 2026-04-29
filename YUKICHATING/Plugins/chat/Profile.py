"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  P R O F I L E
─  Routes : /profile/*
─  Features:
─    • View profile (public / private)
─    • Update name, bio, birthday
─    • Profile photo upload (jpg, png, webp)
─    • Profile video upload (mp4) — Premium only
─    • Public / Private profile toggle
─    • Last seen toggle (show / hide)
─    • Add to contacts / Remove contact
─    • Online status
─    • Search users by username
────────────────────────────────────────────────────────────────────────
"""

import base64
import hashlib
import logging
import mimetypes
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, Header
from jose import JWTError, jwt
from pydantic import BaseModel

from YUKICHATING.mango.mango import (
    usersdb,
    get_user_by_username,
)

# chat.py ka manager import — online status ke liye
from YUKICHATING.Plugins.chat.chat import manager as chat_manager

log = logging.getLogger("YUKICHATING.profile")

router = APIRouter(prefix="/profile", tags=["Profile"])

# ── Config ────────────────────────────────────────────────────────────────────
JWT_SECRET   = os.getenv("JWT_SECRET", "yuki_super_secret_change_this")
JWT_ALGO     = "HS256"

MAX_PHOTO_MB  = 5
MAX_VIDEO_MB  = 20
MAX_PHOTO_B   = MAX_PHOTO_MB * 1024 * 1024
MAX_VIDEO_B   = MAX_VIDEO_MB * 1024 * 1024

ALLOWED_PHOTO = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO = {"video/mp4", "video/webm"}

# ══════════════════════════════════════════════════════════════════════════════
# JWT AUTH HELPER
# ══════════════════════════════════════════════════════════════════════════════

def get_current_user(authorization: str = None) -> str:
    """
    Authorization header se username nikalo.
    Format: Bearer <token>
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="❌ Token required! Login karo pehle.")
    token = authorization.split(" ", 1)[1]
    try:
        payload  = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="❌ Invalid token!")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="❌ Token expired ya invalid!")

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def now_iso() -> str:
    return datetime.utcnow().isoformat()

def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc

def _is_online(username: str) -> bool:
    return chat_manager.is_online(username)

def _last_seen_text(last_seen: str | None, show_last_seen: bool) -> str:
    """Telegram jaisa last seen text."""
    if _is_online(username := ""):   # runtime pe call hoga
        return "online"
    if not show_last_seen:
        return "last seen recently"
    if not last_seen:
        return "last seen recently"
    try:
        dt   = datetime.fromisoformat(last_seen)
        diff = (datetime.utcnow() - dt).total_seconds()
        if diff < 60:
            return "last seen just now"
        elif diff < 3600:
            mins = int(diff // 60)
            return f"last seen {mins} minute{'s' if mins > 1 else ''} ago"
        elif diff < 86400:
            hours = int(diff // 3600)
            return f"last seen {hours} hour{'s' if hours > 1 else ''} ago"
        else:
            return f"last seen {dt.strftime('%b %d')}"
    except Exception:
        return "last seen recently"

def _build_public_profile(user: dict, viewer: str = None) -> dict:
    """
    Kisi bhi user ka public-safe profile banao.
    Private profile pe sirf basic info dikhao.
    """
    username      = user["username"]
    is_online     = _is_online(username)
    is_public     = user.get("is_public", True)
    show_last_seen= user.get("show_last_seen", True)

    # Last seen
    if is_online:
        last_seen_text = "online"
    elif not show_last_seen:
        last_seen_text = "last seen recently"
    else:
        last_seen_text = _last_seen_text(user.get("last_seen"), show_last_seen)

    base = {
        "username":      username,
        "name":          user.get("name") or username,
        "avatar":        user.get("avatar"),        # base64 or URL
        "avatar_video":  user.get("avatar_video"),  # base64 or URL (premium)
        "is_online":     is_online,
        "last_seen":     last_seen_text,
        "is_premium":    user.get("is_premium", False),
        "is_public":     is_public,
    }

    # Private profile pe sirf basic info
    if not is_public and viewer != username:
        base["bio"]      = None
        base["birthday"] = None
        base["private"]  = True
        return base

    base["bio"]      = user.get("bio", "")
    base["birthday"] = user.get("birthday")    # "YYYY-MM-DD"
    base["private"]  = False
    return base

# ══════════════════════════════════════════════════════════════════════════════
# PAYLOAD MODELS
# ══════════════════════════════════════════════════════════════════════════════

class UpdateProfilePayload(BaseModel):
    name:           Optional[str] = None    # Display name
    bio:            Optional[str] = None    # Max 70 chars
    birthday:       Optional[str] = None    # "YYYY-MM-DD"
    is_public:      Optional[bool] = None   # Public / Private profile
    show_last_seen: Optional[bool] = None   # Last seen dikhao ya nahi

# ══════════════════════════════════════════════════════════════════════════════
# PROFILE VIEW ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{username}")
async def get_profile(
    username:      str,
    authorization: Optional[str] = Header(default=None),
):
    """
    Kisi bhi user ka profile dekho.
    Private profile pe limited info milegi.
    """
    # Viewer kaun hai (agar logged in hai toh)
    viewer = None
    if authorization:
        try:
            viewer = get_current_user(authorization)
        except Exception:
            pass

    user = await get_user_by_username(username.lower().strip())
    if not user:
        raise HTTPException(status_code=404, detail="❌ User not found!")

    return _build_public_profile(user, viewer=viewer)


@router.get("/me/full")
async def get_my_profile(authorization: str = Header(...)):
    """
    Apna pura profile dekho (private fields bhi).
    Sirf logged-in user ke liye.
    """
    me = get_current_user(authorization)
    user = await get_user_by_username(me)
    if not user:
        raise HTTPException(status_code=404, detail="❌ User not found!")

    user = _serialize(user)
    # Sensitive fields hata do
    user.pop("password", None)
    user.pop("email", None)
    user.pop("api_key", None)

    # Online status add karo
    user["is_online"]  = _is_online(me)

    return user

# ══════════════════════════════════════════════════════════════════════════════
# PROFILE UPDATE
# ══════════════════════════════════════════════════════════════════════════════

@router.put("/me/update")
async def update_profile(
    payload:       UpdateProfilePayload,
    authorization: str = Header(...),
):
    """Name, bio, birthday, public/private toggle."""
    me = get_current_user(authorization)

    updates = {}

    if payload.name is not None:
        name = payload.name.strip()
        if len(name) > 64:
            raise HTTPException(400, "❌ Name max 64 characters!")
        if len(name) < 1:
            raise HTTPException(400, "❌ Name empty nahi ho sakta!")
        updates["name"] = name

    if payload.bio is not None:
        bio = payload.bio.strip()
        if len(bio) > 70:
            raise HTTPException(400, "❌ Bio max 70 characters! (Telegram jaisa 😄)")
        updates["bio"] = bio

    if payload.birthday is not None:
        try:
            datetime.strptime(payload.birthday, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "❌ Birthday format galat! Use: YYYY-MM-DD")
        updates["birthday"] = payload.birthday

    if payload.is_public is not None:
        updates["is_public"] = payload.is_public

    if payload.show_last_seen is not None:
        updates["show_last_seen"] = payload.show_last_seen

    if not updates:
        raise HTTPException(400, "❌ Kuch bhi update nahi kiya!")

    updates["updated_at"] = now_iso()

    await usersdb.update_one(
        {"username": me},
        {"$set": updates},
    )
    log.info(f"[profile] {me} updated: {list(updates.keys())}")
    return {"status": "success", "updated": list(updates.keys()), "message": "✅ Profile updated!"}

# ══════════════════════════════════════════════════════════════════════════════
# PROFILE PHOTO UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/me/avatar/photo")
async def upload_avatar_photo(
    file:          UploadFile,
    authorization: str = Header(...),
):
    """
    Profile photo upload karo.
    Allowed: jpg, png, webp, gif
    Max: 5MB
    """
    me = get_current_user(authorization)

    data = await file.read()
    if len(data) > MAX_PHOTO_B:
        raise HTTPException(413, f"❌ Photo too large! Max {MAX_PHOTO_MB}MB.")

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_PHOTO:
        raise HTTPException(400, "❌ Sirf JPG, PNG, WEBP, GIF allowed hai!")

    b64    = base64.b64encode(data).decode()
    avatar = f"data:{mime};base64,{b64}"

    await usersdb.update_one(
        {"username": me},
        {"$set": {"avatar": avatar, "updated_at": now_iso()}},
    )
    log.info(f"[profile] {me} updated avatar photo")
    return {"status": "success", "message": "✅ Profile photo update ho gaya!"}


@router.delete("/me/avatar")
async def remove_avatar(authorization: str = Header(...)):
    """Profile photo/video remove karo."""
    me = get_current_user(authorization)
    await usersdb.update_one(
        {"username": me},
        {"$set": {"avatar": None, "avatar_video": None, "updated_at": now_iso()}},
    )
    return {"status": "success", "message": "✅ Avatar remove ho gaya!"}

# ══════════════════════════════════════════════════════════════════════════════
# PROFILE VIDEO UPLOAD — PREMIUM ONLY
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/me/avatar/video")
async def upload_avatar_video(
    file:          UploadFile,
    authorization: str = Header(...),
):
    """
    Profile video (animated PFP) upload karo.
    ⭐ Premium users only!
    Allowed: mp4, webm
    Max: 20MB
    """
    me   = get_current_user(authorization)
    user = await get_user_by_username(me)

    # Premium check
    if not user.get("is_premium", False):
        raise HTTPException(
            status_code=403,
            detail="⭐ Ye feature sirf Premium users ke liye hai! Upgrade karo.",
        )

    data = await file.read()
    if len(data) > MAX_VIDEO_B:
        raise HTTPException(413, f"❌ Video too large! Max {MAX_VIDEO_MB}MB.")

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_VIDEO:
        raise HTTPException(400, "❌ Sirf MP4 aur WEBM allowed hai!")

    b64   = base64.b64encode(data).decode()
    video = f"data:{mime};base64,{b64}"

    await usersdb.update_one(
        {"username": me},
        {"$set": {"avatar_video": video, "updated_at": now_iso()}},
    )
    log.info(f"[profile] ⭐ {me} updated avatar video")
    return {"status": "success", "message": "✅ Profile video update ho gaya! ⭐"}

# ══════════════════════════════════════════════════════════════════════════════
# CONTACTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/contacts/add/{username}")
async def add_contact(username: str, authorization: str = Header(...)):
    """Kisi ko contacts mein add karo."""
    me = get_current_user(authorization)

    if me == username.lower():
        raise HTTPException(400, "❌ Apne aap ko add nahi kar sakte!")

    target = await get_user_by_username(username.lower())
    if not target:
        raise HTTPException(404, "❌ User not found!")

    # Already in contacts?
    my_data = await get_user_by_username(me)
    if username.lower() in my_data.get("contacts", []):
        return {"status": "success", "message": "Already in contacts!"}

    await usersdb.update_one(
        {"username": me},
        {"$push": {"contacts": username.lower()}},
    )
    log.info(f"[contacts] {me} added {username}")
    return {"status": "success", "message": f"✅ {username} contacts mein add ho gaya!"}


@router.delete("/contacts/remove/{username}")
async def remove_contact(username: str, authorization: str = Header(...)):
    """Contacts se remove karo."""
    me = get_current_user(authorization)
    await usersdb.update_one(
        {"username": me},
        {"$pull": {"contacts": username.lower()}},
    )
    return {"status": "success", "message": f"✅ {username} contacts se remove ho gaya!"}


@router.get("/me/contacts")
async def get_my_contacts(authorization: str = Header(...)):
    """Apne saare contacts dekho with online status."""
    me      = get_current_user(authorization)
    my_data = await get_user_by_username(me)
    contacts= my_data.get("contacts", [])

    result = []
    for uname in contacts:
        u = await get_user_by_username(uname)
        if u:
            result.append({
                "username":  u["username"],
                "name":      u.get("name") or u["username"],
                "avatar":    u.get("avatar"),
                "is_online": _is_online(uname),
                "is_premium":u.get("is_premium", False),
            })

    return {"contacts": result, "total": len(result)}

# ══════════════════════════════════════════════════════════════════════════════
# USER SEARCH
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search/users")
async def search_users(q: str, authorization: str = Header(...)):
    """
    Username se user search karo.
    Sirf public profiles dikhenge.
    """
    get_current_user(authorization)   # Auth check

    if len(q) < 3:
        raise HTTPException(400, "❌ Search query kam se kam 3 characters!")

    # MongoDB regex search
    users = await usersdb.find(
        {
            "username":  {"$regex": q.lower(), "$options": "i"},
            "is_suspended": False,
        }
    ).limit(20).to_list(length=None)

    result = []
    for u in users:
        result.append({
            "username":  u["username"],
            "name":      u.get("name") or u["username"],
            "avatar":    u.get("avatar"),
            "bio":       u.get("bio", "") if u.get("is_public", True) else None,
            "is_online": _is_online(u["username"]),
            "is_public": u.get("is_public", True),
            "is_premium":u.get("is_premium", False),
        })

    return {"results": result, "count": len(result)}

# ══════════════════════════════════════════════════════════════════════════════
# LAST SEEN UPDATE (call this on WebSocket disconnect in chat.py)
# ══════════════════════════════════════════════════════════════════════════════

async def update_last_seen(username: str):
    """
    chat.py ke WebSocket disconnect mein ye call karo:
    
    from YUKICHATING.Plugins.profile.Profile import update_last_seen
    await update_last_seen(username)
    """
    await usersdb.update_one(
        {"username": username},
        {"$set": {"last_seen": now_iso()}},
    )
