# Cortex frontend redesign

## Visual direction

The previous frontend has been replaced with a restrained enterprise workspace rather than a generic AI-chat dashboard:

- Warm off-white surfaces and a quiet blue accent in light mode.
- Dark mode uses near-black surfaces (`#090a0c`) with the same blue accent; it is not a dark-blue/purple theme.
- DM Sans for interface text and Manrope for headings/brand. No mono/robotic display font is used for ordinary UI copy.
- No gradients, oversized glowing panels, decorative AI artwork, or excessive card chrome.
- Citation anchors render as compact `[1]` chips that open the relevant source context.

## Layout and scrolling

`AppShell` uses a fixed viewport-height grid. The sidebar is `100dvh` and never grows with page content. Its logo/header and account footer are non-scrolling; only the conversation history region scrolls. The main content has its own scroll container. The chat page has a separate message scroller and composer region, so long answers do not push the account control down the page.

The bottom-left account control opens a profile menu containing the signed-in user's name, email, role, department, clearance, profile/memory navigation, appearance preference, and sign-out action.

## Route structure

| Route | Purpose |
|---|---|
| `/login` | Sign in using username/email and password |
| `/register` | Request an account; approval is required before sign-in |
| `/chat` | New conversation |
| `/chat/:conversationId` | Existing conversation with paginated history |
| `/profile` | User details, theme preference, and saved-memory management |
| `/admin/overview` | Service health, provider configuration, usage and answer-quality summary |
| `/admin/documents` | Paginated document inventory, upload, replace, reindex, rollback and processing logs |
| `/admin/users` | Paginated user management, role/clearance updates and account activation |
| `/admin/quality` | Reranker/fallback metrics and paginated low-confidence review queue |
| `/admin/audit` | Paginated, filterable audit trail |

## Chat interactions

- The model selector is populated by `/api/v1/chat/available-models`.
- The chat stream is consumed with `fetch` and a small SSE parser, rather than native `EventSource`, because the API is a POST endpoint using cookie authentication and a request body.
- Reconnect attempts reuse the same assistant message ID and send `Last-Event-ID`; the UI does not submit a second user turn for a replay.
- Inline citations open the right-side temporary Sources drawer. The three-dot answer menu also contains **View sources** and **Regenerate answer**.
- The drawer lists all anchored citations. Internal citations load the cited chunk plus its preceding and succeeding chunks; external references open their cited URL in a new tab.
- Regenerated variants are grouped under the originating user turn and can be switched between.
- Partial responses remain visibly marked as interrupted after reloading history.
- Action drafts expose explicit confirm/discard actions; suggestions are clickable.

## Module map

```text
frontend/src/
  api/          typed API client, error handling, SSE parser, shared types
  auth/         cookie-session auth provider
  layouts/      AppShell, fixed Sidebar, account/profile menu
  components/   route guards, pagination, modal, citation renderer, source drawer
  chat/         chat history, SSE state, variants, citations, feedback, action cards
  admin/        overview, documents, users, quality, audit pages
  pages/        login, registration, profile
  styles.css    shared design tokens, light/dark themes, responsive layout
```

## Pagination implemented in the UI

- Conversations: 30 per page, older conversations appended on demand.
- Conversation messages: 40 per page, earlier messages prepended.
- Documents: 20 per page.
- Users: 25 per page.
- Audit records: 50 per page.
- Low-confidence queries: 25 per page.
- Personal memories: cursor pagination, 25 per request.
- Document processing history: 25 per page.

## API assumptions

The frontend expects the paginated response shape `{ items, page, page_size, total, total_pages }` for offset-paginated resources. Personal memories use `{ items, total, next_cursor }`. Authentication is carried in the HttpOnly `access_token` cookie; the frontend does not store bearer tokens in local storage.
