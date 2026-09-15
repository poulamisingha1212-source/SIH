"""
Unit tests for Role-Based Access Control (RBAC) across endpoints.
"""
from fastapi.testclient import TestClient

from backend.auth import (
    ROLE_DISTRICT_AUDITOR,
    ROLE_MOSPI_REVIEWER,
    ROLE_PUBLIC_TIER,
    create_access_token,
)
from backend.config import settings
from backend.database import works
from backend.main import app

client = TestClient(app)


def _get_first_work_id() -> str:
    item = works.find_one({}, {"work_id": 1})
    return item["work_id"] if item else "TEST-WORK-001"


def test_rbac_review_requires_authentication():
    work_id = _get_first_work_id()
    payload = {"outcome": "irregularity", "notes": "Test review"}

    # 1. Unauthenticated -> 401
    res_no_auth = client.post(f"/api/works/{work_id}/review", json=payload)
    assert res_no_auth.status_code == 401

    # 2. Public Tier token -> 403
    pub_token = create_access_token("citizen", ROLE_PUBLIC_TIER)
    res_pub = client.post(
        f"/api/works/{work_id}/review",
        json=payload,
        headers={"Authorization": f"Bearer {pub_token}"}
    )
    assert res_pub.status_code == 403

    # 3. District Auditor token -> 200
    auditor_token = create_access_token("auditor_user", ROLE_DISTRICT_AUDITOR)
    res_auditor = client.post(
        f"/api/works/{work_id}/review",
        json=payload,
        headers={"Authorization": f"Bearer {auditor_token}"}
    )
    assert res_auditor.status_code == 200
    assert res_auditor.json()["success"] is True

    # 4. MoSPI Reviewer token -> 200
    mospi_token = create_access_token("mospi_user", ROLE_MOSPI_REVIEWER)
    res_mospi = client.post(
        f"/api/works/{work_id}/review",
        json=payload,
        headers={"Authorization": f"Bearer {mospi_token}"}
    )
    assert res_mospi.status_code == 200


def test_rbac_sync_run_permissions():
    # 1. Unauthenticated -> 403
    res_no_auth = client.post("/api/sync/run?mode=auto")
    assert res_no_auth.status_code == 403

    # 2. District Auditor token -> 403 (Auditors cannot trigger sync)
    auditor_token = create_access_token("auditor_user", ROLE_DISTRICT_AUDITOR)
    res_auditor = client.post(
        "/api/sync/run?mode=auto",
        headers={"Authorization": f"Bearer {auditor_token}"}
    )
    assert res_auditor.status_code == 403

    # 3. MoSPI Reviewer token -> 200
    mospi_token = create_access_token("mospi_user", ROLE_MOSPI_REVIEWER)
    res_mospi = client.post(
        "/api/sync/run?mode=auto",
        headers={"Authorization": f"Bearer {mospi_token}"}
    )
    assert res_mospi.status_code in {200, 409}  # 200 or 409 if sync already running


def test_rbac_cron_sync_authentication():
    settings.CRON_SECRET = "test_cron_secret_12345"

    # 1. Invalid secret -> 403
    res_invalid = client.get(
        "/api/cron/sync",
        headers={"Authorization": "Bearer wrong_secret"}
    )
    assert res_invalid.status_code == 403

    # 2. Valid secret via Authorization Bearer header -> 200 or 409
    res_valid_auth = client.get(
        "/api/cron/sync?mode=auto",
        headers={"Authorization": f"Bearer {settings.CRON_SECRET}"}
    )
    assert res_valid_auth.status_code in {200, 409}

    # 3. Valid secret via X-Cron-Secret header -> 200 or 409
    res_valid_header = client.get(
        "/api/cron/sync?mode=auto",
        headers={"X-Cron-Secret": settings.CRON_SECRET}
    )
    assert res_valid_header.status_code in {200, 409}
