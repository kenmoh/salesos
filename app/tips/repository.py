"""Database access for cashier tips and settlements."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import User
from app.sales.models import Sale, SaleItem
from app.tips.models import CashierTip, CashierTipSettlement
from app.tips.service import build_tips_for_sale


def _day_bounds(start: date | None, end: date | None):
    start_dt = datetime.combine(start, time.min, tzinfo=UTC) if start else None
    end_dt = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
        if end
        else None
    )
    return start_dt, end_dt


async def record_tips_for_sale(
    session: AsyncSession, sale: Sale, items: list[SaleItem]
) -> int:
    """Insert this sale's positive-extra tips, safe to call twice."""
    tips = build_tips_for_sale(sale, items)
    if not tips:
        return 0

    now = datetime.now(UTC)
    values = [
        {
            "id": tip.id,
            "tenant_id": tip.tenant_id,
            "sale_id": tip.sale_id,
            "sale_item_id": tip.sale_item_id,
            "cashier_id": tip.cashier_id,
            "store_id": tip.store_id,
            "extra": tip.extra,
            "qty": tip.qty,
            "amount": tip.amount,
            "status": tip.status,
            "earned_at": tip.earned_at,
            "created_at": now,
        }
        for tip in tips
    ]
    await session.execute(
        pg_insert(CashierTip)
        .values(values)
        .on_conflict_do_nothing(index_elements=["sale_item_id"])
    )
    return len(values)


async def lock_payable_tips(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    cashier_id: UUID,
    period_start: date,
    period_end: date,
) -> list[CashierTip]:
    """Lock every pending tip whose sale completed inside the range.

    ``FOR UPDATE`` keeps two settlement requests for the same cashier from
    paying the same tips twice.
    """
    start_dt, end_dt = _day_bounds(period_start, period_end)
    result = await session.execute(
        select(CashierTip)
        .join(Sale, Sale.id == CashierTip.sale_id)
        .where(
            CashierTip.tenant_id == tenant_id,
            CashierTip.cashier_id == cashier_id,
            CashierTip.status == "pending",
            Sale.status == "completed",
            CashierTip.earned_at >= start_dt,
            CashierTip.earned_at < end_dt,
        )
        .order_by(CashierTip.earned_at)
        .with_for_update(of=CashierTip)
    )
    return list(result.scalars().all())


async def create_settlement(
    session: AsyncSession, settlement: CashierTipSettlement
) -> None:
    session.add(settlement)
    await session.flush()


async def mark_tips_settled(
    session: AsyncSession,
    tip_ids: list[UUID],
    settlement_id: UUID,
    settled_at: datetime,
) -> None:
    if not tip_ids:
        return
    await session.execute(
        update(CashierTip)
        .where(CashierTip.id.in_(tip_ids))
        .values(
            status="settled",
            settlement_id=settlement_id,
            settled_at=settled_at,
        )
    )


async def void_pending_tips_for_sale(session: AsyncSession, sale_id: UUID) -> None:
    await session.execute(
        update(CashierTip)
        .where(
            CashierTip.sale_id == sale_id,
            CashierTip.status == "pending",
        )
        .values(status="voided")
    )


