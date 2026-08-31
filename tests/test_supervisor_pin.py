"""Tests for supervisor PIN override system.

Covers:
- plan_remove_item with approved_by (planning layer)
- POST /auth/pin — set supervisor PIN
- POST /auth/users/{user_id}/pin — force-regenerate
- POST /cart/items/{id}/void — void with supervisor PIN
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import TenantContext, TokenData
from app.cart.models import CartItem
from app.cart.schemas import RemoveItemCommand
from app.cart.service import plan_remove_item
from app.common.events.names import CART_ITEM_REMOVED


# ── Helpers ────────────────────────────────────────────────────────────────────

ALL_PERMISSIONS = [
    "products:create", "products:read", "products:update", "products:delete",
    "sales:create", "sales:read", "sales:void",
    "cart:create", "cart:read", "cart:delete", "cart:checkout",
    "inventory:write", "inventory:read", "inventory:adjust", "inventory:transfer",
    "payments:create", "payments:read",
    "accounting:read", "accounting:write", "accounting:post",
    "reports:read",
    "auth:manage_sessions", "auth:manage_totp", "auth:login",
    "employees:create", "employees:read", "employees:update", "employees:assign_roles",
    "roles:read",
    "stores:create", "stores:read", "stores:update",
    "categories:create", "categories:read", "categories:update", "categories:delete",
    "documents:create", "documents:read", "documents:update",
    "sync:read", "sync:manage",
]


def _make_token_data(perms=None):
    return TokenData({
        "sub": str(uuid4()),
        "bid": str(uuid4()),
        "role": "admin",
        "perms": perms or ALL_PERMISSIONS,
        "jti": str(uuid4()),
        "exp": 9999999999,
    })


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
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )
    )
    return session


def _make_app():
    from app.auth import routes as auth
    from app.cart import routes as cart

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(cart.router)
    return app


def _make_identity_session(supervisor_pins=None, has_permission=True):
    """Build a mock identity session with configurable PIN lookup results."""
    identity_session = AsyncMock()
    identity_session.__aenter__.return_value = identity_session
    identity_session.__aexit__.return_value = None

    supervisor_pins = supervisor_pins or []
    perm_result = MagicMock()
    perm_result.scalar_one_or_none.return_value = uuid4() if has_permission else None

    call_count = 0

    async def execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: supervisor_pins query
            return MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=supervisor_pins))
                ),
            )
        # Second call: permission check
        return perm_result

    identity_session.execute = execute
    return identity_session


def _make_cart_session(cart_item=None):
    """Build a mock cart session."""
    cart_session = AsyncMock()
    cart_session.__aenter__.return_value = cart_session
    cart_session.__aexit__.return_value = None
    return cart_session


# ── Planning Layer Tests ───────────────────────────────────────────────────────


class TestPlanRemoveItemApprovedBy:
    """Unit tests for plan_remove_item with approved_by (supervisor override)."""

    def test_plan_remove_item_with_approved_by(self):
        """Event payload should include approved_by when set."""
        item = CartItem(
            id=uuid4(), cart_id=uuid4(), product_id=uuid4(),
            product_public_id="prd_abc123", name="Bottle Water",
            unit_price=250.0, qty=1.0,
        )
        supervisor_id = uuid4()
        command = RemoveItemCommand(
            cart_id=uuid4(), tenant_id=uuid4(), item_id=item.id,
            created_by=uuid4(), approved_by=supervisor_id,
        )
        outbox = plan_remove_item(command, item)
        assert outbox[0].event.event_type == CART_ITEM_REMOVED
        assert outbox[0].event.payload["approved_by"] == str(supervisor_id)

    def test_plan_remove_item_without_approved_by(self):
        """Event payload should not contain approved_by for direct deletion."""
        item = CartItem(
            id=uuid4(), cart_id=uuid4(), product_id=uuid4(),
            product_public_id="prd_abc123", name="Bottle Water",
            unit_price=250.0, qty=1.0,
        )
        command = RemoveItemCommand(
            cart_id=uuid4(), tenant_id=uuid4(), item_id=item.id,
            created_by=uuid4(),
        )
        outbox = plan_remove_item(command, item)
        assert outbox[0].event.event_type == CART_ITEM_REMOVED
        assert "approved_by" not in outbox[0].event.payload

    def test_plan_remove_item_event_metadata(self):
        """Event should record actor_id from created_by."""
        item = CartItem(
            id=uuid4(), cart_id=uuid4(), product_id=uuid4(),
            product_public_id="prd_test", name="Test",
            unit_price=100.0, qty=2.0,
        )
        actor = uuid4()
        command = RemoveItemCommand(
            cart_id=uuid4(), tenant_id=uuid4(), item_id=item.id,
            created_by=actor, approved_by=uuid4(),
        )
        outbox = plan_remove_item(command, item)
        assert outbox[0].event.actor_id == actor


# ── API Layer Tests ────────────────────────────────────────────────────────────


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


class TestSetSupervisorPin:
    """POST /auth/pin — set own supervisor PIN."""

    async def test_set_pin_success(self, client):
        """Valid 4-digit PIN is accepted and stored."""
        resp = await client.post("/auth/pin", json={"pin": "1234"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Supervisor PIN set"

    async def test_set_pin_6_digits(self, client):
        """6-digit PIN is also valid."""
        resp = await client.post("/auth/pin", json={"pin": "123456"})
        assert resp.status_code == 200

    async def test_set_pin_too_short(self, client):
        """3-digit PIN is rejected."""
        resp = await client.post("/auth/pin", json={"pin": "123"})
        assert resp.status_code == 400
        assert "4-6 digits" in resp.json()["detail"]

    async def test_set_pin_too_long(self, client):
        """7-digit PIN is rejected."""
        resp = await client.post("/auth/pin", json={"pin": "1234567"})
        assert resp.status_code == 400
        assert "4-6 digits" in resp.json()["detail"]

    async def test_set_pin_letters_rejected(self, client):
        """Non-numeric PIN is rejected."""
        resp = await client.post("/auth/pin", json={"pin": "abcd"})
        assert resp.status_code == 400

    async def test_set_pin_mixed_rejected(self, client):
        """Mixed alphanumeric PIN is rejected."""
        resp = await client.post("/auth/pin", json={"pin": "12ab"})
        assert resp.status_code == 400

    async def test_set_pin_empty_rejected(self, client):
        """Empty PIN is rejected."""
        resp = await client.post("/auth/pin", json={"pin": ""})
        assert resp.status_code == 400


class TestForceRegeneratePin:
    """POST /auth/users/{user_id}/pin — owner force-regenerates."""

    async def test_force_regenerate_success(self, client):
        """Owner can regenerate another user's PIN."""
        user_id = str(uuid4())
        resp = await client.post(f"/auth/users/{user_id}/pin", json={"pin": "5678"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Supervisor PIN regenerated"

    async def test_force_regenerate_invalid_pin(self, client):
        """Short PIN is rejected."""
        user_id = str(uuid4())
        resp = await client.post(f"/auth/users/{user_id}/pin", json={"pin": "12"})
        assert resp.status_code == 400
        assert "4-6 digits" in resp.json()["detail"]

    async def test_force_regenerate_6_digits(self, client):
        """6-digit PIN works for force-regenerate."""
        user_id = str(uuid4())
        resp = await client.post(f"/auth/users/{user_id}/pin", json={"pin": "999999"})
        assert resp.status_code == 200


class TestVoidCartItem:
    """POST /cart/items/{item_id}/void — supervisor PIN override."""

    @patch("app.common.bridge._get_sdb")
    @patch("app.core.security.verify_pin", return_value=True)
    async def test_void_success(self, mock_verify, mock_get_sdb, client):
        """Valid PIN + matching supervisor with cart:delete → item removed."""
        from app.common.events.outbox import OutboxWrite

        item_id = uuid4()
        supervisor_id = uuid4()

        mock_pin = MagicMock()
        mock_pin.user_id = supervisor_id
        mock_pin.pin_hash = "hashed_pin"

        identity_session = _make_identity_session(supervisor_pins=[mock_pin], has_permission=True)
        cart_session = _make_cart_session()

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session if name == "identity" else cart_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        mock_item = MagicMock()
        mock_item.id = item_id
        mock_item.cart_id = uuid4()

        with (
            patch("app.cart.repository.get_cart_item", new_callable=AsyncMock, return_value=mock_item),
            patch("app.cart.repository.remove_cart_item", new_callable=AsyncMock),
            patch("app.cart.service.plan_remove_item", return_value=[
                OutboxWrite(event=MagicMock(event_type="cart.item.removed"), aggregate_type="cart_item", aggregate_id=str(item_id))
            ]),
        ):
            resp = await client.post(
                f"/cart/items/{item_id}/void",
                json={"supervisor_pin": "1234"},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["success"] is True

    @patch("app.common.bridge._get_sdb")
    async def test_void_invalid_pin(self, mock_get_sdb, client):
        """PIN doesn't match any supervisor → 403."""
        item_id = uuid4()

        identity_session = _make_identity_session(supervisor_pins=[], has_permission=False)

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        resp = await client.post(
            f"/cart/items/{item_id}/void",
            json={"supervisor_pin": "0000"},
        )
        assert resp.status_code == 403
        assert "Invalid supervisor PIN" in resp.json()["detail"]

    @patch("app.common.bridge._get_sdb")
    async def test_void_expired_pin_rejected(self, mock_get_sdb, client):
        """Expired PIN filtered by query → 403."""
        item_id = uuid4()

        # Empty list = PIN expired (filtered out by WHERE expires_at > NOW())
        identity_session = _make_identity_session(supervisor_pins=[], has_permission=False)

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        resp = await client.post(
            f"/cart/items/{item_id}/void",
            json={"supervisor_pin": "1234"},
        )
        assert resp.status_code == 403
        assert "Invalid supervisor PIN" in resp.json()["detail"]

    @patch("app.common.bridge._get_sdb")
    @patch("app.core.security.verify_pin", return_value=True)
    async def test_void_pin_holder_lacks_permission(self, mock_verify, mock_get_sdb, client):
        """PIN matches but user has no cart:delete → 403."""
        item_id = uuid4()

        mock_pin = MagicMock()
        mock_pin.user_id = uuid4()
        mock_pin.pin_hash = "hashed_pin"

        identity_session = _make_identity_session(supervisor_pins=[mock_pin], has_permission=False)

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        resp = await client.post(
            f"/cart/items/{item_id}/void",
            json={"supervisor_pin": "1234"},
        )
        assert resp.status_code == 403
        assert "Invalid supervisor PIN" in resp.json()["detail"]

    @patch("app.common.bridge._get_sdb")
    @patch("app.core.security.verify_pin", return_value=True)
    async def test_void_cart_item_not_found(self, mock_verify, mock_get_sdb, client):
        """Valid PIN but cart item doesn't exist → 404."""
        item_id = uuid4()

        mock_pin = MagicMock()
        mock_pin.user_id = uuid4()
        mock_pin.pin_hash = "hashed_pin"

        identity_session = _make_identity_session(supervisor_pins=[mock_pin], has_permission=True)
        cart_session = _make_cart_session()

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session if name == "identity" else cart_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        with patch("app.cart.repository.get_cart_item", new_callable=AsyncMock, return_value=None):
            resp = await client.post(
                f"/cart/items/{item_id}/void",
                json={"supervisor_pin": "1234"},
            )
            assert resp.status_code == 404
            assert "Cart item not found" in resp.json()["detail"]

    @patch("app.common.bridge._get_sdb")
    @patch("app.core.security.verify_pin", return_value=True)
    async def test_void_records_approved_by_in_event(self, mock_verify, mock_get_sdb, client):
        """Supervisor ID should be passed as approved_by in the event."""
        from app.common.events.outbox import OutboxWrite

        item_id = uuid4()
        supervisor_id = uuid4()

        mock_pin = MagicMock()
        mock_pin.user_id = supervisor_id
        mock_pin.pin_hash = "hashed_pin"

        identity_session = _make_identity_session(supervisor_pins=[mock_pin], has_permission=True)
        cart_session = _make_cart_session()

        def get_sdb(name):
            sdb = MagicMock()
            sdb.session.return_value = identity_session if name == "identity" else cart_session
            return sdb

        mock_get_sdb.side_effect = get_sdb

        mock_item = MagicMock()
        mock_item.id = item_id
        mock_item.cart_id = uuid4()

        captured_command = None

        def capture_plan(command, item):
            nonlocal captured_command
            captured_command = command
            return [OutboxWrite(event=MagicMock(event_type="cart.item.removed"), aggregate_type="cart_item", aggregate_id=str(item_id))]

        with (
            patch("app.cart.repository.get_cart_item", new_callable=AsyncMock, return_value=mock_item),
            patch("app.cart.repository.remove_cart_item", new_callable=AsyncMock),
            patch("app.cart.service.plan_remove_item", side_effect=capture_plan),
        ):
            resp = await client.post(
                f"/cart/items/{item_id}/void",
                json={"supervisor_pin": "1234"},
            )
            assert resp.status_code == 200
            assert captured_command is not None
            assert captured_command.approved_by == supervisor_id
