"""Integration tests for the full StoreFlow checkout flow.

Tests the complete event-driven flow:
    scan product → cart → checkout → sale → stock reservation → payment → confirm → commit stock → accounting
"""

from decimal import Decimal
from uuid import uuid4

import pytest


class TestCheckoutFlow:
    """End-to-end checkout flow using planning functions only (no DB)."""

    def test_full_checkout_flow(self):
        tenant_id = uuid4()
        actor_id = uuid4()
        product_id = uuid4()
        product_public_id = "prd_abc123"
        product_name = "Test Product"
        selling_price = Decimal("1500.00")
        qty = Decimal("2")

        from app.cart.schemas import CartCreateCommand, AddItemCommand, CheckoutCommand
        from app.cart.service import plan_cart_creation, plan_add_item, plan_checkout
        from app.sales.schemas import SaleCreateCommand, SaleItemLine
        from app.sales.service import plan_sale_creation
        from app.payments.schemas import PaymentIntentCreateCommand
        from app.payments.service import plan_payment_intent, plan_payment_success
        from app.accounting.schemas import JournalPostCommand
        from app.accounting.service import plan_post_journal

        cart_result, cart_model, cart_outbox = plan_cart_creation(
            CartCreateCommand(
                store_id=uuid4(),
            ),
            tenant_id=tenant_id,
            correlation_id="corr-001",
        )
        assert cart_result.status == "active"
        assert cart_result.session_id.startswith("sess_")
        assert len(cart_outbox) == 1
        assert cart_outbox[0].event.event_type == "cart.created"

        add_result, add_outbox = plan_add_item(
            AddItemCommand(
                tenant_id=tenant_id,
                cart_id=cart_result.id,
                store_id=uuid4(),
                product_id=product_id,
                product_public_id=product_public_id,
                name=product_name,
                unit_price=selling_price,
                qty=qty,
                correlation_id="corr-002",
            ),
            current_items=[],
        )
        assert add_result.product_id == product_id
        assert float(add_result.unit_price) == float(selling_price)
        assert len(add_outbox) == 1
        assert add_outbox[0].event.event_type == "cart.item_added"

        checkout_outbox = plan_checkout(
            CheckoutCommand(
                tenant_id=tenant_id,
                cart_id=cart_result.id,
                correlation_id="corr-003",
            ),
            cart_model,
            [add_result],
        )
        assert len(checkout_outbox) == 1
        assert checkout_outbox[0].event.event_type == "cart.checked_out"
        assert checkout_outbox[0].event.payload["total"] == str(float(selling_price) * float(qty))

        sale_items = [
            SaleItemLine(
                product_id=product_id,
                product_name=product_name,
                qty=qty,
                unit_price=selling_price,
            )
        ]
        sale_result, sale_model, sale_item_models, sale_outbox = plan_sale_creation(
            SaleCreateCommand(
                tenant_id=tenant_id,
                cashier_id=actor_id,
                store_id=uuid4(),
                items=sale_items,
                correlation_id="corr-004",
            )
        )
        assert sale_result.status == "pending"
        assert sale_result.total == float(selling_price) * float(qty)
        assert len(sale_outbox) == 1
        assert sale_outbox[0].event.event_type == "sales.sale_created"

        payment_result, payment_model, payment_outbox = plan_payment_intent(
            PaymentIntentCreateCommand(
                tenant_id=tenant_id,
                sale_id=sale_result.id,
                method="card",
                amount=sale_result.total,
                currency="NGN",
                correlation_id="corr-005",
            )
        )
        assert payment_result.reference.startswith("SF-")
        assert payment_result.amount == sale_result.total
        assert len(payment_outbox) == 1
        assert payment_outbox[0].event.event_type == "payment.intent_created"

        success_outbox = plan_payment_success(
            tenant_id=tenant_id,
            payment_id=payment_result.payment_id,
            sale_id=str(sale_result.id),
            amount=str(sale_result.total),
            method="card",
            reference=payment_result.reference,
            correlation_id="corr-006",
        )
        assert len(success_outbox) == 1
        assert success_outbox[0].event.event_type == "payment.succeeded"

        journal_outbox, journal_update = plan_post_journal(
            command=JournalPostCommand(
                journal_id=uuid4(),
                actor_id=actor_id,
                correlation_id="corr-007",
            ),
            journal_number="JRN-TEST-001",
            tenant_id=str(tenant_id),
            reference_type="sale",
            reference_id=str(sale_result.id),
        )
        assert len(journal_outbox) == 1
        assert journal_outbox[0].event.event_type == "accounting.journal_posted"

    def test_stock_reservation_and_commit(self):
        tenant_id = uuid4()
        product_id = uuid4()
        store_id = uuid4()
        sale_id = uuid4()

        from app.inventory.schemas import AdjustStockCommand, ReserveStockCommand
        from app.inventory.service import (
            plan_adjust_stock,
            plan_reserve_stock,
            plan_commit_stock,
        )

        adjustment, balance, adjust_outbox = plan_adjust_stock(
            AdjustStockCommand(
                tenant_id=tenant_id,
                product_id=product_id,
                store_id=store_id,
                reason="initial_stock",
                qty_change=Decimal("100"),
                unit_cost=Decimal("500"),
            ),
            current_balance=None,
        )
        assert float(balance.qty) == 100.0
        assert balance.reserved_qty == 0
        assert len(adjust_outbox) == 1

        reservation, updated_balance, reserve_outbox = plan_reserve_stock(
            ReserveStockCommand(
                tenant_id=tenant_id,
                product_id=product_id,
                store_id=store_id,
                sale_id=sale_id,
                qty=Decimal("5"),
                correlation_id="corr-reserve-001",
            ),
            balance=balance,
        )
        assert float(updated_balance.reserved_qty) == 5.0
        assert float(updated_balance.qty) == 100.0
        assert len(reserve_outbox) == 1

        committed_balance, commit_outbox = plan_commit_stock(
            reservation=reservation,
            balance=updated_balance,
            correlation_id="corr-commit-001",
        )
        assert float(committed_balance.qty) == 95.0
        assert float(committed_balance.reserved_qty) == 0.0
        assert len(commit_outbox) == 1

    def test_stock_release_on_void(self):
        tenant_id = uuid4()
        product_id = uuid4()
        store_id = uuid4()
        sale_id = uuid4()

        from app.inventory.schemas import AdjustStockCommand, ReserveStockCommand
        from app.inventory.service import (
            plan_adjust_stock,
            plan_reserve_stock,
            plan_release_reservation,
        )

        _, balance, _ = plan_adjust_stock(
            AdjustStockCommand(
                tenant_id=tenant_id,
                product_id=product_id,
                store_id=store_id,
                reason="initial_stock",
                qty_change=Decimal("50"),
                unit_cost=Decimal("500"),
            ),
            current_balance=None,
        )

        reservation, reserved_balance, _ = plan_reserve_stock(
            ReserveStockCommand(
                tenant_id=tenant_id,
                product_id=product_id,
                store_id=store_id,
                sale_id=sale_id,
                qty=Decimal("10"),
            ),
            balance=balance,
        )
        assert float(reserved_balance.reserved_qty) == 10.0

        released_balance, release_outbox = plan_release_reservation(
            reservation=reservation,
            balance=reserved_balance,
            correlation_id="corr-release-001",
        )
        assert float(released_balance.reserved_qty) == 0.0
        assert float(released_balance.qty) == 50.0
        assert len(release_outbox) == 1
        assert release_outbox[0].event.event_type == "inventory.stock_released"

    def test_tenant_tier_limits(self):
        from app.tenancy.schemas import TenantCreateCommand
        from app.tenancy.service import plan_tenant_creation

        command = TenantCreateCommand(
            business_name="Test Store",
            business_email="test@store.com",
            owner_name="Test Owner",
            owner_email="owner@store.com",
            owner_phone="+2348000000000",
            owner_password_hash="hashed_abc123",
            tier="starter",
        )
        result, tenant, user_model, outbox = plan_tenant_creation(command, slug="test-store")
        assert result.tier == "starter"
        assert result.status == "active"
        assert len(outbox) == 2
        assert outbox[0].event.event_type == "tenancy.tenant_created"

    def test_event_envelope_consistency(self):
        from app.common.events import EventEnvelope
        from app.common.events.names import SALE_CREATED, PAYMENT_SUCCEEDED, CART_CHECKED_OUT

        sale_event = EventEnvelope(
            event_type=SALE_CREATED,
            tenant_id=uuid4(),
            payload={"sale_id": str(uuid4()), "total": "1000"},
        )
        assert sale_event.routing_key() == SALE_CREATED
        assert sale_event.event_version == 1
        assert sale_event.occurred_at is not None

        payment_event = EventEnvelope(
            event_type=PAYMENT_SUCCEEDED,
            tenant_id=sale_event.tenant_id,
            payload={"payment_id": str(uuid4()), "amount": "1000"},
            correlation_id=str(sale_event.event_id),
        )
        assert payment_event.correlation_id == str(sale_event.event_id)

        cart_event = EventEnvelope(
            event_type=CART_CHECKED_OUT,
            tenant_id=sale_event.tenant_id,
            payload={"cart_id": str(uuid4()), "total": "1000", "items": []},
        )
        assert cart_event.routing_key() == CART_CHECKED_OUT

    def test_qr_generation(self):
        from app.catalog.qr import (
            build_product_qr_url,
            generate_qr_png,
            generate_qr_base64,
        )

        url = build_product_qr_url(
            base_url="https://api.storeflow.ng",
            store_id="550e8400-0000-0000-0000-000000000001",
            product_id="550e8400-0000-0000-0000-000000000002",
        )
        assert url == "https://api.storeflow.ng/v1/products/550e8400-0000-0000-0000-000000000001/550e8400-0000-0000-0000-000000000002"

        # Trailing slash on base_url is handled
        url2 = build_product_qr_url(
            base_url="https://api.storeflow.ng/",
            store_id="s1",
            product_id="p1",
        )
        assert url2 == "https://api.storeflow.ng/v1/products/s1/p1"

        png_bytes = generate_qr_png(url)
        assert len(png_bytes) > 0
        assert png_bytes[:4] == b"\x89PNG"

        b64 = generate_qr_base64(url)
        assert len(b64) > 0

    def test_money_handling(self):
        from app.common.money import naira_to_kobo, kobo_to_naira, format_naira

        assert naira_to_kobo(Decimal("1000")) == 100000
        assert naira_to_kobo(Decimal("1500.50")) == 150050
        assert kobo_to_naira(100000) == Decimal("1000")
        assert kobo_to_naira(150050) == Decimal("1500.50")
        assert "1,500.50" in format_naira(Decimal("1500.50"))

    def test_outbox_write_serialization(self):
        from app.common.events import EventEnvelope
        from app.common.events.outbox import OutboxWrite

        event = EventEnvelope(
            event_type="test.event",
            tenant_id=uuid4(),
            actor_id=uuid4(),
            correlation_id="corr-001",
            payload={"key": "value", "nested": {"a": 1}},
        )
        write = OutboxWrite(
            event=event,
            aggregate_type="test",
            aggregate_id="test-123",
        )
        model = write.to_model()
        assert model.event_type == "test.event"
        assert model.aggregate_type == "test"
        assert model.aggregate_id == "test-123"
        assert model.payload == {"key": "value", "nested": {"a": 1}}
        assert model.headers["correlation_id"] == "corr-001"
        assert model.headers["event_version"] == 1
        assert model.id == event.event_id
        assert model.tenant_id == event.tenant_id
