from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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
