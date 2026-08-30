from uuid import UUID

from common.events import EventEnvelope
from common.events.names import (
    INVENTORY_LOW_STOCK_DETECTED,
    INVENTORY_STOCK_ADJUSTED,
    INVENTORY_STOCK_COMMITTED,
    INVENTORY_STOCK_RELEASED,
    INVENTORY_STOCK_RESERVED,
    INVENTORY_TRANSFER_APPROVED,
    INVENTORY_TRANSFER_FULFILLED,
    INVENTORY_TRANSFER_REJECTED,
    INVENTORY_TRANSFER_REQUESTED,
)


def stock_adjusted_event(
    *,
    tenant_id: UUID,
    product_id: UUID,
    store_id: UUID,
    reason: str,
    qty_change: str,
    new_balance: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_STOCK_ADJUSTED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "product_id": str(product_id),
            "store_id": str(store_id),
            "reason": reason,
            "qty_change": qty_change,
            "new_balance": new_balance,
        },
    )


def stock_reserved_event(
    *,
    tenant_id: UUID,
    reservation_id: UUID,
    product_id: UUID,
    sale_id: UUID,
    qty: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_STOCK_RESERVED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "reservation_id": str(reservation_id),
            "product_id": str(product_id),
            "sale_id": str(sale_id),
            "qty": qty,
        },
    )


def stock_released_event(
    *,
    tenant_id: UUID,
    reservation_id: UUID,
    product_id: UUID,
    sale_id: UUID,
    qty: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_STOCK_RELEASED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "reservation_id": str(reservation_id),
            "product_id": str(product_id),
            "sale_id": str(sale_id),
            "qty": qty,
        },
    )


def stock_committed_event(
    *,
    tenant_id: UUID,
    product_id: UUID,
    store_id: UUID,
    sale_id: UUID,
    qty: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_STOCK_COMMITTED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "product_id": str(product_id),
            "store_id": str(store_id),
            "sale_id": str(sale_id),
            "qty": qty,
        },
    )


def low_stock_detected_event(
    *,
    tenant_id: UUID,
    product_id: UUID,
    store_id: UUID,
    current_qty: str,
    reorder_point: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_LOW_STOCK_DETECTED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "product_id": str(product_id),
            "store_id": str(store_id),
            "current_qty": current_qty,
            "reorder_point": reorder_point,
        },
    )


def transfer_requested_event(
    *,
    tenant_id: UUID,
    request_id: UUID,
    product_id: UUID,
    requesting_store_id: UUID,
    supplying_store_id: UUID,
    requested_qty: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_TRANSFER_REQUESTED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "request_id": str(request_id),
            "product_id": str(product_id),
            "requesting_store_id": str(requesting_store_id),
            "supplying_store_id": str(supplying_store_id),
            "requested_qty": requested_qty,
        },
    )


def transfer_approved_event(
    *,
    tenant_id: UUID,
    request_id: UUID,
    product_id: UUID,
    requesting_store_id: UUID,
    supplying_store_id: UUID,
    requested_qty: str,
    approved_qty: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_TRANSFER_APPROVED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "request_id": str(request_id),
            "product_id": str(product_id),
            "requesting_store_id": str(requesting_store_id),
            "supplying_store_id": str(supplying_store_id),
            "requested_qty": requested_qty,
            "approved_qty": approved_qty,
        },
    )


def transfer_rejected_event(
    *,
    tenant_id: UUID,
    request_id: UUID,
    product_id: UUID,
    requesting_store_id: UUID,
    supplying_store_id: UUID,
    rejection_reason: str | None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_TRANSFER_REJECTED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "request_id": str(request_id),
            "product_id": str(product_id),
            "requesting_store_id": str(requesting_store_id),
            "supplying_store_id": str(supplying_store_id),
            "rejection_reason": rejection_reason,
        },
    )


def transfer_fulfilled_event(
    *,
    tenant_id: UUID,
    request_id: UUID,
    product_id: UUID,
    from_store_id: UUID,
    to_store_id: UUID,
    qty: str,
    from_new_balance: str,
    to_new_balance: str,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=INVENTORY_TRANSFER_FULFILLED,
        tenant_id=tenant_id,
        actor_id=None,
        correlation_id=correlation_id,
        payload={
            "request_id": str(request_id),
            "product_id": str(product_id),
            "from_store_id": str(from_store_id),
            "to_store_id": str(to_store_id),
            "qty": qty,
            "from_new_balance": from_new_balance,
            "to_new_balance": to_new_balance,
        },
    )
