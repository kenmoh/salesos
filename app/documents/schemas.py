"""Pydantic schemas for document domain commands and results.

This module defines the input (Command) and output (Result) schemas used by
the document service layer. Commands represent user-initiated actions that
trigger business logic. Results represent the data returned after an operation.

Abbreviations Used in This Module
----------------------------------
- QT: Quote -- a price quotation offered to a customer.
- INV: Invoice -- a request for payment from a customer.
- RCP: Receipt -- proof that payment has been received.
- PO: Purchase Order -- an order placed with a supplier.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- VAT: Value Added Tax -- a consumption tax (Nigerian standard rate: 7.5%).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentItemLine(BaseModel):
    """A single line item within a document (quote, invoice, receipt, or PO).

    Each line item represents a product or service with its quantity, price,
    discount, and optional tax rate. The line total is calculated as:
        line_total = (qty × unit_price) - (qty × unit_price × discount_pct / 100)

    Attributes:
        product_id: Optional UUID of a Product in the catalog. None for custom items.
        description: Text description of the product or service.
        qty: Quantity of units (must be > 0). Supports decimals for weight-based items.
        unit_price: Price per unit in NGN (must be >= 0).
        discount_pct: Discount percentage (0-100). Default: 0 (no discount).
        tax_rate: Optional tax rate percentage (e.g., 7.5 for Nigerian VAT).
    """

    product_id: UUID | None = None
    description: str = Field(..., min_length=1, max_length=300)
    qty: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    discount_pct: Decimal = Decimal("0")
    tax_rate: Decimal | None = None


class DocumentCreateCommand(BaseModel):
    """Command to create a new document (quote, invoice, receipt, or purchase order).

    Documents are independent business records. They can be created standalone
    or linked to other documents or sales. The document number is auto-generated
    based on the document type prefix (QT, INV, RCP, PO).

    Document Types:
        - quote: Price quotation. Statuses: draft → sent → accepted/expired/void
        - invoice: Payment request. Statuses: draft → sent → paid/overdue/void
        - receipt: Proof of payment. Statuses: draft → issued/void
        - purchase_order: Supplier order. Statuses: draft → sent → confirmed/received/cancelled

    Accounting Impact:
        - When an invoice status changes to "sent", an AR record is created.
        - When an invoice status changes to "paid", the AR record is updated.
        - Quotes and purchase orders have no accounting impact.

    Attributes:
        tenant_id: The business tenant this document belongs to.
        actor_id: UUID of the user creating this document.
        doc_type: The type of document. Must be one of: quote, invoice, receipt, purchase_order.
        customer_name: Optional name of the customer or contact person.
        customer_email: Optional email address of the customer.
        customer_phone: Optional phone number of the customer.
        customer_address: Optional mailing or delivery address.
        due_date: Optional date by which payment is expected (for invoices).
        notes: Optional internal notes or comments.
        terms: Optional terms and conditions (e.g., "Payment due within 30 days").
        items: List of line items (at least 1 required).
        linked_sale_id: Optional UUID of a Sale this document is linked to.
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    tenant_id: UUID
    actor_id: UUID
    doc_type: str = Field(..., pattern="^(quote|invoice|receipt|purchase_order)$")
    customer_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    customer_address: str | None = None
    due_date: datetime | None = None
    notes: str | None = None
    terms: str | None = None
    items: list[DocumentItemLine] = Field(..., min_length=1)
    linked_sale_id: UUID | None = None
    correlation_id: str | None = None


class DocumentResult(BaseModel):
    """Result returned after creating or querying a document.

    Contains the document header information excluding line items.
    Use the get_document_by_id endpoint to retrieve full line items.

    Attributes:
        id: Unique identifier for this document (UUID).
        tenant_id: The business tenant this document belongs to.
        doc_number: Auto-generated document number (e.g., "INV-20260826-A1B2C3D4").
        doc_type: The type of document (quote, invoice, receipt, purchase_order).
        status: Current status of the document.
        subtotal: Sum of line totals before discount and tax (NGN).
        discount: Total discount amount (NGN).
        tax: Total tax amount (NGN).
        total: Final amount after discount and tax (NGN).
        item_count: Number of line items in this document.
        due_date: Optional date by which payment is expected.
        linked_sale_id: Optional UUID of the linked Sale.
    """

    id: UUID
    tenant_id: UUID
    doc_number: str
    doc_type: str
    status: str
    subtotal: float
    discount: float
    tax: float
    total: float
    item_count: int
    due_date: datetime | None = None
    linked_sale_id: str | None = None


class DocumentStatusCommand(BaseModel):
    """Command to change the status of a document.

    Status transitions are validated based on the document type. For example,
    an invoice can only transition from "draft" to "sent", not directly to "paid".

    Valid Transitions:
        - quote:      draft → sent → accepted | expired | void
        - invoice:    draft → sent → paid | overdue | void
        - receipt:    draft → issued | void
        - purchase_order: draft → sent → confirmed | received | cancelled

    Accounting Impact:
        - When invoice status changes to "sent": Creates AR record and journal.
        - When invoice status changes to "paid": Updates AR and creates journal.

    Attributes:
        document_id: UUID of the document to update.
        tenant_id: The business tenant this document belongs to.
        actor_id: UUID of the user performing this status change.
        new_status: The desired new status (must be a valid transition).
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    document_id: UUID
    tenant_id: UUID
    actor_id: UUID
    new_status: str
    correlation_id: str | None = None


class ConvertQuoteToSaleCommand(BaseModel):
    """Command to convert a quote or invoice into a completed sale.

    This command validates that the document is a quote or invoice in "sent"
    or "accepted" status, then creates a Sale with the document's line items.
    The document is marked as "accepted" and linked to the new sale.

    Attributes:
        document_id: UUID of the document to convert.
        tenant_id: The business tenant this document belongs to.
        cashier_id: UUID of the user (cashier) performing the conversion.
        correlation_id: Optional request correlation ID for distributed tracing.
    """

    document_id: UUID
    tenant_id: UUID
    cashier_id: UUID
    correlation_id: str | None = None
