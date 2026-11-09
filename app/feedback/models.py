"""Feedback reports for bug reports and feature requests.

This module lets any signed-in user submit a feedback report that is either
a bug report or a feature request. The table is tenant-scoped so each
business only sees its own reports. Platform admins can read across tenants.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class FeedbackReport(StoreFlowBase):
    """A user-submitted bug report or feature request.

    Attributes:
        id: Unique identifier for this report.
        tenant_id: The business this report belongs to.
        user_id: The user who submitted the report.
        type: Whether this is a bug report or a feature request ("bug" or "feature").
        title: Short summary of the report.
        description: Detailed description of the issue or request.
        status: Current lifecycle status ("open", "in_progress", "resolved", "rejected").
        created_at: When the report was submitted.
        updated_at: Last time the report was modified.
    """

    __tablename__ = "feedback_reports"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
