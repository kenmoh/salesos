from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.envelope import EventEnvelope
from app.sales.repository import (
    create_sale,
    create_sale_items,
    create_receipt,
    get_receipt_by_sale,
)
from app.sales.schemas import ReceiptCreateCommand, SaleCreateCommand, SaleItemLine
from app.sales.service import plan_create_receipt, plan_sale_creation
from app.taxes.resolve import product_tax_map


async def handle_cart_checked_out(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Handle cart checked out event by creating a sale.

    Resolves each product's own taxes so the backend, not the client, is the
    source of truth for tax calculation.

    Args:
        envelope: Event envelope with cart details in payload.
        session: Database session for sales operations.
    """
    data = envelope.payload
    tenant_id = data.get("tenant_id")
    cart_id = data.get("cart_id")
    items_data = data.get("items", [])
    session_id = data.get("session_id")
    if not tenant_id or not cart_id or not items_data:
        return

    # Taxes follow the product, one line at a time. Applying every active tax
    # to every line would charge a customer tax on goods that carry only VAT.
    tax_map = await product_tax_map(
        session,
        UUID(tenant_id),
        [UUID(str(i["product_id"])) for i in items_data if i.get("product_id")],
    )

    sale_items = []
    for i in items_data:
        line_taxes = tax_map.get(str(i["product_id"]), [])
        sale_items.append(
            SaleItemLine(
                product_id=UUID(i["product_id"]),
                product_name=i["product_name"],
                qty=Decimal(i["qty"]),
                unit_price=Decimal(i["unit_price"]),
                tax_id=UUID(line_taxes[0]["id"]) if line_taxes else None,
                taxes=line_taxes,
            )
        )

    command = SaleCreateCommand(
        tenant_id=UUID(tenant_id),
        cashier_id=UUID(session_id),
        items=sale_items,
        correlation_id=envelope.correlation_id,
    )

    _result, sale_model, sale_item_models, outbox = plan_sale_creation(command)
    await create_sale(session, sale_model)
    await create_sale_items(session, sale_item_models)
    for write in outbox:
        session.add(write.to_model())


async def handle_payment_succeeded(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Handle payment succeeded event by confirming sale and creating receipt.

    Args:
        envelope: Event envelope with payment details in payload.
        session: Database session for sales operations.
    """
    from app.sales.repository import get_sale_by_id, update_sale_status

    sale_id = envelope.payload.get("sale_id")
    if not sale_id:
        return

    sale = await get_sale_by_id(session, UUID(sale_id))
    if not sale:
        return

    if sale.status == "pending":
        await update_sale_status(session, sale.id, "confirmed")

    existing = await get_receipt_by_sale(session, sale.id)
    if existing:
        return

    receipt_number = f"RCP-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
    command = ReceiptCreateCommand(
        tenant_id=sale.tenant_id,
        sale_id=sale.id,
        receipt_number=receipt_number,
    )
    receipt, outbox = plan_create_receipt(command, sale)
    await create_receipt(session, receipt)
    for write in outbox:
        session.add(write.to_model())