async def list_tips(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    cashier_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = 1,
    page_size: int = 50,
):
    filters = [CashierTip.tenant_id == tenant_id]
    if cashier_id:
        filters.append(CashierTip.cashier_id == cashier_id)
    if status:
        filters.append(CashierTip.status == status)
    start_dt, end_dt = _day_bounds(from_date, to_date)
    if start_dt:
        filters.append(CashierTip.earned_at >= start_dt)
    if end_dt:
        filters.append(CashierTip.earned_at < end_dt)

    query = (
        select(
            CashierTip,
            Sale.sale_number,
            Sale.status,
            SaleItem.product_name,
            User.full_name,
        )
        .join(Sale, Sale.id == CashierTip.sale_id)
        .outerjoin(SaleItem, SaleItem.id == CashierTip.sale_item_id)
        .outerjoin(
            User,
            and_(
                User.id == CashierTip.cashier_id,
                User.tenant_id == tenant_id,
            ),
        )
        .where(*filters)
        .order_by(CashierTip.earned_at.desc(), CashierTip.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(query)).all()

    count_query = (
        select(func.count())
        .select_from(CashierTip)
        .join(Sale, Sale.id == CashierTip.sale_id)
        .where(*filters)
    )
    total = int((await session.execute(count_query)).scalar() or 0)
    return rows, total


async def tip_summary(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    cashier_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
):
    filters = [CashierTip.tenant_id == tenant_id]
    if cashier_id:
        filters.append(CashierTip.cashier_id == cashier_id)
    start_dt, end_dt = _day_bounds(from_date, to_date)
    if start_dt:
        filters.append(CashierTip.earned_at >= start_dt)
    if end_dt:
        filters.append(CashierTip.earned_at < end_dt)

    payable = and_(
        CashierTip.status == "pending",
        Sale.status == "completed",
    )
    settled = CashierTip.status == "settled"

    query = (
        select(
            CashierTip.cashier_id,
            User.full_name,
            func.coalesce(
                func.sum(case((payable, CashierTip.amount), else_=0)), 0
            ),
            func.coalesce(func.sum(case((payable, 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((settled, CashierTip.amount), else_=0)), 0
            ),
            func.coalesce(func.sum(case((settled, 1), else_=0)), 0),
            func.max(case((settled, CashierTip.settled_at))),
        )
        .join(Sale, Sale.id == CashierTip.sale_id)
        .outerjoin(
            User,
            and_(
                User.id == CashierTip.cashier_id,
                User.tenant_id == tenant_id,
            ),
        )
        .where(*filters)
        .group_by(CashierTip.cashier_id, User.full_name)
        .order_by(CashierTip.cashier_id)
    )
    return (await session.execute(query)).all()


async def list_settlements(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    cashier_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = 1,
    page_size: int = 50,
):
    filters = [CashierTipSettlement.tenant_id == tenant_id]
    if cashier_id:
        filters.append(CashierTipSettlement.cashier_id == cashier_id)
    start_dt, end_dt = _day_bounds(from_date, to_date)
    if start_dt:
        filters.append(CashierTipSettlement.paid_at >= start_dt)
    if end_dt:
        filters.append(CashierTipSettlement.paid_at < end_dt)

    query = (
        select(CashierTipSettlement)
        .where(*filters)
        .order_by(CashierTipSettlement.paid_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    settlements = list((await session.execute(query)).scalars().all())

    count_query = (
        select(func.count())
        .select_from(CashierTipSettlement)
        .where(*filters)
    )
    total = int((await session.execute(count_query)).scalar() or 0)
    return settlements, total


async def get_settlement(
    session: AsyncSession, *, tenant_id: UUID, settlement_id: UUID
) -> CashierTipSettlement | None:
    result = await session.execute(
        select(CashierTipSettlement).where(
            CashierTipSettlement.id == settlement_id,
            CashierTipSettlement.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def settlement_tips(
    session: AsyncSession, *, tenant_id: UUID, settlement_id: UUID
):
    query = (
        select(
            CashierTip,
            Sale.sale_number,
            SaleItem.product_name,
        )
        .join(Sale, Sale.id == CashierTip.sale_id)
        .outerjoin(SaleItem, SaleItem.id == CashierTip.sale_item_id)
        .where(
            CashierTip.tenant_id == tenant_id,
            CashierTip.settlement_id == settlement_id,
        )
        .order_by(CashierTip.earned_at)
    )
    return (await session.execute(query)).all()


async def cashier_exists(session: AsyncSession, *, tenant_id: UUID, cashier_id: UUID) -> bool:
    result = await session.execute(
        select(User.id).where(
            User.id == cashier_id,
            User.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none() is not None
