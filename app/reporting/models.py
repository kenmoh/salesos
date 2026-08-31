from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class DailySalesSummary(StoreFlowBase):
    __tablename__ = "daily_sales_summary"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_sales: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    total_discounts: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    total_tax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    avg_order_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    voided_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voided_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ProductPerformance(StoreFlowBase):
    __tablename__ = "product_performance"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    units_sold: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class CashierPerformance(StoreFlowBase):
    __tablename__ = "cashier_performance"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    sales_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    avg_transaction: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class PaymentMethodSummary(StoreFlowBase):
    __tablename__ = "payment_method_summary"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Materialized views — NOT created by Alembic; created via raw SQL migration
# ═══════════════════════════════════════════════════════════════════════════════


class MvDailySales(StoreFlowBase):
    __tablename__ = "mv_daily_sales"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_sales: Mapped[int] = mapped_column(Integer, nullable=False)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    total_discounts: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    total_tax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    avg_order_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    voided_count: Mapped[int] = mapped_column(Integer, nullable=False)
    voided_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)


class MvProductRanking(StoreFlowBase):
    __tablename__ = "mv_product_rankings"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    units_sold: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    avg_selling_price: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    cost_price: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)


class MvPaymentMethod(StoreFlowBase):
    __tablename__ = "mv_payment_methods"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    method: Mapped[str] = mapped_column(String(30), primary_key=True)
    payment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)


class MvCashierPerformance(StoreFlowBase):
    __tablename__ = "mv_cashier_performance"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    sales_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    avg_transaction: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    void_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MvCustomerSummary(StoreFlowBase):
    __tablename__ = "mv_customer_summary"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    customer_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    first_purchase: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_purchase: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_purchases: Mapped[int] = mapped_column(Integer, nullable=False)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    avg_order_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)


class MvInventoryStatus(StoreFlowBase):
    __tablename__ = "mv_inventory_status"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    store_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    current_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reorder_point: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  Store-level materialized views
# ═══════════════════════════════════════════════════════════════════════════════


class MvStoreSales(StoreFlowBase):
    __tablename__ = "mv_store_sales"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    store_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_sales: Mapped[int] = mapped_column(Integer, nullable=False)
    total_revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    voided_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    voided_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_order_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    cash_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    card_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    transfer_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)


class MvStoreProductRanking(StoreFlowBase):
    __tablename__ = "mv_store_product_rankings"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    units_sold: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    revenue: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    avg_selling_price: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)


class MvStoreInventory(StoreFlowBase):
    __tablename__ = "mv_store_inventory"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    store_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reserved_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    available_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reorder_point: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
