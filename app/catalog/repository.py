from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Category, Product
from app.common.events.outbox import OutboxWrite


async def get_product_by_id(session: AsyncSession, product_id: UUID) -> Product | None:
    result = await session.execute(select(Product).where(Product.id == product_id))
    return result.scalar_one_or_none()


async def get_product_by_public_id(session: AsyncSession, public_id: str) -> Product | None:
    result = await session.execute(select(Product).where(Product.public_id == public_id))
    return result.scalar_one_or_none()


async def get_products_by_public_ids(session: AsyncSession, public_ids: list[str]) -> list[Product]:
    result = await session.execute(select(Product).where(Product.public_id.in_(public_ids)))
    return list(result.scalars().all())


async def get_product_by_sku(session: AsyncSession, tenant_id: UUID, sku: str) -> Product | None:
    result = await session.execute(
        select(Product).where(Product.tenant_id == tenant_id, Product.sku == sku)
    )
    return result.scalar_one_or_none()


async def create_product(session: AsyncSession, product: Product) -> Product:
    session.add(product)
    await session.flush()
    return product


async def update_product(session: AsyncSession, product_id: UUID, **kwargs) -> Product:
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one()
    for key, value in kwargs.items():
        if hasattr(product, key) and value is not None:
            setattr(product, key, value)
    await session.flush()
    return product


async def list_products(
    session: AsyncSession,
    tenant_id: UUID,
    status: str | None = None,
    category_id: UUID | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Product]:
    query = select(Product).where(Product.tenant_id == tenant_id)
    if status:
        query = query.where(Product.status == status)
    if category_id:
        query = query.where(Product.category_id == category_id)
    if search:
        query = query.where(Product.name.ilike(f"%{search}%"))
    query = query.order_by(Product.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def create_category(session: AsyncSession, category: Category) -> Category:
    session.add(category)
    await session.flush()
    return category


async def get_category_by_id(session: AsyncSession, category_id: UUID) -> Category | None:
    result = await session.execute(select(Category).where(Category.id == category_id))
    return result.scalar_one_or_none()


async def get_category_by_name(
    session: AsyncSession, store_id: UUID, name: str
) -> Category | None:
    result = await session.execute(
        select(Category).where(Category.store_id == store_id, Category.name == name)
    )
    return result.scalar_one_or_none()


async def list_categories(session: AsyncSession, store_id: UUID) -> list[Category]:
    result = await session.execute(
        select(Category).where(Category.store_id == store_id).order_by(Category.name)
    )
    return list(result.scalars().all())


async def update_category(session: AsyncSession, category_id: UUID, **kwargs) -> Category:
    result = await session.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one()
    for key, value in kwargs.items():
        if hasattr(category, key) and value is not None:
            setattr(category, key, value)
    await session.flush()
    return category


async def delete_category(session: AsyncSession, category_id: UUID) -> None:
    result = await session.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one()
    await session.delete(category)
    await session.flush()


async def write_outbox_events(session: AsyncSession, outbox_writes: list[OutboxWrite]) -> None:
    for write in outbox_writes:
        session.add(write.to_model())
    await session.flush()
