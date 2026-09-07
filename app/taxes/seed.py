from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Tax
from .repository import list_taxes


async def seed_default_taxes(tenant_id: UUID, session: AsyncSession) -> None:
    """Seed default tax types for a new tenant.

    Creates a VAT tax type at 7.5% (Nigerian standard rate).
    Skips if tax types already exist for this tenant.

    Args:
        tenant_id: The business tenant UUID.
        session: The async SQLAlchemy database session.
    """
    existing = await list_taxes(session, tenant_id, include_inactive=True)
    if existing:
        return

    vat = Tax(
        tenant_id=tenant_id,
        name="VAT",
        rate=7.50,
        is_active=True,
    )
    session.add(vat)
    await session.flush()
