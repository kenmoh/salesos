from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BusinessUpdate(Base):
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    tax_rate: Decimal | None = None
    currency: str | None = None
    settings: dict | None = None


class StoreCreate(Base):
    name: str = Field(..., min_length=2)
    address: str | None = None
    is_warehouse: bool = False


class StoreUpdate(Base):
    name: str | None = Field(default=None, min_length=2)
    address: str | None = None
    is_warehouse: bool | None = None


class StoreDistribute(Base):
    product_id: UUID
    to_store_id: UUID
    qty: Decimal = Field(..., gt=0)
    notes: str | None = None


class TransferRequestCreate(Base):
    product_id: UUID
    requesting_store_id: UUID
    supplying_store_id: UUID
    requested_qty: Decimal = Field(..., gt=0)
    notes: str | None = None


class TransferRequestApprove(Base):
    approved_qty: Decimal = Field(..., ge=0)
    rejection_reason: str | None = None
    notes: str | None = None


class StoreProductAdd(Base):
    product_id: str
    qty: float = Field(default=0, ge=0)


class ProductCreateForStore(Base):
    name: str = Field(..., min_length=1, max_length=200)
    sku: str | None = None
    description: str | None = None
    category_id: UUID | None = None
    unit: str = "unit"
    cost_price: Decimal = Decimal("0")
    selling_price: Decimal = Field(..., ge=0)
    tax_rate: Decimal | None = None
    reorder_point: int = 0
    image_url: str | None = None
    qty: float = Field(default=0, ge=0)


class StoreProductUpdate(Base):
    name: str | None = Field(default=None, max_length=200)
    sku: str | None = None
    selling_price: Decimal | None = None
    cost_price: Decimal | None = None
    tax_rate: Decimal | None = None
    reorder_point: int | None = None
    image_url: str | None = None
    status: str | None = None


class StoreSyncResult(Base):
    synced: int
    skipped: int


class SetMinStockLevel(Base):
    min_stock_level: float = Field(..., ge=0)


class CategoryCreate(Base):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None


class CategoryUpdate(Base):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None


class ProductCreate(Base):
    name: str = Field(..., min_length=1, max_length=200)
    sku: str | None = None
    description: str | None = None
    category_id: UUID | None = None
    unit: str = "unit"
    cost_price: Decimal = Decimal("0")
    selling_price: Decimal = Field(..., ge=0)
    tax_rate: Decimal | None = None
    reorder_point: int = 0
    image_url: str | None = None
    metadata: dict = {}


class ProductUpdate(Base):
    name: str | None = None
    sku: str | None = None
    description: str | None = None
    cost_price: Decimal | None = None
    selling_price: Decimal | None = None
    tax_rate: Decimal | None = None
    reorder_point: int | None = None
    is_active: bool | None = None
    image_url: str | None = None


class ProductListItem(Base):
    product_id: UUID
    sku: str | None
    name: str
    unit: str
    cost_price: Decimal
    selling_price: Decimal
    tax_rate: Decimal | None
    reorder_point: int
    total_qty: Decimal
    is_active: bool
    category_name: str | None
    image_url: str | None


class PaginatedProducts(Base):
    items: list[ProductListItem]
    total: int
    page: int
    page_size: int


class InventoryAdjust(Base):
    product_id: UUID
    reason: str
    qty_change: Decimal
    unit_cost: Decimal | None = None
    notes: str | None = None


class StockTransfer(Base):
    product_id: UUID
    from_store_id: UUID
    to_store_id: UUID
    qty: Decimal = Field(..., gt=0)
    notes: str | None = None


class SaleItem(Base):
    product_id: UUID
    product_name: str = Field(..., min_length=1)
    qty: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    discount_pct: Decimal = Decimal("0")
    tax_rate: Decimal | None = None


class SaleCreate(Base):
    store_id: UUID
    customer_name: str | None = None
    customer_phone: str | None = None
    notes: str | None = None
    discount: Decimal = Decimal("0")
    items: list[SaleItem] = Field(..., min_length=1)


class VoidSale(Base):
    reason: str = Field(..., min_length=3)


class SaleListItem(Base):
    sale_id: UUID
    sale_number: str
    status: str
    customer_name: str | None
    total_amount: Decimal
    amount_paid: Decimal
    cashier_name: str | None
    created_at: datetime


class PaginatedSales(Base):
    items: list[SaleListItem]
    total: int
    page: int
    page_size: int


class PaymentCreate(Base):
    sale_id: UUID
    method: str
    amount: Decimal = Field(..., gt=0)
    reference: str | None = None


class SplitPaymentCreate(Base):
    sale_id: UUID
    splits: dict = Field(
        ...,
        description="Split amounts: {cash: X, card: Y, transfer: Z}. Must sum to sale total.",
    )
    customer_email: str = Field(..., description="Required for card/transfer payment links")
    customer_name: str | None = None


class VirtualTerminalPaymentInit(Base):
    sale_id: UUID
    amount: Decimal = Field(..., gt=0)
    terminal_code: str
    customer_email: str | None = None
    customer_phone: str | None = None
    callback_url: str | None = None


class DocumentItem(Base):
    product_id: UUID | None = None
    description: str = Field(..., min_length=1)
    qty: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    discount_pct: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")


class DocumentCreate(Base):
    doc_type: str
    sale_id: UUID | None = None
    customer_name: str | None = None
    customer_email: EmailStr | None = None
    customer_phone: str | None = None
    customer_addr: str | None = None
    due_date: date | None = None
    notes: str | None = None
    terms: str | None = None
    items: list[DocumentItem] = Field(..., min_length=1)


class DocumentStatusUpdate(Base):
    status: str


class JournalEntry(Base):
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None


class JournalCreate(Base):
    description: str = Field(..., min_length=3)
    reference_id: UUID | None = None
    ref_type: str | None = None
    entries: list[JournalEntry] = Field(..., min_length=2)


class AccountCreate(Base):
    code: str
    name: str
    account_type: str
    parent_id: UUID | None = None


class SyncEvent(Base):
    event_type: str
    payload: dict
    client_id: str | None = None
    client_ts: datetime | None = None


class SyncBatch(Base):
    events: list[SyncEvent] = Field(..., min_length=1, max_length=500)


class SyncPendingParams(Base):
    since: datetime
    limit: int = Field(default=50, ge=1, le=500)


class SyncPendingResult(Base):
    events: list[SyncEvent]
    cursor: datetime


class SyncTriggerResult(Base):
    triggered: bool
    pending_count: int


class SyncServerEvent(Base):
    event_type: str
    payload: dict
    source: str = "server"


class DateRangeParams(Base):
    from_date: datetime
    to_date: datetime
    group_by: str = "day"
