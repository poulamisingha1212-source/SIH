"""
Unit tests for Public Review Feedback Endpoint & Abuse Prevention.
"""
from fastapi.testclient import TestClient

from backend.database import works
from backend.main import app

client = TestClient(app)


def _get_first_work_id() -> str:
    item = works.find_one({}, {"work_id": 1})
    return item["work_id"] if item else "TEST-WORK-001"


def test_public_review_success():
    work_id = _get_first_work_id()
    valid_base64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    payload = {
        "is_completed": True,
        "comment": "Community center building is fully constructed and functional.",
        "photo_proof": valid_base64,
        "reporter_name": "Ramesh Kumar"
    }

    res = client.post(f"/api/works/{work_id}/public-review", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["work_id"] == work_id
    assert data["is_completed"] is True
    assert data["reporter_name"] == "Ramesh Kumar"


def test_public_review_rejects_external_urls():
    """SSRF Prevention: Remote HTTP/HTTPS image URLs must be rejected."""
    work_id = _get_first_work_id()
    payload = {
        "is_completed": False,
        "comment": "Work not started.",
        "photo_proof": "https://malicious-site.example/ssrf-test.jpg",
        "reporter_name": "Citizen"
    }
    res = client.post(f"/api/works/{work_id}/public-review", json=payload)
    assert res.status_code == 422


def test_public_review_rejects_oversized_photo():
    """Size Capping: Excessively large base64 payload must be rejected."""
    work_id = _get_first_work_id()
    huge_payload = "data:image/png;base64," + ("A" * 3_500_000)

    payload = {
        "is_completed": False,
        "comment": "Work delayed.",
        "photo_proof": huge_payload,
        "reporter_name": "Citizen"
    }
    res = client.post(f"/api/works/{work_id}/public-review", json=payload)
    assert res.status_code == 422


def test_public_review_sanitizes_xss_inputs():
    """XSS Prevention: Script tags in comments and reporter names must be stripped/escaped."""
    work_id = _get_first_work_id()
    payload = {
        "is_completed": True,
        "comment": "<script>alert('xss')</script>Ground reality looks good.",
        "photo_proof": None,
        "reporter_name": "<b>EvilHacker</b>"
    }
    res = client.post(f"/api/works/{work_id}/public-review", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "<script>" not in data["comment"]
    assert "<b>" not in data["reporter_name"]


def test_public_review_short_comment_rejected():
    work_id = _get_first_work_id()
    payload = {
        "is_completed": True,
        "comment": "hi",  # less than 3 chars
    }
    res = client.post(f"/api/works/{work_id}/public-review", json=payload)
    assert res.status_code == 422
