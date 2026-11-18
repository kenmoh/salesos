"""Tests for the cashier tip ledger and settlement planning."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.sales.models import Sale, SaleItem
from app.tips.routes import _scope_cashier
from app.tips.schemas import SettlementRequest
from app.tips.service import build_tips_for_sale, plan_settlement


def _sale(**overrides):
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "cashier_id": uuid4(),
        "store_id": uuid4(),
        "created_at": datetime(2026, 11, 18, 9, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return Sale(**values)


def _item(extra, qty="1", **overrides):
    values = {
        "id": uuid4(),
        "sale_id": uuid4(),
        "product_id": uuid4(),
        "product_name": "Phone",
        "qty": Decimal(str(qty)),
        "unit_price": Decimal("2000"),
        "extra": Decimal(str(extra)),
        "line_total": Decimal("2000"),
    }
    values.update(overrides)
    return SaleItem(**values)


class TestBuildTips:
    def test_positive_extra_creates_one_tip_per_line(self):
        sale = _sale()
        first = _item("200", "2", sale_id=sale.id)
        second = _item("50", "1", sale_id=sale.id)

        tips = build_tips_for_sale(sale, [first, second])

        assert len(tips) == 2
        assert [float(tip.amount) for tip in tips] == [400.0, 50.0]
        assert [float(tip.extra) for tip in tips] == [200.0, 50.0]
        assert all(tip.status == "pending" for tip in tips)
        assert all(tip.cashier_id == sale.cashier_id for tip in tips)
        assert all(tip.store_id == sale.store_id for tip in tips)
        assert all(tip.sale_id == sale.id for tip in tips)
        assert all(tip.earned_at == sale.created_at for tip in tips)

    def test_negative_or_zero_extra_is_not_a_tip(self):
        sale = _sale()
        tips = build_tips_for_sale(
            sale,
            [
                _item("-50", "1", sale_id=sale.id),
                _item("0", "1", sale_id=sale.id),
            ],
        )
        assert tips == []

    def test_sale_without_cashier_earns_no_tips(self):
        sale = _sale(cashier_id=None)
        assert build_tips_for_sale(sale, [_item("100", "1", sale_id=sale.id)]) == []

    def test_amount_rounds_to_two_decimals(self):
        sale = _sale()
        tips = build_tips_for_sale(
            sale, [_item("12.55", "3", sale_id=sale.id)]
        )
        assert tips[0].amount == Decimal("37.65")

    def test_earned_at_falls_back_to_now_when_sale_has_no_timestamp(self):
        sale = _sale(created_at=None)
        tips = build_tips_for_sale(sale, [_item("10", "1", sale_id=sale.id)])
        assert tips[0].earned_at.tzinfo is not None


class TestPlanSettlement:
    def _tips(self, sale):
        return build_tips_for_sale(
            sale,
            [
                _item("100", "2", sale_id=sale.id),
                _item("25.50", "1", sale_id=sale.id),
            ],
        )

    def test_sums_locked_tips_into_a_paid_settlement(self):
        sale = _sale()
        tips = self._tips(sale)
        paid_by = uuid4()

        settlement, amount = plan_settlement(
            SettlementRequest(
                cashier_id=sale.cashier_id,
                period_start=date(2026, 11, 1),
                period_end=date(2026, 11, 18),
                note="Week 3",
            ),
            tips,
            paid_by=paid_by,
        )

        assert amount == Decimal("225.50")
        assert settlement.amount == amount
        assert settlement.tip_count == 2
        assert settlement.status == "paid"
        assert settlement.tenant_id == sale.tenant_id
        assert settlement.cashier_id == sale.cashier_id
        assert settlement.paid_by == paid_by
        assert settlement.note == "Week 3"

    def test_rejects_reversed_period(self):
        sale = _sale()
        with pytest.raises(ValueError, match="invalid_period"):
            plan_settlement(
                SettlementRequest(
                    cashier_id=sale.cashier_id,
                    period_start=date(2026, 11, 18),
                    period_end=date(2026, 11, 1),
                ),
                self._tips(sale),
                paid_by=uuid4(),
            )

    def test_rejects_empty_batch(self):
        sale = _sale()
        with pytest.raises(ValueError, match="no_tips_to_settle"):
            plan_settlement(
                SettlementRequest(
                    cashier_id=sale.cashier_id,
                    period_start=date(2026, 11, 1),
                    period_end=date(2026, 11, 18),
                ),
                [],
                paid_by=uuid4(),
            )


class TestCashierScope:
    def _ctx(self, *perms):
        user = SimpleNamespace(
            user_id=str(uuid4()),
            has_perm=lambda perm: perm in perms,
        )
        return SimpleNamespace(user=user)

    def test_settler_may_filter_any_cashier(self):
        requested = uuid4()
        assert _scope_cashier(self._ctx("tips:settle"), str(requested)) == requested

    def test_reader_without_settle_permission_is_pinned_to_self(self):
        ctx = self._ctx("tips:read")
        assert str(_scope_cashier(ctx, str(uuid4()))) == ctx.user.user_id

    def test_invalid_cashier_id_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            _scope_cashier(self._ctx("tips:settle"), "not-a-uuid")
        assert exc.value.status_code == 400
