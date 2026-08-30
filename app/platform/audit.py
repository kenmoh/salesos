from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from platform.models import PlatformAuditLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def log_platform_audit(
    session: AsyncSession,
    *,
    admin_id: UUID | None,
    action: str,
    resource: str,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> None:
    entry = PlatformAuditLog(
        admin_id=admin_id,
        action=action,
        resource=resource,
        resource_id=resource_id,
        details=json.dumps(details) if details else None,
        ip_address=ip_address,
    )
    session.add(entry)
    await session.flush()
