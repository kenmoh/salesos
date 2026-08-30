from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reporting.models import (
    CashierPerformance,
    DailySalesSummary,
    PaymentMethodSummary,
    ProductPerformance,
)


async def upsert_daily_summary(
    session: AsyncSession, summary: DailySalesSummary
) -> DailySalesSummary:
    existing = await get_daily_summary(session, summary.tenant_id, summary.date)
    if existing:
        existing.total_sales = summary.total_sales
        existing.total_revenue = summary.total_revenue
        existing.total_discounts = summary.total_discounts
        existing.total_tax = summary.total_tax
        existing.avg_order_value = summary.avg_order_value
        existing.voided_count = summary.voided_count
        existing.voided_amount = summary.voided_amount
        await session.flush()
        return existing
    session.add(summary)
    await session.flush()
    return summary


async def get_daily_summary(
    session: AsyncSession, tenant_id: UUID, summary_date: date
) -> DailySalesSummary | None:
    result = await session.execute(
        select(DailySalesSummary).where(
            DailySalesSummary.tenant_id == tenant_id,
            DailySalesSummary.date == summary_date,
        )
    )
    return result.scalar_one_or_none()


async def get_daily_summaries_range(
    session: AsyncSession,
    tenant_id: UUID,
    start_date: date,
    end_date: date,
) -> list[DailySalesSummary]:
    result = await session.execute(
        select(DailySalesSummary)
        .where(
            DailySalesSummary.tenant_id == tenant_id,
            DailySalesSummary.date >= start_date,
            DailySalesSummary.date <= end_date,
        )
        .order_by(DailySalesSummary.date)
    )
    return list(result.scalars().all())


async def upsert_product_performance(
    session: AsyncSession, perf: ProductPerformance
) -> ProductPerformance:
    existing = await get_product_performance(
        session, perf.tenant_id, perf.product_id, perf.period_start, perf.period_end
    )
    if existing:
        existing.units_sold += perf.units_sold
        existing.revenue += perf.revenue
        await session.flush()
        return existing
    session.add(perf)
    await session.flush()
    return perf


async def get_product_performance(
    session: AsyncSession,
    tenant_id: UUID,
    product_id: UUID,
    period_start: date,
    period_end: date,
) -> ProductPerformance | None:
    result = await session.execute(
        select(ProductPerformance).where(
            ProductPerformance.tenant_id == tenant_id,
            ProductPerformance.product_id == product_id,
            ProductPerformance.period_start == period_start,
            ProductPerformance.period_end == period_end,
        )
    )
    return result.scalar_one_or_none()


async def upsert_cashier_performance(
    session: AsyncSession, perf: CashierPerformance
) -> CashierPerformance:
    existing = await get_cashier_performance(session, perf.tenant_id, perf.user_id, perf.date)
    if existing:
        existing.sales_count += perf.sales_count
        existing.total_revenue += perf.total_revenue
        if existing.sales_count > 0:
            existing.avg_transaction = existing.total_revenue / existing.sales_count
        await session.flush()
        return existing
    if perf.sales_count > 0:
        perf.avg_transaction = perf.total_revenue / perf.sales_count
    session.add(perf)
    await session.flush()
    return perf


async def get_cashier_performance(
    session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    perf_date: date,
) -> CashierPerformance | None:
    result = await session.execute(
        select(CashierPerformance).where(
            CashierPerformance.tenant_id == tenant_id,
            CashierPerformance.user_id == user_id,
            CashierPerformance.date == perf_date,
        )
    )
    return result.scalar_one_or_none()


async def upsert_payment_method_summary(
    session: AsyncSession, summary: PaymentMethodSummary
) -> PaymentMethodSummary:
    existing = await get_payment_method_summary(
        session, summary.tenant_id, summary.date, summary.method
    )
    if existing:
        existing.count += summary.count
        existing.total_amount += summary.total_amount
        await session.flush()
        return existing
    session.add(summary)
    await session.flush()
    return summary


async def get_payment_method_summary(
    session: AsyncSession,
    tenant_id: UUID,
    summary_date: date,
    method: str,
) -> PaymentMethodSummary | None:
    result = await session.execute(
        select(PaymentMethodSummary).where(
            PaymentMethodSummary.tenant_id == tenant_id,
            PaymentMethodSummary.date == summary_date,
            PaymentMethodSummary.method == method,
        )
    )
    return result.scalar_one_or_none()
