import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import bcrypt
import pyotp
from jose import jwt

from app.core.config import settings
from app.core.redis_client import get_session_redis


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")[:72]
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    encoded = password.encode("utf-8")[:72]
    return bcrypt.checkpw(encoded, hashed.encode("utf-8"))


def hash_pin(pin: str) -> str:
    """Hash a 4-6 digit PIN for supervisor override."""
    encoded = pin.encode("utf-8")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, hashed: str) -> bool:
    """Verify a PIN against its hash."""
    encoded = pin.encode("utf-8")
    return bcrypt.checkpw(encoded, hashed.encode("utf-8"))


def validate_password_strength(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if not any(c.isupper() for c in password):
        errors.append("Password must include an uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Password must include a lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Password must include a number")
    return errors


def _token(
    payload: dict, secret: str, minutes: int | None = None, days: int | None = None
) -> tuple[str, datetime]:
    exp = datetime.now(UTC) + (timedelta(days=days) if days else timedelta(minutes=minutes or 15))
    payload = {**payload, "exp": exp, "iat": datetime.now(UTC), "jti": uuid4().hex}
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm), exp


def create_access_token(
    user_id: str, business_id: str, role: str, permissions: list[str]
) -> tuple[str, datetime]:
    return _token(
        {"sub": user_id, "bid": business_id, "role": role, "perms": permissions, "type": "access"},
        settings.jwt_access_secret,
        minutes=settings.access_token_expire_minutes,
    )


def create_refresh_token(user_id: str, business_id: str, session_id: str) -> tuple[str, datetime]:
    return _token(
        {"sub": user_id, "bid": business_id, "sid": session_id, "type": "refresh"},
        settings.jwt_refresh_secret,
        days=settings.refresh_token_expire_days,
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_access_secret, algorithms=[settings.jwt_algorithm])


def decode_refresh_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_refresh_secret, algorithms=[settings.jwt_algorithm])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def generate_session_id() -> str:
    return secrets.token_urlsafe(24)


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=settings.totp_issuer)


def verify_totp(secret: str | None, code: str) -> bool:
    return bool(secret and pyotp.TOTP(secret).verify(code, valid_window=1))


async def store_refresh_session(
    user_id: str, sid: str, token_hash: str, device: dict, ttl: int
) -> None:
    client = await get_session_redis()
    if client is None:
        return
    try:
        session_data = json.dumps({"token_hash": token_hash, "device": device, "user_id": user_id})
        await client.set(f"sf:session:{user_id}:{sid}", session_data, ex=ttl)
    except Exception:
        pass


async def verify_session_hash(sid: str, raw_token: str) -> bool:
    client = await get_session_redis()
    if client is None:
        return True
    try:
        stored = await client.get(f"sf:session:{sid}")
        if stored is None:
            return False
        data = json.loads(stored)
        return data.get("token_hash") == hash_token(raw_token)
    except Exception:
        return True


async def revoke_session(sid: str) -> None:
    client = await get_session_redis()
    if client is None:
        return
    try:
        await client.delete(f"sf:session:{sid}")
    except Exception:
        pass


async def revoke_all_sessions(user_id: str) -> None:
    client = await get_session_redis()
    if client is None:
        return
    try:
        async for key in client.scan_iter(f"sf:session:{user_id}:*"):
            await client.delete(key)
    except Exception:
        pass


async def list_user_sessions(user_id: str) -> list[dict]:
    """List all active sessions for a user."""
    client = await get_session_redis()
    if client is None:
        return []
    try:
        sessions = []
        async for key in client.scan_iter(f"sf:session:{user_id}:*"):
            raw = await client.get(key)
            if raw:
                data = json.loads(raw)
                sid = key.decode().split(":")[-1]
                sessions.append({
                    "session_id": sid,
                    "user_id": data.get("user_id", user_id),
                    "device": data.get("device", {}),
                })
        return sessions
    except Exception:
        return []


async def blacklist_token(jti: str, ttl: int) -> None:
    client = await get_session_redis()
    if client is None:
        return
    try:
        await client.set(f"sf:blacklist:{jti}", "1", ex=max(ttl, 1))
    except Exception:
        pass


async def is_token_blacklisted(jti: str) -> bool:
    client = await get_session_redis()
    if client is None:
        return False
    try:
        return bool(await client.exists(f"sf:blacklist:{jti}"))
    except Exception:
        return False


def seconds_until(dt: datetime) -> int:
    return max(int((dt - datetime.now(UTC)).total_seconds()), 0)
