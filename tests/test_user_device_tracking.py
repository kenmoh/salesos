"""Last-known device tracking on sign-in.

A sign-in records the device on the account row that already exists: one row
per account, updated in place, so tracking devices never grows the table.
These tests cover the parsing of the device, the update that writes it, and
the read paths that surface it to platform admins and tenant owners.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.device import (
    ANDROID,
    BROWSER,
    IOS,
    UNKNOWN,
    describe_client,
)

IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
IPAD_SAFARI = (
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
ANDROID_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Mobile Safari/537.36"
)
WINDOWS_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
MAC_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
WINDOWS_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)


class TestDeviceParsing:
    def test_iphone_is_ios(self):
        device = describe_client(user_agent=IPHONE_SAFARI, ip_address="10.0.0.1")
        assert device.device_type == IOS
        assert device.browser == "Safari"
        assert device.label == "iPhone"
        assert device.ip_address == "10.0.0.1"

    def test_ipad_is_ios(self):
        device = describe_client(user_agent=IPAD_SAFARI)
        assert device.device_type == IOS
        assert device.label == "iPad"

    def test_android_is_android(self):
        device = describe_client(user_agent=ANDROID_CHROME)
        assert device.device_type == ANDROID
        assert device.browser == "Chrome"
        assert device.label == "Android"

    def test_desktop_browser_is_browser(self):
        device = describe_client(user_agent=WINDOWS_CHROME)
        assert device.device_type == BROWSER
        assert device.browser == "Chrome"
        assert device.label == "Chrome"

    def test_mac_safari_is_browser(self):
        device = describe_client(user_agent=MAC_SAFARI)
        assert device.device_type == BROWSER
        assert device.browser == "Safari"

    def test_edge_is_not_read_as_chrome(self):
        device = describe_client(user_agent=WINDOWS_EDGE)
        assert device.browser == "Edge"

    def test_unknown_user_agent(self):
        device = describe_client(user_agent=None)
        assert device.device_type == UNKNOWN
        assert device.browser is None
        assert device.user_agent is None

    def test_client_device_name_labels_but_never_retypes(self):
        device = describe_client(user_agent=ANDROID_CHROME, device_name="Shop Tablet")
        assert device.label == "Shop Tablet"
        assert device.device_type == ANDROID

    def test_long_user_agent_is_truncated_to_the_column_width(self):
        device = describe_client(user_agent="x" * 900)
        assert len(device.user_agent) == 500


class TestRecordUserDevice:
    async def test_updates_the_existing_row_without_inserting(self):
        from app.common.auth_service import record_user_device

        session = AsyncMock()
        user_id = str(uuid4())
        device = describe_client(user_agent=IPHONE_SAFARI, ip_address="10.0.0.2")

        await record_user_device(session=session, user_id=user_id, device=device)

        session.add.assert_not_called()
        session.execute.assert_awaited_once()
        statement = session.execute.await_args.args[0]
        compiled = statement.compile()
        assert "UPDATE users" in str(compiled)
        values = compiled.params
        assert values["last_device_type"] == IOS
        assert values["last_browser"] == "Safari"
        assert values["last_ip_address"] == "10.0.0.2"
        assert values["last_seen_at"] is not None
        assert "last_login_at" not in values

    async def test_login_marks_the_login_time_on_the_same_update(self):
        from app.common.auth_service import record_user_device

        session = AsyncMock()
        device = describe_client(user_agent=WINDOWS_CHROME)

        await record_user_device(
            session=session, user_id=str(uuid4()), device=device, mark_login=True
        )

        values = session.execute.await_args.args[0].compile().params
        assert values["last_login_at"] is not None
        assert values["last_device_type"] == BROWSER


class TestPlatformReadPaths:
    async def test_users_list_carries_the_device(self):
        from app.platform.routes import list_all_users

        user = MagicMock()
        user.id = str(uuid4())
        user.tenant_id = str(uuid4())
        user.email = "owner@shop.ng"
        user.full_name = "Owner"
        user.status = "active"
        user.totp_enabled = False
        user.last_login_at = None
        user.last_device_type = IOS
        user.last_browser = "Safari"
        user.last_seen_at = None
        user.last_ip_address = "10.0.0.3"
        user.created_at = None

        result = MagicMock()
        result.scalar = MagicMock(return_value=1)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[user])))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.session = session

        response = await list_all_users(ctx=ctx)

        item = response["data"]["items"][0]
        assert item["email"] == "owner@shop.ng"
        assert item["last_device_type"] == IOS
        assert item["last_browser"] == "Safari"
        assert item["last_ip_address"] == "10.0.0.3"

    async def test_tenants_list_carries_the_owner_device(self):
        from app.platform.routes import list_tenants

        tenant = MagicMock()
        tenant.id = str(uuid4())
        tenant.slug = "acme"
        tenant.subdomain = "acme.storeflow.ng"
        tenant.business_name = "Acme"
        tenant.business_email = "acme@shop.ng"
        tenant.owner_name = "Owner"
        tenant.owner_email = "owner@shop.ng"
        tenant.tier = "starter"
        tenant.status = "active"
        tenant.created_at = None

        seen_at = MagicMock()
        seen_at.isoformat = MagicMock(return_value="2026-11-26T10:00:00+00:00")

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=1)
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[(tenant, IOS, "Safari", seen_at)])

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[count_result, rows_result])
        ctx = MagicMock()
        ctx.session = session

        response = await list_tenants(ctx=ctx)

        item = response["data"]["items"][0]
        assert item["owner_email"] == "owner@shop.ng"
        assert item["owner_device_type"] == IOS
        assert item["owner_browser"] == "Safari"
        assert item["owner_last_seen_at"] == "2026-11-26T10:00:00+00:00"


class TestTenantReadPaths:
    async def test_employee_list_carries_the_device(self):
        from app.auth.routes import employees

        row = {
            "id": str(uuid4()),
            "email": "cashier@shop.ng",
            "full_name": "Cashier",
            "phone": None,
            "status": "active",
            "last_login_at": None,
            "created_at": None,
            "store_id": None,
            "last_device_type": ANDROID,
            "last_browser": "Chrome",
            "last_seen_at": None,
            "last_ip_address": "10.0.0.4",
            "role": "cashier",
        }
        result = MagicMock()
        result.mappings = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.session = session
        ctx.user.business_id = str(uuid4())

        response = await employees(ctx=ctx)

        item = response["data"][0]
        assert item["email"] == "cashier@shop.ng"
        assert item["last_device_type"] == ANDROID
        assert item["last_browser"] == "Chrome"
        assert item["last_ip_address"] == "10.0.0.4"


@pytest.mark.parametrize(
    "user_agent,expected",
    [
        (IPHONE_SAFARI, IOS),
        (ANDROID_CHROME, ANDROID),
        (WINDOWS_CHROME, BROWSER),
    ],
)
def test_every_platform_a_tenant_can_sign_in_from_is_recognised(
    user_agent, expected
):
    assert describe_client(user_agent=user_agent).device_type == expected
