"""The audit middleware writes one row per mutating request.

The session factory has to be called to get a session. Handing the factory
itself to ``async with`` raised a TypeError, and the middleware swallowed
every failure, so each request logged "Failed to write audit event" and the
audit trail stayed empty.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.middleware import audit


def _session():
    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.begin.return_value.__aenter__.return_value = session
    session.begin.return_value.__aexit__.return_value = False
    session.execute = AsyncMock()
    return session


class TestAuditEventWrite:
    async def test_the_event_reaches_the_database(self):
        session = _session()
        # _get_factory() hands back a session factory, and the factory is
        # called to make the session.
        sessionmaker = MagicMock(return_value=session)
        factory = MagicMock(return_value=sessionmaker)

        with patch.object(audit, "_get_factory", factory):
            await audit._write_audit_event(
                {
                    "user_id": None,
                    "business_id": None,
                    "action": "create",
                    "resource_type": "waitlist",
                    "method": "POST",
                    "path": "/waitlist/signups",
                    "status_code": 201,
                    "ip_address": "1.2.3.4",
                    "user_agent": "test",
                }
            )

        factory.assert_called_once()
        session.execute.assert_awaited_once()
        statement = session.execute.await_args.args[0]
        assert "INSERT INTO audit_events" in str(statement)

    async def test_a_failing_write_never_breaks_the_request(self):
        session = _session()
        session.execute = AsyncMock(side_effect=RuntimeError("database is down"))
        sessionmaker = MagicMock(return_value=session)
        factory = MagicMock(return_value=sessionmaker)

        with patch.object(audit, "_get_factory", factory):
            await audit._write_audit_event(
                {
                    "user_id": None,
                    "business_id": None,
                    "action": "create",
                    "resource_type": "waitlist",
                    "method": "POST",
                    "path": "/waitlist/signups",
                    "status_code": 201,
                    "ip_address": "1.2.3.4",
                    "user_agent": "test",
                }
            )
