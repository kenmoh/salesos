from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class AuditEvent(StoreFlowBase):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    business_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    store_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


async def write_audit_event(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    business_id: str | None = None,
    store_id: str | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    method: str = "POST",
    path: str = "",
    status_code: int = 200,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist one audit event on an open session."""
    import json

    await session.execute(
        text(
            """
            INSERT INTO audit_events
                (user_id, business_id, store_id, action, resource_type,
                 resource_id, method, path, status_code, ip_address,
                 user_agent, details, created_at)
            VALUES
                (:user_id, :business_id, :store_id, :action, :resource_type,
                 :resource_id, :method, :path, :status_code, :ip_address,
                 :user_agent, :details, now())
        """
        ),
        {
            "user_id": user_id,
            "business_id": business_id,
            "store_id": store_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
            "method": method,
            "path": path,
            "status_code": status_code,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "details": json.dumps(details) if details else None,
        },
    )
