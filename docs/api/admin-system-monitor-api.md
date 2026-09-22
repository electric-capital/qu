# Admin System Reports API

REST endpoints powering the admin-only `/admin/system-reports` page (UI title "System Reports"; the API prefix keeps the historical `system-monitor` name). See [Admin System Reports Architecture](../architecture/admin-system-monitor.md) for the page itself and section design.

**Endpoint prefix:** `/app/api/admin/system-monitor`

## Key Files

| File | Description |
|------|-------------|
| `chat/routes/admin.py` | Endpoint implementations (`admin_latest_active_conversations`, `admin_most_expensive_conversations`, `admin_user_report`, `admin_guides_report`), the shared `_admin_conversation_view` row assembler, and the shared `_resolve_range_window` date parser |
| `db/conversation_store.py` | `list_latest_active_conversations()`, `get_conversations_with_users()`, and `list_conversation_activity_rows()` (all-conversation owner/routine/activity projection for the user report) -- queries and row shapes |
| `db/llm_call_store.py` | `get_usage_by_model_for_conversations()` (batched per-conversation, per-model native-token aggregation), `get_most_expensive_conversations()` (date-range known-cost ranking), `get_usage_by_user()` (date-range per-user fold with routine cost split), `get_latest_context_tokens_for_conversations()` (batched latest top-level context size) |
| `db/guide_store.py` | `list_all_guides()` -- every user guide joined to its owner, with the per-guide routine reference count (guides report) |
| `db/project_store.py` | `list_all_project_guides()` -- projects with non-empty `projects.guide` instructions joined to their owner (guides report) |
| `db/llm_pricing.py` | Static per-model USD list-price table (both providers, cache read/write rates incl. the Anthropic 5m/1h TTL split, Gemini long-context tier) and `estimate_cost_usd()` |
| `chat/storage.py` | `ChatStorage._resolve_list_title` (title precedence), `count_user_message_active_days()` (distinct user-message days, whole lifetime), and `user_message_active_days()` (the range-clipped day-set variant) |
| `chat/auth.py` | `is_admin()` -- admin-email check used by all admin endpoints |
| `frontend/src/api/client.ts` | `fetchLatestActiveConversations()`, `fetchMostExpensiveConversations()`, `fetchAdminUserReport()`, `fetchAdminGuidesReport()` client functions |
| `frontend/src/api/config.ts` | `adminLatestActiveConversations`, `adminMostExpensiveConversations`, `adminUserReport`, `adminGuidesReport` URL builders |
| `frontend/src/api/types.ts` | `AdminActiveConversation`, `AdminConversationModelUsage`, `AdminConversationUsageTotal`, `AdminUserReportRow`, `AdminGuideReportRow`, response types |

## Authentication

Dual auth (session cookie OR API key Bearer) like the rest of `/app/api/*`, plus an inline `is_admin(user["email"])` check at the top of every handler. Non-admins receive HTTP 403 `forbidden`. The admin check runs against the *effective* user, so an admin who is currently impersonating someone else is treated as non-admin and gets 403 -- matching the other admin endpoints in `chat/routes/admin.py`.

## Shared Conversation Row Shape

The two conversation-level endpoints return `{"conversations": [...]}` where each row is an `AdminActiveConversation` (see `frontend/src/api/types.ts`), assembled by `_admin_conversation_view` in `chat/routes/admin.py`:

