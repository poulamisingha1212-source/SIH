"""
MongoDB-backed distributed lock for ingestion and synchronization operations.

Guarantees mutual exclusion across multiple uvicorn/gunicorn worker processes
and server instances. Automatically expires stale locks if a worker crashes mid-run.
"""
import os
import socket
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from backend.database import db

_LOCK_COLLECTION = "sync_locks"
_LOCK_ID = "mplads_ingestion_lock"


def get_worker_id() -> str:
    """Unique worker identifier combining hostname, process ID, and thread ID."""
    return f"{socket.gethostname()}-{os.getpid()}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def acquire_sync_lock(owner: str | None = None, ttl_seconds: int = 900) -> bool:
    """
    Attempts to atomically acquire the distributed sync lock.

    Returns True if the lock was acquired, False if a valid unexpired lock
    is held by another process.
    """
    now = _utcnow()
    expires_at = now + timedelta(seconds=ttl_seconds)
    lock_owner = owner or get_worker_id()

    collection = db[_LOCK_COLLECTION]

    try:
        # Atomic test-and-set: acquire if unlocked OR if previous lock has expired
        res = collection.find_one_and_update(
            {
                "_id": _LOCK_ID,
                "$or": [
                    {"locked": False},
                    {"locked": {"$exists": False}},
                    {"expires_at": {"$lt": now}},
                ]
            },
            {
                "$set": {
                    "locked": True,
                    "owner": lock_owner,
                    "locked_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return res is not None and res.get("owner") == lock_owner
    except Exception:
        # On database connection error, fail closed
        return False


def release_sync_lock(owner: str | None = None, force: bool = False) -> bool:
    """
    Releases the distributed sync lock. If force=True, releases regardless of owner.
    """
    collection = db[_LOCK_COLLECTION]
    try:
        if force or not owner:
            filt = {"_id": _LOCK_ID}
        else:
            filt = {"_id": _LOCK_ID, "owner": owner}
        res = collection.update_one(
            filt,
            {"$set": {"locked": False, "released_at": _utcnow()}}
        )
        return res.modified_count > 0
    except Exception:
        return False


def is_sync_locked() -> bool:
    """
    Checks if a valid, unexpired sync lock is currently active.
    """
    collection = db[_LOCK_COLLECTION]
    now = _utcnow()
    try:
        doc = collection.find_one({"_id": _LOCK_ID})
        if not doc:
            return False
        exp = doc.get("expires_at")
        if not exp:
            return False
        exp_naive = _to_utc_naive(exp) if isinstance(exp, datetime) else None
        return bool(doc.get("locked") and exp_naive and exp_naive > now)
    except Exception:
        return False
