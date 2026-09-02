"""Discounts & Coupons models.

Promotions (discounts): percentage, fixed_amount, buy_x_get_y_free.
Coupons: code-based one-time or multi-use vouchers.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Discount(StoreFlowBase):
    """A promotion that can apply to carts matching certain criteria."""

    __tablename__ = "discounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)  # percentage | fixed_amount | buy_x_get_y
    value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    buy_x_get_y_free_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="all")  # all | specific_products | specific_categories
    min_order: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class DiscountProduct(StoreFlowBase):
    """Links a discount to specific products (when scope = specific_products)."""

    __tablename__ = "discount_products"
    __table_args__ = (
        UniqueConstraint("discount_id", "product_id", name="uq_discount_product"),
    )

    discount_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("discounts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)


class DiscountCategory(StoreFlowBase):
    """Links a discount to specific categories (when scope = specific_categories)."""

    __tablename__ = "discount_categories"
    __table_args__ = (
        UniqueConstraint("discount_id", "category_id", name="uq_discount_category"),
    )

    discount_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("discounts.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)


class Coupon(StoreFlowBase):
    """A code-based voucher that applies a discount at checkout."""

    __tablename__ = "coupons"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)  # percentage | fixed_amount
    value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = unlimited
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_order: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
