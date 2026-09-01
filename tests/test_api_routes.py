from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import app.common.auth_service as auth_service

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import TenantContext, TokenData
from app.core.platform_deps import PlatformContext

ALL_PERMISSIONS = [
    "products:create",
    "products:read",
    "products:update",
    "products:delete",
    "sales:create",
    "sales:read",
    "sales:void",
    "cart:create",
    "cart:read",
    "cart:delete",
    "cart:checkout",
    "inventory:write",
    "inventory:read",
    "inventory:adjust",
    "inventory:transfer",
    "inventory:transfer_request",
    "inventory:transfer_approve",
    "inventory:transfer_fulfill",
    "payments:create",
    "payments:read",
    "accounting:read",
    "accounting:write",
    "accounting:post",
    "reports:read",
    "admin:security",
    "ai:generate",
    "ai:manage",
    "ai:read",
    "documents:create",
    "documents:read",
    "documents:update",
    "sync:read",
    "sync:manage",
    "auth:manage_sessions",
    "auth:manage_totp",
    "auth:login",
    "employees:create",
    "employees:read",
    "employees:update",
    "employees:delete",
    "employees:assign_roles",
    "roles:read",
    "stores:create",
    "stores:read",
    "stores:update",
    "stores:manage_main",
    "categories:create",
    "categories:read",
    "categories:update",
    "categories:delete",
]


def _make_app():
    app = FastAPI()

    from app.accounting import routes as accounting
    from app.identity import routes as admin_security
    from app.ai import routes as ai
    from app.auth import routes as auth
    from app.cart import routes as cart
    from app.documents import routes as documents
    from app.inventory import routes as inventory
    from app.payments import routes as payments
    from app.reporting import routes as reports
    from app.sales import routes as sales
    from app.stores import routes as stores
    from app.stores.routes_sync import sync_router as sync_mod

    for router in (
        accounting.router,
        admin_security.router,
        ai.router,
        auth.router,
        cart.router,
        documents.router,
        inventory.router,
        payments.router,
        reports.router,
        sales.router,
        stores.router,
        sync_mod,
    ):
        app.include_router(router)

    return app


def _make_token_data():
    return TokenData(
        {
            "sub": str(uuid4()),
            "bid": str(uuid4()),
            "role": "admin",
            "perms": ALL_PERMISSIONS,
            "jti": str(uuid4()),
            "exp": 9999999999,
        }
    )


def _make_session():
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.begin.return_value.__aenter__.return_value = session
    session.begin.return_value.__aexit__.return_value = None
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=42),
            mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )
    )
    return session


