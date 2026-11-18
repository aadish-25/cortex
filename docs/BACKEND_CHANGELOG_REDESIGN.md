# Backend change inventory — frontend redesign and backend remediation

**Scope:** This document records the backend implementation changes made while reviewing the updated Cortex archive and rebuilding its frontend. It separates changes to existing behaviour from newly added endpoints/models and records verification limitations. It is intended as the complete change inventory for this pass.

## 1. Authentication, account lifecycle and authorization

### `backend/services/auth/auth_service.py`
- Changed session revocation from setting `token_version = -1` to monotonically advancing the version (`max(1, current_version) + 1`).
- This prevents a reactivated account from returning to a token version that an older JWT already contains.
- Updated the revocation documentation to describe version advancement rather than the old `-1` sentinel behaviour.
- Kept `-1` only as the inactive self-registration sentinel; activation advances it to a positive version.
- Removed identifying login details from audit event text. Failed-login audit records use a generic message and an internal user ID only when the account was found.

### `backend/routers/users.py`
- Account activation/deactivation now advances `token_version` whenever `is_active` changes, invalidating existing sessions on both transitions.
- User listing is paginated and supports search, role, department and active-state filters.
- The response includes `items`, `page`, `page_size`, `total`, and `total_pages`.
- User audit details use target user IDs and authorization metadata instead of names/emails.
- Removed a duplicated count-query assignment.

### `backend/routers/auth.py` and `backend/core/security.py`
- Authentication remains cookie-based; logout deletes the cookie using the same path, HttpOnly, Secure-environment and SameSite settings used when it was created.
- CORS is restricted to configured frontend origins and credentials are enabled for the cookie session.
- Invalid/malformed JWT decoding is handled as an authentication failure rather than leaking a parsing exception.
- Registration remains an approval flow: self-registered accounts are inactive until an administrator activates them.

### `scripts/seed_users.py`
- Removed the fixed `admin/admin123` bootstrap credential.
- In development, when no admin exists and no password is configured, the seed script generates a one-time random admin password and prints it once.
- In production, the script refuses to create the initial admin unless `CORTEX_BOOTSTRAP_ADMIN_PASSWORD` is set.
- Production seeding is restricted to the administrator account; demo manager/employee accounts are seeded only outside production.

## 2. Database schema and safe startup migrations

### `backend/db/database.py`
- Existing PostgreSQL databases receive additive migrations for:
  - `documents.storage_path`;
  - `messages.is_complete`.
- Existing SQLite databases receive equivalent `PRAGMA table_info` checks and additive `ALTER TABLE` operations.
- Existing duplicate feedback rows are deduplicated before adding the unique `(message_id, user_id)` feedback index.
- The migration creates `uq_message_feedback_user` so one user has one current rating per assistant message.
- New SQLModel tables are created through metadata on startup:
  - `query_quality_logs`;
  - `document_processing_logs`.

**Migration limitation:** this project still uses additive startup migrations rather than Alembic revision files. The new startup migration handles the current PostgreSQL and SQLite configurations; a deployment using another database dialect needs a proper migration for these columns/indexes.

## 3. Chat, conversation history, citations and feedback

### `backend/routers/chat.py`
- Conversation listing is paginated and scoped to the authenticated user. Search is applied to conversation titles.
- Conversation message history is paginated; page 1 returns the newest messages, while each page is returned in chronological order for rendering.
- Added explicit conversation ownership validation before starting a chat turn.
- Feedback submission verifies that the target message is an assistant message in a conversation owned by the current user.
- Feedback is an upsert per `(message_id, user_id)`, so a second rating updates the existing record rather than creating duplicates.
- Added the regeneration endpoint `POST /api/v1/chat/messages/{assistant_message_id}/regenerate`.
- Regeneration resolves the originating user message, assigns a new `variant_index`, links the assistant variant through `parent_message_id`, and does not append a duplicate user turn.
- Added paginated personal-memory listing and user-scoped deletion endpoints.
- Evidence drawer access now accepts an opaque signed evidence reference instead of exposing raw Qdrant point IDs.
- Evidence access rechecks current user ownership, live document state, department and clearance before returning a cited chunk and its adjacent chunks.
- Chat message responses include `parent_message_id` and `is_complete` so the frontend can group variants and retain interrupted-response state.

