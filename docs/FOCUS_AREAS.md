# Strategic Focus Areas — Cortex (AAD-H1)

This document highlights the core architectural pillars, engineering innovations, and creative technical decisions implemented in Cortex. It serves as the primary technical showcase for engineering leadership, system architects, and technical evaluations.

---

## 1. RAG & Retrieval Quality Engine
*My primary focus area was architecting a zero-hallucination, high-precision retrieval pipeline tailored for nuanced enterprise documents.*

* **Section-Aware Multi-Format Parsing**: Extracts real markdown headers (`#`, `##`) and Word styles rather than relying on synthetic chunk boundaries.
* **Zero Fake Page Numbers (Decision D15)**: Strictly preserves physical page numbers for PDFs (`[Doc, p. 3]`) while citing exact section headings for continuous formats (`[Doc, Section: Meal Allowance]`).
* **Contextual Identity Header Stamping (Decision D6)**: Mathematically anchors every chunk by prepending `[Document: {title} | Version: {v} | Department: {dept} | Section: {heading}]` before embedding.
* **Sliding Window with Remainder Absorption**: Implements 500-character windows with 100-character overlap, automatically absorbing small trailing fragments (< 80 chars) to eliminate orphan stubs.
* **Parallel Hybrid Retrieval**: Executes Dense vector search (FastEmbed `BAAI/bge-small-en-v1.5`, 384d) and Sparse lexical search (BM25) simultaneously via `asyncio.gather()`.
* **Reciprocal Rank Fusion (RRF)**: Merges dense semantic hits and sparse keyword matches into a calibrated pool of ~25 candidate chunks.
* **Cross-Encoder Re-Ranking**: Scores candidates jointly with the query using a cross-encoder model to surface the top 4–5 most relevant passages.
* **Optuna-Calibrated Confidence Cutoff**: Aborts generation and returns safe fallbacks if the top reranker score is below the calibrated threshold (default 0.35).
* **Updating Policy Detection Probe (Decision D9)**: Distinguishes between nonexistent policies and policies locked in `UPDATING` state without leaking document existence.
* **Prompt Injection XML Sandboxing (Decision D13)**: Wraps all retrieved passages in `<retrieved_context>` XML tags with explicit untrusted data instructions.
* **Cross-Document Contradiction Presentation (Decision D14)**: Detects and presents conflicting active policies side-by-side with source timestamps instead of fabricating a synthetic compromise.

---

## 2. Conversational Engine, Memory & Chatbot UX
*Focused on delivering a fluid, context-aware chatbot interface with enterprise-grade short-term and long-term memory.*

* **Zero-Latency Turn 1 Fast Path**: Skips query rewriting entirely on Turn 1 (0ms latency penalty).
* **Targeted Anaphora & Coreference Rewriter (Turn 2+)**: Detects pronouns (*"it"*, *"those"*) and elliptical queries (*"What about Mumbai?"*), resolving them using a static 2-turn window (last 4 messages).
* **Single-Pass Corrective RAG (CRAG)**: Automatically reformulates and re-searches queries only when initial reranker confidence lands in the marginal/fuzzy zone ($0.15 \le \text{score} < 0.39$).
* **Live DAG Execution Trace**: Displays an animated, non-emoji status badge `[Step X/Y] {Stage}...` that expands on click and auto-collapses to `✓ Verified across authorized sources (Xms)`.
* **3-Chunk Context Sandwich Evidence Drawer**: Clicking an inline citation chip slides open a drawer displaying the cited passage (highlighted) sandwiched between dimmed preceding and succeeding context chunks.
* **In-Drawer Multi-Citation Scroll**: Smoothly scrolls within the active evidence drawer when jumping between citations from the same document.
* **Strict Security Data Masking**: Prevents non-admin users from viewing raw reranker scores, vector distances, point IDs, or restricted document metadata.
* **Built-in Rich Markdown & Table Rendering**: Fully supports native markdown tables, code syntax highlighting, and callout alerts.
* **Short-Term Memory Compaction**: Uses LangChain's `ConversationSummaryBufferMemory` to maintain raw recent buffers while auto-summarizing older turns.
* **Branched New Chat on Context Ceiling**: Prompts user to branch into a clean new session when token limits are reached, summarizing prior summaries and recent turns into a single master seed.
* **Long-Term Cross-Session Personalization**: Uses Mem0 to extract and retrieve persistent user profile facts (base office, department role, communication preferences) across sessions.
* **Dynamic Follow-Up Suggestions**: Generates exactly 2 clickable pill chips below responses that populate and submit on click.
* **Server-Sent Events (SSE) with Token Rehydration (Decision D17)**: Streams tokens in real-time with Redis Stream replay cache (`chat_stream:{id}`) supporting network reconnection via `Last-Event-ID`.

---

## 3. Security, 3D RBAC & Enterprise Governance
*Architected a zero-trust access control layer enforced at the vector storage level to eliminate data leaks.*

