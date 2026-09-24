from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import cache_get, cache_set
from app.core.security import decode_access_token, is_token_blacklisted
from app.common.db.session import clear_rls_context, set_rls_context
from app.common.db.engine import set_current_tenant, reset_current_tenant
from app.identity.models import Role, UserRole

bearer = HTTPBearer(auto_error=False)


async def get_cached_role_rank(
    user_id: str, business_id: str, session: AsyncSession | None = None
) -> int:
    """Return the user's highest role rank, cached in Redis for 5 minutes.

    Pass ``session`` to reuse an existing one; otherwise a short-lived session
    is opened only on a cache miss. Tenant GUCs are always set — ``roles`` is
    FORCE ROW LEVEL SECURITY, so the lookup needs the user's business context.
    """
    key = f"sm:role_rank:{user_id}"
    cached = await cache_get(key)
    if cached is not None:
        return int(cached)

    async def _lookup(s: AsyncSession) -> int:
        result = await s.execute(
            select(Role.rank)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == UUID(user_id))
            .order_by(Role.rank.desc())
            .limit(1)
        )
        rank = result.scalar_one_or_none() or 0
        await cache_set(key, str(rank), 300)
        return rank

    if session is not None:
        return await _lookup(session)

    factory = _get_session_factory()
    for attempt in range(2):
        try:
            async with factory() as s:
                async with s.begin():
                    await set_rls_context(s, user_id, business_id, "")
                    return await _lookup(s)
        except DBAPIError as exc:
            if attempt == 1 or not exc.connection_invalidated:
                raise
            # Stale pooled connection killed server-side by Neon; the read
            # below is side-effect free, so retry once on a fresh checkout.


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

    async def min_role(self, session: AsyncSession | None = None, role: str = "") -> bool:
        max_rank = await get_cached_role_rank(self.user_id, self.business_id, session)
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
    """Per-request auth context.

    ``session`` is populated only when the route depends on
    ``DbTenantDep``/``get_tenant_db_context``. Session-less routes (the common
    case) never open a DB connection.
    """

    def __init__(self, user: TokenData, session: AsyncSession | None = None):
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


async def _authenticate(
    creds: HTTPAuthorizationCredentials | None,
    unauth: HTTPException,
) -> TokenData:
    """Shared token validation for both tenant dependencies."""
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
    return TokenData(payload)


async def get_tenant_context(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AsyncGenerator[TenantContext, None]:
    """Authenticate the request WITHOUT opening a DB session.

    Sets request.state (consumed by the audit middleware) and the tenant
    ContextVars (consumed by ServiceDatabase.session() when bridge code runs).
    Use ``DbTenantDep`` instead when the route body needs ``ctx.session``.
    """
    unauth = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = await _authenticate(creds, unauth)
    request.state.user_id = user.user_id
    request.state.business_id = user.business_id
    tokens = set_current_tenant(user.user_id, user.business_id, user.role)
    try:
        yield TenantContext(user)
    finally:
        reset_current_tenant(tokens)


async def get_tenant_db_context(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AsyncGenerator[TenantContext, None]:
    """Same as ``get_tenant_context`` plus a request-scoped DB session.

    Mirrors the historical lifecycle exactly: session with an open transaction,
    tenant GUCs set (required by FORCE ROW LEVEL SECURITY), committed/closed
    after the response. Tenant GUCs are transaction-local, so no explicit
    clear step is needed.
    """
    unauth = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = await _authenticate(creds, unauth)
    request.state.user_id = user.user_id
    request.state.business_id = user.business_id
    tokens = set_current_tenant(user.user_id, user.business_id, user.role)
    try:
        factory = _get_session_factory()
        reached_route = False
        for attempt in range(2):
            try:
                async with factory() as session:
                    async with session.begin():
                        await set_rls_context(session, user.user_id, user.business_id, user.role)
                        reached_route = True
                        try:
                            yield TenantContext(user, session)
                        finally:
                            await clear_rls_context(session)
                break
            except DBAPIError as exc:
                if reached_route or attempt == 1 or not exc.connection_invalidated:
                    raise
                # set_config hit a pooled connection Neon had already closed
                # server-side, before the route ran. The pool discards the
                # dead connection, so retry once with a fresh session. Never
                # retried after yield: the route must not run twice.
                reached_route = False
                continue
    finally:
        reset_current_tenant(tokens)


TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
DbTenantDep = Annotated[TenantContext, Depends(get_tenant_db_context)]


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
        if not await ctx.user.min_role(role=minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Minimum role: {minimum} or higher")

    return guard


async def require_owner(ctx: TenantDep) -> TenantContext:
    max_rank = await get_cached_role_rank(ctx.user.user_id, ctx.user.business_id)
    rank_map = {"super_admin": 100, "owner": 80}
    if max_rank < rank_map.get("owner", 999):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner or higher required")
    return ctx
