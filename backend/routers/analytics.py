"""
Portfolio Analytics & Statistics Router.

Provides:
- Macro portfolio-level statistics (/stats/overview)
- Categorical and status aggregation charts (/analytics/categories, /analytics/status)
- Filter options catalog (/filter-options)
"""

from fastapi import APIRouter, Depends, Query

from backend.database import get_db, mp_allocations, works
from backend.schemas import (
    CategoryStat,
    EntityRiskStat,
    StatsOverviewResponse,
    StatusStat,
)
from backend.services import analytics
from backend.services.ingestion import get_sync_status

router = APIRouter(tags=["Portfolio Analytics"])


@router.get("/stats/overview", response_model=StatsOverviewResponse)
def get_stats_overview(
    house: str | None = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    db=Depends(get_db)
):
    """
    Returns portfolio-level statistics including risk tier counts, top-risk MPs,
    top-risk states, top-risk vendors, and sync health / staleness status.
    """
    def scoped(filt: dict) -> dict:
        return analytics.apply_house(filt, house) if house else filt

    total_works = works.count_documents(scoped({}))
    high_risk_count = works.count_documents(scoped({"risk_tier": 'High Risk - Review'}))
    medium_risk_count = works.count_documents(scoped({"risk_tier": 'Medium Risk - Monitor'}))
    low_risk_count = works.count_documents(scoped({"risk_tier": 'Low Risk'}))

    def _scalar(stage_op: str, field: str, extra_match: dict | None = None) -> float:
        match = scoped(extra_match or {})
        rows = works.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "v": {stage_op: {"$ifNull": [f"${field}", 0.0]}}}},
        ])
        row = next(rows, None)
        return float(row["v"]) if row and row["v"] is not None else 0.0

    total_sanctioned = _scalar("$sum", "sanction_amount")
    total_disbursed = _scalar("$sum", "total_fund_disbursed")
    avg_utilization = _scalar("$avg", "utilization_ratio")
    avg_risk = _scalar("$avg", "final_risk_score")
    reviewed_count = works.count_documents(scoped({"human_review_outcome": {"$ne": None}}))

    # Portal allocation ledger
    alloc_match: dict = {}
    if house:
        alloc_match["house"] = house.strip()
    alloc_rows = mp_allocations.aggregate([
        {"$match": alloc_match},
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$allocated_amount", 0.0]}}}},
    ])
    alloc_row = next(alloc_rows, None)
    total_allocated = float(alloc_row["total"]) if alloc_row and alloc_row["total"] is not None else 0.0

    works_completed = works.count_documents(
        scoped({"completion_date": {"$nin": [None, ""]}})
    )
    works_pending = max(0, total_works - works_completed)

    ongoing_payments = _scalar("$sum", "total_fund_disbursed", extra_match={
        "$or": [{"completion_date": None}, {"completion_date": ""}],
        "total_fund_disbursed": {"$gt": 0},
    })

    tier_dist = {
        'High Risk - Review': high_risk_count,
        'Medium Risk - Monitor': medium_risk_count,
        'Low Risk': low_risk_count,
    }

    top_states = [EntityRiskStat(**s) for s in analytics.top_entity_stats("state", house)]
    top_mps = [EntityRiskStat(**m) for m in analytics.top_entity_stats("mp_name", house)]
    top_vendors = [EntityRiskStat(**v) for v in analytics.top_entity_stats("primary_vendor", house)]

    sync_info = get_sync_status()

    return StatsOverviewResponse(
        total_works=total_works,
        high_risk_count=high_risk_count,
        medium_risk_count=medium_risk_count,
        low_risk_count=low_risk_count,
        tier_distribution=tier_dist,
        total_sanctioned_amount=round(float(total_sanctioned), 2),
        total_disbursed_amount=round(float(total_disbursed), 2),
        total_allocated_amount=round(float(total_allocated), 2),
        fund_utilization_pct=round(float(total_sanctioned) / float(total_allocated) * 100, 1) if total_allocated else 0.0,
        expenditure_rate_pct=round(float(total_disbursed) / float(total_allocated) * 100, 1) if total_allocated else 0.0,
        works_completed=int(works_completed),
        works_pending=int(works_pending),
        ongoing_work_payments=round(float(ongoing_payments), 2),
        avg_utilization_pct=round(float(avg_utilization) * 100, 1),
        avg_risk_score=round(float(avg_risk), 1),
        reviewed_works=int(reviewed_count),
        top_risk_mps=top_mps,
        top_risk_states=top_states,
        top_risk_vendors=top_vendors,
        latest_sync_timestamp=sync_info["latest_sync_timestamp"],
        latest_sync_status=sync_info["latest_sync_status"],
        is_data_stale=sync_info["is_data_stale"],
        staleness_message=sync_info["staleness_message"]
    )


@router.get("/analytics/categories", response_model=list[CategoryStat])
def get_category_analytics(
    house: str | None = Query(None, description="Filter by House"),
    db=Depends(get_db)
):
    """Fund share, disbursed value and risk per work category."""
    return analytics.get_category_analytics(db, house=house)


@router.get("/analytics/status", response_model=list[StatusStat])
def get_status_analytics(
    house: str | None = Query(None, description="Filter by House"),
    db=Depends(get_db)
):
    """Execution status distribution with average risk per status."""
    return analytics.get_status_analytics(db, house=house)


@router.get("/filter-options")
def get_filter_options(
    house: str | None = Query(None, description="Filter options by House"),
    db=Depends(get_db)
):
    """Returns unique filter values for the frontend dropdowns."""
    house_filter = {"house": house.strip()} if house else {}

    def _sorted_distinct(field: str) -> list:
        values = works.distinct(field, house_filter)
        return sorted(v for v in values if v)

    return {
        "states": _sorted_distinct("state"),
        "categories": _sorted_distinct("work_category"),
        "statuses": _sorted_distinct("work_status"),
        "mps": _sorted_distinct("mp_name"),
        "risk_tiers": ["High Risk - Review", "Medium Risk - Monitor", "Low Risk"]
    }