### `backend/services/orchestration/chat_service.py`
- The final synthesis prompt now receives the compacted recent conversation history, not just the query planner. Follow-up questions therefore retain dialogue context at the answer-generation stage.
- Archival-memory vector lookup is gated to prompts that indicate personal context (e.g. `my`, `me`, `remember`) rather than running for every generic policy question. The memory service's similarity threshold remains in force.
- Regeneration applies the PRD D19 changes: synthesis temperature increases from `0.2` to `0.4`, and the retrieval candidate count increases from 5 to 7.
- The answer stores structured citation data: citation list, anchor-to-source map, and opaque evidence references. This makes citation/source relationships available after a conversation is reloaded.
- Evidence payloads sent to the browser exclude raw Qdrant point IDs, department metadata and scoring internals.
- Admin users receive a `corpus_gap` SSE event when an operational query returns LOW/FUZZY confidence.
- Mid-stream failures are persisted as incomplete messages and emit `interrupted`, not `done`. The new `messages.is_complete` field preserves that state after reload.
- Session revocation during a stream emits `session_terminated`; the Redis replay terminal-event set recognizes it.
- Query-quality telemetry is recorded after orchestration using a de-identified query preview, confidence, reranker scores and fallback status.

### `backend/services/orchestration/memory_service.py`
- Message persistence accepts structured citation data and records the originating user-message ID, variant index and completion state.
- Conversation lookup now rejects a supplied conversation ID that does not belong to the requesting user instead of silently creating a new conversation.

### `backend/models/chat.py`
- Added `Message.is_complete` (defaults to true).
- Retained `parent_message_id` and `variant_index` for assistant variants.
- Added a unique feedback constraint on message/user.
- Added the `ADMIN_OPERATION` task type for explicitly requested administrative chat tools.

## 4. SSE replay and stream lifecycle

### `backend/db/redis.py`
- Added a live-tail replay iterator for Redis Streams using `Last-Event-ID`.
- Replay reads missed events and continues waiting for newly appended events rather than doing a one-shot read and returning while generation is still active.
- The iterator recognizes `done`, `error`, `interrupted` and `session_terminated` as terminal events.
- Expired/unavailable stream IDs return an explicit non-retryable error; a stream that goes idle after replaying events returns a retryable `STREAM_IDLE` event.
- Existing stream TTL remains 10 minutes.

### `backend/services/orchestration/chat_service.py` and `backend/routers/chat.py`
- The client supplies a stable assistant message ID for the initial POST.
- Reconnect requests replay the existing stream and return before re-appending the user message or restarting LLM generation.
- The API remains a POST SSE endpoint; the new frontend uses `fetch` streaming so it can send the JSON body, cookies and `Last-Event-ID` header together.

## 5. Citation/evidence security and source rendering

### `backend/core/evidence_tokens.py` (new)
- Added HMAC-signed opaque references containing the Qdrant point ID, owning user ID and expiry.
- References are user-bound and default to a 30-day lifetime so citations in older conversation history remain usable.
- Token length, signature, owner and expiry are validated before Qdrant lookup.

### `backend/routers/chat.py`
- Evidence retrieval rejects invalid/expired tokens and performs a fresh RBAC/live-state check.
- The endpoint returns the cited chunk plus preceding/succeeding context when available.
- Raw Qdrant point IDs are not returned to the browser.

### `backend/services/vector/vector_service.py`
- New vector payloads retain document ID and chunk index metadata required to fetch adjacent chunks.
- Existing document-level RBAC filters remain applied at retrieval time.

### `backend/services/orchestration/dag_orchestrator.py`
- Citation anchors are now the canonical source ordering.
- External web sources receive anchors in the same citation map as internal document sources, preventing external citations from shifting the UI's internal evidence anchors.
- The final citation list is derived from the anchor map rather than a separately accumulated list that could be out of order.

## 6. DAG planning and concurrent execution

### `backend/services/orchestration/dag_orchestrator.py`
- Added strict topological-layer resolution and validation for missing dependencies and cycles.
- Independent tasks execute concurrently with isolated `AsyncSession` instances. SQLAlchemy `AsyncSession` is not shared between concurrently running tasks.
- Dependent tasks run only after their declared dependencies complete.
- Retrieval `top_k` is configurable per orchestration turn and is raised to 7 for regeneration.
- Admin operational tasks are routed to the admin tool catalog and return a formatted operational response.

