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
    title       = "YUKI CHATING",
    description = "⚡ Fastest Chating Backend — Built by YUKI TEAM",
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Auto Plugin Loader ────────────────────────────────────────────────────────
def load_plugins():
    plugins = glob.glob("YUKICHATING/Plugins/**/*.py", recursive=True)
    loaded  = 0
    failed  = 0
    skipped = 0

    for path in sorted(plugins):
        if path.endswith("__init__.py"):
            skipped += 1
            continue

        module_path = path.replace(os.sep, ".").replace("/", ".").replace(".py", "")

        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "router"):
                app.include_router(module.router)
                log.info(f"✅ Loaded   : {module_path}")
                loaded += 1
            else:
                log.warning(f"⚠️  No router : {module_path}")
                skipped += 1
        except Exception as e:
            log.error(f"❌ Failed   : {module_path} → {e}")
            failed += 1

    log.info("╔══════════════════════════════════╗")
    log.info(f"  🔌 Plugins Found  : {loaded + failed + skipped}")
    log.info(f"  ✅ Loaded         : {loaded}")
    log.info(f"  ❌ Failed         : {failed}")
    log.info(f"  ⏭️  Skipped        : {skipped}")
    log.info("╚══════════════════════════════════╝")

# ── Events ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    log.info("╔═══════════════════════════════════════╗")
    log.info("  ☠️   Y U K I  C H A T I N G           ")
    log.info("  ⚡  Starting Backend...                ")
    log.info("╚═══════════════════════════════════════╝")
    load_plugins()
    log.info("✅ Backend is ALIVE & KICKING 🚀")

@app.on_event("shutdown")
async def shutdown():
    log.info("💀 YUKI CHATING Shutting down... Bye!")

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {
        "status":  "alive",
        "name":    "YUKI CHATING",
        "version": "1.0.0",
        "docs":    "/docs",
    }

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}

@app.get("/ping", tags=["Health"])
async def ping():
    return {"ping": "pong 🏓"}

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = Config.PORT,
        reload  = Config.DEBUG,
)
