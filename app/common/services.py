import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.helpers import call, call_scalar, exec_fn

logger = logging.getLogger(__name__)


async def create_product(
    *, session: AsyncSession, business_id: str, user_id: str, data: dict
) -> str:
    return str(
        await call_scalar(
            session,
            "api.fn_create_product",
            p_bid=business_id,
            p_uid=user_id,
            p_data=json.dumps(data),
        )
    )


async def list_products(
    *,
    session: AsyncSession,
    business_id: str,
    search=None,
    category=None,
    low_stock=False,
    page=1,
    page_size=50,
) -> dict:
    rows = await call(
        session,
        "api.fn_list_products",
        p_bid=business_id,
        p_search=search,
        p_category=category,
        p_low_stock=low_stock,
        p_limit=page_size,
        p_offset=(page - 1) * page_size,
    )
    total = rows[0].get("total_count") if rows else None
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


async def get_product(*, session: AsyncSession, business_id: str, product_id: str) -> dict:
    rows = await call(session, "api.fn_get_product", p_bid=business_id, p_pid=product_id)
    if not rows:
        raise ValueError("product_not_found")
    return rows[0]


_PRODUCT_COLUMNS = frozenset(
    {
        "name",
        "sku",
        "barcode",
        "description",
        "unit",
        "cost_price",
        "selling_price",
        "tax_rate",
        "reorder_point",
        "track_inventory",
        "image_url",
        "metadata",
        "is_active",
    }
)


async def update_product(
    *, session: AsyncSession, business_id: str, product_id: str, updates: dict
) -> None:
    safe_updates = {k: v for k, v in updates.items() if v is not None and k in _PRODUCT_COLUMNS}
    if not safe_updates:
        return
    sets = ", ".join(f"{k}=:{k}" for k in safe_updates)
    await session.execute(
        text(f"UPDATE products SET {sets}, updated_at=NOW() WHERE id=:id AND business_id=:bid"),
        {**safe_updates, "id": product_id, "bid": business_id},
    )


async def adjust_inventory(
    *,
    session: AsyncSession,
    business_id: str,
    user_id: str,
    product_id: str,
    store_id: str,
    reason: str,
    qty_change: Decimal,
    unit_cost=None,
    notes=None,
) -> dict:
    rows = await call(
        session,
        "api.fn_adjust_inventory",
        p_bid=business_id,
        p_uid=user_id,
        p_product=product_id,
        p_warehouse=store_id,
        p_reason=reason,
        p_qty_change=qty_change,
        p_unit_cost=unit_cost,
        p_ref_id=None,
        p_ref_type=None,
        p_notes=notes,
    )
    return rows[0] if rows else {}


async def transfer_stock(
    *,
    session: AsyncSession,
    business_id: str,
    user_id: str,
    product_id: str,
    from_store: str,
    to_store: str,
    qty: Decimal,
    notes=None,
) -> None:
    await exec_fn(
        session,
        "api.fn_transfer_stock",
        p_bid=business_id,
        p_uid=user_id,
        p_product=product_id,
        p_from_wh=from_store,
        p_to_wh=to_store,
        p_qty=qty,
        p_notes=notes,
    )


async def low_stock_report(*, session: AsyncSession, business_id: str) -> list:
    return await call(session, "api.fn_low_stock_report", p_bid=business_id)


async def inventory_history(
    *,
    session: AsyncSession,
    business_id: str,
    product_id=None,
    store_id=None,
    page=1,
    page_size=50,
) -> dict:
    rows = await call(
        session,
        "get_store_history",
        p_tenant_id=business_id,
        p_store_id=store_id,
        p_product_id=product_id,
        p_page=page,
        p_page_size=page_size,
    )
    if rows and isinstance(rows[0], dict):
        return rows[0]
    return {"data": [], "total": 0, "page": page, "page_size": page_size}


async def product_stock_series(
    *,
    session: AsyncSession,
    business_id: str,
    store_id: str,
    product_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    """Daily closing balance and quantity moved for one product in one store.

    The range is a half-open UTC window on ``created_at`` so the filter stays
    sargable; movement counts per product are small either way.
    """
    from datetime import UTC, datetime, time, timedelta
    from uuid import UUID

    from sqlalchemy import select

    from app.inventory.models import StockMovement
    from app.inventory.service import group_movements_by_day

    d_from = datetime.fromisoformat(from_date).date()
    d_to = datetime.fromisoformat(to_date).date()
    start = datetime.combine(d_from, time.min, tzinfo=UTC)
    end = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=UTC)

    result = await session.execute(
        select(StockMovement)
        .where(
            StockMovement.tenant_id == UUID(business_id),
            StockMovement.store_id == UUID(store_id),
            StockMovement.product_id == UUID(product_id),
            StockMovement.created_at >= start,
            StockMovement.created_at < end,
        )
        .order_by(StockMovement.created_at)
    )
    points = group_movements_by_day(result.scalars().all())

    balance = points[-1]["balance"] if points else 0.0
    moved = round(sum(p["qty_change"] for p in points), 2)
    return {
        "items": points,
        "totals": {"balance": balance, "qty_change": moved},
    }


