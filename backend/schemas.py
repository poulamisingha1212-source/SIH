import html
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkListItem(BaseModel):
    work_id: str
    mp_name: str | None = None
    state: str | None = None
    constituency: str | None = None
    ida: str | None = None
    primary_vendor: str | None = None
    work_category: str | None = None
    work_type: str | None = None
    sanction_amount: float = 0.0
    total_fund_disbursed: float = 0.0
    utilization_ratio: float = 0.0
    work_status: str | None = None
    completion_date: str | None = None
    final_risk_score: float = 0.0
    priority_rank: int = 0
    risk_tier: str = "Low Risk"
    recommended_action: str | None = None
    rule_flag_count: int = 0
    rule_flags_triggered: list[str] = []
    causes: list[str] = []
    human_review_outcome: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkPaginationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[WorkListItem]


class CasePacketResponse(BaseModel):
    work_id: str
    mp_name: str | None = None
    state: str | None = None
    constituency: str | None = None
    ida: str | None = None
    primary_vendor: str | None = None
    work_category: str | None = None
    work_type: str | None = None
    sanction_amount: float = 0.0
    total_fund_disbursed: float = 0.0
    utilization_ratio: float = 0.0
    work_status: str | None = None
    completion_date: str | None = None
    final_risk_score: float = 0.0
    priority_rank: int = 0
    risk_tier: str = "Low Risk"
    recommended_action: str | None = None
    rule_flag_count: int = 0
    rule_flags_triggered: list[str] = []
    causes: list[str] = []
    impact_note: str | None = None
    likelihood_score: float = 0.0
    impact_score: float = 0.0
    weighted_rule_score: float = 0.0
    anomaly_percentile: float = 0.0
    is_anomaly: bool = False
    cost_mad_score: float | None = 0.0
    vendor_share_in_state: float | None = 0.0
    disbursement_mismatch_ratio: float | None = 0.0
    days_since_sanction: float | None = 0.0
    n_distinct_vendors: float | None = 0.0
    n_vendor_payments: float | None = 0.0
    human_review_outcome: str | None = None
    prior_reviews: list[dict] = []
    public_reviews: list[dict] = []


