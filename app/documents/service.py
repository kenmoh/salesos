"""Document service layer for planning business logic.

This module contains pure planning functions that validate business rules and
create model instances without persisting them. The actual persistence is handled
by the repository layer. This separation keeps business logic testable and
independent of database concerns.

Planning Function Pattern:
    1. Accept a Command (Pydantic schema) as input
    2. Validate business rules (e.g., valid status transitions)
    3. Create model instances (in-memory, not persisted)
    4. Return a tuple of (Result, Model, [optional models], [OutboxWrite events])

Abbreviations Used in This Module
----------------------------------
- QT: Quote -- a price quotation offered to a customer.
- INV: Invoice -- a request for payment from a customer.
- RCP: Receipt -- proof that payment has been received.
- PO: Purchase Order -- an order placed with a supplier.
- AR: Accounts Receivable -- money owed TO the business by customers.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- OutboxWrite: A record to be written to the transactional outbox table for
    reliable event publishing (ensures events are published exactly once).

Document Lifecycle:
    - Documents start in "draft" status and can be edited.
    - When sent to a customer/supplier, status changes to "sent".
    - For invoices: "sent" → "paid" (customer paid) or "overdue" (past due date).
    - For quotes: "sent" → "accepted" (customer accepted) or "expired" (past有效期).
    - Documents can be voided at any point to cancel them.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.common.events.outbox import OutboxWrite
from app.documents.events import (
    document_created_event,
    document_status_changed_event,
)
from app.documents.models import Document, DocumentItem
from app.documents.schemas import (
    DocumentCreateCommand,
    DocumentResult,
    DocumentStatusCommand,
)

# Valid status transitions for each document type.
# Keys are document types, values are lists of valid statuses.
# Transitions flow from left to right (e.g., draft → sent → paid).
VALID_TRANSITIONS: dict[str, list[str]] = {
    "quote": ["draft", "sent", "accepted", "expired", "void"],
    "invoice": ["draft", "sent", "paid", "overdue", "void"],
    "receipt": ["draft", "issued", "void"],
    "purchase_order": ["draft", "sent", "confirmed", "received", "cancelled"],
}


def _new_doc_number(doc_type: str) -> str:
    """Generate a unique document number based on the document type.

    Document numbers follow the format: PREFIX-YYYYMMDD-XXXXXXXX
    Where PREFIX is determined by the document type:
        - QT: Quote
        - INV: Invoice
        - RCP: Receipt
        - PO: Purchase Order

    Args:
        doc_type: The type of document (quote, invoice, receipt, purchase_order).

    Returns:
        A unique document number string.

    Example:
        >>> _new_doc_number("invoice")
        'INV-20260826-A1B2C3D4'
    """
    prefix = {"quote": "QT", "invoice": "INV", "receipt": "RCP", "purchase_order": "PO"}
    p = prefix.get(doc_type, "DOC")
    return f"{p}-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def plan_document_creation(
    command: DocumentCreateCommand,
) -> tuple[DocumentResult, Document, list[DocumentItem], list[OutboxWrite]]:
    """Plan the creation of a new document with line items.

    This function validates the command, calculates totals, and creates in-memory
    Document and DocumentItem models. It does NOT persist to the database.

    Financial Calculations:
        1. subtotal = sum(qty × unit_price) for all items
        2. discount = sum(qty × unit_price × discount_pct / 100) for all items
        3. tax = sum(qty × unit_price × tax_rate / 100) for all items
        4. total = (subtotal - discount) + tax

    Args:
        command: The DocumentCreateCommand containing document details and line items.
            Must include: tenant_id, actor_id, doc_type, items (at least 1).
            Optional: customer info, due_date, notes, terms, linked_sale_id.

    Returns:
        A tuple of:
            - DocumentResult: Pydantic result schema for API responses.
            - Document: SQLAlchemy model instance for database persistence.
            - list[DocumentItem]: List of line item models.
            - list[OutboxWrite]: Outbox events for reliable event publishing.

    Example:
        >>> command = DocumentCreateCommand(
        ...     tenant_id=uuid4(),
        ...     actor_id=uuid4(),
        ...     doc_type="invoice",
        ...     customer_name="John Doe",
        ...     items=[DocumentItemLine(description="Phone", qty=1, unit_price=85000)],
        ... )
        >>> result, doc, items, events = plan_document_creation(command)
        >>> print(result.doc_number)
        INV-20260826-A1B2C3D4
    """
    doc_id = uuid4()
    doc_number = _new_doc_number(command.doc_type)

    # Calculate financial totals from line items
    subtotal = sum(float(i.qty) * float(i.unit_price) for i in command.items)
    discount_amt = sum(
        float(i.qty) * float(i.unit_price) * (float(i.discount_pct) / 100) for i in command.items
    )
    taxable = subtotal - discount_amt
    tax = sum(
        float(i.qty) * float(i.unit_price) * (float(i.tax_rate or 0) / 100) for i in command.items
    )
    total = taxable + tax

    # Receipts linked to sales start as "issued" (no draft needed)
    initial_status = "draft"
    if command.doc_type == "receipt" and command.linked_sale_id:
        initial_status = "issued"

    # Create the document header
    doc = Document(
        id=doc_id,
        tenant_id=command.tenant_id,
        doc_number=doc_number,
        doc_type=command.doc_type,
        status=initial_status,
        customer_name=command.customer_name,
        customer_email=command.customer_email,
        customer_phone=command.customer_phone,
        customer_address=command.customer_address,
        subtotal=subtotal,
        discount=discount_amt,
        tax=tax,
        total=total,
        due_date=command.due_date,
        notes=command.notes,
        terms=command.terms,
        linked_sale_id=command.linked_sale_id,
        created_by=command.actor_id,
    )

    # Create line items
    items = []
    for line in command.items:
        qty = float(line.qty)
        unit_price = float(line.unit_price)
        discount_pct = float(line.discount_pct)
        tax_rate = float(line.tax_rate or 0)
        line_discount = unit_price * qty * (discount_pct / 100)
        line_total = (unit_price * qty) - line_discount

        item = DocumentItem(
            id=uuid4(),
            document_id=doc_id,
            product_id=line.product_id,
            description=line.description,
            qty=qty,
            unit_price=unit_price,
            discount_pct=discount_pct,
            tax_rate=tax_rate if tax_rate else None,
            line_total=line_total,
        )
        items.append(item)

    # Create the document_created event for the outbox
    event = document_created_event(
        tenant_id=command.tenant_id,
        document_id=doc_id,
        doc_number=doc_number,
        doc_type=command.doc_type,
        total=str(total),
        created_by=command.actor_id,
        item_count=len(items),
        correlation_id=command.correlation_id,
    )

    result = DocumentResult(
        id=doc_id,
        tenant_id=command.tenant_id,
        doc_number=doc_number,
        doc_type=command.doc_type,
        status=initial_status,
        subtotal=subtotal,
        discount=discount_amt,
        tax=tax,
        total=total,
        item_count=len(items),
        due_date=command.due_date,
        linked_sale_id=str(command.linked_sale_id) if command.linked_sale_id else None,
    )

    outbox = [OutboxWrite(event=event, aggregate_type="document", aggregate_id=str(doc_id))]
    return result, doc, items, outbox


def plan_status_change(
    command: DocumentStatusCommand, doc: Document
) -> tuple[Document, list[OutboxWrite]]:
    """Plan a status change for a document with transition validation.

    This function validates that the requested status transition is allowed for
    the document type, then creates the status change event.

    Valid Transitions:
        - quote:      draft → sent → accepted | expired | void
        - invoice:    draft → sent → paid | overdue | void
        - receipt:    draft → issued | void
        - purchase_order: draft → sent → confirmed | received | cancelled

    Accounting Impact (handled by worker event handler):
        - invoice sent → sent: Creates AR record and journal entry
        - invoice sent → paid: Updates AR and creates journal entry

    Args:
        command: The DocumentStatusCommand containing the new status.
        doc: The current Document model instance.

    Returns:
        A tuple of:
            - Document: Updated Document model with new status.
            - list[OutboxWrite]: Outbox events for the status change.

    Raises:
        ValueError: If the status transition is not allowed for this document type.

    Example:
        >>> command = DocumentStatusCommand(
        ...     document_id=doc.id,
        ...     tenant_id=doc.tenant_id,
        ...     actor_id=user_id,
        ...     new_status="sent",
        ... )
        >>> updated_doc, events = plan_status_change(command, doc)
    """
    # Validate the status transition
    allowed = VALID_TRANSITIONS.get(doc.doc_type, [])
    if command.new_status not in allowed:
        raise ValueError(
            f"Invalid status '{command.new_status}' for {doc.doc_type}. Allowed: {allowed}"
        )

    old_status = doc.status
    doc.status = command.new_status

    # Create the status_changed event for the outbox
    # Includes doc_type, total, customer info, and due_date for accounting worker
    event = document_status_changed_event(
        tenant_id=command.tenant_id,
        document_id=command.document_id,
        doc_number=doc.doc_number,
        doc_type=doc.doc_type,
        old_status=old_status,
        new_status=command.new_status,
        actor_id=command.actor_id,
        total=str(doc.total) if doc.total else None,
        customer_name=doc.customer_name,
        customer_id=doc.created_by,
        due_date=doc.due_date.isoformat() if doc.due_date else None,
        correlation_id=command.correlation_id,
    )

    outbox = [
        OutboxWrite(event=event, aggregate_type="document", aggregate_id=str(command.document_id))
    ]
    return doc, outbox


def plan_accept_quote(doc: Document) -> tuple[Document, list[OutboxWrite]]:
    """Plan the acceptance of a quote for conversion to a sale.

    This function validates that the document is a quote in "sent" or "accepted"
    status, then marks it as "accepted" and creates the status change event.

    Args:
        doc: The Document model instance to accept.

    Returns:
        A tuple of:
            - Document: Updated Document model with status="accepted".
            - list[OutboxWrite]: Outbox events for the acceptance.

    Raises:
        ValueError: If the document is not a quote.
        ValueError: If the quote is not in "sent" or "accepted" status.

    Example:
        >>> updated_doc, events = plan_accept_quote(quote_doc)
        >>> print(updated_doc.status)
        accepted
    """
    if doc.doc_type != "quote":
        raise ValueError("Only quotes can be accepted for conversion")
    if doc.status not in ("sent", "accepted"):
        raise ValueError(f"Quote must be 'sent' or 'accepted', currently '{doc.status}'")

    old_status = doc.status
    doc.status = "accepted"

    event = document_status_changed_event(
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        doc_number=doc.doc_number,
        doc_type=doc.doc_type,
        old_status=old_status,
        new_status="accepted",
        actor_id=doc.created_by,
        total=str(doc.total) if doc.total else None,
        customer_name=doc.customer_name,
        customer_id=doc.created_by,
        due_date=doc.due_date.isoformat() if doc.due_date else None,
    )

    outbox = [OutboxWrite(event=event, aggregate_type="document", aggregate_id=str(doc.id))]
    return doc, outbox
