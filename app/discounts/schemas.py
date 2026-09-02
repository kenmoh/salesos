"""Discount & Coupon schemas for routes and bridge."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
#  DISCOUNT (PROMOTION) SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class CreateDiscountRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    discount_type: str = Field(..., pattern="^(percentage|fixed_amount|buy_x_get_y)$")
    value: Decimal = Field(..., ge=0)
    buy_x_get_y_free_qty: int = Field(default=0, ge=0)
    scope: str = Field(default="all", pattern="^(all|specific_products|specific_categories)$")
    min_order: Decimal = Field(default=Decimal("0"), ge=0)
    is_active: bool = True
    start_date: datetime | None = None
    end_date: datetime | None = None
    product_ids: list[str] = Field(default_factory=list)
    category_ids: list[str] = Field(default_factory=list)


class UpdateDiscountRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    discount_type: str | None = Field(default=None, pattern="^(percentage|fixed_amount|buy_x_get_y)$")
    value: Decimal | None = Field(default=None, ge=0)
    buy_x_get_y_free_qty: int | None = Field(default=None, ge=0)
    scope: str | None = Field(default=None, pattern="^(all|specific_products|specific_categories)$")
    min_order: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    product_ids: list[str] | None = None
    category_ids: list[str] | None = None


class DiscountResponse(BaseModel):
    id: str
    name: str
    discount_type: str
    value: float
    buy_x_get_y_free_qty: int
    scope: str
    min_order: float
    is_active: bool
    start_date: str | None
    end_date: str | None
    product_ids: list[str] = []
    category_ids: list[str] = []
    created_at: str | None
    updated_at: str | None


# ═══════════════════════════════════════════════════════════════════════════════
#  COUPON SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class CreateCouponRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    discount_type: str = Field(..., pattern="^(percentage|fixed_amount)$")
    value: Decimal = Field(..., ge=0)
    max_uses: int = Field(default=0, ge=0)
    min_order: Decimal = Field(default=Decimal("0"), ge=0)
    is_active: bool = True
    expires_at: datetime | None = None


class UpdateCouponRequest(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    discount_type: str | None = Field(default=None, pattern="^(percentage|fixed_amount)$")
    value: Decimal | None = Field(default=None, ge=0)
    max_uses: int | None = Field(default=None, ge=0)
    min_order: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None
    expires_at: datetime | None = None


class CouponResponse(BaseModel):
    id: str
    code: str
    discount_type: str
    value: float
    max_uses: int
    used_count: int
    min_order: float
    is_active: bool
    expires_at: str | None
    created_at: str | None
    updated_at: str | None


class ValidateCouponRequest(BaseModel):
    coupon_code: str = Field(..., min_length=1, max_length=50)
    cart_subtotal: Decimal = Field(..., ge=0)


class ValidateCouponResponse(BaseModel):
    valid: bool
    coupon_id: str | None = None
    code: str | None = None
    discount_type: str | None = None
    discount_amount: float = 0
    final_total: float = 0
    message: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
#  CHECKOUT DISCOUNT INPUT
# ═══════════════════════════════════════════════════════════════════════════════


class CheckoutDiscountInput(BaseModel):
    coupon_code: str | None = None
    discount_id: str | None = None
