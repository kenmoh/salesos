from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from tenancy.models import Tenant, TenantTierProjection


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def get_tenant_by_id(session: AsyncSession, tenant_id: UUID) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def slug_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(
        select(func.count()).select_from(Tenant).where(Tenant.slug == slug)
    )
    return result.scalar() > 0


async def create_tenant(session: AsyncSession, tenant: Tenant) -> Tenant:
    session.add(tenant)
    await session.flush()
    return tenant


async def update_tenant_tier(session: AsyncSession, tenant_id: UUID, new_tier: str) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one()
    tenant.tier = new_tier
    await session.flush()
    return tenant


async def get_tier_projection(
    session: AsyncSession, tenant_id: UUID
) -> TenantTierProjection | None:
    result = await session.execute(
        select(TenantTierProjection).where(TenantTierProjection.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def upsert_tier_projection(session: AsyncSession, projection: TenantTierProjection) -> None:
    existing = await get_tier_projection(session, projection.tenant_id)
    if existing:
        existing.tier = projection.tier
        existing.max_terminals = projection.max_terminals
        existing.max_products = projection.max_products
        existing.max_users = projection.max_users
    else:
        session.add(projection)
    await session.flush()
