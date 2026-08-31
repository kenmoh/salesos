"""Sales service database models.

This module defines the core models for the sales domain:
- Sale: Represents a complete transaction with payment tracking
- SaleItem: Individual line items within a sale
- Receipt: Generated receipt records for completed sales

These models are used in the sales service and are accessed
through repository functions for database operations.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Sale(StoreFlowBase):
    """Represents a complete sales transaction.

    A Sale tracks the entire lifecycle of a customer purchase,
    from creation through payment completion. It stores:
    - Basic sale information (number, status, customer details)
    - Financial amounts (subtotal, discount, tax, total, amount paid)
    - Payment method breakdown (JSONB for flexible payment tracking)
    - Void information (if the sale is cancelled)

    The payment_methods JSONB column stores detailed payment information:
    - cash: Amount paid in cash
    - card: Amount paid via card
    - transfer: Amount paid via bank transfer
    - split: Nested payment method amounts for split payments
    - total: Total payment amount
    - compare_total: For validating split sum
    - platform_fee: Platform commission fee
    - settlement_amount: Net amount after fee deduction
    - fee_rule_id: Reference to the fee rule used

    Status values: pending, completed, voided, refunded

    Attributes:
        tenant_identifier: Business/tenant identifier.
        sale_number: Unique sequential sale number (e.g., "SALE-001").
        status: Current sale status.
        customer_name: Optional customer name.
        customer_phone: Optional customer phone number.
    store_identifier: Reference to the store.
    cashier_identifier: Reference to the cashier.
        subtotal: Sum of all line items before discount/tax.
        discount: Total discount amount.
        tax: Total tax amount.
        total: Final amount (subtotal - discount + tax).
        amount_paid: Total amount paid by customer.
        payment_methods: JSONB with payment method breakdown.
        notes: Optional sale notes.
        void_reason: Reason if sale is voided.
        voided_identifier: User who voided the sale.
        voided_at: Timestamp when sale was voided.
    """

    __tablename__ = "sales"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    customer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    subtotal: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    discount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    tax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    amount_paid: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    payment_methods: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SaleItem(StoreFlowBase):
    """Represents an individual line item within a sale.

    Each SaleItem tracks a single product quantity with its pricing
    and discount information. Multiple SaleItems form a complete Sale.

    Attributes:
        sale_identifier: Reference to the parent sale.
        product_identifier: Reference to the product.
        product_name: Denormalized product name for quick display.
        qty: Quantity purchased.
        unit_price: Price per unit at time of sale.
        discount_pct: Percentage discount applied to this item.
        tax_rate: Tax rate applied (if different from sale-level).
        line_total: Final line total after discount and tax.
    """

    __tablename__ = "sale_items"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    line_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)


class Receipt(StoreFlowBase):
    """Represents a receipt generated for a completed sale.

    Receipts are created when a sale is completed and can be
    delivered to customers via email, SMS, or printed.

    Attributes:
        tenant_identifier: Business/tenant identifier.
        sale_identifier: Reference to the completed sale.
        receipt_number: Unique receipt number for display.
        pdf_url: Optional URL to the generated PDF receipt.
        sent_via: Delivery method (e.g., "email", "sms", "print").
        sent_at: Timestamp when receipt was sent to customer.
    """

    __tablename__ = "receipts"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), unique=True, nullable=False, index=True
    )
    receipt_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_via: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
