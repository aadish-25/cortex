# Architecture Decision Log — Cortex (AAD-H1)

All decisions are final. Do not reopen unless a hard technical blocker is discovered.

---

## D1 — Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI + Python 3.14 |
| Database | PostgreSQL (prod) / SQLite (dev) via SQLModel + asyncpg/aiosqlite |
| Vector Store | Qdrant |
| Cache / Broker | Redis |
| Task Queue | Celery |
| Embeddings | FastEmbed (BAAI/bge-small-en-v1.5, 384d) |
| Frontend | React + Vite + TypeScript |
| Deployment | Railway |

---

## D2 — Qdrant over pgvector

Qdrant is the vector store. pgvector was rejected.

Reasons:
- Native sparse BM25 vector support — pgvector has no sparse vector type
- Filtered HNSW — Qdrant applies metadata filters before ANN traversal, not after; pgvector filters post-retrieval which inflates recall cost
- Resource isolation — vector index memory is separate from PostgreSQL
- Native RRF fusion API

Consequence: two databases to manage. PostgreSQL owns relational state; Qdrant owns vector index. They must stay in sync via the reconciliation sweeper (D8).

---

## D3 — Multi-Dimensional RBAC

Access control is enforced on three dimensions simultaneously:

1. **Role** — `Admin | Manager | Employee`
2. **Department** — string, e.g. `"Engineering"`, `"HR"`
3. **ClearanceLevel** — `Public < Internal < Confidential`

A document is accessible to a user if and only if:
- `is_live == True`, AND
- User is Admin (bypass), OR
- Document clearance is `Public`, OR
- User department matches document department AND user clearance >= document clearance

This filter is applied as a Qdrant pre-filter before any vector math. It cannot be bypassed via prompt injection.

---

## D4 — Staged Version Flip

Document updates follow a strict state machine to prevent serving stale or partial chunks:

1. Upload starts → old version `is_live = False` (locked), new version inserted with `is_live = False`
2. All new chunks embedded and indexed → new version flipped `is_live = True`, old chunks purged
3. On failure/timeout → `status = UPDATE_FAILED`, old version stays locked, admin must recover manually

States: `ACTIVE → UPDATING → ACTIVE` (success) or `UPDATING → UPDATE_FAILED` (failure)

Concurrency guard: atomic SQL `UPDATE documents SET status = 'UPDATING' WHERE id = ? AND status != 'UPDATING'` — only one update per document at a time.

---

## D5 — SHA-256 Deduplication

On every upload, a SHA-256 hash of the file content is computed before any processing.

- Exact hash match → reject as duplicate, return HTTP 409
- Filename match but different hash → new version, increment version number
- Both new → fresh document

---

## D6 — Contextual Identity Header Stamping

Every chunk has a structured header prepended before embedding:

```
[Document: {title} | Version: {v} | Department: {dept} | Section: {heading}]
```

This anchors every chunk mathematically to its parent document, preventing cross-document confusion when multiple policies use similar language.

---

## D7 — Hybrid Search + RRF + Cross-Encoder Reranking

Retrieval runs in three stages:

1. **Parallel hybrid search** — Dense vector (top 25) + Sparse BM25 (top 25) on the RBAC-filtered collection simultaneously
2. **Reciprocal Rank Fusion** — merges both lists into a unified pool of ~25 candidates
3. **Cross-Encoder Reranking** — reads query and each chunk together, assigns true relevance score, keeps top 4–5

---

## D8 — Bidirectional Reconciliation Sweeper

A Celery beat or lifespan background task runs periodically to detect and fix drift between PostgreSQL and Qdrant:

- Finds chunks in Qdrant with no matching active document in PostgreSQL → deletes them
- Finds active documents in PostgreSQL with no chunks in Qdrant → flags for re-ingestion

Uses Redis lock (`reconcile_lock`) to prevent concurrent sweeper runs.

---

## D9 — ACL-Gated Detection Probe

When a user queries and their top reranker score is below the confidence threshold, before returning the fallback response, the pipeline runs a secondary probe:

- Retrieves the same query without the RBAC filter restricted to old versions (`version == old_version`)
- If a matching chunk from an in-progress document update outscores the live results, returns: `"This policy is currently being updated. Please check back shortly."`
- If no match → returns the standard fallback

The fallback response is identical whether the document doesn't exist or exists in a restricted department — prevents information existence leaks.

---

## D10 — Confidence Threshold Cutoff

If the top reranker score after the cross-encoder is below the calibrated threshold (default `0.35`, tuned via Optuna on the calibration set), LLM generation is aborted and a safe fallback is returned.

