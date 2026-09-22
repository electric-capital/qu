"""Tests for the ``send_message_accepted`` delivery receipt on the persistent
WS send path.

Once ``_handle_send_message`` has appended the user row to disk it sends the
SENDING connection a direct receipt carrying the row's ``seq`` and echoing
the optional ``client_send_id`` from the request envelope. The frontend arms
a timer on every send and flips the optimistic bubble into a failed state
(Retry / Discard) when no receipt arrives -- the only way to tell a frame
that never left a half-open mobile socket apart from a slow model run.

Same harness as ``tests/test_realtime_send_fresh_user.py``: a
``_Connection`` around a fake WebSocket, storage helpers stubbed so the
handler reaches the task-creation point, ``_run_send_message`` stubbed.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from chat.realtime import socket as socket_mod


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: bool = False
        self.cookies: dict[str, str] = {}
        self.app = object()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def _make_conn() -> tuple[socket_mod._Connection, _FakeWebSocket]:
    ws = _FakeWebSocket()
    conn = socket_mod._Connection(ws, {"id": 7, "email": "test@example.com"})
    return conn, ws


def _patch_send_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    append_seq: int,
    calls: list[str],
    append_raises: bool = False,
) -> None:
    async def _get_user_by_id(_user_id: int):
        return {"id": 7, "email": "test@example.com"}

    monkeypatch.setattr(socket_mod, "get_user_by_id", _get_user_by_id, raising=True)

    async def _meta(_user_id: int, _conv_id: str) -> dict:
        return {"last_message_seq": 0, "project_id": None, "routine_id": None}

    monkeypatch.setitem(
        sys.modules, "db.conversation_store",
        types.SimpleNamespace(get_conversation_meta=_meta, set_conversation_flags=None),
    )

    async def _append_message(**_kwargs) -> tuple[int, dict]:
        calls.append("append")
        if append_raises:
            raise RuntimeError("disk full")
        return append_seq, {"role": "user", "seq": append_seq}

    monkeypatch.setattr(
        socket_mod.ChatStorage, "append_message",
        staticmethod(_append_message), raising=True,
    )

    async def _fake_run_send_message(**_kwargs) -> None:
        calls.append("run")

    monkeypatch.setattr(
        socket_mod, "_run_send_message", _fake_run_send_message, raising=True,
    )


async def _drive_send(conn, envelope: dict) -> None:
    await socket_mod._handle_send_message(conn, envelope)
    task = socket_mod._active_send_runs.get(envelope["conversation_id"])
    if task is not None:
        await task


def _accepted(ws: _FakeWebSocket) -> list[dict]:
    return [m for m in ws.sent if m.get("type") == "send_message_accepted"]


def test_receipt_carries_seq_and_echoes_client_send_id(monkeypatch):
    conn, ws = _make_conn()
    calls: list[str] = []
    _patch_send_path(monkeypatch, append_seq=42, calls=calls)

    asyncio.run(_drive_send(conn, {
        "conversation_id": "conv-1",
        "message": "hello",
        "client_send_id": "abc-123",
    }))

    receipts = _accepted(ws)
    assert receipts == [{
        "type": "send_message_accepted",
        "conversation_id": "conv-1",
        "seq": 42,
        "client_send_id": "abc-123",
    }]
    # The receipt is sent after the append and before the run task runs,
    # so the sending tab learns the row exists even if the run then fails.
    assert calls == ["append", "run"]


def test_receipt_omits_client_send_id_when_absent_or_malformed(monkeypatch):
    for envelope_extra in ({}, {"client_send_id": ""}, {"client_send_id": 7}):
        conn, ws = _make_conn()
        calls: list[str] = []
        _patch_send_path(monkeypatch, append_seq=3, calls=calls)

        asyncio.run(_drive_send(conn, {
            "conversation_id": "conv-1",
            "message": "hello",
            **envelope_extra,
        }))

        receipts = _accepted(ws)
        assert len(receipts) == 1, envelope_extra
        assert receipts[0]["seq"] == 3
        assert "client_send_id" not in receipts[0], envelope_extra


def test_no_receipt_when_append_fails(monkeypatch):
    """An append failure surfaces as the existing ``append_failed`` error
    envelope and never as a receipt -- a receipt must mean the row is on disk.
    """
    conn, ws = _make_conn()
    calls: list[str] = []
    _patch_send_path(monkeypatch, append_seq=1, calls=calls, append_raises=True)

    asyncio.run(_drive_send(conn, {
        "conversation_id": "conv-1",
        "message": "hello",
        "client_send_id": "abc-123",
    }))

    assert _accepted(ws) == []
    assert any(
        m.get("type") == "error" and m.get("error") == "append_failed"
        for m in ws.sent
    )
    assert calls == ["append"]
