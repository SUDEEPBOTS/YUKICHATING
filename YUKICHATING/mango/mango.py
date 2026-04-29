"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  M O N G O D B
─  Import anywhere:
─  from YUKICHATING.mango.mango import db, usersdb, chatsdb, msgsdb
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
usersdb  = db.users      # registered users
chatsdb  = db.chats      # chat rooms
msgsdb   = db.messages   # message history
tokensdb = db.tokens     # auth sessions

# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════

async def get_user(user_id: str):
    user = await usersdb.find_one({"user_id": user_id})
    return user or {}

async def save_user(user_id: str, data: dict):
    await usersdb.update_one(
        {"user_id": user_id},
        {"$set": data},
        upsert=True,
    )

async def delete_user(user_id: str):
    await usersdb.delete_one({"user_id": user_id})

async def user_exists(user_id: str) -> bool:
    user = await usersdb.find_one({"user_id": user_id})
    return bool(user)

# ══════════════════════════════════════════════════════════════════════════════
# CHATS / ROOMS
# ══════════════════════════════════════════════════════════════════════════════

async def get_chat(chat_id: str):
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

async def get_all_chats():
    return await chatsdb.find().to_list(length=None)

# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

async def save_message(chat_id: str, data: dict):
    data["chat_id"] = chat_id
    await msgsdb.insert_one(data)

async def get_messages(chat_id: str, limit: int = 50):
    msgs = await msgsdb.find(
        {"chat_id": chat_id}
    ).sort("timestamp", -1).limit(limit).to_list(length=None)
    return msgs[::-1]  # oldest first

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

async def get_token(token: str):
    return await tokensdb.find_one({"token": token})

async def delete_token(user_id: str):
    await tokensdb.delete_one({"user_id": user_id})
