"""Tenant intelligence: deep overview + store breakdown.

Run against mock sessions with ordered results that mirror the aggregate
sequence each handler performs — deterministic, no DB needed.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from datetime import datetime, timedelta, timezone

from app.platform.routes import (
    audit_actor_details,
    platform_audit_log_feed,
    tenant_audit_trail,
    tenant_overview,
    tenant_stores,
)

Now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _plat_row():
    return dict(
        admin_id=uuid4(),
        action="tenant.delete",
        resource="tenant",
        resource_id="t1",
        details='{"confirm": "Ked Mart"}',
        created_at=Now - timedelta(days=2),
    )


def _auth_row():
    return dict(
        action="login_success",
        details='{"method": "password"}',
        created_at=Now - timedelta(days=1),
    )


def _biz_row():
    return dict(
        user_id=uuid4(),
        action="login",
        resource_type="auth",
        details=None,
        status_code=200,
        created_at=Now - timedelta(hours=3),
    )



def _row(**kwargs):
    # setattr instead of MagicMock(**kwargs): `name` is a Mock constructor
    # kwarg (it names the mock) rather than an attribute.
    row = MagicMock()
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


def _tenant():
    tenant = MagicMock()
    tenant.id = str(uuid4())
    tenant.slug = "ked-mart"
    tenant.business_name = "Ked Mart"
    tenant.tier = "starter"
    tenant.status = "active"
    return tenant


def _one(**kwargs):
    return MagicMock(counts=kwargs) if False else _row(**kwargs)


class _Result:
    def __init__(self, scalar=None, one=None, rows=None):
        self._scalar = scalar
        self._one = one
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def one(self):
        return self._one

    def all(self):
        return self._rows


def _overview_session(tenant):
    """Ordered results matching tenant_overview's aggregate sequence."""
    executor_calls = []

    async def execute(query):
        executor_calls.append(query)
        if len(executor_calls) == 1:
            return _Result(scalar=tenant)
        idx = len(executor_calls)
        table = {
            2: _Result(rows=[]),                                          # role rows
            3: _Result(one=_row(active=6, suspended=1, last_login=None)),  # people
            4: _Result(scalar=7),                                          # user count
            5: _Result(scalar=54),                                         # products
            6: _Result(scalar=230),                                        # sales lifetime
            7: _Result(scalar=1_234_500.0),                                # lifetime revenue
            8: _Result(one=_row(cnt=40, vol=150_000.0)),                   # window stats
            9: _Result(one=_row(cnt=33, vol=120_000.0)),                   # prev window
            10: _Result(scalar=None),                                      # last sale
            11: _Result(rows=[]),                                          # daily activity
            12: _Result(scalar=61),                                        # payments count
            13: _Result(rows=[_row(method="card", cnt=30, vol=90_000.0)]),  # methods
            14: _Result(one=_row(settled=1500.0, pending=300.0, pending_count=2, last_fee=None)),  # fees
            15: _Result(rows=[("invoice", 3)]),                            # doc types
            16: _Result(scalar=12),                                        # customers
            17: _Result(scalar=2),                                         # stores
            18: _Result(scalar=1),                                         # warehouses
        }
        if idx not in table:
            return _Result()
        return table[idx]

    ctx = MagicMock()
    ctx.session.execute = AsyncMock(side_effect=execute)
    return ctx


class TestTenantOverview:
    async def test_shape_and_metrics(self):
        tenant = _tenant()
        ctx = _overview_session(tenant)
        resp = await tenant_overview(tenant.id, ctx, days=30)

        data = resp["data"]
        assert data["counts"]["users"] == 7
        assert data["employees"]["suspended"] == 1
        assert data["sales"]["window_count"] == 40
        assert data["sales"]["window_revenue"] == 150000.0
        assert data["sales"]["revenue_change_pct"] == 25.0
        assert data["sales"]["avg_basket"] == 3750.0
        assert data["fees"]["settled_total"] == 1500.0
        assert data["fees"]["pending_count"] == 2
        assert data["risk"]["level"] == "high"  # no sales on record
        assert data["counts"]["stores"] == 2
        assert data["payments"]["by_method"][0]["method"] == "card"

    async def test_days_is_capped(self):
        ctx = _overview_session(_tenant())
        resp = await tenant_overview("t", ctx, days=9999)
        assert resp["data"]["sales"]["window_days"] == 365

    async def test_unknown_tenant_404(self):
        ctx = MagicMock()
        ctx.session.execute = AsyncMock(
            return_value=_Result(scalar=None)
        )
        with pytest.raises(HTTPException) as exc_info:
            await tenant_overview("nope", ctx)
        assert exc_info.value.status_code == 404


