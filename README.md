---
title: MPLADS AI Sentinel
sdk: docker
app_port: 7860
---

# MPLADS AI Sentinel — Audit & Anomaly Prioritization Platform
**MoSPI (SIH26102)** — Ministry of Statistics and Programme Implementation

---

## 1. What MPLADS AI Sentinel Does

**MPLADS AI Sentinel** is an AI-assisted audit prioritization and decision-support web platform designed for **MoSPI**. It systematically surfaces potential irregularities, cost outliers, duplicate claims, stagnant implementation, and procurement concentration across works executed under the **Members of Parliament Local Area Development Scheme (MPLADS)**.

> **Crucial Explainability Principle:**  
> The system produces **Risk Tiers (`High Risk - Review`, `Medium Risk - Monitor`, `Low Risk`)** and **Action Directives**, not definitive criminal verdicts. An AI flag is a decision-support filter for audit inspection; only authorized human reviewers can confirm an irregularity or fraud.

-## 2. Project Architecture

```
project-root/
├── .github/workflows/
│   └── ci.yml                      # GitHub Actions: Pytest, Ruff, Frontend build, Docker
├── docker-compose.yml              # Local multi-container development stack
├── Dockerfile                      # Hardened container image with health check
├── requirements.txt                # Lean production dependencies
├── requirements-dev.txt            # Development & testing dependencies
│
├── docs/
│   ├── SRS.md                      # Full Software Requirements Specification
│   └── fraud_detection_logic.md    # Evidence-grounded typology & signal mapping
│
├── model/                          # Multi-Agent Risk Scoring System
│   ├── risk_engine.py              # Authoritative risk engine facade
│   └── agents/                     # Specialist autonomous audit agents
│       ├── vendor_agent.py         # Procurement concentration & vendor collusion
│       ├── timeline_agent.py       # Stagnant implementation & timeline anomalies
│       ├── financial_agent.py      # Utilization ratio & disbursement discrepancies
│       ├── duplicate_agent.py      # Ghost work & cross-MP duplicate detection
│       ├── compliance_agent.py     # Trust routing & evidence verification
│       └── coordinator.py          # Unified agent consensus coordinator
│
├── data/
│   ├── mplads_raw_sample.csv       # Reference schema sample for tests/offline replay
│   └── last_live_feed.csv          # Local live-sync cache
│
├── backend/
│   ├── config.py                   # Pydantic environment configuration (v1.1.0)
│   ├── database.py                 # MongoDB driver & index lifecycle management
│   ├── models.py                   # Mongo document helper utilities
│   ├── schemas.py                  # Pydantic REST API schemas & payload sanitizers
│   ├── auth.py                     # PBKDF2 password hashing & JWT Bearer token RBAC
│   ├── seeder.py                   # Idempotent database seeder with hashed credentials
│   ├── main.py                     # Modular FastAPI app coordinator (<120 lines)
│   ├── routers/                    # Decoupled REST API routers
│   │   ├── auth.py                 # Login, JWT issuance, rate limiting & lockout
│   │   ├── works.py                # Priority queue, case packets & CSV export
│   │   ├── reviews.py              # Human audit outcomes & citizen verification
│   │   ├── directory.py            # MP and State aggregate directories & dossiers
│   │   ├── analytics.py            # Portfolio macro statistics & category charts
│   │   ├── sync.py                 # Ingestion triggers & distributed sync lock
│   │   └── health.py               # Liveness & readiness probe
│   └── services/
│       ├── ingestion.py            # Live MPLADS API ingestion pipeline
│       ├── analytics.py            # MongoDB aggregation pipelines for directories/charts
│       └── sync_lock.py            # Distributed MongoDB lock for multi-worker safety
│
├── frontend/                       # React + Tailwind CSS Dashboard (Vite)
│   ├── src/
│   │   ├── components/             # Dashboard, PriorityQueue, Header, CasePacketModal, LoginModal
│   │   ├── lib/                    # api.js (JWT Bearer injection), chart.js, format.js
│   │   ├── App.jsx                 # Main stateful application & session restoration
│   │   └── index.css               # Design tokens & glass panel styling
│   └── vite.config.js              # Vite bundler configuration
│
└── tests/                          # Automated Pytest Suite
    ├── conftest.py                 # Offline mongomock test fixture
    ├── test_auth.py                # Password hashing, JWT token & lockout tests
    ├── test_rbac.py                # Complete RBAC permission matrix tests
    ├── test_public_review.py       # SSRF/XSS abuse prevention & validation tests
    ├── test_distributed_lock.py    # Multi-worker lock contention tests
    ├── test_risk_engine.py         # Multi-agent scoring & zero-drift consistency
    └── test_api.py                 # API integration and regression tests
```

