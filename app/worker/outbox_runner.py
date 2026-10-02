"""Outbox relay runner — polls the outbox table and publishes to RabbitMQ.

Run as a standalone process:
    python -m app.worker.outbox_runner
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


async def main():
    if not settings.rabbitmq_url:
        raise SystemExit("RABBIT_MQ_URL is not set — cannot start outbox relay")

    publisher = EventPublisher(connection_url=settings.rabbitmq_url)
    await publisher.connect()

    # The relay polls across all tenants, so it must bypass per-tenant RLS:
    # use the admin (owner) URL when configured.
    if settings.admin_database_url:
        db_url = settings.admin_database_url
    else:
        db_url = settings.database_url
        logger.warning(
            "ADMIN_DATABASE_URL not set — outbox relay uses DATABASE_URL and "
            "may see no rows due to RLS"
        )

    relay = OutboxRelay(
        publisher=publisher,
        database_url=db_url,
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
