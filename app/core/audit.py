"""Audit trail (who did what) and persistent system log (what the system did)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.models import AuditLog, SystemLog

log = logging.getLogger("parallax")

_SENSITIVE = {"password", "secret", "api_key", "token", "key", "destination"}


def _scrub(detail: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (detail or {}).items():
        out[k] = "[redacted]" if any(s in k.lower() for s in _SENSITIVE) else v
    return out


async def audit(
    session: AsyncSession,
    action: str,
    *,
    user_id: int | None = None,
    target: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    session.add(AuditLog(user_id=user_id, action=action, target=target, ip=ip, detail=_scrub(detail)))
    if commit:
        await session.commit()


async def system_log(level: str, source: str, message: str, context: dict[str, Any] | None = None) -> None:
    """Write to the DB-backed system log (visible in the admin panel) and to stdout."""
    getattr(log, level.lower(), log.info)("[%s] %s", source, message)
    try:
        async with session_factory()() as s:
            s.add(SystemLog(level=level.upper(), source=source, message=message[:4000], context=_scrub(context)))
            await s.commit()
    except Exception as exc:  # noqa: BLE001 - logging must never crash the caller
        log.error("failed to persist system log: %s", exc)
