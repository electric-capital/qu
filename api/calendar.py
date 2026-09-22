"""Google Calendar API instructions for the system prompt.

Calendar read operations are handled via the ``authed_get`` tool, which
makes authenticated GET requests directly to the Google Calendar API at
``https://www.googleapis.com/calendar/v3/...``.  The write side
(``create_calendar_invite`` action request) is handled separately and
remains unchanged.
"""

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


# ---------------------------------------------------------------------------
# Instruction text for the Calendar API
# ---------------------------------------------------------------------------

def get_instructions(base_url: str) -> str:
    """Calendar API documentation section (accessed via authed_get)."""
    from auth.config import allowed_login_domain

    login_domain = allowed_login_domain()
    return f"""## Google Calendar API (via authed_get)

Access the Google Calendar API using `authed_get` with the full Google Calendar API URL. Authentication is handled automatically.

**Base URL:** `{CALENDAR_API_BASE}`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/calendar/v3/users/me/calendarList` | List calendars in calendar list |
| `/calendar/v3/users/me/calendarList/{{calendarId}}` | Get a specific calendar |
| `/calendar/v3/calendars/{{calendarId}}/events` | List events in a calendar |
| `/calendar/v3/calendars/{{calendarId}}/events/{{eventId}}` | Get a specific event |

**Calendar List Parameters:**
- `maxResults`: Maximum number of calendars per page
- `pageToken`: Token for pagination
- `showDeleted`: Include deleted calendars
- `showHidden`: Include hidden calendars
- `fields`: Fields to include in response (e.g., 'items(id,summary)'). Use to reduce response size and save tokens.

**Event List Parameters:**
- `maxResults`: Maximum number of events per page (default: 250)
- `pageToken`: Token for pagination
- `timeMin`: Lower bound (inclusive) for event start time (RFC3339 timestamp, e.g., '2025-01-28T00:00:00Z')
- `timeMax`: Upper bound (exclusive) for event end time (RFC3339 timestamp)
- `q`: Free text search terms
- `singleEvents`: Whether to expand recurring events (default: false, set to true for individual instances)
- `orderBy`: Order of events ('startTime' requires singleEvents=true, or 'updated')
- `fields`: Fields to include in response (e.g., 'items(summary,start,end,attendees)'). Use to reduce response size and save tokens.

**Example tool calls:**

```
# List all calendars
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/users/me/calendarList"}})

# Get primary calendar details
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/users/me/calendarList/primary"}})

# List upcoming events from primary calendar
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/calendars/primary/events?timeMin=2025-01-28T00:00:00Z&singleEvents=true&orderBy=startTime&maxResults=10"}})

# List events in a date range
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/calendars/primary/events?timeMin=2025-01-01T00:00:00Z&timeMax=2025-02-01T00:00:00Z&singleEvents=true"}})

# Search for events by text
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/calendars/primary/events?q=meeting&singleEvents=true"}})

# Get specific event
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/calendars/primary/events/EVENT_ID"}})

# List events with only summary and times (saves tokens)
tool_call(tool_name="authed_get", arguments={{"url": "{CALENDAR_API_BASE}/calendars/primary/events?timeMin=2025-01-28T00:00:00Z&singleEvents=true&orderBy=startTime&fields=items(summary,start,end)"}})
```

**Important Notes:**
- Use 'primary' as the calendar ID for the user's primary calendar
- For event listing with `orderBy=startTime`, you must set `singleEvents=true`
- Timestamps must be in RFC3339 format (e.g., '2025-01-28T00:00:00Z')
- Calendar IDs can be obtained from the calendar list call
- Calendars of other @{login_domain} users are usually visible from the Google Calendar API even if they are not in the user's calendarList. You can query their events by using their email address as the `calendarId` (e.g., `{CALENDAR_API_BASE}/calendars/colleague@{login_domain}/events`)
- Use the `fields` parameter to request only the data you need, which reduces response size and saves tokens. Example: `fields=items(summary,start,end)` for event listings.

---

## Calendar Write Operations (via create_action_request)

Creating calendar events / invites goes through `create_action_request`
so the user can approve before the event is created or invitations are
sent. Never POST to the Calendar API directly. `create_action_request`
is top-level only -- if you are running as a sub-agent, do not call it;
return the proposed `request_type` and `params` to the parent via
`agent_task_response` instead.

### `create_calendar_invite` — create a Google Calendar event

Parameters:
- `summary` (string, required): Event title.
- `start` (string, required): Start time as an RFC3339 datetime. Include
  a timezone offset (e.g. `2026-03-18T16:00:00-07:00`).
- `end` (string, required): End time as an RFC3339 datetime.
- `calendar_id` (string, optional): Defaults to `"primary"`. For a
  non-primary calendar, first look up its id via `authed_get` on
  `{CALENDAR_API_BASE}/users/me/calendarList`.
- `attendees` (array of string, optional): Email addresses to invite.
  When present, Google sends invite emails after approval.
- `location` (string, optional): Free-form location string.
- `description` (string, optional): Event description / notes.
- `time_zone` (string, optional): IANA timezone (e.g.
  `America/Los_Angeles`). When omitted, the event uses the timezone
  embedded in the `start` / `end` datetimes.

Example params:
```json
{{
  "summary": "Team sync",
  "start": "2026-03-18T16:00:00-07:00",
  "end": "2026-03-18T16:30:00-07:00",
  "calendar_id": "primary",
  "attendees": ["alice@example.com"],
  "location": "Zoom",
  "description": "Weekly sync",
  "time_zone": "America/Los_Angeles"
}}
```

Best practices:
- Use `authed_get` on the Calendar API first to check for conflicts
  before proposing a meeting time.
- To change an EXISTING event (e.g. "move the 3pm meeting to 4pm"), use
  `edit_calendar_event` below -- never create a duplicate invite.

### `edit_calendar_event` — update an existing Google Calendar event

Read the event first with `authed_get` on
`{CALENDAR_API_BASE}/calendars/{{calendarId}}/events/{{eventId}}` (the
full resource, not a `fields=`-trimmed list row) so you have its `id`,
`updated` timestamp, and current values. Only the fields you pass are
changed; everything else on the event is left untouched.

Parameters:
- `event_id` (string, required): The event's `id` from the read.
- `expected_updated` (string, required): The event's `updated` timestamp
  exactly as returned by the read. The edit is rejected -- at proposal
  time and again at approval time -- if the event has changed since,
  so you never overwrite a version you have not seen. On rejection,
  re-read the event and propose again with the new value.
- `calendar_id` (string, optional): Defaults to `"primary"`; must be the
  calendar the event was read from.
- `summary` (string, optional): New title.
- `start` / `end` (string, optional): New times as RFC3339 datetimes
  with a timezone offset, or `YYYY-MM-DD` dates for all-day events
  (all-day `end` is exclusive). Either may be given alone; when
  switching between timed and all-day, pass both.
- `time_zone` (string, optional): IANA timezone applied to the event's
  start and end.
- `attendees` (array of string, optional): The COMPLETE replacement
  attendee list -- include the existing attendees you want to keep;
  anyone omitted is removed. Attendees are emailed about the change.
- `location` (string, optional): New location; `""` clears it.
- `description` (string, optional): New description; `""` clears it.
  The approval card shows a line diff against the current description.

Example params (move a meeting one hour later and add an attendee):
```json
{{
  "event_id": "abc123def456",
  "calendar_id": "primary",
  "expected_updated": "2026-03-10T18:22:41.512Z",
  "start": "2026-03-18T17:00:00-07:00",
  "end": "2026-03-18T17:30:00-07:00",
  "attendees": ["alice@example.com", "bob@example.com"]
}}
```

Notes:
- Editing a recurring event's master id changes EVERY occurrence; to
  change one occurrence, list the calendar's events with
  `singleEvents=true` and use that instance's id (it looks like
  `<masterId>_20260318T160000Z`). The approval card states which scope
  applies.
- Cancelled (deleted) events cannot be edited.
- A request whose fields all match the event already is rejected as a
  no-op."""
