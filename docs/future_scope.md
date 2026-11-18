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

## 2. Moderate Complexity Scope (Medium-Term Enhancements)

These features involve integrations with external worker pipelines, scheduled cron tasks, or cross-service state synchronization.

### 2.1 Automated Cloud Storage Change Data Capture (CDC)
- **Real-time Webhooks for Cloud Drives**:
  - Connect Google Drive Push Notifications and Microsoft OneDrive Webhooks to the Celery worker queue.
  - Automatically detect when a source document is edited, renamed, or deleted in the cloud drive.
  - Re-trigger ingestion, chunking, and embedding with zero administrator intervention.
- **Diff-Based Version Reindexing**:
  - Compute structural diffs between document versions so only modified sections are re-embedded, reducing LLM token consumption and embedding costs by up to 80%.

### 2.2 Granular Multi-Tenant & Workspace Scoping
- **Virtual Team Spaces**:
  - Allow enterprise organizations to partition the knowledge base into isolated virtual workspaces (e.g., *Alpha Team*, *M&A Due Diligence*, *Legal Compliance*).
  - Enforce cross-workspace firewall rules preventing vector similarity bleed across different client accounts.

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
