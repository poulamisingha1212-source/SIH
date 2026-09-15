import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env loader — KEY=VALUE lines, no new dependency.
    Existing process env vars always win (setdefault)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS")
    if not configured:
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


def _clean_db_name(val: str | None) -> str:
    if not val:
        return "mplads_sentinel"
    # Strip spaces, single/double quotes, and trailing/leading invalid chars
    cleaned = val.strip().strip('"').strip("'").replace(" ", "")
    # Remove any character not allowed in Mongo database names: /\. "$*<>:|?
    for char in ['/', '\\', '.', ' ', '"', '$', '*', '<', '>', ':', '|', '?']:
        cleaned = cleaned.replace(char, '')
    return cleaned if cleaned else "mplads_sentinel"


class Settings:
    PROJECT_NAME: str = "MPLADS AI Sentinel"
    VERSION: str = "1.1.0"
    API_V1_STR: str = "/api"

    # Security & JWT Configuration
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "jan-nidhi-sentinel-secure-jwt-secret-key-2026").strip()
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256").strip()
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120"))

    # Default Administrative & Auditor Bootstrap Credentials (overridden via env)
    INITIAL_ADMIN_USERNAME: str = os.getenv("INITIAL_ADMIN_USERNAME", "admin_mospi").strip()
    INITIAL_ADMIN_PASSWORD: str = os.getenv("INITIAL_ADMIN_PASSWORD", "MoSPI@JanNidhi2026").strip()
    INITIAL_AUDITOR_USERNAME: str = os.getenv("INITIAL_AUDITOR_USERNAME", "district_auditor").strip()
    INITIAL_AUDITOR_PASSWORD: str = os.getenv("INITIAL_AUDITOR_PASSWORD", "Auditor@JanNidhi2026").strip()

    # Rate Limiting & Lockout
    AUTH_MAX_FAILED_ATTEMPTS: int = int(os.getenv("AUTH_MAX_FAILED_ATTEMPTS", "5"))
    AUTH_LOCKOUT_MINUTES: int = int(os.getenv("AUTH_LOCKOUT_MINUTES", "15"))

    # MongoDB: the only persistence layer
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017").strip().strip('"').strip("'")
    MONGO_DB_NAME: str = _clean_db_name(os.getenv("MONGO_DB_NAME"))

    DATA_DIR: Path = BASE_DIR / "data"
    MODEL_DIR: Path = BASE_DIR / "model"
    # Bundled long-format sample feed, used only by tests / offline replays
    RAW_SAMPLE_PATH: Path = BASE_DIR / "data" / "mplads_raw_sample.csv"

    # Serverless & Decoupled Architecture:
    # On Render Web Service, ENABLE_IN_PROCESS_SCHEDULER is False by default
    # so heavy scraping never runs on user-facing API instances.
    ENABLE_IN_PROCESS_SCHEDULER: bool = os.getenv("ENABLE_IN_PROCESS_SCHEDULER", "0").lower() in {"1", "true", "yes"}
    IS_SERVERLESS: bool = os.getenv("VERCEL") == "1" or os.getenv("IS_SERVERLESS") == "1"

    # On first boot with an empty database, ingest the bundled sample CSV
    # so the deployment shows rich data immediately (fast, memory-friendly).
    SEED_FROM_SAMPLE: bool = os.getenv("SEED_FROM_SAMPLE", "1").lower() in {"1", "true", "yes"}

    # Vercel Cron authenticates with `Authorization: Bearer $CRON_SECRET`
    # (the env var of this exact name). Also accepted as X-Cron-Secret.
    CRON_SECRET: str = os.getenv("CRON_SECRET", "")

    # Scheduled sync time (configurable)
    SYNC_CRON_HOUR: int = int(os.getenv("SYNC_CRON_HOUR", "19"))
    SYNC_CRON_MINUTE: int = int(os.getenv("SYNC_CRON_MINUTE", "0"))
    SYNC_CRON_TIMEZONE: str = os.getenv("SYNC_CRON_TIMEZONE", "Asia/Kolkata")

    CORS_ORIGINS: list[str] = _cors_origins()

    # Live MPLADS dashboard API (mplads.mospi.gov.in /digigov)
    MPLADS_BASE_URL: str = os.getenv("MPLADS_BASE_URL", "https://mplads.mospi.gov.in")
    MPLADS_LIVE_HOUSE: str = os.getenv("MPLADS_LIVE_HOUSE", "rajya_sabha")
    MPLADS_LIVE_TIMEOUT: int = int(os.getenv("MPLADS_LIVE_TIMEOUT", "300"))

    # Dedicated Batch Scraper Configuration
    BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "5000"))
    SCRAPER_CONCURRENCY: int = int(os.getenv("SCRAPER_CONCURRENCY", "3"))
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
    SCRAPER_CHUNK_SIZE: int = int(os.getenv("SCRAPER_CHUNK_SIZE", "100"))

settings = Settings()

