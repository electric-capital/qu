# Calendar API Documentation

This document describes how Quest accesses Google Calendar data for reads and writes.

## Overview

Calendar read operations use the `authed_get` tool to make authenticated GET requests directly to the Google Calendar API v3 at `https://www.googleapis.com/calendar/v3/...`. The `authed_get` handler in `chat/gemini_api/authed_get.py` matches the hostname and path, loads the user's Google Services OAuth credentials, and injects a Bearer token into each request. An `allowed_endpoints` validation mechanism restricts which API paths can be called, preventing access to arbitrary Calendar API endpoints. Calendar write operations go through the Action Requests system: `create_calendar_invite` creates a new event and `edit_calendar_event` updates an existing one.

## Authentication

Calendar reads require the user to have connected Google Services. The `authed_get` handler loads credentials via `get_valid_service_credentials()` in `auth/google_credentials.py` and injects a Bearer token. If the user has not connected Google Services, the handler returns an error.

**OAuth scopes required** (granted via the separate Google Services OAuth flow at `/auth/google-services`):
- `calendar.readonly` - Read access to Google Calendar data
- `calendar.events` - Write access for the `create_calendar_invite` and `edit_calendar_event` action request handlers

The Google Calendar entry in `_SERVICE_REGISTRY` sets `requires_user: True` (per-user OAuth credentials) and `retry_on_401: True` (automatic token refresh and retry on 401 responses).

## Calendar Read Access (via `authed_get`)

Calendar reads use `authed_get` with the Google Calendar API v3. The `_SERVICE_REGISTRY` entry in `chat/gemini_api/authed_get.py` defines allowed endpoint patterns via regex validation -- requests to non-matching paths are rejected. See that file for the allowed endpoint list and `api/calendar.py` (`get_instructions()`) for query parameter documentation provided to the LLM.

## Cross-Organization Calendar Visibility

Calendars of other users in the same Google Workspace domain are usually visible from the Google Calendar API even if they are not in the user's `calendarList`. Their events can be queried by using their email address as the `calendarId` (e.g., `https://www.googleapis.com/calendar/v3/calendars/colleague@example.com/events`). This behavior is documented in the system prompt via `get_instructions()` in `api/calendar.py` so the AI assistant is aware of it.

## Calendar Write Access

Quest does not expose a direct Calendar write endpoint. Both writes go through the Action Requests system and use the user's Google Services token to call the Google Calendar events API only after approval. The `calendar.events` scope is required for both. If a user connected Google Services before this scope was added, the Settings panel will show the connector as needing reauthorization.

- **Create** -- `create_calendar_invite` (`chat/action_request_types/create_calendar_invite.py`): `events.insert` with the proposed title, times, attendees, location, and description.
- **Edit** -- `edit_calendar_event` (`chat/action_request_types/edit_calendar_event.py`): `events.patch` on an existing event carrying only the supplied fields (`summary`, `start`/`end` as RFC3339 or all-day dates, `time_zone`, a complete replacement `attendees` list, `location`, `description`).
  - The model must read the event first and pass its `updated` timestamp as `expected_updated`; the handler fetches the live event at proposal time (rejecting same-turn on a mismatch, a missing or cancelled event, a no-op edit, or out-of-order merged start/end, and capturing a `current_event` snapshot for the before/after approval card) and again at Approve time so an event that changed while the card sat open is never overwritten.
  - Editing a recurring master id changes every occurrence; an expanded instance id (from `singleEvents=true`) changes only that occurrence, and the card states which applies.
  - Attendees are notified (`sendUpdates=all`) whenever the resulting event has any.

The parameter reference the model sees lives in `api/calendar.py` (`get_instructions()`), surfaced via the `system:calendar` skill.

## Design Decisions

**Why `authed_get` instead of proxy endpoints?**
Calendar reads are standard Google Calendar API GET requests. Using `authed_get` with per-user OAuth support eliminates the need for dedicated proxy endpoints in `quest.py`, reduces backend code, and follows the same pattern used for other external API services (e.g., CoinGecko). The `authed_get` handler already provides credential injection, hostname-based service matching, and 401 retry logic.

**Why only read access via `authed_get`?**
Calendar writes (event creation and editing) require user approval before execution, so they go through the Action Requests system which provides an approval UI. Reads are safe to execute without approval.
