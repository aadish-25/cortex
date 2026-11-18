# Cortex Intelligent Memory Management System

## 1. Existing Architecture Discovered
Before this redesign, Cortex had:
- **Short-term conversational memory**: `MemoryService` (`backend/services/orchestration/memory_service.py`) storing `conversations` and append-only `messages` in PostgreSQL with an 8,000-token rolling context window and cached `Conversation.summary`.
- **Authoritative user profile (Tier 1 Hot LTM)**: `memory_service.build_user_context_block(user)` injecting SQL `User` fields (`name`, `email`, `department`, `role`, `clearance_level`).
- **Qdrant-only archival memory (Tier 2 Cold LTM)**: `EpisodicMemoryService` storing points directly in Qdrant collection `cortex_user_memories` with `BAAI/bge-small-en-v1.5` (384d) embeddings.
- **Basic UI & API**: `GET`/`PATCH`/`DELETE /api/v1/chat/memories` and `frontend/src/pages/MemoriesPage.tsx`.

## 2. Problems Identified
1. **Per-turn blocking LLM retrieval gate**: `should_search_archival_memory()` invoked a remote Fast LLM (~300–800ms + rate-limit quota) on every turn just to decide whether to run a ~25ms local FastEmbed + Qdrant search.
2. **Memory coupled into `query_planner.py` (ADD-only)**: `LLMExecutionPlan` only supported single-fact `ADD`, lacked explicit `UPDATE`/`DELETE`/`RETRIEVE` commands, and falsely confirmed `"Memory Saved"` even when storage returned `None`.
3. **Destructive 0.85 deduplication**: Updates such as switching from MongoDB to PostgreSQL were either silently dropped or stored as contradictory active records.
4. **Prompt injection & privacy gaps**: `<user_preferences_and_context>` was not covered by prompt injection defense rules, and no secret/credential or sensitive-PII validation existed.
5. **No PostgreSQL source of truth**: No `status` (`active`/`superseded`/`deleted`), `supersedes_memory_id`, versioning, provenance, category/status indexes, or sync recovery on failed Qdrant writes/deletes.

## 3. New Architecture
Memory management is now decoupled into an independent service package (`backend/services/memory/`) integrated cleanly into the chat lifecycle:

```
User Message
  └─> Deterministic Explicit Command Detector (command_detector.py)
        ├─ Explicit ADD / UPDATE / SUPERSEDE / DELETE / RETRIEVE?
        │    ├─ MemoryManagerService parses structured ops (EXPLICIT_COMMAND_PROMPT_V1)
        │    ├─ PrivacyGuard validates & sanitizes content
        │    ├─ MemoryStore commits PostgreSQL -> invalidates Redis -> syncs Qdrant
        │    ├─ Emits SSE `memory_event`
        │    └─ Pure command -> returns factual backend-verified summary (bypasses RAG/DAG)
        │       Compound command -> forwards remaining_user_query to normal pipeline
        └─ Normal / Compound Query
             ├─ Zero-LLM-Cost Hybrid Retrieval:
             │    1. Redis-cached active `preference` memories (<3ms)
             │    2. Redis-cached active count fast-exit (<1ms when 0)
             │    3. Local ONNX FastEmbed + Qdrant search (~25ms, PG-verified active IDs)
             │    4. Broad-personal PG fallback (0 LLM calls) for indirect queries
             ├─ Main Cortex Pipeline (Rewrite -> Planner -> DAG Tools -> Sandboxed Synthesis)
             └─ Async Post-Turn Background Extraction & Conflict Reconciliation (1 LLM call, pre-filtered)
```

