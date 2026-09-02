from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    StockAdjustment,
    StockBalance,
    StockReservation,
    TransferRequest,
)
from app.stores.repository import list_stores


async def get_stock_balance(
    session: AsyncSession, product_id: UUID, store_id: UUID
) -> StockBalance | None:
    result = await session.execute(
        select(StockBalance).where(
            StockBalance.product_id == product_id, StockBalance.store_id == store_id
        )
    )
    return result.scalar_one_or_none()


async def upsert_stock_balance(session: AsyncSession, balance: StockBalance) -> StockBalance:
    existing = await get_stock_balance(session, balance.product_id, balance.store_id)
    if existing:
        existing.qty = balance.qty
        existing.reserved_qty = balance.reserved_qty
        existing.unit_cost = balance.unit_cost
        existing.min_stock_level = balance.min_stock_level
        await session.flush()
        return existing
    session.add(balance)
    await session.flush()
    return balance


async def create_stock_adjustment(
    session: AsyncSession, adjustment: StockAdjustment
) -> StockAdjustment:
    session.add(adjustment)
    await session.flush()
    return adjustment


async def get_stock_balances_by_product(
    session: AsyncSession, product_id: UUID
) -> list[StockBalance]:
    result = await session.execute(
        select(StockBalance).where(StockBalance.product_id == product_id)
    )
    return list(result.scalars().all())


async def create_reservation(
    session: AsyncSession, reservation: StockReservation
) -> StockReservation:
    session.add(reservation)
    await session.flush()
    return reservation


async def get_reservation_by_id(
    session: AsyncSession, reservation_id: UUID
) -> StockReservation | None:
    result = await session.execute(
        select(StockReservation).where(StockReservation.id == reservation_id)
    )
    return result.scalar_one_or_none()


async def get_reservations_by_sale(session: AsyncSession, sale_id: UUID) -> list[StockReservation]:
    result = await session.execute(
        select(StockReservation).where(
            StockReservation.sale_id == sale_id, StockReservation.status == "active"
        )
    )
    return list(result.scalars().all())


async def release_reservation(session: AsyncSession, reservation_id: UUID) -> StockReservation:
    from datetime import UTC, datetime

    result = await session.execute(
        select(StockReservation).where(StockReservation.id == reservation_id)
    )
    reservation = result.scalar_one()
    reservation.status = "released"
    reservation.released_at = datetime.now(UTC)
    await session.flush()
    return reservation


async def get_adjustment_history(
    session: AsyncSession,
    tenant_id: UUID,
    product_id: UUID | None = None,
    store_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[StockAdjustment]:
    query = select(StockAdjustment).where(StockAdjustment.tenant_id == tenant_id)
    if product_id:
        query = query.where(StockAdjustment.product_id == product_id)
    if store_id:
        query = query.where(StockAdjustment.store_id == store_id)
    query = query.order_by(StockAdjustment.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def commit_reservations_for_sale(
    session: AsyncSession, tenant_id: UUID, sale_id: UUID
) -> None:
    reservations = await get_reservations_by_sale(session, sale_id)
    if not reservations:
        return
    product_store_pairs = [(r.product_id, r.store_id) for r in reservations]
    from sqlalchemy import select

    stmt = select(StockBalance).where(
        StockBalance.tenant_id == tenant_id,
        StockBalance.product_id.in_({p for p, _ in product_store_pairs}),
    )
    result = await session.execute(stmt)
    balances = {(b.product_id, b.store_id): b for b in result.scalars().all()}
    for reservation in reservations:
        balance = balances.get((reservation.product_id, reservation.store_id))
        if balance:
            balance.qty -= reservation.qty
            balance.reserved_qty -= reservation.qty
        reservation.status = "committed"
        reservation.released_at = datetime.now(UTC)
    await session.flush()


async def release_reservations_for_sale(
    session: AsyncSession, tenant_id: UUID, sale_id: UUID
) -> None:
    reservations = await get_reservations_by_sale(session, sale_id)
    if not reservations:
        return
    product_store_pairs = [(r.product_id, r.store_id) for r in reservations]
    from sqlalchemy import select

    stmt = select(StockBalance).where(
        StockBalance.tenant_id == tenant_id,
        StockBalance.product_id.in_({p for p, _ in product_store_pairs}),
    )
    result = await session.execute(stmt)
    balances = {(b.product_id, b.store_id): b for b in result.scalars().all()}
    for reservation in reservations:
        balance = balances.get((reservation.product_id, reservation.store_id))
        if balance:
            balance.reserved_qty -= reservation.qty
        reservation.status = "released"
        reservation.released_at = datetime.now(UTC)
    await session.flush()


async def create_transfer_request(
    session: AsyncSession, request: TransferRequest
) -> TransferRequest:
    session.add(request)
    await session.flush()
    return request


async def get_transfer_request_by_id(
    session: AsyncSession, request_id: UUID
) -> TransferRequest | None:
    result = await session.execute(select(TransferRequest).where(TransferRequest.id == request_id))
    return result.scalar_one_or_none()


async def list_transfer_requests(
    session: AsyncSession,
    tenant_id: UUID,
    status: str | None = None,
    store_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TransferRequest]:
    query = select(TransferRequest).where(TransferRequest.tenant_id == tenant_id)
    if status:
        query = query.where(TransferRequest.status == status)
    if store_id:
        query = query.where(
            (TransferRequest.requesting_store_id == store_id)
            | (TransferRequest.supplying_store_id == store_id)
        )
    query = query.order_by(TransferRequest.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def count_transfer_requests(
    session: AsyncSession,
    tenant_id: UUID,
    status: str | None = None,
    store_id: UUID | None = None,
) -> int:
    query = select(TransferRequest).where(TransferRequest.tenant_id == tenant_id)
    if status:
        query = query.where(TransferRequest.status == status)
    if store_id:
        query = query.where(
            (TransferRequest.requesting_store_id == store_id)
            | (TransferRequest.supplying_store_id == store_id)
        )
    result = await session.execute(query)
    return len(list(result.scalars().all()))


async def get_stores_with_stock(session: AsyncSession, tenant_id: UUID) -> list[dict]:
    stores = await list_stores(session, tenant_id)
    if not stores:
        return []

    stmt = select(StockBalance).where(
        StockBalance.tenant_id == tenant_id,
        StockBalance.qty > 0,
    )
    result = await session.execute(stmt)
    all_balances = list(result.scalars().all())

    store_map: dict[UUID, list] = {s.id: [] for s in stores}
    for balance in all_balances:
        if balance.store_id in store_map:
            store_map[balance.store_id].append(balance)

    return [
        {
            "store_id": s.id,
            "store_name": s.name,
            "is_warehouse": s.is_warehouse,
            "balances": [
                {
                    "product_id": str(b.product_id),
                    "qty": float(b.qty),
                    "reserved_qty": float(b.reserved_qty),
                    "min_stock_level": float(b.min_stock_level),
                    "unit_cost": float(b.unit_cost) if b.unit_cost else None,
                }
                for b in store_map.get(s.id, [])
            ],
        }
        for s in stores
    ]


async def update_stock_balance_min_level(
    session: AsyncSession, store_id: UUID, product_id: UUID, min_stock_level: float
) -> StockBalance | None:
    result = await session.execute(
        select(StockBalance).where(
            StockBalance.store_id == store_id,
            StockBalance.product_id == product_id,
        )
    )
    balance = result.scalar_one_or_none()
    if balance:
        balance.min_stock_level = min_stock_level
        await session.flush()
    return balance
