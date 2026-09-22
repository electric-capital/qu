# Admin System Reports

Admin-only operator dashboard at `/admin/system-reports` (titled "System Reports"; the pre-rename `/admin/system-monitor` URL redirects, and the API prefix keeps the historical `system-monitor` name). A left-hand nav switches between report sections; ships with "Latest Conversations", "Cost Analysis", "Users", and "Guides".

## Overview

System Reports gives admins a global view of activity and spend across all users. Unlike [Admin Impersonation](admin-impersonation.md), nothing here switches identity -- it is read-only observation. The page is a thin frame: a header, a left-hand section nav (`SECTIONS` in `AdminSystemReportsPage.tsx` -- adding a report is one entry plus its component), and the selected section. Each section owns its own data fetching, error state, and styling. Section choice is local component state, not a URL segment.

Admin gating is enforced on the backend via the inline `is_admin(user["email"])` check used by every other endpoint in `chat/routes/admin.py` (returns 403 for non-admins, including admins who are currently impersonating another user). The frontend menu-item hide and page-level guard are UX niceties, not security boundaries.

## Key Files

**Backend:**
- `chat/routes/admin.py` -- `admin_latest_active_conversations`, `admin_most_expensive_conversations`, `admin_user_report`, and `admin_guides_report` handlers (admin check, limit clamping, date-range parsing via the shared `_resolve_range_window`, title resolution) sharing the `_admin_conversation_view` row assembler
- `db/conversation_store.py` -- `list_latest_active_conversations()` (joins `Conversation` to `User`, orders by `last_message_at DESC`) and the batch companion `get_conversations_with_users()`, sharing `_admin_conversation_row`; plus `list_conversation_activity_rows()`, the narrow all-conversation owner/routine/activity projection behind the user report
- `db/llm_call_store.py` -- `_collect_usage_buckets()` grouped `GROUP BY (conversation_id, model, long_context_flag)` aggregation over the per-provider `llm_calls_gemini` / `llm_calls_anthropic` raw tables (one query per table, optional conversation-id and `created_at`-window filters), behind `get_usage_by_model_for_conversations()` (batch), `get_most_expensive_conversations()` (date-range top-N ranking), and `get_usage_by_user()` (date-range per-user fold with the routine cost split); plus `get_latest_context_tokens_for_conversations()` (batched latest top-level call context size)
- `db/llm_pricing.py` -- static per-model USD list-price table and `estimate_cost_usd()` (cache read/write rates incl. the Anthropic 5m/1h TTL split; Gemini long-context tier >200K)
- `db/guide_store.py` / `db/project_store.py` -- `list_all_guides()` (every user guide joined to its owner plus a per-guide routine reference count) and `list_all_project_guides()` (projects with non-empty `projects.guide` instructions) behind the guides report
- `chat/storage.py` -- `ChatStorage.count_user_message_active_days()` (distinct UTC days with user messages, from chat_history.json) and the range-clipped day-set variant `user_message_active_days()`