## 4. Backend Changes
- **`backend/models/memory.py`** *(new)*: `UserMemory` SQLModel table + `MemoryCategory`, `MemoryStatus`, `MemorySyncStatus`, `MemorySourceType`, and `normalize_memory_category()`.
- **`backend/models/__init__.py`**: Registered `UserMemory` and enums for automatic table creation in `init_db()`.
- **`backend/db/qdrant.py` & `backend/main.py`**: Added `user_id`, `category`, and `status` keyword payload indexes on `cortex_user_memories` and initialized the collection on FastAPI startup.
- **`backend/services/memory/memory_store.py`** *(new)*: Canonical PostgreSQL + Qdrant + Redis repository with optimistic concurrency (`MemoryVersionConflictError`), `supersede_memory()`, soft-delete, bulk delete, PG-verified `semantic_search()`, Redis preference/count caching + invalidation, and `backfill_qdrant_to_pg()`.
- **`backend/services/memory/privacy_guard.py`** *(new)*: Deterministic rejection of secrets/credentials (API keys, JWTs, passwords, DB credentials, payment cards) and sensitive personal categories (health, financial accounts, private children details, intimate info), plus XML prompt-boundary tag stripping.
- **`backend/services/memory/command_detector.py`** *(new)*: Zero-latency deterministic pre-filter for explicit `ADD`/`UPDATE`/`DELETE`/`RETRIEVE` commands, trivial message skipping, broad-personal cues, and background extraction gating.
- **`backend/services/memory/prompts.py`** *(new)*: Versioned (`v1`) prompts (`EXPLICIT_COMMAND_SYSTEM_PROMPT_V1`, `AUTO_RECONCILE_SYSTEM_PROMPT_V1`) with strict Pydantic schemas (`ExplicitMemoryCommandPlan`, `MemoryReconciliationPlan`).
- **`backend/services/memory/memory_manager.py`** *(new)*: Independent `MemoryManagerService` coordinating explicit commands, zero-LLM hybrid retrieval, and background extraction + conflict resolution.
- **`backend/worker/sweeper.py`**: Added `reconcile_user_memories()` retrying `pending_upsert`/`pending_delete` rows and purging orphan Qdrant vectors.
- **`backend/services/orchestration/query_planner.py`**: Removed memory extraction fields and instructions from `LLMExecutionPlan`.
- **`backend/services/orchestration/chat_service.py`**: Removed blocking per-turn LLM retrieval classifier, wired `memory_manager` into `stream_chat_turn()`, emitted `memory_event` SSE frames, bypassed DAG on pure memory commands, hardened `build_sandboxed_prompt()` to sandbox `<user_preferences_and_context>`, and scheduled `extract_and_reconcile_background()`.
- **`backend/services/orchestration/episodic_memory_service.py`**: Refactored into a thin compatibility facade over `memory_store` and `memory_manager` (single source of truth, no competing store).

## 5. Frontend Changes
- **`frontend/src/pages/MemoriesPage.tsx`**: Added manual "Add memory" modal, search input, category filter, status selector (`Active` / `Active + superseded` / `Superseded history`), multi-select bulk delete, scoped clear-category/clear-all confirmation modal, optimistic `expected_version` on edits, active/superseded status badges, and created/updated dates.
- **`frontend/src/chat/ChatPage.tsx`**: Added handling for the `memory_event` SSE frame and a compact `MemoryEventCard` showing backend-confirmed mutations with a link to `/memories`.
- **`frontend/src/styles.css`**: Added matching styles for the memory toolbar, bulk controls, status tags, and chat `memory-event-card`.

## 6. Database Model (`user_memories`)
| Column | Type | Purpose |
|---|---|---|
| `id` | `UUID` (PK) | Canonical ID (1:1 with Qdrant point ID) |
| `user_id` | `UUID` (FK `users.id`, indexed) | Strict user ownership |
| `content` | `TEXT` | Sanitized canonical memory text |
| `category` | `VARCHAR` (indexed) | `preference`, `tech_stack`, `project_context`, `goal`, `workflow`, `location`, `general` |
| `status` | `VARCHAR` (indexed) | `active`, `superseded`, `deleted` |
| `confidence` | `FLOAT` | `1.0` explicit/UI, `0.0–1.0` auto-extracted |
| `source_type` | `VARCHAR` | `explicit_chat`, `auto_extracted`, `manual_ui`, `backfilled` |
| `source_conversation_id` | `UUID` (FK, nullable) | Provenance conversation |
| `source_message_id` | `UUID` (FK, nullable) | Provenance message |
| `supersedes_memory_id` | `UUID` (FK self, nullable) | Lineage chain for superseded memories |
| `version` | `INT` | Optimistic concurrency version |
| `sync_status` | `VARCHAR` (indexed) | `synced`, `pending_upsert`, `pending_delete` |
| `expires_at` | `TIMESTAMP` (nullable) | Optional expiration timestamp |
| `created_at` / `updated_at` / `deleted_at` | `TIMESTAMP` | Audit timestamps |

