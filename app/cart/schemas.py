from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class CartCreateCommand(BaseModel):
    store_id: UUID
    customer_name: str | None = None
    customer_phone: str | None = None


class CartResult(BaseModel):
    id: UUID
    tenant_id: UUID
    session_id: str
    status: str
    customer_name: str | None
    customer_phone: str | None
    created_by: str | None = None
    item_count: int = 0
    total: float = 0


class AddItemCommand(BaseModel):
    cart_id: UUID
    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    product_public_id: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=200)
    unit_price: Decimal = Field(..., ge=0)
    qty: Decimal = Field(..., gt=0)
    created_by: UUID | None = None
    correlation_id: str | None = None


class RemoveItemCommand(BaseModel):
    cart_id: UUID
    tenant_id: UUID
    item_id: UUID
    created_by: UUID | None = None
    approved_by: UUID | None = None
    correlation_id: str | None = None


class CheckoutItem(BaseModel):
    product_public_id: str = Field(..., min_length=1, max_length=20)
    qty: Decimal = Field(..., gt=0)
    store_id: UUID | None = None


class CheckoutCommand(BaseModel):
    cart_id: UUID
    tenant_id: UUID
    items: list[CheckoutItem] | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None
