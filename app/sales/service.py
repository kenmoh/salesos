from datetime import UTC, datetime
from uuid import uuid4

from app.common.events.outbox import OutboxWrite
from app.sales.events import (
    sale_confirmed_event,
    sale_created_event,
    sale_receipt_created_event,
    sale_voided_event,
)
from app.sales.models import Receipt, Sale, SaleItem
from app.sales.schemas import (
    ConfirmSaleCommand,
    ReceiptCreateCommand,
    SaleCreateCommand,
    SaleResult,
    VoidSaleCommand,
)
from app.taxes.calc import TaxLine, compute_taxes


def _new_sale_number() -> str:
    return f"SL-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def plan_sale_creation(
    command: SaleCreateCommand,
) -> tuple[SaleResult, Sale, list[SaleItem], list[OutboxWrite]]:
    sale_id = uuid4()
    sale_number = _new_sale_number()

    # Tax belongs to the lines that earned it. The command carries only a
    # cart-level discount, which the calculator spreads across the lines so
    # nothing is taxed on money the customer never paid.
    calc = compute_taxes(
        [
            TaxLine(
                qty=float(line.qty),
                unit_price=float(line.unit_price),
                discount_pct=float(line.discount_pct),
                taxes=list(line.taxes or []),
            )
            for line in command.items
        ],
        cart_discount=float(command.discount),
    )

    sale = Sale(
        id=sale_id,
        tenant_id=command.tenant_id,
        sale_number=sale_number,
        status="pending",
        customer_name=command.customer_name,
        customer_phone=command.customer_phone,
        store_id=command.store_id,
        cashier_id=command.cashier_id,
        subtotal=calc.subtotal,
        discount=calc.discount,
        tax=calc.total_tax,
        tax_breakdown=calc.breakdown or None,
        total=round(calc.taxable + calc.total_tax, 2),
        notes=command.notes,
    )

    items = []
    for line, line_taxes, tax_rate, line_total in zip(
        command.items, calc.line_taxes, calc.line_rates, calc.line_bases
    ):
        items.append(
            SaleItem(
                id=uuid4(),
                sale_id=sale_id,
                product_id=line.product_id,
                product_name=line.product_name,
                qty=float(line.qty),
                unit_price=float(line.unit_price),
                discount_pct=float(line.discount_pct),
                tax_id=line.tax_id,
                tax_rate=tax_rate if line_taxes else None,
                tax_breakdown=line_taxes or None,
                line_total=line_total,
            )
        )

    event = sale_created_event(
        tenant_id=command.tenant_id,
        sale_id=sale_id,
        sale_number=sale_number,
        total=str(sale.total),
        cashier_id=command.cashier_id,
        item_count=len(items),
        correlation_id=command.correlation_id,
    )

    result = SaleResult(
        id=sale_id,
        tenant_id=command.tenant_id,
        sale_number=sale_number,
        status="pending",
        subtotal=calc.subtotal,
        discount=calc.discount,
        tax=calc.total_tax,
        total=sale.total,
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
        total=float(sale.total),
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