## 7. API Endpoints (`/api/v1/chat/memories`)
All endpoints enforce authenticated `current_user.id` ownership (admins cannot read or delete other users' private memories):
- `GET /api/v1/chat/memories` — paginated list with `category`, `search`, `status` (`active|superseded|all`), `limit`, `cursor`.
- `GET /api/v1/chat/memories/search` — hybrid semantic + keyword search (`q`, `category`, `limit`).
- `POST /api/v1/chat/memories` — create validated memory (`422` on secret/privacy rejection).
- `GET /api/v1/chat/memories/{memory_id}` — single memory details (`404` if missing or owned by another user).
- `PATCH /api/v1/chat/memories/{memory_id}` — update text/category (`409` on `expected_version` conflict, `422` on privacy rejection).
- `DELETE /api/v1/chat/memories/{memory_id}` — soft-delete + Qdrant delete + Redis invalidation.
- `DELETE /api/v1/chat/memories` — delete by `category` or `delete_all=true`.
- `POST /api/v1/chat/memories/bulk-delete` — bulk delete by `memory_ids`, `category`, or `delete_all`.

## 8. Security & Privacy Measures
- Authenticated `user_id` is always derived from JWT (`current_user.id`), never from LLM output or request payloads.
- Same-department peers and admins have zero access to another employee's private memories.
- Secrets (API keys, JWTs, passwords, DB URLs with passwords, payment cards) and sensitive personal categories are rejected before persistence.
- `<user_preferences_and_context>` is explicitly treated as untrusted data in `build_sandboxed_prompt()` and boundary XML tags are stripped on write.
- Semantic search cross-checks Qdrant hits against active PostgreSQL rows so a transient Qdrant delete failure can never resurface deleted memories.

## 9. Verification & Test Results
- **Frontend**: `npm run build` (`tsc -b && vite build`) — **passed** (0 TypeScript errors, production bundle built).
- **Backend Unit & Integration Tests** (`uv run pytest`):
  - `tests/unit/test_memory_manager.py` — **14 passed** (covering all 27 explicit, auto-extraction, conflict resolution, privacy/isolation, retrieval, sweeper recovery, reliability, and SSE bypass scenarios).
  - `tests/unit/test_episodic_memory_service.py` — **2 passed**.
  - `tests/unit/test_chat_fixes.py` — **8 passed**.
  - `tests/unit/test_chat_service.py` — **5 passed**.
  - `tests/unit/test_query_planner.py` — **10 passed**.
  - `tests/unit/test_memory_service.py` — **7 passed**.
  - `tests/integration/test_chat_router.py` — **9 passed** (including full `/api/v1/chat/memories` CRUD, 422 secret block, 409 concurrency conflict, 404 cross-user isolation, search, and bulk delete).
- **Known pre-existing limitation noted**: `tests/unit/test_sweeper.py` (untouched by this work) references an undefined `async_session_factory` fixture; memory sweeper reconciliation (`reconcile_user_memories`) is directly tested and verified in `tests/unit/test_memory_manager.py`.

## 10. Migration & Deployment
- No manual Alembic step is required: `init_db()` automatically creates the `user_memories` table on startup via `SQLModel.metadata.create_all` (works on both PostgreSQL and SQLite).
- FastAPI lifespan automatically initializes Qdrant `cortex_user_memories` payload indexes (`user_id`, `category`, `status`).
- For existing deployments with legacy Qdrant-only points, invoke `memory_store.backfill_qdrant_to_pg(session, qdrant_client, user_id)` to import them idempotently into PostgreSQL.