---

## 3. Quickstart with Docker Compose

The fastest way to spin up the entire application stack (MongoDB + FastAPI + React Frontend) locally:

```bash
docker compose up --build
```

- Web Dashboard: `http://localhost:7860`
- API Docs: `http://localhost:7860/docs`
- Health Check: `http://localhost:7860/api/health`

---

## 4. Local Development Setup

### Prerequisites
- Python 3.11+
- Node.js 20+ & npm
- MongoDB 7.0+ (or MongoDB Atlas)

### Backend Setup
```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run database seeder
python -m backend.seeder

# Start FastAPI development server
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

---

## 5. Security & RBAC Model

JanNidhi Sentinel implements a multi-tier, zero-trust security architecture:

1. **Server-Side Role Authority**: Roles are exclusively resolved from the database document upon login. Client-asserted `target_role` injection has been completely removed.
2. **Cryptographic Authentication**: Passwords are saved with salted `PBKDF2-HMAC-SHA256` (100k iterations).
3. **Stateless JWT Tokens**: Authenticated sessions issue signed `HS256` JWT access tokens sent via standard `Authorization: Bearer <token>` headers.
4. **Brute-Force Protection**: 5 consecutive failed attempts trigger a 15-minute IP/user lockout.
5. **Citizen Endpoint Hardening**: Public feedback submissions validate base64 proof images (≤ 2 MB), reject remote URLs (SSRF prevention), and sanitize HTML tags (XSS protection).
6. **Multi-Worker Concurrency**: Ingestion runs are guarded by an atomic, distributed MongoDB lock with automatic TTL expiration.e fallback `sqlite:///./mplads_sentinel.db` during local development).*

---

## 5. How to Configure Environment Variables

Copy the example template:
```bash
cp .env.example .env
```
Key configuration parameters:
- `DATABASE_URL`: PostgreSQL connection string.
- `API_PREFIX`: `/api`
- `CORS_ORIGINS`: Allowed web client origins.
- Daily sync is automatic at **19:00 IST (7:00 PM IST)** (APScheduler cron); no configuration needed.

---

## 6. How to Seed the Database

On application startup, the backend automatically verifies if the `works` table is empty. If empty, it starts a live sync from the MPLADS dashboard in the background:
```bash
py -m backend.seeder
```
- **Idempotent:** Safe to run repeatedly; skips execution if records already exist.
- The API becomes available immediately while the initial live sync runs.

---

## 7. How to Start the Backend

Start the FastAPI application with Uvicorn:
```bash
py -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
API Documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 8. How to Start the Frontend

In a separate terminal:
```bash
cd frontend
npm run dev
```
Open your browser at `http://localhost:5173`.

---

## 9. How to Run the Ingestion Job Manually

Sync is **mode-based**. Trigger via HTTP (requires `MoSPI Reviewer` role):

```bash
# auto / live — pull fresh data from the MPLADS portal API (runs in the background)
curl -X POST "http://localhost:8000/api/sync/run?mode=live" -H "X-User-Role: MoSPI Reviewer"
```

The daily run happens automatically at **19:00 IST (7:00 PM IST)**. Every run writes exactly one
`sync_logs` entry (LIVE API badge) and the failure reason is preserved in the log.

