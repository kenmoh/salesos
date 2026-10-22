"""Scope cache: versioned hot-read keys and the invalidation contract.

The cache must answer three questions with a strict hand: a second read of
the same scope hits Redis and never calls the DB twice, a write bumps the
scope so the next read fetches fresh data, and a Redis outage degrades to
plain DB reads. Nothing cached may cross tenants.
"""

import fnmatch
from unittest.mock import patch

import pytest

from app.common import scope_cache
from app.common.scope_cache import scope_bump, scope_read


class _FakeRedis:
    def __init__(self):
        self.data = {}
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, "0") or "0") + 1)
        return int(self.data[key])

    async def scan_iter(self, pattern):
        for key in list(self.data):
            if fnmatch.fnmatch(key, pattern):
                yield key


@pytest.fixture
def redis_client():
    fake = _FakeRedis()

    async def _get():
        return fake

    with patch.object(scope_cache, "get_cache_redis", new=_get):
        yield fake


class TestScopeCache:
    async def test_repeat_read_hits_cache_and_skips_the_db(self, redis_client):
        calls = []

        async def fetch():
            calls.append(1)
            return {"items": [1], "total": 1}

        first = await scope_read("t1", "s1", scope_cache.CATALOG, {"p": 1}, 60, fetch)
        second = await scope_read("t1", "s1", scope_cache.CATALOG, {"p": 1}, 60, fetch)

        assert first == second == {"items": [1], "total": 1}
        assert len(calls) == 1

    async def test_a_write_bumps_the_scope_into_fresh_reads(self, redis_client):
        calls = []

        async def fetch():
            calls.append(1)
            return len(calls)

        await scope_read("t1", "s1", scope_cache.CATALOG, {"p": 1}, 60, fetch)
        await scope_bump("t1", "s1", (scope_cache.CATALOG,))
        after = await scope_read("t1", "s1", scope_cache.CATALOG, {"p": 1}, 60, fetch)

        assert after == 2
        assert len(calls) == 2

    async def test_tenants_do_not_share_cache(self, redis_client):
        async def make_fetch(value):
            async def fetch():
                return value
            return fetch

        t1 = await scope_read("t1", "-", scope_cache.STORES, {}, 60, await make_fetch("first"))
        t2 = await scope_read("t2", "-", scope_cache.STORES, {}, 60, await make_fetch("second"))
        t1_again = await scope_read("t1", "-", scope_cache.STORES, {}, 60, await make_fetch("first"))

        assert t1 == t1_again == "first"
        assert t2 == "second"

    async def test_different_fields_key_different_entries(self, redis_client):
        calls = []

        async def fetch():
            calls.append(1)
            return {"n": len(calls)}

        a = await scope_read("t1", "s1", scope_cache.CATALOG, {"search": "a"}, 60, fetch)
        b = await scope_read("t1", "s1", scope_cache.CATALOG, {"search": "b"}, 60, fetch)

        assert a == {"n": 1} and b == {"n": 2}

    async def test_redis_outage_falls_back_to_the_db(self):
        calls = []

        async def fetch():
            calls.append(1)
            return {"from": "db"}

        async def _none():
            return None

        with patch.object(scope_cache, "get_cache_redis", new=_none):
            result = await scope_read("t1", "s1", scope_cache.CATALOG, {}, 60, fetch)
            again = await scope_read("t1", "s1", scope_cache.CATALOG, {}, 60, fetch)

        assert result == again == {"from": "db"}
        assert len(calls) == 2

    async def test_bump_survives_a_redis_outage(self):
        async def _none():
            return None

        with patch.object(scope_cache, "get_cache_redis", new=_none):
            await scope_bump("t1", "s1", (scope_cache.REPORTS,))

    async def test_accounting_scopes_cache_and_bump(self, redis_client):
        calls = []

        async def fetch():
            calls.append(1)
            return {"journals": len(calls)}

        first = await scope_read("t1", "-", scope_cache.ACCOUNTING, {"journals": 1}, 45, fetch)
        second = await scope_read("t1", "-", scope_cache.ACCOUNTING, {"journals": 1}, 45, fetch)
        await scope_bump("t1", "-", (scope_cache.ACCOUNTING, scope_cache.SALES, scope_cache.DOCUMENTS))
        fresh = await scope_read("t1", "-", scope_cache.ACCOUNTING, {"journals": 1}, 45, fetch)

        assert first == second == {"journals": 1}
        assert fresh == {"journals": 2}
        assert len(calls) == 2

    async def test_sales_scope_isolated_from_accounting(self, redis_client):
        calls = []

        async def fetch():
            calls.append(1)
            return len(calls)

        sales = await scope_read("t1", "-", scope_cache.SALES, {"list": 1}, 30, fetch)
        await scope_bump("t1", "-", (scope_cache.ACCOUNTING,))
        still_cached = await scope_read("t1", "-", scope_cache.SALES, {"list": 1}, 30, fetch)
        await scope_bump("t1", "-", (scope_cache.SALES,))
        fresh = await scope_read("t1", "-", scope_cache.SALES, {"list": 1}, 30, fetch)

        assert sales == still_cached == 1
        assert fresh == 2
        assert len(calls) == 2