async def create_sale(*, session: AsyncSession, business_id: str, user_id: str, data: dict) -> dict:
    rows = await call(
        session,
        "api.fn_create_sale",
        p_bid=business_id,
        p_uid=user_id,
        p_data=json.dumps(data, default=str),
    )
    return rows[0] if rows else {}


async def get_sale(*, session: AsyncSession, business_id: str, sale_id: str) -> dict:
    from app.sales.models import Sale, SaleItem
    from sqlalchemy import select
    from uuid import UUID

    result = await session.execute(
        select(Sale).where(Sale.id == UUID(sale_id), Sale.tenant_id == UUID(business_id))
    )
    sale = result.scalars().first()
    if not sale:
        raise ValueError("sale_not_found")

    items_result = await session.execute(
        select(SaleItem).where(SaleItem.sale_id == sale.id).order_by(SaleItem.id)
    )
    items = [
        {
            "product_id": str(si.product_id),
            "product_name": si.product_name,
            "qty": float(si.qty),
            "unit_price": float(si.unit_price),
            "extra": float(si.extra),
            "discount": float(si.discount_pct) if si.discount_pct else 0,
            "tax_rate": float(si.tax_rate) if si.tax_rate else None,
        }
        for si in items_result.scalars().all()
    ]

    return {
        "id": str(sale.id),
        "sale_number": sale.sale_number or "",
        "status": sale.status or "pending",
        "customer_name": sale.customer_name,
        "customer_phone": None,
        "subtotal": float(sale.subtotal) if sale.subtotal else 0,
        "discount": float(sale.discount) if sale.discount else 0,
        "tax": float(sale.tax) if sale.tax else 0,
        "tax_breakdown": sale.tax_breakdown if isinstance(sale.tax_breakdown, list) else [],
        "total": float(sale.total) if sale.total else 0,
        "amount_paid": float(sale.amount_paid) if sale.amount_paid else 0,
        "payment_methods": sale.payment_methods if isinstance(sale.payment_methods, dict) else None,
        "notes": sale.notes,
        "items": items,
        "created_at": sale.created_at.isoformat() if sale.created_at else None,
    }


