"""
Human & Public Review Feedback Router.

Provides:
- Phase 5 Human Review outcome recording (restricted to authenticated Auditors / MoSPI Reviewers)
- Public Tier citizen verification submission with payload validation, XSS sanitization, and size capping
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.auth import require_reviewer_role
from backend.database import get_db, next_id, public_reviews, review_logs, works
from backend.schemas import (
    PublicReviewCreateRequest,
    PublicReviewResponse,
    ReviewCreateRequest,
    ReviewResponse,
)

logger = logging.getLogger("jannidhi.reviews")
router = APIRouter(tags=["Reviews & Feedback"])

# Simple IP-based rate limiter for public review submissions
_public_review_submissions: dict[str, list[datetime]] = {}


def _check_public_rate_limit(client_ip: str, max_per_minute: int = 10) -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timezone.utc.utcoffset(None) if False else now - datetime.resolution * 60  # 60s
    from datetime import timedelta
    cutoff = now - timedelta(seconds=60)

    recent = [ts for ts in _public_review_submissions.get(client_ip, []) if ts > cutoff]
    _public_review_submissions[client_ip] = recent

    if len(recent) >= max_per_minute:
        logger.warning("PUBLIC REVIEW RATE LIMIT EXCEEDED: IP %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification submissions. Please wait a minute before submitting again."
        )
    _public_review_submissions[client_ip].append(now)


@router.post("/works/{work_id:path}/review", response_model=ReviewResponse)
def record_human_review(
    work_id: str,
    payload: ReviewCreateRequest,
    db=Depends(get_db),
    user_role: str = Depends(require_reviewer_role)
):
    """
    Phase 5 feedback loop hook: Record a formal human audit review outcome.
    Restricted to authenticated District Auditors and MoSPI Reviewers with valid JWT Bearer tokens.
    """
    valid_outcomes = {'legitimate', 'data-quality issue', 'irregularity', 'confirmed fraud'}
    norm_outcome = payload.outcome.strip().lower()
    if norm_outcome not in valid_outcomes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid review outcome '{payload.outcome}'. Must be one of: {list(valid_outcomes)}"
        )

    work_id = work_id.strip()
    if not works.find_one({"work_id": work_id}):
        raise HTTPException(status_code=404, detail=f"Work ID '{work_id}' not found.")

    reviewer_name = payload.reviewer_name or user_role
    reviewer_role = payload.reviewer_role or user_role
    now = datetime.now(timezone.utc)

    review_log = {
        "id": next_id("review_logs"),
        "work_id": work_id,
        "reviewer_name": reviewer_name,
        "reviewer_role": reviewer_role,
        "outcome": norm_outcome,
        "notes": payload.notes,
        "created_at": now,
    }
    review_logs.insert_one(review_log)

    works.update_one(
        {"work_id": work_id},
        {"$set": {"human_review_outcome": norm_outcome, "updated_at": now}}
    )

    logger.info("AUDIT REVIEW RECORDED: Work ID '%s' marked '%s' by '%s' (%s)", work_id, norm_outcome, reviewer_name, reviewer_role)

    return ReviewResponse(
        success=True,
        work_id=work_id,
        outcome=norm_outcome,
        reviewer_name=reviewer_name,
        reviewer_role=reviewer_role,
        notes=payload.notes,
        created_at=now
    )


@router.post("/works/{work_id:path}/public-review", response_model=PublicReviewResponse)
def record_public_review(
    work_id: str,
    payload: PublicReviewCreateRequest,
    request: Request,
    db=Depends(get_db)
):
    """
    Public Tier feedback endpoint: Allows citizens to report completion status,
    ground observations, and photo proof.

    Security Controls:
    - Base64 payload capped at ~2 MB binary
    - Arbitrary remote URLs rejected (SSRF protection)
    - HTML and script tags sanitized against XSS
    - IP rate-limited against automated spam
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_public_rate_limit(client_ip)

    work_id = work_id.strip()
    if not works.find_one({"work_id": work_id}):
        raise HTTPException(status_code=404, detail=f"Work ID '{work_id}' not found.")

    now = datetime.now(timezone.utc)

    doc = {
        "id": next_id("public_reviews"),
        "work_id": work_id,
        "is_completed": bool(payload.is_completed),
        "comment": payload.comment,
        "photo_proof": payload.photo_proof,
        "reporter_name": payload.reporter_name or "Anonymous Citizen",
        "created_at": now
    }
    public_reviews.insert_one(doc)

    logger.info("PUBLIC REVIEW RECORDED: Work ID '%s' citizen report submitted by '%s'", work_id, doc["reporter_name"])

    return PublicReviewResponse(
        success=True,
        work_id=work_id,
        is_completed=doc["is_completed"],
        comment=doc["comment"],
        photo_proof=doc["photo_proof"],
        reporter_name=doc["reporter_name"],
        created_at=now
    )
