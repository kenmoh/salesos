"""Tenant creation must hand back the owner row it actually built.

``TenantResult.owner_user_id`` used to be a freshly generated UUID that had
nothing to do with the owner user the plan created, so any caller acting on
it — recording the sign-up device, for one — updated a row that does not
exist. The id has to be the owner's own id.
"""

from app.tenancy.schemas import TenantCreateCommand
from app.tenancy.service import plan_tenant_creation


def _command() -> TenantCreateCommand:
    return TenantCreateCommand(
        business_name="Acme",
        business_email="acme@shop.ng",
        owner_name="Owner",
        owner_email="owner@shop.ng",
        owner_phone=None,
        owner_password_hash="hashed",
    )


class TestPlanTenantCreation:
    def test_owner_user_id_is_the_owner_row_that_was_planned(self):
        result, tenant, user_model, _outbox = plan_tenant_creation(_command(), "acme")

        assert result.owner_user_id == user_model.id
        assert result.tenant_id == tenant.id
        assert user_model.tenant_id == tenant.id
        assert user_model.email == "owner@shop.ng"

    def test_two_tenants_never_share_an_owner_id(self):
        first, _t1, user_one, _o1 = plan_tenant_creation(_command(), "acme")
        second, _t2, user_two, _o2 = plan_tenant_creation(_command(), "acme-two")

        assert first.owner_user_id != second.owner_user_id
        assert first.owner_user_id != user_two.id
        assert second.owner_user_id != user_one.id
