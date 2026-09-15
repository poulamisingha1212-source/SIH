"""
Ingestion & Data Synchronization Router.

Provides:
- Ingestion status banner endpoint (/sync/status)
- Historical ingestion run audit logs (/sync/logs)
- Manual background ingestion trigger (/sync/run) with distributed MongoDB lock
- Platform-cron inline ingestion trigger (/cron/sync) with credential verification
"""
import logging
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo import DESCENDING

from backend.auth import ROLE_MOSPI_REVIEWER, get_current_role
from backend.config import settings
from backend.database import get_db, sync_logs
from backend.schemas import SyncLogResponse
from backend.services.ingestion import VALID_MODES, get_sync_status

logger = logging.getLogger("jannidhi.sync")
router = APIRouter(tags=["Ingestion & Sync"])


def _cron_authorized(request: Request) -> bool:
    """Platform-cron authentication: Vercel Cron sends
    `Authorization: Bearer $CRON_SECRET` when a CRON_SECRET env var exists."""
    if not settings.CRON_SECRET:
        return False
    auth_header = request.headers.get("authorization", "")
    cron_header = request.headers.get("x-cron-secret", "")
    return (
        secrets.compare_digest(auth_header, f"Bearer {settings.CRON_SECRET}")
        or secrets.compare_digest(cron_header, settings.CRON_SECRET)
    )




def _start_background_sync(mode: str) -> dict:
    if mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ingestion mode '{mode}'. Must be one of {sorted(VALID_MODES)}"
        )

    # In decoupled architecture, the web API queries MongoDB and does NOT scrape MPLAD live
    status_data = get_sync_status()
    return {
        "status": "started",
        "mode": mode,
        "message": "MPLADS live data fetching is decoupled from the web API. "
                   "Data is collected independently by the scraper service. Current synchronization state returned from MongoDB.",
        "sync_status": status_data,
    }


@router.get("/sync/status")
def sync_status():
    """Returns current sync freshness, scraper checkpoint, and staleness indicators."""
    return get_sync_status()


@router.get("/sync/logs", response_model=list[SyncLogResponse])
def get_sync_logs(
    limit: int = 20,
    db=Depends(get_db)
):
    """Returns recent historical ingestion runs from sync_logs."""
    logs = sync_logs.find({}, {"_id": 0}).sort([("run_timestamp", DESCENDING)]).limit(limit)
    return [SyncLogResponse(**doc) for doc in logs]


@router.post("/sync/run")
def trigger_manual_sync(
    request: Request,
    mode: str = Query("auto", description="Ingestion mode: auto | live"),
    user_role: str = Depends(get_current_role)
):
    """
    Manually trigger the ingestion pipeline. Restricted to authenticated MoSPI Reviewers
    or platform crons with the CRON_SECRET credential.
    """
    is_cron = _cron_authorized(request)
    if not (is_cron or user_role == ROLE_MOSPI_REVIEWER):
        logger.warning("UNAUTHORIZED SYNC ATTEMPT: Role '%s' attempted to trigger manual sync.", user_role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Only MoSPI Reviewers can perform governance and sync operations."
        )
    return _start_background_sync(mode)


@router.get("/cron/sync")
def cron_sync(
    request: Request,
    mode: str = Query("auto", description="Ingestion mode: auto | live"),
):
    """
    Platform-cron entry point (e.g. Vercel Cron GET requests).
    Returns current sync status from MongoDB without triggering heavy in-process scraping.
    """
    if not _cron_authorized(request):
        logger.warning("CRON REJECTED: Invalid cron credentials.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: invalid cron credentials."
        )
    if mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ingestion mode '{mode}'. Must be one of {sorted(VALID_MODES)}"
        )

    status_data = get_sync_status()
    return {
        "status": "success",
        "mode": mode,
        "message": "Main backend is decoupled from MPLADS scraping. Current sync status retrieved from MongoDB.",
        "sync_status": status_data,
    }
