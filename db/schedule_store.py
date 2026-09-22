"""Routine schedule data access layer.

Provides async CRUD operations for routine schedules. Each routine can have at most
one schedule for automatic execution.

This module follows the same async pattern as db/routine_store.py:
each function opens a fresh AsyncSessionLocal() session.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, delete, update as sa_update

from db.engine import AsyncSessionLocal
from db.models import RoutineSchedule, Routine


class StaleScheduleError(Exception):
    """Raised when update_schedule() detects that the row was modified
    concurrently by someone else (expected_updated_at mismatch).

    Carries the current row (post-conflict) so the route handler can return
    it in the 409 body, sparing the FE a second GET round-trip.
    """

    def __init__(self, current: dict):
        super().__init__("Schedule was modified by another writer")
        self.current = current


async def create_schedule(
    user_id: int,
    routine_id: str,
    schedule_type: str,
    daily_time_utc: Optional[str] = None,
    daily_time_local: Optional[str] = None,
    timezone_str: Optional[str] = None,
    hourly_minute: Optional[int] = None,
    interval_minutes: Optional[int] = None,
) -> dict:
    """Create a schedule for a routine.

    Validates schedule_type and required fields for each type.
    Raises ValueError if a schedule already exists for this routine.
    """
    _validate_schedule_params(schedule_type, daily_time_utc, daily_time_local,
                              timezone_str, hourly_minute, interval_minutes)

    async with AsyncSessionLocal() as db:
        # Check for existing schedule
        result = await db.execute(
            select(RoutineSchedule).where(RoutineSchedule.routine_id == routine_id)
        )
        existing = result.scalars().first()
        if existing:
            raise ValueError("This routine already has a schedule. Delete it first or update it.")

        # Stamp updated_at on create so the optimistic-concurrency guard in
        # update_schedule() has a non-null baseline to compare on first edit.
        now = datetime.now(timezone.utc)
        schedule = RoutineSchedule(
            routine_id=routine_id,
            user_id=user_id,
            schedule_type=schedule_type,
            daily_time_utc=daily_time_utc,
            daily_time_local=daily_time_local,
            timezone=timezone_str,
            hourly_minute=hourly_minute,
            interval_minutes=interval_minutes,
            updated_at=now,
        )
        db.add(schedule)
        await db.commit()
        await db.refresh(schedule)
        return _schedule_to_dict(schedule)


async def get_schedule_for_routine(routine_id: str) -> Optional[dict]:
    """Get the schedule for a routine, or None if no schedule exists."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RoutineSchedule).where(RoutineSchedule.routine_id == routine_id)
        )
        schedule = result.scalars().first()
        return _schedule_to_dict(schedule) if schedule else None


async def update_schedule(
    schedule_id: str,
    user_id: int,
    schedule_type: Optional[str] = None,
    daily_time_utc: Optional[str] = ...,
    daily_time_local: Optional[str] = ...,
    timezone_str: Optional[str] = ...,
    hourly_minute: Optional[int] = ...,
    interval_minutes: Optional[int] = ...,
    is_enabled: Optional[bool] = None,
    expected_updated_at: Optional[str] = None,
) -> Optional[dict]:
    """Update a schedule. Uses ellipsis sentinels for nullable fields.

    ``expected_updated_at`` is the optimistic-concurrency token: if provided,
    it must match the row's current ``updated_at`` (ISO string) or the call
    raises :class:`StaleScheduleError`. Passing ``None`` skips the check
    (back-compat path for legacy rows with NULL updated_at).
    """
    async with AsyncSessionLocal() as db:
        schedule = await db.get(RoutineSchedule, schedule_id)
        if not schedule or schedule.user_id != user_id:
            return None

        # Optimistic concurrency check. Compare before mutating so we can
        # surface the current row state in the conflict response.
        if expected_updated_at is not None:
            current_token = schedule.updated_at.isoformat() if schedule.updated_at else None
            if current_token != expected_updated_at:
                raise StaleScheduleError(current=_schedule_to_dict(schedule))

        # Track whether anything actually changed so we don't bump
        # updated_at on a true no-op save (which would generate spurious
        # 409s for the next opener).
        changed = False
        if schedule_type is not None and schedule_type != schedule.schedule_type:
            schedule.schedule_type = schedule_type
            changed = True
        if daily_time_utc is not ... and daily_time_utc != schedule.daily_time_utc:
            schedule.daily_time_utc = daily_time_utc
            changed = True
        if daily_time_local is not ... and daily_time_local != schedule.daily_time_local:
            schedule.daily_time_local = daily_time_local
            changed = True
        if timezone_str is not ... and timezone_str != schedule.timezone:
            schedule.timezone = timezone_str
            changed = True
        if hourly_minute is not ... and hourly_minute != schedule.hourly_minute:
            schedule.hourly_minute = hourly_minute
            changed = True
        if interval_minutes is not ... and interval_minutes != schedule.interval_minutes:
            schedule.interval_minutes = interval_minutes
            changed = True
        if is_enabled is not None and is_enabled != schedule.is_enabled:
            schedule.is_enabled = is_enabled
            changed = True

        if changed:
            schedule.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(schedule)
        return _schedule_to_dict(schedule)


