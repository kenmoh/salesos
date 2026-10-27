"""The website contact form keeps an inbox outside the tenancy model.

Anyone can submit — the marketing site never signs a visitor in — so the
table carries no ``tenant_id`` and no RLS policy. Submissions land for the
platform admins, who read them through the platform router.
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


def _contact_app(session) -> FastAPI:
    from app.contact.routes import router
    from app.common.db.session import get_db

    application = FastAPI()
    application.include_router(router)

    async def override_db():
        yield session

    application.dependency_overrides[get_db] = override_db
    return application


PAYLOAD = {
    "name": "Adaeze Nwosu",
    "business_name": "Green Basket Ltd",
    "email": "adaeze@greenbasket.ng",
    "phone": "+2348012345678",
    "message": "We run 3 supermarkets and want to unify POS and inventory.",
}


class TestContactInbox:
    ROUTE = "/contact/messages"

    def _app(self, session=None):
        session = session or _session()
        return _contact_app(session), session

    async def test_a_visitor_can_leave_a_message(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json=PAYLOAD)
        assert resp.status_code == 201, resp.text
        stored = session.add.call_args.args[0]
        assert stored.name == "Adaeze Nwosu"
        assert stored.email == "adaeze@greenbasket.ng"
        assert stored.business_name == "Green Basket Ltd"
        assert stored.phone == "+2348012345678"
        session.commit.assert_awaited_once()

    async def test_a_double_click_does_not_double_store(self):
        existing = MagicMock()
        existing.id = "abc-1"
        session = MagicMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing))
        )
        app, _ = self._app(session=session)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json=PAYLOAD)
        assert resp.status_code == 201
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        assert resp.json()["message"] == (
            "We already have your message. We'll get back to you shortly."
        )

    async def test_a_message_needs_a_real_email_and_text(self):
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            for bad in (
                {**PAYLOAD, "email": "not-an-email"},
                {**PAYLOAD, "message": "hi"},
                {**PAYLOAD, "name": ""},
            ):
                resp = await ac.post(self.ROUTE, json=bad)
                assert resp.status_code == 422, bad
        session.add.assert_not_called()

    async def test_optional_fields_may_be_left_out(self):
        minimal = {k: v for k, v in PAYLOAD.items() if k not in ("business_name", "phone")}
        app, session = self._app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(self.ROUTE, json=minimal)
        assert resp.status_code == 201, resp.text
        stored = session.add.call_args.args[0]
        assert stored.business_name is None
        assert stored.phone is None
