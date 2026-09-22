# Slides API Documentation

This document describes how Quest accesses Google Slides content for reading presentations.

## Overview

Slides read operations use the `authed_get` tool to make authenticated GET requests directly to the Google Slides API v1 at `https://slides.googleapis.com/v1/...`. The `authed_get` handler in `chat/gemini_api/authed_get.py` matches the hostname, loads the user's Google Services OAuth credentials, and injects a Bearer token into each request. An `allowed_endpoints` validation mechanism restricts which API paths can be called, preventing access to arbitrary Slides API endpoints. Listing Google Slides presentations uses the Google Drive API via `authed_get` with a `mimeType='application/vnd.google-apps.presentation'` filter (Drive is already registered in `_SERVICE_REGISTRY`). Access is read-only.

## Authentication

Slides reads require the user to have connected Google Services. The `authed_get` handler loads credentials via `get_valid_service_credentials()` in `auth/google_credentials.py` and injects a Bearer token. If the user has not connected Google Services, the handler returns an error.

**OAuth scope required** (granted via the separate Google Services OAuth flow at `/auth/google-services`):
- `presentations.readonly` - Read access to Google Slides content

The scope is added to `GOOGLE_SERVICE_SCOPES` in `auth/config.py`. Because it is a new scope, **existing users must re-consent** to the Google Services OAuth grant before Slides requests will succeed; until re-consent, requests fail at the credential/scope check.

The Google Slides entry in `_SERVICE_REGISTRY` sets `requires_user: True` (per-user OAuth credentials) and `retry_on_401: True` (automatic token refresh and retry on 401 responses), sharing the `_load_google_services_credentials` loader and `_inject_google_bearer_auth` injector with the other Google services.

## Presentation Read Access (via `authed_get`)

Slides reads use `authed_get` with the Google Slides API v1 at `slides.googleapis.com`. The `_SERVICE_REGISTRY` entry in `chat/gemini_api/authed_get.py` defines two read-only allowed endpoint patterns via regex validation -- requests to non-matching paths are rejected: fetching a presentation by ID, and fetching a single page (slide/master/layout) by `pageObjectId`. The regexes use `[^/:]+` for the id segments to exclude the `:` character, which keeps write verbs such as `:batchUpdate` out of the allow-list. See that file for the exact patterns and `api/slides.py` (`get_instructions()`) for query parameter documentation provided to the LLM.

## Listing Google Slides (via Google Drive API)

The Google Slides API does not provide a list endpoint. Listing and searching for Google Slides presentations uses the Google Drive API with a mimeType filter (`mimeType='application/vnd.google-apps.presentation'`), also via `authed_get`. The Drive API base URL is `https://www.googleapis.com/drive/v3`. See `api/slides.py` (`get_instructions()`) for query parameter details.

## System Skill

Slides usage instructions are surfaced through the gated `system:slides` skill (`requires="google_services"`), registered in `chat/system_skills/catalog.py` with its content built from `api/slides.py` (`get_instructions()`). The skill is loadable only when the user has connected Google Services. See [Skill Library](../architecture/skill-library.md).

## Response Size

Full presentation payloads are large and commonly exceed the `authed_get` response size gate. The skill instructions direct the model to trim payloads with the Slides API `fields` parameter (e.g. `fields=title,slides.objectId`), and for very large decks to pass `output_file` so the response body is written under the hidden `.responses/` workspace subdirectory (and read back via the receipt's `.responses/...` path) and bypasses the size gate. See [Large Response Protection](../architecture/gemini-api.md#large-response-protection).

## Design Decisions

**Why `authed_get` instead of proxy endpoints?**
Slides reads are standard Google Slides API GET requests. Using `authed_get` with per-user OAuth support eliminates the need for dedicated proxy endpoints in `quest.py`, reduces backend code, and follows the same pattern used for Google Calendar, Google Drive, Google Docs, Google Sheets, and other external API services. The `authed_get` handler already provides credential injection, hostname-based service matching, and 401 retry logic.

**Why read-only?**
The allowed-endpoint regexes match only GET-shaped paths and exclude the `:` segment used by the Slides API for write verbs (`:batchUpdate`), so the registry entry cannot reach any mutation endpoint. This matches the read-only posture of the Docs and Sheets integrations.

**Why use the Drive API for listing presentations?**
The Google Slides API does not provide a list endpoint. The Drive API is used with a mimeType filter to return only Google Slides presentations. This follows Google's recommended pattern for discovering presentations.

**Why a separate hostname (`slides.googleapis.com`) instead of path-prefix on `www.googleapis.com`?**
Unlike Google Calendar and Drive (which live at `www.googleapis.com/calendar/v3` and `www.googleapis.com/drive/v3`), the Google Slides API uses its own hostname (`slides.googleapis.com`). The `_SERVICE_REGISTRY` entry uses plain hostname matching without a `path_prefix` field.
