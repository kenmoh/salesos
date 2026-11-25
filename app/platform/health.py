"""Tenant health scoring shared by the analytics and per-tenant views.

A health score is deliberately simple and explainable: recency of sales, the
direction of revenue, whether the team still signs in, billing state and fee
debt. Each component is returned so the UI can show *why* a tenant scored low.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

GRADES = ("healthy", "watch", "at_risk", "critical")


def _days_since(ts: datetime | None, now: datetime) -> int:
    return (now - ts).days if ts else 999


def score_health_row(row: dict, now: datetime | None = None) -> dict:
    """Turn one aggregate row into a 0-100 score plus components."""
    now = now or datetime.now(UTC)

    last_sale = row.get("last_sale")
    rev_30 = float(row.get("rev_30") or 0)
    rev_prev = float(row.get("rev_prev_30") or 0)
    active_users = int(row.get("active_users") or 0)
    total_users = int(row.get("total_users") or 0)
    pending_fees = float(row.get("pending_fees") or 0)
    debt_cap = float(row.get("debt_cap") or 2000)
    sub_status = row.get("sub_status")
    overdue = int(row.get("overdue_invoices") or 0)

    days_since_sale = _days_since(last_sale, now)

    if days_since_sale <= 7:
        recency = 40
    elif days_since_sale <= 14:
        recency = 35
    elif days_since_sale <= 30:
        recency = 25
    elif days_since_sale <= 60:
        recency = 12
    elif days_since_sale <= 90:
        recency = 5
    else:
        recency = 0

    if rev_prev > 0:
        ratio = rev_30 / rev_prev
        if ratio >= 1.1:
            growth = 20
        elif ratio >= 0.9:
            growth = 15
        elif ratio > 0:
            growth = 8
        else:
            growth = 3
    elif rev_30 > 0:
        growth = 15
    else:
        growth = 0

    team = round((active_users / total_users) * 20) if total_users else 0

    if sub_status in ("active", "trialing"):
        billing = 10 if overdue == 0 else 0
    elif sub_status in ("past_due", "grace"):
        billing = 3
    elif sub_status:
        billing = 2
    else:
        billing = 4

    fee_ratio = pending_fees / debt_cap if debt_cap > 0 else 0
    if fee_ratio <= 0.5:
        fees = 10
    elif fee_ratio <= 0.9:
        fees = 6
    elif fee_ratio <= 1:
        fees = 3
    else:
        fees = 0

    score = max(0, min(100, recency + growth + team + billing + fees))
    if score >= 75:
        grade = "healthy"
    elif score >= 50:
        grade = "watch"
    elif score >= 25:
        grade = "at_risk"
    else:
        grade = "critical"

    recommendations: list[str] = []
    if days_since_sale > 30:
        recommendations.append("No sale in 30+ days — reach out to the owner")
    if rev_prev > 0 and rev_30 < rev_prev * 0.9:
        recommendations.append("Revenue is declining — review pricing, stock or staffing")
    if total_users and active_users == 0:
        recommendations.append("No active users recently — confirm the team can sign in")
    if overdue:
        recommendations.append("Overdue invoice — follow up on billing")
    if debt_cap > 0 and pending_fees >= debt_cap * 0.9:
        recommendations.append("Fee debt near the cap — settlement may be blocked")
    if grade == "healthy":
        recommendations.append("Healthy account — candidate for case study or upsell")

    return {
        "score": score,
        "grade": grade,
        "components": {
            "recency": recency,
            "growth": growth,
            "team": team,
            "billing": billing,
            "fees": fees,
        },
        "metrics": {
            "days_since_last_sale": days_since_sale,
            "revenue_30d": round(rev_30, 2),
            "revenue_prev_30d": round(rev_prev, 2),
            "revenue_change_pct": (
                round((rev_30 - rev_prev) / rev_prev * 100, 1) if rev_prev else None
            ),
            "active_users_30d": active_users,
            "total_users": total_users,
            "pending_fees": round(pending_fees, 2),
            "debt_cap": round(debt_cap, 2),
            "subscription_status": sub_status,
            "overdue_invoices": overdue,
        },
        "recommendations": recommendations,
    }


def summarize_health(items: list[dict]) -> dict:
    counts = {grade: 0 for grade in GRADES}
    revenue_at_risk = 0.0
    for item in items:
        grade = item.get("grade")
        if grade in counts:
            counts[grade] += 1
        if grade in ("at_risk", "critical"):
            revenue_at_risk += float(item.get("metrics", {}).get("revenue_30d") or 0)
    total = len(items)
    return {
        "total": total,
        "by_grade": counts,
        "average_score": round(sum(i.get("score", 0) for i in items) / total, 1) if total else 0,
        "revenue_at_risk_30d": round(revenue_at_risk, 2),
    }


async def fetch_health_rows(
    session: AsyncSession, tenant_id: str | None = None
) -> list[dict]:
    """Load every tenant with the aggregates the scorer needs, in one query."""
    from app.billing.models import BillingInvoice, MerchantSubscription
    from app.identity.models import User
    from app.platform.models import PlatformFeeLedger
    from app.sales.models import Sale
    from app.tenancy.models import Tenant

    now = datetime.now(UTC)
    cutoff_30 = now - timedelta(days=30)
    cutoff_60 = now - timedelta(days=60)

    last_sale_sq = (
        select(func.max(Sale.created_at))
        .where(Sale.tenant_id == Tenant.id, Sale.status != "voided")
        .correlate(Tenant)
        .scalar_subquery()
    )
    rev_30_sq = (
        select(func.coalesce(func.sum(Sale.total), 0))
        .where(
            Sale.tenant_id == Tenant.id,
            Sale.status != "voided",
            Sale.created_at >= cutoff_30,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    rev_prev_sq = (
        select(func.coalesce(func.sum(Sale.total), 0))
        .where(
            Sale.tenant_id == Tenant.id,
            Sale.status != "voided",
            Sale.created_at >= cutoff_60,
            Sale.created_at < cutoff_30,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    total_users_sq = (
        select(func.count(User.id))
        .where(User.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    active_users_sq = (
        select(func.count(User.id))
        .where(
            User.tenant_id == Tenant.id,
            User.status == "active",
            User.last_login_at >= cutoff_30,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    pending_fees_sq = (
        select(func.coalesce(func.sum(PlatformFeeLedger.amount), 0))
        .where(
            PlatformFeeLedger.tenant_id == Tenant.id,
            PlatformFeeLedger.status == "pending",
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    sub_status_sq = (
        select(MerchantSubscription.status)
        .where(MerchantSubscription.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    debt_cap_sq = (
        select(MerchantSubscription.debt_cap)
        .where(MerchantSubscription.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    overdue_sq = (
        select(func.count(BillingInvoice.id))
        .where(
            BillingInvoice.tenant_id == Tenant.id,
            BillingInvoice.due_at < now,
            BillingInvoice.status.notin_(("paid", "void", "cancelled")),
        )
        .correlate(Tenant)
        .scalar_subquery()
    )

    query = select(
        Tenant.id,
        Tenant.business_name,
        Tenant.slug,
        Tenant.status,
        Tenant.created_at,
        last_sale_sq.label("last_sale"),
        rev_30_sq.label("rev_30"),
        rev_prev_sq.label("rev_prev_30"),
        total_users_sq.label("total_users"),
        active_users_sq.label("active_users"),
        pending_fees_sq.label("pending_fees"),
        sub_status_sq.label("sub_status"),
        debt_cap_sq.label("debt_cap"),
        overdue_sq.label("overdue_invoices"),
    )
    if tenant_id:
        query = query.where(Tenant.id == tenant_id)
    else:
        query = query.where(Tenant.status != "cancelled")

    rows = (await session.execute(query)).all()
    result: list[dict] = []
    for row in rows:
        base = {
            "tenant_id": str(row.id),
            "business_name": row.business_name,
            "slug": row.slug,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_sale": row.last_sale,
            "rev_30": row.rev_30,
            "rev_prev_30": row.rev_prev_30,
            "total_users": row.total_users,
            "active_users": row.active_users,
            "pending_fees": row.pending_fees,
            "sub_status": row.sub_status,
            "debt_cap": row.debt_cap,
            "overdue_invoices": row.overdue_invoices,
        }
        scored = score_health_row(base, now=now)
        result.append(
            {
                **{k: v for k, v in base.items() if k != "last_sale"},
                "last_sale_at": base["last_sale"].isoformat() if base["last_sale"] else None,
                **scored,
            }
        )
    return result
