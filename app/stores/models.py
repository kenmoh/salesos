"""Stores service database models.

This module defines the models for store/warehouse locations and
per-store product overrides:
- Store: Represents a physical store or warehouse
- StoreProduct: Store-specific product data (price, name, SKU, etc.)

Stores are used to organize inventory and sales by location.
StoreProduct enables each store to have independent pricing and
product settings while sharing a common catalog template.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import StoreFlowBase


class Store(StoreFlowBase):
    """Represents a store or warehouse location.

    Stores organize inventory and sales within a tenant. A store
    can be a physical retail location or a warehouse.

    Status values:
    - active: Store is operational
    - inactive: Store is temporarily closed
    - closed: Store is permanently closed

    Attributes:
        tenant_identifier: Reference to the tenant.
        name: Store name (e.g., "Main Branch", "Warehouse A").
        address: Optional physical address.
        is_warehouse: True if this is a warehouse (not a retail store).
        status: Store status.
        """

    __tablename__ = "stores"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_warehouse: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class StoreProduct(StoreFlowBase):
    """Store-specific product data with independent pricing and settings.

    Links a catalog product template to a specific store with
    store-specific overrides. Each store can have different prices,
    names, SKUs, and inventory settings for the same product.

    The catalog.products table holds the master template. This table
    holds per-store customizations. When a product is created with a
    store_id, both records are created. When synced, a new StoreProduct
    record is created in the target store.

    Status values:
    - active: Product is available in this store
    - inactive: Product is hidden in this store
    - discontinued: Product is no longer sold in this store

    Attributes:
        tenant_identifier: Reference to the tenant.
        store_identifier: Reference to the store.
        product_identifier: Reference to the catalog product template.
        name: Store-specific product display name.
        sku: Store-specific SKU (unique per tenant).
        selling_price: Store-specific selling price.
        cost_price: Store-specific cost price.
        tax_rate: Store-specific tax rate.
        reorder_point: Minimum stock level for alerts in this store.
        image_url: Store-specific product image URL.
        status: Product status within this store.
        extra_metadata: Additional store-specific product data as JSON.
    """

    __tablename__ = "store_products"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", name="uq_store_product"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    selling_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    cost_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
