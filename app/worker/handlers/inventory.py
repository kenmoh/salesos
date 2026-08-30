"""Inventory event handlers.

This module handles inventory-related events from the RabbitMQ
message bus. Events are processed idempotently using the inbox pattern.

Handlers:
    - handle_sale_created: Reserves stock for sale items
    - handle_sale_voided: Releases reservations for voided sales
    - handle_product_created: Stub for future product sync
"""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from common.events.envelope import EventEnvelope


async def handle_sale_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Handle sale created event by reserving stock.

    For each item in the sale, reserves the requested quantity from
    available stock. If successful, checks for low stock conditions.

    Args:
        envelope: Event envelope with sale details in payload.
        session: Database session for inventory operations.
    """
    sale_id = envelope.payload.get("sale_id")
    tenant_id = envelope.payload.get("tenant_id")
    if not sale_id or not tenant_id:
        return

    from inventory.repository import (
        create_reservation,
        get_stock_balances_by_product,
    )
    from inventory.models import StockReservation
    from inventory.service import check_low_stock
    from sales.repository import get_sale_items

    from common.db.engine import create_service_database
    from common.settings import get_common_settings

    settings = get_common_settings()
    sdb_sales = create_service_database(settings.database_url_sales)

    async with sdb_sales.session() as sales_session:
        sale_items = await get_sale_items(sales_session, UUID(sale_id))

    for item in sale_items:
        balances = await get_stock_balances_by_product(session, item.product_id)
        if balances:
            balance = balances[0]
            available = balance.qty - balance.reserved_qty
            if available >= float(item.qty):
                reservation = StockReservation(
                    id=uuid4(),
                    tenant_id=UUID(tenant_id),
                    product_id=item.product_id,
                    store_id=balance.store_id,
                    sale_id=UUID(sale_id),
                    qty=float(item.qty),
                    status="active",
                )
                balance.reserved_qty += float(item.qty)
                await create_reservation(session, reservation)

                # Check for low stock after reservation
                low_stock_outbox = check_low_stock(
                    balance,
                    reorder_point=float(item.reorder_point) if hasattr(item, 'reorder_point') else 0,
                    correlation_id=envelope.correlation_id,
                )
                if low_stock_outbox:
                    # Write low stock event to outbox
                    from common.events.outbox import OutboxWrite
                    for outbox_event in low_stock_outbox:
                        session.add(outbox_event.to_model())

    await session.flush()


async def handle_product_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Handle product created event.

    Initializes stock balances for the product across all stores in the tenant.

    Args:
        envelope: Event envelope with product details.
        session: Database session.
    """
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    product_id = envelope.payload.get("product_id")
    if not tenant_id or not product_id:
        return

    await session.execute(
        text("""
            INSERT INTO stock_balances
                (id, tenant_id, store_id, product_id, qty, reserved_qty, committed_qty)
            SELECT
                gen_random_uuid(),
                :tenant_id,
                s.id,
                :product_id,
                0, 0, 0
            FROM stores s
            WHERE s.tenant_id = :tenant_id
            AND s.status = 'active'
            ON CONFLICT DO NOTHING
        """),
        {"tenant_id": tenant_id, "product_id": product_id},
    )
    await session.flush()


async def handle_sale_voided(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Handle sale voided event by releasing reservations.

    Releases all stock reservations associated with the voided sale,
    making the stock available for other orders.

    Args:
        envelope: Event envelope with sale details.
        session: Database session for inventory operations.
    """
    from inventory.repository import release_reservations_for_sale

    sale_id = envelope.payload.get("sale_id")
    tenant_id = envelope.payload.get("tenant_id")
    if not sale_id or not tenant_id:
        return
    await release_reservations_for_sale(session, tenant_id=UUID(tenant_id), sale_id=UUID(sale_id))
