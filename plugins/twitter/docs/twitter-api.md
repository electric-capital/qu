# Twitter/X API Documentation

This document describes Quest's Twitter/X integration for reading Direct Message conversations, listing bookmarked tweets, looking up individual tweets, and sending DMs. It is packaged as the in-tree `plugins/twitter` plugin (plugin id `twitter`; see [Plugin Architecture](../../../docs/architecture/plugins.md)).

## Overview

Reads go through the `authed_get` tool against the X API v2 directly (`https://api.twitter.com/2/...`): the plugin registers an `api.twitter.com` service entry whose credential loader injects the user's per-user OAuth 2.0 token, proactively refreshed near expiry. Sending DMs goes through the action request system (requiring user approval) via the grandfathered `send_twitter_dm` type. The old `/api/twitter/*` proxy routes are gone (`/api/twitter` is in the route-dispatch blocked-proxy list so cached sessions get a pointer instead of a bare no-route error).

**Access tier requirement:** Basic ($100/month) for DM endpoints. The Free tier returns HTTP 403. Bookmarks and tweet lookup work on the Free tier.

## Key Files

| File | Description |
|------|-------------|
| `plugins/twitter/manifest.py` | The plugin manifest: admin credential schema, oauth-kind user connection, the `api.twitter.com` authed_get entry with its GET allow-list, the `system:twitter` skill, and the `send_twitter_dm` handler registration |
| `plugins/twitter/upstream.py` | Shared upstream access: `load_twitter_client_config()`, token blob handling, proactive refresh under a per-user lock (`get_twitter_token()`), the authed_get loader/injector, and the `twitter_request()` helper |
| `plugins/twitter/oauth.py` | OAuth PKCE flow endpoints (`/auth/twitter`, `/auth/twitter/callback`, `/auth/twitter/disconnect`) |
| `plugins/twitter/handlers.py` | `SendTwitterDmHandler` for sending DMs via action requests |
| `plugins/twitter/instructions.md` | The `system:twitter` skill body (LLM-facing endpoint documentation) |

## Authentication

**1. Quest Authentication:** The `authed_get` tool runs as the authenticated user; there are no Twitter HTTP routes to call directly.

**2. Twitter OAuth 2.0 Token:** Users connect their Twitter/X account via Settings > Data Connections. The token JSON lives in a `user_service_credentials` row (service `twitter`, `oauth_blob` column) -- migration `e2f8a4c61b93` moved it there from the old `users.twitter_oauth` column. Access tokens expire after ~2 hours and are refreshed automatically by `get_twitter_token()` in `plugins/twitter/upstream.py` (5-minute margin, per-user asyncio lock). Refresh tokens are rotated on every use -- the new refresh token is persisted with the fresh access token, or the connection breaks permanently. The service entry additionally sets `retry_on_401` as a backstop.

## OAuth Flow

The OAuth endpoints are the plugin's `oauth_router`, mounted by `mount_plugin_oauth_routers()` under the same URLs as pre-plugin:

- `GET /auth/twitter` -- initiates OAuth, redirects to Twitter authorization page, sets CSRF + PKCE state cookie
- `GET /auth/twitter/callback` -- exchanges code for tokens (sending the PKCE verifier), stores the blob via `upsert_credential`, invalidates sessions to refresh system prompts
- `POST /auth/twitter/disconnect` -- deletes the `twitter` credential row

The callback verifies the CSRF nonce and reads the PKCE code verifier from the signed, session-bound `twitter_oauth_state` cookie (minted and verified by `auth/oauth_state.py`; a cookie without a valid signature, a mismatched nonce, or one bound to a different user is rejected before the token exchange). On success, it closes the popup window (popup mode) or redirects to `/`. The connector row's "Update Available" re-authorize badge comes from the plugin's `needs_reauth` hook: the stored granted-scope string is checked against `TWITTER_SCOPES`, so a blob granted before `bookmark.read` was added flags for reconnect.

## Read Endpoints (authed_get allow-list)

The GET-only allow-list in the `api.twitter.com` service entry (`plugins/twitter/manifest.py`); read-only by construction -- no `allowed_post_endpoints` are defined:

- `/2/users/me` -- authenticated user profile
- `/2/users/{user_id}` -- user lookup by id
- `/2/dm_events` -- list DM events across all conversations -- Basic tier required
- `/2/dm_conversations/{dm_conversation_id}` -- conversation metadata (participants via expansions)
- `/2/dm_conversations/{dm_conversation_id}/dm_events` -- DM events for a specific conversation -- Basic tier required
- `/2/users/{user_id}/bookmarks` -- list bookmarked tweets (own id only upstream) -- requires `bookmark.read` scope
- `/2/tweets/{tweet_id}` -- look up a single tweet

