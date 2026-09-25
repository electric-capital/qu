"""Tests for the ``run_active`` field on the persistent-WS ``subscribed`` ack.

The run-lifecycle envelopes (``resume_started`` / ``send_message_finished``)
are transient, so a client whose socket was down when one was published
never sees it. Every ``subscribed`` ack therefore carries the server's
current verdict on whether a run is streaming, and the client reconciles
its streaming/stop state against it.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from chat.realtime import socket as socket_mod
from chat.realtime.bus import Bus
from chat.realtime.replay_buffer import ReplayBuffer
from chat.wait_handles import resume as resume_mod


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.cookies: dict[str, str] = {}

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        pass


def _make_conn(monkeypatch: pytest.MonkeyPatch) -> tuple[socket_mod._Connection, _FakeWebSocket]:
    ws = _FakeWebSocket()
    conn = socket_mod._Connection(ws, {"id": 7, "email": "test@example.com"})
    monkeypatch.setattr(socket_mod, "bus", Bus(), raising=True)
    monkeypatch.setattr(socket_mod, "replay_buffer", ReplayBuffer(), raising=True)
    return conn, ws


def _patch_meta(monkeypatch: pytest.MonkeyPatch, *, last_seq: int = 0) -> None:
    async def _meta(_user_id: int, _conv_id: str) -> dict:
        return {"last_message_seq": last_seq}

    monkeypatch.setitem(
        sys.modules, "db.conversation_store",
        types.SimpleNamespace(get_conversation_meta=_meta),
    )


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    monkeypatch.setattr(socket_mod, "_active_send_runs", {}, raising=True)
    monkeypatch.setattr(resume_mod, "_active_resumes", {}, raising=True)
    monkeypatch.setattr(resume_mod, "_streaming_resumes", set(), raising=True)


def _subscribed_ack(ws: _FakeWebSocket) -> dict:
    acks = [m for m in ws.sent if m.get("type") == "subscribed"]
    assert len(acks) == 1, ws.sent
    return acks[0]


def test_up_to_date_ack_reports_idle(monkeypatch):
    _patch_meta(monkeypatch)
    conn, ws = _make_conn(monkeypatch)
    asyncio.run(socket_mod._handle_subscribe(conn, {"conversation_id": "conv-1", "last_seq": 0}))
    ack = _subscribed_ack(ws)
    assert ack["mode"] == "up_to_date"
    assert ack["run_active"] is False


def test_ack_reports_live_send_run(monkeypatch):
    """A registered, unfinished send task makes the ack say ``run_active``."""
    _patch_meta(monkeypatch)
    conn, ws = _make_conn(monkeypatch)

    async def scenario() -> dict:
        started = asyncio.Event()
        async def _run() -> None:
            await started.wait()
        task = asyncio.create_task(_run())
        socket_mod._active_send_runs["conv-1"] = task
        try:
            await socket_mod._handle_subscribe(conn, {"conversation_id": "conv-1", "last_seq": 0})
            return _subscribed_ack(ws)
        finally:
            started.set()
            await task

    ack = asyncio.run(scenario())
    assert ack["run_active"] is True


def test_finished_send_run_is_not_live(monkeypatch):
    """A task that already completed (even if its done-callback has not
    popped it from the registry yet) is never reported as live."""
    _patch_meta(monkeypatch)
    conn, ws = _make_conn(monkeypatch)

    async def scenario() -> dict:
        async def _run() -> None:
            return None
        task = asyncio.create_task(_run())
        await task
        socket_mod._active_send_runs["conv-1"] = task
        await socket_mod._handle_subscribe(conn, {"conversation_id": "conv-1", "last_seq": 0})
        return _subscribed_ack(ws)

    assert asyncio.run(scenario())["run_active"] is False


def test_resync_and_catchup_acks_carry_run_active(monkeypatch):
    """Every ack shape carries the field, not just ``up_to_date``."""
    _patch_meta(monkeypatch, last_seq=5)
    conn, ws = _make_conn(monkeypatch)

    # Buffer empty for the requested window -> resync.
    asyncio.run(socket_mod._handle_subscribe(conn, {"conversation_id": "conv-1", "last_seq": 2}))
    ack = _subscribed_ack(ws)
    assert ack["mode"] == "resync"
    assert ack["run_active"] is False
    ws.sent.clear()

    # Seed the replay buffer so the same window is a catchup.
    for seq in range(1, 6):
        socket_mod.replay_buffer.append("conv-1", seq, {"role": "assistant", "content": str(seq)})
    asyncio.run(socket_mod._handle_subscribe(conn, {"conversation_id": "conv-1", "last_seq": 2}))
    ack = _subscribed_ack(ws)
    assert ack["mode"] == "catchup"
    assert [m["seq"] for m in ack["messages"]] == [3, 4, 5]
    assert ack["run_active"] is False


def test_resume_counts_only_once_streaming():
    """A resume task that is still held (never published ``resume_started``)
    is not live; one that has started streaming is; a finished one is not."""

    async def scenario() -> list[bool]:
        verdicts: list[bool] = []
        gate = asyncio.Event()
        async def _resume() -> None:
            await gate.wait()
        task = asyncio.create_task(_resume())
        resume_mod._active_resumes["conv-1"] = task
        await asyncio.sleep(0)
        verdicts.append(socket_mod.is_run_active("conv-1"))  # held: not live

        resume_mod._streaming_resumes.add("conv-1")
        verdicts.append(socket_mod.is_run_active("conv-1"))  # streaming: live

        gate.set()
        await task
        verdicts.append(socket_mod.is_run_active("conv-1"))  # task done: not live
        return verdicts

    assert asyncio.run(scenario()) == [False, True, False]


def test_stale_streaming_marker_without_task_is_not_live():
    """The marker alone never reports a run: the task must still be live."""
    resume_mod._streaming_resumes.add("conv-1")
    assert socket_mod.is_run_active("conv-1") is False
