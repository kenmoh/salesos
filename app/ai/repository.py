"""Repository layer for AI service database operations.

This module provides async database operations for:
    1. Conversation memory (Conversations and ConversationMessages).
    2. Saved generations (legacy, kept for backward compatibility).

All functions accept an AsyncSession and follow the repository pattern.

Abbreviations Used in This Module
----------------------------------
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- CRUD: Create, Read, Update, Delete -- the four basic database operations.
- SQL: Structured Query Language -- the language used to query databases.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import Conversation, ConversationMessage


# --- Conversation Memory --------------------------------------------------------------


async def create_conversation(session: AsyncSession, conv: Conversation) -> Conversation:
    """Insert a new conversation record.

    Args:
        session: The async SQLAlchemy database session.
        conv: The Conversation model instance to persist.

    Returns:
        The same Conversation instance with the database-assigned ID.
    """
    session.add(conv)
    await session.flush()
    return conv


async def get_conversation(session: AsyncSession, conv_id: UUID) -> Conversation | None:
    """Retrieve a conversation by its UUID.

    Args:
        session: The async SQLAlchemy database session.
        conv_id: The UUID of the conversation.

    Returns:
        The Conversation if found, or None.
    """
    result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
    return result.scalar_one_or_none()


async def list_conversations(
    session: AsyncSession,
    tenant_id: UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[Conversation]:
    """List conversations for a tenant.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        limit: Maximum conversations to return (default: 20).
        offset: Pagination offset.

    Returns:
        A list of Conversation instances, newest first.
    """
    query = (
        select(Conversation)
        .where(Conversation.tenant_id == tenant_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def create_conversation_message(
    session: AsyncSession, msg: ConversationMessage
) -> ConversationMessage:
    """Insert a new conversation message.

    Args:
        session: The async SQLAlchemy database session.
        msg: The ConversationMessage model instance to persist.

    Returns:
        The same ConversationMessage instance.
    """
    session.add(msg)
    await session.flush()
    return msg


async def get_conversation_messages(
    session: AsyncSession,
    conversation_id: UUID,
    limit: int = 50,
) -> list[ConversationMessage]:
    """List messages for a conversation in chronological order.

    Args:
        session: The async SQLAlchemy database session.
        conversation_id: The UUID of the conversation.
        limit: Maximum messages to return (default: 50).

    Returns:
        A list of ConversationMessage instances, oldest first.
    """
    query = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_conversation_title(
    session: AsyncSession, conv_id: UUID, title: str
) -> Conversation | None:
    """Update a conversation's title.

    Args:
        session: The async SQLAlchemy database session.
        conv_id: The UUID of the conversation.
        title: The new title (typically the first user message, truncated).

    Returns:
        The updated Conversation, or None if not found.
    """
    result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return None
    conv.title = title
    await session.flush()
    return conv


async def delete_conversation(session: AsyncSession, conv_id: UUID) -> bool:
    """Delete a conversation and all its messages.

    Args:
        session: The async SQLAlchemy database session.
        conv_id: The UUID of the conversation to delete.

    Returns:
        True if deleted, False if not found.
    """
    msgs = await get_conversation_messages(session, conv_id, limit=1000)
    for msg in msgs:
        await session.delete(msg)

    result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return False
    await session.delete(conv)
    return True
