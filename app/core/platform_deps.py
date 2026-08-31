from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, is_token_blacklisted
from app.platform.models import PlatformAdmin

bearer = HTTPBearer(auto_error=False)


class PlatformUserData:
    def __init__(self, payload: dict):
        self.admin_id = payload["sub"]
        self.role = payload["role"]
        self.permissions: list[str] = payload.get("perms", [])
        self.jti = payload["jti"]
        self.exp = payload["exp"]

    def has_perm(self, perm: str) -> bool:
        if "platform:*" in self.permissions:
            return True
        return perm in self.permissions


class PlatformContext:
    def __init__(self, user: PlatformUserData, session: AsyncSession):
        self.user = user
        self.session = session


async def get_platform_context(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AsyncGenerator[PlatformContext, None]:
    unauth = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Platform authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not creds:
        raise unauth
    try:
        payload = decode_access_token(creds.credentials)
    except ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise unauth

    if payload.get("type") != "access" or payload.get("bid") != "platform":
        raise unauth
    if await is_token_blacklisted(payload.get("jti", "")):
        raise unauth

    user = PlatformUserData(payload)
    request.state.admin_id = user.admin_id

    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(PlatformAdmin).where(PlatformAdmin.id == user.admin_id)
            )
            admin = result.scalar_one_or_none()
            if not admin or not admin.is_active:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "Platform admin account is inactive or not found"
                )

            yield PlatformContext(user, session)


PlatformDep = Annotated[PlatformContext, Depends(get_platform_context)]


def require_platform_permission(perm: str):
    async def guard(ctx: PlatformDep):
        if not ctx.user.has_perm(perm):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Permission denied: '{perm}'",
            )

    return guard
