# Cortex Future Scope Roadmap

This document outlines architectural enhancements, user experience improvements, and platform capabilities planned for future iterations of Cortex Enterprise. Items are categorized by implementation complexity, technical dependencies, and estimated effort.

---

## 1. Doable & Near-Term Scope (High ROI, Well-Defined Implementation)

These features have clear architectural boundaries, minimal external dependencies, and can be implemented incrementally on top of the current schema and state management.

### 1.1 Conversation Tree Branching (ChatGPT-Style Message Forks)

#### The Problem
In standard linear chat interfaces, editing an earlier prompt or regenerating an assistant response either overwrites existing messages or creates an orphaned list of variants at a single position. If a user asks Prompt A, receives Response A, follows up with Prompt B and Prompt C, and then decides to regenerate or edit Response A, the subsequent conversation context (Prompts B and C) becomes invalid or disconnected.

#### How ChatGPT-Style Branching Works
ChatGPT models conversations as a **Directed Acyclic Graph (DAG) / Tree of Nodes**, where every message knows its immediate parent:
- **Root Node**: The initial user prompt (`parent_id = None`).
- **Turn Nodes**: Each assistant answer points to its prompt (`parent_id = prompt_id`).
- **Branch Creation**: When editing a prompt or regenerating a response at depth $k$:
  1. A new sibling message node is created with the same `parent_id` (depth $k-1$).
  2. The parent's `children` array now holds multiple node IDs (e.g., `[child_v1, child_v2]`).
  3. The conversation holds an **Active Branch Path Pointer** (or each parent remembers its `active_child_id`).
  4. Any subsequent messages (Prompt C, Prompt D) are appended as children of `child_v2`.
- **Branch Navigation**:
  - The variant switcher `< 1 / 2 >` changes the `active_child_id` at that node.
  - When switching back from variant 2 to variant 1, the UI traverses the active child path of variant 1, instantly revealing Prompts A and B while hiding Prompts C and D.

#### Proposed Implementation for Cortex
1. **Database Schema (`messages` table)**:
   ```sql
   ALTER TABLE messages ADD COLUMN parent_message_id UUID REFERENCES messages(id);
   ALTER TABLE messages ADD COLUMN active_child_id UUID REFERENCES messages(id);
   ```
2. **Backend Chat Endpoint**:
   - Update `list_messages` or `get_conversation` to return the tree or the active path nodes in chronological order:
     ```python
     # Traverse from root to leaf via active_child_id pointers
     active_path = []
     curr = root_message
     while curr:
         active_path.append(curr)
         curr = get_message(curr.active_child_id)
     ```
3. **Frontend Variant Switcher (`ChatPage.tsx`)**:
   - When clicking `<` or `>`, dispatch `PATCH /api/v1/chat/messages/{parent_id}/active-child` with `{ child_id: target_variant_id }`.
   - Re-render the message list with the newly active branch path.

---

### 1.2 In-Depth Admin Answer Quality Review Workflow

#### The Problem
Currently, the Admin "Answer Quality" view offers a binary "Mark Reviewed" button that clears items from the unreviewed queue. This does not provide review auditing, root-cause tagging, or feedback to the prompt engineering / retrieval tuning pipeline.

#### Planned Capabilities
1. **Review Annotation & Categorization**:
   - Provide categorical tagging: *Retrieval Failure (Missing Context)*, *Hallucination*, *Formatting/Tone Issue*, *Excessive Latency*, *Stale Document Version*.
2. **Ground Truth Correction Studio**:
   - Allow admins to draft the "Ideal Ground Truth Answer" directly beside the model's actual answer.
3. **One-Click Synthetic Eval Generation**:
   - Automatically export corrected question-answer pairs directly into the Qdrant retrieval regression test suite (`tests/evals/test_rag_quality.py`).
4. **Reviewer Audit Trail**:
   - Track `reviewed_by_user_id`, timestamp, and review status (`Pending`, `In Progress`, `Approved`, `Flagged for Retraining`).

---

### 1.3 Interactive Jira Ticket Canvas / Artifact Panels (Antigravity & ChatGPT Style)

#### The Problem
Currently, confirmed Jira tickets display a status confirmation card and offer a "View ticket" modal or external Atlassian link. While functional, standard modal dialogs interrupt the conversation stream and do not provide an ambient, persistent workspace.

#### How Antigravity / ChatGPT-Style Artifacts Work
Modern agentic interfaces decouple chat text from visual artifacts:
- **Slide-Out Split-Pane Canvas**: Clicking "View ticket" or mentioning an existing ticket key smoothly slides open a dedicated right-hand side panel while keeping the conversation active on the left.
- **Live Ticket Synchronizer**: The artifact dynamically subscribes to Jira status webhooks (`Open` $\to$ `In Progress` $\to$ `Resolved`), displaying a real-time progress track.
- **In-Canvas Interactive Actions**:
  - Add internal comments or additional notes without prompting the LLM.
  - Drag-and-drop new receipt or screenshot files directly onto the ticket canvas (uploading via Cloudinary).
  - One-click copy of Jira markdown formatted ticket blocks for Slack/Teams cross-posting.

---

### 1.4 RAG Source Diversity & Anti-Domination Pipeline (MMR & Per-Document Quotas)

#### The Problem: "Source Monopolization"
When executing enterprise RAG on broad or multi-faceted queries (e.g., *"What is our equipment reimbursement policy and how does it relate to remote work allowances?"*), a single large, keyword-dense document (Document A) often produces several consecutive or overlapping high-scoring chunks (e.g., ranks 1 through 5 scoring 0.88 down to 0.82). Meanwhile, a crucial secondary source (Document B) with a complementary rule scoring 0.80 is completely crowded out.
Consequently:
- The top-5 context window is monopolized by a single document.
- The synthesis LLM receives a one-sided context and may falsely conclude that no verified information exists for the secondary topic.
- Overlapping sliding-window chunks repeat redundant boilerplate text, wasting LLM context window tokens without adding novel facts.

#### Architectural Solution & Proposed Implementation
1. **Per-Document Quota / Round-Robin Diversity Selector**:
   - In `backend/services/retrieval/reranker_service.py`, after cross-encoder scoring, implement a diversity selector that caps any single `document_id` to at most $M$ chunks (e.g., $M = 2$) within the top $K$ (e.g., $K = 5$).
   - If Document A has chunks ranked #1, #2, #3, #4, the selector preserves #1 and #2, then skips to #5 (Document B) and #6 (Document C) to complete the final context.
   - Provides guaranteed multi-source coverage with deterministic $O(N)$ execution and 0ms latency overhead.
