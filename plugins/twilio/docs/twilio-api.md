# Twilio SMS Integration

This document describes Quest's Twilio SMS integration: texting the user's own verified phone without approval (policy-governed) and texting anyone else through an approval-gated action request. It is packaged as the in-tree `plugins/twilio` plugin (plugin id `twilio`; see [Plugin Architecture](../../../docs/architecture/plugins.md)).

## Overview

Two differences from the other in-tree plugins:

1. **No OAuth provider.** The per-user connection is a *verified phone number*: the user types their number in the Data Connections popup, Quest texts a 6-digit code to it via Twilio, and the user types the code back. The manifest still declares the `oauth` connection kind because that is the shape that mounts a plugin-owned, session-cookie-authed router under `/auth/twilio` and renders the generic Connect-popup row; the popup page is the plugin's own verification form.
2. **Trusted-channel policy.** The admin Service Credentials card carries a **Trusted channel** checkbox (`trusted_channel`, a `bool` `CredentialField`). It only governs the no-approval `twilio_send_self_sms` tool: trusted → free-form text to the user's own number; not trusted (default) → only one of the user's pre-written messages (Settings > SMS Messages), sent verbatim. Texts to anyone else always go through the `twilio_send_sms` action request and its approval card regardless of the switch.

## Key Files

| File | Description |
|------|-------------|
| `plugins/twilio/manifest.py` | The plugin manifest: credential schema (Account SID, Auth Token, sending number / Messaging Service SID, `trusted_channel` bool), the oauth-kind user connection wired to the verification router, the `system:twilio` skill, tools, and the `twilio_send_sms` handler |
| `plugins/twilio/upstream.py` | Admin config loader (`load_twilio_config`, `is_trusted_channel`, `validate_twilio_credentials`), E.164 normalization/masking, the per-user verified-number helpers (`get_user_phone_number`, `twilio_connected`), and `send_sms()` against the Messages API |
| `plugins/twilio/verify.py` | The `/auth/twilio` router: popup page, `start`, `verify`, `disconnect`, and the `templates` GET/PUT |
| `plugins/twilio/templates.py` | Pre-written message validation/lookup; `TEMPLATES_SETTINGS_KEY` in `users.settings` |
| `plugins/twilio/tools.py` | `twilio_list_sms_templates` and `twilio_send_self_sms` (the trusted-channel policy lives here) |
| `plugins/twilio/handlers.py` | `TwilioSendSmsHandler` (`twilio_send_sms` action request) |
| `plugins/twilio/instructions.md` | The `system:twilio` skill body (LLM-facing) |
| `frontend/src/components/settings/SmsMessagesSection.tsx` | Settings > SMS Messages editor (core FE; listed only while the `twilio` connector row is available) |

## Configuration

- Admin: Settings > Service Credentials > Twilio SMS (store file `data/service_credentials/twilio.json`, written by the generic `PUT /admin/service-credentials/twilio`). Fields: `account_sid` (`AC` + 32 hex), `auth_token` (secret), `from_number` (E.164 number or `MG...` Messaging Service SID), `trusted_channel` (bool). `is_configured` requires the first three; the connector row is hidden until then. See [twilio-setup.md](twilio-setup.md).
- The trusted-channel switch is re-read from the store on every tool call, so flipping it takes effect immediately.

## Verification Flow (`/auth/twilio`)

Routes are the plugin's router (`plugins/twilio/verify.py`), mounted by `mount_plugin_oauth_routers()`; every route is session-cookie authed (no API-key auth, so the LLM's `curl_proxy_*` tools cannot reach them -- `validate_url` also confines those to `/api/*`).

1. Data Connections "Connect" opens `GET /auth/twilio?popup=1` -- a self-contained HTML page (phone step, then code step).
2. `POST /auth/twilio/start` `{"phone_number"}` -- normalizes to E.164 (country code required, never guessed), enforces a 60 s resend cooldown, texts `Your Quest verification code is NNNNNN`, and stores `{phone_number, code_hash, salt, sent_at, expires_at, attempts}` under `oauth_blob.pending` in the user's `user_service_credentials` row (`upsert_credential`). An existing verified number stays until the new one is confirmed.
3. `POST /auth/twilio/verify` `{"code"}` -- 10-minute expiry, 5 wrong guesses void the code; on success the blob becomes `{phone_number, verified_at}` and `invalidate_user_sessions()` refreshes the system prompt. The page posts `oauth_callback_success` to the opener (the same message the OAuth popups send) and closes.
4. `POST /auth/twilio/disconnect` -- deletes the row.

