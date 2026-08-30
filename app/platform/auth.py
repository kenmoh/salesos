from datetime import UTC, datetime
from uuid import UUID

from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.models import PlatformAdmin

from core.config import settings
from core.security import (
    create_access_token,
    decode_access_token,
    generate_session_id,
    hash_token,
    revoke_all_sessions,
    revoke_session,
    store_refresh_session,
    verify_password,
    verify_session_hash,
)
from platform.models import PlatformAdmin


class PlatformAuthError(Exception):
    def __init__(self, msg: str, code: str = "auth_error"):
        self.message = msg
        self.code = code
        super().__init__(msg)


PLATFORM_ROLE_RANK = {"super_admin": 100, "admin": 85, "support": 60, "readonly": 20}


async def _get_admin_by_email(session: AsyncSession, email: str) -> PlatformAdmin | None:
    result = await session.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))
    return result.scalar_one_or_none()


async def platform_login(*, session: AsyncSession, email: str, password: str, req) -> dict:
    admin = await _get_admin_by_email(session, email)
    if not admin:
        raise PlatformAuthError("Invalid email or password")
    if admin.status != "active":
        raise PlatformAuthError("Account is disabled")
    if not verify_password(password, admin.password_hash):
        raise PlatformAuthError("Invalid email or password")

    perms = ["platform:*"]
    role_str = admin.role

    access_token, _ = create_access_token(str(admin.id), "platform", role_str, perms)
    sid = generate_session_id()
    refresh_token, _ = create_access_token(str(admin.id), "platform", role_str, perms)

    from core.security import create_refresh_token

    refresh_token, _ = create_refresh_token(str(admin.id), "platform", sid)
    await store_refresh_session(
        str(admin.id),
        sid,
        hash_token(refresh_token),
        {},
        settings.refresh_token_expire_days * 86400,
    )

    admin.last_login_at = datetime.now(UTC)
    await session.flush()

    return {
        "tokens": {"access_token": access_token, "refresh_token": refresh_token},
        "admin": {
            "id": str(admin.id),
            "email": admin.email,
            "full_name": admin.full_name,
            "role": admin.role,
        },
    }


async def platform_refresh(*, session: AsyncSession, raw_token: str) -> dict:
    try:
        payload = decode_access_token(raw_token)
    except ExpiredSignatureError:
        raise PlatformAuthError("Refresh token expired")
    except JWTError:
        raise PlatformAuthError("Invalid refresh token")

    if not await verify_session_hash(payload["sid"], raw_token):
        await revoke_all_sessions(payload["sub"])
        raise PlatformAuthError("Token reuse detected")

    from core.security import create_refresh_token, generate_session_id

    new_sid = generate_session_id()
    new_refresh, _ = create_refresh_token(payload["sub"], "platform", new_sid)
    await revoke_session(payload["sid"])
    await store_refresh_session(
        payload["sub"],
        new_sid,
        hash_token(new_refresh),
        {},
        settings.refresh_token_expire_days * 86400,
    )

    admin = await session.execute(
        select(PlatformAdmin).where(PlatformAdmin.id == UUID(payload["sub"]))
    )
    admin_obj = admin.scalar_one_or_none()
    role_str = admin_obj.role if admin_obj else "admin"
    perms = ["platform:*"]

    new_access, _ = create_access_token(payload["sub"], "platform", role_str, perms)
    return {"access_token": new_access, "refresh_token": new_refresh}
