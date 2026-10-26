"""Cash collections were billed the external processor's share of the fee.

The full commission (3.5%) bundles a 2% gateway cost on top of the platform's
1.5%, but money a customer hands over at the counter — or via a receipt or a
converted document — goes through no processor, so only the platform's share
should be charged. The rule's ``cash_amount`` now holds that platform-only
rate, and every fee path (single payment, batch, split, documents) picks the
rate by how the money actually arrived.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.platform import fee_calculator
from app.platform.fee_calculator import calculate_fee_shares, calculate_platform_fee


def _rule(amount=3.5, cash_amount=1.5, fee_type="percentage", min_threshold=0):
    return SimpleNamespace(
        id=uuid4(),
        label="standard",
        fee_type=fee_type,
        amount=amount,
        cash_amount=cash_amount,
        min_threshold=min_threshold,
    )


def _session(*results):
    """AsyncSession stand-in answering consecutive execute() calls in order."""
    session = MagicMock()

    async def execute(*args, **kwargs):
        call = execute.call_order = getattr(execute, "call_order", 0)
        execute.call_order = call + 1
        return results[min(call, len(results) - 1)]

    session.execute = AsyncMock(side_effect=execute)
    session.add = MagicMock()
    return session


def _result(rows=None, row=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows or []
    result.scalar.return_value = row
    result.scalar_one_or_none.return_value = row
    return result


class TestCalculatePlatformFeeMethodRates:
    async def test_cash_is_charged_the_platform_only_rate(self):
        session = _session(_result(rows=[_rule()]))
        fee = await calculate_platform_fee(session, 1000.0, method="cash")
        assert fee["rate"] == 1.5
        assert fee["platform_fee"] == 15.0
        assert fee["settlement_amount"] == 985.0

    async def test_card_is_charged_the_full_rate(self):
        session = _session(_result(rows=[_rule()]))
        fee = await calculate_platform_fee(session, 1000.0, method="card")
        assert fee["rate"] == 3.5
        assert fee["platform_fee"] == 35.0

    async def test_transfer_is_charged_the_full_rate(self):
        session = _session(_result(rows=[_rule()]))
        fee = await calculate_platform_fee(session, 1000.0, method="transfer")
        assert fee["platform_fee"] == 35.0

    async def test_a_rule_without_a_cash_rate_falls_back_to_the_full_rate(self):
        """Legacy rules predate cash_amount; they keep charging as before."""
        session = _session(_result(rows=[_rule(cash_amount=None)]))
        fee = await calculate_platform_fee(session, 1000.0, method="cash")
        assert fee["platform_fee"] == 35.0

    async def test_receipts_and_converted_docs_are_processorless(self):
        session = _session(_result(rows=[_rule()]))
        for method in ("receipt", "converted"):
            fee = await calculate_platform_fee(session, 1000.0, method=method)
            assert fee["platform_fee"] == 15.0, method

    async def test_flat_cash_rules_use_the_cash_flat_amount(self):
        session = _session(_result(rows=[_rule(amount=100, cash_amount=45, fee_type="flat")]))
        fee = await calculate_platform_fee(session, 1000.0, method="cash")
        assert fee["platform_fee"] == 45.0


class TestCalculateFeeShares:
    async def test_a_mixed_batch_is_billed_per_method(self):
        session = _session(_result(rows=[_rule()]))
        result = await calculate_fee_shares(
            session, 1000.0, {"cash": 400.0, "card": 600.0}
        )
        by_method = {s["method"]: s for s in result["shares"]}
        # cash: 1.5% of 1000 scaled by 400/1000 = 6.00
        assert by_method["cash"]["amount"] == 6.0
        assert by_method["cash"]["rate"] == 1.5
        # card: 3.5% of 1000 scaled by 600/1000 = 21.00
        assert by_method["card"]["amount"] == 21.0
        assert by_method["card"]["rate"] == 3.5
        assert result["total_fee"] == 27.0

    async def test_a_cash_only_batch_never_bills_the_processor_share(self):
        session = _session(_result(rows=[_rule()]))
        result = await calculate_fee_shares(session, 1000.0, {"cash": 1000.0})
        assert result["total_fee"] == 15.0

    async def test_zero_splits_are_skipped(self):
        session = _session(_result(rows=[_rule()]))
        result = await calculate_fee_shares(
            session, 1000.0, {"cash": 0, "card": 1000.0}
        )
        assert [s["method"] for s in result["shares"]] == ["card"]
        assert result["total_fee"] == 35.0


class TestRecordPlatformFeeUsesTheMethod:
    async def test_a_booked_fee_carries_the_method_to_the_calculator(self):
        """A standalone receipt books its fee at the platform-only rate."""
        from app.platform.fee_calculator import record_platform_fee

        session = _session(
            _result(row=None),  # no existing fee for the document
            _result(rows=[_rule()]),
        )
        await record_platform_fee(
            session,
            tenant_id=uuid4(),
            total=1000.0,
            document_id=uuid4(),
            payment_method="receipt",
        )

        fee_row = session.add.call_args
        assert fee_row is not None
        recorded = fee_row.args[0]
        assert recorded.amount == 15.0  # 1.5%, not the 3.5% full rate
        assert recorded.rate == 1.5
        assert recorded.payment_method == "receipt"


class TestRateForRule:
    def test_no_processor_methods_pick_the_cash_rate(self):
        rule = _rule()
        for method in fee_calculator.NO_PROCESSOR_METHODS:
            assert fee_calculator.rate_for_rule(rule, method) == pytest.approx(1.5)

    def test_processor_methods_pick_the_full_rate(self):
        rule = _rule()
        assert fee_calculator.rate_for_rule(rule, "card") == 3.5

    def test_unknown_method_gets_the_full_rate(self):
        rule = _rule()
        assert fee_calculator.rate_for_rule(rule, "something-new") == 3.5
