"""Tenant activity trail: unified auth + business layers, actor profile.

Mirrors the platform audit trail shape for the tenant-facing console.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
Now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _row(**kwargs):
    row = MagicMock()
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


def _ctx(tenant_id, results):
    calls = []

    async def execute(_q):
        calls.append(_q)
        return results[len(calls) - 1]

    ctx = MagicMock()
    ctx.user.business_id = str(tenant_id)
    ctx.session.execute = AsyncMock(side_effect=execute)
    return ctx


def _auth_row(action="login_success", details='{"method": "password"}'):
    return _row(
        user_id=uuid4(),
        action=action,
        ip_address="10.0.0.7",
        user_agent="pytest",
        details=details,
        created_at=Now - timedelta(days=1),
    )


def _biz_row(action="sale.create", resource_type="sale"):
    return _row(
        user_id=str(uuid4()),
        action=action,
        resource_type=resource_type,
        resource_id=str(uuid4()),
        details=None,
        status_code=201,
        ip_address="10.0.0.9",
        created_at=Now - timedelta(hours=2),
    )


class TestTenantActivity:
    async def test_merges_layers_with_resource_and_pagination(self):
        from app.auth.routes import tenant_activity

        tenant_id = uuid4()
        supervisor = uuid4()
        void_row = _auth_row(
            action="cart_void_approved",
            details=f'{{"supervisor_id": "{supervisor}", "cart_id": "c1"}}',
        )
        ctx = _ctx(
            tenant_id,
            [
                _Result(rows=[void_row]),
                _Result(rows=[_biz_row()]),
            ],
        )
        resp = await tenant_activity(ctx, page=1, page_size=20)

        data = resp["data"]
        assert data["total"] == 2
        # business layer is newer than the auth layer → sorted desc
        assert data["items"][0]["source"] == "tenant"
        assert data["items"][0]["resource"] == "sale"
        assert data["items"][0]["resource_id"]
        assert data["items"][1]["source"] == "auth"
        assert data["items"][1]["approver"] == str(supervisor)
        assert data["items"][1]["actor"] == str(void_row.user_id)
        assert data["items"][1]["ip_address"] == "10.0.0.7"

    async def test_paginates_window(self):
        from app.auth.routes import tenant_activity

        tenant_id = uuid4()
        ctx = _ctx(
            tenant_id,
            [
                _Result(rows=[_auth_row(), _auth_row()]),
                _Result(rows=[]),
            ],
        )
        resp = await tenant_activity(ctx, page=2, page_size=1)
        data = resp["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 1
        assert data["page"] == 2

    async def test_filters_by_action(self):
        from app.auth.routes import tenant_activity

        tenant_id = uuid4()
        ctx = _ctx(
            tenant_id,
            [
                _Result(rows=[_auth_row(action="login_success")]),
                _Result(rows=[_biz_row(action="sale.create")]),
            ],
        )
        resp = await tenant_activity(ctx, action="sale.create")
        data = resp["data"]
        assert data["total"] == 1
        assert data["items"][0]["action"] == "sale.create"

    async def test_filters_by_user_id(self):
        from app.auth.routes import tenant_activity

        tenant_id = uuid4()
        user = uuid4()
        ctx = _ctx(
            tenant_id,
            [
                _Result(rows=[_auth_row()]),
                _Result(rows=[_biz_row()]),
            ],
        )
        resp = await tenant_activity(ctx, user_id=str(user))
        assert resp["data"]["total"] == 0


class TestTenantActivityActor:
    async def test_resolves_employee(self):
        from app.auth.routes import tenant_activity_actor

        tenant_id = uuid4()
        uid = uuid4()
        store_id = uuid4()
        user = _row(
            id=uid,
            full_name="Ada Obi",
            email="ada@kedmart.com",
            phone="+2348000000001",
            status="active",
            store_id=store_id,
            created_at=Now - timedelta(days=200),
            last_login_at=Now - timedelta(days=1),
        )
        store = _row(name="Main Store")
        ctx = _ctx(
            tenant_id,
            [
                _Result(scalar=user),
                _Result(scalar=store),
                _Result(rows=[("Cashier",)]),
            ],
        )
        resp = await tenant_activity_actor(str(uid), ctx)
        data = resp["data"]
        assert data["full_name"] == "Ada Obi"
        assert data["store_name"] == "Main Store"
        assert data["roles"] == ["Cashier"]

    async def test_unknown_actor_404(self):
        from app.auth.routes import tenant_activity_actor

        tenant_id = uuid4()
        ctx = _ctx(tenant_id, [_Result(scalar=None)])
        with pytest.raises(HTTPException) as exc_info:
            await tenant_activity_actor(str(uuid4()), ctx)
        assert exc_info.value.status_code == 404

    async def test_non_uuid_404(self):
        from app.auth.routes import tenant_activity_actor

        tenant_id = uuid4()
        ctx = _ctx(tenant_id, [])
        with pytest.raises(HTTPException) as exc_info:
            await tenant_activity_actor("system", ctx)
        assert exc_info.value.status_code == 404
        ctx.session.execute.assert_not_awaited()
