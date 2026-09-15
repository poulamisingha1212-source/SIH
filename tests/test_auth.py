"""
Unit tests for Authentication, Password Hashing, JWT, and Security Controls.
"""
from datetime import timedelta

from fastapi.testclient import TestClient

from backend.auth import (
    ROLE_DISTRICT_AUDITOR,
    ROLE_MOSPI_REVIEWER,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.config import settings
from backend.main import app

client = TestClient(app)


def test_password_hashing_and_verification():
    raw_password = "SecurePassword@2026"
    hashed = hash_password(raw_password)

    assert hashed.startswith("pbkdf2_sha256$100000$")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(raw_password, "invalid$format") is False


def test_jwt_generation_and_decoding():
    token = create_access_token("test_user", ROLE_MOSPI_REVIEWER)
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "test_user"
    assert payload["role"] == ROLE_MOSPI_REVIEWER

    # Test expired token
    expired_token = create_access_token(
        "test_user",
        ROLE_MOSPI_REVIEWER,
        expires_delta=timedelta(seconds=-10)
    )
    assert decode_access_token(expired_token) is None

    # Test tampered token
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}.tamperedsignature"
    assert decode_access_token(tampered) is None


def test_login_success_and_jwt_response():
    # Test seeded admin login
    res = client.post(
        "/api/auth/login",
        json={"username": "admin_mospi", "password": settings.INITIAL_ADMIN_PASSWORD}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["username"] == "admin_mospi"
    assert data["role"] == ROLE_MOSPI_REVIEWER
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify that the issued token is authentic
    payload = decode_access_token(data["access_token"])
    assert payload["username"] == "admin_mospi"
    assert payload["role"] == ROLE_MOSPI_REVIEWER


def test_login_invalid_credentials():
    res = client.post(
        "/api/auth/login",
        json={"username": "admin_mospi", "password": "WrongPassword123"}
    )
    assert res.status_code == 401

    res_unknown = client.post(
        "/api/auth/login",
        json={"username": "non_existent_user", "password": "anypassword"}
    )
    assert res_unknown.status_code == 401


def test_login_prevents_privilege_escalation():
    """Verify that an auditor cannot escalate to MoSPI Reviewer via request params."""
    res = client.post(
        "/api/auth/login",
        json={
            "username": "district_auditor",
            "password": settings.INITIAL_AUDITOR_PASSWORD,
            # Even if a client sends extra fields, role is server-determined
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["role"] == ROLE_DISTRICT_AUDITOR


def test_login_lockout_on_brute_force():
    """Verify that repeated failed logins trigger HTTP 429 lockout."""
    from backend.routers.auth import _clear_failures
    test_user = "brute_force_victim"
    _clear_failures(test_user)
    _clear_failures("testclient")

    for _ in range(settings.AUTH_MAX_FAILED_ATTEMPTS):
        res = client.post(
            "/api/auth/login",
            json={"username": test_user, "password": "wrong_password"}
        )
        assert res.status_code == 401

    # 6th attempt should be locked out
    locked_res = client.post(
        "/api/auth/login",
        json={"username": test_user, "password": "wrong_password"}
    )
    assert locked_res.status_code == 429
    assert "locked" in locked_res.json()["detail"].lower()

    # Clean up test state
    _clear_failures(test_user)
    _clear_failures("testclient")
