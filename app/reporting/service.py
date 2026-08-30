from datetime import UTC, date, datetime
from uuid import uuid4

from reporting.models import (
    CashierPerformance,
    DailySalesSummary,
    PaymentMethodSummary,
    ProductPerformance,
)
from reporting.schemas import (
    CashierPerformanceResult,
    DailySalesSummaryResult,
    PaymentMethodSummaryResult,
    ProductPerformanceResult,
)


def plan_upsert_daily_summary(
    *,
    tenant_id: str,
    summary_date: date | None = None,
    total_sales: int = 0,
    total_revenue: float = 0,
    total_discounts: float = 0,
    total_tax: float = 0,
    voided_count: int = 0,
    voided_amount: float = 0,
    sales_total: float | None = None,
    sales_count: int | None = None,
) -> tuple[DailySalesSummaryResult, DailySalesSummary]:
    if summary_date is None:
        summary_date = datetime.now(UTC).date()
    if sales_total is not None:
        total_revenue = sales_total
    if sales_count is not None:
        total_sales = sales_count
    avg_order_value = total_revenue / total_sales if total_sales > 0 else None
    summary = DailySalesSummary(
        id=uuid4(),
        tenant_id=tenant_id,
        date=summary_date,
        total_sales=total_sales,
        total_revenue=total_revenue,
        total_discounts=total_discounts,
        total_tax=total_tax,
        avg_order_value=avg_order_value,
        voided_count=voided_count,
        voided_amount=voided_amount,
    )
    result = DailySalesSummaryResult(
        id=summary.id,
        tenant_id=summary.tenant_id,
        date=summary.date,
        total_sales=summary.total_sales,
        total_revenue=summary.total_revenue,
        total_discounts=summary.total_discounts,
        total_tax=summary.total_tax,
        avg_order_value=summary.avg_order_value,
        voided_count=summary.voided_count,
        voided_amount=summary.voided_amount,
    )
    return result, summary


def plan_upsert_product_performance(
    *,
    tenant_id: str,
    product_id: str,
    product_name: str,
    period_start: date,
    period_end: date,
    units_sold: float,
    revenue: float,
) -> tuple[ProductPerformanceResult, ProductPerformance]:
    perf = ProductPerformance(
        id=uuid4(),
        tenant_id=tenant_id,
        product_id=product_id,
        product_name=product_name,
        period_start=period_start,
        period_end=period_end,
        units_sold=units_sold,
        revenue=revenue,
    )
    result = ProductPerformanceResult(
        id=perf.id,
        tenant_id=perf.tenant_id,
        product_id=perf.product_id,
        product_name=perf.product_name,
        period_start=perf.period_start,
        period_end=perf.period_end,
        units_sold=perf.units_sold,
        revenue=perf.revenue,
    )
    return result, perf


def plan_upsert_cashier_performance(
    *,
    tenant_id: str,
    user_id: str,
    perf_date: date,
    sales_count: int,
    total_revenue: float,
) -> tuple[CashierPerformanceResult, CashierPerformance]:
    avg_transaction = total_revenue / sales_count if sales_count > 0 else None
    perf = CashierPerformance(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        date=perf_date,
        sales_count=sales_count,
        total_revenue=total_revenue,
        avg_transaction=avg_transaction,
    )
    result = CashierPerformanceResult(
        id=perf.id,
        tenant_id=perf.tenant_id,
        user_id=perf.user_id,
        date=perf.date,
        sales_count=perf.sales_count,
        total_revenue=perf.total_revenue,
        avg_transaction=perf.avg_transaction,
    )
    return result, perf


def plan_upsert_payment_method_summary(
    *,
    tenant_id: str,
    summary_date: date | None = None,
    method: str = "unknown",
    count: int = 1,
    total_amount: float = 0,
    payment_method: str | None = None,
    amount: float | None = None,
) -> tuple[PaymentMethodSummaryResult, PaymentMethodSummary]:
    if summary_date is None:
        summary_date = datetime.now(UTC).date()
    if payment_method is not None:
        method = payment_method
    if amount is not None:
        total_amount = amount
    summary = PaymentMethodSummary(
        id=uuid4(),
        tenant_id=tenant_id,
        date=summary_date,
        method=method,
        count=count,
        total_amount=total_amount,
    )
    result = PaymentMethodSummaryResult(
        id=summary.id,
        tenant_id=summary.tenant_id,
        date=summary.date,
        method=summary.method,
        count=summary.count,
        total_amount=summary.total_amount,
    )
    return result, summary
