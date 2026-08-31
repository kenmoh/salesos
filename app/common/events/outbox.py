from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase
from app.common.events.envelope import EventEnvelope


class OutboxEvent(StoreFlowBase):
    """SQLAlchemy mapping for a service-local outbox table."""

    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(120), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    headers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclass(frozen=True)
class OutboxWrite:
    """Small command object used by services to stage domain events transactionally."""

    event: EventEnvelope
    aggregate_type: str
    aggregate_id: str

    def to_model(self) -> OutboxEvent:
        return OutboxEvent(
            id=self.event.event_id,
            event_type=self.event.event_type,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            tenant_id=self.event.tenant_id,
            payload=self.event.payload,
            headers={
                "event_version": self.event.event_version,
                "occurred_at": self.event.occurred_at.isoformat(),
                "actor_id": str(self.event.actor_id) if self.event.actor_id else None,
                "correlation_id": self.event.correlation_id,
                "causation_id": str(self.event.causation_id) if self.event.causation_id else None,
            },
        )