`twilio_connected` only looks at `phone_number`, so a pending verification never counts as connected. The code hash lives server-side (a client-readable cookie would allow offline brute force of a 6-digit code) and in the DB rather than process memory (restart-safe, worker-agnostic).

## Pre-written Messages (Settings > SMS Messages)

- Storage: `users.settings.twilio_sms_templates` -- `[{"name", "body"}]`, validated by `validate_templates()` (≤50, names ≤60 chars unique case-insensitively, bodies ≤1600 chars, no control characters). Kept in settings rather than the credential row so a disconnect/reconnect does not lose them.
- API: `GET /auth/twilio/templates` → `{templates, trusted_channel, phone_number, max_templates}`; `PUT /auth/twilio/templates` `{"templates": [...]}` (whole-list replacement). Plugin-owned routes, so the core `PUT /settings` model does not know the key.
- FE: `SmsMessagesSection.tsx` (`fetchSmsTemplates` / `updateSmsTemplates` in `api/client.ts`); `SettingsModal.tsx` lists the "SMS Messages" nav item only when `GET /connectors` reports the `twilio` row with `available !== false`.

## Tools

Both tools are advertised only while `connected_services["twilio"]` is true (server configured AND number verified) and are NOT in the script-tool allowlist -- sandbox code cannot text the user.

- `twilio_list_sms_templates()` → `{connected, phone_number_masked, trusted_channel, free_form_allowed, templates, note}`.
- `twilio_send_self_sms(body? | template?)` -- exactly one of the two. Recipient is always the user's verified number. Policy (`_tool_send_self_sms`): `template` → looked up case-insensitively and sent verbatim in either mode; `body` → sent only when `is_trusted_channel()`; otherwise `{"error": "untrusted_channel", "available_templates": [...]}`. Other errors: `twilio_not_connected`, `twilio_not_configured`, `unknown_template`, `invalid_arguments`, `body_too_long`, `sms_send_failed`. The result masks the number to its last 4 digits.

## Sending to Others (Action Request)

`twilio_send_sms` (`TwilioSendSmsHandler`): params `to` (E.164, normalized; a number without a country code is rejected same-turn), `body` (≤1600 chars), optional `recipient_name` for the card. Preview shows To + Message; approve label "Send", resolved label "Sent". `execute()` sends from the admin-configured sender via `send_sms()`; Twilio failures become `RuntimeError` with Twilio's message. There is no upstream dry-run.

## Twilio API Surface

Only `POST /2010-04-01/Accounts/{AccountSid}/Messages.json` (basic auth with the Account SID + Auth Token; `From` for a phone-number sender, `MessagingServiceSid` for an `MG...` sender). No `authed_get` service entry -- the plugin reads nothing from Twilio.

## Design Decisions

**Why an `oauth`-kind connection for a non-OAuth flow?** It is the only manifest shape that mounts plugin routes (`/auth/<id>`) and gives a popup-based connector row with no core FE changes; adding a third kind would have meant a core FE/connector change for one plugin.

**Why self-generated codes over Twilio Verify?** One fewer thing to configure (no Verify Service SID) and the same sender covers verification and messages. The trade-off is that the plugin owns cooldown/expiry/attempt limits (`verify.py` constants).

**Why templates in `users.settings` via plugin routes?** Templates must survive a phone disconnect (the credential row is deleted) and the core settings endpoint should not learn plugin keys. The FE section is the one deliberate core-FE surface, mirroring Settings > Gmail's label list.

**Why is the trusted switch admin-only and global?** It is a statement about the channel (who can read texts on the way to the user), not a user preference; an untrusted channel must not be flippable by the model or the user from a conversation.

## Tests

`plugins/twilio/tests/`: upstream helpers, template validation, the verification router (mocked store + sender), the tool policy matrix, handler validation/execute, and registry wiring via the `twilio_plugin` fixture.
