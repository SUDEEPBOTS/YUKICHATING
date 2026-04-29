"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  M O N G O D B
─  Import anywhere:
─  from YUKICHATING.mango.mango import db, usersdb, chatsdb, msgsdb ...
────────────────────────────────────────────────────────────────────────
"""

import logging

from motor.motor_asyncio import AsyncIOMotorClient

from Config import Config

log = logging.getLogger("YUKICHATING.mango")

# ── Connection ────────────────────────────────────────────────────────────────
log.info("Connecting to MongoDB...")
try:
    _mongo_ = AsyncIOMotorClient(Config.MONGO_URI)
    db       = _mongo_.YukiChating
    log.info("Connected to MongoDB ✅")
except Exception as e:
    log.error(f"MongoDB connection failed ❌ → {e}")
    exit()

# ── Collections ───────────────────────────────────────────────────────────────
usersdb    = db.users        # registered users
chatsdb    = db.chats        # chat rooms
msgsdb     = db.messages     # message history
tokensdb   = db.tokens       # auth sessions
voicedb    = db.voice_rooms  # voice call rooms
calllogsdb = db.call_logs    # voice join/leave history
videodb    = db.video_rooms  # video call rooms
suspenddb  = db.suspensions  # suspend history / reasons

# ══════════════════════════════════════════════════════════════════════════════
# USERS — Basic
# ══════════════════════════════════════════════════════════════════════════════

async def get_user(user_id: str) -> dict:
    user = await usersdb.find_one({"user_id": user_id})
    return user or {}

async def get_user_by_username(username: str) -> dict | None:
    return await usersdb.find_one({"username": username.lower().strip()})

async def get_user_by_email(email: str) -> dict | None:
    return await usersdb.find_one({"email": email.lower().strip()})

async def create_user(data: dict):
    """Naya user insert karo."""
    await usersdb.insert_one(data)

async def save_user(user_id: str, data: dict):
    await usersdb.update_one(
        {"user_id": user_id},
        {"$set": data},
        upsert=True,
    )

async def user_exists(identifier: str) -> bool:
    """Username ya email se check karo."""
    user = await usersdb.find_one({
        "$or": [
            {"username": identifier.lower()},
            {"email":    identifier.lower()},
        ]
    })
    return bool(user)

async def update_user_password(username: str, new_password_hash: str):
    await usersdb.update_one(
        {"username": username},
        {"$set": {"password": new_password_hash}},
    )

# ══════════════════════════════════════════════════════════════════════════════
# USERS — Suspend / Unsuspend
# ══════════════════════════════════════════════════════════════════════════════

async def suspend_user(username: str, reason: str, suspended_by: str):
    """
    User ko suspend karo.
    Suspend history bhi save hogi suspenddb mein.
    """
    from datetime import datetime
    import uuid

    now = datetime.utcnow().isoformat()

    # User flag karo
    await usersdb.update_one(
        {"username": username},
        {"$set": {
            "is_suspended":    True,
            "suspend_reason":  reason,
            "suspended_by":    suspended_by,
            "suspended_at":    now,
        }},
    )

    # History log
    await suspenddb.insert_one({
        "log_id":       str(uuid.uuid4()),
        "username":     username,
        "action":       "suspended",
        "reason":       reason,
        "actioned_by":  suspended_by,
        "timestamp":    now,
    })
    log.info(f"[mango] 🚫 {username} suspended by {suspended_by} — reason: {reason}")


async def unsuspend_user(username: str, unsuspended_by: str):
    """User ka suspension hatao."""
    from datetime import datetime
    import uuid

    now = datetime.utcnow().isoformat()

    await usersdb.update_one(
        {"username": username},
        {"$set": {
            "is_suspended":   False,
            "suspend_reason": None,
            "suspended_by":   None,
            "suspended_at":   None,
        }},
    )

    await suspenddb.insert_one({
        "log_id":      str(uuid.uuid4()),
        "username":    username,
        "action":      "unsuspended",
        "reason":      None,
        "actioned_by": unsuspended_by,
        "timestamp":   now,
    })
    log.info(f"[mango] ✅ {username} unsuspended by {unsuspended_by}")


async def get_suspend_history(username: str) -> list:
    """Kisi user ki puri suspend history dekho."""
    logs = await suspenddb.find(
        {"username": username}
    ).sort("timestamp", -1).to_list(length=None)
    for l in logs:
        l.pop("_id", None)
    return logs

# ══════════════════════════════════════════════════════════════════════════════
# USERS — Delete
# ══════════════════════════════════════════════════════════════════════════════

async def delete_user(username: str):
    """
    User aur uska saara data delete karo.
    — User record
    — Uske saare messages
    — Suspend history
    — Auth tokens
    — Call logs
    """
    await usersdb.delete_one({"username": username})
    await msgsdb.delete_many({"sender": username})
    await suspenddb.delete_many({"username": username})
    await tokensdb.delete_many({"username": username})
    await calllogsdb.delete_many({"username": username})
    log.info(f"[mango] 🗑️ User {username} aur uska saara data delete ho gaya")


async def delete_user_by_id(user_id: str):
    """user_id se delete karo."""
    await usersdb.delete_one({"user_id": user_id})

# ══════════════════════════════════════════════════════════════════════════════
# CHATS / ROOMS
# ══════════════════════════════════════════════════════════════════════════════

async def get_chat(chat_id: str) -> dict:
    chat = await chatsdb.find_one({"chat_id": chat_id})
    return chat or {}

async def save_chat(chat_id: str, data: dict):
    await chatsdb.update_one(
        {"chat_id": chat_id},
        {"$set": data},
        upsert=True,
    )

async def delete_chat(chat_id: str):
    await chatsdb.delete_one({"chat_id": chat_id})

async def get_all_chats() -> list:
    return await chatsdb.find().to_list(length=None)

# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

async def save_message(chat_id: str, data: dict):
    data["chat_id"] = chat_id
    await msgsdb.insert_one(data)

async def get_messages(chat_id: str, limit: int = 50) -> list:
    msgs = await msgsdb.find(
        {"chat_id": chat_id}
    ).sort("timestamp", -1).limit(limit).to_list(length=None)
    return msgs[::-1]   # oldest first

async def delete_messages(chat_id: str):
    await msgsdb.delete_many({"chat_id": chat_id})

# ══════════════════════════════════════════════════════════════════════════════
# TOKENS / SESSIONS
# ══════════════════════════════════════════════════════════════════════════════

async def save_token(user_id: str, token: str):
    await tokensdb.update_one(
        {"user_id": user_id},
        {"$set": {"token": token}},
        upsert=True,
    )

async def get_token(token: str) -> dict | None:
    return await tokensdb.find_one({"token": token})

async def delete_token(user_id: str):
    await tokensdb.delete_one({"user_id": user_id})
