"""Document domain models for quotes, invoices, receipts, and purchase orders.

This module defines the SQLAlchemy ORM models for the document management system.
Documents are independent business records that can stand alone or be linked to
sales. Each document type has its own lifecycle and status transitions.

Abbreviations Used in This Module
----------------------------------
- PO: Purchase Order -- an order placed with a supplier for goods/services.
- QT: Quote -- a price quotation offered to a customer.
- INV: Invoice -- a request for payment from a customer.
- RCP: Receipt -- proof that payment has been received.
- FK: Foreign Key -- a constraint that links two tables together.
- PK: Primary Key -- a unique identifier for each row in a table.
- UUID: Universally Unique Identifier -- a 128-bit identifier used for primary keys.
- UTC: Coordinated Universal Time -- the primary time standard.
- PG_UUID: PostgreSQL UUID type -- a native UUID column type for PostgreSQL databases.
- Mapped: SQLAlchemy type annotation that maps a Python type to a database column.
- Numeric(p, s): A fixed-precision decimal type with p total digits and s digits after decimal.

Document Type Conventions:
    - QT-YYYYMMDD-XXXXXXXX: Quote document numbers
    - INV-YYYYMMDD-XXXXXXXX: Invoice document numbers
    - RCP-YYYYMMDD-XXXXXXXX: Receipt document numbers
    - PO-YYYYMMDD-XXXXXXXX: Purchase Order document numbers

Status Lifecycle by Document Type:
    - quote:      draft → sent → accepted | expired | void
    - invoice:    draft → sent → paid | overdue | void
    - receipt:    draft → issued | void
    - purchase_order: draft → sent → confirmed | received | cancelled
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import StoreFlowBase


class Document(StoreFlowBase):
    """Represents a business document (quote, invoice, receipt, or purchase order).

    Documents are independent business records that track commercial interactions
    with customers and suppliers. Each document contains line items, financial
    totals, and customer information.

    Document Independence:
        - Quotes, invoices, receipts, and purchase orders can all be created independently.
        - Documents can optionally be linked to each other or to sales.
        - A quote can be converted to an invoice, which can be converted to a sale.
        - A receipt can be linked to an invoice to record payment.

    Accounting Integration:
        - When an invoice status changes to "sent", an Accounts Receivable (AR) record
          is automatically created in the accounting system.
        - When an invoice status changes to "paid", the AR record is updated.
        - Quotes and purchase orders have no direct accounting impact.

    Attributes:
        id: Unique identifier for this document (UUID, auto-generated).
        tenant_id: The business tenant this document belongs to (multi-tenant isolation).
        doc_number: Auto-generated document number (e.g., "INV-20260826-A1B2C3D4").
            Format: PREFIX-YYYYMMDD-XXXXXXXX where PREFIX is QT/INV/RCP/PO.
        doc_type: The type of document. One of: "quote", "invoice", "receipt",
            "purchase_order". Determines valid status transitions.
        status: Current status of the document. Valid transitions depend on doc_type.
            See VALID_TRANSITIONS in service.py for the complete mapping.
        customer_name: Optional name of the customer or contact person.
        customer_email: Optional email address of the customer.
        customer_phone: Optional phone number of the customer.
        customer_address: Optional mailing or delivery address of the customer.
        subtotal: Sum of all line item totals before discount and tax (in NGN).
        discount: Total discount amount applied across all line items (in NGN).
        tax: Total tax amount applied across all line items (in NGN).
        total: Final amount after discount and tax (subtotal - discount + tax) in NGN.
        due_date: Optional date by which payment is expected (for invoices).
            Used for Accounts Receivable aging reports.
        notes: Optional internal notes or comments on the document.
        terms: Optional terms and conditions (e.g., "Payment due within 30 days").
        linked_sale_id: Optional UUID linking this document to a Sale record.
            Set when a quote/invoice is converted to a sale via convert_document_to_sale().
        pdf_url: Optional URL to a generated PDF version of the document (stored in Cloudinary).
        created_by: UUID of the user who created this document.
        created_at: Timestamp when this document was created (UTC).
        updated_at: Timestamp when this document was last updated (UTC).
            Automatically updated on any modification.
    """

    __tablename__ = "documents"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    doc_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subtotal: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    discount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    tax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_sale_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DocumentItem(StoreFlowBase):
    """Represents a single line item within a document.

    Each document contains one or more line items representing products or services
    being quoted, invoiced, or receipted. Line items calculate their own totals
    based on quantity, unit price, discount, and tax rate.

    Line Total Calculation:
        line_total = (qty × unit_price) - (qty × unit_price × discount_pct / 100)

    Example:
        A document with 3 units of "Samsung Galaxy A14" at NGN 85,000 each
        with 5% discount would have:
            qty = 3
            unit_price = 85000
            discount_pct = 5
            line_total = (3 × 85000) - (3 × 85000 × 5 / 100) = 242,250

    Attributes:
        id: Unique identifier for this line item (UUID, auto-generated).
        document_id: Foreign key linking to the parent Document record.
        product_id: Optional foreign key linking to a Product in the catalog.
            None for custom/non-catalog items.
        description: Text description of the product or service.
            Example: "Samsung Galaxy A14 - Black, 128GB".
        qty: Quantity of units (supports decimals for weight-based items).
        unit_price: Price per unit in NGN before discount and tax.
        discount_pct: Discount percentage applied to this line item (0-100).
            Example: 5.00 means 5% discount.
        tax_rate: Optional tax rate percentage applied to this line item.
            Example: 7.5 means 7.5% VAT (Nigerian standard rate).
        line_total: Calculated total for this line item after discount.
            Formula: (qty × unit_price) - (qty × unit_price × discount_pct / 100).
    """

    __tablename__ = "document_items"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    line_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
