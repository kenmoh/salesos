import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractExchange

from events.envelope import EventEnvelope

logger = logging.getLogger("storeflow.messagebus")

EXCHANGE_NAME = "storeflow.events"
EXCHANGE_TYPE = "topic"


class MessageBusError(Exception):
    pass


@dataclass
class EventPublisher:
    """Publishes EventEnvelope messages to the RabbitMQ topic exchange."""

    connection_url: str
    _connection: AbstractConnection | None = field(default=None, init=False, repr=False)
    _channel: AbstractChannel | None = field(default=None, init=False, repr=False)
    _exchange: AbstractExchange | None = field(default=None, init=False, repr=False)

    async def connect(self) -> None:
        try:
            self._connection = await aio_pika.connect_robust(self.connection_url)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                EXCHANGE_NAME, EXCHANGE_TYPE, durable=True
            )
            logger.info("Connected to RabbitMQ exchange '%s'", EXCHANGE_NAME)
        except Exception as exc:
            raise MessageBusError(f"Failed to connect to RabbitMQ: {exc}") from exc

    async def publish(self, event: EventEnvelope, routing_key: str | None = None) -> None:
        if not self._exchange:
            raise MessageBusError("Publisher not connected. Call connect() first.")

        key = routing_key or event.routing_key()
        body = json.dumps(event.model_dump(mode="json"), default=str).encode("utf-8")

        try:
            await self._exchange.publish(
                aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    headers={
                        "event_type": event.event_type,
                        "event_version": str(event.event_version),
                        "tenant_id": str(event.tenant_id) if event.tenant_id else "",
                        "correlation_id": event.correlation_id or "",
                    },
                ),
                routing_key=key,
            )
            logger.debug("Published event %s to %s", event.event_type, key)
        except Exception as exc:
            raise MessageBusError(f"Failed to publish event {event.event_type}: {exc}") from exc

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            logger.info("RabbitMQ connection closed")


@asynccontextmanager
async def create_publisher(connection_url: str) -> AsyncIterator[EventPublisher]:
    publisher = EventPublisher(connection_url=connection_url)
    try:
        await publisher.connect()
        yield publisher
    finally:
        await publisher.close()