class TestTenantStores:
    async def _stores_session(self, tenant):
        store_id = uuid4()
        row = _row(
            id=store_id,
            name="Main Street",
            address="1 Ave",
            status="active",
            is_warehouse=False,
            lt_count=12,
            lt_revenue=60_000.0,
            cur_revenue=20_000.0,
            cur_count=4,
            prev_revenue=15_000.0,
            last_sale=None,
            products=30,
            employees=3,
            low_stock=2,
        )
        executor_calls = []

        async def execute(query):
            executor_calls.append(query)
            if len(executor_calls) == 1:
                return _Result(scalar=tenant)
            if len(executor_calls) == 2:
                return _Result(rows=[row])
            return _Result(
                rows=[_row(store_id=store_id, method="cash", cnt=4, vol=20_000.0)]
            )

        ctx = MagicMock()
        ctx.session.execute = AsyncMock(side_effect=execute)
        return ctx, row

    async def test_shape_and_change(self):
        tenant = _tenant()
        ctx, _row_ = await self._stores_session(tenant)
        resp = await tenant_stores(tenant.id, ctx, days=30)

        data = resp["data"]
        assert data["total"] == 1
        store = data["items"][0]
        assert store["name"] == "Main Street"
        assert store["revenue_change_pct"] == round((20000 - 15000) / 15000 * 100, 1)
        assert store["avg_basket"] == 5000.0
        assert store["low_stock"] == 2
        assert store["payment_methods"][0]["method"] == "cash"

    async def test_missing_tenant_404(self):
        ctx = MagicMock()
        ctx.session.execute = AsyncMock(return_value=_Result(scalar=None))
        with pytest.raises(HTTPException) as exc_info:
            await tenant_stores("nope", ctx)
        assert exc_info.value.status_code == 404


def _data(r):
    return r["data"]


class TestTenantAuditTrail:
    def _trail_session(self, tenant, rows_by_source):
        """rows_by_source maps call index -> list of row-dicts (already flat)."""
        executor_calls = []

        async def execute(_query):
            executor_calls.append(_query)
            idx = len(executor_calls)
            rows_spec = rows_by_source.get(idx, [])
            objs = []
            for r in rows_spec:
                obj = MagicMock()
                for key, value in r.items():
                    if key == "created_at":
                        setattr(obj, "created_at", value)
                    else:
                        setattr(obj, key, value)
                objs.append(obj)
            result = MagicMock()
            result.all.return_value = objs
            if idx == 1:
                result.scalar_one_or_none.return_value = tenant
            return result

        ctx = MagicMock()
        ctx.session.execute = AsyncMock(side_effect=execute)
        return ctx

    async def test_merged_sources_sorted_desc(self):
        tenant = _tenant()
        # call 2 = platform, 3 = auth, 4 = business (source=None pulls all)
        ctx = self._trail_session(
            tenant,
            {
                2: [_plat_row()],
                3: [_auth_row()],
                4: [_biz_row()],
            },
        )
        resp = await tenant_audit_trail(tenant.id, ctx, days=90)

        data = _data(resp)
        assert data["total"] == 3
        sources = [i["source"] for i in data["items"]]
        assert sources == ["tenant", "auth", "platform"]  # desc by at
        platform_item = data["items"][2]
        assert platform_item["action"] == "tenant.delete"
        assert platform_item["detail"] == {"confirm": "Ked Mart"}
        assert platform_item["actor"] is not None

    async def test_auth_source_skips_other_layers(self):
        tenant = _tenant()
        calls = []

        async def execute(_q):
            calls.append(_q)
            result = MagicMock()
            result.scalar_one_or_none.return_value = tenant if len(calls) == 1 else None
            result.all.return_value = []
            return result

        ctx = MagicMock()
        ctx.session.execute = AsyncMock(side_effect=execute)
        resp = await tenant_audit_trail(tenant.id, ctx, days=90, source="auth")

        assert _data(resp)["total"] == 0
        assert len(calls) == 2  # tenant lookup + auth layer only

    async def test_invalid_source_422(self):
        ctx = MagicMock()
        ctx.session.execute = AsyncMock(return_value=_Result(scalar=_tenant()))
        with pytest.raises(HTTPException) as exc_info:
            await tenant_audit_trail("t", ctx, source="bogus")
        assert exc_info.value.status_code == 422

    async def test_unknown_tenant_404(self):
        ctx = MagicMock()
        ctx.session.execute = AsyncMock(return_value=_Result(scalar=None))
        with pytest.raises(HTTPException) as exc_info:
            await tenant_audit_trail("nope", ctx)
        assert exc_info.value.status_code == 404


