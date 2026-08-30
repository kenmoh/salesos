"""Event constructors for the document domain.

This module defines factory functions that create EventEnvelope instances
for document domain events. These events are published via the transactional
outbox pattern and consumed by worker handlers for side effects like
creating Accounts Receivable records or sending notifications.

Event Flow:
    1. Service layer calls this module's factory function
    2. Factory returns an OutboxWrite with the EventEnvelope
    3. Repository persists the document + outbox record in one transaction
    4. Dispatcher reads outbox and publishes to RabbitMQ
    5. Worker consumes event and performs side effects (e.g., AR creation)

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- QR: Queue/Queueing -- the process of publishing events to RabbitMQ.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- INV: Invoice -- a request for payment from a customer.
"""

from uuid import UUID

from common.events import EventEnvelope
from common.events.names import (
    DOCUMENT_CREATED,
    DOCUMENT_STATUS_CHANGED,
    DOCUMENT_CONVERTED_TO_SALE,
)


def document_created_event(
    *,
    tenant_id: UUID,
    document_id: UUID,
    doc_number: str,
    doc_type: str,
    total: str,
    created_by: UUID,
    item_count: int,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a document.created event envelope.

    Published when a new document is created. Consumed by analytics and
    notification handlers.

    Args:
        tenant_id: The business tenant that owns the document.
        document_id: UUID of the newly created document.
        doc_number: Auto-generated document number (e.g., "INV-20260826-A1B2C3D4").
        doc_type: The type of document (quote, invoice, receipt, purchase_order).
        total: The total amount as a string (e.g., "85000.00"). Stringified to
            preserve decimal precision across event boundaries.
        created_by: UUID of the user who created the document.
        item_count: Number of line items in the document.
        correlation_id: Optional request correlation ID for distributed tracing.

    Returns:
        An EventEnvelope with event_type="document.created".
    """
    return EventEnvelope(
        event_type=DOCUMENT_CREATED,
        tenant_id=tenant_id,
        actor_id=created_by,
        correlation_id=correlation_id,
        payload={
            "document_id": str(document_id),
            "doc_number": doc_number,
            "doc_type": doc_type,
            "total": total,
            "item_count": item_count,
        },
    )


def document_status_changed_event(
    *,
    tenant_id: UUID,
    document_id: UUID,
    doc_number: str,
    doc_type: str,
    old_status: str,
    new_status: str,
    actor_id: UUID,
    total: str | None = None,
    customer_name: str | None = None,
    customer_id: UUID | None = None,
    due_date: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a document.status_changed event envelope.

    Published when a document's status transitions (e.g., draft → sent, sent → paid).
    Consumed by:
        - Accounting worker: Creates AR/journal entries on invoice status changes.
        - Notification worker: Sends email/SMS on status changes.
        - Analytics worker: Updates document metrics.

    Args:
        tenant_id: The business tenant that owns the document.
        document_id: UUID of the document whose status changed.
        doc_number: Document number for display in notifications.
        doc_type: The type of document (quote, invoice, receipt, purchase_order).
            Required for the worker to determine if AR creation is needed.
        old_status: The previous status before the transition.
        new_status: The new status after the transition.
        actor_id: UUID of the user who performed the status change.
        total: The total amount as a string (e.g., "85000.00"). Stringified to
            preserve decimal precision. Used by accounting worker for AR amount.
        customer_name: Optional customer name for AR record display.
        customer_id: Optional UUID of the customer for AR record linking.
        due_date: Optional due date as ISO string for AR aging reports.
        correlation_id: Optional request correlation ID for distributed tracing.

    Returns:
        An EventEnvelope with event_type="document.status_changed".
    """
    return EventEnvelope(
        event_type=DOCUMENT_STATUS_CHANGED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "document_id": str(document_id),
            "doc_number": doc_number,
            "doc_type": doc_type,
            "old_status": old_status,
            "new_status": new_status,
            "total": total,
            "customer_name": customer_name,
            "customer_id": str(customer_id) if customer_id else None,
            "due_date": due_date,
        },
    )


def document_converted_to_sale_event(
    *,
    tenant_id: UUID,
    document_id: UUID,
    doc_number: str,
    sale_id: UUID,
    sale_number: str,
    actor_id: UUID,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a document.converted_to_sale event envelope.

    Published when a quote or invoice is converted to a completed sale.
    Consumed by notification and analytics handlers.

    Args:
        tenant_id: The business tenant that owns the document and sale.
        document_id: UUID of the source document (quote or invoice).
        doc_number: Document number of the source document.
        sale_id: UUID of the newly created sale record.
        sale_number: Auto-generated sale number for display.
        actor_id: UUID of the user who performed the conversion.
        correlation_id: Optional request correlation ID for distributed tracing.

    Returns:
        An EventEnvelope with event_type="document.converted_to_sale".
    """
    return EventEnvelope(
        event_type=DOCUMENT_CONVERTED_TO_SALE,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "document_id": str(document_id),
            "doc_number": doc_number,
            "sale_id": str(sale_id),
            "sale_number": sale_number,
        },
    )
