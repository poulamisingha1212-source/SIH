"""
Standalone MPLADS Batch Scraper Service.

Key Features:
- Decoupled from the public Web API / Render web backend.
- Incremental batch processing with configurable BATCH_SIZE (default 5000).
- Persistent MongoDB checkpointing in `scraper_progress` collection.
- Crash recovery: Resumes from the exact last persisted record offset.
- Low-RAM streaming: Sub-batch chunking (default 500) and explicit garbage collection.
- Failure queue: Isolated logging of unprocessable records in `scraper_failures`.
- Exponential backoff retry handling for transient network issues.
- Atomic distributed sync lock protection.
"""
import argparse
import gc
import logging
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from pymongo import ReplaceOne, UpdateOne

from backend.config import settings
from backend.database import (
    mp_allocations,
    next_id,
    scraper_failures,
    scraper_progress,
    sync_logs,
    works,
)
from backend.models import now_utc
from backend.services.ingestion import (
    _normalize_work,
    _reshape_long_format,
    _upsert_allocations,
)
from backend.services.mplads_live import (
    MPLADSLiveClient,
)
from backend.services.sync_lock import acquire_sync_lock, get_worker_id, release_sync_lock
from model.risk_engine import score_dataset

logger = logging.getLogger("jannidhi.scraper")

DEFAULT_SOURCE = "mplad"


# ------------------------------------------------------------------------------
# Checkpoint & Failure Management
# ------------------------------------------------------------------------------

def get_scraper_checkpoint(source: str = DEFAULT_SOURCE) -> dict:
    """Reads the persistent checkpoint from MongoDB scraper_progress."""
    doc = scraper_progress.find_one({"source": source})
    if not doc:
        return {
            "source": source,
            "last_processed": 0,
            "batch_size": settings.BATCH_SIZE,
            "status": "uninitialized",
            "updated_at": now_utc(),
        }
    return doc


