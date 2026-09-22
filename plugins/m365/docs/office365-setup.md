# Office 365 (Microsoft 365) App Setup

## Overview

Quest integrates with Microsoft 365 email (Outlook / Exchange Online) through Microsoft Graph, using the Microsoft identity platform (Entra ID) authorization-code flow. An admin registers one app in the organization's Entra ID tenant; each user then connects their own mailbox via OAuth from Settings > Data Connections. Unlike GitHub, Microsoft access tokens expire (~1 hour), so the flow requests `offline_access` and the plugin refreshes tokens automatically.

Works with any Microsoft 365 plan that includes Exchange Online (Business Basic and up).

## Creating the Entra ID App Registration

1. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com) (or the Azure portal) with an admin account of the Microsoft 365 tenant.
2. Go to **Entra ID > App registrations > New registration**.
3. Fill in:
   - **Name**: `Quest` (any name; users see it on the consent screen).
   - **Supported account types**: *Accounts in this organizational directory only* (single tenant) — the usual choice for a company deployment.
   - **Redirect URI**: platform **Web**, value `https://your-domain.com/auth/m365/callback` (production) or `http://localhost:8000/auth/m365/callback` (dev). Additional redirect URIs for other environments can be added later under **Authentication**.
4. Register, then from the app's **Overview** page copy:
   - **Application (client) ID**
   - **Directory (tenant) ID**
5. **Certificates & secrets > New client secret** — copy the secret **Value** immediately (it is shown only once). Note the expiry; Entra client secrets max out at 24 months and must be rotated before then.
6. **API permissions > Add a permission > Microsoft Graph > Delegated permissions**, add:
   - `User.Read` (usually present by default)
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `offline_access`
   These are all user-consentable by default; **Grant admin consent** on the same page is optional but skips the per-user consent prompt.

## Credential Configuration

An admin enters the three values in **Settings > Service Credentials > Microsoft 365** (the schema-declared plugin card, stored in `data/service_credentials/m365.json`):

- `tenant_id` — the Directory (tenant) ID
- `client_id` — the Application (client) ID
- `client_secret` — the client secret value

Until these are configured, the Microsoft 365 row is hidden from every user's Data Connections screen (`available: false`), mirroring Ramp/GitHub.

Users then connect individually via **Settings > Data Connections > Microsoft 365**, which opens the OAuth popup flow. See [OAuth Popup Flow](../../../docs/architecture/oauth-popup.md).

## OAuth Scopes

Requested scopes are defined in `M365_SCOPES` in `plugins/m365/upstream.py`: `User.Read` (mailbox identity), `Mail.ReadWrite` (read mail, create drafts, categorize/archive), and `Mail.Send` (send-to-self and future send flows), plus `offline_access` for the refresh token. The callback stores the scopes Microsoft actually granted; when the granted set no longer covers `M365_SCOPES`, the connector row shows the "Update Available" re-authorize badge (the plugin's `needs_reauth` hook).

Despite `Mail.ReadWrite`/`Mail.Send` granting write permission at the OAuth level, the raw Graph surface reachable via `authed_get` is read-only by construction — the `graph.microsoft.com` service entry allow-lists only GET paths and defines no POST endpoints. All writes (drafts, send-to-self, archive) go through the dedicated gated `m365_*` tools. See [Microsoft 365 Mail API](m365-mail-api.md).

## Token Architecture

- Per-user tokens live in the generic `user_service_credentials` table (service `m365`, token JSON in `oauth_blob`): `access_token`, `refresh_token`, `expires_at`, `scope`/`scopes` (granted), `account` (the connected mailbox's id/display name/email — which can differ from the user's Quest Google login), `authorized_at`.
- Access tokens expire after ~1 hour. `get_m365_token()` in `plugins/m365/upstream.py` proactively refreshes tokens within 5 minutes of expiry (per-user asyncio lock so concurrent tool calls don't race the rotation) and persists the rotated refresh token; the `authed_get` entry sets `retry_on_401` as a backstop.
- Refresh tokens age out (default 90 days of inactivity) or die when revoked; a failed refresh surfaces as the standard "connect Microsoft 365 in Settings > Data Connections" error and the user reconnects.

## Key Files

The integration is packaged as the in-tree `plugins/m365` plugin (see [Plugins](../../../docs/architecture/plugins.md)):

- `plugins/m365/upstream.py` — `M365_SCOPES`, client-config loader, token refresh (`get_m365_token`), connected/needs_reauth hooks, `graph_request` helper
- `plugins/m365/oauth.py` — OAuth flow router (`/auth/m365`, `/auth/m365/callback`, `/auth/m365/disconnect`)
- `plugins/m365/manifest.py` — the plugin manifest incl. the `graph.microsoft.com` `authed_get` service entry
- `plugins/m365/tools.py` — the seven `m365_*` mail tools
- `plugins/m365/render.py` — Graph-message markdown rendering (shares the markdownify/URL-replacement pipeline with `api/gmail/helpers.py`)
- `plugins/m365/instructions.md` — `system:m365` skill content

## Design Decisions

**Why delegated permissions instead of application permissions?**
Application permissions would give the server standing access to every mailbox in the tenant. Delegated per-user OAuth means Quest can only touch mailboxes whose owners explicitly connected, matching every other Quest connector.

**Why single-tenant?**
The deployment serves one organization; single-tenant registration keeps consent inside the tenant and lets the token endpoint be tenant-scoped. A multi-tenant deployment would set `tenant_id` to `organizations` and register the app as multi-tenant.

**Why `Mail.ReadWrite` and not `Mail.Read`?**
Draft creation, the archive move, and category tagging all require write access to the mailbox. Send is a separate scope (`Mail.Send`).
