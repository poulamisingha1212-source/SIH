"""
Database bootstrap. The system's primary source is the live MPLADS dashboard
API. When the database is empty (first run):

- Serverless (SEED_FROM_SAMPLE=1): the bundled sample CSV is ingested
  synchronously so a fresh deployment shows data immediately; the scheduled
  live sync then replaces it with real portal data.
- Otherwise: an initial live sync is kicked off in a background thread so the
  API comes up immediately and data streams in.
"""
import logging
import threading

from backend.auth import ROLE_DISTRICT_AUDITOR, ROLE_MOSPI_REVIEWER, hash_password
from backend.config import settings
from backend.database import ensure_indexes, mp_allocations, users, works
from backend.models import lower_or_none, now_utc

logger = logging.getLogger("jannidhi.seeder")


def _initial_live_sync():
    from backend.services.ingestion import run_ingestion
    try:
        result = run_ingestion(mode="live")
        logger.info(
            "Initial live sync finished: %s — %d records processed.",
            result.get("status"),
            result.get("processed", 0)
        )
    except Exception as e:
        logger.error("Initial live sync failed: %s. Use POST /api/sync/run?mode=live to retry.", e)


def _ensure_default_allocations() -> int:
    """
    Fallback seeder: if mp_allocations is empty or missing entries for MPs in works,
    upsert default statutory allocation entries (₹5 Cr per MP per term).
    """
    from pymongo import ReplaceOne

    now = now_utc()
    pipeline = [
        {"$match": {"mp_name": {"$ne": None, "$exists": True}}},
        {"$group": {
            "_id": "$mp_name",
            "house": {"$first": "$house"},
            "constituency": {"$first": "$constituency"},
            "state": {"$first": "$state"},
        }}
    ]

    distinct_mps = list(works.aggregate(pipeline))
    if not distinct_mps:
        return 0

    ops = []
    for mp in distinct_mps:
        mp_name = mp["_id"]
        house_val = mp.get("house") or "Lok Sabha"
        constituency_val = mp.get("constituency") or ""
        state_val = mp.get("state") or ""

        key = dict(
            mp_name=mp_name,
            house=house_val,
            constituency=constituency_val,
            state=state_val,
        )
        doc = {
            **key,
            "allocated_amount": 50000000.0,
            "tenure_start": None,
            "updated_at": now,
            "_mp_name_lower": lower_or_none(mp_name),
        }
        ops.append(ReplaceOne(key, doc, upsert=True))

    if ops:
        mp_allocations.bulk_write(ops, ordered=False)
        logger.info("Ensured %d MP allocation records in mp_allocations.", len(ops))
    return len(ops)


def _seed_from_sample() -> int:
    from backend.services.ingestion import run_ingestion
    if not settings.RAW_SAMPLE_PATH.exists():
        logger.warning("Sample feed not found at %s; skipping sample seed.", settings.RAW_SAMPLE_PATH)
        _ensure_default_allocations()
        return 0
    try:
        result = run_ingestion(mode="auto", source_file_path=settings.RAW_SAMPLE_PATH)
        logger.info("Sample seed finished: %d records processed.", result.get("processed", 0))
        _ensure_default_allocations()
        return int(result.get("processed", 0))
    except Exception as e:
        logger.error("Sample seed failed: %s. Live sync will retry via the scheduler/cron.", e)
        _ensure_default_allocations()
        return 0


def seed_users():
    """
    Ensure administrative and auditor users exist in MongoDB `users` collection
    with cryptographically hashed passwords.
    """
    default_users = [
        {
            "username": settings.INITIAL_ADMIN_USERNAME,
            "password": hash_password(settings.INITIAL_ADMIN_PASSWORD),
            "role": ROLE_MOSPI_REVIEWER,
            "created_at": now_utc(),
        },
        {
            "username": settings.INITIAL_AUDITOR_USERNAME,
            "password": hash_password(settings.INITIAL_AUDITOR_PASSWORD),
            "role": ROLE_DISTRICT_AUDITOR,
            "created_at": now_utc(),
        },
        # Legacy backward-compatibility account (Netai)
        {
            "username": "Netai",
            "password": hash_password(settings.INITIAL_ADMIN_PASSWORD),
            "role": ROLE_MOSPI_REVIEWER,
            "created_at": now_utc(),
        }
    ]

    for u in default_users:
        existing = users.find_one({"username": u["username"]})
        if not existing:
            users.insert_one(u)
            logger.info("Seeded user '%s' with role '%s'.", u["username"], u["role"])
        elif not existing.get("password", "").startswith("pbkdf2_sha256$"):
            # Migrate unhashed plaintext passwords to PBKDF2 hash
            users.update_one(
                {"username": u["username"]},
                {"$set": {"password": u["password"], "role": u["role"], "updated_at": now_utc()}}
            )
            logger.info("Migrated plaintext password to cryptographic hash for user '%s'.", u["username"])


def seed_database(force: bool = False):
    """
    Ensure indexes exist and populate/update works, mp_allocations and users collections —
    synchronously from the bundled sample feed on startup so all features and
    charts render full data. Safe to run repeatedly; idempotent.
    """
    ensure_indexes()
    seed_users()

    existing_count = works.count_documents({})
    alloc_count = mp_allocations.count_documents({})

    # If both works (>= 1000) and mp_allocations (> 0) contain data and force is False, skip
    if existing_count >= 1000 and alloc_count > 0 and not force:
        logger.info(
            "Database already contains %d works and %d allocations. Bootstrap skipped.",
            existing_count,
            alloc_count
        )
        return existing_count

    if settings.SEED_FROM_SAMPLE or existing_count < 1000 or alloc_count == 0 or force:
        logger.info("Seeding database (works: %d, allocations: %d)...", existing_count, alloc_count)
        return _seed_from_sample()

    logger.info("Starting initial live sync in the background...")
    threading.Thread(target=_initial_live_sync, daemon=True).start()
    return 0


if __name__ == "__main__":
    seed_database()
