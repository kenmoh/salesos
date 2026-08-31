"""Alembic environment for SalesOS — async mode.

All domain models are imported so that ``StoreFlowBase.metadata`` contains
every table, enabling ``--autogenerate`` to produce complete migrations.
All tables live in the public schema.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.common.db.base import StoreFlowBase

# Import ALL models so they register with StoreFlowBase.metadata
import app.accounting.models  # noqa: F401
import app.ai.models  # noqa: F401
import app.cart.models  # noqa: F401
import app.catalog.models  # noqa: F401
import app.customers.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.identity.models  # noqa: F401
import app.inventory.models  # noqa: F401
import app.notifications.models  # noqa: F401
import app.payments.models  # noqa: F401
import app.reporting.models  # noqa: F401
import app.sales.models  # noqa: F401
import app.tenancy.models  # noqa: F401
import app.platform.models  # noqa: F401
import app.stores.models  # noqa: F401
from app.auth.audit import AuditEvent  # noqa: F401

# Outbox / inbox models
import app.common.events.outbox  # noqa: F401
import app.common.events.inbox  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.core.config import settings

target_metadata = StoreFlowBase.metadata


MV_TABLES_TO_SKIP = {
    "mv_daily_sales",
    "mv_product_rankings",
    "mv_payment_methods",
    "mv_cashier_performance",
    "mv_customer_summary",
    "mv_inventory_status",
    "mv_store_sales",
    "mv_store_product_rankings",
    "mv_store_inventory",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in MV_TABLES_TO_SKIP:
        return False
    schema = getattr(obj, "schema", None)
    if schema is None or schema == "public":
        return True
    return False


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": settings.database_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
