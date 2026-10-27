"""Suspension enforcement for tenants.

Covers: platform PATCH suspend cascades to employees + session revocation,
reinstatement, login-side rejection, and the per-request tenant-status gate.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.common.auth_service as auth_service
from app.core.dependencies import assert_tenant_active


def _session_result(scalars_rows=None, scalar_value=None, rowcount=0):
    scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=scalars_rows or []))
    )
    return MagicMock(
        scalars=scalars, scalar=MagicMock(return_value=scalar_value), rowcount=rowcount
    )


async def _suspend_via_route(tenant_id: str, *, initial_status="active", to_status="suspended"):
    """Drive platform update_tenant with light mocks; returns (details, changes)."""
    from app.platform.routes import update_tenant, TenantUpdateRequest

    tenant = MagicMock()
    tenant.id = tenant_id
    tenant.status = initial_status
    tenant.tier = "starter"
    tenant.business_name = "Ked Mart"
    tenant.slug = "ked-mart"

    ctx = MagicMock()
    ctx.session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=tenant))
    )
    ctx.session.flush = AsyncMock()
    ctx.session.add = MagicMock()

    with (
        patch("app.platform.audit.log_platform_audit", new_callable=AsyncMock) as audit,
        patch("app.platform.routes._suspend_tenant_users", AsyncMock(return_value=5)) as suspend,
        patch("app.platform.routes._reinstate_tenant_users", AsyncMock(return_value=5)),
    ):
        await update_tenant(tenant_id, TenantUpdateRequest(status=to_status), ctx)

    audit.assert_awaited_once()
    return audit.await_args.kwargs["details"], suspend


class TestSuspendCascades:
    async def test_suspend_suspends_users_and_revokes_sessions(self):
        details, suspend = await _suspend_via_route("t-1")
        suspend.assert_awaited_once()
        assert details["status"] == {"from": "active", "to": "suspended"}
        assert details["users_suspended"] == 5

    async def test_reinstate_reactivates(self):
        details, _ = await _suspend_via_route(
            "t-1", initial_status="suspended", to_status="active"
        )
        assert details["status"] == {"from": "suspended", "to": "active"}

    async def test_suspend_tenant_users_helper_updates_and_revokes(self):
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                _session_result(rowcount=3),
                _session_result(scalars_rows=[str(uuid4()), str(uuid4())]),
            ]
        )
        from app.platform.routes import _suspend_tenant_users

        with patch("app.core.security.revoke_all_sessions", AsyncMock()) as revoke:
            count = await _suspend_tenant_users(session, "tenant-xyz")
        assert count == 3
        assert revoke.await_count == 2


class TestLoginBlock:
    async def test_suspended_tenant_cannot_login(self):
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        session.scalar = AsyncMock(return_value="suspended")
        user = {
            "user_id": str(uuid4()),
            "business_id": str(uuid4()),
            "email": "o@b.ng",
            "full_name": "O",
            "password_hash": "h",
            "status": "active",
            "totp_enabled": False,
            "auto_create_cart": False,
            "totp_secret": None,
            "last_login_at": None,
            "avatar_url": None,
        }
        with (
            patch("app.common.auth_service._get_user_by_email", AsyncMock(return_value=user)),
            pytest.raises(auth_service.AuthError) as exc_info,
        ):
            await auth_service.login(
                session=session, email=user["email"], password="pw", totp_code=None, req=MagicMock()
            )
        assert exc_info.value.code == "tenant_suspended"


class TestRequestGate:
    async def test_suspended_tenant_is_rejected(self, monkeypatch):
        from app.core import dependencies

        factory = MagicMock()

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def scalar(self, query):
                return "suspended"

        factory.return_value = _Session()
        monkeypatch.setattr(dependencies, "_get_session_factory", lambda: factory)

        with pytest.raises(HTTPException) as exc_info:
            await assert_tenant_active(str(uuid4()))
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == "tenant_suspended"

    async def test_active_tenant_passes(self, monkeypatch):
        from app.core import dependencies

        factory = MagicMock()

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def scalar(self, query):
                return "active"

        factory.return_value = _Session()
        monkeypatch.setattr(dependencies, "_get_session_factory", lambda: factory)

        await assert_tenant_active(str(uuid4()))

    async def test_platform_and_anonymous_skipped(self, monkeypatch):
        from app.core import dependencies

        with patch.object(dependencies, "_get_session_factory") as factory:
            await assert_tenant_active(None)
            await assert_tenant_active("platform")
        factory.assert_not_called()