2. **Maximal Marginal Relevance (MMR) & Semantic Deduplication**:
   - For candidate chunks, compute pairwise embedding similarity against already-selected chunks.
   - Apply an MMR marginal novelty penalty:
     $$\text{Score}_{\text{MMR}}(c_i) = \lambda \cdot \text{Score}_{\text{CrossEncoder}}(c_i, q) - (1 - \lambda) \cdot \max_{c_j \in S} \text{Sim}(c_i, c_j)$$
   - Strongly demotes near-identical chunks ($\text{Sim} > 0.85$) in favor of passages that bring fresh facts or counter-clauses.
3. **Cross-Source Grounded Synthesis**:
   - Extend `build_sandboxed_prompt` to group retrieved chunks by document title in `<retrieved_context>` tags and enforce balanced attribution across all represented sources.

---

### 1.5 Jira Account Linking & First-Login Onboarding (User-Attributed Workflows)

#### The Problem: Default Service Account Attribution
Currently, when Cortex creates Jira tickets via the Atlassian REST API v3, it authenticates using the platform's system API token (`JIRA_API_EMAIL`). Because standard ticket requests specify metadata (`summary`, `description`, `priority`) without an explicit `reporter` account ID, Jira Cloud defaults the `reporter` field to the API token holder (`Aadish Sonawane`). Consequently, when employee `Aditi Khaparde` (`adzz.k@cortex.com`) instructs Cortex to raise an issue, the ticket appears in Jira as reported by Aadish Sonawane, obscuring organizational ownership and auditing.

#### Architectural Solution & Implementation
1. **First-Login Onboarding & Account Linking Flow**:
   - Introduce an onboarding step upon first user login (or in `SettingsPage.tsx` > Integrations) presenting a **"Connect your Atlassian Jira Account"** modal.
   - Provide a "Skip for now" option, accompanied by an ambient reminder banner when viewing engineering or incident workflows.
   - Users authorize via Atlassian OAuth 2.0 (3LO) or link their corporate Atlassian account email.
2. **User Profile Jira Mapping (`users` table)**:
   ```sql
   ALTER TABLE users ADD COLUMN jira_account_id VARCHAR(128);
   ALTER TABLE users ADD COLUMN jira_email VARCHAR(255);
   ```
3. **Dynamic Reporter Identification in Jira Service**:
   - In `backend/services/integrations/jira_service.py`, when drafting or confirming an issue:
     - Check if `current_user.jira_account_id` is cached in PostgreSQL.
     - If not cached but `current_user.personal_email` or `corporate_email` is available, query Jira Cloud's User Search API:
       ```
       GET /rest/api/3/user/search?query={user.email}
       ```
     - Inject the resolved `accountId` into the Jira ticket payload:
       ```python
       issue_dict["fields"]["reporter"] = {"accountId": resolved_account_id}
       ```
     - Cache the result in `users.jira_account_id` for instant zero-latency lookup on future actions.
4. **Contextual Fallback & In-Chat Connection Prompting**:
   - If an unlinked user instructs Cortex to *"raise a jira ticket"*, Cortex detects missing credentials and gracefully responds with an interactive prompt:
     > *"To file this ticket under your name in Jira, please connect your Atlassian account [Connect Jira](#onboarding). Alternatively, I can submit this on your behalf via the Engineering Service Proxy."*

---

### 1.6 Stop AI Response (Immediate Stream Interruption & AbortController)

#### The Problem
When the model begins generating a lengthy answer, an unexpected code dump, or a misdirected retrieval synthesis, users are currently forced to wait for full completion or manually refresh the web page. This wastes tokens, delays workflow execution, and causes UI frustration.

#### Planned Architecture & Capabilities
1. **Frontend Interruption Trigger (`ChatPage.tsx`)**:
   - Introduce an active state listener: while `isGenerating` is true, the primary "Send" button morphs into a high-visibility square **"Stop"** button.
   - User trigger immediately calls `abortController.current.abort()`, closing the local HTTP Server-Sent Events (SSE) reader and freezing the UI render at the exact current token.
2. **Backend Execution Cancellation**:
   - Wire the FastAPI SSE generator with `await request.is_disconnected()` detection.
   - Provide an explicit cancellation endpoint `POST /api/v1/chat/conversations/{id}/abort` that broadcasts an abort signal over Redis to terminate any ongoing LLM HTTP connection (Groq / OpenRouter / Gemini).
   - Prevents downstream token burning and ensures server resources are immediately reclaimed.
3. **Graceful Partial Persistence**:
   - Mark the partially streamed assistant message in the database with `status: "interrupted"`, appending a subtle visual indicator: *"[Generation stopped by user]"*.

---

### 1.7 Dedicated Billing & Usage Section (Admin Portal Centralization)

#### The Problem
Currently, billing details and token metrics are embedded inside the Admin "Overview" tab. As enterprise deployment scales across multiple departments, administrators require a dedicated management surface to analyze token burn rates, audit department-level consumption, and configure model tier budgets.

#### Planned Architecture & Capabilities
1. **Dedicated Navigation Surface (`/admin/billing-usage`)**:
   - Move billing and consumption metrics out of the Overview dashboard and into a dedicated **Billing & Usage** sidebar view.
2. **Centralized Usage Analytics & Cost Breakdown**:
   - **Multi-Provider Token Split**: Visualize real-time token volume across Groq, OpenRouter, and Gemini.
   - **Departmental Cost Allocation**: Audit consumption per department (Engineering, HR, Finance) to track cost centers.
   - **Latency & Error Heatmaps**: Monitor average response latency, provider rate-limit spikes (HTTP 429), and fallback switch frequency.
3. **Enterprise Quota Management & Budget Alerts**:
   - Set monthly spending ceilings and automated alerts at 80% and 95% budget utilization thresholds.

---

### 1.8 Audio-to-Text Prompting (Voice Input via Whisper ASR)

#### The Problem
Typing long, complex enterprise queries—such as detailed incident postmortems, travel itineraries, or nuanced policy questions—is time-consuming and inconvenient on mobile devices or in active meeting environments.

