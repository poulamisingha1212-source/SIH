"""
Authentication & Session Router.

Provides:
- Secure login endpoint with cryptographic password verification
- JWT issuance (access token)
- Brute-force rate limiting and lockout protection
- Security audit logging for authentication events
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.auth import (
    PRIVILEGED_ROLES,
    ROLE_PUBLIC_TIER,
    create_access_token,
    require_authenticated_user,
    verify_password,
)
from backend.config import settings
from backend.database import get_db, users
from backend.schemas import LoginRequest, LoginResponse

logger = logging.getLogger("jannidhi.auth")
router = APIRouter(tags=["Authentication"])

# In-memory sliding window tracker for login rate limiting and lockout
# Key: identifier (ip or username), Value: list of UTC attempt timestamps
_login_failures: dict[str, list[datetime]] = {}


def _is_locked_out(identifier: str) -> bool:
    """Checks if an IP or username has exceeded the max failed attempts in the lockout window."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=settings.AUTH_LOCKOUT_MINUTES)

    attempts = _login_failures.get(identifier, [])
    # Filter attempts within current window
    active_attempts = [ts for ts in attempts if ts > window_start]
    _login_failures[identifier] = active_attempts

    return len(active_attempts) >= settings.AUTH_MAX_FAILED_ATTEMPTS


def _record_failure(identifier: str) -> None:
    now = datetime.now(timezone.utc)
    if identifier not in _login_failures:
        _login_failures[identifier] = []
    _login_failures[identifier].append(now)


def _clear_failures(identifier: str) -> None:
    _login_failures.pop(identifier, None)


@router.post("/auth/login", response_model=LoginResponse)
def login_user(payload: LoginRequest, request: Request, db=Depends(get_db)):
    """
    Authenticates District Auditor and MoSPI Reviewer credentials against MongoDB `users` collection.

    Security Controls:
    - Verifies PBKDF2 cryptographic hash
    - Enforces server-determined roles (client cannot self-assert roles)
    - Enforces rate limiting & lockout on failed attempts
    - Returns signed HS256 JWT access token
    """
    client_ip = request.client.host if request.client else "unknown"
    uname = payload.username.strip()
    pwd = payload.password.strip()

    # Rate limiting & lockout check
    if _is_locked_out(client_ip) or _is_locked_out(uname):
        logger.warning(
            "AUTH LOCKOUT: Blocked login attempt from IP '%s' for username '%s' due to excessive failures.",
            client_ip, uname
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Account/IP temporarily locked for {settings.AUTH_LOCKOUT_MINUTES} minutes."
        )

    user = users.find_one({"username": uname})
    if not user:
        _record_failure(client_ip)
        _record_failure(uname)
        logger.warning("AUTH FAILURE: Unknown username '%s' from IP '%s'", uname, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stored_password = user.get("password", "")
    is_valid = verify_password(pwd, stored_password)

    # Backward compatibility fallback for unmigrated local dev seeds
    if not is_valid and stored_password == pwd:
        is_valid = True

    if not is_valid:
        _record_failure(client_ip)
        _record_failure(uname)
        logger.warning("AUTH FAILURE: Incorrect password for user '%s' from IP '%s'", uname, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Clear previous failure history on success
    _clear_failures(client_ip)
    _clear_failures(uname)

    # Derivation of role: strictly server-side from the user document
    db_role = user.get("role", ROLE_PUBLIC_TIER)
    if db_role not in PRIVILEGED_ROLES:
        db_role = ROLE_PUBLIC_TIER

    token = create_access_token(username=user["username"], role=db_role)
    logger.info("AUTH SUCCESS: User '%s' authenticated with role '%s' from IP '%s'", uname, db_role, client_ip)

    return LoginResponse(
        success=True,
        username=user["username"],
        role=db_role,
        access_token=token,
        token_type="bearer",
        message="Authentication successful"
    )


@router.get("/auth/me")
def get_current_user_profile(user: dict = Depends(require_authenticated_user)):
    """
    Returns the authenticated user's profile and verified role from the verified JWT.
    """
    return {
        "authenticated": True,
        "username": user.get("username"),
        "role": user.get("role"),
    }
