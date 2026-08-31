import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select

from app.core.config import settings
from app.core.redis_client import cache_del, cache_get, cache_set
from app.core.security import (
    blacklist_token,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    generate_reset_token,
    generate_session_id,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    hash_token,
    revoke_all_sessions,
    revoke_session,
    seconds_until,
    store_refresh_session,
    verify_password,
    verify_session_hash,
    verify_totp,
)
from app.identity.models import User, Role, UserRole, Permission, RolePermission, AuthAuditLog
from app.identity.repository import log_auth_event


class AuthError(Exception):
    def __init__(self, msg: str, code: str = "auth_error"):
        self.message = msg
        self.code = code
        super().__init__(msg)


class TotpRequired(Exception):
    pass


def _ip(req: Request) -> str:
    forwarded = req.headers.get("x-forwarded-for")
    return (
        forwarded.split(",")[0].strip()
        if forwarded
        else (str(req.client.host) if req.client else "0.0.0.0")
    )


async def _get_user_by_email(session, email: str) -> dict | None:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        return None
    return {
        "user_id": str(user.id),
        "business_id": str(user.tenant_id),
        "email": user.email,
        "full_name": user.full_name,
        "password_hash": user.password_hash,
        "status": user.status,
        "totp_enabled": bool(user.totp_enabled),
        "totp_secret": user.totp_secret,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "avatar_url": user.avatar_url,
    }


async def _get_perms(user_id: str, session) -> list[str]:
    key = f"sf:perms:{user_id}"
    cached = await cache_get(key)
    if cached:
        return json.loads(cached)
    result = await session.execute(
        select(Permission.name)
        .distinct()
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == UUID(user_id))
    )
    perms = [row[0] for row in result.all()]
    await cache_set(key, json.dumps(perms), 300)
    return perms


async def _get_role_names(user_id: str, session) -> str:
    result = await session.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == UUID(user_id))
        .order_by(Role.rank.desc())
    )
    roles = [row[0] for row in result.all()]
    return ",".join(roles) if roles else "viewer"


async def bust_perms(user_id: str):
    await cache_del(f"sf:perms:{user_id}")


