from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.common.events.outbox import OutboxWrite
from app.inventory.events import (
    low_stock_detected_event,
    stock_adjusted_event,
    stock_committed_event,
    stock_released_event,
    stock_reserved_event,
    transfer_approved_event,
    transfer_fulfilled_event,
    transfer_rejected_event,
    transfer_requested_event,
)
from app.inventory.models import (
    StockAdjustment,
    StockBalance,
    StockReservation,
    TransferRequest,
)
from app.inventory.schemas import (
    AdjustStockCommand,
    ReserveStockCommand,
    TransferRequestApproveCommand,
    TransferRequestCreateCommand,
    TransferRequestRejectCommand,
    TransferStockCommand,
)


def _decimal(value) -> Decimal:
    """Coerce a quantity to Decimal without going through binary floats."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def group_movements_by_day(rows) -> list[dict]:
    """Collapse stock movements into one point per calendar day.

    Rows are sorted by time first, so the balance kept for a day is the
    balance after its last movement and the quantity is the day's total --
    later movements replace the balance rather than being averaged with
    whatever came before. Each day also carries the balance it started from.
    """
    ordered = sorted(rows, key=lambda row: row.created_at)
    points: dict[str, dict] = {}
    for row in ordered:
        day = row.created_at.date().isoformat()
        point = points.get(day)
        if point is None:
            points[day] = {
                "date": day,
                "balance": float(row.balance_after),
                # What the stock was before this day's first movement, so a
                # chart can draw the flat run that came before the window.
                "balance_before": float(row.balance_before),
                "qty_change": round(float(row.qty_change), 2),
            }
        else:
            point["balance"] = float(row.balance_after)
            point["qty_change"] = round(
                point["qty_change"] + float(row.qty_change), 2
            )
    return list(points.values())


def plan_adjust_stock(
    command: AdjustStockCommand,
    current_balance: StockBalance | None,
) -> tuple[StockAdjustment, StockBalance, list[OutboxWrite]]:
    adjustment = StockAdjustment(
        id=uuid4(),
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        store_id=command.store_id,
        reason=command.reason,
        qty_change=float(command.qty_change),
        unit_cost=float(command.unit_cost) if command.unit_cost else None,
        notes=command.notes,
        created_by=command.created_by,
    )

    if current_balance:
        new_qty = float(current_balance.qty) + float(command.qty_change)
        if new_qty < 0:
            raise ValueError(
                f"Insufficient stock: current={float(current_balance.qty)}, "
                f"attempted change={float(command.qty_change)}"
            )
        new_cost = (
            float(command.unit_cost) if command.unit_cost else float(current_balance.unit_cost or 0)
        )
        balance = current_balance
        balance.qty = new_qty
        if command.unit_cost:
            balance.unit_cost = new_cost
    else:
        new_qty = float(command.qty_change)
        balance = StockBalance(
            id=uuid4(),
            tenant_id=command.tenant_id,
            product_id=command.product_id,
            store_id=command.store_id,
            qty=new_qty,
            reserved_qty=0,
            unit_cost=float(command.unit_cost) if command.unit_cost else None,
        )

    event = stock_adjusted_event(
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        store_id=command.store_id,
        reason=command.reason,
        qty_change=str(command.qty_change),
        new_balance=str(new_qty),
        correlation_id=command.correlation_id,
    )

    outbox = [
        OutboxWrite(event=event, aggregate_type="stock_balance", aggregate_id=str(balance.id))
    ]
    return adjustment, balance, outbox


def plan_transfer_stock(
    command: TransferStockCommand,
    from_balance: StockBalance,
    to_balance: StockBalance | None,
) -> tuple[StockAdjustment, StockAdjustment, StockBalance, StockBalance, list[OutboxWrite]]:
    qty = _decimal(command.qty)

    from_adjustment = StockAdjustment(
        id=uuid4(),
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        store_id=command.from_store_id,
        reason="transfer_out",
        qty_change=-qty,
        notes=command.notes,
        created_by=command.created_by,
    )

    to_adjustment = StockAdjustment(
        id=uuid4(),
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        store_id=command.to_store_id,
        reason="transfer_in",
        qty_change=qty,
        notes=command.notes,
        created_by=command.created_by,
    )

    from_balance.qty = _decimal(from_balance.qty) - qty

    if to_balance:
        to_balance.qty = _decimal(to_balance.qty) + qty
    else:
        to_balance = StockBalance(
            id=uuid4(),
            tenant_id=command.tenant_id,
            product_id=command.product_id,
            store_id=command.to_store_id,
            qty=qty,
            reserved_qty=0,
            unit_cost=from_balance.unit_cost,
        )

    return from_adjustment, to_adjustment, from_balance, to_balance, []


def plan_reserve_stock(
    command: ReserveStockCommand,
    balance: StockBalance,
) -> tuple[StockReservation, StockBalance, list[OutboxWrite]]:
    qty = float(command.qty)
    if balance.qty - balance.reserved_qty < qty:
        raise ValueError(
            f"Insufficient stock: available {balance.qty - balance.reserved_qty}, requested {qty}"
        )

    reservation = StockReservation(
        id=uuid4(),
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        store_id=command.store_id,
        sale_id=command.sale_id,
        qty=qty,
        status="active",
    )

    balance.reserved_qty += qty

    event = stock_reserved_event(
        tenant_id=command.tenant_id,
        reservation_id=reservation.id,
        product_id=command.product_id,
        sale_id=command.sale_id,
        qty=str(qty),
        correlation_id=command.correlation_id,
    )

    outbox = [
        OutboxWrite(
            event=event, aggregate_type="stock_reservation", aggregate_id=str(reservation.id)
        )
    ]
    return reservation, balance, outbox


def plan_release_reservation(
    reservation: StockReservation,
    balance: StockBalance,
    correlation_id: str | None = None,
) -> tuple[StockBalance, list[OutboxWrite]]:
    balance.reserved_qty -= reservation.qty

    event = stock_released_event(
        tenant_id=reservation.tenant_id,
        reservation_id=reservation.id,
        product_id=reservation.product_id,
        sale_id=reservation.sale_id,
        qty=str(reservation.qty),
        correlation_id=correlation_id,
    )

    outbox = [
        OutboxWrite(
            event=event, aggregate_type="stock_reservation", aggregate_id=str(reservation.id)
        )
    ]
    return balance, outbox


def plan_commit_stock(
    reservation: StockReservation,
    balance: StockBalance,
    correlation_id: str | None = None,
) -> tuple[StockBalance, list[OutboxWrite]]:
    balance.qty -= reservation.qty
    balance.reserved_qty -= reservation.qty

    event = stock_committed_event(
        tenant_id=reservation.tenant_id,
        product_id=reservation.product_id,
        store_id=reservation.store_id,
        sale_id=reservation.sale_id,
        qty=str(reservation.qty),
        correlation_id=correlation_id,
    )

    outbox = [
        OutboxWrite(
            event=event, aggregate_type="stock_reservation", aggregate_id=str(reservation.id)
        )
    ]
    return balance, outbox


def check_low_stock(
    balance: StockBalance,
    reorder_point: float,
    correlation_id: str | None = None,
) -> list[OutboxWrite] | None:
    available = balance.qty - balance.reserved_qty
    threshold = max(reorder_point, balance.min_stock_level)
    if available > 0 and available >= threshold:
        return None
    event = low_stock_detected_event(
        tenant_id=balance.tenant_id,
        product_id=balance.product_id,
        store_id=balance.store_id,
        current_qty=str(available),
        reorder_point=str(threshold),
        correlation_id=correlation_id,
    )
    return [OutboxWrite(event=event, aggregate_type="stock_balance", aggregate_id=str(balance.id))]


def plan_create_transfer_request(
    command: TransferRequestCreateCommand,
) -> tuple[TransferRequest, OutboxWrite]:
    if command.requesting_store_id == command.supplying_store_id:
        raise ValueError("same_store")

    request_id = uuid4()
    request = TransferRequest(
        id=request_id,
        tenant_id=command.tenant_id,
        product_id=command.product_id,
        supplying_product_id=command.supplying_product_id,
        requesting_store_id=command.requesting_store_id,
        supplying_store_id=command.supplying_store_id,
        requested_qty=_decimal(command.requested_qty),
        status="pending",
        notes=command.notes,
        created_by=command.created_by,
        request_number=command.request_number,
        item_name=command.item_name,
    )

    event = transfer_requested_event(
        tenant_id=command.tenant_id,
        request_id=request_id,
        product_id=command.product_id,
        requesting_store_id=command.requesting_store_id,
        supplying_store_id=command.supplying_store_id,
        requested_qty=str(command.requested_qty),
        correlation_id=command.correlation_id,
    )

    outbox = OutboxWrite(
        event=event, aggregate_type="transfer_request", aggregate_id=str(request_id)
    )
    return request, outbox


def plan_approve_transfer_request(
    command: TransferRequestApproveCommand,
    request: TransferRequest,
    supplying_balance: StockBalance | None,
) -> tuple[TransferRequest, OutboxWrite]:
    if request.status != "pending":
        raise ValueError(f"invalid_status:{request.status}")

    approved_qty = _decimal(command.approved_qty)
    requested_qty = _decimal(request.requested_qty)

    if approved_qty <= 0:
        raise ValueError("approved_qty_must_be_positive")

    if approved_qty > requested_qty:
        raise ValueError("approved_qty_exceeds_requested")

    if not supplying_balance:
        raise ValueError("supplying_balance_not_found")

    available = _decimal(supplying_balance.qty) - _decimal(supplying_balance.reserved_qty)
    if available < approved_qty:
        raise ValueError(f"insufficient_stock:available_{available}")

    request.approved_qty = approved_qty
    request.status = "approved"
    request.approved_by = command.approved_by
    request.approved_at = datetime.now(UTC)
    request.pin_verified_by = command.pin_verified_by
    request.decision_note = command.notes or request.decision_note
    request.notes = command.notes or request.notes

    event = transfer_approved_event(
        tenant_id=command.tenant_id,
        request_id=command.request_id,
        product_id=request.product_id,
        requesting_store_id=request.requesting_store_id,
        supplying_store_id=request.supplying_store_id,
        requested_qty=str(request.requested_qty),
        approved_qty=str(approved_qty),
        correlation_id=command.correlation_id,
    )

    outbox = OutboxWrite(
        event=event, aggregate_type="transfer_request", aggregate_id=str(command.request_id)
    )
    return request, outbox


def plan_reject_transfer_request(
    command: TransferRequestRejectCommand,
    request: TransferRequest,
) -> tuple[TransferRequest, OutboxWrite]:
    if request.status != "pending":
        raise ValueError(f"invalid_status:{request.status}")

    request.status = "rejected"
    request.rejection_reason = command.rejection_reason
    request.approved_by = command.rejected_by
    request.rejected_at = datetime.now(UTC)
    request.pin_verified_by = command.pin_verified_by
    request.decision_note = command.notes or request.decision_note
    request.notes = command.notes or request.notes

    event = transfer_rejected_event(
        tenant_id=command.tenant_id,
        request_id=command.request_id,
        product_id=request.product_id,
        requesting_store_id=request.requesting_store_id,
        supplying_store_id=request.supplying_store_id,
        rejection_reason=command.rejection_reason,
        correlation_id=command.correlation_id,
    )

    outbox = OutboxWrite(
        event=event, aggregate_type="transfer_request", aggregate_id=str(command.request_id)
    )
    return request, outbox


def plan_fulfill_transfer_request(
    request: TransferRequest,
    from_balance: StockBalance,
    to_balance: StockBalance | None,
    tenant_id,
    fulfilled_by=None,
    correlation_id: str | None = None,
) -> tuple[
    StockAdjustment, StockAdjustment, StockBalance, StockBalance, TransferRequest, list[OutboxWrite]
]:
    if request.status != "approved":
        raise ValueError(f"invalid_status:{request.status}")

    qty = _decimal(request.approved_qty)

    available = _decimal(from_balance.qty) - _decimal(from_balance.reserved_qty)
    if available < qty:
        raise ValueError(f"insufficient_stock:available_{available}")

    # Each store carries its own product row for the same item name, so the
    # outgoing side is booked against the supplying store's product and the
    # incoming side against the requesting store's product.
    from_product_id = request.supplying_product_id or request.product_id
    to_product_id = request.product_id
    reference = request.request_number or str(request.id)
    note = f"Transfer {reference}: {request.item_name or 'item'} x {qty}"

    from_adjustment = StockAdjustment(
        id=uuid4(),
        tenant_id=tenant_id,
        product_id=from_product_id,
        store_id=request.supplying_store_id,
        reason="transfer_out",
        qty_change=-qty,
        notes=note,
        created_by=fulfilled_by,
    )

    to_adjustment = StockAdjustment(
        id=uuid4(),
        tenant_id=tenant_id,
        product_id=to_product_id,
        store_id=request.requesting_store_id,
        reason="transfer_in",
        qty_change=qty,
        notes=note,
        created_by=fulfilled_by,
    )

    from_balance.qty = _decimal(from_balance.qty) - qty

    if to_balance:
        to_balance.qty = _decimal(to_balance.qty) + qty
    else:
        to_balance = StockBalance(
            id=uuid4(),
            tenant_id=tenant_id,
            product_id=to_product_id,
            store_id=request.requesting_store_id,
            qty=qty,
            reserved_qty=0,
            unit_cost=from_balance.unit_cost,
        )

    request.status = "fulfilled"
    request.fulfilled_qty = qty
    request.fulfilled_by = fulfilled_by
    request.fulfilled_at = datetime.now(UTC)

    event = transfer_fulfilled_event(
        tenant_id=tenant_id,
        request_id=request.id,
        product_id=request.product_id,
        from_store_id=request.supplying_store_id,
        to_store_id=request.requesting_store_id,
        qty=str(qty),
        from_new_balance=str(from_balance.qty),
        to_new_balance=str(to_balance.qty),
        correlation_id=correlation_id,
    )

    outbox = [
        OutboxWrite(event=event, aggregate_type="transfer_request", aggregate_id=str(request.id))
    ]
    return from_adjustment, to_adjustment, from_balance, to_balance, request, outbox
