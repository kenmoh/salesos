import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

TENANT = "00000000-0000-0000-0000-000000000001"


def _session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    return session


def _result(rows):
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    result.fetchone = MagicMock(return_value=rows[0] if rows else None)
    return result


def test_new_tools_are_registered():
    from app.ai.tools import TOOL_DESCRIPTIONS, TOOL_REGISTRY

    expected = {
        "get_expiring_products",
        "get_stock_history",
        "get_transfer_requests",
        "get_payment_status",
        "get_pending_payments",
        "get_settlement_summary",
        "get_receipt",
        "get_accounts_payable",
        "get_sale_detail",
    }
    assert expected.issubset(TOOL_REGISTRY)
    assert expected.issubset(TOOL_DESCRIPTIONS)
    assert all(callable(TOOL_REGISTRY[name]) for name in expected)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Which products are expiring in 14 days?", "get_expiring_products"),
        ("Why did stock of Rice change?", "get_stock_history"),
        ("Show me pending transfer requests", "get_transfer_requests"),
        ("Did the bank transfer arrive?", "get_payment_status"),
        ("Show pending payments older than 2 hours", "get_pending_payments"),
        ("What is the settlement summary this month?", "get_settlement_summary"),
        ("Was the receipt sent?", "get_receipt"),
        ("What do we owe vendors?", "get_accounts_payable"),
        ("Give me the sale details", "get_sale_detail"),
    ],
)
def test_select_tools_routes_new_questions(message, expected):
    from app.ai.service import select_tools

    assert expected in select_tools(message)


def test_stock_transfer_is_not_money_transfer():
    from app.ai.service import select_tools

    tools = select_tools("Show pending stock transfer requests")
    assert "get_transfer_requests" in tools
    assert "get_payment_status" not in tools


def test_sale_number_adds_payment_and_detail_lookup():
    from app.ai.service import select_tools

    tools = select_tools("Was SL-20260101-ABCDEF12 paid?")
    assert "get_payment_status" in tools
    assert "get_sale_detail" in tools


def test_select_tools_returns_empty_when_nothing_matches():
    from app.ai.service import select_tools

    assert select_tools("hello there") == []


def test_payment_method_totals_ignores_settlement_metadata():
    from app.ai.tools import _payment_method_totals

    totals = _payment_method_totals(
        json.dumps(
            {
                "cash": 500,
                "transfer": 1500,
                "total": 2000,
                "compare_total": 2000,
                "platform_fee": 25,
                "settlement_amount": 1975,
                "fee_rule_id": "abc",
            }
        )
    )
    assert totals == {"cash": 500.0, "transfer": 1500.0}


def test_payment_method_totals_expands_split_payments():
    from app.ai.tools import _payment_method_totals

    totals = _payment_method_totals(
        json.dumps({"split": {"cash": 1000, "card": 2500}, "total": 3500})
    )
    assert totals == {"cash": 1000.0, "card": 2500.0}


@pytest.mark.asyncio
async def test_recent_transactions_uses_payment_methods_column():
    """Regression: sales.payment_method does not exist, payment_methods does."""
    from app.ai.tools import get_recent_transactions

    session = _session()
    session.execute.return_value = _result(
        [
            (
                "sale-1",
                "SL-20260101-ABCDEF12",
                "Ada",
                Decimal("2000"),
                json.dumps({"cash": 500, "transfer": 1500, "total": 2000}),
                "completed",
                datetime(2026, 1, 1, tzinfo=UTC),
                "RCP-20260101-1234ABCD",
            )
        ]
    )

    payload = json.loads(
        await get_recent_transactions(session, TENANT, limit=10)
    )
    transaction = payload["transactions"][0]

    assert transaction["payment_methods"] == {"cash": 500.0, "transfer": 1500.0}
    assert transaction["payment_method"] == "transfer"
    assert transaction["receipt_number"] == "RCP-20260101-1234ABCD"

    sql = str(session.execute.call_args.args[0])
    assert "s.payment_methods" in sql
    assert "s.payment_method " not in sql


@pytest.mark.asyncio
async def test_expiring_products_returns_expiry_fields():
    from app.ai.tools import get_expiring_products

    expiry = date.today() + timedelta(days=3)
    session = _session()
    session.execute.return_value = _result(
        [
            (
                "product-1",
                "Milk",
                "SKU-1",
                expiry,
                "store-1",
                "Main Store",
                8,
                Decimal("300"),
            )
        ]
    )

    payload = json.loads(await get_expiring_products(session, TENANT, days=7))
    product = payload["products"][0]

    assert product["expiry_date"] == expiry.isoformat()
    assert product["expired"] is False
    assert product["days_left"] == 3
    assert product["stock_value_at_cost"] == 2400.0


def test_sanitize_tool_result_hides_internal_errors():
    from app.ai.service import _sanitize_tool_result

    hidden = json.loads(
        _sanitize_tool_result(
            json.dumps({"error": 'relation "payments" does not exist'})
        )
    )
    assert hidden["error"] == "Something went wrong"

    safe = json.loads(_sanitize_tool_result(json.dumps({"error": "Sale not found"})))
    assert safe["error"] == "Sale not found"