async def delete_schedule_for_routine(routine_id: str) -> bool:
    """Delete the schedule for a routine (if any)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(RoutineSchedule).where(RoutineSchedule.routine_id == routine_id)
        )
        await db.commit()
        return result.rowcount > 0


async def list_enabled_schedules() -> list[dict]:
    """List all enabled schedules (used by the scheduler daemon).

    Returns schedules joined with routine data (routine name, prompt, guide_id,
    project_id) so the scheduler has everything it needs to execute runs.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RoutineSchedule, Routine)
            .join(Routine, RoutineSchedule.routine_id == Routine.id)
            .where(RoutineSchedule.is_enabled == True)
        )
        rows = result.all()
        return [
            {**_schedule_to_dict(sched), "routine": _routine_summary(routine)}
            for sched, routine in rows
        ]


async def mark_run_started(schedule_id: str, conversation_id: str) -> None:
    """Mark a schedule as currently running."""
    async with AsyncSessionLocal() as db:
        schedule = await db.get(RoutineSchedule, schedule_id)
        if schedule:
            schedule.is_running = True
            schedule.last_run_started_at = datetime.now(timezone.utc)
            schedule.last_conversation_id = conversation_id
            await db.commit()


async def mark_run_completed(schedule_id: str) -> None:
    """Mark a schedule run as completed."""
    async with AsyncSessionLocal() as db:
        schedule = await db.get(RoutineSchedule, schedule_id)
        if schedule:
            schedule.is_running = False
            schedule.last_run_completed_at = datetime.now(timezone.utc)
            await db.commit()


async def mark_run_failed(schedule_id: str) -> None:
    """Mark a schedule run as failed (clears is_running)."""
    async with AsyncSessionLocal() as db:
        schedule = await db.get(RoutineSchedule, schedule_id)
        if schedule:
            schedule.is_running = False
            await db.commit()


async def clear_stale_running_flags(stale_threshold_hours: int = 2) -> int:
    """Clear is_running flags for schedules stuck in running state.

    A schedule is considered stale if is_running=True and last_run_started_at
    is more than stale_threshold_hours ago. Returns count of cleared flags.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_threshold_hours)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            sa_update(RoutineSchedule)
            .where(
                RoutineSchedule.is_running == True,
                RoutineSchedule.last_run_started_at < cutoff,
            )
            .values(is_running=False)
        )
        await db.commit()
        return result.rowcount


def _validate_schedule_params(schedule_type, daily_time_utc, daily_time_local,
                               timezone_str, hourly_minute, interval_minutes):
    """Validate schedule parameters based on type."""
    valid_types = ('daily', 'hourly', 'every_n_minutes')
    if schedule_type not in valid_types:
        raise ValueError(f"Invalid schedule_type: {schedule_type}. Must be one of {valid_types}")

    if schedule_type == 'daily':
        if not daily_time_local or not timezone_str:
            raise ValueError("Daily schedule requires daily_time_local and timezone")
        _validate_time_string(daily_time_local)
        if daily_time_utc:
            _validate_time_string(daily_time_utc)

    elif schedule_type == 'hourly':
        if hourly_minute is None:
            raise ValueError("Hourly schedule requires hourly_minute")
        if not (0 <= hourly_minute <= 59):
            raise ValueError("hourly_minute must be between 0 and 59")

    elif schedule_type == 'every_n_minutes':
        if interval_minutes is None:
            raise ValueError("Interval schedule requires interval_minutes")
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be at least 1")
        if interval_minutes > 1440:
            raise ValueError("interval_minutes cannot exceed 1440 (24 hours)")


def _validate_time_string(time_str: str):
    """Validate a time string in HH:MM format."""
    if not time_str or len(time_str) != 5 or time_str[2] != ':':
        raise ValueError(f"Invalid time format: {time_str}. Must be HH:MM")
    try:
        h, m = int(time_str[:2]), int(time_str[3:])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError()
    except ValueError:
        raise ValueError(f"Invalid time format: {time_str}. Must be HH:MM with valid hours/minutes")


def _schedule_to_dict(schedule: RoutineSchedule) -> dict:
    """Convert a RoutineSchedule ORM instance to a plain dict."""
    return {
        "id": schedule.id,
        "routine_id": schedule.routine_id,
        "user_id": schedule.user_id,
        "schedule_type": schedule.schedule_type,
        "daily_time_utc": schedule.daily_time_utc,
        "daily_time_local": schedule.daily_time_local,
        "timezone": schedule.timezone,
        "hourly_minute": schedule.hourly_minute,
        "interval_minutes": schedule.interval_minutes,
        "is_enabled": schedule.is_enabled,
        "last_run_started_at": schedule.last_run_started_at.isoformat() if schedule.last_run_started_at else None,
        "last_run_completed_at": schedule.last_run_completed_at.isoformat() if schedule.last_run_completed_at else None,
        "is_running": schedule.is_running,
        "last_conversation_id": schedule.last_conversation_id,
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
        "updated_at": schedule.updated_at.isoformat() if schedule.updated_at else None,
    }


def _routine_summary(routine: Routine) -> dict:
    """Extract the routine fields needed by the scheduler."""
    return {
        "id": routine.id,
        "project_id": routine.project_id,
        "user_id": routine.user_id,
        "name": routine.name,
        "prompt": routine.prompt,
        "guide_id": routine.guide_id,
        "model": routine.model,
    }
