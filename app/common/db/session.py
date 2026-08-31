from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_timeout=60,
    echo=settings.db_echo,
    future=True,
    connect_args={
        "server_settings": {"timezone": "UTC", "application_name": "storeflow_api"},
        "command_timeout": 60,
    },
)

SessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def set_rls_context(
    session: AsyncSession, user_id: str, business_id: str, role: str = ""
) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.user_id',:u,true),"
            " set_config('app.business_id',:b,true),"
            " set_config('app.role',:r,true)"
        ),
        {"u": user_id, "b": business_id, "r": role},
    )


async def clear_rls_context(session: AsyncSession) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.user_id','',true),"
            " set_config('app.business_id','',true),"
            " set_config('app.role','',true)"
        )
    )


@asynccontextmanager
async def tenant_session(
    user_id: str, business_id: str, role: str = ""
) -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        async with session.begin():
            await set_rls_context(session, user_id, business_id, role)
            try:
                yield session
            finally:
                await clear_rls_context(session)


@asynccontextmanager
async def admin_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        async with session.begin():
            yield session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        async with session.begin():
            yield session
