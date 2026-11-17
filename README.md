# Cortex (AAD-H1) — Enterprise RAG & Operational Intelligence Assistant

Cortex is an enterprise-grade Autonomous RAG (Retrieval-Augmented Generation) and workflow execution assistant built with FastAPI, PostgreSQL, Qdrant, Redis, and LiteLLM.

---

## Key Architecture & Core Invariants

### 1. 3D RBAC Security Matrix (Decisions D1–D3)
Access control evaluates three orthogonal vectors on every retrieval request:
* **Role**: `Admin` > `Manager` > `Employee`
* **Department**: `Engineering`, `HR`, `Finance`
* **Clearance Level**: `Public` < `Internal` < `Confidential`

### 2. Zero-Write / Zero-Mutate Connector Invariant (Decision D39)
All external Model Context Protocol (MCP) integrations are strictly **read-only**:
* **GitHub MCP Client**: Scoped to `GET` operations (`search/code`, `issues`, `pulls`, raw file contents).
* **Google Drive MCP Client**: Scoped to `GET` operations (`files`, `export` to text/csv, `alt=media` downloads).
* ⛔ **Prohibited HTTP Methods**: `POST`, `PUT`, `DELETE`, and `PATCH` are strictly forbidden across external connectors during user conversational synthesis.

### 3. Explicit Connector Invocation Policy
* **Default Knowledge Retrieval**: Always routes exclusively to internal 3D RBAC RAG (`cortex_documents`).
* **Connector Isolation**: External connectors (`GITHUB_SEARCH`, `DRIVE_SEARCH`) are **never speculatively fanned out**. They trigger if and only if the employee explicitly requests Drive or GitHub in their prompt (e.g. *"in Google Drive"*, *"check GitHub repo for issues"*), preventing context rot and preserving API rate limits.

### 4. Database Ledger Immutability & Lazy Compaction (Decision D37)
* The PostgreSQL `messages` table is **100% append-only and immutable**.
* Context compaction happens purely in memory at inference time: when a conversation crosses 8,000 tokens, older turns ($1 \dots N-4$) are compressed into an executive summary, preserving the most recent 4 raw turns intact.
* Compaction uses lazy hysteresis and is cached on `Conversation.summary`.

### 5. Multi-Tier Operational Architecture
* **Hybrid Vector Retrieval**: Dense FastEmbed (`bge-small-en-v1.5`, 384d) + Sparse BM25 + Reciprocal Rank Fusion ($k=60$) + Cross-Encoder Neural Reranking (`ms-marco-MiniLM-L-6-v2`).
* **Jira Service Management (JSM)**: Universal 4-tier ticket engine (`INCIDENT`, `SERVICE_REQUEST`, `INQUIRY`, `DISPUTE`) with 15-minute Human-in-the-Loop ActionDraft state machine (Decision D12).
* **SSE Streaming Protocol**: Real-time event streaming (`step`, `token`, `citations`, `action_card`, `suggestions`, `done`) with Redis Stream replay buffer (`chat_stream:{message_id}`) for `Last-Event-ID` recovery (Decision D38).
* **Extensible Token-Bucket Rate Limiter**: Per-provider rate limiting with `Retry-After` header inspection and graceful degradation (Decision D27).

---

## Quick Start & Verification

### Running Tests
Execute the full test suite with the root test runner:
```cmd
.\test.bat
```
Run a specific test file or filter:
```cmd
.\test.bat tests/unit/test_query_planner.py
.\test.bat -k "auth"
```

### Verifying External Integrations
Verify live connectivity to GitHub, Google Drive, and Redis:
```cmd
python scripts/ping_integrations.py
```
