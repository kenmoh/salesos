"""Tenant intelligence: deep overview + store breakdown.

Run against mock sessions with ordered results that mirror the aggregate
sequence each handler performs — deterministic, no DB needed.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.platform.routes import tenant_overview, tenant_stores


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
