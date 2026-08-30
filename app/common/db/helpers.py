import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_FN_SAFE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*(?:\(.*\))?$")


async def call(session: AsyncSession, fn: str, **params) -> list[dict]:
    if not _FN_SAFE.match(fn):
        raise ValueError(f"Unsafe function name: {fn}")
    named = ", ".join(f":{k}" for k in params) if params else ""
    result = await session.execute(text(f"SELECT * FROM {fn}({named})"), params)
    rows = result.fetchall()
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in rows]


async def call_scalar(session: AsyncSession, fn: str, **params):
    rows = await call(session, fn, **params)
    return rows[0][list(rows[0].keys())[0]] if rows else None


async def exec_fn(session: AsyncSession, fn: str, **params) -> None:
    if not _FN_SAFE.match(fn):
        raise ValueError(f"Unsafe function name: {fn}")
    named = ", ".join(f":{k}" for k in params) if params else ""
    await session.execute(text(f"SELECT {fn}({named})"), params)
