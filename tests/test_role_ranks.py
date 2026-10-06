"""Role rank is a security boundary, so it gets tests of its own.

Owner was seeded with rank 1 while the owner-only gate compared against 80.
Every owner-only path therefore refused the owner: the supervisor-PIN bypass on
store corrections sent the owner through the PIN flow meant for everyone below
them, and ``require_owner`` would have rejected them outright.

These tests pin the two properties that follow:

- ``ROLE_RANKS`` is the single source of truth, and owner outranks every role a
  tenant can create.
- No role may be created or updated above the owner's rank, because anything at
  or above it satisfies an owner-only gate without being the owner.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import TenantContext, TokenData, get_tenant_context
from app.identity.constants import (
    OWNER_RANK,
    OWNER_ROLE_NAME,
    ROLE_RANKS,
    UNKNOWN_ROLE_RANK,
)

ALL_PERMISSIONS = [
    "roles:read",
    "roles:assign",
    "accounting:read",
    "accounting:write",
]


def _token_data() -> TokenData:
    return TokenData(
        {
            "sub": str(uuid4()),
            "bid": str(uuid4()),
            "role": "owner",
            "perms": ALL_PERMISSIONS,
            "jti": str(uuid4()),
            "exp": 9999999999,
        }
    )


def _bridge_session():
    """An identity session that reports 'no existing role'."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def app() -> FastAPI:
    from app.auth import routes as auth

    application = FastAPI()
    application.include_router(auth.router)

    async def override_tenant():
        yield TenantContext(user=_token_data())

    application.dependency_overrides[get_tenant_context] = override_tenant
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class TestRankMap:
    def test_owner_outranks_the_roles_a_tenant_can_create(self):
        creatable = {
            n: r for n, r in ROLE_RANKS.items() if n not in (OWNER_ROLE_NAME, "super_admin")
        }
        assert [n for n, r in creatable.items() if r >= OWNER_RANK] == ["developer", "admin"]

    def test_owner_rank_is_the_value_the_seed_imports(self):
        # The regression was a literal rank=1 at the seed site. Pin the constant
        # the seed now imports instead of the number it happens to produce.
        assert OWNER_RANK == ROLE_RANKS[OWNER_ROLE_NAME] == 80

    def test_unknown_role_name_is_unsatisfiable(self):
        """A typo in a gate must fail closed, not pass everything."""
        assert UNKNOWN_ROLE_RANK > max(ROLE_RANKS.values())


class TestMinRole:
    @pytest.mark.parametrize(
        ("rank", "role", "expected"),
        [
            (OWNER_RANK, OWNER_ROLE_NAME, True),
            (90, OWNER_ROLE_NAME, True),
            (60, OWNER_ROLE_NAME, False),
            (60, "manager", True),
            (20, "manager", False),
            (100, "ownr", False),
        ],
    )
    async def test_gate_reads_the_role_ranks(self, rank, role, expected):
        user = _token_data()
        with patch(
            "app.core.dependencies.get_cached_role_rank", AsyncMock(return_value=rank)
        ):
            assert await user.min_role(role=role) is expected


class TestRankCeiling:
    """Anything at or above OWNER_RANK satisfies an owner-only gate, so nothing
    may be allowed to claim a rank that high."""

    async def test_create_rejects_a_rank_above_owner_before_touching_the_db(self):
        from app.common import bridge

        session = _bridge_session()
        with patch.object(bridge, "_get_sdb") as sdb:
            sdb.return_value.session.return_value = session
            with pytest.raises(ValueError, match="must not exceed"):
                await bridge.create_role_for_tenant(
                    tenant_id=str(uuid4()), name="rogue", rank=OWNER_RANK + 1
                )

        session.execute.assert_not_called()

    async def test_update_rejects_a_rank_above_owner(self):
        from app.common import bridge

        session = _bridge_session()
        with patch.object(bridge, "_get_sdb") as sdb:
            sdb.return_value.session.return_value = session
            with pytest.raises(ValueError, match="must not exceed"):
                await bridge.update_role_for_tenant(
                    tenant_id=str(uuid4()), role_id=str(uuid4()), rank=99
                )

    async def test_rank_at_the_ceiling_is_allowed(self):
        """The check is `>`, not `>=`: a role may sit exactly at the owner's rank."""
        from app.common import bridge

        session = _bridge_session()
        with patch.object(bridge, "_get_sdb") as sdb:
            sdb.return_value.session.return_value = session
            created = await bridge.create_role_for_tenant(
                tenant_id=str(uuid4()), name="deputy", rank=OWNER_RANK
            )

        assert created["name"] == "deputy"
        assert created["rank"] == OWNER_RANK


class TestRouteSurfacesTheCeilingAsBadRequest:
    """A rejected rank is the caller's mistake, not a server fault."""

    async def test_create_role_returns_400(self, client):
        from app.auth import routes as auth_routes

        with patch.object(
            auth_routes,
            "create_role_for_tenant",
            AsyncMock(side_effect=ValueError(f"rank must not exceed {OWNER_RANK}")),
        ):
            resp = await client.post(
                "/auth/roles",
                json={"name": "rogue", "rank": OWNER_RANK + 1, "permission_ids": []},
            )

        assert resp.status_code == 400, resp.text
        assert "must not exceed" in resp.json()["detail"]

    async def test_update_role_returns_400(self, client):
        from app.auth import routes as auth_routes

        with patch.object(
            auth_routes,
            "update_role_for_tenant",
            AsyncMock(side_effect=ValueError(f"rank must not exceed {OWNER_RANK}")),
        ):
            resp = await client.patch(f"/auth/roles/{uuid4()}", json={"rank": 99})

        assert resp.status_code == 400, resp.text