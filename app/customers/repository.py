from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Customer


async def create_customer(session: AsyncSession, customer: Customer) -> Customer:
    session.add(customer)
    await session.flush()
    return customer


async def get_customer_by_id(session: AsyncSession, customer_id: UUID) -> Customer | None:
    return await session.get(Customer, customer_id)


async def list_customers(session: AsyncSession, tenant_id: UUID) -> list[Customer]:
    result = await session.execute(
        select(Customer).where(Customer.tenant_id == tenant_id).order_by(Customer.created_at.desc())
    )
    return list(result.scalars().all())


async def update_customer(session: AsyncSession, customer: Customer, **kwargs) -> Customer:
    for key, value in kwargs.items():
        setattr(customer, key, value)
    await session.flush()
    return customer


async def delete_customer(session: AsyncSession, customer: Customer) -> None:
    await session.delete(customer)
    await session.flush()