#### Planned Architecture & Capabilities
1. **Audio Capture UI Component**:
   - Integrate an intuitive microphone icon beside the chat prompt bar using the HTML5 `MediaRecorder` API with an animated audio waveform.
   - Support push-to-talk and click-to-record modes with automatic silence detection.
2. **Ultra-Low-Latency ASR Pipeline**:
   - **Primary Engine**: Stream audio chunks to Groq's `whisper-large-v3` API endpoint, delivering speech-to-text transcriptions with sub-250ms latency.
   - **Client-Side Fallback**: Support browser-native Web Speech API (`webkitSpeechRecognition`) for instant, offline local transcription when network connectivity is restricted.
3. **Seamless Prompt Staging**:
   - The transcribed text is populated directly into the active prompt textarea, enabling the user to review, edit, or append citations before sending.

---

### 1.9 Semantic Score Threshold for Updating Policy Probe (`updating_policy_probe.py`)

#### The Problem: False-Positive In-Progress Notices During Active Uploads
Currently, when a user query fails confidence checks in the primary RAG and CRAG pipelines (top reranker score $< 0.35$), the system falls back to `probe_updating_policies()` (`backend/services/retrieval/updating_policy_probe.py`). 
This probe queries Qdrant for non-live chunks (`is_live == False`) matching the user's 3D RBAC filter using `limit = 3`, and checks if the matched document has `status == DocumentStatus.UPDATING` in PostgreSQL.

However, `client.query_points()` does not currently pass an explicit `score_threshold`. Because vector search with `limit = 3` *always* returns the nearest 3 vectors regardless of how low the cosine similarity is:
- If an admin is currently uploading an *Engineering Security SOP*, and an engineer asks an entirely unrelated or out-of-domain question (e.g., *"How do I bake chocolate cookies?"* or *"Who won the match?"*):
- Qdrant will return the staged security SOP chunks as the nearest vectors anyway (even with a dismal cosine similarity score of $0.05$).
- The probe detects that the document status is `UPDATING` and falsely returns:
  > *"Notice: The official policy regarding 'Engineering Security SOP' is currently undergoing an administrative update..."*
- This creates false-positive locks and misleads users into thinking their unrelated question is covered by the updating document.

#### Proposed Implementation & Hardening
1. **Pass `score_threshold` into Qdrant `query_points`**:
   Update `backend/services/retrieval/updating_policy_probe.py` to enforce an explicit semantic similarity cutoff (calibrated at $\ge 0.30$):
   ```python
   search_res = client.query_points(
       collection_name=settings.QDRANT_COLLECTION,
       query=query_dense,
       using="dense",
       query_filter=probe_filter,
       score_threshold=0.30,  # Enforce genuine semantic relevance
       limit=top_k,
   )
   ```
2. **Deterministic Evaluation**:
   If the query has low cosine similarity to the staged document ($< 0.30$), Qdrant returns zero points. The probe cleanly returns `None`, and the system outputs the correct safe refusal message:
   > *"I couldn't find verified information in the documents you have access to."*

---

### 1.10 Document Ingestion Modal UX & Asynchronous Upload Lifecycle

#### The Problem: Modal Freezing and Form Inconsistencies
Currently, uploading a document keeps the modal dialog open in a frozen "Uploading…" state while the file uploads and triggers processing, leaving the modal backdrop open with an active "Cancel" button. Furthermore:
1. **Manual Text Entry for Departments**: The "Department" input in the upload modal is an open text field, leading to typos (e.g. typing `"engineering"` or `"Eng"` instead of `"Engineering"`), which corrupts 3D RBAC filtering.
2. **Confusing File Change Action**: The file selection box displays a simple "Change" button beside a delete icon without clear edit semantics.
3. **Synchronous Upload Experience**: Users must stare at a loading modal rather than continuing their administrative work.

#### Planned Architecture & UX Enhancements
1. **Instant Modal Dismissal & Optimistic Row Insertion**:
   - The moment the admin clicks **"Upload and index"**, the dialog box immediately closes.
   - The UI optimistically inserts a new row at the top of the Documents table with an explicit transient state: **`UPLOADING`** (or a blue spinner badge).
   - The upload executes asynchronously via background XHR/fetch.
2. **New Explicit State (`UPLOADING` $\to$ `UPDATING` $\to$ `ACTIVE`)**:
   - Add `UPLOADING` to `DocumentStatus` enum to represent the file transfer and initial server staging phase before Celery picks up chunking.
3. **Toast Notifications & Admin Alerts**:
   - When the Celery worker finishes indexing, the backend emits a real-time event (or auto-poll completion), triggering a system toast notification:
     * *Success*: `"Document 'SRS_GEO...' successfully indexed and is now Live."*
     * *Failure*: `"Document 'SRS_GEO...' processing failed. Click to view logs."*
4. **Structured Department Dropdown**:
   - Replace the free-text Department `<input>` with a strict `<select>` dropdown populated from authorized departments (`Engineering`, `HR`, `Finance`, `Enterprise`), completely eliminating RBAC categorization typos.
5. **Polished File Attachment Card**:
   - Replace the ambiguous "Change" button with a clear **"Edit / Replace File"** action that cleanly opens the OS file picker, paired with a tooltip for file size/extension limits.

---

### 1.11 Document Processing History Segregation & Department Filtering Performance

#### The Problem
1. **Unsegregated Processing Logs**: Clicking the "Processing history" (scroll icon) opens an unstructured text dump mixing file upload logs, Celery task logs, chunking metrics, and sync sweeper traces in a single view.
2. **Slow Department Filtering**: Filtering documents by department executes client-side re-renders or unindexed PostgreSQL sequential scans as the document repository grows.

#### Planned Architecture & Capabilities
1. **Structured Log Segregation (Tabbed History Modal)**:
   - Partition processing history into distinct, searchable tabs:
     - **Ingestion & Parsing**: File text extraction, page counts, and character counts.
     - **Vector Indexing**: Chunk breakdown, identity header stamps, and Qdrant batch upsert latencies.
     - **Reconciliation & Audits**: Version flip timestamps, sweeper verification, and rollback actions.
