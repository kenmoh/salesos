"""Platform-side tenant hard delete.

The delete must confirm with the tenant's business_name, sweep every
tenant-scoped table, and revoke member sessions.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.platform.routes import DeleteTenantRequest, delete_tenant


def _rows_result(rows):
    """A session.execute(...) result whose .scalars().all() yields `rows`."""
    return MagicMock(
        scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=rows))
        )
    )


def _tenant():
    tenant = MagicMock()
    tenant.id = str(uuid4())
    tenant.business_name = "Ked Mart"
    tenant.owner_email = "owner@kedmart.ng"
    return tenant


def _ctx(tenant):
    ctx = MagicMock()
    ctx.user.admin_id = str(uuid4())
    ctx.session.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=tenant)),  # tenant fetch
            _rows_result(["u1", "u2"]),  # member ids
        ]
    )
    ctx.session.flush = AsyncMock()
    ctx.session.delete = AsyncMock()
    return ctx


class TestPlatformDelete:
    async def test_confirmation_must_match_business_name(self):
        tenant = _tenant()
        ctx = _ctx(tenant)
        with pytest.raises(HTTPException) as exc_info:
            await delete_tenant(tenant.id, DeleteTenantRequest(business_name="Other Mart"), ctx)
        assert exc_info.value.status_code == 400
        ctx.session.flush.assert_not_awaited()

    async def test_missing_tenant_404(self):
        ctx = MagicMock()
        ctx.session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        with pytest.raises(HTTPException) as exc_info:
            await delete_tenant("nope", DeleteTenantRequest(business_name="X"), ctx)
        assert exc_info.value.status_code == 404

    async def test_delete_sweeps_and_revokes_sessions(self):
        tenant = _tenant()
        ctx = _ctx(tenant)

        with (
            patch("app.platform.audit.log_platform_audit", new_callable=AsyncMock) as audit,
            patch("app.common.db.session.set_rls_context", new_callable=AsyncMock) as rls,
            patch("app.common.tenant_sweep.sweep_tenant_data", new_callable=AsyncMock) as sweep,
            patch("app.core.security.revoke_all_sessions", new_callable=AsyncMock) as revoke,
        ):
            sweep.return_value = 37
            result = await delete_tenant(
                tenant.id, DeleteTenantRequest(business_name="  Ked Mart  "), ctx
            )

        sweep.assert_awaited_once()
        assert result["swept_tables"] == 37
        rls.assert_awaited_once_with(ctx.session, ctx.user.admin_id, tenant.id, "owner")
        assert revoke.await_count == 2
        revoke.assert_any_await("u1")
        revoke.assert_any_await("u2")
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "tenant.delete"

    async def test_confirmation_is_case_insensitive(self):
        tenant = _tenant()
        ctx = _ctx(tenant)
        with (
            patch("app.platform.audit.log_platform_audit", new_callable=AsyncMock),
            patch("app.common.db.session.set_rls_context", new_callable=AsyncMock),
            patch("app.common.tenant_sweep.sweep_tenant_data", new_callable=AsyncMock,
                  return_value=12) as sweep,
            patch("app.core.security.revoke_all_sessions", new_callable=AsyncMock),
        ):
            result = await delete_tenant(
                tenant.id, DeleteTenantRequest(business_name="ked MART"), ctx
            )
        assert result["success"] is True
