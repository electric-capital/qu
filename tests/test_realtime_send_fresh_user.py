"""Tests for the per-turn fresh user re-load on the persistent WS send path.

``_handle_send_message`` re-reads the user row from the DB at the start of
every turn (via ``db.user_store.get_user_by_id``) and threads *that* fresh
dict into the model run, rather than the connect-time ``conn.user`` snapshot.
This is what keeps credential/connection-gated decisions (system:* skill
gates, connected-services enumeration, authed_get token loaders) correct when
a service is connected/disconnected mid-session without a WS reconnect.

These tests build a ``socket_mod._Connection`` around a ``_FakeWebSocket``
(mirroring ``tests/test_realtime_subscriptions.py``), stub the conversation
ownership/storage helpers so the handler reaches the task-creation point, and
capture the ``user=`` kwarg passed into ``_run_send_message``.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from api.instructions import get_user_connected_services
from chat.realtime import socket as socket_mod


class _FakeWebSocket:
    """Minimal stand-in for the FastAPI ``WebSocket`` used by socket.py."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: bool = False
        self.cookies: dict[str, str] = {}
        # _run_send_message reads conn.websocket.app; never invoked here
        # because we stub _run_send_message, but create_task still needs the
        # coroutine to be constructible, so a plain attribute suffices.
        self.app = object()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def _make_conn() -> tuple[socket_mod._Connection, _FakeWebSocket]:
    """Build a ``_Connection`` whose cached ``conn.user`` lacks a github credential row."""
    ws = _FakeWebSocket()
    # Stale snapshot: GitHub NOT connected at WS connect time.
    user = {"id": 7, "email": "test@example.com"}
    conn = socket_mod._Connection(ws, user)
    return conn, ws


def _patch_send_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fresh_user,
    captured: dict,
) -> None:
    """Stub everything between the entry of ``_handle_send_message`` and the
    ``_run_send_message`` task creation so the handler runs end-to-end.
    """

    # Fresh DB read -> the updated user row (GitHub now connected).
    async def _get_user_by_id(_user_id: int):
        return fresh_user

    monkeypatch.setattr(socket_mod, "get_user_by_id", _get_user_by_id, raising=True)

    # Conversation ownership / metadata: brand-new conversation.
    async def _meta(_user_id: int, _conv_id: str) -> dict:
        return {"last_message_seq": 0, "project_id": None, "routine_id": None}

    fake_conv_store = types.SimpleNamespace(
        get_conversation_meta=_meta,
        set_conversation_flags=None,
    )
    monkeypatch.setitem(sys.modules, "db.conversation_store", fake_conv_store)

    # Persist the user message: no-op, but honor the ``(seq, row)`` return
    # shape the handler unpacks for the ``send_message_accepted`` receipt.
    async def _append_message(**_kwargs) -> tuple[int, dict]:
        return 1, {}

    monkeypatch.setattr(
        socket_mod.ChatStorage, "append_message",
        staticmethod(_append_message), raising=True,
    )

    # Capture the user= kwarg threaded into the run and avoid actually
    # driving a model run.
    async def _fake_run_send_message(*, user, **_kwargs) -> None:
        captured["user"] = user

    monkeypatch.setattr(
        socket_mod, "_run_send_message", _fake_run_send_message, raising=True,
    )


def _run(coro):
    return asyncio.run(coro)


async def _drive_send(conn) -> None:
    """Run the handler and await the spawned send task so the captured
    kwarg is populated before assertions.
    """
    await socket_mod._handle_send_message(conn, {
        "conversation_id": "conv-1",
        "message": "load system:github please",
    })
    task = socket_mod._active_send_runs.get("conv-1")
    if task is not None:
        await task


def test_send_reloads_fresh_user_reaching_the_gate(monkeypatch, github_plugin):
    """The user dict threaded into the run is the fresh DB read (GitHub
    connected), and that fresh dict is what the connected-services gate
    sees -- not the stale ``conn.user`` snapshot.
    """
    # The combined gate is server-configured AND user-connected; this test
    # is about the user side, so treat the plugin's server config as set.
    monkeypatch.setattr(
        "config.plugins.plugin_server_available", lambda plugin: True,
    )

    conn, _ws = _make_conn()
    # Sanity: the stale snapshot says GitHub is NOT connected. (The github
    # key exists only while the plugin is registered -- hence the fixture.)
    assert get_user_connected_services(conn.user)["github"] is False
    stale_user = conn.user

    fresh_user = {
        "id": 7,
        "email": "test@example.com",
        "service_credentials": {
            "github": {
                "service": "github",
                "secret": None,
                "oauth_blob": {"access_token": "gho_fresh"},
            },
        },
    }
    captured: dict = {}
    _patch_send_path(monkeypatch, fresh_user=fresh_user, captured=captured)

    _run(_drive_send(conn))

    # The fresh dict reached the run...
    assert "user" in captured, "_run_send_message was never invoked"
    assert "service_credentials" in captured["user"]
    # ...and biting at the actual gate, the connected-services map is True.
    assert get_user_connected_services(captured["user"])["github"] is True
    # The stale snapshot was NOT what got passed through (regression guard
    # against re-introducing user=conn.user at this call site).
    assert captured["user"] is not stale_user
    assert captured["user"] is fresh_user
    # conn.user is refreshed so future snapshot consumers benefit too.
    assert conn.user is fresh_user


def test_send_rejected_when_user_row_missing(monkeypatch):
    """If the fresh read returns None (user deleted), the send is rejected
    with ``user_not_found`` and no run is spawned.
    """
    conn, ws = _make_conn()
    captured: dict = {}
    _patch_send_path(monkeypatch, fresh_user=None, captured=captured)

    _run(_drive_send(conn))

    assert "user" not in captured, "run should not be spawned for a missing user"
    assert any(
        msg.get("type") == "send_message_rejected"
        and msg.get("reason") == "user_not_found"
        and msg.get("conversation_id") == "conv-1"
        for msg in ws.sent
    )
    # No send task was registered.
    assert socket_mod._active_send_runs.get("conv-1") is None


def test_send_falls_back_to_cached_user_on_db_error(monkeypatch):
    """A transient DB error during the fresh read falls back to the cached
    ``conn.user`` snapshot (matching prior behavior) rather than dropping
    the user's send.
    """
    conn, _ws = _make_conn()
    stale_user = conn.user
    captured: dict = {}
    _patch_send_path(monkeypatch, fresh_user=None, captured=captured)

    async def _boom(_user_id: int):
        raise RuntimeError("db down")

    monkeypatch.setattr(socket_mod, "get_user_by_id", _boom, raising=True)

    _run(_drive_send(conn))

    assert captured.get("user") is stale_user
