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
    supplying_product_id: UUID
    item_name: str
    request_number: str
    requested_qty: Decimal = Field(..., gt=0)
    notes: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None


class TransferRequestApproveCommand(BaseModel):
    tenant_id: UUID
    request_id: UUID
    approved_qty: Decimal = Field(..., gt=0)
    approved_by: UUID | None = None
    pin_verified_by: UUID | None = None
    notes: str | None = None
    correlation_id: str | None = None


class TransferRequestRejectCommand(BaseModel):
    tenant_id: UUID
    request_id: UUID
    rejected_by: UUID | None = None
    pin_verified_by: UUID | None = None
    rejection_reason: str | None = None
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
    request_number: str | None = None
    item_name: str | None = None
    supplying_product_id: UUID | None = None
    approved_at: str | None = None
    rejected_at: str | None = None
    fulfilled_by: UUID | None = None
    fulfilled_at: str | None = None
    fulfilled_qty: float | None = None
    pin_verified_by: UUID | None = None
    decision_note: str | None = None
    created_at: str
    updated_at: str


class StoreStockDetail(BaseModel):
    store_id: UUID
    store_name: str
    is_warehouse: bool
    stock_balances: list[StockBalanceResult]


# ── Distribution lines / commands ────────────────────────────────────────────


class DistributionLineCreate(BaseModel):
    product_id: UUID
    item_name: str = Field(..., min_length=1, max_length=200)
    qty_dispatched: Decimal = Field(..., gt=0)
    condition_dispatched: str | None = None


class DistributionCreateCommand(BaseModel):
    tenant_id: UUID
    from_store_id: UUID  # warehouse
    to_store_id: UUID
    lines: list[DistributionLineCreate] = Field(..., min_length=1)
    notes: str | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None


class DistributionLineReceive(BaseModel):
    item_id: UUID
    qty_received: Decimal = Field(..., ge=0)
    qty_damaged: Decimal = Field(..., ge=0)
    condition_received: str | None = None
    receive_notes: str | None = None


class DistributionReceiveCommand(BaseModel):
    tenant_id: UUID
    to_store_id: UUID
    distribution_id: UUID
    lines: list[DistributionLineReceive] = Field(..., min_length=1)
    actor_id: UUID | None = None
    pin_verified_by: UUID | None = None
    correlation_id: str | None = None


class DistributionCancelCommand(BaseModel):
    tenant_id: UUID
    distribution_id: UUID
    actor_id: UUID | None = None
    cancel_reason: str | None = None
    pin_verified_by: UUID | None = None
    correlation_id: str | None = None


# ── Distribution results ─────────────────────────────────────────────────────


class DistributionItemResult(BaseModel):
    id: UUID
    distribution_id: UUID
    source_product_id: UUID
    destination_product_id: UUID | None
    item_name: str
    qty_dispatched: float
    qty_received: float
    qty_damaged: float
    shortage: float
    condition_dispatched: str | None
    condition_received: str | None
    damage_notes: str | None
    receive_notes: str | None
    notes: str | None
    created_at: str


class DistributionResult(BaseModel):
    id: UUID
    tenant_id: UUID
    distribution_number: str
    from_store_id: UUID
    to_store_id: UUID
    status: str
    notes: str | None
    dispatched_by: UUID | None
    dispatched_at: str | None
    received_by: UUID | None
    received_at: str | None
    cancelled_by: UUID | None
    cancelled_at: str | None
    cancel_reason: str | None
    pin_verified_by: UUID | None
    created_by: UUID | None
    created_at: str
    updated_at: str
    items: list[DistributionItemResult] = []
    qty_dispatched: float = 0
    qty_received: float = 0
    qty_damaged: float = 0
    shortage: float = 0
