"""Generic wait-handle mechanism for tools awaiting human resolution.

The model-facing surface is the ``wait_for_handles`` tool, dispatched via
``tool_call``. Tools that need confirmation insert a row into
``tool_wait_handles`` and return its id; the model then calls
``wait_for_handles`` to suspend the conversation until the row is resolved.

The DB row is the source of truth. There is no in-process future
registry: ``_await_wait_for_handles`` raises a sentinel exception that
unwinds the model loop, leaving the dangling ``wait_for_handles``
``tool_use`` on disk. When a wait-handle row is resolved (REST,
WebSocket, or background timer expiry), the resume path in
:mod:`chat.wait_handles.resume` re-queries the DB and continues.

The :mod:`chat.wait_handles.wait_timer` module schedules per-handle
background tasks that enforce ``timeout_seconds`` by flipping rows to
``timed_out`` on expiry. The DB ``expires_at`` sweep remains the
restart-resilience fallback for in-memory timers lost on restart.
"""