2. **Database Composite Indexing for Rapid Filtering**:
   - Add a composite B-Tree index on PostgreSQL:
     ```sql
     CREATE INDEX idx_documents_dept_status ON documents (department, status, is_live);
     ```
   - Eliminates table scan latency, providing instant (<10ms) responses when filtering across thousands of enterprise documents by department and clearance level.

---

### 1.12 Document Directory Version Grouping & Client-Side Data Caching (TanStack Query / SWR)

#### The Problem: Historical Version Clutter & High-Frequency API Refetching
1. **Historical Version Clutter in Admin Table**:
   - Currently, `GET /api/v1/documents/` returns all rows in the `documents` table without version grouping. If a document has undergone 3 revisions ($v1, v2, v3$), the table displays 3 separate rows.
   - Rows for older versions ($v1$ and $v2$) have their **Reindex**, **Replace**, and **Restore** action buttons permanently greyed out because `is_latest` is false. This clutters the UI with non-actionable rows, confuses administrators, and artificially bloats the table row count.
2. **Aggressive Network Refetching & Synchronous Disk Probes**:
   - **No Client-Side Cache**: The frontend `AdminDocuments.tsx` executes raw `load()` calls inside `useEffect()` on component mount. Switching away to another tab (e.g., Overview or Settings) and returning immediately re-fires `GET /api/v1/documents/`, `GET /api/v1/auth/me`, and `GET /api/v1/admin/notifications`.
   - **Synchronous Disk I/O on Every List Request**: On the backend, `GET /api/v1/documents/` executes `Path(d.storage_path).is_file()` synchronously for every single document in the page to populate `has_source_file`. On cloud volumes (EFS, EBS, or remote mounts), synchronous filesystem system calls for 20–50 items degrade API latency.
   - **Unindexed In-Memory Aggregation**: The backend calculates `is_latest` dynamically on every request by running `SELECT filename, department, MAX(version) FROM documents GROUP BY filename, department`, causing repeated database grouping operations instead of reading an indexed column.

#### Planned Architecture & Capabilities
1. **Default "Latest Only" View with Expandable Version Drawer**:
   - **Default Table Query**: The main document directory displays **one row per logical document**, strictly filtering `WHERE is_latest = TRUE`.
   - **Expandable Revision History**: Each document row features a clickable version badge (e.g. `v3 (Latest)`). Clicking it expands an accordion drawer showing previous archived revisions ($v2, v1$) with their historical upload dates, audit logs, and a dedicated **"Rollback to this version"** action.
   - Eliminates dead/disabled button rows from the primary administrative workspace.
2. **Persistent `is_latest` Schema Column & Atomic Version Transition**:
   - Add a persistent indexed column to the `documents` table:
     ```sql
     ALTER TABLE documents ADD COLUMN is_latest BOOLEAN DEFAULT TRUE;
     CREATE INDEX idx_documents_filename_latest ON documents (filename, department, is_latest);
     ```
   - When a replacement version is activated, a single atomic database transaction updates the previous latest row to `is_latest = FALSE` and commits the new row with `is_latest = TRUE`.
   - Replaces the dynamic `GROUP BY` aggregation with a fast index lookup.
3. **Cached `has_source_file` Metadata**:
   - Store `has_source_file: bool` directly as a database column updated during upload, replacement, or deletion, completely eliminating filesystem probes during list queries.
4. **Client-Side Query Caching (TanStack Query / SWR)**:
   - Wrap document listing and admin status requests in TanStack Query (`useQuery`):
     ```typescript
     const { data: documents, isLoading } = useQuery({
       queryKey: ['documents', { page, department, clearance, status, search }],
       queryFn: () => fetchDocuments(...),
       staleTime: 5 * 60 * 1000, // 5 minutes stale time
       refetchOnWindowFocus: false,
     });
     ```
   - Navigating between tabs or pages renders immediately from the local in-memory cache without hitting the network, and automatically invalidates only when documents are uploaded, deleted, or replaced.

---

### 1.13 Disambiguation Between Administrative Action Execution (`admin_reindex`) and Audit Log Inspection (`admin_audit_logs`)

#### The Problem: Action-Inspection Collision in Natural Language Prompts
When a system administrator asks natural language questions regarding past operational history—such as *"tell me which documents were reindexed manually by admin in the last 7 days"* or *"list documents reindexed recently"*:
1. **Misclassification in Query Planner**:
   - The LiteLLM query planner (`backend/services/orchestration/query_planner.py`) matches the keyword *"reindex"* and assigns `tool_action = "admin_reindex"` (which is an imperative action tool intended to trigger a new asynchronous reindex task for a specific document UUID).
   - It fails to distinguish that the inquiry is asking for historical **audit trail records** (`admin_audit_logs`).
2. **Missing UUID Rejection**:
   - In `backend/services/admin/admin_agent.py`, the `admin_reindex` handler searches for a document UUID. Because none is provided in a general history inquiry, it returns:
     > *"Provide the document ID to reindex it. You can copy it from Admin → Documents."*
3. **Synthesis LLM Fallback Truncation**:
   - The final synthesis model receives this operational notice in `<retrieved_context>`. Since the notice fails to answer the user's factual inquiry about past documents, the model triggers Rule 4 safe fallback:
     > *"I couldn't find verified information in the documents you have access to."*

#### Architectural Solution & Proposed Implementation
1. **Disambiguation in Query Planner Instructions**:
   - Update `query_planner.py` system prompt rules to explicitly separate action execution from historical inquiry:
     - Imperative execution (*"reindex document <uuid>"*, *"retry ingestion"*) -> `admin_reindex`.
     - Historical/Audit queries (*"which documents were reindexed"*, *"show reindex history"*, *"who reindexed documents"*) -> `admin_audit_logs` with `category: DOCUMENT`.
2. **Graceful Internal Fallback in `admin_agent.py`**:
   - If `admin_reindex` is invoked without a UUID and the query contains listing/past-tense interrogatives (*"which"*, *"list"*, *"show"*, *"last X days"*), gracefully route internally to `query_audit_logs(category=AuditCategory.DOCUMENT)` to return recent reindexing audit records rather than returning a dead-end error message.

---

### 1.14 Dedicated Enterprise Administrative Tool Suite & Operational Agent Expansion

