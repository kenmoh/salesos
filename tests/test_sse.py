"""The SSE broker: scoping, back-pressure, and the heartbeat.

These are the properties that decide whether the stream is safe to rely on: a
frame must never reach another tenant, a client that stops reading must not grow
the server's memory, and an idle connection must stay warm without looking like
an event.
"""

import asyncio
import io
import json
import re

import pytest

from app.core.sse import (
    HEARTBEAT_SECONDS,
    Broker,
    _frame,
    stream,
    tenant_id_of,
)


def data_of(frame: str) -> dict:
    """Pull the payload out of one SSE frame."""
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"no data line in frame: {frame!r}")


class TestTenantScoping:
    def test_frames_only_reach_their_own_tenant(self):
        broker = Broker()
        mine = broker.subscribe("tenant-a")
        theirs = broker.subscribe("tenant-b")

        broker._dispatch("sse:tenant-a", "payment.updated\n{\"payment_id\":\"p1\"}")

        assert mine.queue.qsize() == 1
        assert theirs.queue.qsize() == 0

    async def test_publish_routes_through_redis_to_the_right_tenant(self):
        broker = Broker()
        mine = broker.subscribe("tenant-a")
        theirs = broker.subscribe("tenant-b")

        class _PubSub:
            def __init__(self):
                self.published = []
                self.sent = 0

            async def subscribe(self, *channels):
                self.channels = channels

            async def get_message(self, **kwargs):
                # Deliver once, then idle like a real subscription would.
                if self.sent:
                    await asyncio.sleep(0.01)
                    return None
                self.sent += 1
                return {
                    "type": "message",
                    "channel": "sse:tenant-a",
                    "data": "payment.updated\n{}",
                }

            async def aclose(self):
                return None

        class _Client:
            def __init__(self):
                self.pubsub_instance = _PubSub()
                self.published = []

            def pubsub(self, **kwargs):
                return self.pubsub_instance

            async def publish(self, channel, message):
                self.published.append((channel, message))

        broker._redis = _Client()
        broker._subscribers = {"tenant-a": {mine}, "tenant-b": {theirs}}
        pump = asyncio.create_task(broker._run_pump())
        await asyncio.sleep(0.05)
        await broker.publish("tenant-a", "payment.updated", {"payment_id": "p1"})
        pump.cancel()

        # Went out on the tenant's own channel...
        assert broker._redis.published[0][0] == "sse:tenant-a"
        # ...and the pump that received it landed on the right subscriber only.
        assert mine.queue.qsize() == 1
        assert theirs.queue.qsize() == 0

    async def test_publish_failure_is_swallowed(self):
        """A notification problem must never surface as a payment failure."""
        broker = Broker()

        class _Broken:
            async def publish(self, *args, **kwargs):
                raise RuntimeError("redis down")

        broker._redis = _Broken()
        await broker.publish("tenant-a", "payment.updated", {})  # must not raise

    def test_tenant_id_is_normalised(self):
        assert tenant_id_of("4080906c-b171-4ae4-983d-d6e5e90fbda3") == (
            "4080906c-b171-4ae4-983d-d6e5e90fbda3"
        )
        # A malformed id still yields a usable channel key rather than raising.
        assert tenant_id_of("not-a-uuid") == "not-a-uuid"


class TestBackPressure:
    async def test_a_client_that_stops_reading_keeps_the_newest_frames(self):
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")

        for i in range(200):
            broker._dispatch("sse:tenant-a", f'payment.updated\n{{"n":{i}}}')

        # Bounded rather than growing with the backlog, and the frames it does
        # keep are the most recent: a client that was behind still ends up
        # current after it refetches.
        assert subscriber.queue.qsize() <= 32
        kept = [data_of(subscriber.queue.get_nowait()) for _ in range(subscriber.queue.qsize())]
        assert kept[-1] == {"n": 199}
        assert kept[0] == {"n": 168}
        assert subscriber.dropped > 0

    async def test_unsubscribe_removes_the_client(self):
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")
        assert broker.local_subscriber_count() == 1

        broker.unsubscribe(subscriber)

        assert broker.local_subscriber_count() == 0
        # A publish after the fact must not resurrect it or raise.
        broker._dispatch("sse:tenant-a", "payment.updated\n{}")
        assert subscriber.queue.qsize() == 0


class TestFraming:
    def test_frame_is_a_single_line(self):
        frame = _frame("payment.updated", json.dumps({"a": 1, "b": 2}))
        assert frame.startswith("event: payment.updated\n")
        # A bare newline in the data would terminate the frame early and hand
        # the client half a payload.
        assert frame.count("\n\n") == 1
        assert data_of(frame) == {"a": 1, "b": 2}

    def test_payload_with_embedded_newlines_stays_one_frame(self):
        frame = _frame("payment.updated", json.dumps({"note": "line one\nline two"}))
        # json.dumps escapes the newline, so the frame is still intact.
        assert frame.count("\n\n") == 1
        assert data_of(frame)["note"] == "line one\nline two"


