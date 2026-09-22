# Iru (formerly Kandji) Integration

This document describes Quest's read-only Iru integration: the managed Apple (and, when enabled, Android / Windows) device inventory, per-device detail and library item status, blueprints, directory users, tags, Prism (Visibility) fleet reports, EDR threats, vulnerabilities, the tenant audit log, and Automated Device Enrollment devices. It is packaged as the in-tree `plugins/iru` plugin (plugin id `iru`; see [Plugin Architecture](../../../docs/architecture/plugins.md)).

Kandji rebranded to Iru in late 2025. The API is unchanged; both the new `<subdomain>.api.iru.com` / `<subdomain>.api.eu.iru.com` hosts and the legacy `<subdomain>.api.kandji.io` / `<subdomain>.api.eu.kandji.io` hosts serve it.

## Overview

Three things shape the plugin:

1. **Tenant-specific API host, admin-configured.** Every tenant has its own API origin, so the admin enters it once (Settings > Service Credentials > Iru). A static-hostname `services` (`authed_get`) entry cannot express a runtime-configured host, so -- like UniFi -- every read is a `tools` handler. Requests go to `https://<tenant>/api/v1/...` with `Authorization: Bearer <token>`.
2. **Per-user API token, the generic `api_key` connection kind.** Iru API tokens are tenant-level bearer tokens minted by an Iru admin (Iru web app: Settings > Access > API Token) with per-endpoint permissions. Each Quest user pastes their own token into Settings > Data Connections > Iru (the generic `POST /auth/service-key/iru` routes; shape-checked by `validate_api_token`, stored in the `user_service_credentials` row's `secret`). Access therefore follows what the Iru admin granted that token: a token missing an endpoint's permission answers `iru_auth_failed` with status 403 on that tool while the others keep working.
3. **Read side only, for now.** Every tool is a GET, so there are no action-request handlers or approval cards. Device actions (restart, lock, erase, blank push, rename, ...) and record edits are planned as approval-gated action requests. The device *secrets* GETs (`/secrets/filevaultkey`, `/secrets/bypasscode`, `/secrets/recoverypassword`, `/secrets/unlockpin`) are deliberately **not** wrapped: they would land in conversation transcripts.

## Key Files

| File | Description |
|------|-------------|
| `plugins/iru/manifest.py` | The plugin manifest: credential schema (`api_url`), the api_key-kind user connection (`validate_api_token`, key placeholder), the `system:iru` skill, the fifteen `iru_*` tools, and the script-bridge allowlist |
| `plugins/iru/upstream.py` | Admin config (`normalize_api_url`, `validate_iru_credentials`, `iru_is_configured`, `load_iru_config`), per-user token helpers (`validate_api_token`, `get_user_api_token`, `iru_connected`), the `IruClient` httpx wrapper (bearer header, `IruError` / `IruAuthError` mapping incl. 403-as-missing-permission and 429 with `retry_after`, `get_all_offset_pages` walker honoring `next` / `count`), and the payload helpers `page_items` / `cursor_from_next` |
| `plugins/iru/fleet.py` | Reference resolution (`resolve_device` by id / serial / name, `resolve_blueprint` by id / name, `resolve_user` by id / exact email -- exact match, else a sole partial match, else `IruLookupError` with `candidates`), the compact row views (`device_row`, `app_row`, `library_item_status_row`, `activity_row`, `blueprint_row`, `user_row`), truncation helpers, and `PRISM_CATEGORIES` |
| `plugins/iru/tools.py` | The tool handlers and specs (`IRU_TOOLS`, `SCRIPT_TOOL_ALLOWLIST`) |
| `plugins/iru/instructions.md` | The `system:iru` skill body (LLM-facing) |
| `plugins/iru/tests/` | Plugin test suite; `fake_tenant.py` is an `httpx.MockTransport` tenant serving the wrapped endpoints with Iru's real response shapes |

## Configuration

- Admin: Settings > Service Credentials > Iru (store file `data/service_credentials/iru.json`, written by the generic `PUT /admin/service-credentials/iru`). One field, `api_url`, normalized by `normalize_api_url` to an `https://` origin: bare hosts get the scheme, an `/api/v1` suffix is dropped, and the host must be a tenant subdomain on one of `API_HOST_SUFFIXES` (`.api.iru.com`, `.api.eu.iru.com`, `.api.kandji.io`, `.api.eu.kandji.io`) -- the web-app host `<sub>.kandji.io`, `http://`, custom ports, paths, queries, and embedded credentials are rejected. `is_configured` = URL present; the connector row is hidden until then. See [iru-setup.md](iru-setup.md).
- User: Settings > Data Connections > "+ Add Connection" > Iru (Kandji) -> paste the token. `validate_api_token` rejects whitespace and implausible lengths only; the token is verified live on the first tool call.
- The tenant URL is re-read from the store on every tool call.

## Tools

All tools are advertised only while `connected_services["iru"]` is true (tenant URL configured AND a token stored). Each answers a JSON object; failures are `{"error": <code>, "message", "status_code"?, ...}` with codes `iru_not_configured`, `iru_not_connected`, `iru_auth_failed` (401 rejected token / 403 missing permission), `iru_rate_limited` (+ `retry_after_seconds`), `iru_unreachable`, `iru_timeout`, `iru_not_found`, `iru_request_failed`, `iru_unknown_device` / `_blueprint` / `_user`, `iru_ambiguous_device` / `_blueprint` / `_user` (+ `candidates`), and `invalid_arguments`.

Upstream endpoints per tool (all under `/api/v1`):

- `iru_get_tenant_info()` -- `GET /settings/licensing` (a 403 falls back to `GET /devices?limit=1` to prove the token) + `GET /blueprints?limit=1` for the blueprint count.
- `iru_list_devices(serial_number?, device_name?, platform?, blueprint?, user_email?, user_name?, user_id?, asset_tag?, model?, os_version?, mac_address?, tag_name?, filevault_enabled?, ordering?, limit?, offset?)` -- `GET /devices` (bare list, hard cap 300/page); `blueprint` accepts a name and is resolved to `blueprint_id` first; rows via `device_row`; `next_offset` when the page was full.
- `iru_get_device(device, sections?, app_search?, activity_limit?)` -- `resolve_device` (`GET /devices/{id}` for a UUID, else `GET /devices?serial_number=` then `?device_name=`), then per section: `details` (`/details`), `status` (`/status`, library items + parameters compacted), `library_items` (`/library-items` + per-status counts), `apps` (`/apps`, `app_row` rows sorted by name, optional substring filter, capped at `MAX_APP_ROWS`), `activity` (`/activity?limit=`), `commands` (`/commands?limit=`), `parameters` (`/parameters`), `lost_mode` (`/details/lostmode`). A 403/404 on one section is reported inline under that section instead of failing the call.
- `iru_list_blueprints(name?, limit?, offset?)` -- `GET /blueprints`; `iru_get_blueprint(blueprint)` -- `resolve_blueprint` + `GET /blueprints/{id}/list-library-items`.
- `iru_get_library_item_status(library_item_id, device?, status?, limit?, offset?)` -- `GET /library/library-items/{id}/status` (`computer_id` from `resolve_device`); the endpoint has no status filter, so `status` collects every page (capped at `MAX_COLLECTED_ITEMS`) and filters client-side.
- `iru_list_users(email?, archived?, cursor?)` -- `GET /users` (cursor-paged; `next_cursor` from the `next` URL); `iru_get_user(user)` -- `resolve_user` + `GET /devices?user_id=` for their devices.
- `iru_list_tags(search?)` -- `GET /tags?search=` (`search` is required upstream; sent empty when omitted).
- `iru_prism_query(category, filter?, fields?, blueprint_ids?, device_families?, sort_by?, limit?, offset?)` -- `GET /prism/{category}` (`{cursor, data}` shape) with the JSON `filter` serialized verbatim (object or JSON string accepted; grammar in `instructions.md`), plus `GET /prism/count?category=` on an unfiltered first page only (`total_unfiltered`; a 403/404 there is tolerated). `fields` projects columns client-side and echoes `fields_available`.
- `iru_list_threats(classification?, status?, device_id?, date_range?, term?, sort_by?, limit?, offset?)` -- `GET /threat-details` (cap 1000/page; `malware_count` / `pup_count` passed through).
- `iru_list_vulnerabilities(filter?, sort_by?, page?, size?)` -- `GET /vulnerability-management/vulnerabilities` (page/size, cap 50; `next_page` from `total`); `iru_get_vulnerability(cve_id, page?, size?)` -- `GET .../vulnerabilities/{cve}` + `/devices` + `/software`.
- `iru_list_audit_events(start_date?, end_date?, sort_by?, limit?, cursor?)` -- `GET /audit/events` (cap 500; default sort `-occurred_at`; `old_state` / `new_state` / `metadata` truncated by `compact_json` when oversized).
- `iru_list_ade_devices(serial_number?, device_family?, os?, model?, profile_status?, blueprint_id?, user_id?, ade_token_id?, page?)` -- `GET /integrations/apple/ade/devices` (page-numbered, 300/page).

All fifteen tools are in the script bridge (`SCRIPT_TOOL_ALLOWLIST`), so sandbox scripts can page the fleet into a CSV via `POST /api/tool-call`.

## Design Decisions

**Why per-user tokens instead of one admin token?** Iru tokens are tenant-level, so a single admin token would give every Quest user the union of its permissions with no per-user audit trail on the Iru side. Per-user tokens keep Iru's own permission scoping and its audit log (which records token access to sensitive data) meaningful. The admin card only carries the tenant URL.

**Why compact row views?** The devices list returns ~25 fields per row and Prism rows are 40+ columns wide; a 300-row page of raw records is tens of thousands of tokens. `device_row` trims to the identifying / status fields and `iru_prism_query` takes `fields`, with the raw record still available through `iru_get_device`.

**Why no secrets tools?** FileVault recovery keys and bypass codes are exactly the data Iru audits access to. Exposing them through a tool would copy them into `chat_history.json` and any sub-agent / script output. If a future need arises it should be a dedicated, separately gated surface, not a section of `iru_get_device`.

**Rate limit.** 10,000 requests/hour and 50/second per tenant. The tools never fan out per device (fleet-wide questions go to Prism); a 429 surfaces as `iru_rate_limited` with the `Retry-After` value rather than being retried server-side.

## Not Yet Covered

- Write side: device actions (`POST /devices/{id}/action/*`), device / blueprint / tag / ADE-device edits, library item assignment -- planned as approval-gated action requests.
- Device secrets GETs (deliberate, see above), blueprint templates, per-library-item activity, behavioral (EDR) detections, the ADE integrations / token list, Prism CSV exports, the manual enrollment profile download.