### `backend/services/orchestration/query_planner.py`
- The authenticated role is passed into planning.
- An explicit Admin-only fast path recognizes system/service health, audit log, document status, reindex and retry-ingestion requests.
- The structured planning schema documents `ADMIN_OPERATION` as a privileged task type.
- Non-admin planning prompts explicitly prohibit this task type; the tool layer independently enforces the Admin role even if a planner returns it unexpectedly.

## 7. External search security and reliability

### `backend/services/orchestration/web_search_service.py`
- Outbound query de-identification now removes emails, Indian phone numbers, PAN-like IDs, Aadhaar-like 12-digit identifiers, employee IDs, street addresses and internal Cortex codenames.
- Added a batched lightweight LLM sanitizer pass for inbound web snippets using the fast routing model at temperature 0.0.
- The sanitizer is instructed to remove prompt-injection directives, hidden HTML/Markdown exploits and malicious URL schemes while preserving factual content.
- Deterministic regex/HTML escaping remains the fallback when no LLM provider is configured, the sanitizer fails, or its output is invalid.
- Search-result URLs are normalized and restricted to HTTP/HTTPS before they are exposed as citations.
- Removed the fabricated `verified-external-source.org` fallback. Empty search results now remain empty and are represented as an explicit unavailable state.
- Sanitized snippets are wrapped in `<untrusted_web_source>` XML before synthesis.

## 8. Admin operational tools and telemetry

### `backend/services/admin/admin_agent.py`
- Connected the existing admin tool catalog to chat through a role-gated query dispatcher.
- Explicit supported chat operations: system health, audit log inspection, document status inspection and document reindex.
- Document reindex requires a document UUID; document status accepts a UUID or filename.
- Non-admin attempts are refused and audited.
- Removed decorative emoji from admin tool responses.

### `backend/services/admin/admin_service.py`
- Health telemetry now includes Celery worker responsiveness in addition to PostgreSQL, Redis and Qdrant.
- LLM provider key configuration is reported separately as `CONFIGURED`/`NOT_CONFIGURED`; it is not misrepresented as a live provider health probe.
- Health error details are generic and do not echo raw connection strings or exception text.
- Query analytics now read from the dedicated query-quality ledger rather than audit events that were not being written for every chat turn.
- Added quality analytics: query count, fallback count/rate, low-confidence count and average reranker score.
- Added a paginated low-confidence query review queue and a mark-reviewed operation.
- Added paginated document processing log retrieval.
- Added document rollback to the most recent previous version, synchronizing SQL `is_live` state with Qdrant payloads.
- Reindex requests now require a retained source file, quarantine current vectors before rebuilding, and leave a retryable failure state if the queue is unavailable.
- Replacement uploads are validated, stored, staged and queued for indexing. Queue failure is recorded as `UPDATE_FAILED` so an administrator can retry.

### `backend/routers/admin.py`
- Added `/api/v1/admin/analytics/quality`.
- Added `/api/v1/admin/quality/low-confidence` with pagination and reviewed/unreviewed filtering.
- Added `/api/v1/admin/quality/low-confidence/{quality_id}/review`.
- Added `/api/v1/admin/documents/{doc_id}/logs` with pagination.
- Added `/api/v1/admin/documents/{doc_id}/rollback`.
- Kept separate resource-specific admin endpoints for health, audit, document reindex and replacement.

## 9. Document source retention, reindex and rollback lifecycle

### `backend/models/document.py`
- Added nullable `storage_path` so the original upload can be recovered for reindexing.

### `backend/models/document_log.py` (new)
- Added append-only per-document processing logs with stage, status, message and timestamp.

### `backend/services/ingestion/document_service.py`
- Added `persist_document_file`, which writes originals under a generated UUID filename and never trusts the uploaded filename as a filesystem path.
- Added processing-log recording.
- Document activation now deactivates older-version Qdrant payloads as well as SQL rows. This prevents old versions from remaining searchable after a new version becomes active.
- Failed processing marks SQL state failed and attempts to quarantine that document's Qdrant vectors.
- Failure logs redact common email/phone/local Windows path patterns.
- Document deletion removes associated processing logs and the retained source file after the database operation succeeds.
- Reindex requests fail early when the original source file is missing rather than marking the document updating and then failing later.

### `backend/routers/documents.py`
- New uploads persist the original file before parsing/indexing.
- Added a 25 MB upload limit.
- Successful upload activation and processing start/completion are logged.
- Document listing is paginated and supports search/status/department filtering with role-aware visibility.

