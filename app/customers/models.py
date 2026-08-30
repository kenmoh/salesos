from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.common.db.base import StoreFlowBase



class Customer(StoreFlowBase):
    __tablename__ = "customers"


    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name = mapped_column(String(255), nullable=False)
    phone = mapped_column(String(50))
    email = mapped_column(String(255))
    address = mapped_column(Text)
    created_at = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
