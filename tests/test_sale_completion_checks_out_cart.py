"""When a sale is completed via payment, its cart is marked checked_out."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _make_session():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=None)
    result.scalars = MagicMock(return_value=scalars)
    session.execute.return_value = result
    return session


def _make_sale(cart_id, status="pending", amount_paid=0.0, total=100.0):
    sale = MagicMock()
    sale.id = uuid4()
    sale.tenant_id = uuid4()
    sale.cart_id = cart_id
    sale.status = status
    sale.amount_paid = amount_paid
    sale.total = total
    sale.payment_methods = {}
    sale.sale_number = "SL-20261124-TEST0001"
    return sale


def _make_cart(status="active"):
    cart = MagicMock()
    cart.id = uuid4()
    cart.status = status
    return cart


@pytest.mark.asyncio
class TestSaleCompletionChecksOutCart:
    async def test_cart_checked_out_when_sale_completes(self):
        cart_id = uuid4()
        sale = _make_sale(cart_id, status="pending", amount_paid=0.0, total=100.0)
        cart = _make_cart(status="active")

        pay_session = _make_session()
        cart_session = _make_session()

        pay_sdb = MagicMock()
        pay_sdb.session.return_value = pay_session
        cart_sdb = MagicMock()
        cart_sdb.session.return_value = cart_session

        from app.common import bridge

        with (
            patch.object(
                bridge,
                "_get_sdb",
                side_effect=lambda s: cart_sdb if s == "cart" else pay_sdb,
            ),
            patch("app.sales.repository.get_sale_by_id", AsyncMock(return_value=sale)),
            patch("app.payments.repository.create_payment", AsyncMock()),
            patch(
                "app.platform.fee_calculator.calculate_platform_fee",
                AsyncMock(
                    return_value={
                        "platform_fee": 15.0,
                        "settlement_amount": 85.0,
                        "fee_type": "percentage",
                        "rule_id": "rule-1",
                        "rate": 0.015,
                    }
                ),
            ),
            patch(
                "app.cart.repository.get_cart_by_id", AsyncMock(return_value=cart)
            ),
            patch(
                "app.cart.repository.update_cart_status", AsyncMock(return_value=cart)
            ) as mock_update_cart,
            patch("app.payments.service.plan_payment_success", return_value=[]),
            patch("app.common.bridge._settle_sale_receivable", AsyncMock()),
        ):
            await bridge.record_payment(
                business_id=str(sale.tenant_id),
                sale_id=str(sale.id),
                method="cash",
                amount=Decimal("100.00"),
                reference=None,
                recorded_by=str(uuid4()),
            )

        # Cart should have been checked out
        mock_update_cart.assert_called_once()
        call_args = mock_update_cart.call_args
        assert call_args[0][1] == cart.id
        assert call_args[0][2] == "checked_out"

    async def test_cart_not_checked_out_when_sale_not_completed(self):
        cart_id = uuid4()
        sale = _make_sale(cart_id, status="pending", amount_paid=0.0, total=100.0)
        cart = _make_cart(status="active")

        pay_session = _make_session()
        cart_session = _make_session()

        pay_sdb = MagicMock()
        pay_sdb.session.return_value = pay_session
        cart_sdb = MagicMock()
        cart_sdb.session.return_value = cart_session

        from app.common import bridge

        with (
            patch.object(
                bridge,
                "_get_sdb",
                side_effect=lambda s: cart_sdb if s == "cart" else pay_sdb,
            ),
            patch("app.sales.repository.get_sale_by_id", AsyncMock(return_value=sale)),
            patch("app.payments.repository.create_payment", AsyncMock()),
            patch(
                "app.cart.repository.get_cart_by_id", AsyncMock(return_value=cart)
            ),
            patch(
                "app.cart.repository.update_cart_status", AsyncMock()
            ) as mock_update_cart,
            patch("app.payments.service.plan_payment_success", return_value=[]),
            patch("app.common.bridge._settle_sale_receivable", AsyncMock()),
        ):
            await bridge.record_payment(
                business_id=str(sale.tenant_id),
                sale_id=str(sale.id),
                method="cash",
                amount=Decimal("50.00"),
                reference=None,
                recorded_by=str(uuid4()),
            )

        # Cart should NOT have been checked out (sale is only partial)
        mock_update_cart.assert_not_called()

    async def test_cart_not_checked_out_when_already_completed(self):
        cart_id = uuid4()
        sale = _make_sale(cart_id, status="completed", amount_paid=100.0, total=100.0)
        cart = _make_cart(status="checked_out")

        pay_session = _make_session()
        cart_session = _make_session()

        pay_sdb = MagicMock()
        pay_sdb.session.return_value = pay_session
        cart_sdb = MagicMock()
        cart_sdb.session.return_value = cart_session

        from app.common import bridge

        with (
            patch.object(
                bridge,
                "_get_sdb",
                side_effect=lambda s: cart_sdb if s == "cart" else pay_sdb,
            ),
            patch("app.sales.repository.get_sale_by_id", AsyncMock(return_value=sale)),
            patch("app.payments.repository.create_payment", AsyncMock()),
            patch(
                "app.cart.repository.get_cart_by_id", AsyncMock(return_value=cart)
            ),
            patch(
                "app.cart.repository.update_cart_status", AsyncMock()
            ) as mock_update_cart,
            patch("app.payments.service.plan_payment_success", return_value=[]),
            patch("app.common.bridge._settle_sale_receivable", AsyncMock()),
        ):
            await bridge.record_payment(
                business_id=str(sale.tenant_id),
                sale_id=str(sale.id),
                method="cash",
                amount=Decimal("0.00"),
                reference=None,
                recorded_by=str(uuid4()),
            )

        # Cart should NOT have been touched (sale was already completed)
        mock_update_cart.assert_not_called()

    async def test_cart_not_checked_out_when_no_cart_id(self):
        sale = _make_sale(None, status="pending", amount_paid=0.0, total=100.0)

        pay_session = _make_session()
        cart_session = _make_session()

        pay_sdb = MagicMock()
        pay_sdb.session.return_value = pay_session
        cart_sdb = MagicMock()
        cart_sdb.session.return_value = cart_session

        from app.common import bridge

        with (
            patch.object(
                bridge,
                "_get_sdb",
                side_effect=lambda s: cart_sdb if s == "cart" else pay_sdb,
            ),
            patch("app.sales.repository.get_sale_by_id", AsyncMock(return_value=sale)),
            patch("app.payments.repository.create_payment", AsyncMock()),
            patch(
                "app.platform.fee_calculator.calculate_platform_fee",
                AsyncMock(
                    return_value={
                        "platform_fee": 15.0,
                        "settlement_amount": 85.0,
                        "fee_type": "percentage",
                        "rule_id": "rule-1",
                        "rate": 0.015,
                    }
                ),
            ),
            patch(
                "app.cart.repository.get_cart_by_id", AsyncMock(return_value=None)
            ),
            patch(
                "app.cart.repository.update_cart_status", AsyncMock()
            ) as mock_update_cart,
            patch("app.payments.service.plan_payment_success", return_value=[]),
            patch("app.common.bridge._settle_sale_receivable", AsyncMock()),
        ):
            await bridge.record_payment(
                business_id=str(sale.tenant_id),
                sale_id=str(sale.id),
                method="cash",
                amount=Decimal("100.00"),
                reference=None,
                recorded_by=str(uuid4()),
            )

        # No cart_id on sale, so cart checkout should be skipped
        mock_update_cart.assert_not_called()
