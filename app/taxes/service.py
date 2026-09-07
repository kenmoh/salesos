from datetime import UTC, datetime
from uuid import UUID, uuid4

from .models import Tax
from .schemas import TaxCreateCommand, TaxResult


def plan_create_tax(
    command: TaxCreateCommand,
) -> tuple[TaxResult, Tax]:
    """Plan the creation of a tax type.

    Args:
        command: The TaxCreateCommand containing tax details.

    Returns:
        A tuple of (TaxResult, Tax model instance).
    """
    tax_id = uuid4()
    now = datetime.now(UTC)

    tax = Tax(
        id=tax_id,
        tenant_id=command.tenant_id,
        name=command.name,
        rate=command.rate,
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    result = TaxResult(
        id=tax.id,
        tenant_id=tax.tenant_id,
        name=tax.name,
        rate=float(tax.rate),
        is_active=tax.is_active,
        created_at=tax.created_at,
        updated_at=tax.updated_at,
    )

    return result, tax
