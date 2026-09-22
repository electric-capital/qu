"""Persistent multiplexed WebSocket runtime.

This package houses the singleton in-process pub/sub bus, the per-conversation
replay ring buffer, the typed event envelopes published by REST handlers and
the model loop, and the persistent ``/app/api/stream`` WebSocket endpoint.

The bus + buffer are module-level singletons because the FastAPI deploy is
intentionally single-process (the Slack Socket Mode worker shares state with
the HTTP routes); see ``docs/architecture/slack-socket-mode.md`` for the
deployment constraint.
"""

from chat.realtime.bus import bus
from chat.realtime.replay_buffer import replay_buffer
from chat.realtime import events
from chat.realtime.socket import router as realtime_router

__all__ = [
    "bus",
    "replay_buffer",
    "events",
    "realtime_router",
]
