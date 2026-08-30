from collections.abc import Callable, Coroutine
from typing import Any

from common.events.envelope import EventEnvelope

EventHandler = Callable[[EventEnvelope, Any], Coroutine[Any, Any, None]]

# Registry: service_name -> list of (event_type, handler) tuples
SERVICE_HANDLERS: dict[str, list[tuple[str, EventHandler]]] = {
    "sales": [],
    "inventory": [],
    "accounting": [],
    "notifications": [],
    "reporting": [],
    "documents": [],
}


def register(service: str, event_type: str, handler: EventHandler) -> None:
    if service in SERVICE_HANDLERS:
        SERVICE_HANDLERS[service].append((event_type, handler))


def apply_service_handlers(service: str, register_fn: Callable[[str, EventHandler], None]) -> None:
    for event_type, handler in SERVICE_HANDLERS.get(service, []):
        register_fn(event_type, handler)


from ..handlers import accounting, documents, inventory, notifications, reporting, sales
from common.events.names import (
    CART_CHECKED_OUT,
    DOCUMENT_STATUS_CHANGED,
    IDENTITY_USER_CREATED,
    INVENTORY_LOW_STOCK_DETECTED,
    PAYMENT_SUCCEEDED,
    SALE_CONFIRMED,
    SALE_CREATED,
    SALE_RECEIPT_CREATED,
    SALE_VOIDED,
    TENANT_CREATED,
    CATALOG_PRODUCT_CREATED,
    TENANT_TIER_CHANGED,
)

register("sales", CART_CHECKED_OUT, sales.handle_cart_checked_out)
register("sales", PAYMENT_SUCCEEDED, sales.handle_payment_succeeded)
register("inventory", SALE_CREATED, inventory.handle_sale_created)
register("inventory", SALE_VOIDED, inventory.handle_sale_voided)
register("inventory", CATALOG_PRODUCT_CREATED, inventory.handle_product_created)
register("accounting", SALE_CONFIRMED, accounting.handle_sale_confirmed)
register("accounting", PAYMENT_SUCCEEDED, accounting.handle_payment_succeeded)
register("notifications", TENANT_CREATED, notifications.handle_tenant_created)
register("notifications", IDENTITY_USER_CREATED, notifications.handle_user_created)
register("notifications", INVENTORY_LOW_STOCK_DETECTED, notifications.handle_low_stock_detected)
register("reporting", SALE_RECEIPT_CREATED, reporting.handle_sale_receipt_created)
register("reporting", SALE_VOIDED, reporting.handle_sale_voided)
register("reporting", PAYMENT_SUCCEEDED, reporting.handle_payment_succeeded)
register("reporting", TENANT_TIER_CHANGED, reporting.handle_tier_changed)
register("documents", DOCUMENT_STATUS_CHANGED, documents.handle_document_status_changed)
