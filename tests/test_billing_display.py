"""Display-currency conversion for tenant billing responses."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.billing.display import display_amount, display_context, fx_fields


def test_display_amount():
    ctx = {"currency": "USD", "rate": "0.0007", "as_of": "2026-11-22"}
    assert display_amount(Decimal("2000"), ctx) == Decimal("1.40")
    assert display_amount(5000, ctx) == Decimal("3.50")
    assert display_amount(None, ctx) is None


def test_display_amount_ngn_ctx_no_rate():
    ctx = {"currency": "NGN", "rate": None, "as_of": None}
    assert display_amount(Decimal("2000"), ctx) is None


def test_fx_fields():
    ctx = {"currency": "USD", "rate": "0.0007", "as_of": "2026-11-22"}
    fields = fx_fields(ctx)
    assert fields["display_currency"] == "USD"
    assert fields["display_fx_rate"] == "0.0007"


@pytest.mark.asyncio
async def test_display_context_ngn_tenant():
    session = MagicMock()
    row_result = MagicMock()
    row_result.mappings.return_value.first.return_value = {"settings": '{"currency": "NGN"}'}
    session.execute = AsyncMock(return_value=row_result)
    with patch("app.billing.display.get_rate", new_callable=AsyncMock) as get_rate:
        ctx = await display_context("00000000-0000-0000-0000-000000000001", session)
    assert ctx["currency"] == "NGN"
    assert ctx["rate"] is None
    get_rate.assert_not_awaited()


@pytest.mark.asyncio
async def test_display_context_usd_tenant():
    session = MagicMock()
    row_result = MagicMock()
    row_result.mappings.return_value.first.return_value = {"settings": '{"currency": "USD"}'}
    session.execute = AsyncMock(return_value=row_result)
    with patch(
        "app.billing.display.get_rate",
        new_callable=AsyncMock,
        return_value={"usd_per_ngn": "0.0007", "as_of": "2026-11-22"},
    ):
        ctx = await display_context("00000000-0000-0000-0000-000000000001", session)
    assert ctx["currency"] == "USD"
    assert ctx["rate"] == "0.0007"
    assert ctx["as_of"] == "2026-11-22"
