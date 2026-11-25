"""Additional platform analytics: retention, health, plan mix, adoption,
devices and finance reconciliation.

Kept separate from ``routes.py`` so the original analytics endpoints stay
readable; both routers mount under ``/platform``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import Date, and_, case, cast, desc, func, select

from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.responses import ok
from app.platform.exporting import as_float, parse_iso_date
from app.platform.health import fetch_health_rows, summarize_health

router = APIRouter(prefix="/platform/analytics", tags=["Platform Analytics"])


def _month_start(value: date, offset: int = 0) -> date:
    year = value.year + (value.month - 1 + offset) // 12
    month = (value.month - 1 + offset) % 12 + 1
    return date(year, month, 1)


def _month_key(value: datetime | date) -> date:
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    return date(value.year, value.month, 1)


@router.get(
    "/cohort-retention",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def cohort_retention(ctx: PlatformDep, months: int = 6):
    """Signup-month cohorts and how many still recorded a sale each month."""
    from app.sales.models import Sale
    from app.tenancy.models import Tenant

    months = min(max(months, 1), 12)
    now = datetime.now(UTC)
    first_month = _month_start(now.date(), -(months - 1))
    since = datetime.combine(first_month, datetime.min.time(), tzinfo=UTC)

    cohort_rows = (
        await ctx.session.execute(
            select(
                Tenant.id,
                func.date_trunc("month", Tenant.created_at).label("cohort"),
            ).where(Tenant.created_at >= since)
        )
    ).all()
    activity_rows = (
        await ctx.session.execute(
            select(
                Sale.tenant_id,
                func.date_trunc("month", Sale.created_at).label("activity_month"),
            )
            .where(Sale.created_at >= since, Sale.status != "voided")
            .distinct()
        )
    ).all()

    activity: dict[str, set[date]] = {}
    for tenant_id, month in activity_rows:
        activity.setdefault(str(tenant_id), set()).add(_month_key(month))

    cohorts: dict[date, list[str]] = {}
    for tenant_id, cohort in cohort_rows:
        cohorts.setdefault(_month_key(cohort), []).append(str(tenant_id))

    items = []
    for cohort_month in sorted(cohorts):
        members = cohorts[cohort_month]
        size = len(members)
        periods = []
        for offset in range(months):
            period = _month_start(cohort_month, offset)
            if period > _month_start(now.date()):
                break
            active = sum(1 for member in members if period in activity.get(member, set()))
            periods.append(
                {
                    "offset": offset,
                    "month": period.isoformat(),
                    "label": period.strftime("%b %Y"),
                    "active": active,
                    "retention_pct": round(active / size * 100, 1) if size else 0,
                }
            )
        items.append(
            {
                "cohort": cohort_month.isoformat(),
                "label": cohort_month.strftime("%b %Y"),
                "cohort_size": size,
                "periods": periods,
            }
        )

    def _average(offset: int) -> float:
        values = []
        for item in items:
            for period in item["periods"]:
                if period["offset"] == offset:
                    values.append(period["retention_pct"])
        return round(sum(values) / len(values), 1) if values else 0

    return ok(
        {
            "items": items,
            "summary": {
                "months": months,
                "cohorts": len(items),
                "average_retention": {f"month_{i}": _average(i) for i in range(months)},
            },
        }
    )


@router.get(
    "/tenant-health",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_health(
    ctx: PlatformDep,
    grade: str | None = None,
    sort: str = "score",
    order: str = "asc",
    page: int = 1,
    page_size: int = 50,
):
    """Every tenant scored 0-100 with the components behind the score."""
    rows = await fetch_health_rows(ctx.session)
    if grade:
        rows = [r for r in rows if r["grade"] == grade]

    sort_key = {
        "score": lambda r: r["score"],
        "revenue": lambda r: r["metrics"]["revenue_30d"],
        "name": lambda r: (r["business_name"] or "").lower(),
        "last_sale": lambda r: r["metrics"]["days_since_last_sale"],
    }.get(sort, lambda r: r["score"])
    rows.sort(key=sort_key, reverse=(order != "asc"))

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    total = len(rows)
    start = (page - 1) * page_size
    return ok(
        {
            "items": rows[start : start + page_size],
            "summary": summarize_health(rows),
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get(
    "/plan-distribution",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def plan_distribution(ctx: PlatformDep):
    """Plan mix, MRR/ARR and billing exposure across all tenants."""
    from app.billing.models import BillingInvoice, MerchantSubscription, SubscriptionPlan, TenantWallet

    now = datetime.now(UTC)
    rows = (
        await ctx.session.execute(
            select(
                SubscriptionPlan.id,
                SubscriptionPlan.name,
                SubscriptionPlan.slug,
                SubscriptionPlan.monthly_price,
                SubscriptionPlan.is_active,
                func.count(MerchantSubscription.id).label("subscriptions"),
                func.count(MerchantSubscription.id)
                .filter(MerchantSubscription.status == "active")
                .label("active_subscriptions"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    MerchantSubscription.status == "active",
                                    MerchantSubscription.billing_interval == "monthly",
                                ),
                                SubscriptionPlan.monthly_price,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("monthly_mrr"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    MerchantSubscription.status == "active",
                                    MerchantSubscription.billing_interval == "annual",
                                ),
                                SubscriptionPlan.monthly_price
                                * (1 - SubscriptionPlan.annual_discount_pct / 100)
                                / 12,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("annual_mrr"),
            )
            .select_from(SubscriptionPlan)
            .outerjoin(
                MerchantSubscription,
                MerchantSubscription.plan_id == SubscriptionPlan.id,
            )
            .group_by(
                SubscriptionPlan.id,
                SubscriptionPlan.name,
                SubscriptionPlan.slug,
                SubscriptionPlan.monthly_price,
                SubscriptionPlan.is_active,
            )
            .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name)
        )
    ).all()

    status_rows = (
        await ctx.session.execute(
            select(
                MerchantSubscription.status.label("status"),
                func.count(MerchantSubscription.id).label("count"),
            ).group_by(MerchantSubscription.status)
        )
    ).all()
    invoice_rows = (
        await ctx.session.execute(
            select(
                BillingInvoice.status.label("status"),
                func.count(BillingInvoice.id).label("count"),
                func.coalesce(func.sum(BillingInvoice.amount_due), 0).label("amount_due"),
            ).group_by(BillingInvoice.status)
        )
    ).all()
    overdue = (
        await ctx.session.execute(
            select(
                func.count(BillingInvoice.id).label("count"),
                func.coalesce(func.sum(BillingInvoice.amount_due), 0).label("amount"),
            ).where(
                BillingInvoice.due_at < now,
                BillingInvoice.status.notin_(("paid", "void", "cancelled")),
            )
        )
    ).one()
    wallet = (
        await ctx.session.execute(
            select(
                func.count(TenantWallet.id).label("count"),
                func.coalesce(func.sum(TenantWallet.balance), 0).label("balance"),
            )
        )
    ).one()

    items = []
    mrr = 0.0
    for r in rows:
        plan_mrr = float(r.monthly_mrr or 0) + float(r.annual_mrr or 0)
        mrr += plan_mrr
        items.append(
            {
                "plan_id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "monthly_price": float(r.monthly_price or 0),
                "is_active": bool(r.is_active),
                "subscriptions": int(r.subscriptions or 0),
                "active_subscriptions": int(r.active_subscriptions or 0),
                "mrr": round(plan_mrr, 2),
            }
        )

    return ok(
        {
            "items": items,
            "summary": {
                "mrr": round(mrr, 2),
                "arr": round(mrr * 12, 2),
                "subscriptions_by_status": [
                    {"status": r.status, "count": int(r.count)} for r in status_rows
                ],
                "invoices_by_status": [
                    {
                        "status": r.status,
                        "count": int(r.count),
                        "amount_due": float(r.amount_due or 0),
                    }
                    for r in invoice_rows
                ],
                "overdue": {
                    "count": int(overdue.count or 0),
                    "amount_due": float(overdue.amount or 0),
                },
                "wallets": {
                    "count": int(wallet.count or 0),
                    "total_balance": float(wallet.balance or 0),
                },
            },
        }
    )


@router.get(
    "/module-adoption",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def module_adoption(ctx: PlatformDep):
    """How many active tenants actually use each product module."""
    from app.catalog.models import Product
    from app.customers.models import Customer
    from app.documents.models import Document
    from app.identity.models import User
    from app.inventory.models import StockBalance
    from app.payments.models import Payment
    from app.sales.models import Sale
    from app.stores.models import Store
    from app.tenancy.models import Tenant

    modules = [
        ("products", "Catalog", Product),
        ("stores", "Stores", Store),
        ("sales", "Sales", Sale),
        ("payments", "Payments", Payment),
        ("customers", "Customers", Customer),
        ("documents", "Documents", Document),
        ("inventory", "Inventory", StockBalance),
        ("team", "Team", User),
    ]
    active_tenants = (
        await ctx.session.execute(
            select(func.count(Tenant.id)).where(Tenant.status == "active")
        )
    ).scalar() or 0

    items = []
    for key, label, model in modules:
        row = (
            await ctx.session.execute(
                select(
                    func.count(func.distinct(model.tenant_id)).label("tenants"),
                    func.count(model.id).label("rows"),
                )
            )
        ).one()
        tenants = int(row.tenants or 0)
        items.append(
            {
                "module": key,
                "label": label,
                "tenants": tenants,
                "rows": int(row.rows or 0),
                "adoption_pct": round(tenants / active_tenants * 100, 1)
                if active_tenants
                else 0,
            }
        )

    return ok(
        {
            "items": sorted(items, key=lambda i: i["adoption_pct"], reverse=True),
            "active_tenants": int(active_tenants),
        }
    )


@router.get(
    "/device-distribution",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def device_distribution(ctx: PlatformDep):
    """What devices, browsers and push platforms tenants actually use."""
    from app.identity.models import User
    from app.notifications.models import PushToken

    device_rows = (
        await ctx.session.execute(
            select(
                func.coalesce(User.last_device_type, "unknown").label("device_type"),
                func.count(User.id).label("count"),
                func.count(User.id).filter(User.status == "active").label("active"),
            ).group_by(func.coalesce(User.last_device_type, "unknown"))
        )
    ).all()
    browser_rows = (
        await ctx.session.execute(
            select(
                func.coalesce(User.last_browser, "unknown").label("browser"),
                func.count(User.id).label("count"),
            )
            .where(User.last_browser.isnot(None))
            .group_by(User.last_browser)
            .order_by(desc("count"))
            .limit(15)
        )
    ).all()
    push_rows = (
        await ctx.session.execute(
            select(
                PushToken.platform.label("platform"),
                func.count(PushToken.id).label("count"),
                func.count(PushToken.id).filter(PushToken.is_active.is_(True)).label("active"),
            ).group_by(PushToken.platform)
        )
    ).all()
    provider_rows = (
        await ctx.session.execute(
            select(
                func.coalesce(User.provider, "password").label("provider"),
                func.count(User.id).label("count"),
            ).group_by(func.coalesce(User.provider, "password"))
        )
    ).all()

    return ok(
        {
            "devices": [
                {
                    "device_type": r.device_type,
                    "count": int(r.count),
                    "active": int(r.active or 0),
                }
                for r in device_rows
            ],
            "browsers": [{"browser": r.browser, "count": int(r.count)} for r in browser_rows],
            "push_platforms": [
                {
                    "platform": r.platform,
                    "count": int(r.count),
                    "active": int(r.active or 0),
                }
                for r in push_rows
            ],
            "auth_providers": [
                {"provider": r.provider, "count": int(r.count)} for r in provider_rows
            ],
        }
    )


@router.get(
    "/finance-summary",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def finance_summary(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
):
    """Fees booked vs settled, invoices, wallets and payments in one view."""
    from app.billing.models import (
        BillingInvoice,
        BillingPayment,
        TenantWallet,
    )
    from app.payments.models import Payment
    from app.platform.models import PlatformFeeLedger as Fee

    now = datetime.now(UTC)
    d_from = parse_iso_date(from_date, "from_date") or (now - timedelta(days=30)).date()
    d_to = parse_iso_date(to_date, "to_date") or now.date()

    fee_totals = (
        await ctx.session.execute(
            select(
                func.coalesce(
                    func.sum(case((Fee.status == "deducted", Fee.amount), else_=0)), 0
                ).label("settled"),
                func.coalesce(
                    func.sum(case((Fee.status == "pending", Fee.amount), else_=0)), 0
                ).label("pending"),
                func.count(Fee.id).label("count"),
            ).where(cast(Fee.created_at, Date) >= d_from, cast(Fee.created_at, Date) <= d_to)
        )
    ).one()
    fee_by_category = (
        await ctx.session.execute(
            select(
                Fee.fee_category.label("category"),
                func.coalesce(func.sum(Fee.amount), 0).label("amount"),
                func.count(Fee.id).label("count"),
            )
            .where(cast(Fee.created_at, Date) >= d_from, cast(Fee.created_at, Date) <= d_to)
            .group_by(Fee.fee_category)
        )
    ).all()
    fee_by_method = (
        await ctx.session.execute(
            select(
                Fee.payment_method.label("method"),
                func.coalesce(func.sum(Fee.amount), 0).label("amount"),
                func.count(Fee.id).label("count"),
            )
            .where(cast(Fee.created_at, Date) >= d_from, cast(Fee.created_at, Date) <= d_to)
            .group_by(Fee.payment_method)
        )
    ).all()
    invoice_totals = (
        await ctx.session.execute(
            select(
                func.coalesce(func.sum(BillingInvoice.total_amount), 0).label("billed"),
                func.coalesce(func.sum(BillingInvoice.amount_paid), 0).label("paid"),
                func.coalesce(func.sum(BillingInvoice.amount_due), 0).label("outstanding"),
                func.count(BillingInvoice.id).label("count"),
            ).where(
                cast(BillingInvoice.issued_at, Date) >= d_from,
                cast(BillingInvoice.issued_at, Date) <= d_to,
            )
        )
    ).one()
    overdue = (
        await ctx.session.execute(
            select(
                func.count(BillingInvoice.id).label("count"),
                func.coalesce(func.sum(BillingInvoice.amount_due), 0).label("amount"),
            ).where(
                BillingInvoice.due_at < now,
                BillingInvoice.status.notin_(("paid", "void", "cancelled")),
            )
        )
    ).one()
    wallet = (
        await ctx.session.execute(
            select(
                func.count(TenantWallet.id).label("count"),
                func.coalesce(func.sum(TenantWallet.balance), 0).label("balance"),
            )
        )
    ).one()
    billing_payments = (
        await ctx.session.execute(
            select(
                BillingPayment.payment_method.label("method"),
                func.coalesce(func.sum(BillingPayment.amount), 0).label("amount"),
                func.count(BillingPayment.id).label("count"),
            )
            .where(
                cast(BillingPayment.paid_at, Date) >= d_from,
                cast(BillingPayment.paid_at, Date) <= d_to,
            )
            .group_by(BillingPayment.payment_method)
        )
    ).all()
    tenant_payments = (
        await ctx.session.execute(
            select(
                Payment.method.label("method"),
                func.coalesce(func.sum(Payment.amount), 0).label("amount"),
                func.count(Payment.id).label("count"),
            )
            .where(
                cast(Payment.created_at, Date) >= d_from,
                cast(Payment.created_at, Date) <= d_to,
                Payment.status == "completed",
            )
            .group_by(Payment.method)
        )
    ).all()

    settled = as_float(fee_totals.settled)
    invoiced_fees = as_float(invoice_totals.billed)
    return ok(
        {
            "window": {"from_date": d_from.isoformat(), "to_date": d_to.isoformat()},
            "fees": {
                "settled": settled,
                "pending": as_float(fee_totals.pending),
                "count": int(fee_totals.count or 0),
                "by_category": [
                    {
                        "category": r.category,
                        "amount": as_float(r.amount),
                        "count": int(r.count),
                    }
                    for r in fee_by_category
                ],
                "by_method": [
                    {
                        "method": r.method,
                        "amount": as_float(r.amount),
                        "count": int(r.count),
                    }
                    for r in fee_by_method
                ],
            },
            "invoices": {
                "billed": as_float(invoice_totals.billed),
                "paid": as_float(invoice_totals.paid),
                "outstanding": as_float(invoice_totals.outstanding),
                "count": int(invoice_totals.count or 0),
                "overdue": {
                    "count": int(overdue.count or 0),
                    "amount_due": as_float(overdue.amount),
                },
            },
            "wallets": {
                "count": int(wallet.count or 0),
                "total_balance": as_float(wallet.balance),
            },
            "billing_payments": [
                {
                    "method": r.method,
                    "amount": as_float(r.amount),
                    "count": int(r.count),
                }
                for r in billing_payments
            ],
            "tenant_payments": [
                {
                    "method": r.method,
                    "amount": as_float(r.amount),
                    "count": int(r.count),
                }
                for r in tenant_payments
            ],
            "reconciliation": {
                "fees_settled": settled,
                "fees_billed_on_invoices": invoiced_fees,
                "difference": round(settled - invoiced_fees, 2),
            },
        }
    )