class TestPlatformAuditFeed:
    async def _feed_session(self, rows):
        executor_calls = []

        async def execute(_q):
            executor_calls.append(_q)
            idx = len(executor_calls)
            if idx == 1:
                return _Result(scalar=len(rows))
            objs = []
            for r in rows:
                obj = MagicMock()
                for key, value in r.items():
                    setattr(obj, key, value)
                objs.append(obj)
            result = MagicMock()
            result.all.return_value = objs
            return result

        ctx = MagicMock()
        ctx.session.execute = AsyncMock(side_effect=execute)
        return ctx

    async def test_feed_shape(self):
        r = _plat_row()
        r["id"] = uuid4()
        r["ip_address"] = "10.0.0.1"
        ctx = await self._feed_session([r])
        resp = await platform_audit_log_feed(ctx)

        data = resp["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["action"] == "tenant.delete"
        assert item["details"] == {"confirm": "Ked Mart"}
        assert item["created_at"].startswith("2026-10-25")
        assert item["ip_address"] == "10.0.0.1"


class TestAuditActorDetails:
    def _ctx(self, results):
        """results: ordered list; each is a _Result for that execute call."""
        calls = []

        async def execute(_q):
            calls.append(_q)
            return results[len(calls) - 1]

        ctx = MagicMock()
        ctx.session.execute = AsyncMock(side_effect=execute)
        return ctx

    async def test_resolves_tenant_user(self):
        uid = uuid4()
        user = _row(
            id=uid,
            full_name="Ada Obi",
            email="ada@kedmart.com",
            phone="+2348000000001",
            status="active",
            tenant_id=uuid4(),
            store_id=uuid4(),
            created_at=Now - timedelta(days=200),
            last_login_at=Now - timedelta(days=1),
        )
        tenant = _tenant()
        store = _row(name="Main Store")
        ctx = self._ctx(
            [
                _Result(scalar=user),      # user lookup
                _Result(scalar=tenant),    # tenant lookup
                _Result(scalar=store),     # store lookup
                _Result(rows=[("Cashier",)]),  # role rows
            ]
        )
        resp = await audit_actor_details(str(uid), ctx)
        data = resp["data"]
        assert data["actor_type"] == "user"
        assert data["full_name"] == "Ada Obi"
        assert data["tenant_name"] == "Ked Mart"
        assert data["store_name"] == "Main Store"
        assert data["roles"] == ["Cashier"]

    async def test_resolves_platform_admin(self):
        aid = uuid4()
        admin = _row(
            id=aid,
            full_name="Platform Owner",
            email="admin@servi-pal.com",
            status="active",
            role="super_admin",
            created_at=Now - timedelta(days=30),
            last_login_at=Now - timedelta(days=2),
        )
        ctx = self._ctx(
            [
                _Result(scalar=None),   # user lookup misses
                _Result(scalar=admin),  # admin lookup hits
            ]
        )
        resp = await audit_actor_details(str(aid), ctx)
        data = resp["data"]
        assert data["actor_type"] == "admin"
        assert data["full_name"] == "Platform Owner"
        assert data["roles"] == ["super_admin"]

    async def test_unknown_actor_404(self):
        ctx = self._ctx(
            [
                _Result(scalar=None),
                _Result(scalar=None),
            ]
        )
        with pytest.raises(HTTPException) as exc_info:
            await audit_actor_details(str(uuid4()), ctx)
        assert exc_info.value.status_code == 404

    async def test_non_uuid_404(self):
        ctx = self._ctx([])
        with pytest.raises(HTTPException) as exc_info:
            await audit_actor_details("system", ctx)
        assert exc_info.value.status_code == 404
        ctx.session.execute.assert_not_awaited()