async def login(*, session, email, password, totp_code, req, device_name=None):
    user = await _get_user_by_email(session, email)
    if not user:
        raise AuthError("Invalid email or password", "invalid_credentials")
    if not verify_password(password, user["password_hash"]):
        await log_auth_event(
            session,
            AuthAuditLog(
                user_id=user["user_id"],
                tenant_id=user["business_id"],
                action="login_failed",
                ip_address=_ip(req),
                user_agent=req.headers.get("user-agent", ""),
                details={"reason": "invalid_password"},
            ),
        )
        raise AuthError("Invalid email or password", "invalid_credentials")
    if user.get("totp_enabled"):
        if not totp_code:
            raise TotpRequired()
        if not verify_totp(user.get("totp_secret"), totp_code):
            await log_auth_event(
                session,
                AuthAuditLog(
                    user_id=user["user_id"],
                    tenant_id=user["business_id"],
                    action="login_failed",
                    ip_address=_ip(req),
                    user_agent=req.headers.get("user-agent", ""),
                    details={"reason": "invalid_totp"},
                ),
            )
            raise AuthError("Invalid TOTP code", "invalid_totp")
    perms = await _get_perms(user["user_id"], session)
    role_names = await _get_role_names(user["user_id"], session)
    access, _ = create_access_token(user["user_id"], user["business_id"], role_names, perms)
    sid = generate_session_id()
    refresh, _ = create_refresh_token(user["user_id"], user["business_id"], sid)
    await store_refresh_session(
        user["user_id"],
        sid,
        hash_token(refresh),
        {},
        settings.refresh_token_expire_days * 86400,
    )
    # Update last_login_at
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as upd_session:
        async with upd_session.begin():
            result = await upd_session.execute(select(User).where(User.id == UUID(user["user_id"])))
            db_user = result.scalar_one()
            db_user.last_login_at = datetime.now(UTC)
    # Log successful login
    await log_auth_event(
        session,
        AuthAuditLog(
            user_id=user["user_id"],
            tenant_id=user["business_id"],
            action="login_success",
            ip_address=_ip(req),
            user_agent=req.headers.get("user-agent", ""),
        ),
    )
    return {"access_token": access, "refresh_token": refresh}, {
        "user_id": user["user_id"],
        "business_id": user["business_id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": role_names,
        "status": user.get("status", "active"),
        "permissions": perms,
        "totp_enabled": bool(user.get("totp_enabled")),
        "last_login_at": user.get("last_login_at"),
        "avatar_url": user.get("avatar_url"),
    }


async def refresh_tokens(*, session, raw_token, req):
    try:
        payload = decode_refresh_token(raw_token)
    except ExpiredSignatureError:
        raise AuthError("Refresh token expired", "token_expired")
    except JWTError:
        raise AuthError("Invalid refresh token", "invalid_token")
    if not await verify_session_hash(payload["sid"], raw_token):
        await revoke_all_sessions(payload["sub"])
        raise AuthError("Token reuse detected. All sessions revoked.", "token_reuse")
    new_sid = generate_session_id()
    new_refresh, _ = create_refresh_token(payload["sub"], payload["bid"], new_sid)
    await revoke_session(payload["sid"])
    await store_refresh_session(
        payload["sub"],
        new_sid,
        hash_token(new_refresh),
        {},
        settings.refresh_token_expire_days * 86400,
    )
    perms = await _get_perms(payload["sub"], session)
    role_names = await _get_role_names(payload["sub"], session)
    new_access, _ = create_access_token(payload["sub"], payload["bid"], role_names, perms)
    return {"access_token": new_access, "refresh_token": new_refresh}


async def logout(*, session, access_token, raw_refresh, user_id, all_devices):
    try:
        payload = decode_access_token(access_token)
        await blacklist_token(
            payload["jti"], seconds_until(datetime.fromtimestamp(payload["exp"], UTC))
        )
    except JWTError:
        pass
    if all_devices:
        await revoke_all_sessions(user_id)
    else:
        try:
            await revoke_session(decode_refresh_token(raw_refresh)["sid"])
        except JWTError:
            pass
    await log_auth_event(
        session,
        AuthAuditLog(
            user_id=user_id,
            action="logout",
            details={"all_devices": all_devices},
        ),
    )


async def change_password(*, session, user_id, cur_pw, new_pw, revoke_others, cur_sid, req):
    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not verify_password(cur_pw, user.password_hash):
        raise AuthError("Current password is incorrect", "invalid_password")
    user.password_hash = hash_password(new_pw)
    await session.flush()
    if revoke_others:
        await revoke_all_sessions(user_id)
    await bust_perms(user_id)
    await log_auth_event(
        session,
        AuthAuditLog(
            user_id=user_id,
            action="password_changed",
            ip_address=_ip(req),
            user_agent=req.headers.get("user-agent", ""),
            details={"revoked_other_sessions": revoke_others},
        ),
    )


async def forgot_password(*, session, email, req):
    user = await _get_user_by_email(session, email)
    if not user:
        return
    from app.identity.models import PasswordResetToken

    token = PasswordResetToken(
        user_id=UUID(user["user_id"]),
        token_hash=hash_token(generate_reset_token()),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        ip_address=_ip(req),
    )
    session.add(token)
    await session.flush()
    await log_auth_event(
        session,
        AuthAuditLog(
            user_id=user["user_id"],
            tenant_id=user["business_id"],
            action="password_reset_requested",
            ip_address=_ip(req),
            user_agent=req.headers.get("user-agent", ""),
        ),
    )


async def complete_reset(*, session, token, new_pw, req):
    from app.identity.models import PasswordResetToken

    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(token),
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    prt = result.scalar_one_or_none()
    if not prt:
        return False
    user_result = await session.execute(select(User).where(User.id == prt.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return False
    user.password_hash = hash_password(new_pw)
    await session.delete(prt)
    await session.flush()
    await bust_perms(str(user.id))
    await log_auth_event(
        session,
        AuthAuditLog(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            action="password_reset_completed",
            ip_address=_ip(req),
            user_agent=req.headers.get("user-agent", ""),
        ),
    )
    return True


async def setup_totp(*, session, user_id, email):
    secret = generate_totp_secret()
    await cache_set(f"sf:totp_pending:{user_id}", secret, 600)
    return {"secret": secret, "qr_uri": get_totp_uri(secret, email)}


async def enable_totp(*, session, user_id, code):
    secret = await cache_get(f"sf:totp_pending:{user_id}")
    if not secret or not verify_totp(secret, code):
        raise AuthError("Invalid TOTP code", "invalid_totp")
    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one()
    user.totp_secret = secret
    user.totp_enabled = 1
    await session.flush()
    await cache_del(f"sf:totp_pending:{user_id}")


async def disable_totp(*, session, user_id, password, code):
    result = await session.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("Invalid password", "invalid_password")
    user.totp_secret = None
    user.totp_enabled = 0
    await session.flush()


async def create_employee(
    *, session, business_id, actor_id, email, full_name, phone, role, password, req
):
    from app.core.security import hash_password as hp
    from app.identity.models import User as IdUser

    existing = await session.execute(select(IdUser).where(IdUser.email == email))
    if existing.scalar_one_or_none():
        raise AuthError("A user with this email already exists", "duplicate_email")

    user = IdUser(
        tenant_id=UUID(business_id),
        email=email,
        password_hash=hp(password or secrets.token_urlsafe(16)),
        full_name=full_name,
        phone=phone,
        status="active",
    )
    session.add(user)
    await session.flush()

    role_result = await session.execute(
        select(Role).where(
            Role.name == role,
            (Role.tenant_id == UUID(business_id)) | (Role.tenant_id.is_(None)),
        )
    )
    role_obj = role_result.scalar_one_or_none()
    if role_obj:
        ur = UserRole(
            user_id=user.id, role_id=role_obj.id, assigned_by=UUID(actor_id) if actor_id else None
        )
        session.add(ur)
        await session.flush()

    return {
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": role,
    }
