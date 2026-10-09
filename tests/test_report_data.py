"""Report numbers come from completed sales and from the payments table.

Three things went wrong at once on the report screen, and all three are
pinned here against the live database this suite already runs on:

* the daily/store/cashier materialized views counted voided sales as
  sales, so revenue, sales count, discounts, tax and average order value
  were all inflated by money nobody paid;
* the payment breakdown walked sale.payment_methods, which never carries
  a method key for single-method sales, so every card and transfer
  payment was filed as cash — the payments table is the only place a
  method is actually recorded;
* every report endpoint was tenant-wide, so a store owner could not ask
  what one store did.

Also pinned: a customer whose most recent purchase is today still counts
in the period list. The old bound compared a timestamp to a date at
midnight, which cut out everyone who bought on the final day.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.analytics import (
    cashier_performance,
    customer_insights,
    dashboard_summary,
    payment_breakdown,
    sales_summary,
)
from app.core.config import settings
from app.payments.models import Payment
from app.sales.models import Sale
from app.stores.models import Store

TODAY = datetime.now(UTC).date()
FROM_DATE = str(TODAY - timedelta(days=1))
TO_DATE = str(TODAY)


def _sale_number() -> str:
    return f"T-{uuid4().hex[:16]}"


async def _refresh(engine, *views: str) -> None:
    async with engine.begin() as conn:
        for view in views:
            await conn.execute(text(f"REFRESH MATERIALIZED VIEW {view}"))


VIEWS = ("mv_daily_sales", "mv_store_sales", "mv_cashier_performance", "mv_customer_summary")


async def _cleanup(engine, tenant_id) -> None:
    async with engine.begin() as conn:
        await conn.execute(delete(Payment).where(Payment.tenant_id == tenant_id))
        await conn.execute(delete(Sale).where(Sale.tenant_id == tenant_id))
        await conn.execute(delete(Store).where(Store.tenant_id == tenant_id))
    await _refresh(engine, *VIEWS)


class TestReportsCountCompletedSalesOnly:
    async def test_voided_sales_never_reach_the_numbers(self):
        engine = create_async_engine(settings.admin_database_url or settings.database_url)
        tenant = uuid4()
        store_a, store_b = uuid4(), uuid4()
        cashier = uuid4()
        now = datetime.now(UTC)

        try:
            async with engine.begin() as conn:
                await conn.execute(
                    insert(Store),
                    [
                        {"id": store_a, "tenant_id": tenant, "name": "Report Seed A"},
                        {"id": store_b, "tenant_id": tenant, "name": "Report Seed B"},
                    ],
                )
                await conn.execute(
                    insert(Sale),
                    [
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_number": _sale_number(),
                            "status": "completed",
                            "customer_name": "Ada Buyer",
                            "store_id": store_a,
                            "cashier_id": cashier,
                            "subtotal": 925,
                            "discount": 100,
                            "tax": 75,
                            "total": 1000,
                            "amount_paid": 1000,
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_number": _sale_number(),
                            "status": "voided",
                            "customer_name": None,
                            "store_id": store_a,
                            "cashier_id": cashier,
                            "subtotal": 500,
                            "discount": 0,
                            "tax": 0,
                            "total": 500,
                            "amount_paid": 0,
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_number": _sale_number(),
                            "status": "completed",
                            "customer_name": "Ada Buyer",
                            "store_id": store_a,
                            "cashier_id": cashier,
                            "subtotal": 600,
                            "discount": 0,
                            "tax": 0,
                            "total": 600,
                            "amount_paid": 600,
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_number": _sale_number(),
                            "status": "completed",
                            "customer_name": None,
                            "store_id": store_a,
                            "cashier_id": cashier,
                            "subtotal": 250,
                            "discount": 0,
                            "tax": 0,
                            "total": 250,
                            "amount_paid": 250,
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_number": _sale_number(),
                            "status": "completed",
                            "customer_name": "Bob Buyer",
                            "store_id": store_b,
                            "cashier_id": cashier,
                            "subtotal": 300,
                            "discount": 0,
                            "tax": 0,
                            "total": 300,
                            "amount_paid": 300,
                            "created_at": now,
                        },
                    ],
                )
                sale_rows = (
                    await conn.execute(
                        text(
                            "SELECT id, store_id, total, discount, tax, status "
                            "FROM sales WHERE tenant_id = :t"
                        ),
                        {"t": tenant},
                    )
                ).all()
                by_status = {(r.status, float(r.total)): r.id for r in sale_rows}
                await conn.execute(
                    insert(Payment),
                    [
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_id": by_status[("completed", 600.0)],
                            "method": "card",
                            "amount": 600,
                            "status": "completed",
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_id": by_status[("completed", 250.0)],
                            "method": "cash",
                            "amount": 250,
                            "status": "completed",
                            "created_at": now,
                        },
                        {
                            "id": uuid4(),
                            "tenant_id": tenant,
                            "sale_id": by_status[("completed", 300.0)],
                            "method": "transfer",
                            "amount": 300,
                            "status": "completed",
                            "created_at": now,
                        },
                    ],
                )
            await _refresh(engine, *VIEWS)

            async with engine.connect() as session:
                summary = await sales_summary(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                )
                assert summary["totals"] == {
                    "revenue": 2150.0,
                    "sales_count": 4,
                    "discount_total": 100.0,
                    "tax_total": 75.0,
                }

                dash = await dashboard_summary(
                    session=session, tenant_id=str(tenant), days=1
                )
                assert dash["revenue"]["current"] == 2150.0
                assert dash["sales_count"]["current"] == 4
                assert dash["avg_order_value"]["current"] == 537.5

                store_a_summary = await sales_summary(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                    store_id=str(store_a),
                )
                assert store_a_summary["totals"] == {
                    "revenue": 1850.0,
                    "sales_count": 3,
                    "discount_total": 100.0,
                    "tax_total": 75.0,
                }

                store_b_dash = await dashboard_summary(
                    session=session, tenant_id=str(tenant), days=1, store_id=str(store_b)
                )
                assert store_b_dash["revenue"]["current"] == 300.0
                assert store_b_dash["sales_count"]["current"] == 1

                payments = await payment_breakdown(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                )
                assert payments == {
                    "cash": 250.0,
                    "card": 600.0,
                    "transfer": 300.0,
                    "total": 1150.0,
                }

                store_a_payments = await payment_breakdown(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                    store_id=str(store_a),
                )
                assert store_a_payments == {
                    "cash": 250.0,
                    "card": 600.0,
                    "transfer": 0.0,
                    "total": 850.0,
                }

                all_cashiers = await cashier_performance(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                )
                (row,) = all_cashiers
                assert row["sales_count"] == 4
                assert row["total_revenue"] == 2150.0
                assert row["void_count"] == 1

                store_a_cashiers = await cashier_performance(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                    store_id=str(store_a),
                )
                (row,) = store_a_cashiers
                assert row["sales_count"] == 3
                assert row["total_revenue"] == 1850.0
                assert row["void_count"] == 1
        finally:
            await _cleanup(engine, tenant)
            await engine.dispose()

    async def test_a_customer_who_bought_today_is_still_in_the_list(self):
        engine = create_async_engine(settings.admin_database_url or settings.database_url)
        tenant = uuid4()
        store = uuid4()
        now = datetime.now(UTC)

        try:
            async with engine.begin() as conn:
                await conn.execute(
                    insert(Store),
                    {"id": store, "tenant_id": tenant, "name": "Report Seed C"},
                )
                await conn.execute(
                    insert(Sale),
                    {
                        "id": uuid4(),
                        "tenant_id": tenant,
                        "sale_number": _sale_number(),
                        "status": "completed",
                        "customer_name": "Same Day",
                        "store_id": store,
                        "subtotal": 150,
                        "total": 150,
                        "amount_paid": 150,
                        "created_at": now,
                    },
                )
            await _refresh(engine, "mv_customer_summary")

            async with engine.connect() as session:
                insights = await customer_insights(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                )
                assert [c["customer_name"] for c in insights["top_customers"]] == ["Same Day"]
                assert insights["summary"]["unique_customers"] == 1

                store_insights = await customer_insights(
                    session=session,
                    tenant_id=str(tenant),
                    from_date=FROM_DATE,
                    to_date=TO_DATE,
                    store_id=str(store),
                )
                assert [c["customer_name"] for c in store_insights["top_customers"]] == ["Same Day"]
        finally:
            await _cleanup(engine, tenant)
            await engine.dispose()