def update_scraper_checkpoint(
    source: str = DEFAULT_SOURCE,
    last_processed: int = 0,
    status: str = "in_progress",
    total_records: int | None = None,
    last_run_stats: dict | None = None,
    house: str | None = None,
) -> None:
    """Atomically updates the persistent checkpoint in MongoDB scraper_progress."""
    now = now_utc()
    update_fields: dict[str, Any] = {
        "source": source,
        "last_processed": last_processed,
        "status": status,
        "updated_at": now,
    }
    if total_records is not None:
        update_fields["total_records_discovered"] = total_records
    if last_run_stats is not None:
        update_fields["last_run_stats"] = last_run_stats
    if house is not None:
        update_fields["last_house"] = house

    scraper_progress.update_one(
        {"source": source},
        {"$set": update_fields, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


def reset_scraper_checkpoint(source: str = DEFAULT_SOURCE) -> None:
    """Resets the checkpoint counter to 0 for full re-syncs."""
    scraper_progress.update_one(
        {"source": source},
        {"$set": {"last_processed": 0, "status": "reset", "updated_at": now_utc()}},
        upsert=True,
    )
    logger.info("Checkpoint for source '%s' has been reset to 0.", source)


def record_scraper_failure(
    source: str = DEFAULT_SOURCE,
    record_id: str | None = None,
    error: str = "",
    raw_data: dict | None = None,
) -> None:
    """Persists unprocessable record info into scraper_failures collection."""
    now = now_utc()
    rec_key = record_id or f"unknown-{int(time.time() * 1000)}"
    scraper_failures.update_one(
        {"source": source, "record_id": rec_key},
        {
            "$set": {
                "error": str(error),
                "raw_data": raw_data or {},
                "last_attempt": now,
            },
            "$inc": {"attempts": 1},
            "$setOnInsert": {"first_failed_at": now},
        },
        upsert=True,
    )


# ------------------------------------------------------------------------------
# Low-RAM Incremental Batch Processor
# ------------------------------------------------------------------------------

def _upsert_works_chunk(df_chunk: pd.DataFrame) -> dict:
    """Persists a single chunk of scored works into MongoDB, using upsert."""
    inserted = 0
    updated = 0
    failed = 0
    now = now_utc()

    rows = [r for _, r in df_chunk.iterrows() if not pd.isna(r.get("work_id"))]
    if not rows:
        return {"inserted": 0, "updated": 0, "failed": 0, "processed": 0}

    mappings = []
    for r in rows:
        try:
            mappings.append(_normalize_work(r))
        except Exception as err:
            failed += 1
            record_scraper_failure(
                source=DEFAULT_SOURCE,
                record_id=str(r.get("work_id")),
                error=f"Normalization error: {err}",
                raw_data=r.to_dict() if hasattr(r, "to_dict") else {},
            )

    if not mappings:
        return {"inserted": 0, "updated": 0, "failed": failed, "processed": len(rows)}

    ids = [m["work_id"] for m in mappings]
    existing_ids = set(works.distinct("work_id", {"work_id": {"$in": ids}}))

    ops = []
    for m in mappings:
        try:
            m["updated_at"] = now
            if m["work_id"] in existing_ids:
                ops.append(UpdateOne({"work_id": m["work_id"]}, {"$set": m}))
                updated += 1
            else:
                m["created_at"] = now
                ops.append(ReplaceOne({"work_id": m["work_id"]}, m, upsert=True))
                inserted += 1
        except Exception as err:
            failed += 1
            record_scraper_failure(
                source=DEFAULT_SOURCE,
                record_id=m.get("work_id"),
                error=f"Upsert operation error: {err}",
                raw_data=m,
            )

    if ops:
        try:
            works.bulk_write(ops, ordered=False)
        except Exception as err:
            logger.error("Bulk write partial failure: %s", err)

    return {"inserted": inserted, "updated": updated, "failed": failed, "processed": len(rows)}


def _count_csv_rows(filepath: Path) -> int:
    """Counts data rows in a CSV file quickly without loading it into memory."""
    count = 0
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        if not header:
            return 0
        for _ in f:
            count += 1
    return count


def _read_csv_slice(filepath: Path, start_offset: int, limit: int) -> pd.DataFrame:
    """Reads only the slice [start_offset : start_offset + limit] into memory."""
    if start_offset > 0:
        return pd.read_csv(filepath, skiprows=range(1, start_offset + 1), nrows=limit)
    return pd.read_csv(filepath, nrows=limit)


def run_scraper_batch(
    batch_size: int | None = None,
    house: str | None = None,
    chunk_size: int | None = None,
    source_file: Path | None = None,
    reset_checkpoint: bool = False,
    dry_run: bool = False,
    timeout: int | None = None,
) -> dict:
    """
    Executes a single batch of MPLAD data collection, scoring, and incremental persistence.

    Steps:
    1. Reads MongoDB scraper_progress checkpoint.
    2. Calculates range: [start_offset : start_offset + batch_size].
    3. Fetches data slice incrementally.
    4. Slices batch into low-RAM chunks (default 100).
    5. Scores with multi-agent risk engine and upserts to MongoDB.
    6. Updates checkpoint immediately after each successfully persisted chunk.
    7. Emits structured milestone logs and sync_logs audit entries.
    """
    effective_batch_size = batch_size or settings.BATCH_SIZE or 5000
    effective_chunk_size = chunk_size or settings.SCRAPER_CHUNK_SIZE or 100
    effective_house = house or settings.MPLADS_LIVE_HOUSE or "rajya_sabha"
    effective_timeout = timeout or settings.REQUEST_TIMEOUT or 30

    logger.info("==================================================")
    logger.info("Scraper started")
    logger.info("Source: %s (house: %s)", DEFAULT_SOURCE, effective_house)

    if reset_checkpoint:
        reset_scraper_checkpoint(DEFAULT_SOURCE)

    checkpoint = get_scraper_checkpoint(DEFAULT_SOURCE)
    last_processed = int(checkpoint.get("last_processed", 0))
    start_offset = last_processed
    target_end = start_offset + effective_batch_size

    logger.info("Last checkpoint: %d", last_processed)
    logger.info("Batch size: %d", effective_batch_size)
    logger.info("Starting from: %d", start_offset + 1)
    logger.info("Targeting range: %d to %d", start_offset + 1, target_end)

    start_dt = now_utc()
    t0 = time.time()

    # 1. Acquire Distributed Lock
    worker_id = get_worker_id()
    if not dry_run:
        if not acquire_sync_lock(owner=worker_id, ttl_seconds=1800):
            msg = "Scraper skipped: another sync or scraper process is currently holding the sync lock."
            logger.warning(msg)
            return {"status": "locked", "message": msg}

    total_inserted = 0
    total_updated = 0
    total_failed = 0
    total_processed = 0

    try:
        # 2. Acquire Raw Long-format Data (Memory-Safe Streaming / Cache)
        cache_path = settings.DATA_DIR / "last_live_feed.csv"
        if source_file is not None and Path(source_file).exists():
            feed_path = Path(source_file)
            total_available = _count_csv_rows(feed_path)
            logger.info("Reading raw data slice from local feed file: %s", source_file)
        elif cache_path.exists() and (time.time() - cache_path.stat().st_mtime < 21600) and start_offset > 0:
            logger.info("Resuming from existing live feed cache: %s", cache_path)
            feed_path = cache_path
            total_available = _count_csv_rows(feed_path)
        else:
            logger.info("Fetching live MPLADS datasets from portal API...")
            client = MPLADSLiveClient(timeout=effective_timeout)
            raw_full_df = client.fetch_long_dataframe(houses=effective_house)
            settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
            raw_full_df.to_csv(cache_path, index=False)
            total_available = len(raw_full_df)
            feed_path = cache_path
            del raw_full_df
            gc.collect()

        logger.info("Total records discovered in feed: %d", total_available)

        # Wrap around or check if previous checkpoint exceeded total
        if start_offset >= total_available:
            logger.info(
                "Checkpoint (%d) reached end of feed (%d). Cycling back to 0 for fresh synchronization.",
                start_offset, total_available
            )
            start_offset = 0
            target_end = start_offset + effective_batch_size

        records_to_read = min(effective_batch_size, max(0, total_available - start_offset))
        if records_to_read == 0:
            logger.info("No records to process in this slice. Scraper finished.")
            if not dry_run:
                update_scraper_checkpoint(
                    source=DEFAULT_SOURCE,
                    last_processed=start_offset,
                    status="completed",
                    total_records=total_available,
                    house=effective_house,
                )
            return {
                "status": "completed",
                "processed": 0,
                "last_processed": start_offset,
                "duration_seconds": round(time.time() - t0, 2),
            }

        batch_df = _read_csv_slice(feed_path, start_offset, records_to_read)
        batch_record_count = len(batch_df)
        logger.info("Batch slice extracted: %d records", batch_record_count)

        # 3. Stream & Process in Sub-Chunks for Low-RAM Footprint
        alloc_map = {
            a["mp_name"]: a.get("allocated_amount") or 0.0
            for a in mp_allocations.find({}, {"mp_name": 1, "allocated_amount": 1})
        }

        current_checkpoint = start_offset
        num_chunks = (batch_record_count + effective_chunk_size - 1) // effective_chunk_size

        for chunk_idx in range(num_chunks):
            c_start = chunk_idx * effective_chunk_size
            c_end = min(c_start + effective_chunk_size, batch_record_count)
            chunk_slice = batch_df.iloc[c_start:c_end].copy()
            global_chunk_start = start_offset + c_start + 1
            global_chunk_end = start_offset + c_end

            for rec_num in range(global_chunk_start, global_chunk_end + 1):
                logger.info("Processing: %d", rec_num)

            try:
                # Reshape and score chunk
                reshaped_chunk = _reshape_long_format(chunk_slice)
                scored_chunk = score_dataset(
                    reshaped_chunk,
                    model_dir=settings.MODEL_DIR,
                    mp_allocations=alloc_map,
                )

                if not dry_run:
                    # Upsert works
                    counts = _upsert_works_chunk(scored_chunk)
                    total_inserted += counts["inserted"]
                    total_updated += counts["updated"]
                    total_failed += counts["failed"]
                    total_processed += counts["processed"]

                    # Upsert allocations if any present in chunk
                    _upsert_allocations(chunk_slice)

                    # Update checkpoint incrementally after each chunk succeeds!
                    current_checkpoint = global_chunk_end
                    update_scraper_checkpoint(
                        source=DEFAULT_SOURCE,
                        last_processed=current_checkpoint,
                        status="in_progress",
                        total_records=total_available,
                        house=effective_house,
                    )

                    logger.info(
                        "Persisted %d records (%d inserted, %d updated). Checkpoint updated: %d",
                        counts["processed"], counts["inserted"], counts["updated"], current_checkpoint
                    )
                else:
                    total_processed += len(scored_chunk)
                    current_checkpoint = global_chunk_end
                    logger.info("[DRY RUN] Scored %d records. Simulated checkpoint: %d", len(scored_chunk), current_checkpoint)

            except Exception as chunk_err:
                logger.error("Error processing chunk %d: %s", chunk_idx + 1, chunk_err, exc_info=True)
                total_failed += len(chunk_slice)
            finally:
                # Explicit Memory Release
                del chunk_slice
                gc.collect()

        del batch_df
        gc.collect()

        # 4. Mark Batch Complete
        duration = round(time.time() - t0, 2)
        run_stats = {
            "start_offset": start_offset + 1,
            "end_offset": current_checkpoint,
            "processed": total_processed,
            "inserted": total_inserted,
            "updated": total_updated,
            "failed": total_failed,
            "duration_seconds": duration,
        }

        if not dry_run:
            update_scraper_checkpoint(
                source=DEFAULT_SOURCE,
                last_processed=current_checkpoint,
                status="completed",
                total_records=total_available,
                last_run_stats=run_stats,
                house=effective_house,
            )

            # Record audit log entry in sync_logs
            sync_logs.insert_one({
                "id": next_id("sync_logs"),
                "run_timestamp": start_dt,
                "start_time": start_dt,
                "end_time": now_utc(),
                "status": "success" if total_failed == 0 else "partial_success",
                "source": f"MPLADS Batch Scraper ({effective_house})",
                "rows_fetched": batch_record_count,
                "rows_processed": total_processed,
                "rows_inserted": total_inserted,
                "rows_updated": total_updated,
                "rows_rejected": total_failed,
                "error_message": None if total_failed == 0 else f"{total_failed} records failed normalization/upsert",
            })

        logger.info("Processed: %d", total_processed)
        logger.info("Failed: %d", total_failed)
        logger.info("Checkpoint updated: %d", current_checkpoint)
        logger.info("Scraper finished in %.2fs", duration)
        logger.info("==================================================")

        return {
            "status": "completed",
            "processed": total_processed,
            "inserted": total_inserted,
            "updated": total_updated,
            "failed": total_failed,
            "last_processed": current_checkpoint,
            "total_available": total_available,
            "duration_seconds": duration,
        }

    except Exception as e:
        duration = round(time.time() - t0, 2)
        logger.error("Scraper failed with unhandled exception: %s", e, exc_info=True)
        if not dry_run:
            update_scraper_checkpoint(
                source=DEFAULT_SOURCE,
                last_processed=current_checkpoint if "current_checkpoint" in locals() else start_offset,
                status="failed",
                house=effective_house,
            )
            sync_logs.insert_one({
                "id": next_id("sync_logs"),
                "run_timestamp": start_dt,
                "start_time": start_dt,
                "end_time": now_utc(),
                "status": "failed",
                "source": f"MPLADS Batch Scraper ({effective_house})",
                "rows_fetched": 0,
                "rows_processed": total_processed,
                "rows_inserted": total_inserted,
                "rows_updated": total_updated,
                "rows_rejected": total_failed,
                "error_message": str(e),
            })
        return {
            "status": "failed",
            "error": str(e),
            "last_processed": current_checkpoint if "current_checkpoint" in locals() else start_offset,
            "duration_seconds": duration,
        }
    finally:
        if not dry_run:
            release_sync_lock(owner=worker_id)


def main() -> None:
    """CLI entry point for running the batch scraper standalone."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="JanNidhi Sentinel - Standalone MPLADS Batch Scraper Service"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help=f"Number of records to process per batch (default: {settings.BATCH_SIZE})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help=f"Sub-batch chunk size for memory streaming (default: {settings.SCRAPER_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--house",
        type=str,
        default=None,
        help="MPLADS house selector (rajya_sabha | lok_sabha | both)",
    )
    parser.add_argument(
        "--source-file",
        type=str,
        default=None,
        help="Optional path to local sample CSV for offline replays / test replays",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset checkpoint to 0 before running",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score records without persisting to MongoDB",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Network request timeout in seconds",
    )

    args = parser.parse_args()

    res = run_scraper_batch(
        batch_size=args.batch_size,
        house=args.house,
        chunk_size=args.chunk_size,
        source_file=Path(args.source_file) if args.source_file else None,
        reset_checkpoint=args.reset_checkpoint,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    if res.get("status") == "failed":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
