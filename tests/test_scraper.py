"""
Unit & Integration Tests for Standalone Batch Scraper & Checkpoint System.
"""
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import scraper_failures
from backend.main import app
from backend.services.scraper import (
    DEFAULT_SOURCE,
    get_scraper_checkpoint,
    record_scraper_failure,
    reset_scraper_checkpoint,
    run_scraper_batch,
    update_scraper_checkpoint,
)
from backend.services.sync_lock import release_sync_lock

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_sync_lock():
    release_sync_lock(force=True)
    yield
    release_sync_lock(force=True)


def test_scraper_checkpoint_initial_run():
    """Verify initial batch starts from 0 and updates checkpoint to batch_size."""
    reset_scraper_checkpoint(DEFAULT_SOURCE)
    assert get_scraper_checkpoint(DEFAULT_SOURCE).get("last_processed") == 0

    batch_size = 100
    res = run_scraper_batch(
        batch_size=batch_size,
        chunk_size=50,
        source_file=settings.RAW_SAMPLE_PATH,
    )

    assert res["status"] == "completed"
    assert res["last_processed"] == 100

    # Verify persistent checkpoint in MongoDB
    cp = get_scraper_checkpoint(DEFAULT_SOURCE)
    assert cp["last_processed"] == 100
    assert cp["status"] == "completed"
    assert cp["total_records_discovered"] > 100


def test_scraper_checkpoint_resumes_from_previous_offset():
    """Verify subsequent batch resumes from previous checkpoint (100 -> 250)."""
    # Set starting checkpoint at 100
    update_scraper_checkpoint(source=DEFAULT_SOURCE, last_processed=100, status="completed")

    res = run_scraper_batch(
        batch_size=150,
        chunk_size=50,
        source_file=settings.RAW_SAMPLE_PATH,
    )

    assert res["status"] == "completed"
    assert res["last_processed"] == 250

    cp = get_scraper_checkpoint(DEFAULT_SOURCE)
    assert cp["last_processed"] == 250


def test_scraper_crash_recovery_resumes_from_exact_offset():
    """
    Crash Recovery Scenario:
    Simulate a crash mid-run where last recorded checkpoint is 1238.
    The next run must resume from 1238 and process the next batch.
    """
    update_scraper_checkpoint(source=DEFAULT_SOURCE, last_processed=1238, status="interrupted")

    res = run_scraper_batch(
        batch_size=100,
        chunk_size=50,
        source_file=settings.RAW_SAMPLE_PATH,
    )

    assert res["status"] == "completed"
    assert res["last_processed"] == 1338

    cp = get_scraper_checkpoint(DEFAULT_SOURCE)
    assert cp["last_processed"] == 1338


def test_scraper_failure_queue_logging():
    """Verify unprocessable records are cleanly isolated in scraper_failures collection."""
    record_scraper_failure(
        source=DEFAULT_SOURCE,
        record_id="TEST-FAIL-001",
        error="Simulated validation failure",
        raw_data={"work_id": "TEST-FAIL-001", "invalid_field": True},
    )

    doc = scraper_failures.find_one({"record_id": "TEST-FAIL-001"})
    assert doc is not None
    assert doc["source"] == DEFAULT_SOURCE
    assert "Simulated validation failure" in doc["error"]
    assert doc["attempts"] >= 1


def test_scraper_dry_run_mode():
    """Verify dry-run processes records without mutating MongoDB checkpoint or collections."""
    update_scraper_checkpoint(source=DEFAULT_SOURCE, last_processed=50, status="completed")

    res = run_scraper_batch(
        batch_size=50,
        source_file=settings.RAW_SAMPLE_PATH,
        dry_run=True,
    )

    assert res["status"] == "completed"
    # In dry-run, DB checkpoint remains 50
    cp = get_scraper_checkpoint(DEFAULT_SOURCE)
    assert cp["last_processed"] == 50


def test_sync_status_reports_scraper_checkpoint():
    """Verify GET /api/sync/status includes real-time scraper checkpoint details."""
    update_scraper_checkpoint(
        source=DEFAULT_SOURCE,
        last_processed=5000,
        status="completed",
        total_records=7319,
    )

    resp = client.get("/api/sync/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "scraper_checkpoint" in data
    assert data["scraper_checkpoint"]["last_processed"] == 5000
    assert data["scraper_checkpoint"]["status"] == "completed"
    assert data["scraper_checkpoint"]["total_records_discovered"] == 7319