#### The Problem: Monolithic & Underdeveloped `ADMIN_OPERATION` Layer
Currently, administrative capabilities are channeled through a single generic `ADMIN_OPERATION` task type with basic diagnostic functions (`inspect_system_health`, `inspect_audit_logs`, `get_document_status`, `trigger_reindex`). In its current state, `ADMIN_OPERATION` does not function as a proper enterprise tool suite:
1. **Lack of Granular Tool Definitions**: Tools lack dedicated OpenAI/Pydantic function declarations with schema validation for the planner, creating confusion between read queries (e.g., audit log history) and active system mutations.
2. **Missing Administrative Search & Analytics**: Administrators cannot ask natural-language questions over system telemetry (e.g., query failure rates, active user counts, indexing latency percentiles, or department-level storage consumption) because no dedicated analytical tools exist.
3. **Fragile Workflow Execution**: Actions like `trigger_reindex` require copy-pasting raw 36-character UUIDs from the admin portal rather than resolving documents semantically by title or filename.

#### Planned Architecture & Proper Tool Implementation
1. **First-Class Operational Tool Catalog**:
   - Replace the generic `ADMIN_OPERATION` bucket with a modular catalog of schema-validated administrative tools:
     - `admin_query_audit_trail`: Parameterized structured queries for time windows, categories, user IDs, and status codes.
     - `admin_document_operations`: Semantic document resolution by title/filename, version diff inspection, and batch re-indexing triggers with Celery task monitoring.
     - `admin_user_directory_tools`: Clearance adjustments, department transfers, and immediate session revocations.
     - `admin_system_diagnostics`: Real-time Redis queue depths, Qdrant RAM/HNSW index memory footprints, and PostgreSQL connection pool metrics.
2. **Dedicated Admin Visual Cards & HITL Confirmations**:
   - Destructive operations (e.g., purging older versions or mass reindexing) will mandate Human-in-the-Loop (HITL) approval cards with 15-minute TTLs.
   - Diagnostic outputs will render as dedicated operational widgets (e.g. system status badges, interactive audit tables) rather than being flattened into plain-text synthesis.

---

### 1.15 Problems We Will Discuss Later: Knowledge Base (Internal RAG) vs. External Integration (Drive/GitHub) Collision & Explicit Connector Invocations (`@drive`)

#### The Problem: Ambiguous Routing Collisions & The "Blind Planner" Trap
When an employee uploads a document (e.g., `SRS_GEO_Content_Gap_Engine_v2_ultimate.docx`) into Cortex's internal knowledge base and subsequently asks questions about its content (e.g., *"in srs what is the last ai engine named"*):
1. **The "Blind Planner" Dilemma**:
   - The query planner LLM (`query_planner.py`) has no direct awareness of which specific files exist inside the Qdrant vector database.
   - Natural language queries referencing "documents", "specs", or "uploaded files" or containing file extensions (`.docx`, `.pdf`) get misclassified into `DRIVE_SEARCH` due to regex rules or ambiguous prompt heuristics (*"Use DRIVE_SEARCH when user references Google Drive or uploaded documents"*).
2. **Asymmetric Output Failure Mode**:
   - `INTERNAL_RAG` extracts semantic chunks with cross-encoder re-ranking scores (`0.993`).
   - `DRIVE_SEARCH` merely queries Google Drive API metadata and returns viewer URLs (`[SRS.docx](https://...)`), without extracting text passages unless specifically directed to read.
   - When the DAG executes `DRIVE_SEARCH`, only URL links reach `<retrieved_context>`.
   - The final synthesis LLM strictly follows Grounding Rule 4 (*"If no verified evidence exists in retrieved context, output fallback string"*), responding *"I couldn't find verified information in the documents you have access to"*, despite the document being 100% indexed in Qdrant.
3. **Conversational Context Bias Cascade**:
   - Once a tool like `DRIVE_SEARCH` is invoked, follow-up messages (even explicit overrides like *"use rag"*) inherit conversational history bias. The planner adheres to contextual continuity (*"continue with previous tool"*) and re-dispatches `DRIVE_SEARCH`, locking the user in a failed loop.

#### Planned Architecture & Solutions: The Explicit Connector Paradigm
To eliminate brittle prompt engineering and routing guesswork, enterprise search architectures (ChatGPT, Claude, Cursor) adhere to strict control-plane separation:

1. **Default to Internal Knowledge Base (`INTERNAL_RAG`)**:
   - All informational inquiries, company policies, technical specifications, and uploaded document questions default **strictly and exclusively** to `INTERNAL_RAG` (Qdrant).
   - The router is never permitted to guess external cloud integrations on general document queries.
2. **Explicit Connector Invocations via `@mentions` (Industry Standard)**:
   - External enterprise integrations (Google Drive, GitHub, Jira) are invoked **only** when explicitly designated by the user:
     - **UI `@mentions`**: Tagging connectors directly in the chat input bar (e.g., `@drive find srs doc`, `@github inspect auth.py`, `@jira my open tickets`), mirroring Cursor's `@docs`/`@code` and ChatGPT's `@agent` model.
     - **Explicit Keyword Stems**: The user explicitly names the external third-party service in natural language (e.g., *"in my connected Google Drive"*, *"from the GitHub repository"*).
   - This prevents accidental diversion of internal knowledge queries to external cloud endpoints.
3. **Deterministic Database Catalog Pre-Filter**:
   - Prior to calling the planner LLM, a sub-millisecond PostgreSQL query checks the `documents` table for title/filename matches:
     ```sql
     SELECT id FROM documents WHERE title ILIKE :search OR filename ILIKE :search LIMIT 1;
     ```
   - If an indexed document exists in Cortex, the system deterministically binds the task to `INTERNAL_RAG`, bypassing LLM classification.
4. **DAG Speculative Execution Cascade (The Safety Net)**:
   - In `dag_orchestrator.py`, if an external tool (`DRIVE_SEARCH`) executes but yields **zero text passages** (only URL links), and the prompt is an informational query (*"what is"*, *"how does"*, *"name"*), the DAG orchestrator automatically speculatively cascades to `INTERNAL_RAG` against Qdrant before synthesis, guaranteeing that available vector chunks are never dropped.

---

### 1.16 Chat Conversation Deletion & Foreign Key Cascade Lifecycle

