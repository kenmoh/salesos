import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.events.envelope import EventEnvelope
from app.common.events.outbox import OutboxEvent
from app.common.messagebus.publisher import EventPublisher

logger = logging.getLogger("storeflow.outbox_relay")


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