### `backend/worker/tasks.py`
- Replaced the placeholder reindex task with a real parse → chunk → vector index → activation workflow.
- Reindex uses the retained source file and indexes into a non-live state before activation.
- Same-version vector points are removed before a reindex to avoid duplicate points.
- The new version is activated only after successful indexing; previous version vectors are then marked non-live.
- Processing stages are logged, failures are marked and retried through Celery, and failed vectors are quarantined.
- Celery Beat continues to run the reconciliation task.

### `backend/services/admin/admin_service.py`
- Replacement upload retains the new source file and dispatches the worker.
- Reindex queue failures and replacement queue failures are recorded in the processing log and leave a state that can be retried.
- Rollback reactivates the previous SQL document row and Qdrant points, and deactivates the failed/current version.

## 10. Query-quality telemetry

### `backend/models/query_quality.py` (new)
- Added a query-quality ledger with user/conversation references, de-identified query preview, confidence, reranker score(s), fallback flag, review state and timestamp.

### `backend/services/orchestration/chat_service.py`
- Persists quality telemetry after orchestration. The stored preview passes through the outbound de-identification filter and is truncated.

### `backend/services/admin/admin_service.py` and `backend/routers/admin.py`
- Added aggregate metrics and the low-confidence review queue.
- Query previews are only returned to Admin endpoints.

## 11. Operational launcher and configuration

### `run.py`
- Creates `.env` from `.env.example` when absent.
- Uses `uv sync --frozen` when `uv` is installed; otherwise uses an existing virtual environment or checks/installs project dependencies with pip.
- Installs frontend dependencies on first run.
- Starts Docker infrastructure, FastAPI, Celery worker, Celery Beat and Vite.
- Monitors all launched processes and shuts the stack down if a core process exits.
- Handles Windows `npm.cmd` invocation through `cmd /c`.

### `.env.example`
- Replaced archive-specific placeholder markers with safe, explicit local-development settings.
- Added `FRONTEND_ORIGINS` and `CORTEX_BOOTSTRAP_ADMIN_PASSWORD`.
- Contains no actual provider credentials.

## 12. Tests changed or added

- Updated chat/user/document route tests for paginated response shapes.
- Updated evidence tests for signed user-bound references and adjacent chunk lookup.
- Updated memory tests for cursor-paginated memory results.
- Updated token-version assertions for monotonic revocation/activation.
- Added tests for personal-memory lookup gating.
- Added DAG topological ordering, missing-dependency and cycle tests.
- Added evidence-token ownership/opacity tests.

## 13. Verification status and known limitations

- `python -m compileall -q backend tests run.py main.py scripts` completed successfully in the review workspace.
- The backend integration/unit test suite was **not executed**: `pytest` stopped during `tests/conftest.py` import because `sqlmodel` is not installed.
- `uv sync --frozen` was attempted, but the environment could not download the project's pinned Python 3.14 runtime because DNS/network access was unavailable.
- The frontend Vite/TypeScript production build was **not executed**: `npm install` could not download packages because DNS/network access was unavailable. A global `tsc` invocation was also inconclusive because React/Vite/React-Markdown type packages are not installed.
- The live Docker/PostgreSQL/Redis/Qdrant worker flow has not been exercised in this environment.
- Startup migrations are additive for SQLite/PostgreSQL, not a replacement for a versioned Alembic migration strategy.
- Documents that were uploaded before `storage_path` existed have no recoverable original file path. The reindex endpoint rejects those records with a clear 400 response; an administrator must replace/re-upload the source before reindexing can work.
- The LLM sanitizer falls back to deterministic regex/HTML escaping when provider configuration or sanitizer generation is unavailable. This fallback is deliberate so external snippets remain sandboxed and never become fabricated sources, but it is not equivalent to a successful LLM sanitizer pass.
- Outbound PII scrubbing covers common structured identifiers and addresses with regex. It is not a full named-entity-recognition system; do not treat it as a guarantee that every possible personal name is removed from arbitrary free text.

## 14. Follow-up correctness fixes discovered during implementation review

The following fixes were made after reviewing the newly connected UI flows end-to-end; they are included in the delivered backend, not just recommendations:

