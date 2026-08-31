"""Analytics service module.

This module provides analytics queries for dashboards and reporting.
Results are cached in Redis with short TTLs for near-real-time updates.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Date, cast, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import cache, cached
from app.accounting.models import CommissionLedger, JournalEntry
from app.catalog.models import Product
from app.documents.models import Document
from app.identity.models import User
from app.payments.models import Payment
from app.reporting.models import (
    MvCashierPerformance,
    MvCustomerSummary,
    MvDailySales,
    MvInventoryStatus,
    MvPaymentMethod,
    MvProductRanking,
    MvStoreInventory,
    MvStoreProductRanking,
    MvStoreSales,
)
from app.sales.models import Sale, SaleItem


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _days_ago(n: int) -> date:
    return _today() - timedelta(days=n)


def _pct(curr: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return round((curr - prev) / prev * 100, 1)


@cached(prefix="analytics:dashboard", ttl=60, key_func=lambda *, session, tenant_id, days=30, **kw: f"{tenant_id}:{days}")
async def dashboard_summary(*, session: AsyncSession, tenant_id: str, days: int = 30) -> dict:
    tid = UUID(tenant_id)
    current_start = _days_ago(days)
    prev_start = _days_ago(days * 2)
    prev_end = current_start

    cur = (
        await session.execute(
            select(
                func.coalesce(func.sum(MvDailySales.total_revenue), 0),
                func.coalesce(func.sum(MvDailySales.total_sales), 0),
            ).where(
                MvDailySales.tenant_id == tid,
                MvDailySales.date >= current_start,
            )
        )
    ).one()
    prev = (
        await session.execute(
            select(
                func.coalesce(func.sum(MvDailySales.total_revenue), 0),
                func.coalesce(func.sum(MvDailySales.total_sales), 0),
            ).where(
                MvDailySales.tenant_id == tid,
                MvDailySales.date >= prev_start,
                MvDailySales.date < prev_end,
            )
        )
    ).one()

    cur_rev = float(cur[0])
    prev_rev = float(prev[0])
    cur_cnt = int(cur[1])
    prev_cnt = int(prev[1])
    cur_aov = round(cur_rev / cur_cnt, 2) if cur_cnt else 0
    prev_aov = round(prev_rev / prev_cnt, 2) if prev_cnt else 0

    top = (
        (
            await session.execute(
                select(
                    MvProductRanking.product_name.label("name"),
                    func.sum(MvProductRanking.units_sold).label("qty_sold"),
                    func.sum(MvProductRanking.revenue).label("revenue"),
                )
                .where(
                    MvProductRanking.tenant_id == tid,
                    MvProductRanking.date >= current_start,
                )
                .group_by(MvProductRanking.product_name)
                .order_by(desc("revenue"))
                .limit(1)
            )
        )
        .mappings()
        .first()
    )

    low = (
        await session.execute(
            select(func.count(MvInventoryStatus.product_id)).where(
                MvInventoryStatus.tenant_id == tid,
                or_(
                    MvInventoryStatus.status == "low_stock",
                    MvInventoryStatus.status == "out_of_stock",
                ),
            )
        )
    ).scalar() or 0

    active_users = (
        await session.execute(
            select(func.count(User.id)).where(
                User.tenant_id == tid,
                User.status == "active",
                User.last_login_at >= current_start,
            )
        )
    ).scalar() or 0

    coll = (
        await session.execute(
            select(
                func.coalesce(func.sum(MvDailySales.total_revenue), 0),
                func.coalesce(
                    func.sum(MvDailySales.total_revenue)
                    + func.sum(MvDailySales.total_discounts)
                    + func.sum(MvDailySales.total_tax),
                    0,
                ),
            ).where(
                MvDailySales.tenant_id == tid,
                MvDailySales.date >= current_start,
            )
        )
    ).one()
    paid = float(coll[0])
    tot = float(coll[1]) if coll[1] else 1
    collection_rate = round(paid / tot * 100, 1) if tot else 0

    pending_docs = (
        await session.execute(
            select(func.count(Document.id)).where(
                Document.tenant_id == tid,
                Document.status.in_(["draft", "sent"]),
            )
        )
    ).scalar() or 0

    return {
        "revenue": {
            "current": cur_rev,
            "previous": prev_rev,
            "change_pct": _pct(cur_rev, prev_rev),
        },
        "sales_count": {
            "current": cur_cnt,
            "previous": prev_cnt,
            "change_pct": _pct(cur_cnt, prev_cnt),
        },
        "avg_order_value": {
            "current": cur_aov,
            "previous": prev_aov,
            "change_pct": _pct(cur_aov, prev_aov),
        },
        "top_product": dict(top) if top else None,
        "low_stock_count": int(low),
        "active_users": int(active_users),
        "pending_documents": int(pending_docs),
        "payment_collection_rate": collection_rate,
    }


@cached(prefix="analytics:sales", ttl=60, key_func=lambda *, session, tenant_id, from_date, to_date, group_by="day", **kw: f"{tenant_id}:{from_date}:{to_date}:{group_by}")
async def sales_summary(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    group_by: str = "day",
) -> dict:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvDailySales.date.label("period"),
            MvDailySales.total_revenue.label("revenue"),
            MvDailySales.total_sales.label("sales_count"),
            MvDailySales.total_discounts.label("discount_total"),
            MvDailySales.total_tax.label("tax_total"),
        )
        .where(
            MvDailySales.tenant_id == tid,
            MvDailySales.date >= d_from,
            MvDailySales.date <= d_to,
        )
        .order_by(MvDailySales.date)
    )

    items = []
    tot_rev = tot_cnt = tot_disc = tot_tax = 0
    for r in rows.mappings():
        rev = float(r["revenue"])
        cnt = int(r["sales_count"])
        items.append(
            {
                "period": r["period"].isoformat(),
                "revenue": rev,
                "sales_count": cnt,
                "avg_order_value": round(rev / cnt, 2) if cnt else 0,
                "discount_total": float(r["discount_total"]),
                "tax_total": float(r["tax_total"]),
            }
        )
        tot_rev += rev
        tot_cnt += cnt
        tot_disc += float(r["discount_total"])
        tot_tax += float(r["tax_total"])

    return {
        "items": items,
        "totals": {
            "revenue": tot_rev,
            "sales_count": tot_cnt,
            "discount_total": tot_disc,
            "tax_total": tot_tax,
        },
    }


@cached(prefix="analytics:top_products", ttl=60, key_func=lambda *, session, tenant_id, from_date, to_date, limit=10, **kw: f"{tenant_id}:{from_date}:{to_date}:{limit}")
async def top_products(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    limit: int = 10,
) -> list:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvProductRanking.product_id,
            MvProductRanking.product_name,
            MvProductRanking.sku,
            func.sum(MvProductRanking.units_sold).label("qty_sold"),
            func.sum(MvProductRanking.revenue).label("revenue"),
            func.avg(MvProductRanking.avg_selling_price).label("avg_selling_price"),
            func.avg(MvProductRanking.cost_price).label("cost_price"),
        )
        .where(
            MvProductRanking.tenant_id == tid,
            MvProductRanking.date >= d_from,
            MvProductRanking.date <= d_to,
        )
        .group_by(
            MvProductRanking.product_id,
            MvProductRanking.product_name,
            MvProductRanking.sku,
        )
        .order_by(desc("revenue"))
        .limit(limit)
    )

    items = []
    for r in rows.mappings():
        rev = float(r["revenue"])
        sp = float(r["avg_selling_price"]) if r["avg_selling_price"] else 0
        cp = float(r["cost_price"]) if r["cost_price"] else 0
        margin = round((sp - cp) / sp * 100, 1) if sp else 0
        items.append(
            {
                "product_id": str(r["product_id"]),
                "product_name": r["product_name"],
                "sku": r["sku"],
                "qty_sold": float(r["qty_sold"]),
                "revenue": rev,
                "avg_selling_price": round(sp, 2),
                "margin_pct": margin,
            }
        )
    return items


@cached(prefix="analytics:payments", ttl=60, key_func=lambda *, session, tenant_id, from_date, to_date, **kw: f"{tenant_id}:{from_date}:{to_date}")
async def payment_breakdown(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvPaymentMethod.method,
            func.sum(MvPaymentMethod.payment_count).label("count"),
            func.sum(MvPaymentMethod.total_amount).label("total"),
        )
        .where(
            MvPaymentMethod.tenant_id == tid,
            MvPaymentMethod.date >= d_from,
            MvPaymentMethod.date <= d_to,
        )
        .group_by(MvPaymentMethod.method)
        .order_by(desc("total"))
    )

    items = []
    tot_count = 0
    tot_amount = 0
    for r in rows.mappings():
        cnt = int(r["count"])
        amt = float(r["total"])
        items.append({"method": r["method"], "count": cnt, "total": amt, "percentage": 0})
        tot_count += cnt
        tot_amount += amt

    for item in items:
        item["percentage"] = round(item["total"] / tot_amount * 100, 1) if tot_amount else 0

    return {"items": items, "total_count": tot_count, "total_amount": tot_amount}


async def cashier_performance(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    limit: int = 20,
) -> list:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvCashierPerformance.user_id,
            func.sum(MvCashierPerformance.sales_count).label("sales_count"),
            func.sum(MvCashierPerformance.total_revenue).label("total_revenue"),
            func.avg(MvCashierPerformance.avg_transaction).label("avg_transaction"),
            func.sum(MvCashierPerformance.void_count).label("void_count"),
        )
        .where(
            MvCashierPerformance.tenant_id == tid,
            MvCashierPerformance.date >= d_from,
            MvCashierPerformance.date <= d_to,
        )
        .group_by(MvCashierPerformance.user_id)
        .order_by(desc("total_revenue"))
        .limit(limit)
    )

    items = []
    for r in rows.mappings():
        items.append(
            {
                "user_id": str(r["user_id"]),
                "sales_count": int(r["sales_count"]),
                "total_revenue": float(r["total_revenue"]),
                "avg_transaction": round(float(r["avg_transaction"]), 2)
                if r["avg_transaction"]
                else 0,
                "void_count": int(r["void_count"]),
            }
        )
    return items


@cached(prefix="analytics:inventory", ttl=60, key_func=lambda *, session, tenant_id, alert_type=None, **kw: f"{tenant_id}:{alert_type or 'all'}")
async def inventory_alerts(
    *,
    session: AsyncSession,
    tenant_id: str,
    alert_type: str | None = None,
) -> dict:
    tid = UUID(tenant_id)

    base = select(MvInventoryStatus).where(MvInventoryStatus.tenant_id == tid)
    if alert_type and alert_type != "all":
        base = base.where(MvInventoryStatus.status == alert_type)

    rows = await session.execute(base.order_by(MvInventoryStatus.product_name))
    items = []
    for r in rows.scalars():
        items.append(
            {
                "product_id": str(r.product_id),
                "product_name": r.product_name,
                "sku": r.sku,
                "current_qty": float(r.current_qty),
                "reorder_point": float(r.reorder_point) if r.reorder_point else 0,
                "status": r.status,
            }
        )

    counts = (
        await session.execute(
            select(
                MvInventoryStatus.status,
                func.count(MvInventoryStatus.product_id),
            )
            .where(MvInventoryStatus.tenant_id == tid)
            .group_by(MvInventoryStatus.status)
        )
    ).all()

    summary = {
        "total_products": 0,
        "in_stock": 0,
        "low_stock": 0,
        "out_of_stock": 0,
        "overstocked": 0,
    }
    for status, cnt in counts:
        summary["total_products"] += cnt
        key = status if status in summary else "in_stock"
        if status in summary:
            summary[status] = cnt
        else:
            summary["in_stock"] += cnt

    return {"summary": summary, "items": items}


async def profit_loss(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    group_by: str = "day",
) -> dict:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    sales_rows = await session.execute(
        select(
            MvDailySales.date.label("period"),
            MvDailySales.total_revenue,
            MvDailySales.total_discounts,
        )
        .where(
            MvDailySales.tenant_id == tid,
            MvDailySales.date >= d_from,
            MvDailySales.date <= d_to,
        )
        .order_by(MvDailySales.date)
    )

    sales_map = {}
    for r in sales_rows.mappings():
        sales_map[r["period"]] = {
            "revenue": float(r["total_revenue"]),
            "discounts": float(r["total_discounts"]),
        }

    cogs_rows = await session.execute(
        select(
            cast(SaleItem.sale_id, text("date(created_at)")).label("period"),
            func.sum(SaleItem.qty * Product.cost_price).label("cogs"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .join(Product, Product.id == SaleItem.product_id)
        .where(Sale.tenant_id == tid, Sale.status != "voided")
        .where(
            cast(Sale.created_at, Date) >= d_from,
            cast(Sale.created_at, Date) <= d_to,
        )
        .group_by(text("1"))
    )
    cogs_map = {r["period"]: float(r["cogs"]) for r in cogs_rows.mappings()}

    expense_rows = await session.execute(
        select(
            cast(JournalEntry.posted_at, Date).label("period"),
            func.sum(JournalEntry.amount).label("expenses"),
        )
        .where(
            JournalEntry.tenant_id == tid,
            JournalEntry.type == "expense",
            JournalEntry.status == "posted",
            cast(JournalEntry.posted_at, Date) >= d_from,
            cast(JournalEntry.posted_at, Date) <= d_to,
        )
        .group_by(text("1"))
    )
    expense_map = {r["period"]: float(r["expenses"]) for r in expense_rows.mappings()}

    periods = sorted(set(list(sales_map.keys()) + list(cogs_map.keys()) + list(expense_map.keys())))
    items = []
    tot_rev = tot_cogs = tot_exp = 0
    for p in periods:
        rev = sales_map.get(p, {}).get("revenue", 0)
        cogs = cogs_map.get(p, 0)
        exp = expense_map.get(p, 0)
        gp = rev - cogs
        np_ = gp - exp
        items.append(
            {
                "period": p.isoformat(),
                "revenue": rev,
                "cogs": cogs,
                "gross_profit": gp,
                "gross_margin_pct": round(gp / rev * 100, 1) if rev else 0,
                "expenses": exp,
                "net_profit": np_,
                "net_margin_pct": round(np_ / rev * 100, 1) if rev else 0,
            }
        )
        tot_rev += rev
        tot_cogs += cogs
        tot_exp += exp

    gp = tot_rev - tot_cogs
    np_ = gp - tot_exp
    return {
        "items": items,
        "totals": {
            "revenue": tot_rev,
            "cogs": tot_cogs,
            "gross_profit": gp,
            "gross_margin_pct": round(gp / tot_rev * 100, 1) if tot_rev else 0,
            "expenses": tot_exp,
            "net_profit": np_,
            "net_margin_pct": round(np_ / tot_rev * 100, 1) if tot_rev else 0,
        },
    }


async def customer_insights(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    limit: int = 20,
) -> dict:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    since = (
        await session.execute(
            select(
                func.count(MvCustomerSummary.customer_key).label("unique_customers"),
                func.coalesce(func.sum(MvCustomerSummary.total_purchases), 0).label(
                    "total_purchases"
                ),
                func.coalesce(func.sum(MvCustomerSummary.total_revenue), 0).label("total_revenue"),
            ).where(MvCustomerSummary.tenant_id == tid)
        )
    ).one()
    unique = int(since[0])
    total_purchases = int(since[1])
    total_spend = float(since[2])

    period_customers = (
        (
            await session.execute(
                select(
                    MvCustomerSummary.customer_key,
                    MvCustomerSummary.first_purchase,
                    MvCustomerSummary.last_purchase,
                    MvCustomerSummary.total_purchases,
                    MvCustomerSummary.total_revenue,
                    MvCustomerSummary.avg_order_value,
                )
                .where(
                    MvCustomerSummary.tenant_id == tid,
                    MvCustomerSummary.last_purchase >= d_from,
                    MvCustomerSummary.last_purchase <= d_to,
                )
                .order_by(desc(MvCustomerSummary.total_revenue))
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )

    new_count = sum(
        1
        for r in period_customers
        if r["first_purchase"] >= datetime.combine(d_from, datetime.min.time(), tzinfo=timezone.utc)
    )
    returning = len(period_customers) - new_count

    top = []
    for r in period_customers:
        top.append(
            {
                "customer_name": r["customer_key"],
                "total_purchases": int(r["total_purchases"]),
                "total_revenue": float(r["total_revenue"]),
                "avg_order_value": round(float(r["avg_order_value"]), 2)
                if r["avg_order_value"]
                else 0,
                "last_purchase": r["last_purchase"].date().isoformat(),
            }
        )

    return {
        "summary": {
            "unique_customers": unique,
            "new_customers": new_count,
            "returning_customers": returning,
            "avg_customer_value": round(total_spend / unique, 2) if unique else 0,
            "repeat_purchase_rate": round(returning / len(period_customers) * 100, 1)
            if period_customers
            else 0,
        },
        "top_customers": top,
    }


async def document_summary(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
    doc_type: str | None = None,
) -> dict:
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    base = select(Document).where(
        Document.tenant_id == tid,
        cast(Document.created_at, Date) >= d_from,
        cast(Document.created_at, Date) <= d_to,
    )
    if doc_type and doc_type != "all":
        base = base.where(Document.doc_type == doc_type)

    rows = base.subquery()
    stats = (
        await session.execute(
            select(
                func.count(rows.c.id).label("total"),
                func.coalesce(func.sum(rows.c.total), 0).label("total_amount"),
                func.count(rows.c.id)
                .filter(rows.c.status.in_(["paid", "completed"]))
                .label("paid_count"),
                func.coalesce(
                    func.sum(rows.c.total).filter(rows.c.status.in_(["paid", "completed"])), 0
                ).label("paid_amount"),
                func.count(rows.c.id)
                .filter(rows.c.status.in_(["overdue", "sent"]))
                .label("unpaid_count"),
                func.coalesce(
                    func.sum(rows.c.total).filter(rows.c.status.in_(["overdue", "sent"])), 0
                ).label("unpaid_amount"),
            )
        )
    ).one()

    total = int(stats[0])
    total_amt = float(stats[1])
    paid_cnt = int(stats[2])
    paid_amt = float(stats[3])
    overdue_cnt = int(stats[4])
    overdue_amt = float(stats[5])

    aging_rows = await session.execute(
        select(
            Document.id,
            Document.total,
            Document.due_date,
            Document.status,
        )
        .where(
            Document.tenant_id == tid,
            Document.status.in_(["sent", "overdue", "draft"]),
            Document.due_date.isnot(None),
            cast(Document.created_at, Date) >= d_from,
            cast(Document.created_at, Date) <= d_to,
        )
        .order_by(Document.due_date)
    )

    now = datetime.now(timezone.utc)
    buckets = {
        "0-30 days": {"count": 0, "amount": 0},
        "31-60 days": {"count": 0, "amount": 0},
        "61-90 days": {"count": 0, "amount": 0},
        "90+ days": {"count": 0, "amount": 0},
    }
    for r in aging_rows.mappings():
        due = r["due_date"]
        if due:
            days_overdue = (now - due).days
            if days_overdue < 0:
                continue
            key = (
                "90+ days"
                if days_overdue > 90
                else (
                    "61-90 days"
                    if days_overdue > 60
                    else ("31-60 days" if days_overdue > 30 else "0-30 days")
                )
            )
            buckets[key]["count"] += 1
            buckets[key]["amount"] += float(r["total"])

    return {
        "summary": {
            "total_documents": total,
            "total_amount": total_amt,
            "paid": paid_cnt,
            "paid_amount": paid_amt,
            "overdue": overdue_cnt,
            "overdue_amount": overdue_amt,
            "collection_rate": round(paid_amt / total_amt * 100, 1) if total_amt else 0,
        },
        "aging": [
            {"bucket": k, "count": v["count"], "amount": v["amount"]} for k, v in buckets.items()
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Store-level analytics
# ═══════════════════════════════════════════════════════════════════════════════


async def store_overview(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    """All stores at a glance — revenue, sales count, avg order value per store."""
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)
    days = (d_to - d_from).days or 1
    prev_from = d_from - timedelta(days=days)

    # Current period
    cur_rows = await session.execute(
        select(
            MvStoreSales.store_id,
            MvStoreSales.store_name,
            func.sum(MvStoreSales.total_revenue).label("revenue"),
            func.sum(MvStoreSales.total_sales).label("sales_count"),
            func.avg(MvStoreSales.avg_order_value).label("avg_order_value"),
        )
        .where(
            MvStoreSales.tenant_id == tid,
            MvStoreSales.date >= d_from,
            MvStoreSales.date <= d_to,
        )
        .group_by(MvStoreSales.store_id, MvStoreSales.store_name)
        .order_by(desc("revenue"))
    )

    current = {}
    for r in cur_rows.mappings():
        sid = str(r["store_id"])
        current[sid] = {
            "store_id": sid,
            "store_name": r["store_name"],
            "revenue": float(r["revenue"]),
            "sales_count": int(r["sales_count"]),
            "avg_order_value": round(float(r["avg_order_value"]), 2) if r["avg_order_value"] else 0,
        }

    # Previous period
    prev_rows = await session.execute(
        select(
            MvStoreSales.store_id,
            func.sum(MvStoreSales.total_revenue).label("revenue"),
            func.sum(MvStoreSales.total_sales).label("sales_count"),
        )
        .where(
            MvStoreSales.tenant_id == tid,
            MvStoreSales.date >= prev_from,
            MvStoreSales.date < d_from,
        )
        .group_by(MvStoreSales.store_id)
    )

    prev = {}
    for r in prev_rows.mappings():
        prev[str(r["store_id"])] = {
            "revenue": float(r["revenue"]),
            "sales_count": int(r["sales_count"]),
        }

    # Top product per store
    top_rows = await session.execute(
        select(
            MvStoreProductRanking.store_id,
            MvStoreProductRanking.product_name,
            func.sum(MvStoreProductRanking.units_sold).label("qty_sold"),
        )
        .where(
            MvStoreProductRanking.tenant_id == tid,
            MvStoreProductRanking.date >= d_from,
            MvStoreProductRanking.date <= d_to,
        )
        .group_by(MvStoreProductRanking.store_id, MvStoreProductRanking.product_name)
        .order_by(desc("qty_sold"))
    )

    top_products: dict[str, dict] = {}
    for r in top_rows.mappings():
        sid = str(r["store_id"])
        if sid not in top_products:
            top_products[sid] = {"name": r["product_name"], "qty_sold": float(r["qty_sold"])}

    # Merge
    items = []
    for sid, data in current.items():
        prev_data = prev.get(sid, {"revenue": 0, "sales_count": 0})
        data["revenue_change_pct"] = _pct(data["revenue"], prev_data["revenue"])
        data["sales_change_pct"] = _pct(data["sales_count"], prev_data["sales_count"])
        data["top_product"] = top_products.get(sid)
        items.append(data)

    return {"items": items}


async def store_sales_detail(
    *,
    session: AsyncSession,
    tenant_id: str,
    store_id: str,
    from_date: str,
    to_date: str,
    group_by: str = "day",
) -> dict:
    """Detailed sales for a single store over time — for line/bar charts."""
    tid = UUID(tenant_id)
    sid = UUID(store_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvStoreSales.date.label("period"),
            MvStoreSales.total_revenue.label("revenue"),
            MvStoreSales.total_sales.label("sales_count"),
            MvStoreSales.avg_order_value,
            MvStoreSales.voided_count,
            MvStoreSales.cash_amount,
            MvStoreSales.card_amount,
            MvStoreSales.transfer_amount,
        )
        .where(
            MvStoreSales.tenant_id == tid,
            MvStoreSales.store_id == sid,
            MvStoreSales.date >= d_from,
            MvStoreSales.date <= d_to,
        )
        .order_by(MvStoreSales.date)
    )

    items = []
    tot_rev = tot_cnt = 0
    for r in rows.mappings():
        rev = float(r["revenue"])
        cnt = int(r["sales_count"])
        items.append({
            "period": r["period"].isoformat(),
            "revenue": rev,
            "sales_count": cnt,
            "avg_order_value": round(float(r["avg_order_value"]), 2) if r["avg_order_value"] else 0,
            "voided_count": int(r["voided_count"]),
            "cash_amount": float(r["cash_amount"]),
            "card_amount": float(r["card_amount"]),
            "transfer_amount": float(r["transfer_amount"]),
        })
        tot_rev += rev
        tot_cnt += cnt

    return {
        "store_id": store_id,
        "items": items,
        "totals": {
            "revenue": tot_rev,
            "sales_count": tot_cnt,
            "avg_order_value": round(tot_rev / tot_cnt, 2) if tot_cnt else 0,
        },
    }


async def store_product_rankings(
    *,
    session: AsyncSession,
    tenant_id: str,
    store_id: str,
    from_date: str,
    to_date: str,
    limit: int = 10,
) -> list:
    """Top products in a specific store — for ranking charts."""
    tid = UUID(tenant_id)
    sid = UUID(store_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvStoreProductRanking.product_id,
            MvStoreProductRanking.product_name,
            MvStoreProductRanking.sku,
            func.sum(MvStoreProductRanking.units_sold).label("qty_sold"),
            func.sum(MvStoreProductRanking.revenue).label("revenue"),
            func.avg(MvStoreProductRanking.avg_selling_price).label("avg_selling_price"),
        )
        .where(
            MvStoreProductRanking.tenant_id == tid,
            MvStoreProductRanking.store_id == sid,
            MvStoreProductRanking.date >= d_from,
            MvStoreProductRanking.date <= d_to,
        )
        .group_by(
            MvStoreProductRanking.product_id,
            MvStoreProductRanking.product_name,
            MvStoreProductRanking.sku,
        )
        .order_by(desc("revenue"))
        .limit(limit)
    )

    items = []
    for r in rows.mappings():
        rev = float(r["revenue"])
        sp = float(r["avg_selling_price"]) if r["avg_selling_price"] else 0
        items.append({
            "product_id": str(r["product_id"]),
            "product_name": r["product_name"],
            "sku": r["sku"],
            "qty_sold": float(r["qty_sold"]),
            "revenue": rev,
            "avg_selling_price": round(sp, 2),
        })
    return items


async def store_comparison(
    *,
    session: AsyncSession,
    tenant_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    """Cross-store comparison — series data for line charts."""
    tid = UUID(tenant_id)
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    rows = await session.execute(
        select(
            MvStoreSales.store_id,
            MvStoreSales.store_name,
            MvStoreSales.date.label("period"),
            MvStoreSales.total_revenue.label("revenue"),
            MvStoreSales.total_sales.label("sales_count"),
        )
        .where(
            MvStoreSales.tenant_id == tid,
            MvStoreSales.date >= d_from,
            MvStoreSales.date <= d_to,
        )
        .order_by(MvStoreSales.date, MvStoreSales.store_name)
    )

    store_names: dict[str, str] = {}
    periods: dict[str, dict[str, dict]] = {}

    for r in rows.mappings():
        sid = str(r["store_id"])
        sname = r["store_name"] or sid
        store_names[sid] = sname
        p = r["period"].isoformat()

        if p not in periods:
            periods[p] = {}
        periods[p][sname] = {
            "revenue": float(r["revenue"]),
            "sales_count": int(r["sales_count"]),
        }

    # Build series for charting
    stores = sorted(set(store_names.values()))
    series = []
    for period in sorted(periods.keys()):
        row = {"period": period}
        for sname in stores:
            data = periods[period].get(sname, {"revenue": 0, "sales_count": 0})
            row[sname] = data["revenue"]
        series.append(row)

    # Summary per store
    summary = await session.execute(
        select(
            MvStoreSales.store_id,
            MvStoreSales.store_name,
            func.sum(MvStoreSales.total_revenue).label("total_revenue"),
            func.sum(MvStoreSales.total_sales).label("total_sales"),
            func.avg(MvStoreSales.avg_order_value).label("avg_order_value"),
        )
        .where(
            MvStoreSales.tenant_id == tid,
            MvStoreSales.date >= d_from,
            MvStoreSales.date <= d_to,
        )
        .group_by(MvStoreSales.store_id, MvStoreSales.store_name)
        .order_by(desc("total_revenue"))
    )

    store_summary = []
    for r in summary.mappings():
        store_summary.append({
            "store_id": str(r["store_id"]),
            "store_name": r["store_name"],
            "total_revenue": float(r["total_revenue"]),
            "total_sales": int(r["total_sales"]),
            "avg_order_value": round(float(r["avg_order_value"]), 2) if r["avg_order_value"] else 0,
        })

    return {
        "stores": stores,
        "series": series,
        "summary": store_summary,
    }


async def store_inventory_health(
    *,
    session: AsyncSession,
    tenant_id: str,
    store_id: str,
    alert_type: str | None = None,
) -> dict:
    """Inventory health for a specific store."""
    tid = UUID(tenant_id)
    sid = UUID(store_id)

    base = select(MvStoreInventory).where(
        MvStoreInventory.tenant_id == tid,
        MvStoreInventory.store_id == sid,
    )
    if alert_type and alert_type != "all":
        base = base.where(MvStoreInventory.status == alert_type)

    rows = await session.execute(base.order_by(MvStoreInventory.product_name))

    items = []
    for r in rows.scalars():
        items.append({
            "product_id": str(r.product_id),
            "product_name": r.product_name,
            "sku": r.sku,
            "current_qty": float(r.current_qty),
            "reserved_qty": float(r.reserved_qty),
            "available_qty": float(r.available_qty),
            "reorder_point": float(r.reorder_point) if r.reorder_point else 0,
            "unit_cost": float(r.unit_cost) if r.unit_cost else 0,
            "status": r.status,
        })

    counts = (
        await session.execute(
            select(
                MvStoreInventory.status,
                func.count(MvStoreInventory.product_id),
            )
            .where(
                MvStoreInventory.tenant_id == tid,
                MvStoreInventory.store_id == sid,
            )
            .group_by(MvStoreInventory.status)
        )
    ).all()

    summary = {
        "total_products": 0,
        "in_stock": 0,
        "low_stock": 0,
        "out_of_stock": 0,
        "overstocked": 0,
    }
    for status, cnt in counts:
        summary["total_products"] += cnt
        if status in summary:
            summary[status] = cnt
        else:
            summary["in_stock"] = cnt

    return {"summary": summary, "items": items}
