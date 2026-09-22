# Telegram Setup

## Overview

Quest integrates with Telegram through the in-tree `plugins/telegram` plugin, using Telethon's `StringSession` for session persistence. An admin registers a Telegram application (the MTProto `api_id` / `api_hash`) once; each user then signs in with their own Telegram account from Settings > Data Connections (phone number, the login code Telegram sends to their app, and the cloud password if two-step verification is on). See [telegram-api.md](telegram-api.md) for what the agent can do once connected.

## Key Files

- `data/service_credentials/telegram.json` -- `api_id` and `api_hash`, managed via the admin Settings > Service Credentials card (schema in `plugins/telegram/manifest.py`)
- `plugins/telegram/upstream.py` -- credential loader with the legacy `server_config.json` `telegram_app_info` fallback, the `post_load` migration of that section, and the Telethon client manager
- `plugins/telegram/auth.py` -- the `/auth/telegram` login router and popup page
- `db/user_service_credential_store.py` -- the `user_service_credentials` row (service `telegram`) holding the session

## Admin: Telegram Application Credentials

1. Sign in at https://my.telegram.org with a Telegram account and open **API development tools**.
2. Create an application (any title / short name; platform "Other" is fine). Telegram shows an **App api_id** (a number) and an **App api_hash** (a hex string).
3. In Quest, open **Settings > Service Credentials > Telegram** and paste both values. The API ID must be digits only; the API hash is stored as a secret (leaving it blank on a later save keeps the stored one).

The Telegram row in every user's Data Connections list stays hidden until both values are configured.

**Legacy configuration.** Older deployments kept the same two values in the `telegram_app_info` section of `server_config.json`. On startup the plugin's `post_load` hook copies that section into the per-service store (the store wins if it already has a file; the legacy file is never modified). See [Service Credentials](../../../docs/architecture/service-credentials.md).

Unlike the OAuth-based services (Slack, GitHub, Twitter/X), these are Telegram *application* credentials shared by every user of the deployment; each user's own account is authorized separately below.

## Users: Connecting a Telegram Account

1. Open **Settings > Data Connections**, click **+ Add Connection**, and pick **Telegram**. A popup opens the login page served by `GET /auth/telegram?popup=1`.
2. Enter the phone number of the Telegram account in international format (for example `+14155551234`). Quest asks Telegram to send a login code; Telegram delivers it inside the Telegram app (or by SMS for accounts without an active session).
3. Enter the code. Accounts with two-step verification are asked for their Telegram **cloud password** next (not the device PIN).
4. The popup closes and the row shows as connected. The Telegram skill and the `telegram_*` tools appear in the agent's next conversation turn.

Signing in again replaces the stored session. **Disconnect** on the row logs the session out at Telegram (best effort) and forgets it.

A login code is valid for 10 minutes and five wrong codes or passwords void the attempt; start again from the phone step. Telegram itself may impose a wait after repeated code requests (shown as "Telegram asks you to wait N seconds").

## Data Storage

- **Session**: Telethon `StringSession` in the user's `user_service_credentials` row (service `telegram`, `oauth_blob.session`, plus `phone` and `connected_at`), encrypted at rest. Older deployments' `users.telegram_session` values were moved here by migration `d7a1f3c9e2b4`.
- **In-flight login**: the phone, `phone_code_hash`, pre-auth session string, stage, expiry, and attempt counter under `oauth_blob.pending` in the same row -- server-side, encrypted, removed on completion.
- **Application credentials**: `data/service_credentials/telegram.json` (encrypted store file).

## Troubleshooting

- **The Telegram row is missing from Data Connections** -- the admin application credentials are not configured (or `api_id` is not a number); check Settings > Service Credentials > Telegram.
- **"Telegram not configured" on the login page** -- same cause, seen when the page is opened directly.
- **"Telegram rejected the phone number"** -- the number must include the country code; banned or unregistered numbers are refused by Telegram.
- **"Telegram asks you to wait N seconds"** -- Telegram's flood control after repeated code requests; wait and retry.
- **`telegram_session_expired` from the tools** -- the session was revoked (for example from Telegram's *Devices* settings); reconnect from Data Connections.

## Design Decisions

**Why StringSession?**
Telethon's StringSession provides portable session storage as a simple string. Storing it in the encrypted credential row avoids managing separate SQLite session files per user.

**Why Google auth first?**
The Telegram login attaches to an existing Quest user; the app login establishes that identity before the popup can store anything.

**Why a server-side pending login instead of cookies?**
The pre-auth Telethon session string is a credential. Keeping the in-flight login in the encrypted credential row keeps it off the client, survives restarts, and lets the server enforce expiry and an attempt cap.
