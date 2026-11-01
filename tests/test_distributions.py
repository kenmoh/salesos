"""Warehouse distribution workflow: dispatch from one warehouse to one
store, receive (with damages tracked separately, never added to stock),
and cancel-in-transit returning stock to the warehouse.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.inventory.models import (
    Distribution,
    DistributionItem,
    StockBalance,
    StockDamage,
)
from app.inventory.schemas import DistributionLineCreate
from app.inventory.service import (
    plan_cancel_distribution,
    plan_dispatch_distribution,
    plan_receive_distribution,
)

TENANT = uuid4()
WAREHOUSE = uuid4()
STORE = uuid4()
PRODUCT = uuid4()
DEST_PRODUCT = uuid4()


def _distribution(**overrides) -> Distribution:
    values = {
        "id": uuid4(),
        "tenant_id": TENANT,
        "distribution_number": "DIST-ABC12345",
        "from_store_id": WAREHOUSE,
        "to_store_id": STORE,
        "status": "in_transit",
        "created_by": uuid4(),
    }
    values.update(overrides)
    return Distribution(**values)


def _item(
    distribution_id: UUID,
    source_product_id: UUID = PRODUCT,
        qty_dispatched: Decimal = Decimal("10.00"),
    ) -> DistributionItem:
    return DistributionItem(
        id=uuid4(),
        tenant_id=TENANT,
        distribution_id=distribution_id,
        source_product_id=source_product_id,
        destination_product_id=None,
        item_name="Coke 50cl",
        qty_dispatched=float(qty_dispatched),
    )


def _balance(product_id, store_id, qty="25.00") -> StockBalance:
    return StockBalance(
        id=uuid4(),
        tenant_id=TENANT,
        product_id=product_id,
        store_id=store_id,
        qty=Decimal(qty),
        reserved_qty=Decimal("0"),
    )


# ── Dispatch ─────────────────────────────────────────────────────────────────


def test_dispatch_decrements_warehouse_stock():
    dist = _distribution()
    item = _item(dist.id)
    balance = _balance(PRODUCT, WAREHOUSE, "25.00")

    adjustments, balances, movements, outbox = plan_dispatch_distribution(
        distribution_id=dist.id,
        distribution_number=dist.distribution_number,
        tenant_id=TENANT,
        from_store_id=WAREHOUSE,
        to_store_id=STORE,
        lines=[DistributionLineCreate(product_id=PRODUCT, item_name="Coke 50cl", qty_dispatched=Decimal("10"))],
        actor=uuid4(),
        notes=None,
        correlation_id=None,
        warehouse_balances=[balance],
    )

    assert dist.status == "in_transit"
    assert len(adjustments) == 1
    assert adjustments[0].reason == "distribution_out"
    assert adjustments[0].qty_change == -10
    assert balance.qty == 15
    assert len(movements) == 1
    assert movements[0].movement_type == "distribution_out"
    assert movements[0].reference_type == "distribution"
    assert movements[0].reference_id == adjustments[0].id
    assert len(outbox) == 1
    assert outbox[0].event.event_type == "inventory.distribution_dispatched"


def test_dispatch_refuses_insufficient_stock():
    dist = _distribution()
    balance = _balance(PRODUCT, WAREHOUSE, "5.00")

    with pytest.raises(ValueError, match="Insufficient stock"):
        plan_dispatch_distribution(
            distribution_id=dist.id,
            distribution_number=dist.distribution_number,
            tenant_id=TENANT,
            from_store_id=WAREHOUSE,
            to_store_id=STORE,
            lines=[DistributionLineCreate(product_id=PRODUCT, item_name="Coke 50cl", qty_dispatched=Decimal("10"))],
            actor=uuid4(),
            notes=None,
            correlation_id=None,
            warehouse_balances=[balance],
        )


def test_dispatch_refuses_same_store():
    dist = _distribution(from_store_id=WAREHOUSE, to_store_id=WAREHOUSE)
    balance = _balance(PRODUCT, WAREHOUSE, "25.00")

    with pytest.raises(ValueError, match="cannot_distribute_to_self"):
        plan_dispatch_distribution(
            distribution_id=dist.id,
            distribution_number=dist.distribution_number,
            tenant_id=TENANT,
            from_store_id=WAREHOUSE,
            to_store_id=WAREHOUSE,
            lines=[DistributionLineCreate(product_id=PRODUCT, item_name="Coke 50cl", qty_dispatched=Decimal("10"))],
            actor=uuid4(),
            notes=None,
            correlation_id=None,
            warehouse_balances=[balance],
        )


# ── Receive ──────────────────────────────────────────────────────────────────


def test_receive_adds_stock_and_records_damage_separately():
    dist = _distribution()
    item = _item(dist.id)
    dest_balance = _balance(DEST_PRODUCT, STORE, "0.00")
    warehouse_balance = _balance(PRODUCT, WAREHOUSE, "25.00")

    adjustments, balances, movements, damages, outbox = plan_receive_distribution(
        distribution=dist,
        items=[item],
        receive_lines=[
            {
                "item_id": item.id,
                "qty_received": Decimal("8"),
                "qty_damaged": Decimal("2"),
                "condition_received": "damaged",
                "receive_notes": "2 units crushed",
                "destination_product_id": DEST_PRODUCT,
            }
        ],
        actor=uuid4(),
        correlation_id=None,
    )

    assert dist.status == "received"
    assert dist.received_by is not None
    assert dist.received_at is not None
    # destination stock only counts received units
    assert len(balances) == 1
    assert balances[0].qty == 8
    # damage never touches stock
    assert len(damages) == 1
    assert damages[0].qty == 2
    assert damages[0].condition == "damaged"
    assert damages[0].notes == "2 units crushed"
    # shortage computed on the line
    assert item.qty_received == 8
    assert item.qty_damaged == 2
    assert item.destination_product_id == DEST_PRODUCT
    assert item.destination_product_id != item.source_product_id

    assert len(adjustments) == 1
    assert adjustments[0].reason == "distribution_in"
    assert adjustments[0].qty_change == 8
    assert len(movements) == 1
    assert movements[0].movement_type == "distribution_in"
    assert len(outbox) == 1
    assert outbox[0].event.event_type == "inventory.distribution_received"
    payload = outbox[0].event.payload
    assert payload["qty_received"] == "8"
    assert payload["qty_damaged"] == "2"
    assert payload["shortage"] == "0.0"


def test_receive_allows_shortage_without_extra_stock_entry():
    dist = _distribution()
    item = _item(dist.id, qty_dispatched=Decimal("10.00"))
    dest_balance = _balance(DEST_PRODUCT, STORE, "0.00")

    _, balances, _, _, _ = plan_receive_distribution(
        distribution=dist,
        items=[item],
        receive_lines=[
            {
                "item_id": item.id,
                "qty_received": Decimal("7"),
                "qty_damaged": Decimal("1"),
                "condition_received": "damaged",
                "receive_notes": None,
                "destination_product_id": DEST_PRODUCT,
            }
        ],
        actor=uuid4(),
        correlation_id=None,
    )

    assert len(balances) == 1
    assert balances[0].qty == 7
    shortage = float(item.qty_dispatched) - float(item.qty_received) - float(item.qty_damaged)
    assert shortage == 2


def test_receive_refuses_exceeding_dispatched():
    dist = _distribution()
    item = _item(dist.id, qty_dispatched=Decimal("5.00"))

    with pytest.raises(ValueError, match="Receive exceeds dispatched"):
        plan_receive_distribution(
            distribution=dist,
            items=[item],
            receive_lines=[
                {
                    "item_id": item.id,
                    "qty_received": Decimal("4"),
                    "qty_damaged": Decimal("2"),
                    "condition_received": None,
                    "receive_notes": None,
                    "destination_product_id": DEST_PRODUCT,
                }
            ],
            actor=uuid4(),
            correlation_id=None,
        )


def test_receive_refuses_non_in_transit():
    dist = _distribution(status="received")
    item = _item(dist.id)

    with pytest.raises(ValueError, match="distribution_not_in_transit"):
        plan_receive_distribution(
            distribution=dist,
            items=[item],
            receive_lines=[
                {
                    "item_id": item.id,
                    "qty_received": Decimal("3"),
                    "qty_damaged": Decimal("0"),
                    "condition_received": None,
                    "receive_notes": None,
                    "destination_product_id": DEST_PRODUCT,
                }
            ],
            actor=uuid4(),
            correlation_id=None,
        )


# ── Cancel ───────────────────────────────────────────────────────────────────


def test_cancel_returns_stock_to_warehouse():
    dist = _distribution()
    item = _item(dist.id)
    balance = _balance(PRODUCT, WAREHOUSE, "25.00")

    adjustments, balances, movements, outbox = plan_cancel_distribution(
        distribution=dist,
        items=[item],
        warehouse_balances=[balance],
        actor=uuid4(),
        cancel_reason="Wrong destination",
        correlation_id=None,
    )

    assert dist.status == "cancelled"
    assert dist.cancel_reason == "Wrong destination"
    assert dist.cancelled_by is not None
    assert balance.qty == 35  # 25 + 10 returned
    assert len(adjustments) == 1
    assert adjustments[0].reason == "distribution_cancelled"
    assert adjustments[0].qty_change == 10
    assert len(movements) == 1
    assert movements[0].movement_type == "distribution_cancelled"
    assert len(outbox) == 1
    assert outbox[0].event.event_type == "inventory.distribution_cancelled"


def test_cancel_refuses_non_in_transit():
    dist = _distribution(status="received")
    item = _item(dist.id)
    balance = _balance(PRODUCT, WAREHOUSE, "25.00")

    with pytest.raises(ValueError, match="distribution_not_in_transit"):
        plan_cancel_distribution(
            distribution=dist,
            items=[item],
            warehouse_balances=[balance],
            actor=uuid4(),
            cancel_reason=None,
            correlation_id=None,
        )
