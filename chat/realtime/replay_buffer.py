"""Per-conversation ring buffer of recently-flushed durable messages.

Used by the persistent WS subscribe handler to fulfil ``catchup`` responses
when a client reconnects with a stale ``last_seq`` that is still inside the
buffer's window. If the requested ``after_seq`` is below the buffer's
earliest seq, :meth:`ReplayBuffer.slice` returns ``None`` to signal that the
caller must fall back to ``resync`` (REST-fetch the full conversation).

Phase 1 ships the skeleton; Phase 2 wires it into the flush callback so
durable events land in the buffer alongside their bus publish.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Optional

from chat.realtime.bus import bus

logger = logging.getLogger(__name__)


# Per-conversation buffer size. An active turn rarely emits more than ~50
# structured messages between flush boundaries; 200 absorbs a brief tab
# disconnect across even an aggressive turn before forcing a resync.
DEFAULT_BUFFER_MAXLEN = 200

# Idle-eviction sweep cadence and per-conversation idle threshold. Buffers
# with no subscribers and no appends for the threshold get dropped to keep
# memory bounded across many long-lived but inactive conversations.
EVICTION_SWEEP_INTERVAL_SECONDS = 300
IDLE_EVICTION_SECONDS = 1800


class ReplayBuffer:
    """In-memory ring buffer keyed by ``conversation_id``."""

    def __init__(self, maxlen: int = DEFAULT_BUFFER_MAXLEN) -> None:
        self._maxlen = maxlen
        # Each value is a deque of (seq, message_dict) tuples in append order.
        self._buffers: dict[str, collections.deque[tuple[int, dict]]] = {}
        # Last-append monotonic timestamp per conversation, used by the idle
        # sweep to evict dormant buffers without subscribers.
        self._last_append_at: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Append / slice
    # ------------------------------------------------------------------

    def append(self, conversation_id: str, seq: int, message: dict) -> None:
        """Record one freshly-flushed message at the given seq."""
        buf = self._buffers.get(conversation_id)
        if buf is None:
            buf = collections.deque(maxlen=self._maxlen)
            self._buffers[conversation_id] = buf
        buf.append((seq, message))
        self._last_append_at[conversation_id] = time.monotonic()

    def slice(
        self, conversation_id: str, after_seq: int,
    ) -> Optional[list[tuple[int, dict]]]:
        """Return ``[(seq, message), ...]`` with ``seq > after_seq``.

        Returns:
            * ``[]`` -- nothing newer than ``after_seq`` in the buffer (caller
              should respond ``up_to_date``).
            * non-empty list -- the catchup window.
            * ``None`` -- ``after_seq`` is older than the buffer's earliest
              seq, so the caller must respond ``resync`` instead.
        """
        buf = self._buffers.get(conversation_id)
        if buf is None or not buf:
            return []

        earliest_seq = buf[0][0]
        if after_seq + 1 < earliest_seq:
            # Gap is bigger than what we have buffered -- caller must resync.
            return None

        return [(seq, msg) for (seq, msg) in buf if seq > after_seq]

    def latest_seq(self, conversation_id: str) -> Optional[int]:
        buf = self._buffers.get(conversation_id)
        if not buf:
            return None
        return buf[-1][0]

    # ------------------------------------------------------------------
    # Idle eviction
    # ------------------------------------------------------------------

    def evict_idle(
        self,
        idle_seconds: float = IDLE_EVICTION_SECONDS,
        now: Optional[float] = None,
    ) -> int:
        """Drop buffers with no subscribers and no recent appends.

        Returns the number of conversations evicted (used by tests).
        """
        ts_now = now if now is not None else time.monotonic()
        evicted = 0
        for conv_id in list(self._buffers.keys()):
            last_append = self._last_append_at.get(conv_id, 0.0)
            if ts_now - last_append < idle_seconds:
                continue
            if bus.conversation_subscriber_count(conv_id) > 0:
                continue
            self._buffers.pop(conv_id, None)
            self._last_append_at.pop(conv_id, None)
            evicted += 1
        if evicted:
            logger.debug(
                "[replay-buffer] Evicted %d idle conversation(s)", evicted,
            )
        return evicted


# Module-level singleton.
replay_buffer = ReplayBuffer()


async def eviction_loop(
    interval_seconds: float = EVICTION_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Background task that periodically calls :meth:`ReplayBuffer.evict_idle`.

    Spawned from the FastAPI lifespan so the buffer never grows unbounded
    when a conversation is created and then never re-opened.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            replay_buffer.evict_idle()
        except Exception:
            logger.exception("[replay-buffer] Idle eviction sweep failed")
