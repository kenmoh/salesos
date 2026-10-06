"""Editing a role's permissions is a power change, so it is gated and audited.

Rewriting a role rewrites what every holder can do. Two things have to hold:

- The change needs a supervisor PIN unless the caller is an owner, and a refusal
  must not reach the database at all.
- After it lands, nobody keeps access they just lost. ``sf:perms`` caches a
  user's permissions for five minutes and ``sm:role_rank`` caches their rank for
  five minutes, so an edit that skips them leaves the tenant in a state the UI
  does not describe.
"""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import (
    TenantContext,
    TokenData,
    get_tenant_context,
    get_tenant_db_context,
)

ALL_PERMISSIONS = ["roles:read", "roles:assign", "accounting:read", "accounting:write"]

OWNER_RANK = 80
MANAGER_RANK = 60


class _FakeRedis:
    """Enough Redis for the PIN attempt counter and the rank cache deletes."""

    def __init__(self):
        self.deleted: list[str] = []

    async def get(self, key):
        return None

    async def delete(self, key):
        self.deleted.append(key)

    def pipeline(self):
        return self

    async def incr(self, key):
        return None

    async def expire(self, key, ttl):
        return None

    async def execute(self):
        return None


def _token(rank: int) -> TokenData:
    return TokenData(
        {
            "sub": str(uuid4()),
            "bid": str(uuid4()),
            "role": "owner" if rank >= OWNER_RANK else "manager",
            "perms": ALL_PERMISSIONS,
            "jti": str(uuid4()),
            "exp": 9999999999,
        }
    )


