from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class StoreResult(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    is_warehouse: bool
    status: str



class AdjustStockCommand(BaseModel):
    tenant_id: UUID
    product_id: UUID
    store_id: UUID
    reason: str = Field(..., min_length=1, max_length=100)
    qty_change: Decimal = Field(..., ge=0)
    unit_cost: Decimal | None = None
    notes: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None


class TransferStockCommand(BaseModel):
    tenant_id: UUID
    product_id: UUID
    from_store_id: UUID
    to_store_id: UUID
    qty: Decimal = Field(..., gt=0)
    notes: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None


class ReserveStockCommand(BaseModel):
    tenant_id: UUID
    product_id: UUID
    store_id: UUID
    sale_id: UUID
    qty: Decimal = Field(..., gt=0)
    correlation_id: str | None = None


class StockBalanceResult(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    store_id: UUID
    qty: float
    reserved_qty: float
    available_qty: float
    min_stock_level: float
    unit_cost: float | None


class LowStockItem(BaseModel):
    product_id: UUID
    product_name: str
    store_id: UUID
    store_name: str
    current_qty: float
    reorder_point: float


class TransferRequestCreateCommand(BaseModel):
    tenant_id: UUID
    product_id: UUID
    requesting_store_id: UUID
    supplying_store_id: UUID
    requested_qty: Decimal = Field(..., gt=0)
    notes: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None


class TransferRequestApproveCommand(BaseModel):
    tenant_id: UUID
    request_id: UUID
    approved_qty: Decimal = Field(..., ge=0)
    rejection_reason: str | None = None
    approved_by: UUID | None = None
    notes: str | None = None
    correlation_id: str | None = None


class TransferRequestResult(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    requesting_store_id: UUID
    supplying_store_id: UUID
    requested_qty: float
    approved_qty: float | None
    status: str
    notes: str | None
    rejection_reason: str | None
    created_by: UUID | None
    approved_by: UUID | None
    created_at: str
    updated_at: str


class StoreStockDetail(BaseModel):
    store_id: UUID
    store_name: str
    is_warehouse: bool
    stock_balances: list[StockBalanceResult]
