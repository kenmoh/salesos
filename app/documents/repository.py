"""Repository layer for document domain database operations.

This module provides async database CRUD (Create, Read, Update) operations
for all document domain models. All functions accept an AsyncSession and follow
the repository pattern -- they handle SQL queries and return model instances,
keeping the service layer free of database concerns.

Abbreviations Used in This Module
----------------------------------
- CRUD: Create, Read, Update, Delete -- the four basic database operations.
- FK: Foreign Key -- a reference from one table to another.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- SQL: Structured Query Language -- the language used to query databases.
- ASC: Ascending order -- smallest to largest (A-Z, 0-9).
- DESC: Descending order -- largest to smallest (Z-A, 9-0).
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.models import Document, DocumentItem


async def create_document(session: AsyncSession, doc: Document) -> Document:
    """Insert a new document record.

    Args:
        session: The async SQLAlchemy database session to use.
        doc: The Document model instance to persist.

    Returns:
        The same Document instance with the database-assigned ID.
    """
    session.add(doc)
    await session.flush()
    return doc


async def create_document_items(session: AsyncSession, items: list[DocumentItem]) -> None:
    """Insert multiple document line items in a batch.

    Args:
        session: The async SQLAlchemy database session to use.
        items: A list of DocumentItem model instances to persist.
    """
    for item in items:
        session.add(item)
    await session.flush()


async def get_document_by_id(session: AsyncSession, document_id: UUID) -> Document | None:
    """Retrieve a single document by its UUID.

    Args:
        session: The async SQLAlchemy database session to use.
        document_id: The UUID of the document to retrieve.

    Returns:
        The Document if found, or None if no matching document exists.
    """
    result = await session.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()


async def get_document_by_number(session: AsyncSession, doc_number: str) -> Document | None:
    """Retrieve a single document by its document number.

    Document numbers are unique across the system (e.g., "INV-20260826-A1B2C3D4").

    Args:
        session: The async SQLAlchemy database session to use.
        doc_number: The document number to look up.

    Returns:
        The Document if found, or None if no matching document exists.
    """
    result = await session.execute(select(Document).where(Document.doc_number == doc_number))
    return result.scalar_one_or_none()


async def list_documents_by_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    doc_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Document]:
    """List documents for a tenant with optional filtering and pagination.

    Supports filtering by:
        - doc_type: Filter by document type (quote, invoice, receipt, purchase_order).
        - status: Filter by document status (draft, sent, paid, etc.).

    Results are ordered by created_at in descending order (newest first).

    Args:
        session: The async SQLAlchemy database session to use.
        tenant_id: The business tenant to filter by.
        doc_type: Optional document type to filter by.
        status: Optional status to filter by.
        limit: Maximum number of documents to return (default: 50).
        offset: Number of documents to skip for pagination (default: 0).

    Returns:
        A list of Document instances matching the filters.
    """
    query = select(Document).where(Document.tenant_id == tenant_id)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    query = query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_document_items(session: AsyncSession, document_id: UUID) -> list[DocumentItem]:
    """List all line items for a specific document.

    Args:
        session: The async SQLAlchemy database session to use.
        document_id: The UUID of the parent document.

    Returns:
        A list of DocumentItem instances belonging to the document.
    """
    result = await session.execute(
        select(DocumentItem)
        .where(DocumentItem.document_id == document_id)
        .order_by(DocumentItem.id)
    )
    return list(result.scalars().all())


async def update_document_status(session: AsyncSession, document_id: UUID, status: str) -> Document:
    """Update the status of a document.

    Note: This function does NOT validate status transitions. Use
    plan_status_change() in the service layer for transition validation.

    Args:
        session: The async SQLAlchemy database session to use.
        document_id: The UUID of the document to update.
        status: The new status to set.

    Returns:
        The updated Document instance.

    Raises:
        sqlalchemy.exc.NoResultFound: If no document with the given ID exists.
    """
    result = await session.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one()
    doc.status = status
    await session.flush()
    return doc


async def link_document_to_sale(
    session: AsyncSession, document_id: UUID, sale_id: UUID
) -> Document:
    """Link a document to a sale by setting the linked_sale_id field.

    This is used when a quote or invoice is converted to a sale. The document
    is marked as "accepted" and linked to the newly created sale.

    Args:
        session: The async SQLAlchemy database session to use.
        document_id: The UUID of the document to link.
        sale_id: The UUID of the sale to link to.

    Returns:
        The updated Document instance with linked_sale_id set.

    Raises:
        sqlalchemy.exc.NoResultFound: If no document with the given ID exists.
    """
    result = await session.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one()
    doc.linked_sale_id = sale_id
    await session.flush()
    return doc
