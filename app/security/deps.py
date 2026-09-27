"""Authentication / authorization dependencies enforced server-side on every protected route."""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security.tokens import ACCESS_COOKIE, CSRF_COOKIE, decode_access_token

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def verify_csrf(request: Request) -> None:
    """Double-submit check for cookie-authenticated unsafe requests."""
    if request.method not in UNSAFE_METHODS:
        return
    if not request.cookies.get(ACCESS_COOKIE):
        return  # bearer-token clients are not vulnerable to CSRF
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not hmac.compare_digest(cookie, header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = _extract_token(request)
    payload = decode_access_token(token) if token else None
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    verify_csrf(request)
    request.state.user_id = user.id
    return user


async def optional_user(request: Request, session: AsyncSession = Depends(get_session)) -> User | None:
    token = _extract_token(request)
    payload = decode_access_token(token) if token else None
    if not payload:
        return None
    user = await session.get(User, int(payload["sub"]))
    return user if user and user.is_active else None


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user
