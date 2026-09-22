# Routine Scheduling Architecture

This document describes the Routine Scheduling feature, which allows routines to run automatically on a timer without user interaction.

## Overview

Routine Scheduling extends the existing Routines feature with automatic execution. A routine can have at most one schedule attached (one-to-one relationship). When a schedule fires, the backend creates a new conversation in the project, sends the routine's prompt as the first message, and runs the Gemini conversation to completion -- all without a WebSocket connection or any user interaction.

When a user is drilled into a project in the sidebar, the frontend automatically polls for new conversations every 30 seconds, so scheduled conversations appear without requiring a manual refresh. See [Frontend Architecture](frontend.md) (Sidebar Component section) for polling implementation details.

Three schedule types are supported:

- **Daily** -- runs at a specific local time (stored with IANA timezone, converted to UTC for efficient querying)
- **Hourly** -- runs at a given minute offset (0--59) from the top of each hour
- **Every N minutes** -- runs periodically at a fixed interval, skipping a run if the previous one is still in progress

Routines without a schedule continue to work as before (manual one-click invocation from the sidebar).

## RoutineSchedule Model

The `RoutineSchedule` model in `db/models.py` maps to the `routine_schedules` table. See [Database Architecture](database.md) for the column-level schema. Key constraints: unique index on `routine_id` enforces the one-to-one relationship (each routine can have at most one schedule), and a composite index on `(is_enabled, schedule_type)` supports efficient scheduler polling queries. The table stores schedule type, type-specific timing fields, run state tracking fields (`is_running`, `last_run_started_at`, `last_run_completed_at`), and the most recent conversation ID.

## Scheduler Daemon

The scheduler runs as an asyncio background task within the FastAPI process, started via the `lifespan` context manager in `quest.py`.

### Lifecycle

1. On FastAPI startup, the `lifespan` handler in `quest.py` spawns `scheduler_loop(app)` via `asyncio.create_task()`
2. The scheduler sleeps for `POLL_INTERVAL_SECONDS` (30 seconds) between poll cycles
3. On FastAPI shutdown (Ctrl+C, SIGTERM, or admin-triggered shutdown via `POST /app/api/admin/shutdown`):
   - The scheduler loop task is cancelled
   - All in-progress execution tasks tracked in `_active_execution_tasks` are cancelled via `scheduler.shutdown()`
   - `_execute_scheduled_run()` handles `CancelledError` by marking the schedule as failed and re-raising

### Poll Cycle

Each poll cycle in `_poll_and_execute()`:

1. Clears stale `is_running` flags older than `STALE_THRESHOLD_HOURS` (2 hours) via `clear_stale_running_flags()`
2. Queries `list_enabled_schedules()` to get all enabled schedules with their routine data
3. For each schedule, calls `_is_due()` to check if it should fire
4. If due, spawns `_execute_scheduled_run()` as an independent asyncio task (tracked in `_active_execution_tasks` set so it can be cancelled during shutdown; uses `add_done_callback` to auto-remove from the set when finished)

### Due-Checking Logic

`_is_due()` in `chat/scheduler.py` determines whether a schedule should fire. It returns a `(should_fire, reason)` tuple where the reason string describes why the schedule is or is not due (used for decision logging -- see below).

- **Daily**: Fires if the current UTC time matches `daily_time_utc` within the poll interval window and no run has started today
- **Hourly**: Fires if the current minute matches `hourly_minute` within the poll interval window and no run has started this hour
- **Every N minutes**: Fires if enough time has elapsed since `last_run_completed_at` (or `last_run_started_at` if never completed). Skips if `is_running` is `True`

### Decision Logging

Every poll cycle produces per-routine decision log lines at INFO level, followed by a poll cycle summary. This makes it straightforward to trace why a schedule did or did not fire without enabling DEBUG.

**Per-routine decision**: For each enabled schedule, the scheduler logs whether it fired or was skipped, along with the reason from `_is_due()`:

- `[scheduler] <routine_name>: FIRE -- <reason> (schedule=<id>, type=<type>)` when a schedule fires
- `[scheduler] <routine_name>: skip -- <reason> (schedule=<id>, type=<type>)` when a schedule is not due

Example reason strings by schedule type:

- Daily: `"daily time reached (14:00 UTC)"`, `"not in daily window (target=14:00 UTC, now=08:30 UTC)"`, `"already ran today (last_started=14:00:12 UTC)"`
- Hourly: `"hourly minute reached (:30)"`, `"not in hourly window (target=:30, now=:15)"`, `"already ran this hour (last_started=12:30:05 UTC)"`
- Every N minutes: `"interval elapsed (15.2m >= 15m)"`, `"not yet due (8.3m elapsed, 15m interval, 6.7m remaining)"`, `"still running (started=12:45:00 UTC)"`, `"never run before (interval=15m)"`

**Poll cycle summary**: After all schedules are checked, a summary line is logged (only when there are enabled schedules):

- `[scheduler] Poll complete: checked <N> schedule(s), fired <N> (now=<HH:MM:SS> UTC)`

### Execution Flow

