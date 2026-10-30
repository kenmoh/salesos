"""The landing page collects waitlist emails from people who never signed in.

``POST /waitlist/signups`` is anonymous, so the table it writes to sits
outside the tenancy model and the endpoint has to cope with the two things
that come with being public: the same person clicking twice, and addresses
that are not addresses.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _session():
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=lambda *_: 1),
        )
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _waitlist_app(session) -> FastAPI:
    from app.common.db.session import get_db
    from app.waitlist.routes import router

    application = FastAPI()
    application.include_router(router)

    async def override_db():
        yield session

    application.dependency_overrides[get_db] = override_db
    return application


class TestWaitlistSignup:
    ROUTE = "/waitlist/signups"

    def _app(self, session=None):
        session = session or _session()
        return _waitlist_app(session), session

    async def test_a_visitor_can_join_the_waitlist(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json={"email": "adaeze@example.com"})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["email"] == "adaeze@example.com"
        assert body["data"]["status"] == "waiting"
        assert "on the list" in body["message"].lower()
        stored = session.add.call_args.args[0]
        assert stored.email == "adaeze@example.com"
        assert stored.source == "landing_page"
        session.commit.assert_awaited_once()

    async def test_the_same_email_only_lands_on_the_list_once(self):
        existing = MagicMock()
        existing.id = "abc-1"
        existing.email = "tola@example.com"
        existing.status = "waiting"
        session = MagicMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing))
        )
        app, _ = self._app(session=session)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json={"email": "tola@example.com"})

        assert resp.status_code == 201
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        assert resp.json()["message"] == "You're already on the list — we'll be in touch."

    async def test_an_address_is_stored_the_way_it_was_typed(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json={"email": "  Chinedu@Example.com "})

        assert resp.status_code == 201
        assert session.add.call_args.args[0].email == "chinedu@example.com"

    async def test_an_address_that_is_not_an_address_is_refused(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json={"email": "not-an-email"})

        assert resp.status_code == 422
        session.add.assert_not_called()

    async def test_a_name_and_business_are_kept_when_given(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                self.ROUTE,
                json={
                    "email": "ife@example.com",
                    "name": "  Ife Balogun ",
                    "business_name": " Balogun Stores ",
                },
            )

        assert resp.status_code == 201
        stored = session.add.call_args.args[0]
        assert stored.name == "Ife Balogun"
        assert stored.business_name == "Balogun Stores"
