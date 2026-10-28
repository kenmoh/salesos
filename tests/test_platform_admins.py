"""Platform admin management and 2FA.

Covers the admins CRUD surface and the TOTP second factor on the
platform login (setup → verify → graceful "requires_totp" login).
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.platform.auth import (
    PlatformAuthError,
    PlatformTotpRequired,
    platform_login,
    platform_totp_disable,
    platform_totp_enable,
    platform_totp_setup,
)


def _admin(**overrides):
    admin = MagicMock()
    admin.id = str(uuid4())
    admin.email = "ops@storeflow.ng"
    admin.full_name = "Ops Admin"
    admin.role = "admin"
    admin.status = "active"
    admin.password_hash = "hashed"
    admin.totp_secret = None
    admin.totp_enabled = False
    admin.last_login_at = None
    for key, value in overrides.items():
        setattr(admin, key, value)
    return admin


def _auth_patches(admin, verify_totp_result=True):
    """Everything platform_login touches outside the DB."""
    return [
        patch("app.platform.auth._get_admin_by_email", AsyncMock(return_value=admin)),
        patch("app.platform.auth.verify_password", MagicMock(return_value=True)),
        patch("app.platform.auth.create_access_token", MagicMock(return_value=("AT", "keep"))),
        patch(
            "app.core.security.create_refresh_token", MagicMock(return_value=("RT", "keep"))
        ),
        patch("app.platform.auth.generate_session_id", MagicMock(return_value="sid-1")),
        patch("app.platform.auth.hash_token", MagicMock(return_value="h")),
        patch("app.platform.auth.single_sign_on_device", AsyncMock()),
        patch("app.platform.auth.store_refresh_session", AsyncMock()),
        patch("app.platform.auth.verify_totp", MagicMock(return_value=verify_totp_result)),
    ]


async def _run_login(totp_code=None, verify_totp_result=True, **admin_overrides):
    session = AsyncMock()
    admin = _admin(**admin_overrides)
    req = MagicMock()
    req.headers.get = MagicMock(return_value="pytest-agent")
    with contextlib.ExitStack() as stack:
        for p in _auth_patches(admin, verify_totp_result):
            stack.enter_context(p)
        return await platform_login(
            session=session, email=admin.email, password="pw", req=req, totp_code=totp_code
        )


class TestPlatformLogin2FA:
    async def test_plain_admin_gets_tokens(self):
        result = await _run_login()
        assert result["tokens"]["access_token"] == "AT"
        assert result["admin"]["email"] == _admin().email

    async def test_totp_admin_without_code_is_challenged(self):
        with pytest.raises(PlatformTotpRequired):
            await _run_login(totp_enabled=True)

    async def test_totp_admin_with_wrong_code_rejected(self):
        with pytest.raises(PlatformAuthError, match="Invalid TOTP"):
            await _run_login(totp_code="000000", totp_enabled=True, verify_totp_result=False)

    async def test_totp_admin_with_valid_code_gets_tokens(self):
        result = await _run_login(totp_code="123456", totp_enabled=True, totp_secret="S")
        assert result["tokens"]["access_token"] == "AT"


class TestTotpLifecycle:
    async def _fetched(self, admin):
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one=MagicMock(return_value=admin))
        )
        return session

    async def test_setup_generates_secret_and_uri(self):
        admin = _admin()
        session = await self._fetched(admin)
        with (
            patch("app.platform.auth.generate_totp_secret", MagicMock(return_value="SECRET")),
            patch(
                "app.platform.auth.get_totp_uri", MagicMock(return_value="otpauth://totp/x")
            ),
        ):
            result = await platform_totp_setup(session=session, admin_id=str(admin.id))
        assert result["provisioning_uri"] == "otpauth://totp/x"
        assert result["secret"] == "SECRET"
        assert admin.totp_secret == "SECRET"
        assert admin.totp_enabled is False

    async def test_enable_with_bad_code_fails(self):
        admin = _admin(totp_secret="S")
        session = await self._fetched(admin)
        with (
            patch("app.platform.auth.verify_totp", MagicMock(return_value=False)),
            pytest.raises(PlatformAuthError),
        ):
            await platform_totp_enable(session=session, admin_id=str(admin.id), code="999999")
        assert admin.totp_enabled is False

    async def test_disable_requires_password_and_code(self):
        admin = _admin(totp_enabled=True, totp_secret="S")
        session = await self._fetched(admin)
        with (
            patch("app.platform.auth.verify_password", MagicMock(return_value=False)),
            pytest.raises(PlatformAuthError),
        ):
            await platform_totp_disable(
                session=session, admin_id=str(admin.id), password="wrong", code="000000"
            )
        with (
            patch("app.platform.auth.verify_password", MagicMock(return_value=True)),
            patch("app.platform.auth.verify_totp", MagicMock(return_value=True)),
        ):
            await platform_totp_disable(
                session=session, admin_id=str(admin.id), password="right", code="123456"
            )
        assert admin.totp_enabled is False
        assert admin.totp_secret is None


# ── HTTP surface ─────────────────────────────────────────────────


def _platform_app():
    app_ = FastAPI()
    from app.platform import routes as platform_router

    app_.include_router(platform_router.router)
    return app_


def _platform_token_data(admin_id: str):
    from app.core.platform_deps import PlatformUserData

    return PlatformUserData(
        {
            "sub": admin_id,
            "bid": "platform",
            "role": "admin",
            "perms": ["platform:*"],
            "jti": str(uuid4()),
            "exp": 9999999999,
            "type": "access",
        }
    )


def _exec_result(admin, scalar_one=True):
    """A session.execute(...) result handing back the given admin row."""
    return MagicMock(
        scalar_one=MagicMock(return_value=admin),
        scalar_one_or_none=MagicMock(return_value=admin),
        scalar=MagicMock(return_value=None if not scalar_one else admin),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
    )


def _mock_session(query_result=None):
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.begin.return_value.__aenter__.return_value = session
    session.begin.return_value.__aexit__.return_value = None
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        return_value=query_result
        or MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=0),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )
    )
    return session


@pytest.fixture
def admin_env():
    """Platform context wired with a controllable session and admin id."""
    admin_id = str(uuid4())
    session = _mock_session()
    from app.core.platform_deps import PlatformContext, get_platform_context

    ctx = PlatformContext(user=_platform_token_data(admin_id), session=session)

    application = _platform_app()

    async def override_platform():
        yield ctx

    application.dependency_overrides[get_platform_context] = override_platform
    return application, session, admin_id


@pytest.fixture
async def admin_client(admin_env):
    application, _session, _admin_id = admin_env
    with patch("app.platform.audit.log_platform_audit", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            yield ac


class TestAdminsHTTP:
    ROUTE = "/platform/admins"

    async def test_create_admin(self, admin_env, admin_client):
        _, session, _admin_id = admin_env
        resp = await admin_client.post(
            self.ROUTE,
            json={"email": "new@sos.ng", "full_name": "New Admin", "password": "longenough1"},
        )
        assert resp.status_code == 201, resp.text
        added = session.add.call_args.args[0]
        assert added.email == "new@sos.ng"
        assert added.role == "admin"

    async def test_create_admin_rejects_short_password(self, admin_env, admin_client):
        resp = await admin_client.post(
            self.ROUTE, json={"email": "a@b.ng", "full_name": "A", "password": "short"}
        )
        assert resp.status_code == 400

    async def test_create_admin_rejects_bad_role(self, admin_env, admin_client):
        resp = await admin_client.post(
            self.ROUTE,
            json={"email": "a@b.ng", "full_name": "A", "password": "longenough1", "role": "boss"},
        )
        assert resp.status_code == 400

    async def test_create_admin_conflicts_on_dup_email(self, admin_env, admin_client):
        _, session, _admin_id = admin_env
        session.scalar = AsyncMock(return_value="x")
        resp = await admin_client.post(
            self.ROUTE, json={"email": "dupe@sos.ng", "full_name": "D", "password": "longenough1"}
        )
        assert resp.status_code == 409

    async def test_cannot_disable_yourself(self, admin_env, admin_client):
        _, session, admin_id = admin_env
        my_admin = _admin()
        my_admin.id = admin_id
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=my_admin))
        )
        resp = await admin_client.patch(f"{self.ROUTE}/{admin_id}", json={"status": "disabled"})
        assert resp.status_code == 400

    async def test_disable_revokes_sessions(self, admin_env, admin_client):
        _, session, _admin_id = admin_env
        victim = _admin()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=victim))
        )
        with patch("app.core.security.revoke_all_sessions", AsyncMock()) as revoke:
            resp = await admin_client.patch(
                f"{self.ROUTE}/{victim.id}", json={"status": "disabled"}
            )
        assert resp.status_code == 200, resp.text
        revoke.assert_awaited_once_with(victim.id)
        assert victim.status == "disabled"
        assert victim.role == "admin"
