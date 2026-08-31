"""Repository functions for stores and store products.

Provides CRUD operations for Store and StoreProduct models.
"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.stores.models import Store, StoreProduct


async def create_store(session: AsyncSession, store: Store) -> Store:
    session.add(store)
    await session.flush()
    return store


async def get_store_by_id(session: AsyncSession, store_id: UUID) -> Store | None:
    result = await session.execute(select(Store).where(Store.id == store_id))
    return result.scalar_one_or_none()


async def list_stores(session: AsyncSession, tenant_id: UUID) -> list[Store]:
    result = await session.execute(
        select(Store)
        .where(Store.tenant_id == tenant_id, Store.status == "active")
        .order_by(Store.name)
    )
    return list(result.scalars().all())


async def get_main_store(session: AsyncSession, tenant_id: UUID) -> Store | None:
    result = await session.execute(
        select(Store).where(
            Store.tenant_id == tenant_id,
            Store.is_warehouse.is_(True),
            Store.status == "active",
        )
    )
    return result.scalar_one_or_none()


async def count_main_stores(session: AsyncSession, tenant_id: UUID) -> int:
    result = await session.execute(
        select(Store).where(
            Store.tenant_id == tenant_id,
            Store.is_warehouse.is_(True),
            Store.status != "deleted",
        )
    )
    return len(list(result.scalars().all()))


async def get_store_with_tenant(
    session: AsyncSession, store_id: UUID, tenant_id: UUID
) -> Store | None:
    result = await session.execute(
        select(Store).where(
            Store.id == store_id,
            Store.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def update_store(session: AsyncSession, store_id: UUID, **kwargs) -> Store | None:
    result = await session.execute(
        update(Store).where(Store.id == store_id).values(**kwargs).returning(Store)
    )
    await session.flush()
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# StoreProduct
# ---------------------------------------------------------------------------


async def create_store_product(
    session: AsyncSession, store_product: StoreProduct
) -> StoreProduct:
    """Persist a new store product record.

    Args:
        session: Async SQLAlchemy session.
        store_product: The StoreProduct instance to persist.

    Returns:
        The persisted StoreProduct with its id populated.
    """
    session.add(store_product)
    await session.flush()
    return store_product


async def get_store_product_by_id(
    session: AsyncSession, store_product_id: UUID
) -> StoreProduct | None:
    """Retrieve a store product by its primary key.

    Args:
        session: Async SQLAlchemy session.
        store_product_id: The store product UUID.

    Returns:
        The matching StoreProduct or None.
    """
    result = await session.execute(
        select(StoreProduct).where(StoreProduct.id == store_product_id)
    )
    return result.scalar_one_or_none()


async def get_store_product(
    session: AsyncSession, store_id: UUID, product_id: UUID
) -> StoreProduct | None:
    """Retrieve a store product by store and product pair.

    Args:
        session: Async SQLAlchemy session.
        store_id: The store UUID.
        product_id: The catalog product UUID.

    Returns:
        The matching StoreProduct or None.
    """
    result = await session.execute(
        select(StoreProduct).where(
            StoreProduct.store_id == store_id,
            StoreProduct.product_id == product_id,
        )
    )
    return result.scalar_one_or_none()


async def list_store_products(
    session: AsyncSession,
    tenant_id: UUID,
    store_id: UUID,
    *,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List store products with optional filtering and pagination.

    Args:
        session: Async SQLAlchemy session.
        tenant_id: Filter by tenant UUID.
        store_id: Filter by store UUID.
        status: Optional status filter.
        search: Optional search term matching product name.
        page: Page number (1-indexed).
        page_size: Number of results per page.

    Returns:
        Dict with items, total, page, and page_size keys.
    """
    query = select(StoreProduct).where(
        StoreProduct.tenant_id == tenant_id,
        StoreProduct.store_id == store_id,
    )
    if status:
        query = query.where(StoreProduct.status == status)
    if search:
        query = query.where(StoreProduct.name.ilike(f"%{search}%"))

    count_query = select(StoreProduct.id).where(
        StoreProduct.tenant_id == tenant_id,
        StoreProduct.store_id == store_id,
    )
    if status:
        count_query = count_query.where(StoreProduct.status == status)
    if search:
        count_query = count_query.where(StoreProduct.name.ilike(f"%{search}%"))

    total_result = await session.execute(count_query)
    total = len(list(total_result.scalars().all()))

    query = query.order_by(StoreProduct.name)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.execute(query)
    items = list(result.scalars().all())

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_store_products_by_product(
    session: AsyncSession, tenant_id: UUID, product_id: UUID
) -> list[StoreProduct]:
    """Get all store product records for a given catalog product.

    Args:
        session: Async SQLAlchemy session.
        tenant_id: Filter by tenant UUID.
        product_id: The catalog product UUID.

    Returns:
        List of StoreProduct records across all stores.
    """
    result = await session.execute(
        select(StoreProduct).where(
            StoreProduct.tenant_id == tenant_id,
            StoreProduct.product_id == product_id,
        )
    )
    return list(result.scalars().all())


async def get_store_products_by_product_and_store(
    session: AsyncSession, tenant_id: UUID, product_id: UUID, store_id: UUID
) -> StoreProduct | None:
    """Get the store product record for a specific product in a specific store.

    Args:
        session: Async SQLAlchemy session.
        tenant_id: Filter by tenant UUID.
        product_id: The catalog product UUID.
        store_id: The store UUID.

    Returns:
        The matching StoreProduct or None.
    """
    result = await session.execute(
        select(StoreProduct).where(
            StoreProduct.tenant_id == tenant_id,
            StoreProduct.product_id == product_id,
            StoreProduct.store_id == store_id,
        )
    )
    return result.scalar_one_or_none()


async def update_store_product(
    session: AsyncSession, store_product_id: UUID, **kwargs
) -> StoreProduct | None:
    """Update fields on a store product.

    Args:
        session: Async SQLAlchemy session.
        store_product_id: The store product UUID to update.
        **kwargs: Fields to update.

    Returns:
        The updated StoreProduct or None if not found.
    """
    result = await session.execute(
        update(StoreProduct)
        .where(StoreProduct.id == store_product_id)
        .values(**kwargs)
        .returning(StoreProduct)
    )
    await session.flush()
    return result.scalar_one_or_none()