class TestStream:
    async def test_greets_with_a_retry_hint_then_connects(self):
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")
        iterator = stream(broker, subscriber).__aiter__()

        assert await asyncio.wait_for(anext(iterator), timeout=1) == "retry: 3000\n\n"
        assert await asyncio.wait_for(anext(iterator), timeout=1) == ": connected\n\n"

    async def test_delivers_a_queued_frame(self):
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")
        broker._dispatch("sse:tenant-a", "payment.updated\n{}")
        iterator = stream(broker, subscriber).__aiter__()
        await asyncio.wait_for(anext(iterator), timeout=1)
        await asyncio.wait_for(anext(iterator), timeout=1)

        frame = await asyncio.wait_for(anext(iterator), timeout=1)

        assert frame.startswith("event: payment.updated")

    async def test_idle_connection_is_kept_warm_by_a_comment(self, monkeypatch):
        """A silent connection looks dead to a proxy; a comment keeps it open
        without the client treating it as an event."""
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")
        monkeypatch.setattr("app.core.sse.HEARTBEAT_SECONDS", 0.01)
        iterator = stream(broker, subscriber).__aiter__()
        await asyncio.wait_for(anext(iterator), timeout=1)
        await asyncio.wait_for(anext(iterator), timeout=1)

        frame = await asyncio.wait_for(anext(iterator), timeout=1)

        assert frame == ": ping\n\n"
        assert HEARTBEAT_SECONDS == 15

    async def test_closing_the_stream_unsubscribes(self):
        broker = Broker()
        subscriber = broker.subscribe("tenant-a")
        iterator = stream(broker, subscriber).__aiter__()
        await asyncio.wait_for(anext(iterator), timeout=1)

        await iterator.aclose()

        assert broker.local_subscriber_count() == 0


class TestTimeoutMiddlewareLeavesStreamsAlone:
    def test_stream_paths_are_exempt(self):
        from app.middleware.timeout import STREAM_PATHS, TimeoutMiddleware

        assert "/api/v1/events/stream" in STREAM_PATHS
        # Everything else keeps the cap; a payment must never be exempt.
        assert "/api/v1/payments/confirm" not in STREAM_PATHS
        assert TimeoutMiddleware.__init__.__defaults__ is not None


class TestRelayNotification:
    def test_only_payment_events_wake_a_client(self):
        from app.common.messagebus.outbox_relay import SSE_EVENTS
        from app.common.events.names import (
            PAYMENT_FAILED,
            PAYMENT_INTENT_CREATED,
            PAYMENT_SUCCEEDED,
        )

        assert SSE_EVENTS[PAYMENT_INTENT_CREATED] == "payment.pending"
        assert SSE_EVENTS[PAYMENT_SUCCEEDED] == "payment.updated"
        assert SSE_EVENTS[PAYMENT_FAILED] == "payment.updated"
        # A sale or document event must not drag every payment client awake.
        assert len(SSE_EVENTS) == 3

    async def test_a_non_payment_event_is_not_notified(self):
        from app.common.events.envelope import EventEnvelope
        from app.common.messagebus.outbox_relay import OutboxRelay

        relay = OutboxRelay(publisher=None, database_url="")
        called = False

        async def _boom(*args, **kwargs):
            nonlocal called
            called = True

        import app.core.sse as sse_module

        original = sse_module.broker.publish
        sse_module.broker.publish = _boom
        try:
            await relay._notify_subscribers(
                EventEnvelope(
                    event_type="sale.completed",
                    tenant_id="11111111-1111-1111-1111-111111111111",
                    actor_id=None,
                    correlation_id=None,
                    payload={},
                )
            )
        finally:
            sse_module.broker.publish = original

        assert called is False

class TestStreamEndpointIsReachableByTheRolesThatNeedIt:
    """A cashier holds pending payments, so the stream must not be gated behind
    a permission a cashier lacks.

    Gating it on accounting:read refused the stream to exactly the role that
    needed it, and the client treats a 403 as terminal, so those users would have
    silently dropped back to polling.
    """

    def _seeded_roles(self) -> dict[str, list[str]]:
        """The seeded role definitions.

        They are built inside the tenant-provisioning function, so the source is
        the only place to read them from.
        """
        source = io.open("app/common/bridge.py", encoding="utf-8").read()
        block = source[source.index("ROLE_PERMISSIONS_MAP") :]
        return {
            role: re.findall(r'"([^"]+)"', body)
            for role, body in re.findall(r'"(\w+)": \[(.*?)\]', block, re.S)
        }

    def _gate_permission(self) -> str:
        source = io.open("app/events/routes.py", encoding="utf-8").read()
        match = re.search(r'require_permission\("([^"]+)"\)', source)
        assert match, "the stream endpoint has no permission gate"
        return match.group(1)

    def test_gate_is_payments_read(self):
        assert self._gate_permission() == "payments:read"

    def test_every_role_that_can_pay_can_also_stream(self):
        gate = self._gate_permission()
        for role, perms in self._seeded_roles().items():
            if "payments:create" in perms:
                assert gate in perms, f"{role} can start a payment but cannot open the stream"