- Fixed the document failure handler to use its actual `error_message` argument and imported `DocumentProcessingLog` explicitly. This prevents a second `NameError` while trying to record a failed ingestion.
- Fixed the admin router's explicit imports for `Document` and `select`, which are required by the new document-log endpoint.
- A staged replacement no longer changes the currently-live prior version to `UPDATING`. The prior version remains active/searchable until the new version has indexed successfully; a failed replacement therefore does not make the last good version disappear.
- Finalization marks older versions non-live in Qdrant and SQL. Any legacy older row still marked `UPDATING` is normalized to `ACTIVE` (indexed, but not live).
- Reindex and replacement actions are restricted to the latest version of a document family. The API returns `is_latest` in document read responses so the frontend can disable invalid actions on older versions.
- Rollback now resolves the latest version for the filename even if the administrator clicked a different row. It restores the newest prior version whose processing status is `ACTIVE`, first marks all family vectors non-live, then activates exactly one prior version. This avoids leaving multiple versions live or accidentally restoring a failed intermediate version.
- Reindex queue failure is returned as HTTP 503 rather than a successful 202. Missing source files return 400; missing document IDs return 404; already-running work returns 409; Qdrant quarantine failure returns 503.
- Replacement upload validates file presence, extension and the 25 MB limit. If persisting the original replacement file fails after the new SQL version was created, that new version is marked failed and the previous live version is left intact.
- Reindex worker failures remain `UPDATING` while Celery retries are pending. The document is marked `UPDATE_FAILED` and its vectors are quarantined only after retries are exhausted. This avoids exposing a false retryable state while Celery is still retrying.
- Added processing-log stages for upload/replacement/reindex, parse, chunking, vector indexing, activation, retry, failure and rollback. Admins can inspect these through the paginated `/api/v1/admin/documents/{doc_id}/logs` endpoint.
- Reindex, replacement and rollback now emit audit events with document IDs and version numbers only.
- System health includes a bounded Celery worker ping; an offline worker marks overall service health degraded. Provider key presence is reported separately from live provider health.
- The bootstrap seed script no longer prints a new generated password when an admin already exists; production seeding is restricted to the bootstrap admin and requires an explicit environment password.
- The local launcher keeps Docker data containers running on Ctrl+C to preserve local Postgres/Redis/Qdrant data; only app processes are stopped.
- `DocumentRead` now includes `is_latest`. The document-list route computes the latest version per filename across the full dataset (not just the current page); the UI disables replace/reindex/rollback actions for superseded versions.
- Reindex and replacement services reject attempts against an older version. Replacement keeps the existing document filename as the stable version-family key and requires the replacement to use the same supported extension.
- Rollback restores the newest prior version whose status is `ACTIVE`; a failed intermediate replacement is skipped. A successfully indexed version that is rolled back remains `ACTIVE` but `is_live=false` rather than being incorrectly labelled failed.
- Reindex worker retries no longer mark a document failed before Celery has exhausted retries. `UPDATE_FAILED` is written only after the final attempt; intermediate attempts are logged as `RETRYING`.

## 15. Complete modified/new backend file index

This index covers every backend file that differs from the uploaded `cortex(1).zip` in the delivered archive. The sections above explain the behaviour; this index is here so the change set can be audited file-by-file.

