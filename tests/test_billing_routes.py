from datetime import datetime, UTC
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.billing.routes import platform_update_plan
from app.billing.schemas import PlanOut, PlanUpdate


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, plan):
        self.plan = plan
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.plan)

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_platform_update_plan_eager_loads_response_relationships():
    plan_id = uuid4()
    now = datetime.now(UTC)
    plan = SimpleNamespace(
        id=plan_id,
        name="Updated plan",
        slug="updated-plan",
        description=None,
        monthly_price=Decimal("100.00"),
        currency="NGN",
        is_active=True,
        sort_order=0,
        annual_discount_pct=Decimal("17.00"),
        created_at=now,
        updated_at=now,
        fee_tiers=[],
        plan_features=[],
    )
    session = _Session(plan)

    updated = await platform_update_plan(
        plan_id,
        PlanUpdate(name="Updated plan"),
        SimpleNamespace(session=session),
    )

    loaded_relationships = {option.path[1].key for option in session.statement._with_options}
    assert loaded_relationships == {"fee_tiers", "plan_features"}
    assert PlanOut.model_validate(updated).fee_tiers == []
