from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class SaleItemLine(BaseModel):
    product_id: UUID
    product_name: str = Field(..., min_length=1, max_length=200)
    qty: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    discount_pct: Decimal = Decimal("0")
    tax_rate: Decimal | None = None


class SaleCreateCommand(BaseModel):
    tenant_id: UUID
    cashier_id: UUID
    store_id: UUID
    customer_name: str | None = None
    customer_phone: str | None = None
    items: list[SaleItemLine] = Field(..., min_length=1)
    discount: Decimal = Decimal("0")
    notes: str | None = None
    correlation_id: str | None = None


class SaleResult(BaseModel):
    id: UUID
    tenant_id: UUID
    sale_number: str
    status: str
    subtotal: float
    discount: float
    tax: float
    total: float
    amount_paid: float
    item_count: int


class VoidSaleCommand(BaseModel):
    sale_id: UUID
    tenant_id: UUID
    voided_by: UUID
    reason: str = Field(..., min_length=1)
    correlation_id: str | None = None


class ConfirmSaleCommand(BaseModel):
    sale_id: UUID
    tenant_id: UUID
    correlation_id: str | None = None


class ReceiptCreateCommand(BaseModel):
    tenant_id: UUID
    sale_id: UUID
    receipt_number: str = Field(..., min_length=1, max_length=30)
    sent_via: str | None = None
    correlation_id: str | None = None