#### The Problem: Deletion Failures from Database Constraints and Silent UI Swallowing
Currently, clicking the delete button (trash icon) next to a conversation in the sidebar fails in production environments:
1. **Foreign Key Constraint Violations in PostgreSQL**:
   When the client invokes `DELETE /api/v1/chat/conversations/{conversation_id}`, the backend attempts to delete `MessageFeedback`, `Message`, and `Conversation`. However:
   - `query_quality_metrics` has a foreign key `conversation_id -> conversations.id`. When answering user queries, quality metrics are recorded against the active session. Attempting to delete the conversation row triggers `psycopg.errors.ForeignKeyViolation: update or delete on table "conversations" violates foreign key constraint "query_quality_metrics_conversation_id_fkey"`.
   - `user_memories` has foreign keys `source_conversation_id -> conversations.id` and `source_message_id -> messages.id`. If memories were generated or linked from that conversation or its messages, deleting the messages or conversation row triggers foreign key violation errors on `memories_source_conversation_id_fkey` or `memories_source_message_id_fkey`.
   - `messages` has a self-referencing foreign key `parent_message_id -> messages.id`. If thread messages reference their parent, deleting all messages in a bulk batch without nullifying the self-reference violates foreign key ordering.
   Consequently, the endpoint crashes with HTTP 500.
2. **Silent Failure & Missing Toast Feedback in Frontend**:
   In `Sidebar.tsx`, `confirmDelete()` wrapped the API call in an empty `catch { /* error */ }` block without displaying toast notifications, inspecting HTTP error status, or indicating why the conversation remained in the list.

