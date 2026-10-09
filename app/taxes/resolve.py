"""Resolve the taxes that apply to products and to explicitly chosen lines.

The calculator in :mod:`app.taxes.calc` is pure; this is the only place that
talks to the database about tax. Both matter: the arithmetic must be identical
for a sale, a document and a converted document, and the assignment that feeds
it must be read once, from the product, rather than handed over by whichever
client is calling.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.taxes.models import Tax


def _entry(tax: Tax) -> dict:
    """The calculator's view of a tax: identity plus where the money is owed."""
    return {
        "id": str(tax.id),
        "name": tax.name,
        "rate": float(tax.rate),
        "account_code": tax.account_code or "2400",
    }


async def _active_taxes(session: AsyncSession, tenant_id: UUID, tax_ids: set[UUID]) -> dict[UUID, Tax]:
    if not tax_ids:
        return {}
    rows = (
        await session.execute(
            select(Tax).where(
                Tax.tenant_id == tenant_id,
                Tax.id.in_(tax_ids),
                Tax.is_active == True,  # noqa: E712 -- column comparison, not python bool
            )
        )
    ).scalars()
    return {t.id: t for t in rows}


async def product_tax_map(
    session: AsyncSession,
    tenant_id: UUID,
    product_ids: list[UUID],
) -> dict[str, list[dict]]:
    """Map each product to the active taxes assigned to it.

    Products with no assignment, or whose taxes have all been deactivated,
    map to an empty list -- an untaxed line, not a missing one.
    """
    unique = {pid for pid in product_ids if pid}
    if not unique:
        return {}

    rows = (
        await session.execute(
            select(Product.id, Product.tax_ids).where(
                Product.tenant_id == tenant_id,
                Product.id.in_(unique),
            )
        )
    ).all()

    assigned: dict[UUID, list[UUID]] = {}
    wanted: set[UUID] = set()
    for product_id, tax_ids in rows:
        ids = [UUID(str(t)) for t in (tax_ids or [])]
        assigned[product_id] = ids
        wanted.update(ids)

    active = await _active_taxes(session, tenant_id, wanted)
    return {
        str(pid): [_entry(active[tid]) for tid in ids if tid in active]
        for pid, ids in assigned.items()
    }


async def taxes_for_ids(
    session: AsyncSession,
    tenant_id: UUID,
    tax_ids: list[UUID] | list[str],
) -> list[dict]:
    """Resolve an explicitly chosen set of taxes, as a custom document line picks.

    The client names the tax; the rate and the liability account still come
    from the tenant's own row, so a client cannot price its own tax.

    Callers hand over either UUIDs or the strings the JSON column stores;
    normalising once keeps the query and the lookup below on the same objects.
    """
    ids = [UUID(str(t)) for t in tax_ids if t]
    if not ids:
        return []
    active = await _active_taxes(session, tenant_id, set(ids))
    # Preserve the order the caller chose them in.
    return [_entry(active[tid]) for tid in ids if tid in active]
