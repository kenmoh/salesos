"""Session rules: one device per user, and owner-side revocation.

The single-device rule is the point: a user is signed in on one device at a
time, so a shared login can never silently stay trusted everywhere. These
tests drive security.py against an in-memory Redis so the lifecycle — store,
sign-out-on-new-login, verify, revoke — is what an owner sees on the sessions
screen.
"""

import fnmatch
import json
from unittest.mock import patch

import pytest

from app.core import security


class _FakeRedis:
    """scan_iter over stored keys, get/set/delete like the real client."""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    async def scan_iter(self, pattern):
        for key in list(self.data):
            if fnmatch.fnmatch(key, pattern):
                yield key


@pytest.fixture
def redis_client():
    fake = _FakeRedis()

    async def _get():
        return fake

    with (
        patch.object(security, "get_session_redis", new=_get),
    ):
        yield fake


class TestSingleDevicePerUser:
    """Signing in somewhere else leaves no trace behind."""

    async def test_new_login_signs_the_previous_devices_out(self, redis_client):
        await security.store_refresh_session("u1", "s1", "h1", {}, 900)
        await security.store_refresh_session("u1", "s2", "h2", {}, 900)

        await security.single_sign_on_device("u1", "s3")  # the next login's sid

        assert await security.list_user_sessions("u1") == []

    async def test_coexisting_users_survive_a_sign_in(self, redis_client):
        await security.store_refresh_session("u1", "s1", "h1", {}, 900)
        await security.store_refresh_session("u2", "s2", "h2", {}, 900)

        await security.single_sign_on_device("u1", "s1")

        assert len(await security.list_all_sessions()) == 2

    async def test_verify_checks_the_creator_user_key(self, redis_client):
        await security.store_refresh_session("u1", "s1", security.hash_token("tok"), {}, 900)
        await security.store_refresh_session("u1", "s2", security.hash_token("other"), {}, 900)

        assert await security.verify_session_hash("s1", "tok", "u1") is True
        assert await security.verify_session_hash("s1", "other", "u1") is False

    async def test_verify_scans_when_the_user_id_is_unknown(self, redis_client):
        await security.store_refresh_session("u1", "s1", security.hash_token("tok"), {}, 900)

        assert await security.verify_session_hash("s1", "tok") is True
        assert await security.verify_session_hash("s1", "nope") is False

    async def test_revoke_session_removes_just_that_one(self, redis_client):
        await security.store_refresh_session("u1", "s1", "h1", {}, 900)
        await security.store_refresh_session("u1", "s2", "h2", {}, 900)

        await security.revoke_session("s1", "u1")

        sessions = await security.list_user_sessions("u1")
        assert sorted(s["session_id"] for s in sessions) == ["s2"]

    async def test_revoke_session_finds_the_key_without_user_id(self, redis_client):
        await security.store_refresh_session("u1", "s1", "h1", {}, 900)

        await security.revoke_session("s1")

        assert await security.list_all_sessions() == []

    async def test_device_info_round_trips_for_the_screen(self, redis_client):
        device = {"device_name": "iPhone", "user_agent": "ua", "ip_address": "1.2.3.4"}
        await security.store_refresh_session(
            "u1", "s1", "h1", device, 900, created_at="2026-01-01T00:00:00+00:00"
        )

        sessions = await security.list_user_sessions("u1")
        recorded = sessions[0]
        assert recorded["device_name"] == "iPhone"
        assert recorded["ip_address"] == "1.2.3.4"
        assert recorded["created_at"] == "2026-01-01T00:00:00+00:00"
        assert recorded["last_active_at"] is not None

    async def test_refresh_keeps_the_original_sign_in_time(self, redis_client):
        device = {"device_name": "iPhone", "user_agent": "ua", "ip_address": "1.2.3.4"}
        await security.store_refresh_session(
            "u1", "s1", "h1", device, 900, created_at="2026-01-01T00:00:00+00:00"
        )

        await security.store_refresh_session(
            "u1",
            "s2",
            "h2",
            device,
            900,
            created_at=(await security.get_session("u1", "s1"))["created_at"],
        )
        await security.revoke_session("s1", "u1")

        assert (await security.get_session("u1", "s2"))["created_at"] == (
            "2026-01-01T00:00:00+00:00"
        )
