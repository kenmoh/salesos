"""Catalog service database models.

This module defines the models for the product catalog domain:
- Product: Products available for sale
- Category: Product categorization

The catalog system provides:
- SKU and barcode management
- Tax rate configuration
- Inventory tracking
- QR code generation for products
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import StoreFlowBase


class Product(StoreFlowBase):
    """Represents a product available for sale.

    Products are the core entities in the catalog. Each product has
    pricing, inventory settings, and optional QR code data.

    Status values:
    - active: Product is available for sale
    - inactive: Product is hidden but not deleted
    - discontinued: Product is no longer sold

    Attributes:
        tenant_identifier: Reference to the tenant.
        public_identifier: Short public identifier for display.
        name: Product name.
        sku: Stock Keeping Unit (unique within tenant).
        barcode: Optional barcode (UPC, EAN, etc.).
        description: Optional product description.
        category_identifier: Reference to the category.
        unit: Unit of measurement (e.g., "unit", "kg", "litre").
        cost_price: Cost to the business.
        selling_price: Price for customers.
        tax_rate: Tax percentage (if different from tenant default).
        reorder_point: Minimum stock level for alerts.
        track_inventory: Whether to track inventory for this product.
        image_url: Optional product image URL.
        extra_metadata: Additional product data as JSON.
        status: Product status.
        qr_url: URL to the generated QR code.
        qr_asset_identifier: Cloudinary asset identifier for QR.
        qr_payload: Encoded QR code payload.
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="unit")
    cost_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    selling_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    track_inventory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True, default=dict
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    qr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qr_asset_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Category(StoreFlowBase):
    """Represents a product category scoped to a store.

    Categories organize products within a store. Each store has
    unique category names.

    Attributes:
        tenant_identifier: Reference to the tenant.
        store_identifier: Reference to the store this category belongs to.
        name: Category name (unique within store).
        description: Optional category description.
    """

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("store_id", "name", name="uq_category_store_name"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