Threshold is stored in `config.py` as `RERANKER_CONFIDENCE_THRESHOLD` and can be overridden via `.env`.

---

## D11 — Intent Router

Every user message is classified by a fast LLM call with a strict JSON schema before hitting RAG:

| Intent | Action |
|---|---|
| `KNOWLEDGE_SEARCH` | Proceeds to RAG retrieval pipeline |
| `STRUCTURED_LOOKUP` | Routes to deterministic SQL query, bypasses RAG |
| `ACTION_WORKFLOW` | Routes to action draft creation |
| `UNCLEAR / OUT_OF_SCOPE` | Returns clarification question |

---

## D12 — Expiring Action Drafts

When the router classifies `ACTION_WORKFLOW`:

1. A draft is created in the `action_drafts` table with `expires_at = now + 15 minutes`
2. The user sees a preview and a confirm button
3. On confirm, the draft is committed (`is_committed = True`) and a real ticket is created
4. Replaying the same confirm returns HTTP 200 (idempotent)
5. After expiry, confirm returns HTTP 410 Gone

Single-use `draft_id` prevents double-submission.

---

## D13 — Prompt Injection Sandboxing

Retrieved chunks are wrapped in XML data tags in the LLM prompt:

```xml
<retrieved_context>
  {chunk content}
</retrieved_context>
```

The system prompt instructs the LLM to treat everything inside as untrusted reference text and ignore any embedded instructions.

---

## D14 — Contradiction Handling

If two surviving chunks from active documents contain conflicting information, the LLM prompt explicitly requires presenting both policies side-by-side with their source document names and dates rather than guessing which is correct.

---

## D15 — Inline Citations

Every LLM-generated claim must be followed by an inline citation in the format `[Document Title, p. X]`. The frontend renders these as clickable source references.

---

## D16 — Query Rewriting

If the user message is a follow-up (e.g. "What about international ones?"), it is rewritten into a standalone query using the last 2 turns of chat history before hitting the retrieval pipeline. This prevents the vector search from receiving a pronoun-only query with no semantic content.

---

## D17 — SSE Streaming Protocol

Chat responses are streamed via Server-Sent Events.

Events:
- `event: token` — incremental text chunk
- `event: citations` — final citations JSON
- `event: done` — stream complete
- `event: error` — includes `retryable: bool` and `retry_token`
- `event: session_terminated` — user was revoked mid-stream

Rehydration: Redis Stream (`chat_stream:{message_id}`) stores all tokens for 10 minutes. Frontend sends `Last-Event-ID` header on reconnect to resume from where it dropped.

---

## D18 — Token Version Revocation

Every JWT embeds `token_version` from the user's DB record. On every authenticated request, `get_current_user` fetches the user from DB and compares `token_version`.

- Admin fires employee → sets `token_version = -1` in DB
- Employee's next request → `token_version` mismatch → HTTP 401 immediately, even if JWT hasn't expired

No token blacklist needed. Single DB field.

---

## D19 — Feedback and Regeneration

`message_feedback` table stores per-message thumbs up/down with optional category and comment.

Regeneration:
- User clicks Regenerate → frontend sends `regenerate: true` with `parent_message_id`
- Backend creates a new message row with same `parent_message_id`, `variant_index = previous + 1`
- Temperature bumped to `0.2` above base, `FINAL_TOP_K + 2` chunks retrieved
- If user provides feedback text before regenerating, it is appended to the system prompt as a soft hint

---

## D20 — Password Hashing

`passlib[bcrypt]` is incompatible with Python 3.14 (`bcrypt` module missing `__about__`). Direct `import bcrypt` is used instead.

Password is explicitly truncated to 72 bytes before hashing to conform to the bcrypt specification (bcrypt silently ignores bytes beyond 72).

---

## D21 — Evaluation

**Calibration set:** 35 hand-authored QA pairs (answerable, unanswerable, conflicting, access-restricted)
**Held-out set:** 15 pairs, never used during threshold tuning
**Threshold tuning:** Optuna minimises `FP_rate + FN_rate` on calibration set
**Synthetic QA:** used for smoke testing ingestion only, never mixed into the benchmark
**Metrics:** Recall@K, MRR for retrieval; Faithfulness, Answer Relevance for generation; measured separately

---

## D22 — No Jira Integration in Scope

`jira_key` column exists in the `tickets` table as a nullable field for future use. Actual Jira API integration is out of scope for this submission. Tickets go to `OUTBOX_PENDING` state and stay there.

---
