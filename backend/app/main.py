import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)

from app.database import init_db
from app.routers import health, scan, alerts, overview, brain, kalshi, trades as trades_router, settings as settings_router, auto_trade as auto_trade_router, weather_events as weather_events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services.scheduler import start_scheduler
    start_scheduler()
    yield
    from app.services.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(title="Sibylla Weather Bot", version="1.0.0", lifespan=lifespan)

# CORS: localhost dev origins are always allowed. The Fly.io production URL is
# included so the local React dev server can point VITE_API_URL at it without a
# CORS preflight failure. Extra origins (e.g. a Cloudflare Pages frontend) can
# be added via the CORS_ORIGINS env var as a comma-separated list.
_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://sibylla-kalshibot.fly.dev",
]
_extra_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
_allow_origins = _default_origins + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(scan.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(brain.router, prefix="/api")
app.include_router(kalshi.router, prefix="/api")
app.include_router(trades_router.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(auto_trade_router.router, prefix="/api")
app.include_router(weather_events_router.router, prefix="/api")