#### Planned Architecture & Implementation
1. **Orchestrated Relational Cascade & Foreign Key Nullification**:
   In `backend/routers/chat.py`, ensure atomic, safe cleanup within a single database transaction:
   - Nullify telemetry references: `UPDATE query_quality_metrics SET conversation_id = NULL WHERE conversation_id = :conv_id` (preserving audit telemetry without blocking row deletion).
   - Nullify episodic memory links: `UPDATE user_memories SET source_conversation_id = NULL WHERE source_conversation_id = :conv_id` and `UPDATE user_memories SET source_message_id = NULL WHERE source_message_id IN (:msg_ids)` (preserving the user's semantic memory while safely unlinking the deleted chat).
   - Clear self-referencing message chains: `UPDATE messages SET parent_message_id = NULL WHERE conversation_id = :conv_id`.
   - Cascade delete `message_feedback` records.
   - Delete `messages` records.
   - Delete `conversations` record and commit atomically.
2. **Robust Frontend Feedback & State Management**:
   Surface toast error messages if network or server errors occur during deletion, immediately purge the deleted conversation from the sidebar state upon 200 OK, and gracefully route back to `/chat` if the deleted conversation was currently viewed.

---

### 1.17 Custom Dialog Boxes for Deletion and Destructive Actions (Replacing Browser Popups)

#### The Problem: Native `window.confirm` and `window.alert` Disruptions
Currently, multiple administrative and destructive actions across the frontend rely on primitive browser dialogs:
1. `AdminDocuments.tsx`: Uses `window.confirm("Delete “" + doc.title + "” and its indexed content?")` when deleting an indexed document.
2. `AdminUsers.tsx`: Uses `window.confirm("Reject access request for...")` when deleting a pending user registration and `window.confirm("Reject this corporate email revision request?")` when rejecting email changes.
3. `AdminNotificationBell.tsx`: Uses `window.confirm("Reject this email revision request?")` and native `alert()` when handling approval or rejection errors.

**Deficiencies of Browser Native Dialogs**:
- **Design Inconsistency**: Native popups break the Cortex visual design language and branding.
- **Theming & Dark Mode Failure**: Native browser dialogs render in operating system white/gray, glaring harshly when a user is in Dark Mode.
- **Thread Blocking & Poor Accessibility**: Native `window.confirm()` and `alert()` freeze the JavaScript main thread, cannot be customized with warning icons or loading spinners during async deletion, and lack keyboard/focus management.

#### Planned Architecture & Capabilities
1. **Reusable Accessible `ConfirmDialog` Component**:
   Build a custom, reusable dialog component (`frontend/src/components/ConfirmDialog.tsx`) styled according to the Cortex design system:
   - Backdrop scrim with click-outside and `Escape` key dismissal.
   - Destructive mode (`variant="danger"` with red action button) and informational/warning modes.
   - Customizable title, descriptive message, confirm button text, and cancel button text.
   - In-flight loading state (`isBusy` / `loading`) that disables controls and shows spinner while the API deletion call completes.
2. **Comprehensive Deprecation of Native Alert/Confirm**:
   Replace all occurrences of `window.confirm` and `alert` across `AdminDocuments`, `AdminUsers`, `AdminNotificationBell`, and `Sidebar` with the new custom dialog component and unified toast alerts.

---

## 2. Moderate Complexity Scope (Medium-Term Enhancements)

These features involve integrations with external worker pipelines, scheduled cron tasks, or cross-service state synchronization.

### 2.1 Cloudinary Enterprise Cloud Media & Document Storage Architecture

#### Architecture & Multi-Folder Hierarchy
To support ephemeral cloud container hosts (such as Railway, Render, or AWS ECS), all source documents and user ticket attachments are streamed to Cloudinary under the dedicated root namespace:
- **`cortex/documents/`**: Enterprise knowledge base source files (PDFs, DOCXs, Markdown). Secured via authenticated delivery and signed URLs so internal intellectual property is never publicly exposed.
- **`cortex/jira_tickets/`**: User-uploaded receipts, invoices, and diagnostic screenshots attached to Jira drafts.

#### Asset Naming Convention & User Attribution
To enable instantaneous filtering and ownership attribution, image assets follow the strict schema:
```
cortex/jira_tickets/<username>-<YYYYMMDD-HHMMSS>.<ext>
```
Example: `employee_eng-20261119-104523.png`
The substring before the first hyphen is the exact corporate username of the uploader, allowing audit queries and administrative cleanup to filter assets by employee with zero database overhead.

#### Agent Isolation Guardrail
Cloudinary credentials and endpoints operate strictly as backend platform infrastructure (`backend/services/storage/cloudinary_service.py`). Under no circumstances is Cloudinary exposed as a tool or function declaration to the AI planner or synthesis LLM, preserving architectural boundaries and preventing prompt-injection credential leaks.

---

### 2.2 Automated Cloud Storage Change Data Capture (CDC)
- **Real-time Webhooks for Cloud Drives**:
  - Connect Google Drive Push Notifications and Microsoft OneDrive Webhooks to the Celery worker queue.
  - Automatically detect when a source document is edited, renamed, or deleted in the cloud drive.
  - Re-trigger ingestion, chunking, and embedding with zero administrator intervention.
- **Diff-Based Version Reindexing**:
  - Compute structural diffs between document versions so only modified sections are re-embedded, reducing LLM token consumption and embedding costs by up to 80%.

### 2.3 Granular Multi-Tenant & Workspace Scoping
- **Virtual Team Spaces**:
  - Allow enterprise organizations to partition the knowledge base into isolated virtual workspaces (e.g., *Alpha Team*, *M&A Due Diligence*, *Legal Compliance*).
  - Enforce cross-workspace firewall rules preventing vector similarity bleed across different client accounts.

---

### 2.4 Automated RAG Evaluation Pipeline (Ragas & Optuna Parameter Calibration)

#### Current State vs. Uncalibrated Retrieval
Currently, Cortex operates with static empirical defaults:
- Hybrid vector/sparse weight balance ($\alpha = 0.5$)
- Initial retrieval window ($K = 10$)
- Cross-encoder reranker admission threshold ($\tau = 0.25$)

**No automated parameter calibration using Optuna has been executed to date.** System tuning has relied on manual heuristic checks. Without systematic Bayesian optimization against ground-truth benchmarks, these default values may leave retrieval quality, chunk diversity, and context precision sub-optimal.

#### The Problem: Silent Retrieval Regressions
Retrieval augmented generation systems are prone to silent regression: adjusting chunk overlap, changing re-ranking models, or altering system prompt instructions can improve one class of queries while degrading answer faithfulness or context recall on others. Without automated evaluation metrics and optimization loops, performance assessment remains anecdotal and subjective.

#### Planned Architecture & Capabilities
1. **Ragas Metric Framework Integration**:
   - Evaluate Cortex RAG output using the industry-standard Ragas framework across four foundational dimensions:
     - **Faithfulness**: Measures whether the synthesized answer contains only claims that are directly inferable from the retrieved chunks (hallucination detector).
     - **Answer Relevancy**: Evaluates how pertinent the generated response is to the original user prompt, penalizing fluff and incomplete answers.
     - **Context Recall**: Verifies whether all ground-truth facts required to answer the prompt were successfully retrieved in the top-$K$ candidate chunks.
     - **Context Precision**: Measures whether the most relevant chunks are ranked at the top of the context window (re-ranking quality).
2. **Standardized 50-Pair Enterprise QA Benchmark Suite**:
   - **Calibration Split (35 QA pairs)**: Spanning HR policies, engineering canary SOPs, travel reimbursements, security access controls, and DPDP Act legal compliance. Used for parameter tuning and regression monitoring.
   - **Held-Out Evaluation Split (15 QA pairs)**: Isolated test set evaluated only during release qualification to prevent hyperparameter overfitting.
3. **Optuna Automated Hyperparameter Calibration**:
   - Implement an automated tuning pipeline (`scripts/evals/tune_retrieval_params.py`) using Optuna Bayesian optimization to maximize the harmonic mean of Faithfulness and Context Recall ($F_1$ eval score):
     - $\alpha \in [0.0, 1.0]$: Weight balance between FastEmbed dense semantic vectors and BM25 sparse lexical tokens in hybrid search.
     - $K_{\text{initial}} \in [10, 30]$: Initial hybrid candidate retrieval pool.
     - Cross-Encoder score cutoff $\tau \in [0.15, 0.45]$: Minimum threshold to admit a chunk into the synthesis prompt.
4. **CI/CD Quality Gate**:
   - Integrate evaluation into GitHub Actions: reject pull requests if any commit causes Ragas Faithfulness to drop below 0.90 or Context Recall below 0.85 on the calibration benchmark.

---

### 2.5 Background Query Processing (Decoupled Async Execution & Re-attachment)

#### The Problem
Currently, query execution is coupled directly to the client browser's active Server-Sent Events (SSE) connection. If a user submits a prompt involving deep multi-tool research (e.g. cross-referencing internal RAG, Google Drive documents, and GitHub issues) and navigates to another conversation or tab, the browser unmounts the listener, disrupting response generation and losing partial results.

#### Planned Architecture & Capabilities
1. **Decoupled Backend Task Execution**:
   - Decouple `DAGOrchestrator` execution from the immediate HTTP request-response cycle.
   - When a user dispatches a prompt, the backend initiates an asynchronous background worker task (via Celery or an independent `asyncio.create_task`) indexed by `conversation_id` and `message_id`.
2. **Persistent State Streaming via Redis Pub/Sub**:
   - The orchestrator streams intermediate planner events, tool actions, and synthesized tokens into a transient Redis Pub/Sub channel while incrementally updating the PostgreSQL `messages` record.
3. **Seamless Conversation Re-attachment**:
   - When the user navigates between conversations, the frontend queries `GET /api/v1/chat/conversations/{id}/status`.
   - If a background generation is still running, the frontend immediately re-subscribes to the active SSE channel and streams the remaining response in real time.
   - If the generation has completed, the full synthesized answer and citations are loaded directly from the database without re-running the pipeline.

---

### 2.6 Dynamic Knowledge Source Management & Access Control (Admin Connector Studio)

#### The Problem
Currently, external knowledge sources (such as Google Drive folder IDs and GitHub repositories) are statically declared in `.env` variables (`GOOGLE_DRIVE_ROOT_FOLDER_ID`, `GITHUB_DEFAULT_REPO`). Adding a new departmental shared drive or engineering repository requires updating environment files and redeploying or restarting the server. Furthermore, all users currently share the same hardcoded connector targets regardless of department.

#### Planned Architecture & Capabilities
1. **Admin Knowledge Connector Studio (`/admin/sources`)**:
   - A dedicated administrative interface for registering and managing external integration sources dynamically:
     - **Google Drive**: Connect specific Shared Drives or root folder IDs with custom display labels (e.g., *"HR Shared Drive 2026"*, *"Core Architecture SOWs"*).
     - **GitHub Repositories**: Connect multiple repositories (`aadish-25/cortex-testing`, `cortex-org/infrastructure`) with default branch and path filters.
     - **Future Connectors**: Dynamically link Confluence spaces, Notion workspaces, or Jira Service Desks.
2. **Granular Source-Level Access Control & RBAC**:
   - Associate each registered knowledge source with authorized user departments (`Engineering`, `HR`, `Finance`, `Executive`) and clearance tiers (`Public`, `Internal`, `Confidential`, `Restricted`).
3. **Scoped Retrieval Filtering**:
   - During query planning, `dag_orchestrator.py` queries the active user's permissions and only dispatches searches against knowledge sources that the user is explicitly authorized to view, preventing cross-department information leakage at the connector boundary.

---

## 3. High Complexity & Architectural Scope (Long-Term Deep Tech)

These features represent significant architectural endeavors involving advanced visualization, enterprise identity federation, or distributed agent orchestration.

### 3.1 Dynamic Execution DAG & Retrieval Link Visualizer
- **Interactive Retrieval Graph**:
  - Provide an interactive node-link visualization (using React Flow or D3.js) showing the exact query decomposition path:
    1. User Query $\to$
    2. Query Planner Agent $\to$
    3. Tool Actions dispatched (FastEmbed Hybrid Vector Search, Google Drive API, SQL Metadata Lookup) $\to$
    4. Retrieved Chunk Nodes with semantic similarity scores and cross-encoder re-ranking deltas $\to$
    5. Final Synthesis LLM.
- **Developer Debug Mode**:
  - Allow engineers and administrators to inspect intermediate JSON tokens, latency waterfalls, and prompt token counts at each DAG node.

### 3.2 Enterprise Single Sign-On (SSO) & SCIM Directory Sync
- **SAML 2.0 / OpenID Connect (OIDC)**:
  - Native integration with Okta, Microsoft Entra ID (Azure AD), Google Workspace SAML, and Ping Identity.
- **SCIM 2.0 User Provisioning**:
  - Automatically provision, update, and de-provision employee accounts based on HR information system (HRIS) webhooks (Workday, BambooHR, Rippling).
  - Automatically sync employee department and clearance levels directly from corporate directory attributes.

### 3.3 Collaborative Multi-Agent Research Canvas
- **Multi-Agent Deep Research**:
  - Fan out research tasks to multiple subagents running in parallel across vector memory, web sources, and enterprise databases.
  - Synthesize cross-document conflict reports (e.g. "Policy document A states X, but HR memo B states Y").

---

## 4. Future Scope / To Be Done (TBD)

A consolidated roadmap checklist of core features and operational capabilities slated for upcoming sprints:

1. **Audio-to-Text Prompting:** Add voice input functionality, allowing users to submit prompts through speech, which are transcribed into text and processed by Cortex. *(Refer to [Section 1.8](#18-audio-to-text-prompting-voice-input-via-whisper-asr))*
2. **Stop AI Response:** Introduce a dedicated Stop button that allows users to interrupt an ongoing AI response or generation immediately. *(Refer to [Section 1.6](#16-stop-ai-response-immediate-stream-interruption--abortcontroller))*
3. **Background Query Processing:** Ensure that submitting a prompt and switching to another chat does not cancel its execution. Queries should continue processing in the background, with completed responses available when users return to the conversation. *(Refer to [Section 2.5](#25-background-query-processing-decoupled-async-execution--re-attachment))*
4. **Dedicated Billing & Usage Section:** Introduce a separate Billing & Usage section instead of keeping billing information within the Overview tab. Centralize usage statistics, consumption details, billing information, and related management features in one dedicated section. *(Refer to [Section 1.7](#17-dedicated-billing--usage-section-admin-portal-centralization))*
5. **Dynamic Knowledge Source Management & Access Control:** Allow administrators to dynamically connect and manage knowledge sources such as Google Drive folders and GitHub repositories, configure which sources are available to different users or teams, and enforce source-level access permissions during retrieval. *(Refer to [Section 2.6](#26-dynamic-knowledge-source-management--access-control-admin-connector-studio))*
6. **Automated RAG Parameter Calibration via Optuna:** Build and execute an automated parameter calibration pipeline using Optuna Bayesian optimization (TPE) over the 50-pair QA benchmark dataset. Calibrate hybrid retrieval alpha weighting ($\alpha$), initial retrieval depth ($K$), and cross-encoder reranking cutoffs ($\tau$) to maximize Ragas Faithfulness and Context Recall, replacing current uncalibrated empirical defaults. *(Refer to [Section 2.4](#24-automated-rag-evaluation-pipeline-ragas--optuna-parameter-calibration))*
7. **Document Directory Version Grouping & Client-Side Caching:** Default the admin document table to display only latest active versions (`is_latest = TRUE`) with expandable revision drawers, add an indexed `is_latest` database column, and eliminate unneeded network refetches and disk probes via TanStack Query client caching. *(Refer to [Section 1.12](#112-document-directory-version-grouping--client-side-data-caching-tanstack-query--swr))*
8. **Explicit Connector Invocations (`@drive`) & Knowledge Base Collision Prevention:** Default all document inquiries strictly to `INTERNAL_RAG` (Qdrant), require explicit user `@mentions` (`@drive`, `@github`, `@jira`) to invoke external tools (mirroring ChatGPT/Claude/Cursor), ground routing with a deterministic Postgres catalog pre-filter, and implement a DAG speculative execution cascade if external tools return zero text passages. *(Refer to [Section 1.15](#115-problems-we-will-discuss-later-knowledge-base-internal-rag-vs-external-integration-drivegithub-collision--explicit-connector-invocations-drive))*
9. **Chat Conversation Deletion & Foreign Key Cascade Lifecycle:** Ensure conversation deletion cleanly cascades without 500 foreign key errors by nullifying telemetry references (`query_quality_metrics`), unlinking episodic memories (`user_memories`), clearing self-referencing message chains (`parent_message_id`), deleting message feedback, and providing explicit frontend feedback. *(Refer to [Section 1.16](#116-chat-conversation-deletion--foreign-key-cascade-lifecycle))*
10. **Custom Confirmation & Alert Dialogs (Eliminate Browser Native Popups):** Replace all primitive `window.confirm()` and `window.alert()` popups with a standardized, theme-aware custom `ConfirmDialog` component across document deletion, user access request rejection, email revision rejections, and conversation deletion. *(Refer to [Section 1.17](#117-custom-dialog-boxes-for-deletion-and-destructive-actions-replacing-browser-popups))*
