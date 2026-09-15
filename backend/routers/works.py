"""
Works & Audit Case Packet Router.

Provides:
- Paginated and filtered Priority Queue of works (/works)
- Comprehensive evidence case packet generation (/works/{work_id})
- Open-data CSV export stream (/export/works)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pymongo import ASCENDING, DESCENDING

from backend.auth import ROLE_PUBLIC_TIER, get_current_role
from backend.database import get_db, public_reviews, review_logs, works
from backend.schemas import CasePacketResponse, WorkListItem, WorkPaginationResponse
from backend.services import analytics
from model.risk_engine import RULE_DESCRIPTIONS, generate_case_packet

router = APIRouter(tags=["Works & Audit Dossiers"])


@router.get("/works", response_model=WorkPaginationResponse)
def get_works(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    state: str | None = Query(None, description="Filter by State"),
    mp_name: str | None = Query(None, description="Filter by MP Name"),
    house: str | None = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    ida: str | None = Query(None, description="Filter by Implementing Agency"),
    risk_tier: str | None = Query(None, description="Filter by Risk Tier"),
    work_category: str | None = Query(None, description="Filter by Work Category"),
    work_status: str | None = Query(None, description="Filter by Execution Status"),
    search: str | None = Query(None, description="Search by Work ID, vendor or description"),
    sort_by: str = Query("priority_rank", description="Sort field"),
    order: str = Query("asc", description="Sort direction: 'asc' or 'desc'"),
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Returns a paginated list of works ordered by priority_rank (default ascending = highest priority first).
    Supports database-level filtering and sorting over the works catalog.
    """
    if sort_by not in analytics.WORK_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.WORK_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")

    filt = analytics.apply_work_filters(
        state=state, mp_name=mp_name, house=house, ida=ida, risk_tier=risk_tier,
        work_category=work_category, work_status=work_status, search=search,
    )

    total = works.count_documents(filt)

    direction = DESCENDING if order.lower() == "desc" else ASCENDING
    cursor = works.find(filt).sort([(sort_by, direction), ("work_id", ASCENDING)])

    offset = (page - 1) * page_size
    items_raw = cursor.skip(offset).limit(page_size)

    items = []
    for w in items_raw:
        item = analytics.work_to_list_item(w)
        flags = item["rule_flags_triggered"]
        causes = [RULE_DESCRIPTIONS.get(f, f"Flag triggered: {f}") for f in flags]
        item["causes"] = causes
        items.append(WorkListItem(**item))

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return WorkPaginationResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items
    )


@router.get("/export/works")
def export_works_csv(
    state: str | None = Query(None),
    mp_name: str | None = Query(None),
    house: str | None = Query(None),
    ida: str | None = Query(None),
    risk_tier: str | None = Query(None),
    work_category: str | None = Query(None),
    work_status: str | None = Query(None),
    search: str | None = Query(None),
    row_limit: int = Query(50000, ge=1, le=100000),
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Streams the filtered works list as a downloadable CSV (up to row_limit rows).
    Open-data companion to the /works endpoint — same filters, machine-readable.
    """
    filt = analytics.apply_work_filters(
        state=state, mp_name=mp_name, house=house, ida=ida, risk_tier=risk_tier,
        work_category=work_category, work_status=work_status, search=search,
    )
    filename = f"mplads_works_export_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return StreamingResponse(
        analytics.stream_works_csv(filt, row_limit=row_limit),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Limit": str(row_limit),
        }
    )


@router.get("/works/{work_id:path}", response_model=CasePacketResponse)
def get_work_case_packet(
    work_id: str,
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """
    Returns the complete audit case packet for the requested Work ID using multi-agent risk engine.
    """
    work = works.find_one({"work_id": work_id.strip()}, {"_id": 0})
    if not work:
        raise HTTPException(status_code=404, detail=f"Work ID '{work_id}' not found.")

    work_dict = {k: v for k, v in work.items() if not k.startswith("_")}
    packet = generate_case_packet(work_id, work_row=work_dict)

    # Fetch prior reviews for this work
    prior_reviews = review_logs.find({"work_id": work_id}).sort([("created_at", DESCENDING)])
    packet['prior_reviews'] = [
        {
            'id': r.get("id"),
            'reviewer_name': r.get("reviewer_name"),
            'reviewer_role': r.get("reviewer_role"),
            'outcome': r.get("outcome"),
            'notes': r.get("notes") if user_role != ROLE_PUBLIC_TIER else None,
            'created_at': r["created_at"].isoformat() if r.get("created_at") else None
        }
        for r in prior_reviews
    ]

    # Fetch public feedback reviews
    pub_reviews = public_reviews.find({"work_id": work_id}).sort([("created_at", DESCENDING)])
    packet['public_reviews'] = [
        {
            'id': pr.get("id"),
            'is_completed': pr.get("is_completed", False),
            'comment': pr.get("comment"),
            'photo_proof': pr.get("photo_proof"),
            'reporter_name': pr.get("reporter_name", "Anonymous Citizen"),
            'created_at': pr["created_at"].isoformat() if pr.get("created_at") else None
        }
        for pr in pub_reviews
    ]

    return CasePacketResponse(**packet)
