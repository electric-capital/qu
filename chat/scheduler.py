"""Routine scheduler daemon.

Runs as an asyncio background task within the FastAPI process. Polls for
due schedules every POLL_INTERVAL_SECONDS and executes them by creating
conversations and calling run_conversation_turn() directly.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# How often the scheduler checks for due schedules
POLL_INTERVAL_SECONDS = 30

# Maximum age of a "running" flag before it's considered stale
STALE_THRESHOLD_HOURS = 2

# Track last DST reconversion date (module-level state)
_last_reconversion_date = None

# Track active execution tasks so they can be cancelled during shutdown
_active_execution_tasks: set[asyncio.Task] = set()


async def scheduler_loop(app) -> None:
    """Main scheduler loop. Runs forever, polling for due schedules.

    Args:
        app: The FastAPI application instance (needed for run_conversation_turn).
    """
    logger.info("[scheduler] Routine scheduler started (poll interval: %ds)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            await _poll_and_execute(app)
        except asyncio.CancelledError:
            logger.info("[scheduler] Scheduler loop cancelled, shutting down")
            raise
        except Exception:
            logger.exception("[scheduler] Error in scheduler poll cycle")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _poll_and_execute(app) -> None:
    """Single poll cycle: find due schedules and execute them."""
    global _last_reconversion_date

    from db.schedule_store import list_enabled_schedules, clear_stale_running_flags

    # Clear stale running flags first
    stale_cleared = await clear_stale_running_flags(STALE_THRESHOLD_HOURS)
    if stale_cleared > 0:
        logger.warning("[scheduler] Cleared %d stale running flags", stale_cleared)

    # Run DST reconversion once per day at midnight UTC
    today_utc = datetime.now(timezone.utc).date()
    if _last_reconversion_date != today_utc:
        _last_reconversion_date = today_utc
        await _reconvert_daily_utc_times()

    schedules = await list_enabled_schedules()
    now = datetime.now(timezone.utc)

    fired_count = 0
    for entry in schedules:
        schedule = entry
        routine = entry["routine"]

        try:
            due, reason = _is_due(schedule, now)
            if due:
                fired_count += 1
                logger.info(
                    "[scheduler] %s: FIRE -- %s (schedule=%s, type=%s)",
                    routine["name"], reason, schedule["id"], schedule["schedule_type"],
                )
                # Fire-and-forget as an asyncio task so we don't block
                # the scheduler loop waiting for Gemini to respond.
                # Track the task so it can be cancelled during shutdown.
                task = asyncio.create_task(
                    _execute_scheduled_run(app, schedule, routine)
                )
                _active_execution_tasks.add(task)
                task.add_done_callback(_active_execution_tasks.discard)
            else:
                logger.info(
                    "[scheduler] %s: skip -- %s (schedule=%s, type=%s)",
                    routine["name"], reason, schedule["id"], schedule["schedule_type"],
                )
        except Exception:
            logger.exception(
                "[scheduler] Error checking schedule %s (routine=%s)",
                schedule["id"], routine["name"],
            )

    if schedules:
        logger.info(
            "[scheduler] Poll complete: checked %d schedule(s), fired %d (now=%s)",
            len(schedules), fired_count, now.strftime("%H:%M:%S UTC"),
        )


def _is_due(schedule: dict, now: datetime) -> tuple[bool, str]:
    """Determine if a schedule should fire right now.

    Returns a (should_fire, reason) tuple. The reason string describes
    why the schedule is or is not due, used for decision logging.

    For 'daily': fires if current UTC time matches daily_time_utc within
    the poll interval window and last_run_started_at is not today.

    For 'hourly': fires if current minute matches hourly_minute within
    the poll interval window and last_run_started_at is not this hour.

    For 'every_n_minutes': fires if enough time has elapsed since
    last_run_completed_at (or last_run_started_at if never completed).
    Skips if is_running is True.
    """
    schedule_type = schedule["schedule_type"]
    last_started = _parse_iso(schedule.get("last_run_started_at"))

    if schedule_type == "daily":
        utc_time = schedule.get("daily_time_utc")
        if not utc_time:
            return False, "daily schedule has no UTC time configured"
        target_h, target_m = int(utc_time[:2]), int(utc_time[3:])

        # Check if we're within the poll window of the target time
        target_today = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        window_start = target_today - timedelta(seconds=POLL_INTERVAL_SECONDS)
        window_end = target_today + timedelta(seconds=POLL_INTERVAL_SECONDS)

        if not (window_start <= now <= window_end):
            return False, "not in daily window (target=%s UTC, now=%s UTC)" % (
                utc_time, now.strftime("%H:%M"))

        # Don't fire again if already ran today
        if last_started and last_started.date() == now.date():
            return False, "already ran today (last_started=%s)" % (
                last_started.strftime("%H:%M:%S UTC"),)

        return True, "daily time reached (%s UTC)" % utc_time

    elif schedule_type == "hourly":
        target_minute = schedule.get("hourly_minute")
        if target_minute is None:
            return False, "hourly schedule has no minute configured"

        # Check if we're within the poll window of the target minute
        target_this_hour = now.replace(minute=target_minute, second=0, microsecond=0)
        window_start = target_this_hour - timedelta(seconds=POLL_INTERVAL_SECONDS)
        window_end = target_this_hour + timedelta(seconds=POLL_INTERVAL_SECONDS)

        if not (window_start <= now <= window_end):
            return False, "not in hourly window (target=:%02d, now=:%02d)" % (
                target_minute, now.minute)

        # Don't fire again if already ran this hour
        if last_started and last_started.replace(minute=0, second=0, microsecond=0) == now.replace(minute=0, second=0, microsecond=0):
            return False, "already ran this hour (last_started=%s)" % (
                last_started.strftime("%H:%M:%S UTC"),)

        return True, "hourly minute reached (:%02d)" % target_minute

    elif schedule_type == "every_n_minutes":
        interval = schedule.get("interval_minutes")
        if not interval:
            return False, "interval schedule has no interval_minutes configured"

        # Skip if currently running
        if schedule.get("is_running"):
            return False, "still running (started=%s)" % (
                last_started.strftime("%H:%M:%S UTC") if last_started else "unknown",)

        # Check if enough time has elapsed
        last_completed = _parse_iso(schedule.get("last_run_completed_at"))
        reference_time = last_completed or last_started

        if reference_time is None:
            # Never run before -- it's due
            return True, "never run before (interval=%dm)" % interval

        elapsed = (now - reference_time).total_seconds() / 60.0
        if elapsed >= interval:
            return True, "interval elapsed (%.1fm >= %dm)" % (elapsed, interval)
        else:
            remaining = interval - elapsed
            return False, "not yet due (%.1fm elapsed, %dm interval, %.1fm remaining)" % (
                elapsed, interval, remaining)

    return False, "unknown schedule_type '%s'" % schedule_type


async def shutdown() -> None:
    """Cancel all active execution tasks and wait for them to finish.

    Called during server shutdown to ensure all in-progress scheduled runs
    are cleanly terminated.
    """
    tasks = list(_active_execution_tasks)
    if not tasks:
        logger.info("[scheduler] No active execution tasks to cancel")
        return

    logger.info("[scheduler] Cancelling %d active execution task(s)", len(tasks))
    for task in tasks:
        task.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    cancelled_count = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
    logger.info(
        "[scheduler] Shutdown complete: %d task(s) cancelled, %d already finished",
        cancelled_count, len(tasks) - cancelled_count,
    )


async def _execute_scheduled_run(app, schedule: dict, routine: dict) -> None:
    """Execute a single scheduled routine run.

    Creates a new conversation in the project, then calls run_conversation_turn()
    directly (headless -- no WebSocket stream).
    """
    from db.schedule_store import mark_run_started, mark_run_completed, mark_run_failed
    from db.user_store import get_user_by_id
    from chat.storage import ChatStorage
    from chat.gemini_api import run_conversation_turn

    schedule_id = schedule["id"]
    routine_id = routine["id"]
    routine_name = routine["name"]
    project_id = routine["project_id"]
    user_id = routine["user_id"]
    prompt = routine["prompt"]
    guide_id = routine.get("guide_id")
    routine_model = routine.get("model")

    logger.info(
        "[scheduler] Executing scheduled run: routine=%s, project=%s, user=%d, type=%s",
        routine_name, project_id, user_id, schedule["schedule_type"],
    )

    # Look up the user record (needed by run_conversation_turn)
    user = await get_user_by_id(user_id)
    if not user:
        logger.error("[scheduler] User %d not found for schedule %s, skipping", user_id, schedule_id)
        return

    # Create a new conversation in the project
    try:
        conversation_id, _created_at = await ChatStorage.create_project_conversation(
            user_id, project_id, routine_id=routine_id, model=routine_model,
        )
    except Exception:
        logger.exception(
            "[scheduler] Failed to create conversation for schedule %s (routine=%s)",
            schedule_id, routine_name,
        )
        return

    # Mark the schedule as running
    await mark_run_started(schedule_id, conversation_id)

    # Shared messages list for persistence. Defined outside the try so the
    # failure path below can persist partial progress + the durable error
    # message without risking a NameError.
    messages_out: list[dict] = []

    try:
        # Save the user message (the routine prompt)
        from chat.storage import utc_timestamp
        user_timestamp = utc_timestamp()
        await ChatStorage.append_message(
            conversation_id=conversation_id,
            role="user",
            content=prompt,
            timestamp=user_timestamp,
        )

        # No-op event handler (no WebSocket to stream to)
        async def noop_event(event: dict) -> None:
            pass

        # Determine the user's timezone for the Gemini call
        user_tz = schedule.get("timezone") or "UTC"

        # Run the Gemini API conversation to completion
        await run_conversation_turn(
            app=app,
            user=user,
            message=prompt,
            conversation_id=conversation_id,
            timezone=user_tz,
            model=routine_model,
            on_event=noop_event,
            messages_out=messages_out,
            guide_id=guide_id,
            project_id=project_id,
            routine_id=routine_id,
        )

        # Persist the response messages
        await _persist_scheduler_messages(conversation_id, messages_out)

        await mark_run_completed(schedule_id)
        logger.info(
            "[scheduler] Completed scheduled run: routine=%s, conversation=%s",
            routine_name, conversation_id,
        )

    except asyncio.CancelledError:
        await mark_run_failed(schedule_id)
        logger.info(
            "[scheduler] Cancelled scheduled run (shutdown): routine=%s, schedule=%s, conversation=%s",
            routine_name, schedule_id, conversation_id,
        )
        raise

    except Exception:
        await mark_run_failed(schedule_id)
        logger.exception(
            "[scheduler] Failed scheduled run: routine=%s, schedule=%s, conversation=%s",
            routine_name, schedule_id, conversation_id,
        )
        # Persist whatever the run produced before it died -- including the
        # durable ``error`` message run_conversation_turn appends on failure -- so
        # the conversation shows why the routine stopped instead of ending
        # silently after the prompt.
        try:
            await _persist_scheduler_messages(conversation_id, messages_out)
        except Exception:
            logger.exception(
                "[scheduler] Failed to persist messages for failed run "
                "(conversation=%s)", conversation_id,
            )


async def _persist_scheduler_messages(
    conversation_id: str, messages_out: list[dict],
) -> None:
    """Append accumulated run messages to the conversation history."""
    from chat.storage import ChatStorage, utc_timestamp

    if not messages_out:
        return
    assistant_timestamp = utc_timestamp()
    for msg in messages_out:
        if "timestamp" not in msg:
            msg["timestamp"] = assistant_timestamp
    await ChatStorage.append_structured_messages(
        conversation_id=conversation_id,
        messages=messages_out,
    )


async def _reconvert_daily_utc_times() -> None:
    """Reconvert all daily schedule UTC times from local time + timezone.

    Called once per day (at midnight UTC) to handle DST transitions.
    """
    from db.schedule_store import list_enabled_schedules, update_schedule
    from chat.schedule_routes import _local_time_to_utc

    schedules = await list_enabled_schedules()
    for entry in schedules:
        schedule = entry
        if schedule["schedule_type"] != "daily":
            continue
        local_time = schedule.get("daily_time_local")
        tz = schedule.get("timezone")
        if not local_time or not tz:
            continue
        try:
            new_utc = _local_time_to_utc(local_time, tz)
            if new_utc != schedule.get("daily_time_utc"):
                await update_schedule(
                    schedule_id=schedule["id"],
                    user_id=schedule["user_id"],
                    daily_time_utc=new_utc,
                )
                logger.info(
                    "[scheduler] DST reconversion: schedule %s UTC time %s -> %s",
                    schedule["id"], schedule.get("daily_time_utc"), new_utc,
                )
        except Exception:
            logger.exception(
                "[scheduler] Failed DST reconversion for schedule %s",
                schedule["id"],
            )


def _parse_iso(iso_str: str | None) -> datetime | None:
    """Parse an ISO datetime string to a timezone-aware datetime, or None."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
