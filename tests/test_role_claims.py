"""A user can hold several roles, and the token has to say so.

The role claim used to be one comma-joined string that every check compared for
equality, so a user with two roles failed every role check — a manager who was
also a cashier was neither. ``roles`` is now a list, ``role`` stays the joined
string for the clients that display it, and tokens minted before the list
existed still authenticate.

Role changes also have to reach the permission cache. A role removed from a user
used to keep authorising requests until the cached set expired, which is a
security problem rather than a slow one.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.dependencies import TokenData
from app.core.security import create_access_token, decode_access_token


def _payload(**overrides) -> dict:
    base = {
        "sub": str(uuid4()),
        "bid": str(uuid4()),
        "role": "manager,cashier",
        "perms": ["sales:read"],
        "jti": str(uuid4()),
        "exp": 9999999999,
    }
    base.update(overrides)
    return base


class TestTokenCarriesEveryRole:
    def test_roles_are_a_list(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.roles == ["manager", "cashier"]

    def test_joined_string_is_kept_for_display(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.role == "manager,cashier"

    def test_primary_role_is_the_first(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.primary_role == "manager"

    def test_minted_token_carries_both_forms(self):
        token, _ = create_access_token(
            "user-1", "tenant-1", "manager,cashier", ["sales:read"], roles=["manager", "cashier"]
        )
        claims = decode_access_token(token)
        assert claims["role"] == "manager,cashier"
        assert claims["roles"] == ["manager", "cashier"]

    def test_single_role_token_is_unchanged_in_shape(self):
        token, _ = create_access_token("user-1", "tenant-1", "owner", [], roles=["owner"])
        claims = decode_access_token(token)
        assert claims["role"] == "owner"
        assert claims["roles"] == ["owner"]


class TestRoleMembership:
    def test_a_multi_role_user_matches_either_role(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.has_role("manager") is True
        assert token.has_role("cashier") is True

    def test_it_does_not_match_a_role_it_lacks(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.has_role("owner") is False

    def test_any_of_several_is_enough(self):
        token = TokenData(_payload(roles=["manager", "cashier"]))
        assert token.has_role("owner", "cashier") is True

    def test_an_owner_who_also_holds_another_role_is_still_an_owner(self):
        """The case that motivated this: rank said owner, the name check said no."""
        token = TokenData(_payload(role="owner,Create Customer", roles=["owner", "Create Customer"]))
        assert token.has_role("owner") is True


class TestTokensIssuedBeforeTheListExisted:
    def test_the_joined_string_is_split_as_a_fallback(self):
        token = TokenData(_payload(role="manager,cashier"))
        assert token.roles == ["manager", "cashier"]
        assert token.has_role("manager") is True

    def test_a_single_legacy_role_still_resolves(self):
        token = TokenData(_payload(role="manager"))
        assert token.roles == ["manager"]

    def test_surrounding_whitespace_is_ignored(self):
        token = TokenData(_payload(role="manager, cashier"))
        assert token.has_role("cashier") is True

    def test_no_roles_at_all_is_empty_rather_than_a_blank_name(self):
        token = TokenData(_payload(role=""))
        assert token.roles == []


class TestRoleChangeInvalidatesTheAuthCache:
    """``sf:perms`` authorises requests and ``sm:role_rank`` feeds the owner
    gates. Both are cached for minutes, so a role change that leaves them alone
    is a revoked permission that keeps working."""

    async def test_both_caches_are_dropped_for_the_user(self):
        from app.common import bridge

        bust = AsyncMock()
        cache_del = AsyncMock()
        user_id = uuid4()

        with (
            patch("app.common.auth_service.bust_perms", bust),
            patch("app.core.redis_client.cache_del", cache_del),
        ):
            await bridge._invalidate_user_auth_caches(user_id)

        bust.assert_awaited_once_with(str(user_id))
        assert cache_del.await_args.args[0] == f"sm:role_rank:{user_id}"

    async def test_removing_a_role_drops_the_cache(self):
        from app.common import bridge

        user_id, tenant_id = uuid4(), str(uuid4())
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.commit = AsyncMock()
        invalidated = AsyncMock()

        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.identity.repository.get_user_by_id",
                AsyncMock(return_value=MagicMock(tenant_id=tenant_id)),
            ),
            patch(
                "app.identity.repository.get_role_by_name_for_tenant",
                AsyncMock(return_value=MagicMock(id=uuid4())),
            ),
            patch("app.identity.repository.remove_role_from_user", AsyncMock()),
            patch("app.common.cache.cache.delete_pattern", AsyncMock()),
            patch.object(bridge, "_invalidate_user_auth_caches", invalidated),
        ):
            result = await bridge.remove_role(tenant_id, str(user_id), "manager")

        assert result["removed"] is True
        invalidated.assert_awaited_once()

    async def test_assigning_a_role_drops_the_cache(self):
        """Otherwise a new role grants nothing until the cache expires."""
        from app.common import bridge

        user_id, tenant_id = uuid4(), str(uuid4())
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.commit = AsyncMock()
        session.add = MagicMock()
        invalidated = AsyncMock()

        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.identity.repository.get_user_by_id",
                AsyncMock(return_value=MagicMock(tenant_id=tenant_id)),
            ),
            patch(
                "app.identity.repository.get_role_by_name_for_tenant",
                AsyncMock(return_value=MagicMock(id=uuid4())),
            ),
            patch(
                "app.identity.repository.get_user_roles", AsyncMock(return_value=[])
            ),
            patch("app.identity.repository.assign_role_to_user", AsyncMock()),
            patch("app.common.cache.cache.delete_pattern", AsyncMock()),
            patch.object(bridge, "_invalidate_user_auth_caches", invalidated),
        ):
            result = await bridge.assign_role(tenant_id, str(user_id), "manager")

        assert result["role"] == "manager"
        invalidated.assert_awaited_once()