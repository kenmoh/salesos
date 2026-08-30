"""Auto-generated SKU utilities for store products.

Generates unique SKUs per tenant in the format ``{PREFIX}-{RANDOM}``
where PREFIX is derived from the product name and RANDOM is a
cryptographically random alphanumeric string.

The prefix is the first 4 alphanumeric characters of the product name,
uppercased. If the name starts with non-alphanumeric characters, the
prefix defaults to "SKU". The random component is 8 characters from
``secrets.token_urlsafe``, giving approximately 2.8 trillion unique
combinations per prefix, making collisions extremely unlikely.

Uniqueness is validated against the database before returning.
"""

import re
import secrets

_MAX_RETRIES = 3
_RANDOM_LENGTH = 8


def _extract_prefix(name: str) -> str:
    """Extract a 4-character uppercase prefix from a product name.

    Takes the first 4 alphanumeric characters from the name. Falls back
    to ``SKU`` if no alphanumeric characters exist.

    Args:
        name: The product name to extract from.

    Returns:
        A 4-character uppercase string suitable for use in a SKU.
    """
    alphanumeric = re.sub(r"[^a-zA-Z0-9]", "", name)[:4].upper()
    return alphanumeric if alphanumeric else "SKU"


def generate_sku() -> str:
    """Generate a random SKU string.

    Returns:
        A SKU in the format ``SKU-XXXXXXXX`` with 8 random alphanumeric
        characters.
    """
    prefix = "SKU"
    random_part = secrets.token_urlsafe(_RANDOM_LENGTH)[:_RANDOM_LENGTH].upper()
    return f"{prefix}-{random_part}"


async def generate_unique_sku(session, tenant_id) -> str:
    """Generate a SKU guaranteed unique within the given tenant.

    Attempts up to ``_MAX_RETRIES`` times to generate a SKU that does
    not collide with an existing ``store_products.sku`` for the tenant.

    Args:
        session: An async SQLAlchemy session for database queries.
        tenant_id: The tenant UUID to check uniqueness against.

    Returns:
        A unique SKU string.

    Raises:
        RuntimeError: If a unique SKU could not be generated after
            all retry attempts.
    """
    from sqlalchemy import select, text

    from stores.models import StoreProduct

    for _ in range(_MAX_RETRIES):
        candidate = generate_sku()
        result = await session.execute(
            select(StoreProduct.id).where(
                StoreProduct.tenant_id == tenant_id,
                StoreProduct.sku == candidate,
            )
        )
        if result.scalar_one_or_none() is None:
            return candidate

    raise RuntimeError(
        f"Failed to generate a unique SKU after {_MAX_RETRIES} attempts"
    )
