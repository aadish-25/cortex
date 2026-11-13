# Frontend Specifications — Cortex (AAD-H1)

Stack: React + Vite + TypeScript

---

## Pages

### `/login`
- Email + password form
- On success: stores JWT in `httpOnly` cookie (set by backend), redirects to `/chat`
- On failure: inline error message, no page reload

### `/chat`
- Main application shell
- Left sidebar: conversation list, new conversation button
- Main area: message thread + input box
- No separate dashboard page — admin features live in a separate `/admin` route

### `/admin`
- Accessible only to users with `role = Admin`
- Tabs: Documents, Users, Audit Log, Quality Dashboard

---

## Chat UI

### Message Thread
- User messages: right-aligned
- Assistant messages: left-aligned with Cortex avatar
- Streaming: tokens appear word by word as SSE events arrive
- Citations: rendered as inline chips `[Document Title, p. X]` that expand to a source popover on click
- Error state: shows retry button if `retryable: true` in the error SSE event

### Input Box
- Textarea, expands vertically up to 5 lines
- Send on Enter, newline on Shift+Enter
- Disabled while a response is streaming

### Feedback Controls (per assistant message)
- Thumbs up / Thumbs down buttons
- On thumbs down: optional dropdown (`INACCURATE`, `MISSING_CONTEXT`, `OUTDATED`, `OTHER`) + free text field
- Regenerate button: appears after every assistant message
- On regenerate: optional prompt "What was wrong?" (dismissible), then sends regeneration request

### Regeneration
- New message appears below the original with a `Variant 2` label
- Both variants remain visible in the thread
- User can toggle between variants

---

## Admin — Documents Tab
- Table: filename, department, clearance level, version, status, uploaded at, actions
- Upload button → file picker (PDF, DOCX, MD, TXT only) + department selector + clearance selector
- Status badge: `ACTIVE` (green), `UPDATING` (yellow), `UPDATE_FAILED` (red)
- Delete button → confirmation modal

## Admin — Users Tab
- Table: email, role, department, clearance level, is_active, created at
- Edit role / clearance inline
- Deactivate button (sets `is_active = False`, bumps `token_version` to -1)

## Admin — Audit Log Tab
- Read-only table with filters: category, status, date range
- No PII displayed — only user_id, event_tag, status, timestamp

## Admin — Quality Dashboard
- Cards: total queries today, avg reranker score, fallback rate, thumbs up %, thumbs down %
- Table: recent low-confidence queries (score < threshold) for manual review

---

## API Communication

All requests include the JWT via `Authorization: Bearer <token>` header.

SSE endpoint: `GET /api/v1/chat/stream?conversation_id=<id>&message=<text>`

On `event: session_terminated` → redirect to `/login` immediately.

On `event: error` with `retryable: true` → show retry button, resend with `retry_token` from event data.

---

## State Management

- Auth state: React Context
- Chat state: React Query for conversation list, local state for active message thread
- SSE: native `EventSource` API, reconnects automatically using `Last-Event-ID`

---