async def list_sales(
    *,
    session: AsyncSession,
    business_id: str,
    status=None,
    from_date=None,
    to_date=None,
    cashier_id=None,
    store_id=None,
    page=1,
    page_size=50,
) -> dict:
    from app.sales.models import Sale, SaleItem
    from sqlalchemy import select, func
    from uuid import UUID

    tid = UUID(business_id)

    item_count_sub = (
        select(
            SaleItem.sale_id,
            func.coalesce(func.sum(SaleItem.qty), 0).label("item_count"),
        )
        .group_by(SaleItem.sale_id)
        .subquery()
    )

    query = (
        select(Sale, item_count_sub.c.item_count.label("item_count"))
        .outerjoin(item_count_sub, Sale.id == item_count_sub.c.sale_id)
        .where(Sale.tenant_id == tid)
    )
    count_query = select(func.count()).select_from(Sale).where(Sale.tenant_id == tid)

    if status:
        query = query.where(Sale.status == status)
        count_query = count_query.where(Sale.status == status)
    if cashier_id:
        query = query.where(Sale.cashier_id == UUID(cashier_id))
        count_query = count_query.where(Sale.cashier_id == UUID(cashier_id))
    if store_id:
        query = query.where(Sale.store_id == UUID(store_id))
        count_query = count_query.where(Sale.store_id == UUID(store_id))
    if from_date:
        query = query.where(Sale.created_at >= from_date)
        count_query = count_query.where(Sale.created_at >= from_date)
    if to_date:
        query = query.where(Sale.created_at <= to_date)
        count_query = count_query.where(Sale.created_at <= to_date)

    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Sale.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    result = await session.execute(query)
    rows = result.all()

    items = [
        {
            "id": str(row.Sale.id),
            "sale_number": row.Sale.sale_number or "",
            "status": row.Sale.status or "pending",
            "customer_name": row.Sale.customer_name,
            "total": float(row.Sale.total) if row.Sale.total else 0,
            "amount_paid": float(row.Sale.amount_paid) if row.Sale.amount_paid else 0,
            "item_count": int(row.item_count) if row.item_count else 0,
            "created_at": row.Sale.created_at.isoformat() if row.Sale.created_at else None,
        }
        for row in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def sales_status_summary(
    *,
    session: AsyncSession,
    business_id: str,
    store_id=None,
    from_date=None,
    to_date=None,
) -> dict:
    """Status tallies and revenue for the sales screen header cards.

    One grouped SQL over the sales table: total revenue (completed sales
    only), plus how many are done, pending (partial included, the customer
    still owes) and none (voided or returned). The same store/date filters
    as ``list_sales`` so the cards agree with the list they sit above.
    """
    from uuid import UUID

    from sqlalchemy import func, select

    from app.sales.models import Sale

    tid = UUID(business_id)

    query = (
        select(
            Sale.status,
            func.count().label("count"),
            func.coalesce(func.sum(Sale.total), 0).label("amount"),
        )
        .where(Sale.tenant_id == tid)
        .group_by(Sale.status)
    )
    if store_id:
        query = query.where(Sale.store_id == UUID(store_id))
    if from_date:
        query = query.where(Sale.created_at >= from_date)
    if to_date:
        query = query.where(Sale.created_at <= to_date)

    rows = (await session.execute(query)).all()
    by_status: dict[str, dict] = {}
    for r in rows:
        status = r.status or "pending"
        by_status[status] = {"count": int(r.count), "amount": float(r.amount)}

    def tally(statuses):
        return (
            sum(by_status.get(s, {"count": 0})["count"] for s in statuses),
            sum(by_status.get(s, {"amount": 0})["amount"] for s in statuses),
        )

    served = {"pending", "partial"}
    done_count, done_amount = tally(["completed"])
    due_count, due_amount = tally(served)  # partial means the payer still owes
    dead_count, dead_amount = tally(["voided", "returned"])
    all_count = sum(v["count"] for v in by_status.values())
    return {
        "total_revenue": round(done_amount, 2),
        "completed": done_count,
        "pending": due_count,
        "pending_amount": round(due_amount, 2),
        "voided": dead_count,
        "all": all_count,
    }


async def void_sale(
    *, session: AsyncSession, business_id: str, user_id: str, sale_id: str, reason: str
) -> bool:
    """Void a sale: mark voided, restock inventory, reverse the sale journal.

    Runs entirely inside the caller's transaction so the void is atomic.
    The journal reversal mirrors the original sale journal exactly (accounts
    and amounts swapped); if the sale never posted a journal, nothing is
    reversed — cash was never booked, so it must not be unbooked.

    Raises ValueError("sale_not_found") / ValueError("sale_already_voided").
    """
    from uuid import UUID, uuid4

    from sqlalchemy import select

    from app.accounting.models import Journal, JournalEntry
    from app.common.events.outbox import OutboxWrite
    from app.inventory.models import StockBalance, StockMovement
    from app.sales.events import sale_voided_event
    from app.sales.models import Sale, SaleItem
    from app.tips.repository import void_pending_tips_for_sale

    tenant_id = UUID(business_id)

    result = await session.execute(
        select(Sale).where(Sale.id == UUID(sale_id), Sale.tenant_id == tenant_id)
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise ValueError("sale_not_found")
    if sale.status == "voided":
        raise ValueError("sale_already_voided")

    items_result = await session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id))
    items = list(items_result.scalars().all())

    now = datetime.now(UTC)

    # 1. Mark the sale voided
    sale.status = "voided"
    sale.void_reason = reason
    sale.voided_by = UUID(user_id)
    sale.voided_at = now
    await void_pending_tips_for_sale(session, sale.id)

    # 2. Restock: return each line's qty to the sale's store, with audit trail
    for item in items:
        balance_result = await session.execute(
            select(StockBalance).where(
                StockBalance.tenant_id == tenant_id,
                StockBalance.product_id == item.product_id,
                StockBalance.store_id == sale.store_id,
            )
        )
        balance = balance_result.scalar_one_or_none()
        if not balance:
            continue
        qty = Decimal(str(item.qty))
        before = Decimal(str(balance.qty))
        balance.qty = before + qty
        session.add(
            StockMovement(
                tenant_id=tenant_id,
                product_id=item.product_id,
                store_id=sale.store_id,
                movement_type="void_return",
                qty_change=qty,
                balance_before=before,
                balance_after=balance.qty,
                reference_type="sale",
                reference_id=sale.id,
                reason=reason[:100],
                created_by=UUID(user_id),
            )
        )

    # 3. Reverse the original sale journal (mirror: debits <-> credits)
    orig_result = await session.execute(
        select(Journal).where(
            Journal.tenant_id == tenant_id,
            Journal.reference_type == "sale",
            Journal.reference_id == sale.id,
            Journal.status == "posted",
        )
    )
    original = orig_result.scalars().first()
    if original:
        entries_result = await session.execute(
            select(JournalEntry).where(JournalEntry.journal_id == original.id)
        )
        original_entries = list(entries_result.scalars().all())

        journal_id = uuid4()
        journal_number = f"JRN-{now.strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
        session.add(
            Journal(
                id=journal_id,
                tenant_id=tenant_id,
                journal_number=journal_number,
                description=f"Void sale {sale.sale_number}: {reason}",
                reference_id=sale.id,
                reference_type="sale_void",
                status="posted",
                posted_at=now,
                store_id=sale.store_id,
            )
        )
        for entry in original_entries:
            reversed_debit = float(entry.credit or 0)
            reversed_credit = float(entry.debit or 0)
            session.add(
                JournalEntry(
                    journal_id=journal_id,
                    tenant_id=tenant_id,
                    account_id=entry.account_id,
                    account_code=entry.account_code,
                    debit=reversed_debit,
                    credit=reversed_credit,
                    description=f"Void {sale.sale_number}: {entry.description or ''}"[:255],
                    type=entry.type,
                    status="posted",
                    posted_at=now,
                    amount=reversed_debit + reversed_credit,
                )
            )

    # 4. Emit sales.sale_voided (reporting reverses daily sales;
    #    inventory releases any reservations)
    event = sale_voided_event(
        tenant_id=tenant_id,
        sale_id=sale.id,
        sale_number=sale.sale_number,
        reason=reason,
        voided_by=UUID(user_id),
        total=float(sale.total),
    )
    session.add(OutboxWrite(event=event, aggregate_type="sale", aggregate_id=str(sale.id)).to_model())

    from app.identity.models import AuthAuditLog
    from app.common.scope_cache import CATALOG, scope_bump

    session.add(
        AuthAuditLog(
            user_id=UUID(user_id),
            tenant_id=tenant_id,
            action="sale_voided",
            details=json.dumps({
                "sale_number": sale.sale_number,
                "total": float(sale.total),
                "reason": reason,
                "store_id": str(sale.store_id) if sale.store_id else None,
            }),
        )
    )
    await session.flush()
    if sale.store_id:
        await scope_bump(business_id, str(sale.store_id), (CATALOG,))
    return True


