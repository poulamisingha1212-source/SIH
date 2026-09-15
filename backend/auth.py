"""
Authentication and Role-Based Access Control (RBAC) Layer.

Provides:
- Cryptographic password hashing (PBKDF2-HMAC-SHA256 with 100k iterations and CSPRNG salt)
- JWT access token generation and cryptographic verification (HS256)
- Fail-closed FastAPI dependencies enforcing authenticated roles via `Authorization: Bearer <token>`
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from typing import Any

from fastapi import Header, HTTPException, status

from backend.config import settings

ROLE_MOSPI_REVIEWER = "MoSPI Reviewer"
ROLE_DISTRICT_AUDITOR = "District Authority Auditor"
ROLE_PUBLIC_TIER = "Read-Only Public Tier"

VALID_ROLES = {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR, ROLE_PUBLIC_TIER}
PRIVILEGED_ROLES = {ROLE_MOSPI_REVIEWER, ROLE_DISTRICT_AUDITOR}
DEFAULT_ROLE = ROLE_PUBLIC_TIER


# ==============================================================================
# 1. Cryptographic Password Hashing (PBKDF2-HMAC-SHA256)
# ==============================================================================

def hash_password(password: str) -> str:
    """
    Hashes a password with PBKDF2-HMAC-SHA256 using 100,000 iterations
    and a 16-byte cryptographically secure random salt.
    Format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    """
    iterations = 100_000
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain-text password against a stored hashed password in constant time.
    """
    if not hashed_password or not isinstance(hashed_password, str):
        return False
    parts = hashed_password.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_dk = bytes.fromhex(parts[3])
        actual_dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(expected_dk, actual_dk)
    except Exception:
        return False


# ==============================================================================
# 2. JWT Generation & Verification (HS256)
# ==============================================================================

def _b64encode_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode_url(data_str: str) -> bytes:
    padding = 4 - (len(data_str) % 4)
    if padding != 4:
        data_str += "=" * padding
    return base64.urlsafe_b64decode(data_str.encode("ascii"))


def create_access_token(
    username: str,
    role: str,
    expires_delta: timedelta | None = None
) -> str:
    """
    Creates a signed JWT access token containing username, verified role,
    issued-at (iat) and expiration (exp).
    """
    now = int(time.time())
    if expires_delta:
        expire_seconds = int(expires_delta.total_seconds())
    else:
        expire_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": username,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + expire_seconds,
    }

    header_b64 = _b64encode_url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64encode_url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode()

    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256
    ).digest()
    sig_b64 = _b64encode_url(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Cryptographically verifies and decodes a JWT access token.
    Returns the payload dictionary if valid and non-expired; None otherwise.
    """
    if not token or not isinstance(token, str):
        return None

    parts = token.strip().split(".")
    if len(parts) != 3:
        return None

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256
    ).digest()

    try:
        actual_sig = _b64decode_url(sig_b64)
        if not secrets.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = _b64decode_url(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        # Check expiration
        now = int(time.time())
        if payload.get("exp") and int(payload["exp"]) < now:
            return None

        return payload
    except Exception:
        return None


# ==============================================================================
# 3. RBAC Dependencies
# ==============================================================================

def get_current_user_optional(
    authorization: str | None = Header(
        None,
        alias="Authorization",
        description="Bearer JWT access token"
    )
) -> dict[str, Any] | None:
    """
    Extracts and verifies user identity from Authorization header.
    Returns user payload or None if unauthenticated / invalid.
    """
    if not authorization:
        return None

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    token = parts[1]
    return decode_access_token(token)


def get_current_role(
    authorization: str | None = Header(None, alias="Authorization")
) -> str:
    """
    Returns the verified role from the decoded JWT token.
    Fail-closed: returns ROLE_PUBLIC_TIER if unauthenticated or token invalid.
    """
    user = get_current_user_optional(authorization)
    if not user:
        return DEFAULT_ROLE
    role = user.get("role", DEFAULT_ROLE)
    if role not in VALID_ROLES:
        return DEFAULT_ROLE
    return role


def require_authenticated_user(
    authorization: str | None = Header(None, alias="Authorization")
) -> dict[str, Any]:
    """
    Enforces that a valid, non-expired JWT token is provided.
    """
    user = get_current_user_optional(authorization)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_reviewer_role(
    authorization: str | None = Header(None, alias="Authorization")
) -> str:
    """
    Enforces that the requester is an authenticated District Auditor or MoSPI Reviewer.
    """
    user = require_authenticated_user(authorization)
    role = user.get("role")
    if role not in PRIVILEGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Read-Only Public Tier cannot record or alter human review outcomes."
        )
    return role


def require_mospi_admin_role(
    authorization: str | None = Header(None, alias="Authorization")
) -> str:
    """
    Enforces that the requester is an authenticated MoSPI Reviewer.
    """
    user = require_authenticated_user(authorization)
    role = user.get("role")
    if role != ROLE_MOSPI_REVIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Only MoSPI Reviewers can perform governance and sync operations."
        )
    return role
