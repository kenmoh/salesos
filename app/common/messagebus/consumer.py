import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from events.envelope import EventEnvelope
from events.inbox import InboxEvent
from messagebus.publisher import EXCHANGE_NAME, EXCHANGE_TYPE

logger = logging.getLogger("storeflow.messagebus.consumer")

EventHandler = Callable[[EventEnvelope, Any], Coroutine[Any, Any, None]]


class EventConsumer:
    """Consumes events from a RabbitMQ topic queue with idempotent inbox processing."""

    def __init__(
        self,
        connection_url: str,
        service_name: str,
        database_session_factory: Any,
        *,
        schema: str | None = None,
        queue_name: str | None = None,
        routing_keys: list[str] | None = None,
        prefetch_count: int = 10,
    ):
        self.connection_url = connection_url
        self.service_name = service_name
        self.database_session_factory = database_session_factory
        self.schema = schema or service_name
        self.queue_name = queue_name or f"{service_name}.events"
        self.routing_keys = routing_keys or ["#"]
        self.prefetch_count = prefetch_count
        self._handlers: dict[str, EventHandler] = {}
        self._running = False

    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type] = handler

    async def start(self) -> None:
        self._running = True
        connection = await aio_pika.connect_robust(self.connection_url)
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=self.prefetch_count)
            exchange = await channel.declare_exchange(EXCHANGE_NAME, EXCHANGE_TYPE, durable=True)
            queue = await channel.declare_queue(self.queue_name, durable=True, auto_delete=False)
            for key in self.routing_keys:
                await queue.bind(exchange, routing_key=key)
            logger.info(
                "Consumer '%s' listening on %s with keys %s",
                self.service_name,
                self.queue_name,
                self.routing_keys,
            )
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process(ignore_processed=True):
                        await self._handle_message(message)

    async def stop(self) -> None:
        self._running = False

    async def _handle_message(self, message: AbstractIncomingMessage) -> None:
        try:
            body = json.loads(message.body.decode("utf-8"))
            envelope = EventEnvelope(**body)
        except Exception as exc:
            logger.error("Failed to parse event message: %s", exc)
            return

        async with self.database_session_factory() as session:
            async with session.begin():
                already_processed = await session.get(InboxEvent, envelope.event_id)
                if already_processed:
                    logger.debug("Skipping duplicate event %s", envelope.event_id)
                    return

                handler = self._handlers.get(envelope.event_type)
                if handler is None:
                    logger.debug("No handler for event type %s", envelope.event_type)
                    inbox = InboxEvent(
                        event_id=envelope.event_id,
                        event_type=envelope.event_type,
                    )
                    session.add(inbox)
                    await session.commit()
                    return

                try:
                    await handler(envelope, session)
                    inbox = InboxEvent(
                        event_id=envelope.event_id,
                        event_type=envelope.event_type,
                    )
                    session.add(inbox)
                    await session.commit()
                    logger.debug("Processed event %s", envelope.event_type)
                except Exception as exc:
                    logger.error("Handler failed for event %s: %s", envelope.event_type, exc)
                    raise