async def return_sale(
    *, session: AsyncSession, business_id: str, user_id: str, sale_id: str, reason: str
) -> dict:
    """Return a sale: mark returned, restock inventory, reverse the sale journal.

    Runs entirely inside the caller's transaction so the return is atomic.
    Mirrors ``void_sale``: the journal reversal swaps debits and credits of
    the original sale journal (a sale that never posted a journal is not
    reversed — cash was never booked, so it must not be unbooked) and the
    void audit columns are reused for the return reason/user/timestamp.

    Returns:
        Dict for ``SaleReturnResult``: ``sale_id``, ``status`` and
        ``refund_amount`` (the amount the customer actually paid).

    Raises ValueError("sale_not_found") / ValueError("sale_already_returned")
    / ValueError("sale_not_returnable").
    """
    from uuid import UUID, uuid4

    from sqlalchemy import select

    from app.accounting.models import Journal, JournalEntry
    from app.common.events.outbox import OutboxWrite
    from app.inventory.models import StockBalance, StockMovement
    from app.sales.events import sale_returned_event
    from app.sales.models import Sale, SaleItem
    from app.tips.repository import void_pending_tips_for_sale

    tenant_id = UUID(business_id)

    result = await session.execute(
        select(Sale).where(Sale.id == UUID(sale_id), Sale.tenant_id == tenant_id)
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise ValueError("sale_not_found")
    if sale.status == "returned":
        raise ValueError("sale_already_returned")
    if sale.status == "voided":
        raise ValueError("sale_not_returnable")

    items_result = await session.execute(select(SaleItem).where(SaleItem.sale_id == sale.id))
    items = list(items_result.scalars().all())

    now = datetime.now(UTC)

    # 1. Mark the sale returned (reuses the void audit columns)
    sale.status = "returned"
    sale.void_reason = reason
    sale.voided_by = UUID(user_id)
    sale.voided_at = now
    await void_pending_tips_for_sale(session, sale.id)

    # 2. Restock: return each line's qty to the sale's store, with audit trail
    for item in items:
        balance_result = await session.execute(
            select(StockBalance).where(
                StockBalance.tenant_id == tenant_id,
                StockBalance.product_id == item.product_id,
                StockBalance.store_id == sale.store_id,
            )
        )
        balance = balance_result.scalar_one_or_none()
        if not balance:
            continue
        qty = Decimal(str(item.qty))
        before = Decimal(str(balance.qty))
        balance.qty = before + qty
        session.add(
            StockMovement(
                tenant_id=tenant_id,
                product_id=item.product_id,
                store_id=sale.store_id,
                movement_type="sale_return",
                qty_change=qty,
                balance_before=before,
                balance_after=balance.qty,
                reference_type="sale",
                reference_id=sale.id,
                reason=reason[:100],
                created_by=UUID(user_id),
            )
        )

    # 3. Reverse the original sale journal (mirror: debits <-> credits)
    orig_result = await session.execute(
        select(Journal).where(
            Journal.tenant_id == tenant_id,
            Journal.reference_type == "sale",
            Journal.reference_id == sale.id,
            Journal.status == "posted",
        )
    )
    original = orig_result.scalars().first()
    if original:
        entries_result = await session.execute(
            select(JournalEntry).where(JournalEntry.journal_id == original.id)
        )
        original_entries = list(entries_result.scalars().all())

        journal_id = uuid4()
        journal_number = f"JRN-{now.strftime('%Y%m%d')}-{str(journal_id)[:8].upper()}"
        session.add(
            Journal(
                id=journal_id,
                tenant_id=tenant_id,
                journal_number=journal_number,
                description=f"Return sale {sale.sale_number}: {reason}",
                reference_id=sale.id,
                reference_type="sale_return",
                status="posted",
                posted_at=now,
                store_id=sale.store_id,
            )
        )
        for entry in original_entries:
            reversed_debit = float(entry.credit or 0)
            reversed_credit = float(entry.debit or 0)
            session.add(
                JournalEntry(
                    journal_id=journal_id,
                    tenant_id=tenant_id,
                    account_id=entry.account_id,
                    account_code=entry.account_code,
                    debit=reversed_debit,
                    credit=reversed_credit,
                    description=f"Return {sale.sale_number}: {entry.description or ''}"[:255],
                    type=entry.type,
                    status="posted",
                    posted_at=now,
                    amount=reversed_debit + reversed_credit,
                )
            )

    # 4. Emit sales.sale_returned (reporting reverses daily sales, same
    #    payload shape as sale_voided)
    event = sale_returned_event(
        tenant_id=tenant_id,
        sale_id=sale.id,
        sale_number=sale.sale_number,
        reason=reason,
        returned_by=UUID(user_id),
        total=float(sale.total),
    )
    session.add(OutboxWrite(event=event, aggregate_type="sale", aggregate_id=str(sale.id)).to_model())

    from app.identity.models import AuthAuditLog
    from app.common.scope_cache import CATALOG, scope_bump

    session.add(
        AuthAuditLog(
            user_id=UUID(user_id),
            tenant_id=tenant_id,
            action="sale_returned",
            details=json.dumps({
                "sale_number": sale.sale_number,
                "total": float(sale.total),
                "refund_amount": float(sale.amount_paid),
                "reason": reason,
                "store_id": str(sale.store_id) if sale.store_id else None,
            }),
        )
    )
    await session.flush()
    if sale.store_id:
        await scope_bump(business_id, str(sale.store_id), (CATALOG,))
    return {
        "sale_id": sale_id,
        "status": "returned",
        "refund_amount": float(sale.amount_paid),
    }


async def record_payment(
    *,
    session: AsyncSession,
    business_id: str,
    user_id: str,
    sale_id: str,
    method: str,
    amount: Decimal,
    reference=None,
    gateway_resp=None,
) -> dict:
    rows = await call(
        session,
        "api.fn_record_payment",
        p_bid=business_id,
        p_uid=user_id,
        p_sale_id=sale_id,
        p_method=method,
        p_amount=amount,
        p_reference=reference,
        p_gateway_resp=json.dumps(gateway_resp) if gateway_resp else None,
    )
    return rows[0] if rows else {}


async def split_payment(
    *, session: AsyncSession, business_id: str, user_id: str, sale_id: str, payments: list
) -> list:
    return [
        await record_payment(
            session=session,
            business_id=business_id,
            user_id=user_id,
            sale_id=sale_id,
            method=p["method"],
            amount=p["amount"],
            reference=p.get("reference"),
        )
        for p in payments
    ]


async def sales_summary(
    *, session: AsyncSession, business_id: str, from_date, to_date, group_by="day"
) -> list:
    return await call(
        session,
        "api.fn_sales_summary",
        p_bid=business_id,
        p_from=from_date,
        p_to=to_date,
        p_group_by=group_by,
    )


async def top_products(
    *, session: AsyncSession, business_id: str, from_date, to_date, limit=10
) -> list:
    return await call(
        session,
        "api.fn_top_products",
        p_bid=business_id,
        p_from=from_date,
        p_to=to_date,
        p_limit=limit,
    )


async def payment_breakdown(*, session: AsyncSession, business_id: str, from_date, to_date) -> list:
    return await call(
        session, "api.fn_payment_breakdown", p_bid=business_id, p_from=from_date, p_to=to_date
    )


async def dashboard_summary(*, session: AsyncSession, business_id: str, days: int = 30) -> dict:
    rows = await call(session, "api.fn_dashboard_summary", p_bid=business_id, p_days=days)
    return rows[0] if rows else {}


async def post_journal(
    *,
    session: AsyncSession,
    business_id: str,
    user_id: str,
    description: str,
    entries: list,
    ref_id=None,
    ref_type=None,
) -> str:
    return str(
        await call_scalar(
            session,
            "api.fn_post_journal",
            p_bid=business_id,
            p_uid=user_id,
            p_desc=description,
            p_ref_id=ref_id,
            p_ref_type=ref_type,
            p_entries=json.dumps(entries, default=str),
        )
    )


async def trial_balance(*, session: AsyncSession, business_id: str, as_at=None) -> list:
    return await call(session, "api.fn_trial_balance", p_bid=business_id, p_at=as_at)


async def profit_and_loss(*, session: AsyncSession, business_id: str, from_date, to_date) -> dict:
    rows = await call(
        session, "api.fn_profit_and_loss", p_bid=business_id, p_from=from_date, p_to=to_date
    )
    revenue = [r for r in rows if r.get("account_type") == "revenue"]
    expenses = [r for r in rows if r.get("account_type") == "expense"]
    return {
        "revenue": revenue,
        "expenses": expenses,
        "total_revenue": sum(r.get("amount", 0) for r in revenue),
        "total_expenses": sum(r.get("amount", 0) for r in expenses),
    }


async def list_journals(*, session: AsyncSession, business_id: str, page=1, page_size=50) -> dict:
    rows = await call(
        session,
        "api.fn_list_journals",
        p_bid=business_id,
        p_limit=page_size,
        p_offset=(page - 1) * page_size,
    )
    return {
        "items": rows,
        "total": rows[0].get("total_count") if rows else None,
        "page": page,
        "page_size": page_size,
    }


async def create_account(*, session: AsyncSession, business_id: str, data: dict) -> None:
    await session.execute(
        text(
            "INSERT INTO chart_of_accounts(business_id,code,name,account_type,parent_id) VALUES(:bid,:code,:name,:type,:parent) ON CONFLICT DO NOTHING"
        ),
        {
            "bid": business_id,
            "code": data["code"],
            "name": data["name"],
            "type": data["account_type"],
            "parent": data.get("parent_id"),
        },
    )


async def create_document(
    *, session: AsyncSession, business_id: str, user_id: str, data: dict
) -> dict:
    rows = await call(
        session,
        "api.fn_create_document",
        p_bid=business_id,
        p_uid=user_id,
        p_data=json.dumps(data, default=str),
    )
    return rows[0] if rows else {}


async def get_document(*, session: AsyncSession, business_id: str, doc_id: str) -> dict:
    rows = await call(session, "api.fn_get_document", p_bid=business_id, p_did=doc_id)
    if not rows:
        raise ValueError("document_not_found")
    return rows[0]


async def list_documents(
    *, session: AsyncSession, business_id: str, doc_type=None, status=None, page=1, page_size=50
) -> dict:
    rows = await call(
        session,
        "api.fn_list_documents",
        p_bid=business_id,
        p_type=doc_type,
        p_status=status,
        p_limit=page_size,
        p_offset=(page - 1) * page_size,
    )
    return {
        "items": rows,
        "total": rows[0].get("total_count") if rows else None,
        "page": page,
        "page_size": page_size,
    }


async def update_document_status(
    *, session: AsyncSession, business_id: str, user_id: str, doc_id: str, status: str
) -> bool:
    return bool(
        await call(
            session,
            "api.fn_update_doc_status",
            p_bid=business_id,
            p_uid=user_id,
            p_did=doc_id,
            p_status=status,
        )
    )


async def _sync_event_exists(session: AsyncSession, business_id: str, client_id: str) -> str | None:
    row = await session.execute(
        text(
            "SELECT id, status, error FROM sync_events "
            "WHERE tenant_id = :bid AND client_id = :cid"
        ),
        {"bid": business_id, "cid": client_id},
    )
    r = row.fetchone()
    if not r:
        return None
    return {"id": str(r[0]), "status": r[1], "error": r[2]}


async def _mark_sync_event(
    session: AsyncSession,
    event_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE sync_events SET status = :status, error = :error, "
            "applied_at = CASE WHEN :set_applied_at THEN now() ELSE applied_at END, "
            "retry_count = CASE WHEN :increment_retry THEN retry_count + 1 ELSE retry_count END "
            "WHERE id = :eid"
        ),
        {
            "status": status,
            "error": error,
            "set_applied_at": status in ("processed", "failed"),
            "increment_retry": status == "failed",
            "eid": event_id,
        },
    )


