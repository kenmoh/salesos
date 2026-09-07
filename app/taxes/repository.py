from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Tax


async def create_tax(session: AsyncSession, tax: Tax) -> Tax:
    session.add(tax)
    await session.flush()
    return tax


async def get_tax_by_id(session: AsyncSession, tax_id: UUID) -> Tax | None:
    result = await session.execute(
        select(Tax).where(Tax.id == tax_id)
    )
    return result.scalar_one_or_none()


async def list_taxes(
    session: AsyncSession, tenant_id: UUID, include_inactive: bool = False
) -> list[Tax]:
    stmt = select(Tax).where(Tax.tenant_id == tenant_id)
    if not include_inactive:
        stmt = stmt.where(Tax.is_active == True)
    stmt = stmt.order_by(Tax.name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_tax(
    session: AsyncSession,
    tax_id: UUID,
    tenant_id: UUID,
    **fields,
) -> Tax | None:
    result = await session.execute(
        select(Tax).where(Tax.id == tax_id, Tax.tenant_id == tenant_id)
    )
    tax = result.scalar_one_or_none()
    if not tax:
        return None
    for key, value in fields.items():
        if value is not None:
            setattr(tax, key, value)
    await session.flush()
    return tax


async def delete_tax(session: AsyncSession, tax_id: UUID, tenant_id: UUID) -> bool:
    result = await session.execute(
        select(Tax).where(Tax.id == tax_id, Tax.tenant_id == tenant_id)
    )
    tax = result.scalar_one_or_none()
    if not tax:
        return False
    tax.is_active = False
    await session.flush()
    return True
