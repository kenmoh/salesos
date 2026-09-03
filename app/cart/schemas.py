from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
#  SERVICE-LAYER COMMAND SCHEMAS (used by bridge.py / service)
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTE REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class CartCreateRequest(BaseModel):
    store_id: str
    customer_name: str | None = None
    customer_phone: str | None = None


class CheckoutRequest(BaseModel):
    store_id: str | None = None
    items: list[CheckoutItem] | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    coupon_code: str | None = None
    discount_id: str | None = None


class VoidItemRequest(BaseModel):
    supervisor_pin: str


class AddItemRequest(BaseModel):
    product_id: str
    qty: float = Field(default=1, gt=0)


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTE RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════


class CartItemResponse(BaseModel):
    id: str = ""
    product_id: str = ""
    product_public_id: str = ""
    name: str = ""
    unit_price: float = 0
    qty: float = 0


class CartCreatedResponse(BaseModel):
    id: str = ""
    session_id: str = ""
    store_id: str | None = None
    status: str = "active"
    resumed: bool = False


class CartDetailResponse(BaseModel):
    id: str = ""
    session_id: str = ""
    status: str = "active"
    customer_name: str | None = None
    customer_phone: str | None = None
    expires_at: str | None = None
    items: list[CartItemResponse] = []


class CheckoutResultResponse(BaseModel):
    sale_id: str = ""
    sale_number: str = ""
    subtotal: float = 0
    discount: float = 0
    total: float = 0
    amount_paid: float = 0
    status: str = "pending"
    coupon_code: str | None = None


class CartListItemResponse(BaseModel):
    id: str = ""
    session_id: str = ""
    status: str = "active"
    customer_name: str | None = None
    customer_phone: str | None = None
    item_count: int = 0
    total: float = 0
    created_at: str = ""