async def _apply_sale_created(
    session: AsyncSession,
    business_id: str,
    user_id: str,
    payload: dict,
) -> dict:
    from app.common.bridge import create_sale_via_service, record_payment

    client_reference = payload.get("client_reference") or payload.get("client_id")
    store_id = payload.get("store_id")
    cashier_id = payload.get("cashier_id") or user_id
    customer_name = payload.get("customer_name")
    customer_phone = payload.get("customer_phone")
    discount = payload.get("discount") or 0
    notes = payload.get("notes")
    items = payload.get("items") or []
    payment = payload.get("payment") or {}

    if not store_id:
        raise ValueError("store_id is required")
    if not items or not isinstance(items, list):
        raise ValueError("items are required")

    # Idempotent replay: a sale already created for this client reference must
    # not be duplicated when the mobile client retries after a lost response.
    if client_reference:
        row = await session.execute(
            text(
                "SELECT id, status, total FROM sales "
                "WHERE tenant_id = :bid AND client_reference = :ref"
            ),
            {"bid": business_id, "ref": client_reference},
        )
        existing = row.fetchone()
        if existing:
            return {
                "sale_id": str(existing[0]),
                "sale_status": existing[1],
                "total": float(existing[2] or 0),
                "client_reference": client_reference,
                "replayed": True,
            }

    # The owner's price-override switch is authoritative; offline payloads
    # carry captured prices but may not introduce overrides when disabled.
    settings_row = await session.execute(
        text("SELECT settings FROM tenants WHERE id = :bid"),
        {"bid": business_id},
    )
    settings_r = settings_row.fetchone()
    settings = {}
    if settings_r and settings_r[0]:
        settings = settings_r[0] if isinstance(settings_r[0], dict) else json.loads(settings_r[0] or "{}")
    allow_override = bool(settings.get("allow_price_override", False))
    for item in items:
        if item.get("extra") not in (None, 0) and not allow_override:
            raise ValueError("price_override_not_enabled")

    # Validate that every product is available in the tenant's store.
    for item in items:
        product_id = item.get("product_id")
        if not product_id:
            raise ValueError("product_id is required")
        row = await session.execute(
            text(
                "SELECT id FROM store_products "
                "WHERE tenant_id = :bid AND product_id = :pid AND store_id = :sid"
            ),
            {"bid": business_id, "pid": product_id, "sid": store_id},
        )
        if not row.fetchone():
            raise ValueError(f"product_not_found:{product_id}")

    sale_items = [
        {
            "product_id": str(i["product_id"]),
            "product_name": str(i.get("product_name") or ""),
            "qty": Decimal(str(i["qty"])),
            "unit_price": Decimal(str(i["unit_price"])),
            "extra": Decimal(str(i.get("extra") or 0)),
            "discount_pct": Decimal(str(i.get("discount_pct", 0))),
            "taxes": list(i.get("taxes") or []),
        }
        for i in items
    ]

    sale_result = await create_sale_via_service(
        tenant_id=business_id,
        cashier_id=str(cashier_id),
        items=sale_items,
        discount=Decimal(str(discount)),
        customer_name=customer_name,
        customer_phone=customer_phone,
        store_id=str(store_id),
        notes=notes,
        correlation_id=str(client_reference) if client_reference else None,
        client_reference=client_reference,
    )
    sale_id = str(sale_result["id"])
    total = float(sale_result.get("total") or 0)

    # Offline checkout is paid at the till: cash or manually recorded transfer.
    await record_payment(
        business_id=business_id,
        sale_id=sale_id,
        method=str(payment.get("method") or "cash"),
        amount=Decimal(str(total)),
        reference=str(payment.get("reference") or client_reference or sale_id),
    )

    return {
        "sale_id": sale_id,
        "sale_status": "completed",
        "total": total,
        "client_reference": client_reference,
        "replayed": False,
    }


