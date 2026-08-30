from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    """Canonical event format used across all StoreFlow service boundaries."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    event_version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: UUID | None = None
    actor_id: UUID | None = None
    correlation_id: str | None = None
    causation_id: UUID | None = None
    payload: dict[str, Any]

    def routing_key(self) -> str:
        return self.event_type
