"""Tests for the per-line ``extra`` price override.

Once the owner enables ``allow_price_override`` in the business settings, any
permitted role can reprice a cart/sale line without touching the product's
selling price: the effective price is ``unit_price + extra`` and everything
downstream (line totals, tax, cart total) must use it.
"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.cart.schemas import AddItemCommand, CartCreateCommand, CheckoutCommand
from app.cart.service import (
    plan_add_item,
    plan_bulk_add_items,
    plan_cart_creation,
    plan_checkout,
)
from app.sales.schemas import SaleCreateCommand, SaleItemLine
from app.sales.service import plan_sale_creation


def _cart_creation():
    tenant_id = uuid4()
    cart_result, cart_model, _ = plan_cart_creation(
        CartCreateCommand(store_id=uuid4()), tenant_id=tenant_id
    )
    return tenant_id, cart_result, cart_model


class TestExtraOnCart:
    def test_add_item_stores_extra(self):
        tenant_id, cart_result, _ = _cart_creation()
        item, _ = plan_add_item(
            AddItemCommand(
                tenant_id=tenant_id,
                cart_id=cart_result.id,
                store_id=uuid4(),
                product_id=uuid4(),
                product_public_id="prd_extra",
                name="Phone",
                unit_price=Decimal("2000"),
                extra=Decimal("200"),
                qty=Decimal("1"),
            ),
            current_items=[],
        )
        assert float(item.unit_price) == 2000
        assert float(item.extra) == 200
        assert float(item.unit_price) + float(item.extra) == 2200

    def test_checkout_total_and_event_include_extra(self):
        tenant_id, cart_result, cart_model = _cart_creation()
        item, _ = plan_add_item(
            AddItemCommand(
                tenant_id=tenant_id,
                cart_id=cart_result.id,
                store_id=uuid4(),
                product_id=uuid4(),
                product_public_id="prd_extra",
                name="Phone",
                unit_price=Decimal("2000"),
                extra=Decimal("-50"),
                qty=Decimal("2"),
            ),
            current_items=[],
        )
        outbox = plan_checkout(
            CheckoutCommand(tenant_id=tenant_id, cart_id=cart_result.id),
            cart_model,
            [item],
        )
        event = outbox[0].event
        assert Decimal(event.payload["total"]) == Decimal("3900")
        assert event.payload["items"][0]["unit_price"] == "2000.0"
        assert event.payload["items"][0]["extra"] == "-50.0"

    def test_bulk_add_items_accepts_extra_tuple(self):
        tenant_id, cart_result, _ = _cart_creation()
        items, _ = plan_bulk_add_items(
            CheckoutCommand(tenant_id=tenant_id, cart_id=cart_result.id),
            [
                (
                    uuid4(),
                    "prd_bulk",
                    "Watch",
                    Decimal("1000"),
                    Decimal("1"),
                    None,
                    Decimal("150"),
                )
            ],
        )
        assert float(items[0].extra) == 150


class TestExtraOnSale:
    def test_tax_and_line_total_use_effective_price(self):
        tax = {"id": str(uuid4()), "name": "VAT", "rate": 10, "tax_inclusive": False}
        sale_result, _, sale_items, _ = plan_sale_creation(
            SaleCreateCommand(
                tenant_id=uuid4(),
                cashier_id=uuid4(),
                store_id=uuid4(),
                items=[
                    SaleItemLine(
                        product_id=uuid4(),
                        product_name="Phone",
                        qty=Decimal("1"),
                        unit_price=Decimal("2000"),
                        extra=Decimal("200"),
                        taxes=[tax],
                    )
                ],
            )
        )
        assert float(sale_items[0].unit_price) == 2000
        assert float(sale_items[0].extra) == 200
        assert float(sale_items[0].line_total) == 2200
        assert float(sale_result.tax) == 220
        assert float(sale_result.total) == 2420

    def test_negative_extra_reduces_taxable_base(self):
        tax = {"id": str(uuid4()), "name": "VAT", "rate": 10, "tax_inclusive": False}
        sale_result, _, sale_items, _ = plan_sale_creation(
            SaleCreateCommand(
                tenant_id=uuid4(),
                cashier_id=uuid4(),
                store_id=uuid4(),
                items=[
                    SaleItemLine(
                        product_id=uuid4(),
                        product_name="Phone",
                        qty=Decimal("1"),
                        unit_price=Decimal("2000"),
                        extra=Decimal("-50"),
                        taxes=[tax],
                    )
                ],
            )
        )
        assert float(sale_items[0].line_total) == 1950
        assert float(sale_result.tax) == 195
        assert float(sale_result.total) == 2145


class TestPriceOverrideSetting:
    """The owner enables overrides for the tenant; the flag gates non-zero extra."""

    def test_defaults_to_disabled(self):
        from app.tenancy.settings import price_override_enabled

        assert price_override_enabled("{}") is False
        assert price_override_enabled(None) is False
        assert price_override_enabled("not json") is False
        assert price_override_enabled({"allow_price_override": False}) is False

    def test_reads_flag_from_json_string(self):
        from app.tenancy.settings import price_override_enabled

        assert price_override_enabled('{"allow_price_override": true}') is True

    def test_reads_flag_from_dict(self):
        from app.tenancy.settings import price_override_enabled

        assert price_override_enabled({"allow_price_override": True}) is True

    async def test_guard_rejects_non_zero_extra_when_disabled(self, monkeypatch):
        from app.core import dependencies

        async def _disabled(business_id, session=None):
            return False

        monkeypatch.setattr(
            "app.tenancy.settings.is_price_override_enabled", _disabled
        )
        ctx = SimpleNamespace(user=SimpleNamespace(business_id=str(uuid4())))

        with pytest.raises(HTTPException) as exc:
            await dependencies.require_price_override_enabled(ctx)
        assert exc.value.status_code == 403

    async def test_guard_allows_extra_when_owner_enabled_it(self, monkeypatch):
        from app.core import dependencies

        async def _enabled(business_id, session=None):
            return True

        monkeypatch.setattr(
            "app.tenancy.settings.is_price_override_enabled", _enabled
        )
        ctx = SimpleNamespace(user=SimpleNamespace(business_id=str(uuid4())))

        await dependencies.require_price_override_enabled(ctx)
