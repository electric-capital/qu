# Twitter/X App Setup

## Overview

Quest integrates with Twitter/X using OAuth 2.0 PKCE flow, packaged as the in-tree `plugins/twitter` plugin. Each user connects individually to grant access to Direct Message conversations, bookmarks, and tweet data. Access tokens expire after ~2 hours and are refreshed automatically using refresh tokens.

DM access requires the **Basic API tier** ($100/month minimum). Bookmarks and tweet lookup work on all tiers including Free.

## Key Files

- Settings > Service Credentials (admin) -- Twitter/X card (plugin-registered via the manifest's `credential_schema`) managing `client_id` and `client_secret` in the per-service credential store (`twitter.json`); see [Service Credentials](../../../docs/architecture/service-credentials.md)
- `twitter_credentials.json` -- legacy standalone credentials file (never a `server_credentials.json` section), used as a fallback when the store has nothing and migrated into the store at startup. Loaded by `load_twitter_client_config()` in `plugins/twitter/upstream.py`
- `plugins/twitter/upstream.py` -- `TWITTER_SCOPES` constant, token blob handling, `get_twitter_token()` with automatic refresh
- `plugins/twitter/oauth.py` -- OAuth PKCE flow + callback handling
- `db/user_service_credential_store.py` -- per-user token storage (`user_service_credentials` row, service `twitter`, token JSON in `oauth_blob`)
- `chat/gemini_api/session.py` -- `invalidate_user_sessions()` called on connect/disconnect
- `api/instructions.py` -- `get_user_connected_services()` (the plugin loop supplies the `twitter` key)
- `chat/routes/user.py` -- `get_connectors()` for Data Connections UI (the twitter row is the generic plugin oauth row)

## Credential Configuration

A Twitter/X OAuth 2.0 App must be created at the Twitter Developer Portal. Requirements:
- App must be a **Confidential Client** (has a `client_secret`)
- Callback URL: `http://localhost:8000/auth/twitter/callback` (dev) or `https://your-domain.com/auth/twitter/callback` (production). Twitter is strict about exact URL matching
- An admin enters the `client_id` and `client_secret` in Settings > Service Credentials (or, legacy, in `twitter_credentials.json` in the project root -- not `server_credentials.json`)

`twitter_credentials.json` is in `.gitignore` and must never be committed.

Users connect via the Data Connections section in Settings. See [OAuth Popup Flow](../../../docs/architecture/oauth-popup.md).

## OAuth Scopes

Scopes are defined in `TWITTER_SCOPES` in `plugins/twitter/upstream.py`: `dm.read`, `dm.write`, `tweet.read`, `users.read`, `bookmark.read`, plus `offline.access` on the authorize request (refresh tokens; excluded from the reauth subset check).

The `bookmark.read` scope was added after initial integration. Connections whose stored granted-scope string no longer covers `TWITTER_SCOPES` get the "Update Available" re-authorize badge on the Data Connections row (the plugin's `needs_reauth` hook) and must reconnect.

## Token Architecture

- **Per-user tokens** stored as a `user_service_credentials` row (service `twitter`, token JSON in `oauth_blob`); migration `e2f8a4c61b93` moved existing blobs from the old `users.twitter_oauth` column
- Blob fields: `access_token`, `refresh_token`, `expires_at`, `scope`, `scopes`, `token_type`, `authorized_at`
- Access tokens expire after ~2 hours; `get_twitter_token()` checks `expires_at` and refreshes when expired or within 5 minutes of expiry, under a per-user asyncio lock so concurrent tool calls cannot race duplicate refreshes
- Twitter rotates refresh tokens on every use -- both new access and refresh tokens are persisted together in a single `upsert_credential()` call

On connect/disconnect, `invalidate_user_sessions()` from `chat/gemini_api/session.py` is called.

## Access Tier Requirements

DM endpoints (`dm_events`, `dm_conversations`) and `dm.write` require the Basic tier ($100/month). User profile, bookmarks, and tweet lookup work on all tiers. Without Basic, DM calls return HTTP 403.

## Connector Status

Connected when the stored row's `oauth_blob` carries an `access_token` (the plugin's `connected` hook). Surfaced through the generic plugin row in `get_connectors()` and the `twitter` key in `get_user_connected_services()` -- both additionally require the admin credentials to be configured server-side.

## Design Decisions

**Why OAuth 2.0 PKCE instead of OAuth 1.0a?**
Twitter's v2 API (DM endpoints) uses OAuth 2.0. OAuth 1.0a is legacy for v1.1 endpoints. PKCE adds a code challenge to prevent authorization code interception, required by Twitter for confidential clients.

**Why store the refresh token?**
Access tokens expire after ~2 hours. Without refresh tokens (requiring `offline.access` scope), users would need to manually reconnect every 2 hours.

**Why a Confidential Client?**
Confidential clients can use HTTP Basic Auth for token refresh without PKCE on the refresh call, which is simpler for the backend. Public clients require PKCE on refresh as well.

**Why no disconnect button in Settings UI?**
OAuth connectors follow a pattern where connected state shows a badge and Reconnect button only -- no separate Disconnect. Reconnecting replaces the stored token. See [Settings Data Connections](../../../docs/architecture/settings-data-connections.md).
