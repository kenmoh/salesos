from uuid import UUID

from app.common.events import EventEnvelope
from app.common.events.names import (
    CART_ABANDONED,
    CART_CHECKED_OUT,
    CART_CREATED,
    CART_ITEM_ADDED,
    CART_ITEM_REMOVED,
)


def cart_created_event(
    *,
    tenant_id: UUID,
    cart_id: UUID,
    session_id: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=CART_CREATED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "cart_id": str(cart_id),
            "session_id": session_id,
        },
    )


def cart_item_added_event(
    *,
    tenant_id: UUID,
    cart_id: UUID,
    item_id: UUID,
    product_id: UUID,
    qty: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=CART_ITEM_ADDED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "cart_id": str(cart_id),
            "item_id": str(item_id),
            "product_id": str(product_id),
            "qty": qty,
        },
    )


def cart_item_removed_event(
    *,
    tenant_id: UUID,
    cart_id: UUID,
    item_id: UUID,
    product_id: UUID,
    actor_id: str | None = None,
    approved_by: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    payload: dict = {
        "cart_id": str(cart_id),
        "item_id": str(item_id),
        "product_id": str(product_id),
    }
    if approved_by:
        payload["approved_by"] = approved_by
    return EventEnvelope(
        event_type=CART_ITEM_REMOVED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload=payload,
    )


def cart_checked_out_event(
    *,
    tenant_id: UUID,
    cart_id: UUID,
    session_id: str,
    item_count: int,
    total: str,
    items: list[dict] | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    payload: dict = {
        "cart_id": str(cart_id),
        "session_id": session_id,
        "item_count": item_count,
        "total": total,
    }
    if items:
        payload["items"] = items
    return EventEnvelope(
        event_type=CART_CHECKED_OUT,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload=payload,
    )


def cart_abandoned_event(
    *,
    tenant_id: UUID,
    cart_id: UUID,
    session_id: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=CART_ABANDONED,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload={
            "cart_id": str(cart_id),
            "session_id": session_id,
        },
    )
