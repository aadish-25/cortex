# Production Deployment Guide — Cortex (AAD-H1)

This document outlines the production architecture, service topology, environment configurations, and deployment procedures for Cortex.

---

## Target Platform: Railway Architecture

The recommended production deployment target is [Railway](https://railway.com), orchestrating all application components within a single project using Railway's high-speed private networking (`*.railway.internal`).

```
                    ┌────────────────────────────────────────┐
                    │               Internet                 │
                    └───────────────────┬────────────────────┘
                                        │ (HTTPS / TLS)
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
             ┌─────────────────────┐         ┌─────────────────────┐
             │   cortex-frontend   │         │     cortex-api      │
             │   (React SPA / CDN) │         │  (FastAPI / Uvicorn)│
             └──────────┬──────────┘         └──────────┬──────────┘
                        │ CORS                          │
                        └───────────────────────────────┤ (Private Network)
                                                        ▼
         ┌───────────────────┬───────────────────┬───────────────────┐
         ▼                   ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│    cortex-db    │ │  cortex-redis   │ │  cortex-qdrant  │ │  cortex-worker  │
│ (PostgreSQL 16) │ │ (Redis 7 Cache) │ │ (Vector Engine) │ │ (Celery Worker) │
│ [Managed Plugin]│ │ [Managed Plugin]│ │ [Docker Volume] │ │ [Same Codebase] │
└─────────────────┘ └─────────────────┘ └────────┬────────┘ └────────┬────────┘
                                                 │                   │
                                                 │                   ▼
                                                 │          ┌─────────────────┐
                                                 │          │   cortex-beat   │
                                                 │          │ (Celery Beat)   │
                                                 │          └────────┬────────┘
                                                 │                   │
                                                 ▼                   ▼
                                          ┌─────────────────────────────┐
                                          │     Persistent Storage      │
                                          │ /qdrant/storage & /storage  │
                                          └─────────────────────────────┘
```

---

## Service Topology Breakdown

| Service | Railway Type | Build / Image | Start Command | Persistent Storage |
|---|---|---|---|---|
| **`cortex-api`** | Web Service | Python (`Dockerfile` or buildpack) | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` | Mount `/app/storage` (uploaded docs) |
| **`cortex-frontend`** | Web Service | Node.js (or static Docker/Nginx) | `npm run build && npx serve -s dist -l $PORT` | None (stateless) |
| **`cortex-worker`** | Worker Service | Python (same repo as API) | `celery -A backend.worker.celery_app worker --concurrency=1 --max-tasks-per-child=50 --loglevel=info` | Mount `/app/storage` (document reindexing) |
| **`cortex-beat`** | Worker Service | Python (same repo as API) | `celery -A backend.worker.celery_app beat --loglevel=info -s /app/storage/celerybeat-schedule` | Mount `/app/storage` (scheduler state) |
| **`cortex-db`** | Database Plugin | PostgreSQL 16 | Managed by Railway | Managed volume |
| **`cortex-redis`** | Database Plugin | Redis 7 | Managed by Railway | Managed volume |
| **`cortex-qdrant`** | Docker Service | `qdrant/qdrant:latest` | Default image entrypoint | **Required**: `/qdrant/storage` |

---

## Persistent Volumes (Critical for Production)

Data loss will occur across redeployments unless persistent volumes are attached to the following services:

1. **`cortex-qdrant`**:
   - **Mount Path**: `/qdrant/storage`
   - **Purpose**: Stores Qdrant vector collections (`cortex_documents`, `cortex_user_memories`) and payload indexes.
2. **`cortex-api` & `cortex-worker`**:
   - **Mount Path**: `/app/storage`
   - **Purpose**: Stores original uploaded documents under `/app/storage/documents/` for on-demand reindexing and Celery Beat schedule database (`/app/storage/celerybeat-schedule`).

---

## Environment Variables Configuration

Set these variables in the Railway project dashboard (shared environment or per service):

### 1. Networking & Database Connections
```ini
ENVIRONMENT=production
# Frontend CORS allowlist (include both the production domain and local test domains)
FRONTEND_ORIGINS=https://cortex.up.railway.app,https://yourcustomdomain.com

# Database Connection (Note: SQLAlchemy asyncpg driver prefix)
# Railway provides DATABASE_URL=postgresql://...; append '+asyncpg'
DATABASE_URL=postgresql+asyncpg://${{cortex-db.PGUSER}}:${{cortex-db.PGPASSWORD}}@${{cortex-db.RAILWAY_PRIVATE_DOMAIN}}:5432/${{cortex-db.PGDATABASE}}

# Redis Connection (Broker, Result Backend, SSE replay buffer, Prefs cache)
REDIS_URL=${{cortex-redis.REDIS_URL}}

# Qdrant Vector DB (Private network)
QDRANT_URL=http://${{cortex-qdrant.RAILWAY_PRIVATE_DOMAIN}}:6333
QDRANT_HOST=${{cortex-qdrant.RAILWAY_PRIVATE_DOMAIN}}
QDRANT_PORT=6333
QDRANT_API_KEY=
```

### 2. Security & Authentication
```ini
# Generate with: openssl rand -hex 32
JWT_SECRET_KEY=generate_a_secure_minimum_32_char_hex_key_here
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Pre-defined admin password for first-time seeding
CORTEX_BOOTSTRAP_ADMIN_PASSWORD=SetAStrongAdminPasswordHere123!
```

### 3. LLM Gateway & Provider API Keys
```ini
# Primary Model Configuration (LiteLLM compatible)
DEFAULT_LLM_PROVIDER=gemini
DEFAULT_LLM_MODEL=gemini/gemini-2.5-flash
FAST_ROUTING_MODEL=gemini/gemini-2.5-flash
SYNTHESIS_MODEL=gemini/gemini-2.5-flash

# Active Provider API Keys
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
```

### 4. Optional Integrations (Phase 6 Connectors & JSM)
```ini
# External Read-Only MCP Connectors (Optional)
GITHUB_API_TOKEN=
GITHUB_DEFAULT_REPO=
GOOGLE_DRIVE_API_KEY=
GOOGLE_DRIVE_ROOT_FOLDER_ID=

# Jira Service Management (Optional)
JIRA_BASE_URL=
JIRA_API_EMAIL=
JIRA_API_TOKEN=
JIRA_DEFAULT_PROJECT_KEY=ITSD
```

### 5. Frontend Environment Variables (`cortex-frontend`)
```ini
# Base URL for API requests
VITE_API_BASE_URL=https://cortex-api.up.railway.app
```

---

## First-Time Initialization & Bootstrapping

Once the services are deployed and healthy:

1. **Database Schema Auto-Creation**:
   FastAPI's startup lifespan executes `init_db()`, which automatically runs SQLModel metadata reflection to create all tables (`users`, `conversations`, `messages`, `user_memories`, `documents`) and alter necessary columns.
2. **Qdrant Collection & Index Initialization**:
   FastAPI's startup lifespan calls `init_qdrant()`, creating `cortex_documents` and `cortex_user_memories` with 384-dimension cosine distance configurations and keyword payload indexes (`user_id`, `status`, `category`).
3. **Admin User Seeding**:
   Run the seeding script via Railway CLI or one-off deployment command:
   ```bash
   railway run --service cortex-api python scripts/seed_users.py
   ```
   If `CORTEX_BOOTSTRAP_ADMIN_PASSWORD` is defined, the admin account (`admin@cortex.internal`) will use that password. In non-production environments, demo accounts (`manager_eng`, `employee_eng`, etc.) are also seeded.

---

## Health Checks & Zero-Downtime Deployments

- **API Liveness / Readiness Endpoint**:
  ```http
  GET /health
  ```
  Returns `{"status": "ok"}` with HTTP 200.
  - Set Railway Health Check path to `/health`.
  - Set Health Check timeout to `30` seconds.

- **Graceful Shutdown**:
  FastAPI and Uvicorn catch `SIGTERM` and finish inflight SSE streams before exiting.
  Celery worker handles `SIGTERM` with `task_reject_on_worker_lost=True` and `task_acks_late=True` to requeue uncompleted indexing jobs in Redis without data loss.

---

## Resource Sizing & Worker Tuning

- **API Web Service**: 0.5 – 1 vCPU, 512MB RAM.
- **Worker Service**: 0.5 – 1 vCPU, 512MB – 768MB RAM.
  - Thanks to PEP 562 lazy loading, Celery worker idle footprint is only **~77 MiB**.
  - `--concurrency=1` and `--max-tasks-per-child=50` prevent any memory bloat during heavy document parsing.
- **Qdrant**: 0.5 – 1 vCPU, 512MB RAM (scales with vector count).
- **PostgreSQL & Redis**: Minimal tier handles standard organizational loads.
