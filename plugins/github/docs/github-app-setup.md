# GitHub App Setup

## Overview

Quest integrates with GitHub using a standard OAuth App flow. Each user connects individually via OAuth to obtain an access token for read-only API operations (repos, issues, pull requests, commits, search, file contents). GitHub OAuth App tokens do not expire, so no refresh logic is needed.

## Key Files

The integration is packaged as the in-tree `plugins/github` plugin (see [Plugins](../../../docs/architecture/plugins.md)):

- Settings > Service Credentials (admin) -- GitHub card (schema-declared by the plugin) managing `client_id` and `client_secret` in the per-service credential store (`github.json`); see [Service Credentials](../../../docs/architecture/service-credentials.md)
- `server_credentials.json` -- legacy `github` section, migrated into the store at startup and used as a runtime fallback by `load_github_client_config()` in `plugins/github/upstream.py`
- `server_credentials.example.json` -- Template showing the legacy file structure
- `plugins/github/upstream.py` -- `GITHUB_SCOPES` (`repo`, `read:org`), client-config loader, credential/connected/needs_reauth hooks
- `plugins/github/oauth.py` -- OAuth flow router (start, callback, disconnect), token storage
- `plugins/github/manifest.py` -- the plugin manifest incl. the `api.github.com` `authed_get` service entry (credential loader, Bearer auth injector, allowed-endpoints regex list, service `default_headers`)
- `plugins/github/instructions.md` -- `system:github` skill content
- `db/user_service_credential_store.py` -- token JSON in the github row's `oauth_blob`
- `chat/gemini_api/session.py` -- `invalidate_user_sessions()` called on connect to refresh system prompts
- `api/instructions.py` -- `get_user_connected_services()` checks connector status (plugin loop) for skill/tool gating
- `chat/routes/user.py` -- `get_connectors()` auto-appends the plugin's oauth row for the Data Connections UI

## Credential Configuration

A GitHub OAuth App must be created at GitHub Developer Settings > OAuth Apps. The app requires:
- A callback URL: `http://localhost:8000/auth/github/callback` (dev) or `https://your-domain.com/auth/github/callback` (production)
- An admin enters the resulting `client_id` and `client_secret` in Settings > Service Credentials (or, legacy, in the `github` section of `server_credentials.json`)

Users connect individually via the Data Connections section in Settings, which opens an OAuth popup flow. See [OAuth Popup Flow](../../../docs/architecture/oauth-popup.md).

## OAuth Scopes

Scopes are defined in `GITHUB_SCOPES` in `plugins/github/upstream.py`: `repo` (full repo access -- GitHub has no read-only repo scope) and `read:org` (read-only organization membership). The callback stores the scopes GitHub actually granted; when the granted set no longer covers `GITHUB_SCOPES`, the connector row shows the "Update Available" re-authorize badge (the plugin's `needs_reauth` hook).

Despite `repo` granting write permissions at the OAuth level, Quest exposes only read-only GitHub API paths through the `allowed_endpoints` regex list on the plugin's `api.github.com` service entry (`plugins/github/manifest.py`). No write operations are reachable.

## Token Architecture

- **Per-user tokens** stored in the generic `user_service_credentials` table (service `github`, token JSON in `oauth_blob`), managed via `db/user_service_credential_store.py`; the pre-plugin `users.github_oauth` column was migrated into rows by Alembic revision `a7c3e91b52d8`
- Fields stored: `access_token`, `token_type`, `scope` (raw comma-separated grant), `scopes` (granted list), `authorized_at`
- GitHub OAuth App tokens do not expire -- they remain valid until the user revokes them
- No shared bot token (unlike Slack) -- all API access is per-user

On connect, `invalidate_user_sessions()` from `chat/gemini_api/session.py` is called so the next conversation picks up the GitHub API documentation in system instructions.

## Connector Status

The connector is "connected" when the user's stored github row has an `oauth_blob.access_token` (the plugin's `connected` hook) AND the admin side is configured -- the standard combined plugin gate applied by `get_user_connected_services()` and the `/connectors` row. While the admin has not entered client credentials, the row is hidden entirely (`available: false`), mirroring Ramp.

## Design Decisions

**Why a standard OAuth App instead of a GitHub App?**
GitHub Apps offer more granular permissions but require installation at the organization level and use short-lived tokens needing periodic refresh. Standard OAuth Apps are simpler -- tokens do not expire, any user can authorize without org-level installation, and the flow matches the existing Slack OAuth pattern.

**Why no refresh logic?**
GitHub OAuth App tokens do not expire, eliminating refresh token management. The token remains valid until explicitly revoked.

**Why read-only endpoints only?**
Write operations carry higher risk and require more careful permission management. Read-only access provides immediate value for querying repository data while minimizing risk.

**Why `repo` scope?**
GitHub's OAuth scope model does not offer a read-only scope for repository access. `repo` is the only way to access private repository data.
