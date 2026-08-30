"""Cart service database models.

This module defines the models for the shopping cart domain:
- Cart: Represents a customer's shopping session
- CartItem: Individual products added to the cart

Carts are time-limited and expire after a configurable period.
When a cart is checked out, it is converted into a Sale.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Cart(StoreFlowBase):
    """Represents a customer's shopping cart session.

    A Cart tracks products a customer intends to purchase. It has
    an expiration time to prevent abandoned carts from consuming
    resources indefinitely.

    Status values:
    - active: Cart is being used
    - checked_out: Cart converted to a sale
    - expired: Cart has expired

    Attributes:
        tenant_identifier: Business/tenant identifier.
        session_identifier: Unique session identifier for the cart.
        status: Current cart status.
        customer_name: Optional customer name.
        customer_phone: Optional customer phone number.
        created_identifier: User who created the cart.
        expires_at: When the cart expires (auto-cleanup).
    """

    __tablename__ = "carts"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    cart_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    customer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class CartItem(StoreFlowBase):
    """Represents an individual product in a cart.

    Each CartItem tracks a product quantity and its price at the
    time it was added to the cart.

    Attributes:
        cart_identifier: Reference to the parent cart.
        product_identifier: Reference to the product.
        product_public_identifier: Short public identifier for display.
        name: Product name at time of cart creation.
        unit_price: Price per unit at time of cart creation.
        qty: Quantity in the cart.
        created_identifier: User who added this item.
    """

    __tablename__ = "cart_items"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    cart_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    product_public_id: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=1)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