@pytest.fixture
def app():
    from app.core.dependencies import get_db, get_tenant_context

    application = _make_app()
    token_data = _make_token_data()
    session = _make_session()
    ctx = TenantContext(user=token_data, session=session)

    async def override_tenant():
        yield ctx

    async def override_db():
        yield session

    application.dependency_overrides[get_tenant_context] = override_tenant
    application.dependency_overrides[get_db] = override_db

    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _bridge_kwargs():
    return dict(
        create_tenant=AsyncMock(return_value={"id": str(uuid4()), "slug": "test"}),
        get_tenant_by_id=AsyncMock(return_value={"id": str(uuid4()), "slug": "test"}),
        list_products=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 50}),
        list_categories=AsyncMock(return_value=[]),
        list_stores=AsyncMock(return_value=[]),
        get_store=AsyncMock(return_value={"id": str(uuid4()), "name": "Test Store"}),
        get_store_details=AsyncMock(return_value={"id": str(uuid4()), "name": "Test Store"}),
        get_product_by_id=AsyncMock(return_value={"id": str(uuid4()), "name": "Test"}),
        get_product_qr_download=AsyncMock(return_value=(b"png", "qr_id")),
        get_category=AsyncMock(return_value={"id": str(uuid4()), "name": "Cat"}),
        get_cart=AsyncMock(return_value={"id": str(uuid4()), "items": []}),
        list_assignable_roles=AsyncMock(return_value=[{"name": "cashier"}]),
        list_all_roles=AsyncMock(return_value=[{"name": "admin"}]),
        list_all_permissions=AsyncMock(return_value=[{"code": "p:r"}]),
        get_user_roles=AsyncMock(return_value=[{"name": "cashier"}]),
        assign_role=AsyncMock(),
        remove_role=AsyncMock(return_value={"success": True}),
        create_category=AsyncMock(return_value={"id": str(uuid4())}),
        create_store=AsyncMock(return_value={"id": str(uuid4()), "name": "Store"}),
        update_store=AsyncMock(return_value={"id": str(uuid4()), "name": "Main"}),
        sync_store_products=AsyncMock(return_value={"synced": 5, "skipped": 2}),
        get_store_products=AsyncMock(
            return_value={"items": [], "total": 0, "page": 1, "page_size": 50}
        ),
        add_product_to_store=AsyncMock(
            return_value={"product_id": "pid", "store_id": "sid", "qty": 10}
        ),
        create_product_for_store=AsyncMock(
            return_value={"product_id": "pid", "store_id": "sid", "name": "Widget", "selling_price": 500, "qty": 20}
        ),
        get_store_product=AsyncMock(
            side_effect=lambda tenant_id, store_id, product_id: (
                {"id": product_id, "name": "Widget", "sku": None, "selling_price": 500, "qty": 20, "available": 20, "status": "active", "history": []}
                if product_id == "pid" else None
            )
        ),
        update_store_product=AsyncMock(
            side_effect=lambda tenant_id, store_id, product_id, **fields: (
                {"product_id": product_id, "store_id": store_id, "name": fields.get("name", "Widget"), "selling_price": fields.get("selling_price", 500), "status": "active"}
                if product_id == "pid" else None
            )
        ),
        delete_store_product=AsyncMock(
            side_effect=lambda tenant_id, store_id, product_id: (
                True if product_id == "pid" else (_ for _ in ()).throw(ValueError("product_not_in_store"))
            )
        ),
        set_min_stock_level=AsyncMock(
            return_value={"store_id": "sid", "product_id": "pid", "min_stock_level": 10}
        ),
        adjust_stock=AsyncMock(return_value={"id": str(uuid4())}),
        transfer_stock=AsyncMock(return_value={"id": str(uuid4())}),
        create_product=AsyncMock(return_value={"id": str(uuid4())}),
        create_products=AsyncMock(return_value={"products": [], "errors": []}),
        update_product=AsyncMock(),
        delete_product=AsyncMock(),
        create_sale_via_service=AsyncMock(return_value={"id": str(uuid4())}),
        create_or_resume_cart=AsyncMock(return_value={"id": str(uuid4()), "items": []}),
        remove_cart_item=AsyncMock(),
        checkout_cart=AsyncMock(return_value={"sale_id": str(uuid4())}),
        create_account=AsyncMock(),
        update_category=AsyncMock(),
        delete_category=AsyncMock(),
        create_document=AsyncMock(return_value={"id": str(uuid4()), "doc_number": "DOC-001"}),
        list_documents=AsyncMock(
            return_value={"items": [], "total": 0, "page": 1, "page_size": 50}
        ),
        get_document_by_id=AsyncMock(return_value={"id": str(uuid4()), "doc_number": "DOC-001"}),
        update_document_status=AsyncMock(return_value={"id": str(uuid4()), "status": "sent"}),
        convert_document_to_sale=AsyncMock(return_value={"sale_id": str(uuid4())}),
        record_payment=AsyncMock(
            return_value={
                "payment_id": str(uuid4()),
                "payment_status": "completed",
                "sale_status": "partial",
                "total": 100.0,
                "amount_paid": 50.0,
                "balance": 50.0,
            }
        ),
        record_split_payment=AsyncMock(
            return_value={
                "payments": [{"payment_id": str(uuid4()), "status": "completed"}],
                "sale_status": "partial",
                "total": 100.0,
                "amount_paid": 50.0,
                "balance": 50.0,
            }
        ),
        suggest_even_split=AsyncMock(
            return_value={
                "sale_id": str(uuid4()),
                "total": 9000.0,
                "splits": {"card": 3000.0, "transfer": 3000.0, "cash": 3000.0},
            }
        ),
        process_split_payment=AsyncMock(
            return_value={
                "sale_id": str(uuid4()),
                "total": 9000.0,
                "cash": {"amount": 3000.0, "status": "completed"},
                "card": {
                    "amount": 3000.0,
                    "payment_url": "https://checkout.flutterwave.com/...",
                    "qr_code_base64": "abc",
                    "tx_ref": "SF-XXX",
                    "status": "pending",
                },
                "transfer": {
                    "amount": 3000.0,
                    "payment_url": "https://checkout.flutterwave.com/...",
                    "tx_ref": "SF-TRF-XXX",
                    "status": "pending",
                },
                "sale_status": "partial",
                "amount_paid": 3000.0,
                "balance": 6000.0,
            }
        ),
        confirm_split_card_payment=AsyncMock(),
        confirm_split_transfer_payment=AsyncMock(),
        initiate_card_payment=AsyncMock(
            return_value={
                "sale_id": str(uuid4()),
                "payment_url": "https://checkout.flutterwave.com/...",
                "qr_code_base64": "abc123",
                "tx_ref": "SF-XXX",
                "amount": 5000.0,
                "status": "pending",
            }
        ),
        initiate_transfer_payment=AsyncMock(
            return_value={
                "sale_id": str(uuid4()),
                "account_number": "9755152912",
                "bank_name": "Flutterwave MFB",
                "amount": 5000.0,
                "tx_ref": "SF-TRF-XXX",
                "transfer_reference": "REF123",
                "account_expiration": "2026-08-26 12:00:00",
                "transfer_note": "Payment for sale",
                "instructions": "Transfer exactly NGN 5,000.00 to Flutterwave MFB account 9755152912.",
                "status": "pending",
            }
        ),
    )