class PublicReviewCreateRequest(BaseModel):
    is_completed: bool = Field(..., description="True if work is completed, False if not done")
    comment: str | None = Field(None, max_length=1000, description="Citizen feedback comment (max 1000 chars)")
    photo_proof: str | None = Field(None, description="Base64 encoded image (data:image/...;base64, max ~2 MB)")
    reporter_name: str | None = Field("Anonymous Citizen", max_length=100, description="Citizen reporter name")

    @field_validator('reporter_name')
    @classmethod
    def sanitize_reporter_name(cls, v: str | None) -> str:
        if not v or not v.strip():
            return "Anonymous Citizen"
        clean = html.escape(re.sub(r'<[^>]+>', '', v.strip()))
        return clean[:100] if clean else "Anonymous Citizen"

    @field_validator('comment')
    @classmethod
    def sanitize_comment(cls, v: str | None) -> str | None:
        if not v or not v.strip():
            return None
        stripped = v.strip()
        if len(stripped) < 3:
            raise ValueError("Comment must be at least 3 characters long if provided.")
        # Strip HTML tags & escape entities to prevent XSS / stored injection
        clean = html.escape(re.sub(r'<[^>]+>', '', stripped))
        return clean[:1000]

    @field_validator('photo_proof')
    @classmethod
    def validate_photo_proof(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        # Cap raw string length to ~3MB (~2MB binary base64)
        if len(v) > 3_000_000:
            raise ValueError("Photo proof exceeds maximum allowed payload size (2 MB image limit).")
        # Validate MIME prefix for base64 images; disallow arbitrary external HTTP/HTTPS URLs to prevent SSRF
        if v.startswith("http://") or v.startswith("https://"):
            raise ValueError("Direct external image URLs are not permitted. Please upload image proof as base64 data.")
        if not v.startswith("data:image/"):
            raise ValueError("Invalid image format. Expected base64 data URI starting with 'data:image/'.")
        return v


class PublicReviewResponse(BaseModel):
    success: bool
    work_id: str
    is_completed: bool
    comment: str | None = None
    photo_proof: str | None = None
    reporter_name: str
    created_at: datetime


class ReviewCreateRequest(BaseModel):
    outcome: str = Field(
        ...,
        description="One of: legitimate, data-quality issue, irregularity, confirmed fraud"
    )
    notes: str | None = Field(None, max_length=2000, description="Auditor review notes")
    reviewer_name: str | None = None
    reviewer_role: str | None = None


class ReviewResponse(BaseModel):
    success: bool
    work_id: str
    outcome: str
    reviewer_name: str
    reviewer_role: str
    notes: str | None = None
    created_at: datetime


class EntityRiskStat(BaseModel):
    name: str
    count: int
    avg_risk_score: float
    high_risk_count: int
    total_sanctioned: float


class StatsOverviewResponse(BaseModel):
    total_works: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int = 0
    tier_distribution: dict
    total_sanctioned_amount: float
    total_disbursed_amount: float
    total_allocated_amount: float = 0.0
    fund_utilization_pct: float = 0.0      # sanctioned vs allocated (MoSPI definition)
    expenditure_rate_pct: float = 0.0      # disbursed vs allocated
    works_completed: int = 0
    works_pending: int = 0
    ongoing_work_payments: float = 0.0     # vendor payments on not-yet-completed works
    avg_utilization_pct: float = 0.0
    avg_risk_score: float = 0.0
    reviewed_works: int = 0
    top_risk_mps: list[EntityRiskStat]
    top_risk_states: list[EntityRiskStat]
    top_risk_vendors: list[EntityRiskStat]
    latest_sync_timestamp: str | None = None
    latest_sync_status: str | None = "success"
    is_data_stale: bool = False
    staleness_message: str = "Data is fresh and synchronized."


class MPDirectoryItem(BaseModel):
    rank: int = 0
    mp_name: str | None = None
    constituency: str | None = None
    state: str | None = None
    mp_count: int = 0
    works_count: int = 0
    total_allocated: float = 0.0
    total_sanctioned: float = 0.0
    total_disbursed: float = 0.0
    allocated_amount: float = 0.0
    avg_utilization: float = 0.0
    avg_risk_score: float = 0.0
    max_risk_score: float = 0.0
    high_risk_count: int = 0
    medium_risk_count: int = 0
    reviewed_count: int = 0


class EntityDirectoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[MPDirectoryItem]


class BreakdownStat(BaseModel):
    name: str
    count: int
    total_sanctioned: float
    total_disbursed: float = 0.0
    avg_risk_score: float
    high_risk_count: int


class StatusStat(BaseModel):
    name: str
    count: int
    share: float
    total_sanctioned: float
    avg_risk_score: float


class CategoryStat(BreakdownStat):
    sanctioned_share: float = 0.0


class MPProfileResponse(BaseModel):
    mp_name: str
    constituency: str | None = None
    state: str | None = None
    works_count: int
    total_allocated: float = 0.0
    allocated_amount: float = 0.0
    total_sanctioned: float
    sanction_amount: float = 0.0
    total_disbursed: float
    fund_disbursed_amount: float = 0.0
    avg_utilization: float
    avg_risk_score: float
    max_risk_score: float
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    reviewed_count: int
    vendor_count: int = 0
    tier_distribution: dict
    category_breakdown: list[BreakdownStat] = []
    status_breakdown: list[BreakdownStat] = []
    agency_breakdown: list[BreakdownStat] = []
    top_vendors: list[BreakdownStat] = []
    top_risk_works: list[WorkListItem] = []
    recent_reviews: list[dict] = []


class StateProfileResponse(BaseModel):
    state: str
    works_count: int
    mp_count: int
    total_sanctioned: float
    total_disbursed: float
    avg_utilization: float
    avg_risk_score: float
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int = 0
    reviewed_count: int
    tier_distribution: dict
    top_mps: list[MPDirectoryItem] = []
    category_breakdown: list[BreakdownStat] = []
    agency_breakdown: list[BreakdownStat] = []


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    works_count: int
    timestamp: str


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100, description="Username")
    password: str = Field(..., min_length=1, max_length=200, description="Password")


class LoginResponse(BaseModel):
    success: bool
    username: str
    role: str
    access_token: str
    token_type: str = "bearer"
    message: str


class SyncLogResponse(BaseModel):
    id: int
    run_timestamp: datetime
    start_time: datetime
    end_time: datetime
    status: str
    source: str
    rows_fetched: int
    rows_processed: int
    rows_inserted: int
    rows_updated: int
    rows_rejected: int
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)
