import glob
import importlib
import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from Config import Config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
)
log = logging.getLogger("YUKICHATING")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="YUKI CHATING",
    description="⚡ Fastest Chating Backend",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auto Plugin Loader ────────────────────────────────────────────────────────
def load_plugins():
    plugins = glob.glob("YUKICHATING/Plugins/**/*.py", recursive=True)
    loaded = 0
    failed = 0

    for path in plugins:
        if path.endswith("__init__.py"):
            continue
        module_path = path.replace("/", ".").replace(".py", "")
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "router"):
                app.include_router(module.router)
                log.info(f"✅ Loaded: {module_path}")
                loaded += 1
            else:
                log.warning(f"⚠️  No router in: {module_path}")
        except Exception as e:
            log.error(f"❌ Failed: {module_path} → {e}")
            failed += 1

    log.info(f"╔══════════════════════════════╗")
    log.info(f"  ✅ Loaded  : {loaded} plugins")
    log.info(f"  ❌ Failed  : {failed} plugins")
    log.info(f"╚══════════════════════════════╝")

# ── Events ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    log.info("🚀 YUKI CHATING Starting...")
    load_plugins()
    log.info("✅ Backend is ALIVE & KICKING")

@app.on_event("shutdown")
async def shutdown():
    log.info("💀 YUKI CHATING Shutting down...")

# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "status": "alive",
        "name": "YUKI CHATING",
        "version": "1.0.0",
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
