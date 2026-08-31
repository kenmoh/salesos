from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class InboxEvent(StoreFlowBase):
    """Records processed events so handlers remain idempotent."""

    __tablename__ = "inbox_events"

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
