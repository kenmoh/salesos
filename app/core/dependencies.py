from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_get, cache_set
from app.core.security import decode_access_token, is_token_blacklisted
from app.common.db.session import clear_rls_context, set_rls_context
from app.common.db.engine import set_current_tenant, reset_current_tenant
from app.identity.models import Role, UserRole

bearer = HTTPBearer(auto_error=False)


async def get_cached_role_rank(session: AsyncSession, user_id: str) -> int:
    key = f"sm:role_rank:{user_id}"
    cached = await cache_get(key)
    if cached is not None:
        return int(cached)
    result = await session.execute(
        select(Role.rank)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == UUID(user_id))
        .order_by(Role.rank.desc())
        .limit(1)
    )
    rank = result.scalar_one_or_none() or 0
    await cache_set(key, str(rank), 300)
    return rank


class TokenData:
    def __init__(self, payload: dict):
        self.user_id = payload["sub"]
        self.business_id = payload["bid"]
        self.role = payload["role"]
        self.permissions: list[str] = payload.get("perms", [])
        self.jti = payload["jti"]
        self.exp = payload["exp"]

    def has_perm(self, perm: str) -> bool:
        return perm in self.permissions

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    async def min_role(self, session: AsyncSession, role: str) -> bool:
        max_rank = await get_cached_role_rank(session, self.user_id)
        rank_map = {
            "super_admin": 100,
            "developer": 90,
            "admin": 85,
            "moderator": 75,
            "auditor": 70,
            "owner": 80,
            "manager": 60,
            "cashier": 40,
            "viewer": 20,
        }
        return max_rank >= rank_map.get(role, 999)


class TenantContext:
    def __init__(self, user: TokenData, session: AsyncSession):
        self.user = user
        self.session = session


def _get_session_factory():
    """Return a session factory using the shared engine."""
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker
    engine = _get_shared_engine()
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = _get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session


async def get_tenant_context(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AsyncGenerator[TenantContext, None]:
    unauth = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not creds:
        raise unauth
    try:
        payload = decode_access_token(creds.credentials)
    except ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Token expired", headers={"WWW-Authenticate": "Bearer"}
        )
    except JWTError:
        raise unauth
    if payload.get("type") != "access" or await is_token_blacklisted(payload.get("jti", "")):
        raise unauth
    user = TokenData(payload)
    request.state.user_id = user.user_id
    request.state.business_id = user.business_id
    tokens = set_current_tenant(user.user_id, user.business_id, user.role)
    factory = _get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_rls_context(session, user.user_id, user.business_id, user.role)
            try:
                yield TenantContext(user, session)
            finally:
                await clear_rls_context(session)
                reset_current_tenant(tokens)


TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


async def _current_user(ctx: TenantDep) -> TokenData:
    return ctx.user


CurrentUser = Annotated[TokenData, Depends(_current_user)]


def require_permission(perm: str):
    async def guard(ctx: TenantDep):
        if not ctx.user.has_perm(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Permission denied: '{perm}'")

    return guard


def require_role(*roles: str):
    async def guard(ctx: TenantDep):
        if not ctx.user.has_role(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role required: {' or '.join(roles)}")

    return guard


def require_min_role(minimum: str):
    async def guard(ctx: TenantDep):
        if not await ctx.user.min_role(ctx.session, minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Minimum role: {minimum} or higher")

    return guard


async def require_owner(ctx: TenantDep) -> TenantContext:
    max_rank = await get_cached_role_rank(ctx.session, ctx.user.user_id)
    rank_map = {"super_admin": 100, "owner": 80}
    if max_rank < rank_map.get("owner", 999):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner or higher required")
    return ctx
