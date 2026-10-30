"""Inter-store transfer request workflow.

A request is raised by one store and decided by another, so the rules worth
pinning down are: the two stores identify the same item by name even though
each keeps its own catalog row, only the supplying store decides, and the
stock math keeps working with Decimal (Numeric) balances.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.inventory.models import StockBalance, TransferRequest
from app.inventory.schemas import (
    TransferRequestApproveCommand,
    TransferRequestCreateCommand,
    TransferRequestRejectCommand,
)
from app.inventory.service import (
    plan_approve_transfer_request,
    plan_create_transfer_request,
    plan_fulfill_transfer_request,
    plan_reject_transfer_request,
)

TENANT = uuid4()
STORE_A = uuid4()
STORE_B = uuid4()
PRODUCT_A = uuid4()
PRODUCT_B = uuid4()


def _pending_request(**overrides) -> TransferRequest:
    values = {
        "id": uuid4(),
        "tenant_id": TENANT,
        "product_id": PRODUCT_A,
        "supplying_product_id": PRODUCT_B,
        "item_name": "Coke 50cl",
        "request_number": "TR-ABC12345",
        "requesting_store_id": STORE_A,
        "supplying_store_id": STORE_B,
        "requested_qty": Decimal("10.00"),
        "status": "pending",
        "created_by": uuid4(),
    }
    values.update(overrides)
    return TransferRequest(**values)


def _balance(product_id, store_id, qty="25.00", reserved="0.00") -> StockBalance:
    return StockBalance(
        id=uuid4(),
        tenant_id=TENANT,
        product_id=product_id,
        store_id=store_id,
        qty=Decimal(qty),
        reserved_qty=Decimal(reserved),
    )


# ── Create ────────────────────────────────────────────────────────────────────


def test_create_records_item_name_and_both_store_products():
    """The same item is two catalog rows; the request keeps both."""
    command = TransferRequestCreateCommand(
        tenant_id=TENANT,
        product_id=PRODUCT_A,
        supplying_product_id=PRODUCT_B,
        item_name="Coke 50cl",
        request_number="TR-ABC12345",
        requesting_store_id=STORE_A,
        supplying_store_id=STORE_B,
        requested_qty=Decimal("10"),
        created_by=uuid4(),
    )

    request, outbox = plan_create_transfer_request(command)

    assert request.status == "pending"
    assert request.item_name == "Coke 50cl"
    assert request.product_id == PRODUCT_A
    assert request.supplying_product_id == PRODUCT_B
    assert request.request_number == "TR-ABC12345"
    assert request.requested_qty == Decimal("10")
    assert outbox.aggregate_type == "transfer_request"


def test_create_refuses_requesting_from_the_same_store():
    command = TransferRequestCreateCommand(
        tenant_id=TENANT,
        product_id=PRODUCT_A,
        supplying_product_id=PRODUCT_B,
        item_name="Coke 50cl",
        request_number="TR-ABC12345",
        requesting_store_id=STORE_A,
        supplying_store_id=STORE_A,
        requested_qty=Decimal("10"),
    )

    with pytest.raises(ValueError, match="same_store"):
        plan_create_transfer_request(command)


# ── Approve / reject ─────────────────────────────────────────────────────────


def test_approve_caps_the_quantity_at_what_was_requested():
    """A supplier may send less, never more than the store asked for."""
    request = _pending_request()
    command = TransferRequestApproveCommand(
        tenant_id=TENANT,
        request_id=request.id,
        approved_qty=Decimal("11"),
        approved_by=uuid4(),
    )

    with pytest.raises(ValueError, match="approved_qty_exceeds_requested"):
        plan_approve_transfer_request(command, request, _balance(PRODUCT_B, STORE_B))


def test_approve_records_the_decision_pin_and_timestamp():
    request = _pending_request()
    approver = uuid4()
    supervisor = uuid4()
    command = TransferRequestApproveCommand(
        tenant_id=TENANT,
        request_id=request.id,
        approved_qty=Decimal("6"),
        approved_by=approver,
        pin_verified_by=supervisor,
        notes="Only six left",
    )

    updated, outbox = plan_approve_transfer_request(
        command, request, _balance(PRODUCT_B, STORE_B)
    )

    assert updated.status == "approved"
    assert updated.approved_qty == Decimal("6")
    assert updated.approved_by == approver
    assert updated.pin_verified_by == supervisor
    assert updated.approved_at is not None
    assert updated.decision_note == "Only six left"
    assert outbox.aggregate_type == "transfer_request"


def test_approve_will_not_promise_stock_the_store_no_longer_has():
    request = _pending_request()
    command = TransferRequestApproveCommand(
        tenant_id=TENANT,
        request_id=request.id,
        approved_qty=Decimal("10"),
        approved_by=uuid4(),
    )

    with pytest.raises(ValueError, match="insufficient_stock"):
        plan_approve_transfer_request(
            command, request, _balance(PRODUCT_B, STORE_B, qty="4.00")
        )


def test_approve_cannot_be_redone():
    request = _pending_request(status="approved", approved_qty=Decimal("10"))
    command = TransferRequestApproveCommand(
        tenant_id=TENANT,
        request_id=request.id,
        approved_qty=Decimal("5"),
        approved_by=uuid4(),
    )

    with pytest.raises(ValueError, match="invalid_status:approved"):
        plan_approve_transfer_request(command, request, _balance(PRODUCT_B, STORE_B))


def test_reject_records_the_reason_for_the_requester():
    request = _pending_request()
    decider = uuid4()
    command = TransferRequestRejectCommand(
        tenant_id=TENANT,
        request_id=request.id,
        rejected_by=decider,
        rejection_reason="Count says only two left",
    )

    updated, outbox = plan_reject_transfer_request(command, request)

    assert updated.status == "rejected"
    assert updated.rejection_reason == "Count says only two left"
    assert updated.approved_by == decider
    assert updated.rejected_at is not None
    assert outbox.aggregate_type == "transfer_request"


# ── Fulfil ───────────────────────────────────────────────────────────────────


def test_fulfil_moves_stock_between_each_stores_own_product_row():
    """Coke B leaves store B's row; Coke A arrives on store A's row."""
    request = _pending_request(status="approved", approved_qty=Decimal("10"))
    fulfiller = uuid4()

    from_balance = _balance(PRODUCT_B, STORE_B, qty="25.00")
    to_balance = _balance(PRODUCT_A, STORE_A, qty="3.00")

    from_adj, to_adj, from_bal, to_bal, updated, outbox = plan_fulfill_transfer_request(
        request=request,
        from_balance=from_balance,
        to_balance=to_balance,
        tenant_id=TENANT,
        fulfilled_by=fulfiller,
    )

    # Decimal math: Numeric columns come back as Decimal, not float.
    assert from_bal.qty == Decimal("15.00")
    assert to_bal.qty == Decimal("13.00")

    assert from_adj.product_id == PRODUCT_B
    assert from_adj.store_id == STORE_B
    assert from_adj.qty_change == Decimal("-10")
    assert to_adj.product_id == PRODUCT_A
    assert to_adj.store_id == STORE_A
    assert to_adj.qty_change == Decimal("10")

    # Both sides reference the request number so the history is traceable.
    assert "TR-ABC12345" in from_adj.notes
    assert "TR-ABC12345" in to_adj.notes

    assert updated.status == "fulfilled"
    assert updated.fulfilled_qty == Decimal("10")
    assert updated.fulfilled_by == fulfiller
    assert updated.fulfilled_at is not None
    assert outbox[0].aggregate_type == "transfer_request"


def test_fulfil_creates_the_missing_balance_for_the_requesting_store():
    request = _pending_request(status="approved", approved_qty=Decimal("4"))

    *_, to_bal, _updated, _outbox = plan_fulfill_transfer_request(
        request=request,
        from_balance=_balance(PRODUCT_B, STORE_B, qty="9.00"),
        to_balance=None,
        tenant_id=TENANT,
        fulfilled_by=uuid4(),
    )

    assert to_bal.product_id == PRODUCT_A
    assert to_bal.store_id == STORE_A
    assert to_bal.qty == Decimal("4")


def test_fulfil_requires_an_approved_request():
    request = _pending_request()  # still pending
    with pytest.raises(ValueError, match="invalid_status:pending"):
        plan_fulfill_transfer_request(
            request=request,
            from_balance=_balance(PRODUCT_B, STORE_B),
            to_balance=None,
            tenant_id=TENANT,
        )


def test_fulfil_rechecks_stock_before_sending():
    request = _pending_request(status="approved", approved_qty=Decimal("10"))
    with pytest.raises(ValueError, match="insufficient_stock"):
        plan_fulfill_transfer_request(
            request=request,
            from_balance=_balance(PRODUCT_B, STORE_B, qty="6.00"),
            to_balance=None,
            tenant_id=TENANT,
        )


# ── Bridge: item identity resolution ─────────────────────────────────────────


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_create_resolves_the_same_named_item_in_both_stores():
    from app.common import bridge

    session = _mock_session()
    sdb = MagicMock()
    sdb.return_value.session.return_value = session
    store_a = SimpleNamespace(id=STORE_A, name="Shop A", is_warehouse=False)
    store_b = SimpleNamespace(id=STORE_B, name="Shop B", is_warehouse=True)
    item_a = SimpleNamespace(product_id=PRODUCT_A, name="Coke 50cl")
    item_b = SimpleNamespace(product_id=PRODUCT_B, name="Coke 50cl")

    with (
        patch.object(bridge, "_get_sdb", sdb),
        patch(
            "app.stores.repository.get_store_with_tenant",
            AsyncMock(side_effect=[store_a, store_b]),
        ),
        patch(
            "app.stores.repository.get_store_product_by_name",
            AsyncMock(side_effect=[item_a, item_b]),
        ),
        patch("app.inventory.repository.create_transfer_request", AsyncMock()),
        patch.object(
            bridge, "_next_transfer_request_number", AsyncMock(return_value="TR-ABC12345")
        ),
        patch.object(
            bridge, "_transfer_request_names", AsyncMock(return_value=({}, {}))
        ),
        patch.object(bridge, "scope_bump", AsyncMock()),
    ):
        result = await bridge.create_transfer_request(
            tenant_id=str(TENANT),
            item_name="Coke 50cl",
            requesting_store_id=str(STORE_A),
            supplying_store_id=str(STORE_B),
            requested_qty=Decimal("10"),
            created_by=str(uuid4()),
        )

    assert result["product_id"] == str(PRODUCT_A)
    assert result["supplying_product_id"] == str(PRODUCT_B)
    assert result["item_name"] == "Coke 50cl"
    assert result["request_number"] == "TR-ABC12345"
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_create_explains_when_the_supplier_does_not_carry_the_item():
    from app.common import bridge

    session = _mock_session()
    sdb = MagicMock()
    sdb.return_value.session.return_value = session
    store_a = SimpleNamespace(id=STORE_A, name="Shop A", is_warehouse=False)
    store_b = SimpleNamespace(id=STORE_B, name="Shop B", is_warehouse=True)
    item_a = SimpleNamespace(product_id=PRODUCT_A, name="Coke 50cl")

    with (
        patch.object(bridge, "_get_sdb", sdb),
        patch(
            "app.stores.repository.get_store_with_tenant",
            AsyncMock(side_effect=[store_a, store_b]),
        ),
        patch(
            "app.stores.repository.get_store_product_by_name",
            AsyncMock(side_effect=[item_a, None]),
        ),
    ):
        with pytest.raises(ValueError, match="supplying_store_does_not_carry_item"):
            await bridge.create_transfer_request(
                tenant_id=str(TENANT),
                item_name="Coke 50cl",
                requesting_store_id=str(STORE_A),
                supplying_store_id=str(STORE_B),
                requested_qty=Decimal("10"),
            )


@pytest.mark.asyncio
async def test_approve_bridge_checks_the_supplying_stores_own_product_row():
    """Availability is read from the supplying store's product, not the
    requesting store's — same name, different catalog row."""
    from app.common import bridge
    from app.inventory.models import TransferRequest as Model

    session = _mock_session()
    sdb = MagicMock()
    sdb.return_value.session.return_value = session
    request = _pending_request()

    with (
        patch.object(bridge, "_get_sdb", sdb),
        patch(
            "app.inventory.repository.get_transfer_request_by_id",
            AsyncMock(return_value=request),
        ),
        patch(
            "app.inventory.repository.get_stock_balance",
            AsyncMock(return_value=_balance(PRODUCT_B, STORE_B, qty="4.00")),
        ) as balance_lookup,
    ):
        with pytest.raises(ValueError, match="insufficient_stock"):
            await bridge.approve_transfer_request(
                tenant_id=str(TENANT),
                request_id=str(request.id),
                approved_qty=Decimal("10"),
                approved_by=str(uuid4()),
            )

    product_id, store_id = balance_lookup.await_args.args[1:]
    assert isinstance(request, Model)
    assert product_id == PRODUCT_B
    assert store_id == STORE_B
