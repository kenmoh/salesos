"""RabbitMQ topology setup — creates exchanges, queues, and bindings.

Run this once when deploying or after adding new event subscriptions.
"""

import asyncio
import logging

import aio_pika

from messagebus.publisher import EXCHANGE_NAME, EXCHANGE_TYPE

logger = logging.getLogger("storeflow.messagebus.setup")

# Service queues and their routing key bindings
SERVICE_QUEUES: dict[str, list[str]] = {
    "tenancy": ["tenancy.#"],
    "identity": ["identity.#"],
    "catalog": ["catalog.#"],
    "cart": ["cart.#"],
    "sales": ["sales.#", "cart.checked_out", "payment.succeeded"],
    "inventory": [
        "inventory.#",
        "sales.sale_created",
        "sales.sale_voided",
        "catalog.product_created",
    ],
    "payments": ["payment.#", "sales.sale_created"],
    "terminals": ["terminal.#", "tenancy.tier_changed"],
    "accounting": ["accounting.#", "sales.sale_confirmed", "payment.succeeded"],
    "notifications": [
        "notification.#",
        "tenancy.tenant_created",
        "identity.user_created",
        "inventory.low_stock_detected",
    ],
    "reporting": ["reporting.#", "sales.#", "payment.#", "tenancy.tier_changed"],
}


async def setup_topology(rabbitmq_url: str) -> None:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(EXCHANGE_NAME, EXCHANGE_TYPE, durable=True)
        for service_name, routing_keys in SERVICE_QUEUES.items():
            queue = await channel.declare_queue(
                f"{service_name}.events", durable=True, auto_delete=False
            )
            for key in routing_keys:
                await queue.bind(exchange, routing_key=key)
            logger.info("Queue '%s' bound with keys: %s", service_name, routing_keys)
    logger.info("RabbitMQ topology setup complete")


def run_setup(rabbitmq_url: str) -> None:
    asyncio.run(setup_topology(rabbitmq_url))


def run_setup_cli() -> None:
    import os

    url = os.environ.get("RABBITMQ_URL", "amqp://storeflow:storeflow@localhost:5672//")
    run_setup(url)
