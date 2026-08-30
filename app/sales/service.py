from datetime import UTC, datetime
from uuid import uuid4

from common.events.outbox import OutboxWrite
from sales.events import (
    sale_confirmed_event,
    sale_created_event,
    sale_receipt_created_event,
    sale_voided_event,
)
from sales.models import Receipt, Sale, SaleItem
from sales.schemas import (
    ConfirmSaleCommand,
    ReceiptCreateCommand,
    SaleCreateCommand,
    SaleResult,
    VoidSaleCommand,
)


def _new_sale_number() -> str:
    return f"SL-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def plan_sale_creation(
    command: SaleCreateCommand,
) -> tuple[SaleResult, Sale, list[SaleItem], list[OutboxWrite]]:
    sale_id = uuid4()
    sale_number = _new_sale_number()

    subtotal = sum(float(i.qty) * float(i.unit_price) for i in command.items)
    discount_amt = float(command.discount)
    taxable_amt = subtotal - discount_amt
    tax = sum(
        float(i.qty) * float(i.unit_price) * (float(i.tax_rate or 0) / 100) for i in command.items
    )
    total = taxable_amt + tax

    sale = Sale(
        id=sale_id,
        tenant_id=command.tenant_id,
        sale_number=sale_number,
        status="pending",
        customer_name=command.customer_name,
        customer_phone=command.customer_phone,
        store_id=command.store_id,
        cashier_id=command.cashier_id,
        subtotal=subtotal,
        discount=discount_amt,
        tax=tax,
        total=total,
        notes=command.notes,
    )

    items = []
    for line in command.items:
        qty = float(line.qty)
        unit_price = float(line.unit_price)
        discount_pct = float(line.discount_pct)
        tax_rate = float(line.tax_rate or 0)
        line_discount = unit_price * qty * (discount_pct / 100)
        line_total = (unit_price * qty) - line_discount

        item = SaleItem(
            id=uuid4(),
            sale_id=sale_id,
            product_id=line.product_id,
            product_name=line.product_name,
            qty=qty,
            unit_price=unit_price,
            discount_pct=discount_pct,
            tax_rate=tax_rate if tax_rate else None,
            line_total=line_total,
        )
        items.append(item)

    event = sale_created_event(
        tenant_id=command.tenant_id,
        sale_id=sale_id,
        sale_number=sale_number,
        total=str(total),
        cashier_id=command.cashier_id,
        item_count=len(items),
        correlation_id=command.correlation_id,
    )

    result = SaleResult(
        id=sale_id,
        tenant_id=command.tenant_id,
        sale_number=sale_number,
        status="pending",
        subtotal=subtotal,
        discount=discount_amt,
        tax=tax,
        total=total,
        amount_paid=0,
        item_count=len(items),
    )

    outbox = [OutboxWrite(event=event, aggregate_type="sale", aggregate_id=str(sale_id))]
    return result, sale, items, outbox


def plan_confirm_sale(command: ConfirmSaleCommand, sale: Sale) -> list[OutboxWrite]:
    """Plan sale confirmation with event publishing.

    Args:
        command: Sale confirmation command.
        sale: The sale to confirm.

    Returns:
        List of OutboxWrite events for persistence.
    """
    event = sale_confirmed_event(
        tenant_id=command.tenant_id,
        sale_id=command.sale_id,
        sale_number=sale.sale_number,
        total=str(sale.total),
        correlation_id=command.correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="sale", aggregate_id=str(command.sale_id))]


def plan_void_sale(command: VoidSaleCommand, sale: Sale) -> list[OutboxWrite]:
    """Plan sale void with event publishing.

    Args:
        command: Sale void command with reason.
        sale: The sale to void.

    Returns:
        List of OutboxWrite events for persistence.
    """
    event = sale_voided_event(
        tenant_id=command.tenant_id,
        sale_id=command.sale_id,
        sale_number=sale.sale_number,
        reason=command.reason,
        voided_by=command.voided_by,
        correlation_id=command.correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="sale", aggregate_id=str(command.sale_id))]


def plan_create_receipt(command: ReceiptCreateCommand, sale: Sale) -> tuple[Receipt, list[OutboxWrite]]:
    """Plan receipt creation with event publishing.

    Args:
        command: Receipt creation command.
        sale: The sale for which to create a receipt.

    Returns:
        Tuple of (receipt, outbox_events) for persistence.
    """
    receipt_id = uuid4()
    receipt = Receipt(
        id=receipt_id,
        tenant_id=command.tenant_id,
        sale_id=command.sale_id,
        receipt_number=command.receipt_number,
        sent_via=command.sent_via,
    )

    event = sale_receipt_created_event(
        tenant_id=command.tenant_id,
        receipt_id=receipt_id,
        sale_id=command.sale_id,
        receipt_number=command.receipt_number,
        total=str(sale.total),
        correlation_id=command.correlation_id,
    )

    outbox = [OutboxWrite(event=event, aggregate_type="receipt", aggregate_id=str(receipt_id))]
    return receipt, outbox
