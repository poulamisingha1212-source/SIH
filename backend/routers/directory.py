"""
MP & State Transparency Directory Router.

Provides public aggregation and dossier views for MPs and States across Lok Sabha and Rajya Sabha.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import get_current_role
from backend.database import get_db
from backend.schemas import (
    EntityDirectoryResponse,
    MPProfileResponse,
    StateProfileResponse,
)
from backend.services import analytics

router = APIRouter(tags=["Directories & Profiles"])


@router.get("/mps", response_model=EntityDirectoryResponse)
def get_mp_directory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = Query(None, description="Filter MPs by State"),
    house: str | None = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    search: str | None = Query(None, description="Search by MP or constituency name"),
    sort_by: str = Query("total_sanctioned", description="Aggregate sort key"),
    order: str = Query("desc"),
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """MP-wise directory: sanctioned/disbursed totals, statutory allocation, utilization, risk profile."""
    if sort_by not in analytics.DIRECTORY_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.DIRECTORY_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")
    return analytics.get_mp_directory(
        db, page=page, page_size=page_size, search=search, state=state,
        house=house, sort_by=sort_by, order=order,
    )


@router.get("/mps/{mp_name}", response_model=MPProfileResponse)
def get_mp_profile(
    mp_name: str,
    house: str | None = Query(None, description="Filter by House: 'Lok Sabha' or 'Rajya Sabha'"),
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """Full public dossier for one MP: funds, risk tiers, categories, vendors, top works."""
    profile = analytics.get_mp_profile(db, mp_name, house=house)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No works found for MP '{mp_name}'.")
    return profile


@router.get("/states", response_model=EntityDirectoryResponse)
def get_state_directory(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    house: str | None = Query(None, description="Filter by House"),
    sort_by: str = Query("total_sanctioned"),
    order: str = Query("desc"),
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """State-wise directory: funds, MP coverage and risk concentration."""
    if sort_by not in analytics.DIRECTORY_SORTABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort field '{sort_by}'. Allowed: {sorted(analytics.DIRECTORY_SORTABLE_FIELDS)}"
        )
    if order.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'.")
    return analytics.get_state_directory(db, page=page, page_size=page_size, house=house, sort_by=sort_by, order=order)


@router.get("/states/{state}", response_model=StateProfileResponse)
def get_state_profile(
    state: str,
    db=Depends(get_db),
    user_role: str = Depends(get_current_role)
):
    """State dossier: tier spread, top MPs, agencies and category splits."""
    profile = analytics.get_state_profile(db, state)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No works found for state '{state}'.")
    return profile
