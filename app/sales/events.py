"""Sales domain event factories.

This module defines event factory functions for the sales domain.
Each function creates an EventEnvelope with the appropriate payload
for a specific business event.

Events:
    - sale_created: Emitted when a new sale is created
    - sale_confirmed: Emitted when a sale is confirmed (payment received)
    - sale_voided: Emitted when a sale is voided
    - sale_receipt_created: Emitted when a receipt is generated
"""

from uuid import UUID

from common.events import EventEnvelope
from common.events.names import (
    SALE_CONFIRMED,
    SALE_CREATED,
    SALE_RECEIPT_CREATED,
    SALE_VOIDED,
)


def sale_created_event(
    *,
    tenant_id: UUID,
    sale_id: UUID,
    sale_number: str,
    total: str,
    cashier_id: UUID,
    item_count: int,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a sale created event.

    Args:
        tenant_id: Business/tenant identifier.
        sale_id: Unique sale identifier.
        sale_number: Human-readable sale number.
        total: Sale total amount as string.
        cashier_id: User who created the sale.
        item_count: Number of items in the sale.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the sale created event.
    """
    return EventEnvelope(
        event_type=SALE_CREATED,
        tenant_id=tenant_id,
        actor_id=cashier_id,
        correlation_id=correlation_id,
        payload={
            "sale_id": str(sale_id),
            "sale_number": sale_number,
            "total": total,
            "cashier_id": str(cashier_id),
            "item_count": item_count,
        },
    )


def sale_confirmed_event(
    *,
    tenant_id: UUID,
    sale_id: UUID,
    sale_number: str,
    total: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a sale confirmed event.

    Emitted when payment is received and the sale is confirmed.

    Args:
        tenant_id: Business/tenant identifier.
        sale_id: Unique sale identifier.
        sale_number: Human-readable sale number.
        total: Sale total amount as string.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the sale confirmed event.
    """
    return EventEnvelope(
        event_type=SALE_CONFIRMED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "sale_id": str(sale_id),
            "sale_number": sale_number,
            "total": total,
        },
    )


def sale_voided_event(
    *,
    tenant_id: UUID,
    sale_id: UUID,
    sale_number: str,
    reason: str,
    voided_by: UUID,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a sale voided event.

    Args:
        tenant_id: Business/tenant identifier.
        sale_id: Unique sale identifier.
        sale_number: Human-readable sale number.
        reason: Reason for voiding the sale.
        voided_by: User who voided the sale.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the sale voided event.
    """
    return EventEnvelope(
        event_type=SALE_VOIDED,
        tenant_id=tenant_id,
        actor_id=voided_by,
        correlation_id=correlation_id,
        payload={
            "sale_id": str(sale_id),
            "sale_number": sale_number,
            "reason": reason,
        },
    )


def sale_receipt_created_event(
    *,
    tenant_id: UUID,
    receipt_id: UUID,
    sale_id: UUID,
    receipt_number: str,
    total: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Create a sale receipt created event.

    Args:
        tenant_id: Business/tenant identifier.
        receipt_id: Unique receipt identifier.
        sale_id: Reference to the sale.
        receipt_number: Human-readable receipt number.
        total: Sale total amount as string.
        correlation_id: Optional correlation ID for tracing.

    Returns:
        EventEnvelope for the receipt created event.
    """
    return EventEnvelope(
        event_type=SALE_RECEIPT_CREATED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "receipt_id": str(receipt_id),
            "sale_id": str(sale_id),
            "receipt_number": receipt_number,
            "total": total,
        },
    )