**Frontend:**
- `frontend/src/pages/AdminSystemReportsPage.tsx` / `.css` -- Shell page (admin guard, header, left nav, section container, `AdminOpsMenu` overlay)
- `frontend/src/components/LatestActiveConversationsTable.tsx` / `.css` -- "Latest Conversations" polling section
- `frontend/src/components/CostAnalysisTable.tsx` / `.css` -- "Cost Analysis" section (date-range selector + ranked table, fetch-on-demand)
- `frontend/src/components/UsersReportTable.tsx` / `.css` -- "Users" section (date-range selector + per-user activity/cost table, fetch-on-demand)
- `frontend/src/components/GuidesReportTable.tsx` / `.css` -- "Guides" section (deprecation tracker: every user guide and project guide with owner, fetch-on-demand, client-side "Hide empty" toggle)
- `frontend/src/components/ReportDateRange.tsx` / `.css` -- shared date-range picker (presets + custom start/end, `resolveRange` date math) used by Cost Analysis and Users
- `frontend/src/components/ConversationUsageCell.tsx` / `.css` -- shared per-model token-usage breakdown cell (native-field row format + exact-numbers tooltip), used by both tables
- `frontend/src/components/AdminOpsMenu.tsx` -- "System Reports" menu item (admin-only, hidden during impersonation; sibling to "Impersonate user" and the shutdown button)
- `frontend/src/App.tsx` -- `AdminSystemReportsRoute` at `/admin/system-reports`, plus a legacy `/admin/system-monitor` redirect
- `quest.py` -- `serve_spa_admin` SPA catch-all for `/admin/{rest:path}` so direct browser loads serve `index.html` instead of 404 (safe because all admin API routes live under `/app/api/admin/...`; see [Frontend -- Backend SPA Support](frontend.md#backend-spa-support))
- `frontend/src/api/client.ts` -- `fetchLatestActiveConversations()`, `fetchMostExpensiveConversations()`, `fetchAdminUserReport()`, `fetchAdminGuidesReport()`
- `frontend/src/api/config.ts` -- `adminLatestActiveConversations`, `adminMostExpensiveConversations`, `adminUserReport`, `adminGuidesReport` URL builders
- `frontend/src/api/types.ts` -- `AdminActiveConversation`, `AdminConversationModelUsage`, `AdminConversationUsageTotal`, `AdminUserReportRow`, `AdminGuideReportRow`, response types
- `frontend/src/utils/formatters.ts` -- `formatRelativeTimestamp` shared by both tables' last-active columns

See [Admin System Reports API](../api/admin-system-monitor-api.md) for the endpoint contracts.

## Shared Row Columns

The two conversation-level sections render the same conversation row shape (assembled by `_admin_conversation_view` in `chat/routes/admin.py`):

- **Token usage** -- per-model provider-native breakdown plus a bold coarse total ("N tokens · M calls · ~$X"); see below.
- **Context** -- `latest_context_tokens`: the input-side token count of the conversation's most recent TOP_LEVEL call in either provider table (Gemini: `prompt_token_count`; Anthropic: input + cache_read + cache_creation) -- what the next turn would re-read. Fetched batched by `get_latest_context_tokens_for_conversations()` (one grouped `MAX(id)` subquery per provider table; the later call wins when a conversation switched providers). Same formula as the single-conversation `get_latest_context_tokens()` used by the expensive-resume warning.
- **Active days** -- `active_days`: distinct UTC days with at least one user message over the whole conversation lifetime (not clipped to any query window), computed by `ChatStorage.count_user_message_active_days()` from message timestamps in chat_history.json (one file read per displayed row; fine at ≤30 rows).

**Per-model token usage and estimated cost.** Token consumption is broken down per model used by the conversation (including sub-agent models) in the shared `ConversationUsageCell` component. The route handlers attach `usage_by_model` (provider-discriminated per-model line items with native-field `metrics` and `estimated_cost_usd`, heaviest-first) and `usage_total` (a coarse `{call_count, total_tokens, estimated_cost_usd}` magnitude). Costs are list-price estimates computed server-side from `db/llm_pricing.py`: the grouped query also groups on a per-call long-context flag (context > 200K tokens) so tier-priced models (Gemini Pro's >200K rates) are costed against each call's own context size, then the tier buckets merge back into one displayed entry per model. A model without a pricing entry shows no per-model dollar figure and nulls out the conversation total (a partial sum would read as the full cost). The cell renders each line item in a provider-specific format (`formatModelUsageRow`): Gemini rows as `prompt P (cached C) · out O (+T thoughts)`, Anthropic rows as `in I · cache-r R · cache-w W · out O`, each suffixed with `· ~$X` when priced (green `tokens-cost` span; `<$0.01` below a cent); the row tooltip (`formatModelUsageTooltip`) shows the full native field names and values plus the call count and a 4-decimal cost line, including the Anthropic 5m/1h cache-write TTL split (tooltip-only). See [Database -- llm_call_store](database.md#data-access-layer) for the query.

**Title resolution.** The route handlers call `ChatStorage._resolve_list_title` with the row dict, so titles use the same precedence (`custom_name`, then `auto_title`, then a generated default) as the sidebar.

## Latest Conversations Section

Renders the global top-N most-recently-active conversations across all users. Each row shows the resolved title, owner (display name + email), the shared columns above, the conversation's last-used model, and a relative-time last-active label.

**Routine badge and filter.** Conversations created by a routine (`Conversation.routine_id` set -- covers both scheduled runs and one-click manual runs; the two are not distinguishable at the conversation level) get a ⏰ prefix on the title cell. An "Include Routines" checkbox in the section header (on by default) controls whether routine conversations appear: turning it off re-fetches with `include_routines=false` so the server filters them out in SQL and the list stays full-length, while a client-side filter pass on the rows already in hand makes the toggle take effect instantly before the re-fetch lands.

**Activity definition.** "Active" is keyed off `Conversation.last_message_at`, the canonical most-recent-activity column already maintained by the conversation store. The store layer bumps this column on user input, model output, and tool-call completion -- exactly the signals an operator dashboard cares about. The query orders by this column descending; see `list_latest_active_conversations()` in `db/conversation_store.py`.

**Polling cadence.** The section polls every 60 seconds with three guards (see `LatestActiveConversationsTable.tsx`):
- Visibility pause: interval-driven polls skip the request when `document.hidden` is true; a `visibilitychange` listener triggers an immediate fetch when the tab becomes visible again.
- In-flight dedupe: a ref guards against overlapping requests if the network is slow (applies to both interval and manual fetches).
- Cancellation: unmount cancels pending state updates.

**Freshness indicator and manual refresh.** The header shows a live seconds-granularity elapsed label ("Updated 5s ago" / "2m ago" / "1h ago") driven by a 1s ticker (`formatElapsed`, distinct from the minute-granularity `formatRelativeTimestamp` used for the last-active column). The anchor is set only on a *successful* fetch, so during failed polls the label keeps growing while the error strip shows what went wrong. A manual refresh button (lucide-react `RefreshCw`) re-fetches immediately and resets the timer; it bypasses the hidden-tab guard (the click implies a visible tab) and is disabled with a spinning icon while a fetch is in flight.

A future iteration may replace polling with an admin-scoped per-user global on the [persistent WebSocket](realtime.md); for v1 polling is fine because the section is admin-only and the row count is small.

## Cost Analysis Section

Renders the top-30 most-expensive conversations for a selected date range, ranked most-expensive-first with a rank column and a prominent green "Est. cost" column.

**Date range selector.** The shared `ReportDateRange` component (also used by the Users section): a preset dropdown (Last 7 / 30 / 90 days, Last 12 months, All time -- see `RANGE_PRESETS` in `ReportDateRange.tsx`) plus a "Custom range" option that swaps in two date inputs (either side may be left empty for an open-ended bound). Presets resolve to UTC dates ("Last N days" includes today, so the window starts N-1 days back) because the `llm_calls_*` rows are stamped in UTC; the backend interprets the inclusive `start`/`end` dates as UTC midnights.

**Ranking.** Server-side in `get_most_expensive_conversations()`: the same tier-bucketed grouped queries as the batch aggregation, filtered to the `created_at` window instead of a conversation-id list, ranked by each conversation's *known* cost (the sum of per-model estimates that have a pricing entry) so a conversation mixing priced and unpriced models still ranks by what can be priced -- while the displayed total keeps the null-when-partial convention (the FE shows "n/a" with a tooltip pointing at the per-model tooltips). Ties break on total tokens. Only calls inside the window count toward the ranking and the displayed sums; the Context and Active days columns are whole-conversation values.

**Deleted conversations.** The `llm_calls_*` tables are append-only and outlive conversation deletion, so a deleted conversation can still rank. It renders with a "(deleted conversation)" title, no last-active timestamp, and its owner resolved from the call rows' `user_id` (one `users` lookup per distinct missing owner).

**Fetching.** No polling -- cost analysis is an on-demand report. The section fetches on mount, on every range change, and via a manual refresh button; a monotonic fetch counter discards stale responses so a slow wide-range query can never overwrite a newer selection.

## Users Section

Renders one row per user for the selected date range (same `ReportDateRange` picker and fetch-on-demand pattern as Cost Analysis, incl. the stale-response guard), sorted by known in-range cost. Columns per row (`AdminUserReportRow`):

- **Active days** -- distinct UTC days with at least one user message across the user's *non-routine* conversations, clipped to the range: the server unions per-conversation day sets (`ChatStorage.user_message_active_days()`) so one day spent in several chats counts once. Unlike the conversation tables' lifetime `active_days`, this one IS range-clipped.
- **Convos** -- distinct non-routine conversations with at least one recorded call in the range (the routine count rides in the cell tooltip).
- **Cost / Routine cost** -- the estimated in-range spend split on the conversation rows' `routine_id`; each split independently follows the null-when-any-model-unpriced convention, so an unpriced model inside a routine never hides organic spend (and vice versa). A zero split renders as an em dash, an unpriced split as "n/a" with a tooltip.
- **By routine** -- the routine split itemized per routine so the most expensive routines stand out: one row per routine with at least one call in the range (`routine_costs`), showing the routine name, its project name (routine names are only unique per project) and the routine's estimated cost, ranked by the priceable portion (an unpriced routine shows "n/a" but still sorts by what CAN be priced); the row tooltip carries the run count and the 4-decimal figure. The `RoutineCostsCell` collapses past the top five rows behind a "+N more" toggle (`ROUTINE_ROWS_COLLAPSED` in `UsersReportTable.tsx`). Names come from one batched `get_routine_labels()` lookup in `db/routine_store.py`; a routine deleted mid-request shows as "(deleted routine)" (routine deletes null the conversations' `routine_id`, so steadily-deleted routines simply fold into the non-routine split).
- **Token usage** -- the shared `ConversationUsageCell`, fed per-user aggregates: one merged entry per model across ALL the user's conversations in range, routines included (`get_usage_by_user()` in `db/llm_call_store.py` folds the same tier-bucketed (conversation, model) buckets by the call rows' `user_id`, so the tier pricing stays exact).

The roster comes from `list_all_users()` -- zero-activity users keep their row so the report doubles as a "who is not using Quest" view. Calls whose conversation row was deleted count as non-routine (routine provenance dies with the row) and contribute no active days (the chat history file is gone); calls whose *user* was deleted keep their spend on a placeholder "(unknown user)" row. See [Admin System Reports API](../api/admin-system-monitor-api.md) for the exact row contract.

## Guides Section

A deprecation tracker for the retired [guides](guides.md) feature: guide creation is already disabled, and this section lists who still has guides so the remaining users can be contacted before the feature is removed entirely. It renders one row per guide in the system, sorted by owner email (fetch-on-demand: on mount and via the refresh button; no date range -- guides are a current-state inventory, not an activity stream). Two kinds of rows share the table (`AdminGuideReportRow`, `kind` discriminated):

- **User guide** -- a `guides` table row (yellow badge), including the empty auto-created default guides (tagged "default"), so an admin can tell "has a default row" from "wrote instructions". The **Routines** column counts routines still referencing the guide as an override -- the only remaining code path that applies a guide to new conversations -- so a non-zero count marks a guide that is still live, not just stored.
- **Project** -- a project with non-empty Project Instructions (the `projects.guide` text field, blue badge, "public" tag on public projects). Projects with empty instructions are omitted server-side. These are NOT deprecated (Project Instructions outlived the guides feature), but the operator asked to see every guide-shaped prompt in one inventory.

Other columns: **Chars** is the content length (an em dash for empty content -- the text itself is never shipped to the report), **Updated** the row's `updated_at` falling back to `created_at`. The header carries a live count summary (user / project rows shown, distinct owners) and a client-side **Hide empty** checkbox that drops zero-length guides so the real stragglers stand out; the summary reports how many rows the toggle hid.

## Last-Model Caveat

`last_model` is sourced from `Conversation.model`, which is set on first turn and overwritten only by the explicit `PATCH /conversations/{id}` model change. Per-message model is not persisted anywhere. In practice this equals the last used model unless a user just switched models without sending again -- in that case `last_model` reflects the *next* model they intend to use, not the model that produced the most recent output. Documented at the `last_model` field in `db/conversation_store.py:list_latest_active_conversations()`.

## Design Decisions

**Why a shell page with a section nav?**
The page is explicitly designed to grow. Each section owns its own data fetching so new reports can be added by appending to `SECTIONS` in `AdminSystemReportsPage.tsx` without touching unrelated code.

**Why does the API prefix still say `system-monitor`?**
The UI was renamed to "System Reports"; the REST prefix (`/app/api/admin/system-monitor/...`) kept its original name to avoid churning working endpoints. Only the browser-facing route moved (`/admin/system-reports`, with a redirect from the old path).

**Why poll the latest-conversations endpoint instead of pushing via the persistent WebSocket?**
The realtime infrastructure is per-user-scoped; broadcasting global activity to admins would require a new fan-out channel. For an internal dashboard with a small admin audience, 60s polling plus an on-demand manual refresh is cheap and avoids new protocol surface.

**Why clamp `limit` server-side?**
Both handlers cap `limit` at 100 so a crafted query string cannot fan out into a giant scan. The FE only ever requests the defaults (20 latest / 30 most expensive).

**Why rank by known cost instead of skipping unpriced conversations?**
Hiding spend because one model lacks a pricing entry would make the report silently lossy; ranking by the priceable portion keeps every conversation visible while the null total flags the estimate as incomplete.

**Why does the user report scan chat_history.json files for active days?**
User-message days are not derivable from the `llm_calls_*` tables (a call's `created_at` is a model-turn signal, not a user-message signal, and resumed turns blur it further). The report is fetch-on-demand and admin-only, and the scan prunes by each conversation row's `created_at`/`last_message_at` bounds before touching a file, so narrow ranges read few files.

**Why does the guides report ship content length instead of the guide text?**
The report exists to find owners, not to read prompts: an admin needs "who still has non-empty guides, and are any still wired to routines" to reach out before deprecation. Shipping every user's system-prompt text to a dashboard would be a needless disclosure for that purpose, and the length alone separates the empty auto-created default rows from real ones.

**Why hide the menu item while impersonating?**
Mirrors the existing impersonation UI affordance: while pretending to be another user, admin-only entry points are hidden to avoid identity-confusion during debugging sessions. Backend admin checks still 403 even if the link is hit directly during impersonation.