async def _apply_event(
    session: AsyncSession,
    business_id: str,
    user_id: str,
    event: dict,
) -> dict:
    etype = event.get("event_type")
    payload = event.get("payload") or {}

    if etype == "sale.created":
        return await _apply_sale_created(session, business_id, user_id, payload)

    # Legacy types already handled by the worker path; keep them no-ops here
    # so new clients do not break on old event kinds.
    return {"event_type": etype, "applied": False, "skipped": True}


async def process_sync_batch(
    *, session: AsyncSession, business_id: str, user_id: str, events: list
) -> dict:
    """Queue client sync events and apply supported ones immediately.

    Application is synchronous so the mobile client receives an immediate
    success/failure for every event. Idempotency is provided by ``client_id``
    deduplication and by ``sales.client_reference`` for sale replays.
    """
    results: dict = {"applied": [], "failed": [], "duplicates": []}
    for event in events:
        client_id = event.get("client_id")
        if client_id:
            existing = await _sync_event_exists(session, business_id, client_id)
            if existing:
                results["duplicates"].append(
                    {
                        "client_id": client_id,
                        "event_id": existing["id"],
                        "status": existing["status"],
                        "error": existing["error"],
                    }
                )
                continue

        res = await session.execute(
            text(
                "INSERT INTO sync_events "
                "(tenant_id, user_id, client_id, event_type, payload, client_ts, status, retry_count, created_at) "
                "VALUES (:bid, :uid, :cid, :etype, CAST(:payload AS jsonb), :cts, 'pending', 0, now()) "
                "RETURNING id"
            ),
            {
                "bid": business_id,
                "uid": user_id,
                "cid": client_id,
                "etype": event["event_type"],
                "payload": json.dumps(event.get("payload") or {}),
                "cts": event.get("client_ts"),
            },
        )
        event_id_str = str(res.scalar())
        try:
            applied = await _apply_event(
                session, business_id, user_id, event
            )
            await _mark_sync_event(session, event_id_str, status="processed")
        except Exception as exc:  # noqa: BLE001 - mark and continue batch
            logger = __import__("logging").getLogger(__name__)
            logger.exception("Sync event %s failed", event_id_str)
            await _mark_sync_event(session, event_id_str, status="failed", error=str(exc))
            results["failed"].append(
                {"client_id": client_id, "event_id": event_id_str, "error": str(exc)}
            )
            continue

        results["applied"].append(
            {
                "client_id": client_id,
                "event_id": event_id_str,
                "event_type": event.get("event_type"),
                "applied": applied,
            }
        )

    return results