---

## 10. How Scheduled Ingestion Works

1. **Scheduler:** An `APScheduler` cron job runs a **live sync automatically at 19:00 IST (7:00 PM IST)**
   every night. Manual triggers are available via `POST /api/sync/run?mode=live`, which
   starts the run in the background and returns immediately.
2. **Single source — the live portal:**
   - Data is fetched directly from the MPLADS dashboard's own REST endpoint
     (`mplads.mospi.gov.in/rest/PreLoginDashboardData/getTilesReportData`); houses selected
     via `MPLADS_LIVE_HOUSE` (default `both`). Implemented in `backend/services/mplads_live.py`.
   - Per-MP allotted funds from the portal's *Allocated Limit* dataset are stored in the
     `mp_allocations` table, powering Total Allocated / Fund Utilization / Expenditure Rate.
3. **Failure handling (staleness protection):**
   - If a sync fails, existing records are **never deleted**; the failure reason is written
     to `sync_logs` and the header badge shows **Data Stale** while the last-known-good
     dataset keeps serving.
   - Raw long-format feeds are reshaped and re-scored through `risk_engine.score_dataset()`.

### Remote feed configuration (`.env`)

The backend loads `.env` automatically (no extra dependency). For local/demo runs the
shipped Baseline Export is re-served over HTTP at
`GET /api/feeds/baseline-export.csv`, so the remote mode performs a genuine
fetch → parse → upsert cycle:

```env
REMOTE_INGESTION_URL=http://127.0.0.1:8001/api/feeds/baseline-export.csv
REMOTE_INGESTION_TIMEOUT=120
```

In production, point `REMOTE_INGESTION_URL` at the real official export host instead —
no code changes required. The Sync & Governance screen shows whether the remote feed is
configured and tags every sync-log entry with its feed type.

---

## 6. Multi-Agent Risk Engine & Decision Support

The risk engine is located in `model/risk_engine.py` and coordinates five specialist audit agents:

1. **Vendor Procurement Agent** (`model/agents/vendor_agent.py`):
   - Monitors state-level and MP-level vendor concentration, multi-MP billing networks, and monopoly bidding capture.
2. **Timeline & Execution Agent** (`model/agents/timeline_agent.py`):
   - Flags stagnant progress, impossible dates (completion before sanction), rapid completions (< 15 days), and post-completion payments.
3. **Financial Reconciler Agent** (`model/agents/financial_agent.py`):
   - Computes statistical Median Absolute Deviation (MAD) cost outliers, disbursement-to-payment reconciliation gaps, and fund over-allocation.
4. **Duplicate & Ghost-Work Agent** (`model/agents/duplicate_agent.py`):
   - Employs rarest-token Jaccard similarity indexing to detect duplicate work descriptions within a state and cross-MP ghost projects.
5. **Regulatory Compliance Agent** (`model/agents/compliance_agent.py`):
   - Evaluates trust and society routing, missing vendor disclosures, and negative/zero sanction amounts.

---

## 7. Human Review Loop (Phase 5 Feedback)

Authorized auditors submit verification determinations via:
```http
POST /api/works/{work_id}/review
Authorization: Bearer <jwt_token>
```
**Allowed Outcomes:**
- `legitimate`: Valid documentation and physical progress verified on site.
- `data-quality issue`: Typo, portal date error, or reporting artifact.
- `irregularity`: Substantive procedural, guideline, or procurement cost violation.
- `confirmed fraud`: Fictitious ghost work, embezzlement, or duplicate billing.

Outcomes are permanently logged in MongoDB `review_logs` and update the work's active status.

---

## 8. Running Automated Tests

Run the complete test suite covering authentication, RBAC matrix, public reviews, distributed sync locks, risk models, and API integration:

```bash
pytest -v tests/
```

With test coverage reporting:
```bash
pytest --cov=backend --cov=model --cov-report=term-missing tests/
```
