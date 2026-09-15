# JanNidhi Sentinel — Deployment & Production Architecture

This project is architected as two decoupled services connecting to a shared MongoDB Atlas cluster:

```
Official MPLAD Website (mplads.mospi.gov.in)
       ↓
SERVICE 2: Scraper Service / Worker (Render Cron Job: python scraper.py)
       ↓ (Upserts & Checkpoints)
MongoDB Atlas (works, mp_allocations, scraper_progress, scraper_failures)
       ↓ (Fast Indexed Queries)
SERVICE 1: Web API Backend (Render Web Service: uvicorn backend.main:app)
       ↓
Frontend (Vercel / Render Static Site)
```

---

## Deploying on Render (Two-Service Architecture)

You can deploy directly using the included `render.yaml` Blueprint or set up the services manually in your Render dashboard.

### SERVICE 1: Main Web API Service (Render Web Service)
Pure read/query and governance API. Never executes heavy live scraping on incoming requests, keeping memory consumption < 80 MB.

- **Service Type**: `Web Service`
- **Name**: `jannidhi-api`
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- **Health Check Path**: `/api/health`
- **Plan**: `Free`
- **Environment Variables**:
  ```text
  PYTHON_VERSION=3.11.9
  ENVIRONMENT=production
  MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>/mplads_sentinel
  MONGO_DB_NAME=mplads_sentinel
  JWT_SECRET_KEY=<your-secret-key>
  CORS_ORIGINS=https://<your-frontend-domain>.vercel.app
  ENABLE_IN_PROCESS_SCHEDULER=0
  SEED_FROM_SAMPLE=1
  ```

---

### SERVICE 2: MPLADS Batch Scraper (Render Cron Job)
Dedicated background worker that collects data incrementally in batches of 5000 records, saves them to MongoDB, and updates persistent checkpoints.

- **Service Type**: `Cron Job`
- **Name**: `jannidhi-mplads-scraper`
- **Runtime**: `Python 3`
- **Schedule**: `0 */6 * * *` (Runs every 6 hours, or configure as desired)
- **Build Command**: `pip install -r requirements.txt`
- **Command**: `python scraper.py`
- **Plan**: `Free`
- **Environment Variables**:
  ```text
  PYTHON_VERSION=3.11.9
  ENVIRONMENT=production
  MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>/mplads_sentinel
  MONGO_DB_NAME=mplads_sentinel
  BATCH_SIZE=5000
  SCRAPER_CHUNK_SIZE=500
  SCRAPER_CONCURRENCY=3
  REQUEST_TIMEOUT=30
  MPLADS_LIVE_HOUSE=rajya_sabha
  ```

---

## Scraper CLI Execution Options

To run the scraper manually or in local development:

```bash
# Run standard 5000 record batch from last checkpoint
python scraper.py

# Custom batch size
python scraper.py --batch-size 1000

# Reset checkpoint to 0 and re-sync from start
python scraper.py --reset-checkpoint --batch-size 5000

# Dry-run test without writing to MongoDB
python scraper.py --dry-run --batch-size 500

# Replay offline sample data
python scraper.py --source-file data/mplads_raw_sample.csv
```

---

## MongoDB Atlas Collections & Indexes

The application automatically creates the required indexes on startup via `ensure_indexes()`.

1. **`works`**: Primary collection for scored works.
   - Unique Index: `work_id`
   - Compound Indexes: `(priority_rank, work_id)`, `(risk_tier, priority_rank)`, `(_mp_name_lower, house)`, `(_state_lower, priority_rank)`
   - Filter Indexes: `constituency`, `sanction_date`, `work_category`, `work_status`, `final_risk_score`
2. **`scraper_progress`**: Stores persistent checkpoint state.
   - Unique Index: `source`
3. **`scraper_failures`**: Isolates unprocessable records with error logs.
   - Compound Index: `(source, last_attempt)`
   - Identifier Index: `record_id`
4. **`mp_allocations`**: MP allocated budget ledger.
   - Unique Compound Index: `(mp_name, house, constituency, state)`
5. **`users`**: Administrative & Auditor credentials.
   - Unique Index: `username`
6. **`sync_logs`**: Historical audit log of ingestion and scraper runs.
   - Index: `run_timestamp`
