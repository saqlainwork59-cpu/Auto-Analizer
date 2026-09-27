"""Historical backfill and gap repair from the configured provider."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeframes import delta, seconds
from app.data.providers.base import ProviderError
from app.data.providers.registry import get_provider
from app.data.store import count_candles, latest_open_time, upsert_candles
from app.models import Asset

log = logging.getLogger(__name__)
PAGE = {"binance": 1000, "twelvedata": 5000, "alpaca": 10000}


async def backfill_recent(session: AsyncSession, asset: Asset, tf: str, bars: int) -> int:
    """Ensure roughly `bars` recent closed candles exist; fetch only what is missing."""
    prov = get_provider(asset.provider)
    have = await count_candles(session, asset.id, tf)
    last = await latest_open_time(session, asset.id, tf)
    if have >= bars and last is not None:
        start = last - delta(tf) * 2
        candles = await prov.fetch_candles(asset.provider_symbol, tf, start=start, limit=PAGE[asset.provider])
    else:
        candles = await prov.fetch_candles(asset.provider_symbol, tf, limit=bars)
    return await upsert_candles(session, asset.id, tf, candles, asset.provider)


async def backfill_range(session: AsyncSession, asset: Asset, tf: str, start: datetime, end: datetime,
                         max_requests: int = 200) -> int:
    """Page forward from `start` to `end`. Stops when the provider returns no further progress."""
    prov = get_provider(asset.provider)
    cursor = start
    total = 0
    step = timedelta(seconds=seconds(tf))
    for _ in range(max_requests):
        if cursor >= end:
            break
        try:
            candles = await prov.fetch_candles(asset.provider_symbol, tf, start=cursor, end=end, limit=PAGE[asset.provider])
        except ProviderError as exc:
            log.warning("backfill %s %s stopped: %s", asset.symbol, tf, exc)
            if total == 0:
                raise
            break
        if not candles:
            break
        total += await upsert_candles(session, asset.id, tf, candles, asset.provider)
        nxt = candles[-1].open_time + step
        if nxt <= cursor:
            break
        cursor = nxt
        if candles[-1].open_time + step >= datetime.now(timezone.utc):
            break
    return total