* **Mathematical 3D RBAC Pre-Filtering (Decision D3)**: Enforces `Role` $\times$ `Department` $\times$ `ClearanceLevel` as a boolean filter inside Qdrant before vector similarity traversal, making bypass via prompt injection impossible.
* **Uniform Fallback String Invariant**: Emits the byte-for-byte identical message (`"I couldn't find verified information in the documents you have access to."`) whether content is absent or restricted, eliminating enumeration attacks.
* **Instant Session Revocation via Token Versioning (Decision D18)**: Setting `token_version = -1` in PostgreSQL immediately terminates active JWTs across API requests and open SSE streams without Redis blacklist lookups.
* **Modular Administrative User Provisioning (Decision D23)**: Guards user creation and role escalation behind type-safe dependency sets (`USER_MANAGEMENT_ROLES = {Role.ADMIN}`).
* **Dual Login Identifier Support**: Enables authentication via either unique `username` or corporate `email` with single-query DB resolution.
* **Python 3.14 Safe Password Hashing**: Implements native direct `bcrypt` hashing with defensive 72-byte truncation, bypassing `passlib` compatibility bugs.
* **SHA-256 Byte Deduplication (Decision D5)**: Hashes incoming byte buffers before parsing, rejecting identical files with HTTP 409 Conflict.
* **Two-Phase Staged Version Flip (Decision D4)**: Inserts updated documents as `status = UPDATING` and `is_live = False`, keeping old chunks live until new chunks are fully indexed, locked via atomic SQL concurrency guards.

---

## 4. Agentic Orchestration, DAG Engine & Tooling
*Replaced rigid linear chains with a Directed Acyclic Graph (DAG) supporting conditional branching, parallel execution, and hierarchical tooling.*

* **Topological DAG Execution**: Orchestrates multi-node pipelines with conditional early exits, parallel branches, and synchronized joins.
* **Multi-Query Decomposition & Dependency Engine (Decision D24)**: Decomposes compound multi-task prompts into a DAG with explicit `depends_on` IDs; dependent tasks receive strictly upstream results, completely eliminating context pollution.
* **Hierarchical Tooling Architecture (Decision D26)**: Structures capabilities into 4 common core tools (`internal_knowledge_search`, `external_web_search`, `sql_lookup`, `action_workflow`) containing discrete, typed sub-functions.
* **Strict "No Text-to-SQL" Relational Lookups**: Routes structured queries to bounded, role-isolated parameterized handlers (`get_my_tickets`, `get_ticket_status`) rather than allowing LLMs to generate arbitrary SQL.
* **Fast LLM Web Search Sanitizer (Decision D25)**: Replaces brittle domain whitelisting with an ultra-fast guardrail model inspecting retrieved web snippets for prompt injections before synthesis.
* **Outbound Search De-Identification**: Automatically strips internal project names, company codenames, and employee PII before querying external search APIs.
* **Autonomous Admin Operational Agent**: Equips administrators with elevated tools (`inspect_audit_logs`, `get_document_status`, `trigger_reindex`) to diagnose ingestion pipelines and inspect system health.
* **Human-in-the-Loop (HITL) Action State Machine (Decision D12)**: Stages actions with preview payloads, single-use `draft_id`, and a strict 15-minute countdown timer enforcing idempotent confirmation.

---

## 5. Backend Engineering, Data Architecture & Observability
*Built on modern asynchronous Python principles with strict separation of concerns and end-to-end tracing.*

* **FastAPI Modular Service Layer**: Decouples API endpoints (`routers/`), business rules (`services/`), database entities (`models/`), and validation contracts (`schemas/`).
* **Single Source of Truth Configuration**: Strict separation between runtime secrets (`.env`) and immutable architectural constants (`backend/config.py`).
* **Dual Database Engine Architecture**: Uses SQLModel with `aiosqlite` for rapid, zero-dependency local development and `asyncpg` for production PostgreSQL.
* **Zero-PII Compliance Audit Trail (Decision D21)**: Writes immutable append-only records (`user_id`, `event_tag`, `status_code`, `timestamp`) for all auth events, uploads, and queries without storing personal user queries.
* **Bidirectional Vector-Relational Reconciliation Sweeper (Decision D8)**: Background Celery beat task guarded by Redis distributed locks (`reconcile_lock`) that purges orphaned Qdrant points and identifies un-indexed documents.
* **LangSmith DAG Observability**: Implements lightweight `@traceable` decorators tracking end-to-end latency, token consumption, and intermediate step scores across every DAG node.

---

## 6. External MCP Ecosystem Integration
*Connects Cortex securely to enterprise platforms using the standardized Model Context Protocol (MCP) as a client.*

* **GitHub MCP Client (User-Scoped OAuth)**: Enables engineering staff to query repository pull requests, commit diffs, and issue trackers strictly within repositories where they hold collaborator or owner permissions.
* **Google Workspace / Drive MCP Client (Folder & User Scoped)**: Integrates with Google Drive API's free tier, enforcing granular folder-level scoping (Shared Drives) and user OAuth token pass-through to guarantee zero cross-department data leakage.
