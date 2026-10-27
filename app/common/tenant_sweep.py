"""Destructive tenant data sweeps, shared by owner and platform deletes.

The sweep walks every mapped table that owns a ``tenant_id`` column,
deleting tenant-scoped rows child-first (reversed SQLA dependency order so
in-database foreign keys stay satisfied), then removes the tenant row
itself. Callers are responsible for whatever surrounds the sweep: role
checks, RLS GUCs, confirmations, session revocation and audit entries.
"""

from uuid import UUID
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


MODEL_MODULES = (
    "app.tenancy.models",
    "app.identity.models",
    "app.stores.models",
    "app.catalog.models",
    "app.cart.models",
    "app.sales.models",
    "app.payments.models",
    "app.customers.models",
    "app.inventory.models",
    "app.documents.models",
    "app.taxes.models",
    "app.accounting.models",
    "app.ai.models",
    "app.reporting.models",
    "app.notifications.models",
    "app.platform.models",
)


def ensure_model_metadata() -> None:
    """Register every model module with the shared metadata before sweeping.

    ``sorted_tables`` only knows tables whose owning module has been
    imported; a partial sweep would silently orphan those rows.
    """
    import importlib

    for _module in MODEL_MODULES:
        importlib.import_module(_module)


async def sweep_tenant_data(session: "AsyncSession", tenant_id: UUID | str) -> int:
    """Delete every tenant-scoped row, then the tenant record.

    Returns how many mapped tables were swept (the tenant row itself is
    not counted). Tables without a ``tenant_id`` column, such as the
    contact inbox or audit logs keyed by owner, are intentionally left to
    the caller's policy.
    """
    from app.common.db.base import StoreFlowBase

    ensure_model_metadata()

    tenant_uuid = UUID(str(tenant_id))
    tables = [
        t
        for t in reversed(StoreFlowBase.metadata.sorted_tables)
        if any(c.name == "tenant_id" for c in t.columns)
    ]
    for table in tables:
        await session.execute(table.delete().where(table.c.tenant_id == tenant_uuid))

    from sqlalchemy import select

    from app.tenancy.models import Tenant

    tenant_result = await session.execute(select(Tenant).where(Tenant.id == tenant_uuid))
    tenant = tenant_result.scalar_one_or_none()
    if tenant:
        await session.delete(tenant)

    await session.flush()
    return len(tables)
