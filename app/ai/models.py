"""AI service domain models for conversation memory.

This module defines the SQLAlchemy ORM models for storing AI assistant
conversation history. Conversations are stored per-tenant for multi-tenant
isolation and can be retrieved for context in future interactions.

Abbreviations Used in This Module
----------------------------------
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- UTC: Coordinated Universal Time -- the primary time standard.
- PG_UUID: PostgreSQL UUID type -- a native UUID column type for PostgreSQL databases.
- Mapped: SQLAlchemy type annotation that maps a Python type to a database column.
- JSONB: JSON Binary -- a PostgreSQL column type for storing JSON data efficiently.
- LLM: Large Language Model -- the AI model that generates responses.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Conversation(StoreFlowBase):
    """Represents an AI assistant conversation session.

    A conversation groups related messages together and tracks the
    conversation lifecycle. Each conversation belongs to a single tenant
    and can be associated with a specific user.

    Conversation Lifecycle:
        1. Created when the user sends their first message.
        2. Updated with each new message (user and assistant).
        3. Can be retrieved for context in future interactions.
        4. Old conversations can be archived or deleted.

    Multi-Tenant Isolation:
        All conversations are filtered by tenant_id to ensure data isolation
        between different businesses using the platform.

    Attributes:
        id: Unique identifier for this conversation (UUID, auto-generated).
        tenant_id: The business tenant this conversation belongs to.
        user_id: Optional UUID of the user having this conversation.
        title: Auto-generated title from the first user message (truncated to 200 chars).
        created_at: Timestamp when this conversation was started (UTC).
        updated_at: Timestamp when this conversation was last updated (UTC).
    """

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ConversationMessage(StoreFlowBase):
    """Represents a single message within an AI assistant conversation.

    Messages alternate between "user" (the human's question) and "assistant"
    (the AI's response). Assistant messages may include tool_calls showing
    which read-only tools were invoked.

    Message Content:
        - User messages: The natural language question or request.
        - Assistant messages: The AI's answer, optionally with tool_calls
          and recommendations.

    Tool Calls Storage:
        Tool calls are stored as a JSONB array for efficient querying.
        Each tool call includes the tool name, arguments, and a summary.

    Attributes:
        id: Unique identifier for this message (UUID, auto-generated).
        conversation_id: Foreign key linking to the parent Conversation.
        tenant_id: The business tenant this message belongs to (denormalized
            for efficient direct queries without JOINs).
        role: The role of the message sender. One of: "user", "assistant".
        content: The text content of the message.
        tool_calls: JSONB array of tool calls made by the assistant.
            Format: [{"tool": "search_products", "arguments": {...}, "result_summary": "..."}]
            None for user messages.
        created_at: Timestamp when this message was sent (UTC).
    """

    __tablename__ = "conversation_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
