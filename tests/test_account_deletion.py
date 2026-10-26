"""Unit tests for the in-app account deletion flow (delete_own_account).

Exercises the service logic without a database: a stub session records every
executed statement so we can assert the tenancy sweep is complete (every
tenant-scoped table gets a DELETE and the tenant record itself is removed via
session.delete) and that the owner/password gates behave.
"""

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.sql.dml import Delete

import app.common.auth_service as auth_service
from app.common.db.base import StoreFlowBase
from app.tenancy.models import Tenant


class _Scalars:
    def __init__(self, many):
        self._many = many

    def all(self):
        return list(self._many)


class _Result:
    def __init__(self, value=None, many=None):
        self._value = value
        self._many = many if many is not None else []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _Scalars(self._many)


class _StubSession:
    """AsyncSession stand-in: two positional results, then a default."""

    def __init__(self, first_result, second_result, default_result=None):
        self.executed: list = []
        self.deleted: list = []
        self.flushed = 0
        self.added: list = []
        self._positional = [first_result, second_result]
        self._default = default_result or _Result()

    async def execute(self, statement, params=None):
        index = len(self.executed)
        self.executed.append((statement, params))
        if index < len(self._positional):
            return self._positional[index]
        return self._default

    async def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


class _Owner:
    def __init__(self):
        self.id = uuid4()
        self.email = "owner@example.com"
        self.password_hash: str | None = None


class _Req:
    headers = {}
    client = None


def _run(owner: _Owner, member_ids: list, roles=("owner",), password: str = ""):
    """Drive delete_own_account against a stub session; returns the stub."""
    stub = _StubSession(
        _Result(value=owner),
        _Result(many=member_ids),
        _Result(value=Tenant()),  # default: later selects see the tenant row
    )

    async def noop_log(_session, _log):
        return None

    with patch.object(auth_service, "log_auth_event", noop_log):
        asyncio.run(
            auth_service.delete_own_account(
                session=stub,
                user_id=str(owner.id),
                business_id="00000000-0000-0000-0000-000000000001",
                role_names=list(roles),
                password=password,
                req=_Req(),
            )
        )
    return stub


def _deleted_tables(stub):
    return [stmt.table.name for stmt, _params in stub.executed if isinstance(stmt, Delete)]


class TestGates:
    def test_owner_role_required(self):
        owner = _Owner()
        with pytest.raises(auth_service.AuthError) as exc:
            _run(owner, [], roles=["manager"])
        assert exc.value.code == "not_owner"

    def test_password_required_when_hashed(self):
        owner = _Owner()
        owner.password_hash = "hashed"
        with pytest.raises(auth_service.AuthError) as exc:
            _run(owner, [], password="")
        assert exc.value.code == "password_required"


class TestSweep:
    def test_every_tenant_table_is_swept_and_tenant_row_removed(self):
        owner = _Owner()
        stub = _run(owner, [uuid4(), uuid4()])

        tenant_tables = {
            t.name
            for t in reversed(StoreFlowBase.metadata.sorted_tables)
            if any(c.name == "tenant_id" for c in t.columns)
        }
        swept = set(_deleted_tables(stub))

        assert swept == tenant_tables
        # The tenants table has no tenant_id column; it is removed via
        # session.delete so sweeping cannot accidentally miss it.
        assert "tenants" not in swept
        assert len(stub.deleted) == 1
        assert isinstance(stub.deleted[0], Tenant)
        assert stub.flushed >= 1  # tenant delete then audit-log add flush

    def test_children_are_swept_before_their_parents(self):
        owner = _Owner()
        stub = _run(owner, [])

        names = _deleted_tables(stub)
        # store_products pins product.tax_id without an ondelete action, so
        # taxes rows must still exist when store_products is swept.
        assert "store_products" in names and "taxes" in names
        assert names.index("store_products") < names.index("taxes")

    def test_sweep_runs_under_the_tenant_rls_context(self):
        owner = _Owner()
        stub = _run(owner, [])

        rls_sets = [params for _stmt, params in stub.executed if isinstance(params, dict)]
        assert any(
            params.get("b") == "00000000-0000-0000-0000-000000000001"
            for params in rls_sets
        )


class _ReqWithHeaders:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.client = None


class TestAuditTrail:
    def test_deletion_is_recorded_with_context(self, monkeypatch):
        owner = _Owner()
        owner.password_hash = None
        member_id = uuid4()
        logs: list = []

        captured_logs = []

        async def recording_log(session, log):
            captured_logs.append(log)
            session.add(log)
            await session.flush()

        stub = _StubSession(_Result(value=owner), _Result(many=[member_id]))
        req = _ReqWithHeaders({"user-agent": "StoreFlow/1.0"})
        monkeypatch.setattr(
            "app.identity.repository.log_auth_event", recording_log, raising=False
        )
        with patch.object(auth_service, "log_auth_event", recording_log):
            asyncio.run(
                auth_service.delete_own_account(
                    session=stub,
                    user_id=str(owner.id),
                    business_id="00000000-0000-0000-0000-000000000001",
                    role_names=["owner"],
                    password="",
                    req=req,
                )
            )

        assert len(captured_logs) == 1
        log = captured_logs[0]
        assert log.action == "account_deleted"
        assert log.user_id == str(owner.id)
        assert log.tenant_id == "00000000-0000-0000-0000-000000000001"
        assert json.loads(log.details) == {"email": owner.email}