- Identity/metadata: `id`, resolved `title` (via `ChatStorage._resolve_list_title`), owner `user_id`/`user_email`/`user_name`, `project_id`, `routine_id` (non-null when a routine created the conversation; the FE badges these rows), `last_message_at`, `origin`, `last_model` (a proxy sourced from `Conversation.model` -- see the [last-model caveat](../architecture/admin-system-monitor.md#last-model-caveat)).
- Token usage: `usage_by_model` (per-model breakdown ordered heaviest-first, covering every model the conversation used including sub-agent models) and `usage_total` (a coarse cross-provider `{call_count, total_tokens, estimated_cost_usd}` magnitude -- native buckets don't sum meaningfully across providers, but an all-in magnitude and a dollar sum do; `estimated_cost_usd` is `null` when any model in the conversation lacks a pricing entry, so a partial sum never reads as the full cost). Each `usage_by_model` entry is a discriminated union on `provider`: `{model, provider, call_count, total_tokens, estimated_cost_usd, metrics}`, where `estimated_cost_usd` is a list-price estimate from `db/llm_pricing.py` (`null` for models without a pricing entry) and `metrics` carries that provider's summed NATIVE token fields (see `AdminGeminiModelUsage` / `AdminAnthropicModelUsage` in `frontend/src/api/types.ts` and the shape docstring on `get_usage_by_model_for_conversations()` in `db/llm_call_store.py`). The aggregation runs one batched grouped query per provider table (no N+1) and additionally groups on a per-call long-context flag (context > 200K tokens) so tier-priced models (e.g. Gemini Pro's >200K rates) are costed per call, not against the summed context -- the tier buckets merge back into one displayed entry per model. Costs are list-price estimates: batch discounts, negotiated pricing, and promo rates are not modeled.
- `latest_context_tokens` -- input-side token count of the conversation's most recent TOP_LEVEL call in either provider table (Gemini: `prompt_token_count`; Anthropic: input + cache_read + cache_creation), i.e. what the next turn would re-read; `null` when no top-level calls are recorded. Batched via `get_latest_context_tokens_for_conversations()`.
- `active_days` -- distinct UTC days with at least one user message over the whole conversation lifetime (never clipped to a query window), from `ChatStorage.count_user_message_active_days()`.

## Endpoints

All endpoints are defined in `chat/routes/admin.py`. See that file for parameter defaults, clamping, and error shapes.

- **GET `/admin/system-monitor/latest-active-conversations`** -- Return the most recently active conversations across all users (`admin_latest_active_conversations`). Sorted by `Conversation.last_message_at DESC`. The `limit` query parameter is clamped 1..100 and defaults to 20. The `include_routines` boolean query parameter (default `true`) filters out routine-created conversations (`routine_id` set) in SQL when `false`, so the list stays `limit` rows long.

- **GET `/admin/system-monitor/most-expensive-conversations`** -- Return the conversations with the highest estimated cost in a date range (`admin_most_expensive_conversations`), ranked most-expensive-first. `start` and `end` are optional inclusive ISO dates (`YYYY-MM-DD`) interpreted as UTC midnights (the timezone the `llm_calls_*` rows are stamped in); either may be omitted for an open-ended range, and `start > end` or a malformed date is a 400 `invalid_params`. `limit` is clamped 1..100 and defaults to 30. Ranking (see `get_most_expensive_conversations()` in `db/llm_call_store.py`) sums each conversation's *known* per-model cost estimates over calls inside the window -- unpriced models contribute nothing to the rank but still null out the displayed `usage_total.estimated_cost_usd` -- with ties broken on total tokens. The usage sums cover only in-range calls; `latest_context_tokens` and `active_days` remain whole-conversation values. Conversations deleted since their calls were made still rank (the raw tables are append-only): they return a `"(deleted conversation)"` title, `null` metadata, and owner identity resolved from the call rows' `user_id`.

- **GET `/admin/system-monitor/user-report`** -- Return per-user activity and cost aggregates for a date range (`admin_user_report`): `{"users": [...]}` with one `AdminUserReportRow` per user (the whole roster from `list_all_users()` -- zero-activity users keep a row; call rows referencing a deleted user get a placeholder identity), sorted by known in-range cost descending (ties: total tokens, then active days, then email). `start`/`end` share the most-expensive endpoint's semantics (inclusive UTC ISO dates, either omissible, 400 on malformed/reversed) via the shared `_resolve_range_window` helper; no `limit` (the user base is small and bounded). Each row carries:
  - `active_days` -- distinct UTC days with at least one user message across the user's **non-routine** conversations, clipped to the range: the union of `ChatStorage.user_message_active_days()` day sets so one day across several chats counts once. Read from chat_history.json (rows pruned by `created_at`/`last_message_at` bounds before the file read), so deleted conversations contribute none.
  - `conversation_count` / `routine_conversation_count` -- distinct conversations with at least one recorded call in the range, split on the surviving conversation row's `routine_id`; calls of deleted conversations count as non-routine (routine provenance dies with the row).
  - `cost_excluding_routines_usd` / `cost_routines_usd` -- estimated in-range cost split the same way; each is `null` when any model in *that split* lacks a pricing entry (the usual partial-sum convention, applied per split so an unpriced model in a routine never hides organic spend).
  - `routine_costs` -- the routine split itemized per routine (`AdminUserRoutineCost`, empty when the user ran no routines in range): `routine_id`, `routine_name`, `project_id` / `project_name` (the owning project, from one batched `get_routine_labels()` lookup in `db/routine_store.py`), `conversation_count` (distinct routine-created conversations with a call in range) and `cost_usd` (`null` when any model used under *that routine* is unpriced). Sorted by the routine's priceable cost descending (ties: routine id), computed alongside the split in `get_usage_by_user()`; the ranking key itself is not returned. A routine that vanished between the conversation scan and the label lookup is labeled `"(deleted routine)"` with null project fields.
  - `usage_by_model` / `usage_total` -- the same shapes as the conversation rows, aggregated across **all** the user's conversations in range (routines included), one merged entry per model. Aggregation is `get_usage_by_user()` in `db/llm_call_store.py`, folding the same tier-bucketed (conversation, model) buckets by the call rows' `user_id`.

- **GET `/admin/system-monitor/guides-report`** -- Return every guide in the system with its owner (`admin_guides_report`): `{"guides": [...]}`, one `AdminGuideReportRow` per row, no query parameters (a current-state inventory for tracking down remaining guide users before the deprecated feature is removed). Two row kinds share one key set, discriminated by `kind`:
  - `kind: "user"` -- one row per `guides` table row, **including** empty auto-created default guides (`is_default`), from `list_all_guides()` in `db/guide_store.py`. `routine_count` is the number of routines whose `guide_id` still points at the guide (the last remaining code path that applies a guide to new conversations). `project_id` and `public` are `null`.
  - `kind: "project"` -- one row per project whose `projects.guide` Project Instructions text is non-empty, from `list_all_project_guides()` in `db/project_store.py`; `name` is the project name, `project_id` equals `id`, `public` mirrors the project flag. `is_default` and `routine_count` are `null`.
  - Shared: `id`, `name`, owner `user_id`/`user_email`/`user_name`, `content_length` (the text itself is never returned), `created_at`, `updated_at`. Sorted by owner email, then user guides (default first) before project rows, then case-insensitive name.

## Error Responses

- `403 forbidden` -- caller is not an admin (or is an admin currently impersonating another user)
- `401` -- no/invalid session and no valid API key (dual-auth dependency)
- `400 invalid_params` -- malformed `start`/`end` date, or `start` after `end` (ranged endpoints only)
