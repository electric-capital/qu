# SEC EDGAR API Documentation

This document describes how Quest reads US Securities and Exchange Commission EDGAR data (company submission/filing history and XBRL financial facts) via the `authed_get` tool.

## Overview

SEC EDGAR reads use the `authed_get` tool to make GET requests directly to the public SEC EDGAR data API at `https://data.sec.gov/...`, plus the two static ticker→CIK map files on `https://www.sec.gov/files/...`. The SEC EDGAR data API is free, public, and requires **no authentication**, so like the Federal Register integration it has no credentials, no connection step, and no Settings entry.

The `authed_get` handler in `chat/gemini_api/authed_get.py` matches the hostname, runs the request path through an `allowed_endpoints` regex allow-list, and injects no credentials (a no-op auth injector). SEC's fair-access policy does require a descriptive `User-Agent` on every request (it returns HTTP 403 without one), so each registry entry supplies one via `default_headers`.

The backend documentation is carried in the ungated `system:sec_edgar` system skill rather than always-on prompt text. The integration is modeled closely on the [Federal Register API](federal-register-api.md).

## Key Files

| File | Description |
|------|-------------|
| `api/sec_edgar.py` | `get_instructions(base_url)` for the `system:sec_edgar` skill content (base URL constant `SEC_EDGAR_API_BASE`, CIK 10-digit zero-padding rule, ticker→CIK resolution workflow, key-paths table, parallel-arrays note, size-gate / `output_file` guidance, rate limit, example tool calls); no endpoint functions -- reads use `authed_get` |
| `chat/gemini_api/authed_get.py` | `handle_authed_get()` handler with the `data.sec.gov` and `www.sec.gov` entries in `_SERVICE_REGISTRY` (plain hostname keys, `allowed_endpoints` regexes, `default_headers`); `_load_sec_edgar_credentials()` (sync, no-arg, returns `None`) and the reused `_inject_no_auth()` no-op injector model the no-auth service |
| `chat/system_skills/catalog.py` | Registers `system:sec_edgar` as an ungated (`requires=None`) `SystemSkill`, placed after `system:federal_register`; delegates `content_builder` to `api.sec_edgar.get_instructions` |
| `chat/llm/tool_schemas.py` | The `authed_get` tool description lists "SEC EDGAR" among supported hosts and `system:sec_edgar` in the `system:*` skill id list (endpoints not inlined -- the skill carries the detail) |
| `chat/gemini_api/system_prompt.py` | "SEC EDGAR" added to the prose lines enumerating `authed_get` backends |

## No Authentication

The SEC EDGAR data API needs no credentials, so both hosts are modelled as no-auth entries in `_SERVICE_REGISTRY`, sharing the same loader and injector:

- **No-arg `None` loader:** `_load_sec_edgar_credentials()` in `chat/gemini_api/authed_get.py` is a synchronous, no-argument function that returns `None` (matching the call shape of the CoinGecko key loader rather than the per-user `async (user)` loaders).
- **No-op injector:** the shared `_inject_no_auth()` is used as `inject_auth`. `_make_authed_request()` always invokes `inject_auth` even when the loaded credential is `None`, so the no-op injector makes that call harmless rather than special-casing the code path.
- **`requires_user` deliberately unset:** neither registry entry sets `requires_user`. The missing-credentials guard in `_make_authed_request()` only fires when `credentials is None and requires_user`, so leaving `requires_user` unset lets the `None` credential flow past the guard instead of being treated as a missing-credentials error.
- **No `retry_on_401`:** there is no token to refresh, so the 401-retry branch is not enabled.
- **`default_headers` -- SEC `User-Agent` requirement:** SEC's fair-access policy returns HTTP 403 to any request lacking a descriptive `User-Agent` (name + contact). Both entries set `default_headers` with `User-Agent: Quest/1.0 (admin@quest.example)` so the header is injected on every request below caller-supplied headers in precedence -- the same mechanism GitHub and Federal Register use. This is **not** a custom injector; it rides on the existing `default_headers` merge. The model does not (and should not) set the header itself.

