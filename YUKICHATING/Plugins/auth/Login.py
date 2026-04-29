"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  A U T H  M O D U L E
─  Routes : /auth/*
─  Features:
─    • Register + OTP verify
─    • Login (username OR email) + JWT
─    • Forgot / Reset password
─    • Check username / email availability
─    • Rate limiting (brute-force protection)
─    • Cloudflare Turnstile captcha
─    • Login alert email (IP + location)
─    • Gmail dot/plus trick killer
────────────────────────────────────────────────────────────────────────
"""

import hashlib
import logging
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
import requests as req_sync
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from jose import jwt
from pydantic import BaseModel

from YUKICHATING.Gmailtemplate.Template import (
    render_login_alert,
    render_otp_email,
    render_reset_otp_email,
    render_welcome_email,
)
from YUKICHATING.mango.mango import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    update_user_password,
    user_exists,
)

log = logging.getLogger("YUKICHATING.auth")

router = APIRouter(prefix="/auth", tags=["Auth"])

# ── JWT Config ────────────────────────────────────────────────────────────────
JWT_SECRET    = os.getenv("JWT_SECRET", "yuki_super_secret_change_this")
JWT_ALGO      = "HS256"
JWT_EXPIRE_H  = 72   # hours

# ── Rate Limiter (in-memory, per IP) ─────────────────────────────────────────
# Stores: { ip: [timestamp, timestamp, ...] }
_rate_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT   = 10    # max requests
RATE_WINDOW  = 60    # per 60 seconds

def _check_rate(ip: str):
    now   = time.time()
    calls = [t for t in _rate_store[ip] if now - t < RATE_WINDOW]
    _rate_store[ip] = calls
    if len(calls) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"⏳ Too many requests! Chill karo — {RATE_WINDOW}s baad try karo.",
        )
    _rate_store[ip].append(now)

# ── Brute-force guard on login (5 fails → 5 min lockout) ─────────────────────
_login_fails: dict[str, list[float]] = defaultdict(list)
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT   = 300   # seconds

def _check_login_brute(ip: str):
    now   = time.time()
    fails = [t for t in _login_fails[ip] if now - t < LOGIN_LOCKOUT]
    _login_fails[ip] = fails
    if len(fails) >= LOGIN_MAX_FAILS:
        remaining = int(LOGIN_LOCKOUT - (now - fails[0]))
        raise HTTPException(
            status_code=429,
            detail=f"🔒 Account temporarily locked! {remaining}s baad try karo.",
        )

def _record_login_fail(ip: str):
    _login_fails[ip].append(time.time())

def _clear_login_fail(ip: str):
    _login_fails.pop(ip, None)

# ── OTP temp store ────────────────────────────────────────────────────────────
# key  → { data dict }   for registration
# key  → int otp          for password reset
TEMP_OTP_STORE: dict[str, dict | int] = {}
OTP_TTL = 600   # 10 minutes

def _otp_expired(stored: dict) -> bool:
    return time.time() - stored.get("created_at", 0) > OTP_TTL

# ── Helpers ───────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def sanitize_email(email: str) -> str:
    """Gmail-only, kills dot/plus tricks."""
    email = email.lower().strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="❌ Invalid email format!")
    username, domain = email.split("@", 1)
    if domain not in ("gmail.com", "googlemail.com"):
        raise HTTPException(
            status_code=400,
            detail="❌ Only @gmail.com addresses are allowed!",
        )
    username = username.split("+")[0].replace(".", "")
    return f"{username}@gmail.com"

def validate_username(username: str):
    """3-20 chars, alphanumeric + underscore only."""
    if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
        raise HTTPException(
            status_code=400,
            detail="❌ Username must be 3-20 characters (letters, numbers, _ only).",
        )

def validate_password(password: str):
    """Min 8 chars, at least 1 number."""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="❌ Password must be at least 8 characters!")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="❌ Password must contain at least one number!")

def make_jwt(user: dict) -> str:
    payload = {
        "sub":          user["username"],
        "email":        user["email"],
        "is_premium":   user.get("is_premium", False),
        "is_suspended": user.get("is_suspended", False),
        "exp":          datetime.utcnow() + timedelta(hours=JWT_EXPIRE_H),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def make_api_key(username: str) -> str:
    raw = username + str(random.random())
    return f"yuki_{hash_password(raw)[:20]}"

async def verify_turnstile(token: str) -> bool:
    secret = os.getenv("CLOUDFLARE_SECRET_KEY")
    if not secret:
        log.warning("CLOUDFLARE_SECRET_KEY missing — skipping captcha check")
        return True
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token},
        )
        return r.json().get("success", False)

def get_location(ip: str) -> str:
    try:
        d = req_sync.get(f"http://ip-api.com/json/{ip}", timeout=3).json()
        if d.get("status") == "success":
            return f"{d.get('city')}, {d.get('country')}"
    except Exception:
        pass
    return "Unknown"

# ── Email dispatcher (uses Gmailtemplate/Template.py) ─────────────────────────
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def _smtp_send(to: str, subject: str, html: str, from_name: str = "YUKI CHATING"):
    sender    = os.getenv("SENDER_EMAIL")
    password  = os.getenv("SENDER_PASSWORD")
    if not sender or not password:
        log.warning("Email creds missing — skipping email send")
        return
    msg              = MIMEMultipart("alternative")
    msg["Subject"]   = subject
    msg["From"]      = f"{from_name} <{sender}>"
    msg["To"]        = to
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(sender, password)
            s.sendmail(sender, to, msg.as_string())
        log.info(f"📧 Email sent → {to}")
    except Exception as e:
        log.error(f"🚨 Email failed → {to}: {e}")

def _send_otp(to: str, username: str, otp: int):
    html = render_otp_email(username, otp)
    _smtp_send(to, "🔐 Verify Your YUKI CHATING Account", html)

def _send_reset_otp(to: str, username: str, otp: int):
    html = render_reset_otp_email(username, otp)
    _smtp_send(to, "🔒 Password Reset — YUKI CHATING", html, "YUKI Security")

def _send_welcome(to: str, username: str):
    html = render_welcome_email(username)
    _smtp_send(to, "🎉 Welcome to YUKI CHATING!", html)

def _send_login_alert(to: str, username: str, ip: str):
    location = get_location(ip)
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html     = render_login_alert(username, ip, location, time_now)
    _smtp_send(to, "🚨 New Login Detected — YUKI CHATING", html, "YUKI Security")

# ── Payload Models ────────────────────────────────────────────────────────────
class RegisterPayload(BaseModel):
    username:      str
    email:         str
    password:      str
    captcha_token: str

class VerifyOTPPayload(BaseModel):
    email: str
    otp:   int

class LoginPayload(BaseModel):
    identifier:    str   # username OR email
    password:      str
    captcha_token: str

class ForgotPasswordPayload(BaseModel):
    identifier: str   # username OR email

class ResetPasswordPayload(BaseModel):
    identifier:   str
    otp:          int
    new_password: str

# ══════════════════════════════════════════════════════════════════════════════
# UTILITY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/check/username")
async def check_username(username: str):
    """
    Username available hai ya nahi.
    DB + pending OTP store dono check karta hai.
    """
    clean = username.lower().strip()
    # DB check
    if await get_user_by_username(clean):
        return {"available": False, "reason": "already_taken"}
    # Pending OTP store check
    for val in TEMP_OTP_STORE.values():
        if isinstance(val, dict) and val.get("username") == clean:
            return {"available": False, "reason": "pending_verification"}
    return {"available": True}


@router.get("/check/email")
async def check_email(email: str):
    """
    Email valid (gmail only) hai ya nahi, aur already registered hai ya nahi.
    """
    try:
        clean = sanitize_email(email)
    except HTTPException as e:
        return {"valid": False, "available": False, "reason": e.detail}

    if await get_user_by_email(clean):
        return {"valid": True, "available": False, "reason": "already_registered"}
    return {"valid": True, "available": True}


@router.get("/check/both")
async def check_both(username: str, email: str):
    """One-shot check: username + email dono ek saath."""
    u_result = await check_username(username)
    e_result = await check_email(email)
    return {
        "username": u_result,
        "email":    e_result,
        "all_clear": u_result["available"] and e_result.get("available", False),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 1. REGISTER
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def register(payload: RegisterPayload, request: Request, bg: BackgroundTasks):
    ip = request.client.host if request.client else "unknown"
    _check_rate(ip)

    # Captcha
    if not await verify_turnstile(payload.captcha_token):
        raise HTTPException(status_code=400, detail="🤖 Captcha failed! Tu bot hai kya?")

    # Validate inputs
    validate_username(payload.username)
    validate_password(payload.password)
    clean_email = sanitize_email(payload.email)
    clean_user  = payload.username.lower().strip()

    # Duplicate checks
    if await get_user_by_username(clean_user):
        raise HTTPException(status_code=409, detail="❌ Username already taken!")
    if await get_user_by_email(clean_email):
        raise HTTPException(status_code=409, detail="❌ Email already registered!")

    # Generate OTP
    otp = random.randint(100000, 999999)
    TEMP_OTP_STORE[clean_email] = {
        "username":      clean_user,
        "password_hash": hash_password(payload.password),
        "otp":           otp,
        "created_at":    time.time(),
    }

    bg.add_task(_send_otp, clean_email, payload.username, otp)
    log.info(f"[register] OTP sent → {clean_email}")
    return {"status": "success", "message": "OTP sent! 10 minutes mein verify karo."}

# ══════════════════════════════════════════════════════════════════════════════
# 2. VERIFY OTP
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/verify-otp")
async def verify_otp(payload: VerifyOTPPayload, bg: BackgroundTasks):
    clean_email = sanitize_email(payload.email)

    stored = TEMP_OTP_STORE.get(clean_email)
    if not stored or not isinstance(stored, dict):
        raise HTTPException(status_code=404, detail="❌ No pending registration found!")
    if _otp_expired(stored):
        del TEMP_OTP_STORE[clean_email]
        raise HTTPException(status_code=400, detail="⏳ OTP expired! Register again karo.")
    if stored["otp"] != payload.otp:
        raise HTTPException(status_code=400, detail="❌ Wrong OTP!")

    api_key  = make_api_key(stored["username"])
    new_user = {
        "username":     stored["username"],
        "email":        clean_email,
        "password":     stored["password_hash"],
        "api_key":      api_key,
        "is_suspended": False,
        "is_premium":   False,
        "created_at":   datetime.utcnow().isoformat(),
    }
    await create_user(new_user)

    bg.add_task(_send_welcome, clean_email, stored["username"])
    del TEMP_OTP_STORE[clean_email]

    log.info(f"[verify-otp] Account created → {stored['username']}")
    return {
        "status":  "success",
        "message": f"✅ Welcome {stored['username']}! Account created.",
        "api_key": api_key,
    }

# ══════════════════════════════════════════════════════════════════════════════
# 3. LOGIN
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/login")
async def login(payload: LoginPayload, request: Request, bg: BackgroundTasks):
    ip = request.client.host if request.client else "unknown"
    _check_rate(ip)
    _check_login_brute(ip)

    if not await verify_turnstile(payload.captcha_token):
        raise HTTPException(status_code=400, detail="🤖 Captcha failed!")

    identifier = payload.identifier.lower().strip()

    # Find user — email OR username
    if "@" in identifier:
        try:
            clean_email = sanitize_email(identifier)
            user = await get_user_by_email(clean_email)
        except HTTPException:
            raise HTTPException(status_code=404, detail="❌ Account not found!")
    else:
        user = await get_user_by_username(identifier)

    if not user:
        _record_login_fail(ip)
        raise HTTPException(status_code=404, detail="❌ Account not found!")

    # Suspended check
    if user.get("is_suspended"):
        raise HTTPException(status_code=403, detail="🚫 Your account has been suspended!")

    # Password check
    if user["password"] != hash_password(payload.password):
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="❌ Incorrect password!")

    _clear_login_fail(ip)

    # JWT token
    token = make_jwt(user)

    bg.add_task(_send_login_alert, user["email"], user["username"], ip)
    log.info(f"[login] ✅ {user['username']} from {ip}")

    return {
        "status":       "success",
        "message":      f"✅ Welcome back, {user['username']}!",
        "token":        token,
        "api_key":      user["api_key"],
        "username":     user["username"],
        "is_premium":   user.get("is_premium", False),
        "is_suspended": user.get("is_suspended", False),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 4. FORGOT PASSWORD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordPayload, request: Request, bg: BackgroundTasks):
    ip = request.client.host if request.client else "unknown"
    _check_rate(ip)

    identifier = payload.identifier.lower().strip()

    if "@" in identifier:
        try:
            clean_email = sanitize_email(identifier)
            user = await get_user_by_email(clean_email)
        except HTTPException:
            raise HTTPException(status_code=404, detail="❌ User not found!")
    else:
        user = await get_user_by_username(identifier)

    if not user:
        raise HTTPException(status_code=404, detail="❌ User not found!")

    otp       = random.randint(100000, 999999)
    store_key = f"reset_{user['username']}"
    TEMP_OTP_STORE[store_key] = {
        "otp":        otp,
        "created_at": time.time(),
    }

    bg.add_task(_send_reset_otp, user["email"], user["username"], otp)
    log.info(f"[forgot-password] Reset OTP sent → {user['username']}")
    return {"status": "success", "message": "✅ Reset OTP sent to registered email!"}

# ══════════════════════════════════════════════════════════════════════════════
# 5. RESET PASSWORD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/reset-password")
async def reset_password(payload: ResetPasswordPayload):
    identifier = payload.identifier.lower().strip()

    if "@" in identifier:
        try:
            clean_email = sanitize_email(identifier)
            user = await get_user_by_email(clean_email)
        except HTTPException:
            raise HTTPException(status_code=404, detail="❌ User not found!")
    else:
        user = await get_user_by_username(identifier)

    if not user:
        raise HTTPException(status_code=404, detail="❌ User not found!")

    store_key = f"reset_{user['username']}"
    stored    = TEMP_OTP_STORE.get(store_key)

    if not stored or not isinstance(stored, dict):
        raise HTTPException(status_code=400, detail="❌ No pending reset request found!")
    if _otp_expired(stored):
        del TEMP_OTP_STORE[store_key]
        raise HTTPException(status_code=400, detail="⏳ OTP expired! Request again karo.")
    if stored["otp"] != payload.otp:
        raise HTTPException(status_code=400, detail="❌ Wrong OTP!")

    validate_password(payload.new_password)
    await update_user_password(user["username"], hash_password(payload.new_password))
    del TEMP_OTP_STORE[store_key]

    log.info(f"[reset-password] ✅ Password changed → {user['username']}")
    return {"status": "success", "message": "✅ Password reset ho gaya! Ab login karo."}

