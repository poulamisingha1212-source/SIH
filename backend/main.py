"""
JanNidhi Sentinel — MoSPI MPLADS AI Sentinel Platform.

Modular application coordinator mounting decoupled routers for:
- Authentication & Sessions (/api/auth)
- Works & Audit Case Packets (/api/works, /api/export/works)
- Review Outcomes & Citizen Verification (/api/works/.../review, /api/works/.../public-review)
- MP & State Directories (/api/mps, /api/states)
- Macro Portfolio Analytics (/api/stats/overview, /api/analytics/...)
- Ingestion & Scheduled Sync (/api/sync, /api/cron/sync)
- Health & Liveness Probes (/api/health)
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import ensure_indexes
from backend.routers import analytics, auth, directory, health, reviews, sync, works
from backend.seeder import seed_database
from backend.services.ingestion import run_ingestion

# Structured application logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jannidhi.main")

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager: ensures indexes, bootstraps seed data, and starts cron scheduler."""
    logger.info("Initializing database indexes and security bootstrapping...")
    ensure_indexes()
    seed_database()

    if settings.ENABLE_IN_PROCESS_SCHEDULER and not settings.IS_SERVERLESS:
        # Configurable scheduled live sync
        scheduler.add_job(
            run_ingestion,
            CronTrigger(
                hour=settings.SYNC_CRON_HOUR,
                minute=settings.SYNC_CRON_MINUTE,
                timezone=settings.SYNC_CRON_TIMEZONE
            ),
            kwargs={"mode": "live"},
            id="daily_mplads_sync",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            "APScheduler started: daily MPLADS sync at %02d:%02d %s.",
            settings.SYNC_CRON_HOUR,
            settings.SYNC_CRON_MINUTE,
            settings.SYNC_CRON_TIMEZONE
        )

    yield

    if settings.ENABLE_IN_PROCESS_SCHEDULER and not settings.IS_SERVERLESS:
        scheduler.shutdown()
        logger.info("APScheduler shut down.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="MoSPI (SIH26102) MPLADS AI Sentinel — Audit & Anomaly Prioritization Platform",
    lifespan=lifespan
)

# Middleware: Request ID and Performance Tracing
@app.middleware("http")
async def add_security_and_tracing_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
    return response

# Security Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Cron-Secret"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Mount Routers (both /api primary prefix and legacy root prefix without code duplication)
for router_module in (auth, works, reviews, directory, analytics, sync, health):
    app.include_router(router_module.router, prefix=settings.API_V1_STR)
    app.include_router(router_module.router)

# Production Single-Origin Static Frontend Mount (Docker / Hugging Face)
_frontend_dist = settings.DATA_DIR.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
