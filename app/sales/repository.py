from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales.models import Receipt, Sale, SaleItem


async def get_sale_by_id(session: AsyncSession, sale_id: UUID) -> Sale | None:
    result = await session.execute(select(Sale).where(Sale.id == sale_id))
    return result.scalar_one_or_none()


async def get_sale_by_number(session: AsyncSession, sale_number: str) -> Sale | None:
    result = await session.execute(select(Sale).where(Sale.sale_number == sale_number))
    return result.scalar_one_or_none()


async def create_sale(session: AsyncSession, sale: Sale) -> Sale:
    session.add(sale)
    await session.flush()
    return sale


async def create_sale_items(session: AsyncSession, items: list[SaleItem]) -> None:
    for item in items:
        session.add(item)
    await session.flush()


async def update_sale_status(session: AsyncSession, sale_id: UUID, status: str) -> Sale:
    result = await session.execute(select(Sale).where(Sale.id == sale_id))
    sale = result.scalar_one()
    sale.status = status
    await session.flush()
    return sale


async def void_sale(session: AsyncSession, sale_id: UUID, voided_by: UUID, reason: str) -> Sale:
    from datetime import UTC, datetime

    result = await session.execute(select(Sale).where(Sale.id == sale_id))
    sale = result.scalar_one()
    sale.status = "voided"
    sale.voided_by = voided_by
    sale.voided_at = datetime.now(UTC)
    sale.void_reason = reason
    await session.flush()
    return sale


async def list_sales_by_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    status: str | None = None,
    cashier_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Sale]:
    query = select(Sale).where(Sale.tenant_id == tenant_id)
    if status:
        query = query.where(Sale.status == status)
    if cashier_id:
        query = query.where(Sale.cashier_id == cashier_id)
    query = query.order_by(Sale.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_sale_items(session: AsyncSession, sale_id: UUID) -> list[SaleItem]:
    result = await session.execute(
        select(SaleItem).where(SaleItem.sale_id == sale_id).order_by(SaleItem.id)
    )
    return list(result.scalars().all())


async def create_receipt(session: AsyncSession, receipt: Receipt) -> Receipt:
    session.add(receipt)
    await session.flush()
    return receipt


async def get_receipt_by_sale(session: AsyncSession, sale_id: UUID) -> Receipt | None:
    result = await session.execute(select(Receipt).where(Receipt.sale_id == sale_id))
    return result.scalar_one_or_none()
