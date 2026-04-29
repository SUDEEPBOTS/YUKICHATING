import os
from dotenv import load_dotenv

load_dotenv()

class Config:

    # ── MongoDB ───────────────────────────────────────────────────────────
    MONGO_URI   = os.getenv("MONGO_URI",   "")
    DB_NAME     = os.getenv("DB_NAME",     "YukiChating")

    # ── JWT ───────────────────────────────────────────────────────────────
    JWT_SECRET  = os.getenv("JWT_SECRET",  "YUKICHATINGS")
    JWT_EXPIRE  = int(os.getenv("JWT_EXPIRE", 72))   # hours

    # ── Email (Gmail SMTP) ────────────────────────────────────────────────
    SENDER_EMAIL    = os.getenv("SENDER_EMAIL",    "")
    SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")

    # ── Cloudflare Turnstile ──────────────────────────────────────────────
    CLOUDFLARE_SECRET_KEY = os.getenv("CLOUDFLARE_SECRET_KEY", "")

    # ── Server ────────────────────────────────────────────────────────────
    PORT        = int(os.getenv("PORT", 8000))
    DEBUG       = os.getenv("DEBUG", "false").lower() == "true"

    # ── Validate on startup ───────────────────────────────────────────────
    @classmethod
    def validate(cls):
        required = {
            "MONGO_URI":         cls.MONGO_URI,
            "JWT_SECRET":        cls.JWT_SECRET,
            "SENDER_EMAIL":      cls.SENDER_EMAIL,
            "SENDER_PASSWORD":   cls.SENDER_PASSWORD,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError(
                f"❌ Missing required env variables: {', '.join(missing)}\n"
                f"   Simple.env file mein add karo!"
            )

Config.validate()