def _services_kwargs():
    return dict(
        list_products=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 50}),
        list_sales=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 50}),
        list_journals=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 50}),
        inventory_history=AsyncMock(
            return_value={"items": [], "total": 0, "page": 1, "page_size": 50}
        ),
        create_product=AsyncMock(return_value=str(uuid4())),
        low_stock_report=AsyncMock(return_value=[]),
        get_product=AsyncMock(
            return_value={"id": str(uuid4()), "name": "Test", "selling_price": 10}
        ),
        update_product=AsyncMock(),
        transfer_stock=AsyncMock(),
        queue_server_event=AsyncMock(),
        post_journal=AsyncMock(return_value=str(uuid4())),
        trial_balance=AsyncMock(return_value={}),
        profit_and_loss=AsyncMock(return_value={}),
        get_sale=AsyncMock(return_value={"id": str(uuid4()), "total": 100}),
        void_sale=AsyncMock(return_value=True),
        record_payment=AsyncMock(return_value={"id": str(uuid4())}),
        split_payment=AsyncMock(return_value={"id": str(uuid4())}),
        process_sync_batch=AsyncMock(return_value={"processed": 1}),
        get_pending_events=AsyncMock(return_value=[]),
        get_pending_event_count=AsyncMock(return_value=0),
        call=AsyncMock(return_value=[]),
    )


def _analytics_kwargs():
    return dict(
        dashboard_summary=AsyncMock(return_value={}),
        sales_summary=AsyncMock(return_value={}),
        top_products=AsyncMock(return_value=[]),
        payment_breakdown=AsyncMock(return_value={}),
        cashier_performance=AsyncMock(return_value=[]),
        inventory_alerts=AsyncMock(return_value={"summary": {}, "items": []}),
        profit_loss=AsyncMock(return_value={"items": [], "totals": {}}),
        customer_insights=AsyncMock(return_value={"summary": {}, "top_customers": []}),
        document_summary=AsyncMock(return_value={"summary": {}, "aging": []}),
    )


