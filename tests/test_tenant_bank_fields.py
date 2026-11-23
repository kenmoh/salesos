"""Tenant creation carries business description and settlement bank details.

``account_name`` falls back to the business name when the caller does not
provide one; everything else is optional and stored as given.
"""

from app.tenancy.schemas import TenantCreateCommand
from app.tenancy.service import plan_tenant_creation


def _command(**overrides) -> TenantCreateCommand:
    payload = {
        "business_name": "Acme",
        "business_email": "acme@shop.ng",
        "owner_name": "Owner",
        "owner_email": "owner@shop.ng",
        "owner_phone": None,
        "owner_password_hash": "hashed",
    }
    payload.update(overrides)
    return TenantCreateCommand(**payload)


class TestTenantBankFields:
    def test_account_name_defaults_to_business_name(self):
        _result, tenant, _user, _outbox = plan_tenant_creation(_command(), "acme")

        assert tenant.account_name == "Acme"

    def test_blank_account_name_defaults_to_business_name(self):
        _result, tenant, _user, _outbox = plan_tenant_creation(
            _command(account_name="  "), "acme"
        )

        assert tenant.account_name == "Acme"

    def test_explicit_account_name_is_kept(self):
        _result, tenant, _user, _outbox = plan_tenant_creation(
            _command(account_name="Acme Ventures Ltd"), "acme"
        )

        assert tenant.account_name == "Acme Ventures Ltd"

    def test_business_and_bank_fields_are_persisted(self):
        _result, tenant, _user, _outbox = plan_tenant_creation(
            _command(
                business_description="Retail chain in Lagos",
                account_number="0123456789",
                account_name="Acme Ventures Ltd",
                bank_name="Wema Bank",
            ),
            "acme",
        )

        assert tenant.business_description == "Retail chain in Lagos"
        assert tenant.account_number == "0123456789"
        assert tenant.account_name == "Acme Ventures Ltd"
        assert tenant.bank_name == "Wema Bank"
