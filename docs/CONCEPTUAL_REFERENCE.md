# Conceptual Reference — Cortex (AAD-H1)

Explains the why behind key technical concepts used in this project. Read this before reading code.

---

## RAG (Retrieval-Augmented Generation)

The LLM does not have company documents in its training data. RAG solves this by retrieving relevant document chunks at query time and injecting them into the LLM prompt as context. The LLM then generates a grounded answer from that context rather than hallucinating from general training.

The pipeline is: **Query → Retrieve relevant chunks → Inject into prompt → Generate answer**

---

## Hybrid Search

Two complementary retrieval methods run in parallel:

**Dense vector search** — the query and all chunks are converted to embedding vectors (384-dimensional floats). Similarity is measured by cosine distance. Good at finding semantically related content even when exact words differ.

**Sparse BM25 search** — keyword-based retrieval. Exact word matches, policy IDs, acronyms, and numbers rank highly. Complements dense search which can miss exact terms.

Both produce ranked lists. Reciprocal Rank Fusion (RRF) merges them: a chunk that ranks high in both lists scores higher than one that only ranks high in one.

---

## Cross-Encoder Reranker

The initial retrieval returns top 25 candidates — a broad recall net. The cross-encoder is a second, slower model that reads the query and each candidate chunk together (not as separate vectors) and assigns a true relevance score from 0 to 1.

The difference from embedding similarity: a cross-encoder sees both texts simultaneously and understands their relationship. An embedding model encodes them independently. Cross-encoders are more accurate but too slow to run on the entire corpus, so they only run on the small candidate pool from retrieval.

After reranking, only the top 4–5 chunks proceed to the LLM.

---

## Confidence Threshold

The top reranker score after cross-encoding is a proxy for "does the corpus contain an answer to this question". If this score falls below the calibrated threshold, the question is likely outside the indexed documents, the user lacks access, or the documents genuinely don't cover it.

Rather than hallucinate, the pipeline aborts LLM generation and returns a safe fallback. The same fallback covers both "not in corpus" and "exists but restricted" — this prevents an attacker from probing what documents exist in restricted departments.

---

## RBAC (Role-Based Access Control)

Standard RBAC maps users to roles and roles to permissions. Cortex extends this with two additional dimensions:

- **Department** — an Engineering employee cannot see HR documents even if both are `Internal`
- **ClearanceLevel** — a `Public`-cleared user in Engineering cannot see `Internal` Engineering documents

All three must pass simultaneously. This is enforced as a Qdrant filter, not in application code, so it cannot be bypassed by prompt injection or application bugs.

---

## Staged Version Flip

Naive document updates — delete old, insert new — create a window where no chunks are searchable. Under load this causes false "no results" responses.

The staged flip solves this:
- Old version is locked (`is_live = False`) but its chunks remain indexed
- New chunks are built silently with `is_live = False`
- Only when all new chunks are confirmed indexed does `is_live` flip to `True` for the new version and old chunks are purged

There is never a gap in searchable content. The worst case is a user briefly sees an older version while the update is in progress.

---

## Action Draft State Machine

When a user asks the system to raise a ticket, the system does not create it immediately. It creates a draft, shows the user what will be created, and waits for explicit confirmation. This prevents accidental ticket creation from ambiguous phrasing.

The draft expires after 15 minutes to prevent orphaned drafts accumulating. Confirmation is idempotent — submitting the same draft ID twice returns the same result without creating a duplicate ticket.

---

## Token Version Revocation

JWTs cannot be invalidated server-side once issued — they are self-contained signed tokens. The standard solution is a token blacklist (Redis set of revoked JIDs), which requires a cache lookup on every request.

Token version is simpler: a single integer in the user's DB row. The JWT embeds the version at issue time. On every request, the version in the token is compared to the version in the DB. To revoke all sessions for a user, increment their version. No blacklist, no extra cache lookup beyond the DB read that `get_current_user` already does.

---

## SSE (Server-Sent Events)

LLM responses can take 5–30 seconds. Waiting for the full response before sending anything produces a bad UX. SSE streams tokens to the frontend as they are generated, so the user sees text appearing word by word.

SSE is a one-directional HTTP connection — server pushes events to client. Each event has a type (`token`, `done`, `error`) and data. The client uses the `Last-Event-ID` header on reconnect so the server can replay missed tokens from Redis.

---

## Audit Log

Every security event, document operation, query, and workflow action is written to `audit_logs` as an immutable append-only record. This is not application logging — it is a compliance record for the admin dashboard and grading rubric.

PII (names, email content) is never written to audit logs. Only IDs, roles, event tags, and status codes.

---