class _IdentitySdb:
    """Stands in for the bridge's identity session database.

    ``async with sdb.session() as session`` has to yield the mock the test is
    asserting on. Left unconfigured, MagicMock fabricates its own context
    manager and every assertion reads the wrong object.
    """

    def __init__(self, session):
        self._session = session

    def session(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _identity_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


class _PermissionSession:
    """Routes the gate's queries by table so ordering does not matter."""

    def __init__(self, row):
        self.session = AsyncMock()
        self.session.add = MagicMock()
        self.session.flush = AsyncMock()
        self.session.commit = AsyncMock()
        self.row = row
        self._row = row
        self._approver = uuid4()

        async def execute(stmt, *args, **kwargs):
            text = str(stmt)
            if "supervisor_pins" in text:
                found = MagicMock()
                found.scalars.return_value.all.return_value = [
                    MagicMock(user_id=self._approver, pin_hash="hashed")
                ]
                return found
            if "permissions" in text:
                return MagicMock(
                    scalar_one_or_none=MagicMock(return_value=MagicMock())
                )
            return MagicMock(scalar_one_or_none=MagicMock(return_value=self._row))

        self.session.execute = AsyncMock(side_effect=execute)


@pytest.fixture
async def call_set_permissions() -> AsyncGenerator:
    """PATCH the permission write and report what the route tried to do."""
    calls: list[dict] = []

    async def _fake_set(**kwargs):
        calls.append(kwargs)
        return {
            "id": kwargs["role_id"],
            "name": "manager",
            "rank": MANAGER_RANK,
            "description": "Day-to-day operations",
            "permissions": ["accounting:read"],
        }

    from app.auth import routes as auth_routes

    with patch.object(auth_routes, "set_role_permissions_for_tenant", _fake_set):
        yield calls


def _build_client(session, rank: int) -> AsyncClient:
    app = FastAPI()
    from app.auth import routes as auth

    app.include_router(auth.router)

    async def override():
        yield TenantContext(user=_token(rank), session=session)

    # require_permission() pulls in get_tenant_context, so both need overriding:
    # otherwise the route falls through to bearer auth and answers 401.
    app.dependency_overrides[get_tenant_context] = override
    app.dependency_overrides[get_tenant_db_context] = override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(pin: str | None = None) -> dict:
    body: dict = {"permission_ids": [str(uuid4())]}
    if pin is not None:
        body["supervisor_pin"] = pin
    return body


async def _put(app: AsyncClient, role_id: str, body: dict):
    async with app:
        return await app.put(f"/auth/roles/{role_id}/permissions", json=body)


class TestGate:
    async def test_owner_needs_no_pin(self, call_set_permissions):
        session = _PermissionSession(MagicMock())
        app = _build_client(session.session, OWNER_RANK)

        with (
            patch(
                "app.core.dependencies.get_cached_role_rank",
                AsyncMock(return_value=OWNER_RANK),
            ),
            patch(
                "app.core.redis_client.get_cache_redis",
                AsyncMock(return_value=_FakeRedis()),
            ),
        ):
            resp = await _put(app, str(uuid4()), _body())

        assert resp.status_code == 200, resp.text
        assert call_set_permissions[0]["supervisor_id"] is None

    async def test_non_owner_without_a_pin_is_refused_before_any_write(
        self, call_set_permissions
    ):
        session = _PermissionSession(MagicMock())
        app = _build_client(session.session, MANAGER_RANK)

        with (
            patch(
                "app.core.dependencies.get_cached_role_rank",
                AsyncMock(return_value=MANAGER_RANK),
            ),
            patch(
                "app.core.redis_client.get_cache_redis",
                AsyncMock(return_value=_FakeRedis()),
            ),
        ):
            resp = await _put(app, str(uuid4()), _body())

        assert resp.status_code == 403
        assert resp.json()["detail"] == "supervisor_pin_required"
        # The refusal must happen before the write is attempted.
        assert call_set_permissions == []

    async def test_wrong_pin_is_refused(self, call_set_permissions):
        session = _PermissionSession(MagicMock())
        app = _build_client(session.session, MANAGER_RANK)

        with (
            patch(
                "app.core.dependencies.get_cached_role_rank",
                AsyncMock(return_value=MANAGER_RANK),
            ),
            patch(
                "app.core.redis_client.get_cache_redis",
                AsyncMock(return_value=_FakeRedis()),
            ),
            patch("app.core.approval.verify_pin", return_value=False),
        ):
            resp = await _put(app, str(uuid4()), _body("0000"))

        assert resp.status_code == 403
        assert resp.json()["detail"] == "invalid_supervisor_pin"
        assert call_set_permissions == []

    async def test_supervisor_pin_approves_and_is_recorded(self, call_set_permissions):
        session = _PermissionSession(MagicMock())
        app = _build_client(session.session, MANAGER_RANK)

        with (
            patch(
                "app.core.dependencies.get_cached_role_rank",
                AsyncMock(return_value=MANAGER_RANK),
            ),
            patch(
                "app.core.redis_client.get_cache_redis",
                AsyncMock(return_value=_FakeRedis()),
            ),
            patch("app.core.approval.verify_pin", return_value=True),
        ):
            resp = await _put(app, str(uuid4()), _body("1234"))

        assert resp.status_code == 200, resp.text
        assert call_set_permissions[0]["supervisor_id"] == str(session._approver)
        assert call_set_permissions[0]["actor_id"]


class TestAuditAndCacheInvalidation:
    """The bridge function owns identity writes, so the audit entry and the
    cache invalidation live beside the change rather than in the route."""

    def _patches(self, session, role, before, after, holders):
        return [
            patch(
                "app.common.bridge._get_sdb",
                MagicMock(return_value=_IdentitySdb(session)),
            ),
            patch(
                "app.identity.repository.get_role_by_id",
                AsyncMock(return_value=role),
            ),
            patch(
                "app.identity.repository.get_permissions_for_role",
                AsyncMock(side_effect=[before, after]),
            ),
            patch(
                "app.identity.repository.set_role_permissions",
                AsyncMock(),
            ),
            patch("app.common.bridge._role_holders", AsyncMock(return_value=holders)),
            patch("app.common.auth_service.bust_perms", AsyncMock()),
            patch("app.common.cache.invalidate_tenant", AsyncMock()),
            patch("app.core.redis_client.cache_del", AsyncMock()),
        ]

    async def test_change_is_audited_with_the_delta_and_approver(self):
        import json

        from app.common import bridge

        tenant_id, role_id, actor, supervisor = str(uuid4()), uuid4(), uuid4(), uuid4()
        role = SimpleNamespace(
            id=role_id,
            tenant_id=tenant_id,
            name="manager",
            rank=60,
            description="Day-to-day operations",
        )
        session = _identity_session()

        before = [SimpleNamespace(name=n) for n in ("accounting:read", "sales:void")]
        after = [SimpleNamespace(name=n) for n in ("accounting:read", "sales:read")]

        patches = self._patches(session, role, before, after, [uuid4()])
        for p in patches:
            p.start()
        try:
            result = await bridge.set_role_permissions_for_tenant(
                tenant_id=tenant_id,
                role_id=str(role_id),
                permission_ids=[str(uuid4())],
                actor_id=str(actor),
                supervisor_id=str(supervisor),
            )
        finally:
            for p in reversed(patches):
                p.stop()

        assert result is not None
        audit = session.add.call_args.args[0]
        assert audit.action == "role_permissions_updated"
        assert audit.user_id == actor
        details = json.loads(audit.details)
        assert details["added"] == ["sales:read"]
        assert details["removed"] == ["sales:void"]
        assert details["supervisor_id"] == str(supervisor)
        # The role comes back complete, so a client caching the response does
        # not record the role as granting nothing.
        assert result["permissions"] == ["accounting:read", "sales:read"]
        assert result["rank"] == role.rank

    async def test_every_holder_loses_their_cached_permissions(self):
        from app.common import bridge

        tenant_id, role_id = str(uuid4()), uuid4()
        holders = [uuid4(), uuid4(), uuid4()]
        role = SimpleNamespace(
            id=role_id,
            tenant_id=tenant_id,
            name="cashier",
            rank=40,
            description="Process sales at registers",
        )
        session = _identity_session()

        perms = [SimpleNamespace(name=n) for n in ("sales:read",)]
        bust = AsyncMock()
        cache_del = AsyncMock()
        invalidate = AsyncMock()

        patches = self._patches(session, role, [], perms, holders)
        patches[5] = patch("app.common.auth_service.bust_perms", bust)
        patches[6] = patch("app.common.cache.invalidate_tenant", invalidate)
        patches[7] = patch("app.core.redis_client.cache_del", cache_del)
        for p in patches:
            p.start()
        try:
            await bridge.set_role_permissions_for_tenant(
                tenant_id=tenant_id,
                role_id=str(role_id),
                permission_ids=[str(uuid4())],
                actor_id=str(uuid4()),
            )
        finally:
            for p in reversed(patches):
                p.stop()

        assert {c.args[0] for c in bust.await_args_list} == {str(h) for h in holders}
        # The rank cache matters too: a promoted owner stays refused until it
        # expires otherwise.
        assert {c.args[0] for c in cache_del.await_args_list} == {
            f"sm:role_rank:{h}" for h in holders
        }
        invalidate.assert_awaited_once_with(tenant_id, prefix="roles:all")

    async def test_foreign_role_is_not_touched(self):
        from app.common import bridge

        other_tenant = str(uuid4())
        session = _identity_session()

        patches = self._patches(
            session,
            SimpleNamespace(
                id=uuid4(), tenant_id=other_tenant, name="manager", rank=60, description=None
            ),
            [],
            [],
            [],
        )
        for p in patches:
            p.start()
        try:
            result = await bridge.set_role_permissions_for_tenant(
                tenant_id=str(uuid4()), role_id=str(uuid4()), permission_ids=[]
            )
        finally:
            for p in reversed(patches):
                p.stop()

        assert result is None
        session.add.assert_not_called()