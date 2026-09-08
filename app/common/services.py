import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.helpers import call, call_scalar, exec_fn


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


async def void_sale(
    *, session: AsyncSession, business_id: str, user_id: str, sale_id: str, reason: str
) -> bool:
    return bool(
        await call(
            session,
            "api.fn_void_sale",
            p_bid=business_id,
            p_uid=user_id,
            p_sale_id=sale_id,
            p_reason=reason,
        )
    )


async def return_sale(
    *, session: AsyncSession, business_id: str, user_id: str, sale_id: str, reason: str
) -> dict:
    rows = await call(
        session,
        "api.fn_return_sale",
        p_bid=business_id,
        p_uid=user_id,
        p_sale_id=sale_id,
        p_reason=reason,
    )
    return rows[0] if rows else {}


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


async def process_sync_batch(
    *, session: AsyncSession, business_id: str, user_id: str, events: list
) -> dict:
    results: dict = {"applied": [], "failed": [], "duplicates": []}
    for event in events:
        client_id = event.get("client_id")
        if client_id:
            dup = await call_scalar(
                session,
                "api.fn_check_event_dup",
                p_bid=business_id,
                p_client_id=client_id,
            )
            if dup:
                results["duplicates"].append({"client_id": client_id, "event_id": str(dup)})
                continue
        eid = await call_scalar(
            session,
            "api.fn_queue_event",
            p_bid=business_id,
            p_uid=user_id,
            p_type=event["event_type"],
            p_payload=json.dumps(event["payload"]),
            p_client_id=client_id,
            p_client_ts=event.get("client_ts"),
        )
        results["applied"].append({"client_id": client_id, "event_id": str(eid)})
    return results


async def queue_server_event(
    *, session: AsyncSession, business_id: str, tenant_id: str, event_type: str, payload: dict
) -> str:
    return str(
        await call_scalar(
            session,
            "api.fn_queue_event",
            p_bid=business_id,
            p_uid=None,
            p_type=event_type,
            p_payload=json.dumps(payload),
            p_client_id=None,
            p_client_ts=None,
            p_tenant_id=tenant_id,
        )
    )


async def get_pending_events(
    *, session: AsyncSession, business_id: str, since: datetime, limit: int = 50
) -> dict:
    rows = await call(
        session,
        "api.fn_pending_events",
        p_bid=business_id,
        p_since=since,
        p_limit=limit,
    )
    events = []
    cursor = since
    for row in rows:
        events.append(
            {
                "event_type": row["event_type"],
                "payload": row["payload"],
                "client_id": row.get("client_id"),
                "client_ts": row.get("client_ts"),
            }
        )
        cursor = row["created_at"]
    return {"events": events, "cursor": cursor}


async def get_pending_event_count(*, session: AsyncSession, business_id: str) -> int:
    return await call_scalar(session, "api.fn_pending_event_count", p_bid=business_id) or 0
