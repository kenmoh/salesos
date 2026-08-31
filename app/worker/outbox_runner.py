"""Outbox relay runner — polls outbox tables and publishes to RabbitMQ.

Run as a standalone process:
    python -m worker.outbox_runner
"""

import asyncio
import logging

from app.common.messagebus.outbox_relay import OutboxRelay
from app.common.messagebus.publisher import EventPublisher
from app.common.settings import get_common_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
logger = logging.getLogger("storeflow.outbox_runner")

settings = get_common_settings()

SCHEMAS = [
    "tenancy",
    "identity",
    "catalog",
    "cart",
    "sales",
    "inventory",
    "payments",
    "terminals",
    "accounting",
    "notifications",
    "reporting",
    "documents",
    "ai",
]


async def main():
    publisher = EventPublisher(connection_url=settings.rabbitmq_url)
    await publisher.connect()

    relay = OutboxRelay(
        publisher=publisher,
        database_url=settings.database_url,
        schemas=SCHEMAS,
        poll_interval=5.0,
        batch_size=50,
    )

    try:
        await relay.start()
    except KeyboardInterrupt:
        await relay.stop()
        await publisher.close()


if __name__ == "__main__":
    asyncio.run(main())
