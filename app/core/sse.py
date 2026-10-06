"""Server-sent events for live tenant updates.

The pending-payments badge used to poll ``GET /payments/pending`` every ten
seconds from every device, forever, to render one number. SSE replaces that:
the payment write path publishes a one-line notification and connected clients
refetch what they care about.

Two things make the shape of this module what it is.

**Redis, not the message bus.** The event consumer that handles
``payment.succeeded`` runs as a separate process (see ``main.lifespan``), so a
handler there cannot reach the SSE subscribers held by *this* process. Redis
pub/sub is the only hop every process already shares, so it carries a
notification and nothing more — the client still refetches, so there is no
second source of truth to keep consistent with the database.

**A notification, not the payload.** Frames carry an event name and an id. If a
frame carried the payment data, every consumer would need to reconcile it
against the row anyway, and a missed frame would leave the UI showing data the
database has already superseded. Refetching on a nudge cannot drift.

Every connection is tenant-scoped and the channel name carries the tenant, so a
publish can only ever reach subscribers of that one tenant.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

logger = logging.getLogger("storeflow.sse")

# Frames a client missed are dropped rather than buffered. A client that was
# offline refetches on reconnect anyway, so a deep queue buys nothing and costs
# memory on a connection nobody is reading.
QUEUE_MAXSIZE = 32

# Comment frame on an idle connection. Proxies in front of the app commonly drop
# a connection after 30-60s of silence; a comment keeps the pipe warm without
# looking like an event to the client.
HEARTBEAT_SECONDS = 15

# Reconnect hint sent to the browser/EventSource-style clients.
RECONNECT_MS = 3000


def _channel(tenant_id: str) -> str:
    return f"sse:{tenant_id}"


class Subscriber:
    """One connected client."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.dropped = 0

    def offer(self, frame: str) -> None:
        """Hand a frame to this client, dropping the oldest if it is behind."""
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(frame)
            self.dropped += 1


class Broker:
    """In-process fan-out, fed by Redis so other instances reach us too."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._pump: asyncio.Task | None = None
        self._redis = None

    # ── subscription lifecycle ────────────────────────────────────────────

    def subscribe(self, tenant_id: str) -> Subscriber:
        subscriber = Subscriber(tenant_id)
        peers = self._subscribers.setdefault(tenant_id, set())
        peers.add(subscriber)
        if len(peers) == 1:
            # First client for this tenant on this instance: make sure we are
            # listening for its channel.
            self._ensure_pump()
        logger.info(
            "SSE subscribe tenant=%s local_subscribers=%d",
            tenant_id,
            len(self._subscribers[tenant_id]),
        )
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        peers = self._subscribers.get(subscriber.tenant_id)
        if not peers:
            return
        peers.discard(subscriber)
        if not peers:
            self._subscribers.pop(subscriber.tenant_id, None)
        logger.info(
            "SSE unsubscribe tenant=%s local_subscribers=%d dropped_frames=%d",
            subscriber.tenant_id,
            len(self._subscribers.get(subscriber.tenant_id, ())),
            subscriber.dropped,
        )

    def local_subscriber_count(self) -> int:
        return sum(len(s) for s in self._subscribers.values())

    # ── outbound ──────────────────────────────────────────────────────────

    async def publish(self, tenant_id: str, event: str, data: dict | None = None) -> None:
        """Publish a notification. Safe to call when Redis is unavailable."""
        payload = json.dumps(data or {})
        try:
            client = await self._client()
            if client is None:
                return
            await client.publish(_channel(tenant_id), f"{event}\n{payload}")
        except Exception as exc:  # pragma: no cover - depends on Redis
            # A failed notification must never fail the payment that triggered
            # it. The client falls back to its safety poll.
            logger.warning("SSE publish failed tenant=%s: %s", tenant_id, exc)

    # ── inbound ───────────────────────────────────────────────────────────

    async def _client(self):
        if self._redis is None:
            from app.core.redis_client import get_cache_redis

            client = await get_cache_redis()
            if client is None:
                return None
            self._redis = client
        return self._redis

    def _ensure_pump(self) -> None:
        """Start the Redis listener if it is not already running.

        Checks for a running loop before building the coroutine: creating it
        first and discovering there is no loop leaves an un-awaited coroutine
        behind, and the task is held in ``self._pump`` so it cannot be collected
        early the way a bare create_task can.
        """
        if self._pump is not None and not self._pump.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called outside a request (a sync test, a worker). The next
            # subscribe starts it.
            return
        self._pump = loop.create_task(self._run_pump())

    async def _run_pump(self) -> None:
        """Listen on every channel with at least one local subscriber."""
        while self._subscribers:
            client = await self._client()
            if client is None:
                await asyncio.sleep(5)
                continue
            wanted = {f"sse:{t}" for t in self._subscribers}
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.subscribe(*sorted(wanted))
                logger.info("SSE pump listening on %d channel(s)", len(wanted))
                while self._subscribers:
                    new_channels = {
                        f"sse:{t}" for t in self._subscribers
                    } - wanted
                    if new_channels:
                        await pubsub.subscribe(*sorted(new_channels))
                        wanted |= new_channels
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message and message.get("type") == "message":
                        self._dispatch(message["channel"], message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depends on Redis
                logger.warning("SSE pump error, restarting: %s", exc)
                await asyncio.sleep(2)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    def _dispatch(self, channel: str, raw: str) -> None:
        tenant_id = channel.removeprefix("sse:")
        peers = self._subscribers.get(tenant_id)
        if not peers:
            return
        event, _, body = str(raw).partition("\n")
        frame = _frame(event, body)
        for subscriber in list(peers):
            subscriber.offer(frame)

    async def shutdown(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        self._subscribers.clear()


def _frame(event: str, body: str, event_id: str | None = None) -> str:
    """Render one SSE frame.

    The data is emitted as a single line: a bare newline would terminate the
    frame early and hand the client half a payload.
    """
    lines = [f"event: {event}"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {body}")
    return "\n".join(lines) + "\n\n"


async def stream(broker: Broker, subscriber: Subscriber) -> AsyncIterator[str]:
    """Yield frames for one connection until the client goes away.

    Yields a heartbeat whenever the queue stays empty for
    ``HEARTBEAT_SECONDS``, which is what keeps intermediaries from reaping an
    idle connection.
    """
    try:
        # Retry hint first so a client that reconnects in a tight loop backs off.
        yield f"retry: {RECONNECT_MS}\n\n"
        yield ": connected\n\n"
        while True:
            try:
                frame = await asyncio.wait_for(
                    subscriber.queue.get(), timeout=HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield frame
    finally:
        broker.unsubscribe(subscriber)


broker = Broker()


def tenant_id_of(business_id: str) -> str:
    """Normalise the tenant used as the channel and scope key."""
    with contextlib.suppress(ValueError):
        return str(UUID(business_id))
    return business_id