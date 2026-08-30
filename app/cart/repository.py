from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Cart, CartItem


async def get_cart_by_session(session: AsyncSession, session_id: str) -> Cart | None:
    result = await session.execute(select(Cart).where(Cart.session_id == session_id))
    return result.scalar_one_or_none()


async def get_cart_by_id(session: AsyncSession, cart_id: UUID) -> Cart | None:
    result = await session.execute(select(Cart).where(Cart.id == cart_id))
    return result.scalar_one_or_none()


async def create_cart(session: AsyncSession, cart: Cart) -> Cart:
    session.add(cart)
    await session.flush()
    return cart


async def update_cart_status(session: AsyncSession, cart_id: UUID, status: str) -> Cart:
    result = await session.execute(select(Cart).where(Cart.id == cart_id))
    cart = result.scalar_one()
    cart.status = status
    await session.flush()
    return cart


async def get_cart_items(session: AsyncSession, cart_id: UUID) -> list[CartItem]:
    result = await session.execute(
        select(CartItem).where(CartItem.cart_id == cart_id).order_by(CartItem.created_at)
    )
    return list(result.scalars().all())


async def get_cart_item(session: AsyncSession, item_id: UUID) -> CartItem | None:
    result = await session.execute(select(CartItem).where(CartItem.id == item_id))
    return result.scalar_one_or_none()


async def get_cart_item_by_product(
    session: AsyncSession, cart_id: UUID, product_id: UUID
) -> CartItem | None:
    result = await session.execute(
        select(CartItem).where(
            CartItem.cart_id == cart_id,
            CartItem.product_id == product_id,
        )
    )
    return result.scalar_one_or_none()


async def add_cart_item(session: AsyncSession, item: CartItem) -> CartItem:
    session.add(item)
    await session.flush()
    return item


async def bulk_add_cart_items(session: AsyncSession, items: list[CartItem]) -> list[CartItem]:
    for item in items:
        session.add(item)
    await session.flush()
    return items


async def remove_cart_item(session: AsyncSession, item_id: UUID) -> None:
    result = await session.execute(select(CartItem).where(CartItem.id == item_id))
    item = result.scalar_one()
    await session.delete(item)
    await session.flush()


async def get_active_carts_by_tenant(session: AsyncSession, tenant_id: UUID) -> list[Cart]:
    result = await session.execute(
        select(Cart)
        .where(Cart.tenant_id == tenant_id, Cart.status == "active")
        .order_by(Cart.created_at.desc())
    )
    return list(result.scalars().all())