| File | Change in this pass |
|---|---|
| `backend/core/config.py` | Added frontend-origin configuration and bootstrap-admin password setting. |
| `backend/core/security.py` | Corrected the malformed multi-exception JWT handler to valid Python 3 syntax. |
| `backend/core/evidence_tokens.py` | New signed, opaque, user-bound evidence reference implementation. |
| `backend/db/database.py` | Added SQLite/PostgreSQL additive migrations for storage path, message completion and feedback uniqueness; imports new SQLModel tables. |
| `backend/db/redis.py` | Added replay-and-tail SSE iterator with terminal event and idle handling. |
| `backend/main.py` | Removed wildcard CORS origin; uses configured explicit frontend origins with credentials. |
| `backend/models/__init__.py` | Registers the new pagination, query-quality and document-processing-log models. |
| `backend/models/chat.py` | Added completion state and unique user/message feedback constraint; added admin task type. |
| `backend/models/document.py` | Added original-file storage path and latest-version response flag. |
| `backend/models/pagination.py` | New generic paginated API response model. |
| `backend/models/query_quality.py` | New de-identified query-quality telemetry/review model. |
| `backend/models/document_log.py` | New document-processing history model. |
| `backend/routers/auth.py` | Added inactive self-registration approval flow and consistent cookie deletion attributes. |
| `backend/routers/users.py` | Added paginated/filterable user listing, activation session invalidation and audit-detail cleanup. |
| `backend/routers/documents.py` | Added paginated document listing, source-file persistence, latest-version metadata and upload-size enforcement. |
| `backend/routers/chat.py` | Added paginated conversation/message responses, regeneration, feedback upsert/ownership validation, memory APIs and secure evidence retrieval. |
| `backend/routers/admin.py` | Added quality review, document processing-log and rollback routes; corrected response/error handling for reindex/replacement operations. |
| `backend/services/auth/auth_service.py` | Updated session revocation documentation/behaviour to use monotonic token versions. |
| `backend/services/ingestion/document_service.py` | Added source-file persistence, processing logs, Qdrant failure quarantine, version activation and safe deletion handling. |
| `backend/services/admin/admin_agent.py` | Added role-gated operational chat dispatcher for supported admin tools; removed identifying log content. |
| `backend/services/admin/admin_service.py` | Added quality analytics/review, Celery health, processing logs, replacement/reindex/rollback lifecycle and audit events. |
| `backend/services/integrations/github_service.py` | Removed raw search-query logging. |
| `backend/services/integrations/google_drive_service.py` | Removed raw search-query logging. |
| `backend/services/orchestration/action_engine.py` | Removed user email/refusal text from operational logs. |
| `backend/services/orchestration/chat_service.py` | Added personal-memory gating, synthesis history, regeneration parameters, structured citations, quality telemetry and persisted interrupted state. |
| `backend/services/orchestration/dag_orchestrator.py` | Added topological DAG validation/layers, isolated concurrent sessions, configurable retrieval depth and stable internal/external citation anchors. |
| `backend/services/orchestration/episodic_memory_service.py` | Added cursor-paginated personal-memory listing and error propagation. |
| `backend/services/orchestration/memory_service.py` | Added conversation ownership enforcement and persistence of structured citations/variant/completion metadata. |
| `backend/services/orchestration/query_planner.py` | Added Admin-only operational intent planning and schema/prompt support. |
| `backend/services/orchestration/sql_lookup_service.py` | Removed raw SQL query/parameter logging. |
| `backend/services/orchestration/web_search_service.py` | Added outbound de-identification, batched inbound LLM sanitization, URL validation and removal of fabricated fallback sources. |
| `backend/services/retrieval/crag_service.py` | Removed raw query text from recovery logs. |
| `backend/services/retrieval/updating_policy_probe.py` | Replaced document title/username log fields with internal IDs. |
| `backend/services/vector/vector_service.py` | Added canonical document/chunk payload fields for evidence lookup and reconciliation. |
| `backend/worker/sweeper.py` | Removed document filename from reconciliation warning logs. |
| `backend/worker/tasks.py` | Implemented retained-source reindexing, stage logs, Qdrant activation/quarantine and retry-aware failure state. |

Non-backend files changed alongside this work include `run.py`, `docker-compose.yml`, `.env.example`, `README.md`, `scripts/seed_users.py`, the frontend source tree, frontend docs and relevant backend tests.
- Document version families and deduplication are scoped by `(filename, department)`, not filename globally. This prevents two departments from accidentally sharing a version chain or causing one department's activation/rollback to deactivate the other's vectors. The paginated `is_latest` calculation uses the same scope.

### Test files updated

| Test file | Coverage added/updated |
|---|---|
| `tests/integration/test_chat_router.py` | Paginated conversation/message response contracts, feedback ownership/upsert and regeneration route behaviour. |
| `tests/integration/test_document_routes.py` | Paginated document responses, upload source retention and admin document lifecycle routes. |
| `tests/integration/test_user_routes.py` | Paginated user filters and activation/deactivation token-version changes. |
| `tests/unit/test_auth_service.py` | Monotonic session revocation assertions. |
| `tests/unit/test_chat_fixes.py` | Evidence token ownership/opacity, archival-memory lookup gating and chat persistence changes. |
| `tests/unit/test_dag_orchestrator.py` | Topological ordering, missing dependency and dependency-cycle validation. |
- Startup migration also redacts legacy audit-log detail strings from failed logins, user creation/update and session-revocation events, so older rows written before the PII cleanup are not left exposed in the admin audit view.
- `DocumentRead` exposes a boolean `has_source_file` (never the local path itself). The admin document table disables reindex when the original source is absent and indicates that the record needs a replacement upload.