@pytest.fixture(autouse=True)
def patch_services():
    mock_sdb = MagicMock()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        title="test", conversation_id=uuid4(), role="user",
        content="hi", tool_calls=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    mock_result.scalars.return_value.all.return_value = []
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result
    mock_sdb.session.return_value = mock_session

    with (
        patch.multiple("app.common.bridge", **_bridge_kwargs()),
        patch.multiple("app.common.services", **_services_kwargs()),
        patch("app.core.redis_client.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.core.redis_client.cache_set", new_callable=AsyncMock),
        patch("app.common.cache.cache.get", new_callable=AsyncMock, return_value=None),
        patch("app.common.cache.cache.set", new_callable=AsyncMock),
        patch("app.common.cache.cache.delete", new_callable=AsyncMock),
        patch("app.common.cache.cache.delete_pattern", new_callable=AsyncMock),
        patch("app.ai.routes._ai_db", return_value=mock_sdb),
        patch.multiple(
            "app.common.flutterwave_service",
            create_payment_link=AsyncMock(
                return_value={"link": "https://checkout.flutterwave.com/...", "tx_ref": "SF-XXX"}
            ),
            verify_transaction=AsyncMock(return_value={"status": "successful"}),
            initiate_transfer=AsyncMock(
                return_value={"transfer_id": 123, "reference": "SF-TRF-XXX", "status": "NEW"}
            ),
            verify_transfer=AsyncMock(return_value={"status": "successful"}),
            initiate_bank_transfer_charge=AsyncMock(
                return_value={
                    "tx_ref": "SF-TRF-XXX",
                    "transfer_reference": "REF123",
                    "account_number": "9755152912",
                    "bank_name": "Flutterwave MFB",
                    "account_expiration": "2026-08-26 12:00:00",
                    "transfer_note": "Payment for sale",
                    "amount": 5000,
                }
            ),
            create_subaccount=AsyncMock(
                return_value={"subaccount_id": "RS_XXX", "account_number": "1234567890"}
            ),
            list_banks=AsyncMock(return_value=[{"name": "Bank", "code": "001"}]),
            resolve_account=AsyncMock(return_value={"account_number": "1234567890"}),
            verify_webhook_signature=MagicMock(return_value=True),
        ),
        patch.multiple("app.common.analytics", **_analytics_kwargs()),
        patch.multiple(
            "app.common.auth_service",
            login=AsyncMock(
                return_value=(
                    {"access_token": "tok", "refresh_token": "rtok"},
                    {"id": "u1", "email": "a@a.com"},
                )
            ),
            refresh_tokens=AsyncMock(return_value={"access_token": "tok", "refresh_token": "rtok"}),
            logout=AsyncMock(),
            forgot_password=AsyncMock(),
            complete_reset=AsyncMock(return_value=True),
            change_password=AsyncMock(),
            setup_totp=AsyncMock(
                return_value={"secret": "xxx", "qr_code": "data:image/png;base64,qr"}
            ),
            enable_totp=AsyncMock(),
            disable_totp=AsyncMock(),
            create_employee=AsyncMock(
                return_value={"id": "emp1", "email": "e@e.com", "password": "tmp"}
            ),
            update_employee=AsyncMock(
                return_value={"user_id": "emp1", "email": "e@e.com", "full_name": "Emp"}
            ),
            delete_employee=AsyncMock(return_value={"deleted": True}),
            set_employee_status=AsyncMock(return_value={"user_id": "emp1", "status": "suspended"}),
            TotpRequired=auth_service.TotpRequired,
            AuthError=auth_service.AuthError,
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


class TestSales:
    ROUTE = "/sales"

    async def test_create_sale(self, client):
        resp = await client.post(
            self.ROUTE,
            json={
                "store_id": "00000000-0000-0000-0000-000000000001",
                "items": [
                    {
                        "product_id": "00000000-0000-0000-0000-000000000002",
                        "product_name": "Widget",
                        "qty": 1,
                        "unit_price": 10,
                    }
                ],
            },
        )
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_sales(self, client):
        resp = await client.get(self.ROUTE)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body

    async def test_get_sale(self, client):
        resp = await client.get(f"{self.ROUTE}/x")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_void_sale(self, client):
        resp = await client.post(f"{self.ROUTE}/x/void", json={"reason": "test"})
        assert resp.status_code == 200
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------


class TestCart:
    ROUTE = "/cart"

    async def test_create_cart(self, client):
        resp = await client.post(self.ROUTE, json={"store_id": str(uuid4())})
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_remove_item(self, client):
        resp = await client.delete(f"{self.ROUTE}/items/x")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": {"success": True}}

    async def test_get_cart(self, client):
        resp = await client.get(f"{self.ROUTE}/x")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_checkout_cart(self, client):
        resp = await client.post(f"{self.ROUTE}/x/checkout", json={})
        assert resp.status_code == 200
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class TestStores:
    STORES = "/stores"

    async def test_create_store(self, client):
        resp = await client.post(self.STORES, json={"name": "Main"})
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_stores(self, client):
        resp = await client.get(self.STORES)
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_store(self, client):
        resp = await client.get(f"{self.STORES}/sid")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_store_404(self, client):
        from app.common import bridge

        bridge.get_store_details.return_value = {}
        resp = await client.get(f"{self.STORES}/missing")
        assert resp.status_code == 404

    async def test_update_store(self, client):
        resp = await client.patch(
            f"{self.STORES}/00000000-0000-0000-0000-000000000001", json={"name": "Main"}
        )
        assert resp.status_code == 200
        assert "data" in resp.json()


class TestInventory:
    CATEGORIES = "/inventory/00000000-0000-0000-0000-000000000001/categories"

    async def test_create_category(self, client):
        resp = await client.post(self.CATEGORIES, json={"name": "Cat"})
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_categories(self, client):
        resp = await client.get(self.CATEGORIES)
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_category(self, client):
        resp = await client.get(f"{self.CATEGORIES}/x")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_category_404(self, client):
        from app.common import bridge

        bridge.get_category.return_value = None
        resp = await client.get(f"{self.CATEGORIES}/missing")
        assert resp.status_code == 404

    async def test_update_category(self, client):
        resp = await client.patch(f"{self.CATEGORIES}/x", json={"name": "N"})
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": {"success": True}}

    async def test_delete_category(self, client):
        resp = await client.delete(f"{self.CATEGORIES}/x")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": {"success": True}}

    async def test_download_qr(self, client):
        resp = await client.get("/inventory/00000000-0000-0000-0000-000000000001/products/x/qr")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    async def test_download_qr_404(self, client):
        from app.common import bridge

        bridge.get_product_qr_download.return_value = None
        resp = await client.get("/inventory/00000000-0000-0000-0000-000000000001/products/missing/qr")
        assert resp.status_code == 404

    async def test_adjust_stock(self, client):
        resp = await client.post(
            "/inventory/00000000-0000-0000-0000-000000000001/adjust",
            json={
                "product_id": "00000000-0000-0000-0000-000000000001",
                "reason": "count",
                "qty_change": 5,
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_store_products(self, client):
        resp = await client.get("/inventory/00000000-0000-0000-0000-000000000001/products")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body

    async def test_add_store_product(self, client):
        resp = await client.post(
            "/inventory/00000000-0000-0000-0000-000000000001/products",
            json={"product_id": "00000000-0000-0000-0000-000000000001", "qty": 10},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        assert body["data"]["product_id"] == "pid"

    async def test_create_product_for_store(self, client):
        resp = await client.post(
            "/inventory/00000000-0000-0000-0000-000000000001/products/create",
            json={"name": "Widget", "selling_price": 500, "qty": 20},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["name"] == "Widget"
        assert body["data"]["selling_price"] == 500

    async def test_get_store_product(self, client):
        resp = await client.get("/inventory/00000000-0000-0000-0000-000000000001/products/pid")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["id"] == "pid"

    async def test_get_store_product_not_found(self, client):
        resp = await client.get(
            "/inventory/00000000-0000-0000-0000-000000000001/products/00000000-0000-0000-0000-999999999999"
        )
        assert resp.status_code == 404

    async def test_update_store_product(self, client):
        resp = await client.patch(
            "/inventory/00000000-0000-0000-0000-000000000001/products/pid",
            json={"selling_price": 750, "name": "Updated Widget"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["product_id"] == "pid"

    async def test_update_store_product_not_found(self, client):
        resp = await client.patch(
            "/inventory/00000000-0000-0000-0000-000000000001/products/00000000-0000-0000-0000-999999999999",
            json={"selling_price": 100},
        )
        assert resp.status_code == 404

    async def test_delete_store_product(self, client):
        resp = await client.delete("/inventory/00000000-0000-0000-0000-000000000001/products/pid")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["success"] is True

    async def test_delete_store_product_not_found(self, client):
        resp = await client.delete(
            "/inventory/00000000-0000-0000-0000-000000000001/products/00000000-0000-0000-0000-999999999999"
        )
        assert resp.status_code == 404

    async def test_sync_store_products(self, client):
        resp = await client.post("/inventory/00000000-0000-0000-0000-000000000001/sync")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "synced" in body["data"]

    async def test_set_min_stock_level(self, client):
        resp = await client.patch(
            "/inventory/00000000-0000-0000-0000-000000000001/min-level/pid",
            json={"min_stock_level": 10},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_distribute_stock(self, client):
        resp = await client.post(
            "/inventory/00000000-0000-0000-0000-000000000001/distribute",
            json={
                "product_id": "00000000-0000-0000-0000-000000000002",
                "to_store_id": "00000000-0000-0000-0000-000000000003",
                "qty": 5,
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_inventory_history(self, client):
        resp = await client.get(
            "/inventory/00000000-0000-0000-0000-000000000001/history"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class TestPayments:
    ROUTE = "/payments"

    async def test_cash(self, client):
        resp = await client.post(
            f"{self.ROUTE}/cash",
            json={
                "sale_id": "00000000-0000-0000-0000-000000000001",
                "method": "cash",
                "amount": 100,
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_card_payment(self, client):
        resp = await client.post(
            f"{self.ROUTE}/card",
            json={
                "sale_id": "00000000-0000-0000-0000-000000000001",
                "amount": 5000,
                "customer_email": "c@c.com",
                "customer_name": "Test Customer",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "payment_url" in data["data"]
        assert "qr_code_base64" in data["data"]
        assert "tx_ref" in data["data"]
        assert data["data"]["amount"] == 5000.0
        assert data["data"]["status"] == "pending"

    async def test_transfer_payment(self, client):
        resp = await client.post(
            f"{self.ROUTE}/transfer",
            json={
                "sale_id": "00000000-0000-0000-0000-000000000001",
                "amount": 5000,
                "customer_email": "c@c.com",
                "customer_name": "Test Customer",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "account_number" in data["data"]
        assert "bank_name" in data["data"]
        assert "instructions" in data["data"]
        assert data["data"]["amount"] == 5000.0
        assert data["data"]["status"] == "pending"

    async def test_suggest_split(self, client):
        resp = await client.get(f"{self.ROUTE}/split/suggest/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "card" in data["data"]
        assert "cash" in data["data"]
        assert "transfer" in data["data"]

    async def test_split_payment(self, client):
        resp = await client.post(
            f"{self.ROUTE}/split",
            json={
                "sale_id": "00000000-0000-0000-0000-000000000001",
                "splits": {"cash": 3000, "card": 3000, "transfer": 3000},
                "customer_email": "c@c.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], dict)
        assert "sale_status" in data["data"]
        assert "balance" in data["data"]

    async def test_list_banks(self, client):
        resp = await client.get(f"{self.ROUTE}/setup/banks")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"] == [{"name": "Bank", "code": "001"}]

    async def test_resolve_account(self, client):
        resp = await client.post(
            f"{self.ROUTE}/setup/verify-account",
            json={"account_number": "1234567890", "bank_code": "001"},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


class TestAccounting:
    ROUTE = "/accounting"

    async def test_list_accounts(self, client):
        resp = await client.get(f"{self.ROUTE}/accounts")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}

    async def test_create_account(self, client):
        resp = await client.post(
            f"{self.ROUTE}/accounts",
            json={
                "code": "100",
                "name": "Cash",
                "account_type": "asset",
            },
        )
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_create_journal(self, client):
        resp = await client.post(
            f"{self.ROUTE}/journals",
            json={
                "description": "test",
                "entries": [
                    {"account_id": "00000000-0000-0000-0000-000000000001", "account_code": "100", "debit": 100, "credit": 0},
                    {"account_id": "00000000-0000-0000-0000-000000000002", "account_code": "200", "debit": 0, "credit": 100},
                ],
            },
        )
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_journals(self, client):
        resp = await client.get(f"{self.ROUTE}/journals")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body

    async def test_trial_balance(self, client):
        resp = await client.get(f"{self.ROUTE}/trial-balance")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_profit_and_loss(self, client):
        resp = await client.get(
            f"{self.ROUTE}/profit-and-loss?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_commission(self, client):
        resp = await client.get(f"{self.ROUTE}/commission")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": {}}

    async def test_record_commission(self, client):
        resp = await client.post(f"{self.ROUTE}/commission/x/record")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": {"success": True}}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class TestReports:
    ROUTE = "/reports"

    async def test_dashboard(self, client):
        resp = await client.get(f"{self.ROUTE}/dashboard")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_sales_summary(self, client):
        resp = await client.get(
            f"{self.ROUTE}/sales-summary?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_top_products(self, client):
        resp = await client.get(
            f"{self.ROUTE}/top-products?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_payment_methods(self, client):
        resp = await client.get(
            f"{self.ROUTE}/payment-methods?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_cashier_performance(self, client):
        resp = await client.get(
            f"{self.ROUTE}/cashier-performance?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_inventory_alerts(self, client):
        resp = await client.get(f"{self.ROUTE}/inventory-alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "summary" in data["data"]
        assert "items" in data["data"]

    async def test_profit_loss(self, client):
        resp = await client.get(f"{self.ROUTE}/profit-loss?from_date=2024-01-01&to_date=2024-12-31")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "items" in data["data"]
        assert "totals" in data["data"]

    async def test_customer_insights(self, client):
        resp = await client.get(
            f"{self.ROUTE}/customer-insights?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "total_customers" in data["data"]
        assert "top_customers" in data["data"]

    async def test_document_summary(self, client):
        resp = await client.get(
            f"{self.ROUTE}/document-summary?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "total_invoices" in data["data"]


# ---------------------------------------------------------------------------
# Admin Security
# ---------------------------------------------------------------------------


class TestAdminSecurity:
    ROUTE = "/security"

    async def test_audit_stream(self, client):
        resp = await client.get(f"{self.ROUTE}/audit-stream")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}

    async def test_list_bans(self, client):
        resp = await client.get(f"{self.ROUTE}/bans")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}

    async def test_create_ban(self, client):
        resp = await client.post(f"{self.ROUTE}/bans", json={"ip": "1.2.3.4"})
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_delete_ban(self, client):
        resp = await client.delete(f"{self.ROUTE}/bans/1.2.3.4")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_ban(self, client):
        resp = await client.get(f"{self.ROUTE}/bans/1.2.3.4")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_rate_limits(self, client):
        resp = await client.get(f"{self.ROUTE}/rate-limits/1.2.3.4")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_reset_rate_limits(self, client):
        resp = await client.post(f"{self.ROUTE}/rate-limits/1.2.3.4/reset")
        assert resp.status_code == 200
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


class TestAI:
    ROUTE = "/ai"

    async def test_generate_ui(self, client):
        resp = await client.post(
            f"{self.ROUTE}/chat",
            json={
                "message": "button",
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class TestDocuments:
    ROUTE = "/documents"

    async def test_create_document(self, client):
        resp = await client.post(
            self.ROUTE,
            json={
                "doc_type": "invoice",
                "items": [{"description": "Item", "qty": 1, "unit_price": 10}],
            },
        )
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_documents(self, client):
        resp = await client.get(self.ROUTE)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body

    async def test_get_document(self, client):
        resp = await client.get(f"{self.ROUTE}/x")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_update_document_status(self, client):
        resp = await client.patch(f"{self.ROUTE}/x/status", json={"status": "sent"})
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_convert_to_sale(self, client):
        resp = await client.post(f"{self.ROUTE}/x/convert-to-sale")
        assert resp.status_code == 201
        assert "data" in resp.json()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    ROUTE = "/auth"

    async def test_register(self, client):
        resp = await client.post(
            f"{self.ROUTE}/register",
            json={
                "business_name": "Biz",
                "business_email": "b@b.com",
                "owner_name": "Owner",
                "owner_email": "o@o.com",
                "password": "Str0ng!Pass",
                "confirm_password": "Str0ng!Pass",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        assert "message" in body

    async def test_login(self, client):
        resp = await client.post(
            f"{self.ROUTE}/login",
            json={
                "email": "a@a.com",
                "password": "p",
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_refresh(self, client):
        resp = await client.post(f"{self.ROUTE}/refresh", json={"refresh_token": "tok"})
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_logout(self, client):
        resp = await client.post(f"{self.ROUTE}/logout", json={"refresh_token": "tok"})
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Logged out"}

    async def test_me(self, client):
        resp = await client.get(f"{self.ROUTE}/me")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_verify_email(self, client):
        resp = await client.post(f"{self.ROUTE}/verify-email", json={"token": "x"})
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Email verified"}

    async def test_resend_verify(self, client):
        resp = await client.post(f"{self.ROUTE}/resend-verify", json={"email": "a@a.com"})
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Verification email queued"}

    async def test_forgot_password(self, client):
        resp = await client.post(f"{self.ROUTE}/forgot-password", json={"email": "a@a.com"})
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Password reset email queued"}

    async def test_reset_password(self, client):
        resp = await client.post(
            f"{self.ROUTE}/reset-password",
            json={
                "token": "x",
                "new_password": "Str0ng!New",
                "confirm_password": "Str0ng!New",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "message" in body

    async def test_change_password(self, client):
        resp = await client.post(
            f"{self.ROUTE}/change-password",
            json={
                "current_password": "old",
                "new_password": "Str0ng!New",
                "confirm_password": "Str0ng!New",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Password changed"}

    async def test_sessions(self, client):
        resp = await client.get(f"{self.ROUTE}/sessions")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}

    async def test_revoke_session(self, client):
        resp = await client.delete(f"{self.ROUTE}/sessions/x")
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Session revoked"}

    async def test_totp_setup(self, client):
        resp = await client.post(f"{self.ROUTE}/totp/setup")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_totp_verify(self, client):
        resp = await client.post(f"{self.ROUTE}/totp/verify", json={"code": "123456"})
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "TOTP enabled"}

    async def test_totp_disable(self, client):
        resp = await client.post(
            f"{self.ROUTE}/totp/disable", json={"password": "p", "code": "123456"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "TOTP disabled"}

    async def test_create_employee(self, client):
        resp = await client.post(
            f"{self.ROUTE}/employees",
            json={
                "email": "e@e.com",
                "full_name": "Emp",
                "role": "cashier",
            },
        )
        assert resp.status_code == 201
        assert "data" in resp.json()

    async def test_list_employees(self, client):
        resp = await client.get(f"{self.ROUTE}/employees")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_update_role(self, client):
        resp = await client.patch(
            f"{self.ROUTE}/employees/role",
            json={
                "user_id": "00000000-0000-0000-0000-000000000001",
                "new_role": "manager",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"data": None, "message": "Role assigned"}

    async def test_roles(self, client):
        resp = await client.get(f"{self.ROUTE}/roles")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_all_roles(self, client):
        resp = await client.get(f"{self.ROUTE}/roles/all")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_permissions(self, client):
        resp = await client.get(f"{self.ROUTE}/permissions")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_employee_roles(self, client):
        resp = await client.get(f"{self.ROUTE}/employees/x/roles")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_assign_employee_role(self, client):
        resp = await client.post(
            f"{self.ROUTE}/employees/x/roles",
            json={
                "user_id": "00000000-0000-0000-0000-000000000001",
                "new_role": "cashier",
            },
        )
        assert resp.status_code == 201
        assert resp.json() == {"data": None, "message": "Role assigned"}

    async def test_remove_employee_role(self, client):
        resp = await client.delete(f"{self.ROUTE}/employees/x/roles/cashier")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_update_employee(self, client):
        resp = await client.patch(
            f"{self.ROUTE}/employees/00000000-0000-0000-0000-000000000001",
            json={"full_name": "Updated Name", "phone": "1234567890"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Employee updated"

    async def test_delete_employee(self, client):
        resp = await client.delete(
            f"{self.ROUTE}/employees/00000000-0000-0000-0000-000000000001",
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Employee deleted"

    async def test_suspend_employee(self, client):
        resp = await client.patch(
            f"{self.ROUTE}/employees/00000000-0000-0000-0000-000000000001/status",
            json={"status": "suspended"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Employee suspended"

    async def test_activate_employee(self, client):
        resp = await client.patch(
            f"{self.ROUTE}/employees/00000000-0000-0000-0000-000000000001/status",
            json={"status": "active"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Employee active"

    async def test_suspend_employee_invalid_status(self, client):
        resp = await client.patch(
            f"{self.ROUTE}/employees/00000000-0000-0000-0000-000000000001/status",
            json={"status": "invalid"},
        )
        assert resp.status_code == 422

    async def test_audit(self, client):
        resp = await client.get(f"{self.ROUTE}/audit")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class TestSync:
    async def test_sync_events(self, client):
        resp = await client.post(
            "/sync/events",
            json={
                "events": [{"event_type": "test", "payload": {"x": 1}}],
            },
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_pending_events(self, client):
        resp = await client.get("/sync/pending")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_pending_events_invalid_date(self, client):
        resp = await client.get("/sync/pending?since=invalid")
        assert resp.status_code == 400

    async def test_trigger_sync(self, client):
        resp = await client.post("/sync/trigger")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_business_settings(self, client):
        resp = await client.get("/business/settings")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_update_business_settings(self, client):
        resp = await client.patch("/business/settings", json={"name": "N"})
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_business_permissions(self, client):
        resp = await client.get("/business/permissions")
        assert resp.status_code == 200
        assert resp.json() == {"message": "ok", "data": []}

    async def test_update_business_permissions(self, client):
        resp = await client.patch("/business/permissions", json={"perm": True})
        assert resp.status_code == 200
        assert "data" in resp.json()


# --- Platform Commission Tests ---


def _make_platform_app():
    app = FastAPI()
    from app.platform import routes as platform_router

    app.include_router(platform_router.router)
    return app


def _make_platform_token_data():
    from app.core.platform_deps import PlatformUserData

    return PlatformUserData(
        {
            "sub": str(uuid4()),
            "bid": "platform",
            "role": "admin",
            "perms": ["platform:*"],
            "jti": str(uuid4()),
            "exp": 9999999999,
            "type": "access",
        }
    )


def _make_platform_session():
    from datetime import UTC, datetime

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.begin.return_value.__aenter__.return_value = session
    session.begin.return_value.__aexit__.return_value = None

    original_add = session.add

    def mock_add(obj):
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = datetime.now(UTC)
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = datetime.now(UTC)
        if hasattr(obj, "id") and obj.id is None:
            from uuid import uuid4

            obj.id = uuid4()

    session.add = mock_add
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=42),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            all=MagicMock(return_value=[]),
        )
    )
    return session


@pytest.fixture
def platform_app():
    from app.core.platform_deps import get_platform_context

    application = _make_platform_app()
    token_data = _make_platform_token_data()
    session = _make_platform_session()
    ctx = PlatformContext(user=token_data, session=session)

    async def override_platform():
        yield ctx

    async def override_session():
        yield session

    application.dependency_overrides[get_platform_context] = override_platform

    return application


@pytest.fixture
async def platform_client(platform_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    from app.platform.audit import log_platform_audit

    with patch("app.platform.audit.log_platform_audit", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=platform_app), base_url="http://test"
        ) as ac:
            yield ac


class TestPlatformCommissions:
    ROUTE = "/platform/commissions"

    async def test_create_commission(self, platform_client):
        resp = await platform_client.post(
            self.ROUTE,
            json={
                "label": "transaction_greater_than_5000",
                "fee_type": "percentage",
                "amount": 1.8,
                "min_threshold": 5000.01,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "data" in data
        assert data["data"]["label"] == "transaction_greater_than_5000"
        assert data["data"]["fee_type"] == "percentage"
        assert data["data"]["amount"] == 1.8
        assert data["data"]["min_threshold"] == 5000.01
        assert "id" in data["data"]
        assert "created_at" in data["data"]

    async def test_create_commission_flat(self, platform_client):
        resp = await platform_client.post(
            self.ROUTE,
            json={
                "label": "transaction_less_or_equal_5000",
                "fee_type": "flat",
                "amount": 100,
                "min_threshold": 0,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["data"]["fee_type"] == "flat"
        assert data["data"]["amount"] == 100
        assert data["data"]["min_threshold"] == 0

    async def test_create_commission_invalid_fee_type(self, platform_client):
        resp = await platform_client.post(
            self.ROUTE,
            json={"label": "test", "fee_type": "invalid", "amount": 100},
        )
        assert resp.status_code == 400

    async def test_list_commissions(self, platform_client):
        resp = await platform_client.get(self.ROUTE)
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)
        assert "total" in data

    async def test_get_commission_not_found(self, platform_client):
        resp = await platform_client.get(f"{self.ROUTE}/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_update_commission_not_found(self, platform_client):
        resp = await platform_client.patch(
            f"{self.ROUTE}/00000000-0000-0000-0000-000000000000",
            json={"amount": 2000},
        )
        assert resp.status_code == 404

    async def test_delete_commission_not_found(self, platform_client):
        resp = await platform_client.delete(f"{self.ROUTE}/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_update_commission_invalid_fee_type(self, platform_client):
        resp = await platform_client.patch(
            f"{self.ROUTE}/00000000-0000-0000-0000-000000000000",
            json={"fee_type": "bad_type"},
        )
        assert resp.status_code in (400, 404)