async def queue_server_event(
    *, session: AsyncSession, business_id: str, tenant_id: str, event_type: str, payload: dict
) -> str:
    res = await session.execute(
        text(
            "INSERT INTO sync_events "
            "(tenant_id, user_id, client_id, event_type, payload, client_ts, status, retry_count, created_at) "
            "VALUES (:bid, NULL, NULL, :etype, CAST(:payload AS jsonb), NULL, 'pending', 0, now()) "
            "RETURNING id"
        ),
        {
            "bid": business_id,
            "etype": event_type,
            "payload": json.dumps(payload),
        },
    )
    return str(res.scalar())


async def get_pending_events(
    *, session: AsyncSession, business_id: str, since: datetime, limit: int = 50
) -> dict:
    rows = await session.execute(
        text(
            "SELECT id, event_type, payload, client_id, client_ts, created_at, status, error "
            "FROM sync_events WHERE tenant_id = :bid AND status = 'pending' "
            "ORDER BY created_at ASC LIMIT :limit"
        ),
        {"bid": business_id, "limit": limit},
    )
    events = []
    cursor = since
    for row in rows.mappings():
        events.append(
            {
                "id": str(row["id"]),
                "event_type": row["event_type"],
                "payload": row["payload"],
                "client_id": row.get("client_id"),
                "client_ts": row.get("client_ts"),
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )
        cursor = row["created_at"]
    return {"events": events, "cursor": cursor}


async def get_pending_event_count(*, session: AsyncSession, business_id: str) -> int:
    row = await session.execute(
        text(
            "SELECT COUNT(*) FROM sync_events WHERE tenant_id = :bid AND status = 'pending'"
        ),
        {"bid": business_id},
    )
    return int(row.scalar() or 0)


# ── Offline Sync Extensions ───────────────────────────────────────────────────

from uuid import UUID as PyUUID

ENTITY_TABLES = {
    "stores": "stores",
    "store_products": "store_products",
    "products": "products",
    "categories": "categories",
    "sales": "sales",
    "sale_items": "sale_items",
    "stock_balances": "stock_balances",
    "customers": "customers",
}

ENTITY_TIMESTAMP_COLUMN = {
    "stores": "updated_at",
    "store_products": "updated_at",
    "products": "updated_at",
    "categories": "created_at",
    "sales": "updated_at",
    "sale_items": "sales.updated_at",
    "stock_balances": "updated_at",
    "customers": "updated_at",
}

ENTITY_FROM = {
    "sale_items": "sale_items si JOIN sales s ON s.id = si.sale_id",
}

ENTITY_SELECT_COLUMNS = {
    "sale_items": "si.*, s.id AS sale_id, s.tenant_id, s.updated_at AS updated_at",
}


def _serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, PyUUID):
        return str(value)
    return value


