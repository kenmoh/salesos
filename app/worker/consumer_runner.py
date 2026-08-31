"""Event consumer runner — starts EventConsumer for each service.

Run as a standalone process:
    python -m worker.consumer_runner
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.messagebus.consumer import EventConsumer
from app.common.messagebus.setup import SERVICE_QUEUES
from app.common.settings import get_common_settings

from app.worker.handlers import apply_service_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
logger = logging.getLogger("storeflow.consumer_runner")

settings = get_common_settings()


def _session_factory(schema: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def main():
    consumers: list[EventConsumer] = []

    for service_name, routing_keys in SERVICE_QUEUES.items():
        consumer = EventConsumer(
            connection_url=settings.rabbitmq_url,
            service_name=service_name,
            database_session_factory=_session_factory(service_name),
            schema=service_name,
            queue_name=f"{service_name}.events",
            routing_keys=routing_keys,
        )

        apply_service_handlers(service_name, consumer.register_handler)

        consumers.append(consumer)

    logger.info("Starting %d event consumers...", len(consumers))
    tasks = [asyncio.create_task(c.start()) for c in consumers]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Shutting down consumers...")
        for c in consumers:
            await c.stop()


if __name__ == "__main__":
    asyncio.run(main())
