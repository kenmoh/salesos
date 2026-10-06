import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.events.envelope import EventEnvelope
from app.common.events.names import (
    PAYMENT_FAILED,
    PAYMENT_INTENT_CREATED,
    PAYMENT_SUCCEEDED,
)
from app.common.events.outbox import OutboxEvent
from app.common.messagebus.publisher import EventPublisher

logger = logging.getLogger("storeflow.outbox_relay")

# Events worth waking a connected client for, mapped to the SSE event name.
#
# The relay is the one place every payment state change already passes through:
# the API routes, the gateway webhook and any Celery task all write an outbox
# row, so notifying here catches all of them. Publishing from the handlers
# instead would mean remembering every site that settles a payment, and the one
# most worth catching is the webhook, which is exactly the path a handler-side
# hook gets forgotten on.
#
# ``payment.pending`` fires when an intent is created and ``payment.updated``
# when it settles either way; the client refetches, so no state rides along.
SSE_EVENTS: dict[str, str] = {
    PAYMENT_INTENT_CREATED: "payment.pending",
    PAYMENT_SUCCEEDED: "payment.updated",
    PAYMENT_FAILED: "payment.updated",
}


class OutboxRelay:
    """Polls the outbox_events table and publishes pending events to RabbitMQ.

    Connects to the single outbox table, claims pending rows with
    FOR UPDATE SKIP LOCKED, publishes them, and marks them as published.
    """

    def __init__(
        self,
        publisher: EventPublisher,
        database_url: str,
        *,
        poll_interval: float = 5.0,
        batch_size: int = 50,
    ):
        self.publisher = publisher
        self.database_url = database_url
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._running = False

    async def start(self) -> None:
        self._running = True
        engine = create_async_engine(self.database_url, pool_pre_ping=True, echo=False)
        session_factory = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info(
            "OutboxRelay started: polling every %.1fs (batch_size=%d)",
            self.poll_interval,
            self.batch_size,
        )
        try:
            while self._running:
                try:
                    await self._poll(session_factory)
                except Exception as exc:
                    logger.error("Error polling outbox: %s", exc)
                await asyncio.sleep(self.poll_interval)
        finally:
            await engine.dispose()

    async def stop(self) -> None:
        self._running = False
        logger.info("OutboxRelay stopped")

    async def _notify_subscribers(self, envelope: EventEnvelope) -> None:
        """Wake SSE clients so they refetch.

        Best effort by design: a client that misses a frame falls back to its
        safety poll, so a Redis problem must never stop an event reaching
        RabbitMQ and its handlers.
        """
        event = SSE_EVENTS.get(envelope.event_type)
        if event is None:
            return
        try:
            from app.core.sse import broker

            payload = envelope.payload or {}
            await broker.publish(
                str(envelope.tenant_id),
                event,
                {
                    "payment_id": str(payload.get("payment_id", "")),
                    "sale_id": str(payload.get("sale_id", "")),
                },
            )
        except Exception as exc:
            logger.warning("SSE notify failed for %s: %s", envelope.event_type, exc)

    async def _poll(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == "pending",
                        OutboxEvent.available_at <= datetime.now(UTC),
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
                events: list[OutboxEvent] = list(result.scalars().all())

                for outbox_event in events:
                    try:
                        envelope = EventEnvelope(
                            event_id=outbox_event.id,
                            event_type=outbox_event.event_type,
                            tenant_id=outbox_event.tenant_id,
                            payload=outbox_event.payload,
                            correlation_id=outbox_event.headers.get("correlation_id"),
                            causation_id=(
                                UUID(outbox_event.headers["causation_id"])
                                if outbox_event.headers.get("causation_id")
                                else None
                            ),
                        )

                        await self.publisher.publish(envelope)
                        await self._notify_subscribers(envelope)
                        await session.execute(
                            update(OutboxEvent)
                            .where(OutboxEvent.id == outbox_event.id)
                            .values(
                                status="published",
                                published_at=datetime.now(UTC),
                            )
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to publish outbox event %s (%s): %s",
                            outbox_event.id,
                            outbox_event.event_type,
                            exc,
                        )
                        await session.execute(
                            update(OutboxEvent)
                            .where(OutboxEvent.id == outbox_event.id)
                            .values(
                                attempts=OutboxEvent.attempts + 1,
                                last_error=str(exc),
                            )
                        )

                if events:
                    logger.info("Published %d outbox events", len(events))
