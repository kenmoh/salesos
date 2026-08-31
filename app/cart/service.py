from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from .events import (
    cart_abandoned_event,
    cart_checked_out_event,
    cart_created_event,
    cart_item_added_event,
    cart_item_removed_event,
)
from .models import Cart, CartItem
from .schemas import (
    AddItemCommand,
    CartCreateCommand,
    CartResult,
    CheckoutCommand,
    RemoveItemCommand,
)
from app.common.events.outbox import OutboxWrite


def plan_cart_creation(
    command: CartCreateCommand,
    *,
    tenant_id: UUID,
    actor_id: UUID | None = None,
    correlation_id: str | None = None,
    expires_minutes: int = 360,
) -> tuple[CartResult, Cart, list[OutboxWrite]]:
    cart_id = uuid4()
    session_id = f"sess_{uuid4().hex[:16]}"
    expires_at = datetime.now(UTC) + timedelta(minutes=expires_minutes)

    cart = Cart(
        id=cart_id,
        tenant_id=tenant_id,
        session_id=session_id,
        store_id=command.store_id,
        status="active",
        customer_name=command.customer_name,
        customer_phone=command.customer_phone,
        created_by=actor_id,
        expires_at=expires_at,
    )

    event = cart_created_event(
        tenant_id=tenant_id,
        cart_id=cart_id,
        session_id=session_id,
        actor_id=str(actor_id) if actor_id else None,
        correlation_id=correlation_id,
    )

    result = CartResult(
        id=cart_id,
        tenant_id=tenant_id,
        session_id=session_id,
        status="active",
        customer_name=command.customer_name,
        customer_phone=command.customer_phone,
        created_by=str(actor_id) if actor_id else None,
        item_count=0,
        total=0,
    )

    outbox = [OutboxWrite(event=event, aggregate_type="cart", aggregate_id=str(cart_id))]
    return result, cart, outbox


def plan_add_item(
    command: AddItemCommand, current_items: list[CartItem]
) -> tuple[CartItem, list[OutboxWrite]]:
    item_id = uuid4()
    item = CartItem(
        id=item_id,
        cart_id=command.cart_id,
        store_id=command.store_id,
        product_id=command.product_id,
        product_public_id=command.product_public_id,
        name=command.name,
        unit_price=float(command.unit_price),
        qty=float(command.qty),
        created_by=command.created_by,
    )

    event = cart_item_added_event(
        tenant_id=command.tenant_id,
        cart_id=command.cart_id,
        item_id=item_id,
        product_id=command.product_id,
        qty=str(command.qty),
        actor_id=str(command.created_by) if command.created_by else None,
        correlation_id=command.correlation_id,
    )

    total = sum(i.unit_price * i.qty for i in current_items) + float(command.unit_price) * float(
        command.qty
    )

    outbox = [OutboxWrite(event=event, aggregate_type="cart_item", aggregate_id=str(item_id))]
    return item, outbox


def plan_bulk_add_items(
    command: CheckoutCommand,
    resolved_products: list[tuple],
) -> tuple[list[CartItem], list[OutboxWrite]]:
    items: list[CartItem] = []
    outbox: list[OutboxWrite] = []
    cart_id = command.cart_id

    for product_id, public_id, name, unit_price, qty, store_id in resolved_products:
        item_id = uuid4()
        item = CartItem(
            id=item_id,
            cart_id=cart_id,
            store_id=store_id,
            product_id=product_id,
            product_public_id=public_id,
            name=name,
            unit_price=float(unit_price),
            qty=float(qty),
            created_by=command.created_by,
        )
        items.append(item)

        event = cart_item_added_event(
            tenant_id=command.tenant_id,
            cart_id=cart_id,
            item_id=item_id,
            product_id=product_id,
            qty=str(qty),
            actor_id=str(command.created_by) if command.created_by else None,
            correlation_id=command.correlation_id,
        )
        outbox.append(
            OutboxWrite(event=event, aggregate_type="cart_item", aggregate_id=str(item_id))
        )

    return items, outbox


def plan_remove_item(command: RemoveItemCommand, item: CartItem) -> list[OutboxWrite]:
    event = cart_item_removed_event(
        tenant_id=command.tenant_id,
        cart_id=command.cart_id,
        item_id=command.item_id,
        product_id=item.product_id,
        actor_id=str(command.created_by) if command.created_by else None,
        approved_by=str(command.approved_by) if command.approved_by else None,
        correlation_id=command.correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="cart_item", aggregate_id=str(command.item_id))]


def plan_checkout(command: CheckoutCommand, cart: Cart, items: list[CartItem]) -> list[OutboxWrite]:
    total = sum(i.unit_price * i.qty for i in items) if items else 0
    item_data = [
        {
            "product_id": str(i.product_id),
            "product_name": i.name,
            "qty": str(i.qty),
            "unit_price": str(i.unit_price),
        }
        for i in items
    ]
    event = cart_checked_out_event(
        tenant_id=command.tenant_id,
        cart_id=command.cart_id,
        session_id=cart.session_id,
        item_count=len(items),
        total=str(total),
        items=item_data,
        actor_id=str(command.created_by) if command.created_by else None,
        correlation_id=command.correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="cart", aggregate_id=str(command.cart_id))]


def plan_abandon(cart: Cart, correlation_id: str | None = None) -> list[OutboxWrite]:
    event = cart_abandoned_event(
        tenant_id=cart.tenant_id,
        cart_id=cart.id,
        session_id=cart.session_id,
        correlation_id=correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="cart", aggregate_id=str(cart.id))]