Upstream query parameters (`dm_event.fields`, `expansions`, `pagination_token`, ...) pass through verbatim; the `system:twitter` skill (`plugins/twitter/instructions.md`) documents them for the model.

## DM Conversation ID Format

- **1:1 conversations**: `{smaller_user_id}-{larger_user_id}` (e.g., `123456-789012`)
- **Group conversations**: plain integer string

## Sending DMs (Action Request)

Sending Direct Messages uses the action request system rather than a direct POST. This requires explicit user approval before any DM is sent. Implementation is in `SendTwitterDmHandler` in `plugins/twitter/handlers.py`. `send_twitter_dm` predates the plugin packaging and is persisted in old `action_requests` rows, so it rides on the manifest's `unprefixed_action_types` grandfather list.

`validate_params()` format-checks the recipient ids before persisting an `action_requests` row.

- `participant_id` must be a numeric Twitter user id (digits only, up to 20 digits -- Twitter's `id_str` upper bound), which rejects `@handles`, profile URLs, the literal `me` keyword, leading `+`, and `<user_id>-<user_id>` shapes (which are actually `dm_conversation_id`s).
- `dm_conversation_id` must be either a plain numeric group id or `<user_id>-<user_id>` for a one-to-one DM (each side up to 20 digits).
- Passing both ids is rejected with `Provide exactly one of dm_conversation_id or participant_id, not both`. The both-missing rule and the 10,000-character `message` cap also apply; whitespace-only ids are treated as absent.

Format errors name the field and echo the offending value truncated to ~100 chars so the model self-corrects on the same turn (no `action_requests` row, no card, no wait handle). Coverage is in `plugins/twitter/tests/test_send_twitter_dm_validation.py`.

The handler's `enrich_params_for_preview` hook resolves the recipient to `@username (Name)` for the approval card (participant lookup, or conversation participants minus the authenticated account). The label is always derived server-side from the same `participant_id` / `dm_conversation_id` that `execute()` delivers to; `recipient_name` is not a model-supplied parameter (it is rejected as an unknown key), so the card cannot name a different account than the one the DM goes to. Resolution failures never block the card, which then falls back to the raw id.

See [Action Requests Architecture](../../../docs/architecture/action-requests.md) for the full action request lifecycle.

## System Instructions Integration

The `system:twitter` skill carries the endpoint documentation and is gated on the plugin's `connected_services` key (`requires="twitter"`): server credentials configured AND the user's stored `twitter` row connected. Content lives in `plugins/twitter/instructions.md`.

## Constraints

- **Basic tier required:** DM read/write endpoints return HTTP 403 on the Free tier. Bookmarks and tweet lookup work on all tiers
- **Rate limiting (DMs):** 15 requests per 15-minute window per user
- **Rate limiting (bookmarks):** 180 requests per 15-minute window per user
- **Rate limiting (tweet lookup):** 900 requests per 15-minute window per user
- **Scope requirement (bookmarks):** The `bookmark.read` scope must be authorized. Users who connected before this scope was added get the re-authorize badge and must reconnect
- **Token expiry:** Access tokens expire after ~2 hours; automatic refresh via refresh token rotation
- **Refresh token rotation:** Twitter invalidates the old refresh token on every successful refresh -- the rotated token is persisted under a per-user lock so concurrent tool calls cannot race a stale one
- **Max results:** 100 per page maximum for DMs and bookmarks

## Design Decisions

**Why action requests for DM sends instead of a direct POST?**
Sending messages to other people is a write operation with real-world consequences. Routing it through the action request system ensures the user explicitly approves each DM before it is sent, matching the same pattern used for Slack messages and Telegram messages.

**Why authed_get instead of the old `/api/twitter/*` proxy routes?**
Plugins deliberately cannot mount `/api/*` routes (LLM-reachable via `curl_proxy_get` with no gating). The X API v2 is a plain Bearer-token REST API, so a pass-through `authed_get` service entry with a GET allow-list expresses the whole read surface -- the Ramp pattern. Tokens never reach the frontend or the sandbox; the model passes upstream query parameters directly instead of the proxy's renamed ones.

**Why does the model resolve its own user ID for bookmarks?**
The X API v2 bookmarks endpoint requires the user's Twitter ID in the URL path (`/2/users/{id}/bookmarks`). The old proxy resolved it server-side per request; with direct authed_get access the skill instructs the model to call `/2/users/me` once and reuse the id, saving the hidden extra upstream call.

**Why store `refresh_token` and `expires_at`?**
Access tokens expire after ~2 hours. The `expires_at` timestamp allows proactive refresh (5-minute safety margin) before the token actually expires, preventing mid-conversation failures.
