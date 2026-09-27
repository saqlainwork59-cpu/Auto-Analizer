"""Short-lived JWT access tokens + rotating, DB-backed refresh tokens (both in httpOnly cookies)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import RefreshToken, User, utcnow
from app.security.crypto import sha256_hex

ACCESS_COOKIE = "px_access"
REFRESH_COOKIE = "px_refresh"
CSRF_COOKIE = "px_csrf"
ALGO = "HS256"


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
        "typ": "access",
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGO], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "access" else None


async def issue_refresh_token(
    session: AsyncSession, user: User, *, family: str | None = None, user_agent: str | None, ip: str | None
) -> str:
    raw = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256_hex(raw),
            family=family or secrets.token_hex(16),
            expires_at=utcnow() + timedelta(days=get_settings().refresh_token_days),
            user_agent=(user_agent or "")[:255],
            ip=ip,
        )
    )
    return raw


async def rotate_refresh_token(
    session: AsyncSession, raw: str, *, user_agent: str | None, ip: str | None
) -> tuple[User, str] | None:
    """Validate + rotate. Reuse of an already-rotated token revokes the whole family (theft signal)."""
    row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw)))).scalar_one_or_none()
    if row is None:
        return None
    now = utcnow()
    if row.revoked_at is not None:
        await session.execute(
            update(RefreshToken).where(RefreshToken.family == row.family, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        await session.commit()
        return None
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.revoked_at = now
    new_raw = await issue_refresh_token(session, user, family=row.family, user_agent=user_agent, ip=ip)
    await session.commit()
    return user, new_raw


async def revoke_refresh_token(session: AsyncSession, raw: str | None) -> None:
    if not raw:
        return
    await session.execute(
        update(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw)).values(revoked_at=utcnow())
    )
    await session.commit()


def set_auth_cookies(response: Response, access: str, refresh: str) -> str:
    s = get_settings()
    csrf = secrets.token_urlsafe(24)
    common = {"secure": s.cookie_secure, "domain": s.cookie_domain, "samesite": "lax"}
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, max_age=s.access_token_minutes * 60, path="/", **common)
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, max_age=s.refresh_token_days * 86400, path="/api/auth", **common
    )
    # Double-submit CSRF token: readable by our JS, echoed in X-CSRF-Token on unsafe requests.
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, max_age=s.refresh_token_days * 86400, path="/", **common)
    return csrf


def clear_auth_cookies(response: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/auth"), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=s.cookie_domain)