async def get_data_snapshot(
    *,
    session: AsyncSession,
    business_id: str,
    entity_types: list[str] | None = None,
    since: datetime | None = None,
) -> dict:
    """Return a full data snapshot for initial sync or incremental refresh.

    Queries the raw tables for the requested entity types, filtered by
    tenant (business_id).  If *since* is provided only rows updated or created
    at/after *since* are returned. The cursor is captured before the query so
    concurrent writes cannot be skipped.
    """
    if entity_types is None:
        entity_types = list(ENTITY_TABLES.keys())

    cursor = datetime.now(UTC)
    entities = []
    for etype in entity_types:
        table = ENTITY_TABLES.get(etype)
        if not table:
            continue

        from_clause = ENTITY_FROM.get(etype, table)
        cols = ENTITY_SELECT_COLUMNS.get(etype, "*")
        ts_col = ENTITY_TIMESTAMP_COLUMN.get(etype, "updated_at")

        if since:
            rows_result = await session.execute(
                text(
                    f"SELECT {cols} FROM {from_clause} "
                    f"WHERE tenant_id = :bid AND {ts_col} >= :since"
                ),
                {"bid": PyUUID(business_id), "since": since},
            )
        else:
            rows_result = await session.execute(
                text(f"SELECT {cols} FROM {from_clause} WHERE tenant_id = :bid"),
                {"bid": PyUUID(business_id)},
            )

        keys = list(rows_result.keys())
        for row in rows_result.fetchall():
            d = {k: _serialize_value(v) for k, v in zip(keys, row)}
            entities.append(
                {
                    "entity_type": etype,
                    "id": str(d.get("id", "")),
                    "data": {
                        k: v
                        for k, v in d.items()
                        if k not in ("id", "tenant_id")
                    },
                    "version": 1,
                    "updated_at": d.get("updated_at"),
                }
            )

    return {
        "entities": entities,
        "cursor": cursor.isoformat(),
        "total": len(entities),
    }


async def full_sync(
    *,
    session: AsyncSession,
    business_id: str,
    user_id: str,
    events: list[dict],
    last_cursor: str | None = None,
) -> dict:
    """Process client events and return server events since the last cursor.

    Combines ``process_sync_batch`` and ``get_pending_events`` in a single
    round-trip so the client can push and pull in one call.
    """
    # Process incoming client events
    batch_result = await process_sync_batch(
        session=session,
        business_id=business_id,
        user_id=user_id,
        events=events,
    )

    # Fetch server events since last cursor
    if last_cursor:
        try:
            since_dt = datetime.fromisoformat(last_cursor.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            since_dt = datetime.fromisoformat("1970-01-01T00:00:00Z")
    else:
        since_dt = datetime.fromisoformat("1970-01-01T00:00:00Z")

    pending = await get_pending_events(
        session=session,
        business_id=business_id,
        since=since_dt,
        limit=500,
    )

    # Build cursor from the latest event timestamp
    cursor = pending.get("cursor") or datetime.now(UTC).isoformat()

    return {
        "processed": len(batch_result.get("applied", [])),
        "failed": len(batch_result.get("failed", [])),
        "errors": batch_result.get("failed", []),
        "server_events": pending.get("events", []),
        "cursor": str(cursor),
    }


async def get_sync_cursor(
    *, session: AsyncSession, business_id: str, device_id: str
) -> dict:
    """Retrieve the last sync cursor for a device."""
    rows = await session.execute(
        text(
            "SELECT last_cursor, updated_at FROM sync_cursors "
            "WHERE tenant_id = :bid AND device_id = :did"
        ),
        {"bid": PyUUID(business_id), "did": device_id},
    )
    row = rows.fetchone()
    if row:
        return {
            "device_id": device_id,
            "last_cursor": row[0] or "",
            "updated_at": row[1],
        }
    return {"device_id": device_id, "last_cursor": "", "updated_at": None}


async def update_sync_cursor(
    *, session: AsyncSession, business_id: str, device_id: str, last_cursor: str
) -> dict:
    """Store or update the last sync cursor for a device."""
    now = datetime.now(UTC)
    await session.execute(
        text(
            "INSERT INTO sync_cursors (tenant_id, device_id, last_cursor, updated_at) "
            "VALUES (:bid, :did, :cursor, :now) "
            "ON CONFLICT (tenant_id, device_id) "
            "DO UPDATE SET last_cursor = :cursor, updated_at = :now"
        ),
        {
            "bid": PyUUID(business_id),
            "did": device_id,
            "cursor": last_cursor,
            "now": now,
        },
    )
    return {
        "device_id": device_id,
        "last_cursor": last_cursor,
        "updated_at": now,
    }
