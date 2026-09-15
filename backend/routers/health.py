"""
Health Check & Liveness Probe Router.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pymongo.errors import PyMongoError

from backend.config import settings
from backend.database import get_db, works
from backend.schemas import HealthResponse

router = APIRouter(tags=["Health & Liveness"])


@router.get("/health", response_model=HealthResponse)
def health_check(db=Depends(get_db)):
    """Liveness probe: verifies API + database connectivity."""
    try:
        works_count = works.count_documents({})
        db_status = "connected"
    except PyMongoError:
        works_count = 0
        db_status = "unavailable"
    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        version=settings.VERSION,
        database=db_status,
        works_count=int(works_count),
        timestamp=datetime.now(timezone.utc).isoformat()
    )