When a scheduled run fires (`_execute_scheduled_run()` in `chat/scheduler.py`):

1. Look up the user record via `get_user_by_id()` from `db/user_store.py`
2. Create a new conversation in the project via `ChatStorage.create_project_conversation()`, passing the `routine_id` and the routine's `model` (if specified) so the conversation is linked to the routine and has the model set from creation
3. Mark the schedule as running via `mark_run_started()`
4. Save the routine prompt as a user message via `ChatStorage.append_message()`
5. Call `run_conversation_turn()` with a no-op `on_event` callback (no WebSocket to stream to), passing the routine's `model` if specified (read via `routine.get("model")` from the routine data joined in `list_enabled_schedules()`) and `routine_id` so the routine's auto-load skills are resolved and merged with user/project auto-loads (see [Skill Library Architecture](skill-library.md))
6. Persist the response messages via `_persist_scheduler_messages()` (a thin wrapper over `ChatStorage.append_structured_messages()`)
7. Mark the run as completed via `mark_run_completed()`
8. On `CancelledError` (server shutdown): mark the run as failed via `mark_run_failed()` and re-raise
9. On other errors: mark the run as failed via `mark_run_failed()`, then persist whatever `messages_out` accumulated before the failure -- including the durable `{"type": "error"}` message `run_conversation_turn()` appends on a fatal exception -- so the conversation shows why the routine stopped instead of ending silently after the prompt (see [Conversation Loop -- Error Surfacing](gemini-api.md#error-surfacing))

The resulting conversation appears in the project's sidebar grouped under the routine's collapsible entry, picked up automatically by the sidebar's 30-second polling mechanism when the user is drilled into the project (see [Routines Architecture](routines.md) for sidebar conversation grouping details and [Frontend Architecture](frontend.md) for polling details).

If a scheduled run issues `create_action_request`, the call blocks until the user resolves the card -- so the routine task suspends inside the dispatch arm and parks until a human approves, revises, or denies (or until the underlying wait-handle expiry fires at ~14 days). The suspend uses the same DB-backed pattern as web conversations: the sentinel unwinds the run cleanly, and `wait_resume.maybe_kick_resume()` on the resolve endpoint drives a fresh `run_conversation_turn()` to close out the dangling tool_use. Routines that need fire-and-forget writes should prefer `send_slack_dm_to_self`-style paths instead of `create_action_request`; see [Action Requests](action-requests.md) and the `system:action_requests` skill prose.

### Concurrency

- The scheduler and normal user requests share the same asyncio event loop
- Multiple scheduled runs can execute concurrently (each is its own asyncio task)
- The `is_running` flag prevents interval-based schedules from spawning overlapping runs
- SQLite write contention is minimal: schedule state updates are small and fast

## Timezone Handling for Daily Schedules

### On Schedule Creation/Update

1. The frontend sends `daily_time_local` (e.g., `"09:00"`) and the auto-detected `timezone` (e.g., `"America/New_York"`) from `Intl.DateTimeFormat().resolvedOptions().timeZone`
2. The backend converts the local time to UTC using `zoneinfo.ZoneInfo` and today's date for DST-aware conversion (see `_local_time_to_utc()` in `chat/schedule_routes.py`)
3. Both `daily_time_local` and `daily_time_utc` are stored, along with the IANA `timezone` string

### DST Reconversion

- The scheduler runs `_reconvert_daily_utc_times()` once per day (at midnight UTC), tracked by a module-level variable in `chat/scheduler.py`
- This recalculates `daily_time_utc` from `daily_time_local` + `timezone` using the current date, correctly handling DST transitions
- Example: `"09:00 America/New_York"` is UTC 14:00 during EST but UTC 13:00 during EDT. The reconversion updates `daily_time_utc` when DST boundaries are crossed
- If a timezone string is invalid, the reconversion error is logged and the schedule is skipped (graceful degradation)

## Skip-if-Running for Interval Schedules

### Mechanism

1. Before starting an interval-based run, `_is_due()` checks `schedule["is_running"]`
2. If `True`, the run is skipped (logged at INFO level with the reason "still running")
3. On run start: `mark_run_started()` sets `is_running=True` and `last_run_started_at`
4. On run complete: `mark_run_completed()` sets `is_running=False` and `last_run_completed_at`
5. On error: `mark_run_failed()` sets `is_running=False` but does not update `last_run_completed_at`

### Staleness Protection

`clear_stale_running_flags()` runs at the start of each poll cycle. If `is_running=True` and `last_run_started_at` is more than 2 hours ago, the flag is cleared. This prevents a crashed or stuck run from permanently blocking future runs. The 2-hour threshold is configurable via `STALE_THRESHOLD_HOURS`.

## Frontend

### RoutineSettingsModal Schedule UI

The RoutineSettingsModal uses a left navigation sidebar with Prompt, Schedule, and Delete sections (see [Routines Architecture](routines.md) for the full modal layout). The Schedule section contains:

1. **"Run on a schedule" checkbox** -- a single checkbox that controls whether the schedule is active. When unchecked, the schedule configuration inputs below remain visible but are grayed out and disabled, preserving the configuration for when the user re-enables the schedule
2. **Schedule Type** dropdown -- Daily / Hourly / Every N minutes
3. **Type-specific inputs**:
   - Daily: time picker (`HH:MM`) with the user's local timezone auto-detected via `Intl.DateTimeFormat().resolvedOptions().timeZone`
   - Hourly: number input for minute offset (0--59) with label "Minute of each hour"
   - Every N minutes: number input for interval (1--1440) with "skip if still running" note
4. **Last run info** -- read-only display of last run time and whether currently running (shown when a schedule exists and has been run)

Schedule state is loaded when the modal opens via `fetchRoutineSchedule()`. Schedule save/update/delete is integrated into the routine save flow, and schedule edits are guarded by the same `expected_updated_at` optimistic-concurrency token as routine edits -- see [Routines Architecture](routines.md) (Optimistic Concurrency).

Styles for the schedule section are in `frontend/src/components/RoutineSettingsModal.css` (`.routine-settings-schedule-fields`, `.routine-settings-last-run`).

### Sidebar Schedule Indicator

`frontend/src/components/Sidebar.tsx` shows a small clock icon next to routines that have an active schedule (`schedule.is_enabled`). The routine listing API includes schedule data (via a left-join in `db/routine_store.py`), avoiding N+1 API calls. Styles are in `frontend/src/components/Sidebar.css` (`.routine-schedule-indicator`).

## Cascade Deletion

Schedules are automatically cleaned up in multiple scenarios:

- **Routine deletion**: `ON DELETE CASCADE` FK on `routine_schedules.routine_id` removes the schedule when the parent routine is deleted
- **Project deletion**: Cascades through routines (project -> routine -> schedule)
- **User account deletion**: `ON DELETE CASCADE` FK on `routine_schedules.user_id` provides database-level cleanup

## Design Decisions

**Why a separate `routine_schedules` table instead of columns on `routines`?**
A separate table keeps the scheduling concern isolated from existing routine CRUD, avoids widening the routines table with many nullable columns, and makes it easy to query "all schedules that are due" across all users. The one-to-one relationship is enforced by a unique index on `routine_id`.

**Why a background asyncio task instead of Celery/APScheduler/cron?**
The app runs as a single uvicorn process, so an asyncio background task avoids external dependencies. The scheduler is started via FastAPI's `lifespan` event and shares the same event loop as normal requests.

**Why store both local time and UTC time for daily schedules?**
The UTC time enables efficient querying (the scheduler compares against the current UTC time). The local time and timezone are preserved so the UTC time can be reconverted when DST boundaries shift.

**Why fire-and-forget for scheduled run execution?**
Each scheduled run is spawned as an independent asyncio task so the scheduler loop continues polling without waiting for Gemini to complete. This allows multiple runs to execute concurrently. Tasks are tracked in `_active_execution_tasks` so they can be cleanly cancelled during server shutdown (both Ctrl+C and admin-triggered).

**Why create headless conversations instead of a separate execution model?**
Scheduled runs create real conversations that appear in the project's sidebar, grouped under the routine that created them (via `routine_id`). This reuses the existing conversation infrastructure (message persistence, workspace access, guide resolution) and gives users visibility into what the scheduler produced.

**Why skip-if-running only for interval schedules?**
Daily and hourly schedules have natural non-overlap (they fire at most once per day or once per hour). Interval schedules with short intervals (e.g., every 5 minutes) could easily overlap if a Gemini conversation takes longer than the interval, so the skip-if-running guard prevents resource waste.

## Constraints

- Each routine can have at most one schedule (enforced by the unique index on `routine_schedules.routine_id`)
- Deleting a routine cascades to its schedule via `ON DELETE CASCADE` on `routine_schedules.routine_id`
- Deleting a user cascades to schedules via `ON DELETE CASCADE` on `routine_schedules.user_id`
- The scheduler runs in-process as an asyncio background task (no external process manager needed)
- Schedule checking granularity is approximately 30 seconds (configurable via `POLL_INTERVAL_SECONDS`)
- The skip-if-running mechanism applies only to `every_n_minutes` schedules
- Daily schedules require a valid IANA timezone string from the frontend
- DST reconversion runs once per day at midnight UTC
- Stale `is_running` flags are cleared after 2 hours (configurable via `STALE_THRESHOLD_HOURS`)
- Scheduled runs create real conversations linked to the routine via `routine_id` that appear grouped under the routine in the project's sidebar
- Scheduled runs use the routine's specified model if set; otherwise fall back to the server config default model
- Scheduled runs load the routine's auto-load skills (from `routine_skill_autoloads`) in addition to the owner's user-level and project-level auto-loads; see [Skill Library Architecture](skill-library.md) for the merge order
- Valid `schedule_type` values: `'daily'`, `'hourly'`, `'every_n_minutes'`
- `hourly_minute` must be between 0 and 59
- `interval_minutes` must be between 1 and 1440 (24 hours)
- `daily_time_local` and `daily_time_utc` must be in `"HH:MM"` format
- No new external dependencies -- uses `zoneinfo` (Python standard library), `asyncio`, and existing project dependencies

