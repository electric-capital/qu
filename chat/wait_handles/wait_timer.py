"""Per-handle background timers that enforce ``timeout_seconds`` for
``wait_for_handles`` suspends.

Replaces the deleted in-process future registry. The contract here is
intentionally narrow:

- When ``_await_wait_for_handles`` raises :class:`SuspendForWaitHandles`,
  it first calls :func:`schedule_timeout_for_handles` to register a
  background ``asyncio.Task`` that sleeps for ``timeout_seconds``.
- On expiry, the task marks any still-pending rows ``timed_out`` in the
  DB and calls :func:`chat.wait_handles.resume.maybe_kick_resume` so the
  suspended conversation wakes up and the model sees the new status.
- If the handle is resolved before the deadline, the task observes the
  non-pending status when it fires and no-ops (we do not eagerly cancel
  the task on resolve -- the in-process registry is a pure firing
  convenience, not a coordination primitive).

The DB ``expires_at`` sweep remains the restart-resilience fallback for
in-memory timers lost on a process restart. This module deliberately does
not implement futures, register/discard plumbing, or shutdown drain
coupling -- the durable behaviour lives in the DB.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Active timer tasks keyed by handle_id. We keep a reference so the tasks
# do not get garbage-collected mid-flight, and so the same handle can
# replace its timer if a later wait_for_handles call extends the deadline.
_active_timers: dict[str, asyncio.Task] = {}


def schedule_timeout_for_handles(
    user_id: int,
    conversation_id: str,
    handle_ids: list[str],
    timeout_seconds: int,
    *,
    app: Any = None,
    user: Any = None,
) -> None:
    """Schedule a per-handle expiry task.

    Best-effort: a missing event loop (e.g. called outside an asyncio
    context, which should not happen from the model loop) is logged and
    skipped. ``app`` and ``user`` are optional; when omitted, the
    fallback resume kick on expiry is skipped (the next ``wait_for_handles``
    call still picks up the ``timed_out`` status from the DB).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "[wait-timer] No running loop; cannot schedule timeout for "
            "handles=%s",
            handle_ids,
        )
        return

    for handle_id in handle_ids:
        existing = _active_timers.get(handle_id)
        if existing is not None and not existing.done():
            existing.cancel()
        task = loop.create_task(
            _expire_after(
                user_id=user_id,
                conversation_id=conversation_id,
                handle_id=handle_id,
                timeout_seconds=timeout_seconds,
                app=app,
                user=user,
            )
        )
        _active_timers[handle_id] = task
        task.add_done_callback(
            lambda t, hid=handle_id: _active_timers.pop(hid, None)
            if _active_timers.get(hid) is t else None
        )


async def _expire_after(
    user_id: int,
    conversation_id: str,
    handle_id: str,
    timeout_seconds: int,
    app: Any,
    user: Any,
) -> None:
    """Sleep, then mark a still-pending handle ``timed_out`` and kick resume."""
    try:
        await asyncio.sleep(timeout_seconds)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.debug(
            "[wait-timer] Sleep failed for handle %s",
            handle_id, exc_info=True,
        )
        return

    # If the row resolved during the sleep, mark_timed_out is a no-op (it
    # only flips rows that are still PENDING). We then still kick a resume:
    # if a sibling resolution already drove a resume, maybe_kick_resume is
    # dedupe-safe via _active_resumes.
    try:
        from db import tool_wait_handle_store
        flipped = await tool_wait_handle_store.mark_timed_out(
            [handle_id], user_id,
        )
    except Exception:
        logger.exception(
            "[wait-timer] mark_timed_out failed for handle %s",
            handle_id,
        )
        flipped = []

    if not flipped:
        # Already resolved by another path -- nothing for us to wake up.
        return

    logger.info(
        "[wait-timer] Handle %s timed out after %ds (conversation=%s)",
        handle_id, timeout_seconds, conversation_id,
    )

    try:
        from chat.realtime import bus, events as realtime_events
        # mark_timed_out only flips currently-pending rows, so ``flipped`` is
        # the set of handles we just nudged to ``timed_out``. We re-derive
        # the kind from the row so the envelope stays accurate.
        from db import tool_wait_handle_store as _store
        row = await _store.get_handle_for_user(user_id, handle_id)
        bus.publish_to_user(
            user_id,
            realtime_events.make_wait_handle_resolved(
                conversation_id=conversation_id,
                handle_id=handle_id,
                kind=(row or {}).get("kind") or "",
                status="timed_out",
            ),
        )
    except Exception:
        logger.debug(
            "[wait-timer] publish wait_handle_resolved (timeout) failed",
            exc_info=True,
        )

    if app is None or user is None:
        # No resume-kick context. The next wait_for_handles call (or the
        # restart-time expires_at sweep) will surface the timed_out status
        # to the model.
        return

    try:
        from chat.wait_handles import resume as wait_resume
        wait_resume.maybe_kick_resume(app, user, conversation_id)
    except Exception:
        logger.exception(
            "[wait-timer] Failed to kick resume after timeout for handle %s "
            "(conversation=%s)",
            handle_id, conversation_id,
        )
