from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import audit
from app.db import get_session
from app.models import User, utcnow
from app.schemas import ChangePasswordIn, LoginIn, RegisterIn, UserSettingsIn
from app.security.deps import current_user
from app.security.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.security.ratelimit import client_ip, limiter
from app.security.tokens import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    set_auth_cookies,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role,
            "settings": u.settings or {}, "created_at": u.created_at}


@router.post("/register", status_code=201, dependencies=[Depends(limiter("register", get_settings().register_rate_limit_per_minute))])
async def register(body: RegisterIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    problems = password_problems(body.password, body.email)
    if problems:
        raise HTTPException(422, detail="Password " + "; ".join(problems))
    email = body.email.lower()
    if (await session.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none():
        raise HTTPException(409, detail="An account with this email already exists")
    role = "user"
    boot = get_settings().bootstrap_admin_email.strip().lower()
    if boot and email == boot:
        has_admin = (await session.execute(select(func.count()).select_from(User).where(User.role == "admin"))).scalar()
        role = "admin" if not has_admin else "user"
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name, role=role,
                settings={"theme": "system", "default_timeframe": "1h"})
    session.add(user)
    await session.flush()
    refresh = await issue_refresh_token(session, user, user_agent=request.headers.get("user-agent"), ip=client_ip(request))
    await audit(session, "user.register", user_id=user.id, ip=client_ip(request), commit=False)
    await session.commit()
    csrf = set_auth_cookies(response, create_access_token(user), refresh)
    return {"user": user_out(user), "csrf_token": csrf}


@router.post("/login", dependencies=[Depends(limiter("login", get_settings().login_rate_limit_per_minute))])
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    ip = client_ip(request)
    user = (await session.execute(select(User).where(func.lower(User.email) == body.email.lower()))).scalar_one_or_none()
    now = utcnow()
    if user is not None and user.locked_until is not None:
        locked = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=now.tzinfo)
        if locked > now:
            await audit(session, "auth.login_locked", user_id=user.id, ip=ip)
            raise HTTPException(status.HTTP_423_LOCKED, detail="Account temporarily locked after repeated failures. Try again later.")
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_logins = 0
        await audit(session, "auth.login_failed", user_id=user.id if user else None, ip=ip, detail={"email": body.email.lower()})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    refresh = await issue_refresh_token(session, user, user_agent=request.headers.get("user-agent"), ip=ip)
    await audit(session, "auth.login", user_id=user.id, ip=ip, commit=False)
    await session.commit()
    csrf = set_auth_cookies(response, create_access_token(user), refresh)
    return {"user": user_out(user), "csrf_token": csrf}


@router.post("/refresh", dependencies=[Depends(limiter("refresh", 30))])
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(401, detail="No session")
    rotated = await rotate_refresh_token(session, raw, user_agent=request.headers.get("user-agent"), ip=client_ip(request))
    if rotated is None:
        clear_auth_cookies(response)
        raise HTTPException(401, detail="Session expired")
    user, new_raw = rotated
    csrf = set_auth_cookies(response, create_access_token(user), new_raw)
    return {"user": user_out(user), "csrf_token": csrf}


@router.post("/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    await revoke_refresh_token(session, request.cookies.get(REFRESH_COOKIE))
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_out(user)


@router.patch("/me")
async def update_me(body: UserSettingsIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user.id)
    settings = dict(u.settings or {})
    if body.theme is not None:
        settings["theme"] = body.theme
    if body.default_timeframe is not None:
        settings["default_timeframe"] = body.default_timeframe
    u.settings = settings
    if body.display_name is not None:
        u.display_name = body.display_name
    await session.commit()
    return user_out(u)


@router.post("/change-password", dependencies=[Depends(limiter("chpw", 5))])
async def change_password(body: ChangePasswordIn, request: Request, response: Response,
                          user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user.id)
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(403, detail="Current password is incorrect")
    problems = password_problems(body.new_password, u.email)
    if problems:
        raise HTTPException(422, detail="Password " + "; ".join(problems))
    u.password_hash = hash_password(body.new_password)
    from sqlalchemy import update

    from app.models import RefreshToken

    await session.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))
                          .values(revoked_at=utcnow()))
    refresh = await issue_refresh_token(session, u, user_agent=request.headers.get("user-agent"), ip=client_ip(request))
    await audit(session, "auth.password_changed", user_id=u.id, ip=client_ip(request), commit=False)
    await session.commit()
    csrf = set_auth_cookies(response, create_access_token(u), refresh)
    return {"ok": True, "csrf_token": csrf}
