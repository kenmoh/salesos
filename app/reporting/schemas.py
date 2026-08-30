from datetime import date
from uuid import UUID

from pydantic import BaseModel


class DailySalesSummaryResult(BaseModel):
    id: UUID
    tenant_id: UUID
    date: date
    total_sales: int
    total_revenue: float
    total_discounts: float
    total_tax: float
    avg_order_value: float | None
    voided_count: int
    voided_amount: float


class ProductPerformanceResult(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    product_name: str
    period_start: date
    period_end: date
    units_sold: float
    revenue: float


class CashierPerformanceResult(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    date: date
    sales_count: int
    total_revenue: float
    avg_transaction: float | None


class PaymentMethodSummaryResult(BaseModel):
    id: UUID
    tenant_id: UUID
    date: date
    method: str
    count: int
    total_amount: float
