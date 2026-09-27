"""Emergency controls (kill switches) persisted in `system_flags` and cached briefly."""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_factory
from app.models import SystemFlag, utcnow

# key -> (default, description)
FLAGS: dict[str, tuple[bool, str]] = {
    "signals_enabled": (True, "Signal engine produces new BUY/SELL evaluations"),
    "paper_trading_enabled": (True, "Users may open new simulated trades"),
    "broker_execution_enabled": (False, "Real-money order routing (also requires deploy-time env flag)"),
    "alerts_enabled": (True, "Outbound alert delivery (Telegram / email / browser)"),
    "feed:binance": (True, "Binance crypto feed"),
    "feed:twelvedata": (True, "Twelve Data forex / indices / commodities feed"),
    "feed:alpaca": (True, "Alpaca US-equities feed"),
}

_cache: dict[str, tuple[bool, float]] = {}
_TTL = 5.0


async def get_flag(key: str, session: AsyncSession | None = None) -> bool:
    if key not in FLAGS:
        raise KeyError(key)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[1] < _TTL:
        value = hit[0]
    else:
        async def _load(s: AsyncSession) -> bool:
            row = await s.get(SystemFlag, key)
            return FLAGS[key][0] if row is None else row.enabled

        if session is not None:
            value = await _load(session)
        else:
            async with session_factory()() as s:
                value = await _load(s)
        _cache[key] = (value, time.monotonic())
    if key == "broker_execution_enabled":
        return value and get_settings().broker_execution_enabled
    return value


async def set_flag(session: AsyncSession, key: str, enabled: bool, user_id: int | None, reason: str | None) -> None:
    if key not in FLAGS:
        raise KeyError(key)
    row = await session.get(SystemFlag, key)
    if row is None:
        row = SystemFlag(key=key, enabled=enabled)
        session.add(row)
    row.enabled, row.reason, row.updated_by, row.updated_at = enabled, reason, user_id, utcnow()
    await session.commit()
    _cache.pop(key, None)


async def all_flags(session: AsyncSession) -> list[dict]:
    rows = {r.key: r for r in (await session.execute(select(SystemFlag))).scalars()}
    out = []
    for key, (default, desc) in FLAGS.items():
        r = rows.get(key)
        enabled = default if r is None else r.enabled
        entry = {
            "key": key,
            "enabled": enabled,
            "description": desc,
            "reason": r.reason if r else None,
            "updated_at": r.updated_at if r else None,
        }
        if key == "broker_execution_enabled":
            entry["env_allows"] = get_settings().broker_execution_enabled
        out.append(entry)
    return out


def clear_cache() -> None:
    _cache.clear()
