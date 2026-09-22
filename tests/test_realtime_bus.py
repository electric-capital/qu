"""Tests for the in-process pub/sub bus and replay ring buffer.

The bus is a module-level singleton, so tests instantiate fresh ``Bus``
objects locally rather than relying on the imported one. The replay buffer
behaviour is verified end-to-end against its public ``append`` / ``slice``
API.
"""

import time

import pytest

from chat.realtime.bus import Bus, SubscriberQueue
from chat.realtime.replay_buffer import ReplayBuffer


def test_subscribe_and_publish_to_user():
    bus = Bus()
    q = SubscriberQueue()
    bus.subscribe_user(42, q)

    bus.publish_to_user(42, {"type": "request_count_changed", "counts": {"open": 1}})

    assert q.qsize() == 1
    assert q.get_nowait() == {"type": "request_count_changed", "counts": {"open": 1}}


def test_publish_to_unsubscribed_user_drops():
    bus = Bus()
    bus.publish_to_user(1, {"type": "noop"})  # no subscribers; must not raise


def test_unsubscribe_removes_queue():
    bus = Bus()
    q = SubscriberQueue()
    bus.subscribe_user(1, q)
    bus.unsubscribe_user(1, q)

    bus.publish_to_user(1, {"type": "noop"})
    assert q.qsize() == 0


def test_subscribe_and_publish_to_conversation():
    bus = Bus()
    q1 = SubscriberQueue()
    q2 = SubscriberQueue()
    bus.subscribe_conversation("conv-1", q1)
    bus.subscribe_conversation("conv-1", q2)

    bus.publish_to_conversation("conv-1", {"type": "message_appended", "seq": 1})

    assert q1.qsize() == 1
    assert q2.qsize() == 1
    assert q1.get_nowait()["seq"] == 1


def test_user_overflow_marks_queue():
    bus = Bus()
    q = SubscriberQueue(maxsize=2)
    bus.subscribe_user(1, q)

    for i in range(5):
        bus.publish_to_user(1, {"type": "x", "i": i})

    assert q.overflowed is True


def test_conversation_overflow_marks_overflowed_conversations():
    bus = Bus()
    q = SubscriberQueue(maxsize=2)
    bus.subscribe_conversation("conv-1", q)

    for i in range(5):
        bus.publish_to_conversation("conv-1", {"type": "x", "i": i})

    assert q.overflowed is True
    assert "conv-1" in q.overflowed_conversations


def test_subscriber_count_helpers():
    bus = Bus()
    q = SubscriberQueue()
    bus.subscribe_user(1, q)
    assert bus.user_subscriber_count(1) == 1
    bus.unsubscribe_user(1, q)
    assert bus.user_subscriber_count(1) == 0


def test_replay_buffer_append_and_slice():
    buf = ReplayBuffer(maxlen=10)
    for i in range(5):
        buf.append("conv", i + 1, {"seq": i + 1})

    sliced = buf.slice("conv", 3)
    assert sliced is not None
    assert [s for (s, _msg) in sliced] == [4, 5]


def test_replay_buffer_slice_too_old_returns_none():
    buf = ReplayBuffer(maxlen=3)
    for i in range(10):
        buf.append("conv", i + 1, {"seq": i + 1})

    # Earliest in buffer is seq=8 (maxlen=3, kept last three: 8,9,10).
    # Asking for after_seq=2 means client missed seqs 3-7 entirely.
    sliced = buf.slice("conv", 2)
    assert sliced is None


def test_replay_buffer_slice_up_to_date_returns_empty():
    buf = ReplayBuffer(maxlen=10)
    buf.append("conv", 1, {"seq": 1})

    assert buf.slice("conv", 1) == []


def test_replay_buffer_slice_unknown_conversation_returns_empty():
    buf = ReplayBuffer(maxlen=10)
    assert buf.slice("never-seen", 0) == []


def test_replay_buffer_evict_idle_drops_when_no_subscribers():
    bus = Bus()
    buf = ReplayBuffer(maxlen=10)
    buf.append("conv-idle", 1, {"seq": 1})

    # Force the last-append timestamp into the past so the eviction sweep
    # treats this entry as stale.
    buf._last_append_at["conv-idle"] = 0.0  # very old monotonic time

    # Patch the module-level bus reference used by evict_idle so the
    # subscriber-count check sees zero.
    import sys
    rb_mod = sys.modules["chat.realtime.replay_buffer"]
    original_bus = rb_mod.bus  # noqa: F841 -- replaced for the test scope
    rb_mod.bus = bus
    try:
        evicted = buf.evict_idle(idle_seconds=1.0, now=time.monotonic())
    finally:
        rb_mod.bus = original_bus

    assert evicted == 1
    assert buf.latest_seq("conv-idle") is None


def test_replay_buffer_evict_idle_respects_active_subscribers():
    bus = Bus()
    buf = ReplayBuffer(maxlen=10)
    buf.append("conv-active", 1, {"seq": 1})
    buf._last_append_at["conv-active"] = 0.0  # mark as old

    # Subscriber present -> must NOT evict even if idle.
    q = SubscriberQueue()
    bus.subscribe_conversation("conv-active", q)

    import sys
    rb_mod = sys.modules["chat.realtime.replay_buffer"]
    original_bus = rb_mod.bus  # noqa: F841 -- replaced for the test scope
    rb_mod.bus = bus
    try:
        evicted = buf.evict_idle(idle_seconds=1.0, now=time.monotonic())
    finally:
        rb_mod.bus = original_bus

    assert evicted == 0
    assert buf.latest_seq("conv-active") == 1


def test_event_envelope_helpers_shape():
    from chat.realtime import events

    assert events.make_request_count_changed({"open": 3}) == {
        "type": "request_count_changed",
        "counts": {"open": 3},
    }
    assert events.make_conversation_list_changed("c1", "created") == {
        "type": "conversation_list_changed",
        "conversation_id": "c1",
        "action": "created",
    }
    env = events.make_wait_handle_resolved(
        conversation_id="c1",
        handle_id="h1",
        kind="action_request",
        status="accepted",
        request_id=99,
        response={"verdict": "executed"},
    )
    assert env["type"] == "wait_handle_resolved"
    assert env["request_id"] == 99
    assert env["response"] == {"verdict": "executed"}

    env_msg = events.make_message_appended("c1", 5)
    assert env_msg == {"type": "message_appended", "conversation_id": "c1", "seq": 5}
