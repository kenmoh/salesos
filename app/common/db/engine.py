from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_current_business_id: ContextVar[str] = ContextVar("current_business_id", default="")
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
_current_role: ContextVar[str] = ContextVar("current_role", default="")


def get_current_business_id() -> str:
    return _current_business_id.get()


def set_current_tenant(user_id: str, business_id: str, role: str = "") -> tuple:
    """Set the current tenant context for RLS. Returns tokens for reset."""
    t1 = _current_business_id.set(business_id)
    t2 = _current_user_id.set(user_id)
    t3 = _current_role.set(role)
    return t1, t2, t3


def reset_current_tenant(tokens: tuple) -> None:
    """Reset tenant context using tokens from set_current_tenant."""
    _current_business_id.reset(tokens[0])
    _current_user_id.reset(tokens[1])
    _current_role.reset(tokens[2])


@dataclass(frozen=True)
class ServiceDatabase:
    """Owns the SQLAlchemy async engine/session factory for a single service database.

    All tables live in the public schema. No search_path manipulation needed.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    schema: str = "public"

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        async with self.session_factory() as db:
            biz_id = _current_business_id.get()
            if biz_id:
                await db.execute(
                    text(
                        "SELECT set_config('app.business_id',:b,true),"
                        " set_config('app.user_id',:u,true),"
                        " set_config('app.role',:r,true)"
                    ),
                    {
                        "b": biz_id,
                        "u": _current_user_id.get(),
                        "r": _current_role.get(),
                    },
                )
            yield db


def create_database(
    database_url: str,
    schema: str = "public",
    *,
    echo: bool = False,
    pool_size: int = 20,
    max_overflow: int = 10,
) -> ServiceDatabase:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=60,
        echo=echo,
        future=True,
        connect_args={"command_timeout": 60},
    )
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return ServiceDatabase(engine=engine, session_factory=session_factory, schema=schema)


# Alias for backwards compatibility with worker tasks
create_service_database = create_database