See [Authenticated External API Requests](../architecture/gemini-api.md#authenticated-external-api-requests-authed_get) for the service registry schema, the credential-loading flow, and the `default_headers` merge precedence that the no-auth path reuses.

## SEC EDGAR Read Access (via `authed_get`)

SEC EDGAR reads use `authed_get` with the full API URL. Two `_SERVICE_REGISTRY` entries in `chat/gemini_api/authed_get.py` gate access via `allowed_endpoints` regex validation -- requests to non-matching paths are rejected. See that file for the exact pattern list and `api/sec_edgar.py` (`get_instructions()`) for the LLM-facing documentation (base URL, key paths, the parallel-arrays layout of `filings.recent`, and example invocations).

### Hosts and Path Gating

Both entries use a **plain hostname key with no `path_prefix`**. Unlike Federal Register (whose entire JSON surface lives under `/api/v1`), `data.sec.gov` serves multiple distinct path roots (`/submissions/`, `/api/xbrl/...`), so path scoping is done entirely by the `allowed_endpoints` regexes rather than a prefix.

**`data.sec.gov`** (submission + XBRL data) allow-lists, with CIK as a 10-digit zero-padded value (`CIK0000320193`):

- `/submissions/CIK##########.json` -- primary entity filing history.
- `/submissions/CIK##########-submissions-NNN.json` -- older-filings spillover for large filers.
- `/api/xbrl/companyconcept/CIK##########/<taxonomy>/<tag>.json` -- one concept's time series for one entity.
- `/api/xbrl/companyfacts/CIK##########.json` -- every XBRL fact for one entity (large).
- `/api/xbrl/frames/<taxonomy>/<tag>/<unit>/CY####[Q#][I].json` -- one concept+unit across all entities for a period (`I` = instantaneous/point-in-time).

**`www.sec.gov`** is restricted to **exactly the two static ticker→CIK map files** and nothing else:

- `/files/company_tickers.json`
- `/files/company_tickers_exchange.json`

The rest of `www.sec.gov` (the human-facing site, `/cgi-bin/browse-edgar`, other `/files/...`) is **not proxied** -- the `allowed_endpoints` regexes match only those two paths. The `efts.sec.gov` full-text search host is **not** integrated at all (see Design Decisions).

### Ticker → CIK Resolution Workflow

Every `data.sec.gov` path requires a CIK (Central Index Key), not a ticker. To let the agent resolve a stock ticker to a CIK without leaving `authed_get`, the two SEC ticker→CIK map files on `www.sec.gov` are narrowly allow-listed.

The map files are large (thousands of entries), so the skill instructions direct the model to fetch them with `output_file` (written under the hidden `.responses/` workspace subdirectory), then read/search the file via the receipt's `.responses/...` path and **zero-pad** the resulting integer `cik_str` to 10 digits to build the `data.sec.gov` URL.

See `api/sec_edgar.py` (`get_instructions()`) for the file shapes and the workflow detail. If the CIK is already known, the map fetch is skipped.

### CIK Zero-Padding

The `allowed_endpoints` regexes for `data.sec.gov` require the CIK to be exactly 10 digits (`CIK\d{10}`), so a non-padded value like `CIK320193` is rejected at the allow-list. The `cik_str` field in the map files is an unpadded integer; the model must left-pad it with zeros to 10 digits itself.

### Large Responses

The `companyfacts` payload and busy-filer `submissions` payloads vastly exceed the ~3 KB `authed_get` size gate and return `response_too_large` if fetched inline. EDGAR endpoints do **not** support a `fields[]`-style trimmer, so `output_file` (which writes the body under the hidden `.responses/` workspace subdirectory and bypasses the size gate) is the primary tool for large payloads; the skill also directs the model to prefer `companyconcept` (one metric) over `companyfacts` (everything) when only one value is needed. See [Large Response Protection](../architecture/gemini-api.md#large-response-protection).

## System Instructions Integration

SEC EDGAR documentation is exposed through the `system:sec_edgar` system skill rather than being inlined in every system prompt:

- `api/sec_edgar.py` (`get_instructions()`) builds the skill content.
- `chat/system_skills/catalog.py` registers `system:sec_edgar` with `requires=None` (ungated). Because it has no `requires` gate and no `requires_project`, it is always visible and loadable for every user regardless of connection state -- there is no `connected_services` key to check.
- The model loads it on demand via `load_skills(skill_ids=["system:sec_edgar"])`; it is enumerated in the always-on system-skill bullet list. See [Skill Library Architecture -- System Skills](../architecture/skill-library.md#system-skills).
- The `authed_get` tool schema in `chat/llm/tool_schemas.py` lists "SEC EDGAR" among supported hosts and `system:sec_edgar` among the `system:*` skill ids, and the `authed_get` backend prose in `chat/gemini_api/system_prompt.py` names SEC EDGAR.

## Constraints

- **Read-only:** only the GET paths on the `allowed_endpoints` lists are reachable; the allow-lists are GET-only and there are no SEC EDGAR write operations.
- **Path-gated by regex:** only the allow-listed `data.sec.gov` paths and the two `www.sec.gov` map files are proxied; all other paths on both hosts are rejected. `efts.sec.gov` is not registered.
- **No authentication:** no credentials, connection, or Settings entry; the skill is always available.
- **`User-Agent` required:** SEC returns HTTP 403 without a descriptive `User-Agent`; the server injects one via `default_headers` on both entries.
- **CIK format:** the CIK must be 10-digit zero-padded in `data.sec.gov` paths (`CIK0000320193`, not `CIK320193`).
- **Size gate:** responses over the ~3 KB `authed_get` size gate (notably `companyfacts` and busy-filer `submissions`) must be written to the hidden `.responses/` workspace subdirectory with `output_file`; EDGAR has no `fields[]`-style trimmer.
- **Rate limit:** SEC's fair-access policy caps usage at 10 requests/second. This is documented in the skill but **not enforced** in code.

## Design Decisions

**Why `authed_get` instead of proxy endpoints?**
SEC EDGAR reads are standard public API GET requests. Using `authed_get` avoids dedicated proxy endpoints, reuses the shared `allowed_endpoints` path gating and large-response protection, and aligns SEC EDGAR with the same pattern used by Google services, Airtable, GitHub, and Federal Register.

**Why a no-auth service instead of skipping credential handling?**
`_make_authed_request()` always loads credentials and invokes `inject_auth`. Rather than branch the shared code path for an unauthenticated host, the service supplies a `None`-returning loader and the reused no-op injector and leaves `requires_user` unset, so the generic flow runs unchanged and the `None` credential never trips the missing-credentials guard.

**Why a plain hostname key (no `path_prefix`)?**
Unlike Federal Register's single `/api/v1` JSON surface, `data.sec.gov` serves multiple distinct path roots (`/submissions/`, `/api/xbrl/...`). A single prefix would not cover them, so the entry uses a bare hostname key and relies entirely on the `allowed_endpoints` regexes for path gating.

**Why allow-list `www.sec.gov` to only two files?**
The submission/XBRL calls require a 10-digit CIK, but the only public ticker→CIK mapping lives in two static JSON files on `www.sec.gov`. Narrowly allow-listing exactly those two files lets the agent resolve a ticker to a CIK itself, while keeping the rest of `www.sec.gov` (the human site and other paths) unreachable.

**Why inject `User-Agent` via `default_headers` instead of a custom injector?**
SEC's `User-Agent` requirement is a static header, not a per-request credential. The existing `default_headers` merge already injects static headers below caller headers in precedence (as GitHub and Federal Register do), so reusing it avoids adding a bespoke injector.

**Why an ungated (`requires=None`) system skill?**
The API needs no connection or credentials, so there is no connection state to gate on. Making the skill ungated keeps it visible and loadable for every user, while still keeping the backend detail out of the always-on prompt (the model loads it only when working with SEC filings).

**Why is `efts.sec.gov` (full-text search) not integrated?**
The EDGAR full-text search host was out of scope for this integration, which covers structured submission and XBRL data. It is a possible future extension: it would be a third no-auth `_SERVICE_REGISTRY` entry following the same pattern, with its own `allowed_endpoints` and the SEC `User-Agent` default header.
