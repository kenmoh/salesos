from uuid import UUID

from pydantic import BaseModel, Field


class StoreCreateCommand(BaseModel):
    tenant_id: UUID
    name: str = Field(..., min_length=1, max_length=100)
    address: str | None = None
    is_warehouse: bool = False


class StoreUpdateCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = None
    is_warehouse: bool | None = None


class StoreResult(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    is_warehouse: bool
    status: str



class SetMinStockLevelCommand(BaseModel):
    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    min_stock_level: float = Field(..., ge=0)


class StoreProductCreateCommand(BaseModel):
    """Command for creating a store-specific product record.

    Used when creating a product directly in a store or when syncing
    from another store or the catalog template.
    """

    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    sku: str | None = None
    selling_price: float = Field(..., ge=0)
    cost_price: float = Field(default=0, ge=0)
    tax_rate: float | None = None
    reorder_point: int = Field(default=0, ge=0)
    image_url: str | None = None
    status: str = "active"
    extra_metadata: dict | None = None


class StoreProductUpdateCommand(BaseModel):
    """Command for updating a store-specific product record.

    All fields are optional — only provided fields are updated.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = None
    selling_price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    tax_rate: float | None = None
    reorder_point: int | None = Field(default=None, ge=0)
    image_url: str | None = None
    status: str | None = None
    extra_metadata: dict | None = None


class StoreProductResult(BaseModel):
    """Read model for a store-specific product record."""

    id: UUID
    tenant_id: UUID
    store_id: UUID
    product_id: UUID
    name: str
    sku: str | None
    selling_price: float
    cost_price: float
    tax_rate: float | None
    reorder_point: int
    image_url: str | None
    status: str
    extra_metadata: dict | None


class SyncProductsCommand(BaseModel):
    """Command for syncing products to a target store.

    When ``from_store_id`` is provided, values are copied from that
    store's ``store_products`` records. When omitted, values are
    copied from the ``catalog.products`` template.

    At least one of ``product_ids`` or ``all`` must be provided.
    """

    product_ids: list[UUID] | None = None
    sync_all: bool = Field(default=False, alias="all")
    from_store_id: UUID | None = None


class SyncProductsResult(BaseModel):
    """Result of a product sync operation."""

    synced: int
    skipped: int
    errors: list[str]


class StoreProductListQuery(BaseModel):
    """Query parameters for listing store products."""

    tenant_id: UUID
    store_id: UUID
    status: str | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 50
