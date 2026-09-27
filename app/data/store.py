"""OHLCV persistence. Only *closed* candles are stored; the forming candle lives in Redis."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeframes import delta
from app.data.providers.base import CandleDTO
from app.models import Candle


async def upsert_candles(session: AsyncSession, asset_id: int, timeframe: str, candles: list[CandleDTO], source: str) -> int:
    rows = [
        {
            "asset_id": asset_id,
            "timeframe": timeframe,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "source": source,
            "ingested_at": datetime.now(timezone.utc),
        }
        for c in candles
        if c.closed
    ]
    if not rows:
        return 0
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    for i in range(0, len(rows), 1000):
        chunk = rows[i : i + 1000]
        stmt = ins(Candle).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["asset_id", "timeframe", "open_time"],
            set_={k: getattr(stmt.excluded, k) for k in ("open", "high", "low", "close", "volume", "source", "ingested_at")},
        )
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def latest_open_time(session: AsyncSession, asset_id: int, timeframe: str) -> datetime | None:
    v = (
        await session.execute(
            select(func.max(Candle.open_time)).where(Candle.asset_id == asset_id, Candle.timeframe == timeframe)
        )
    ).scalar()
    if v is not None and v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


async def count_candles(session: AsyncSession, asset_id: int, timeframe: str) -> int:
    return int(
        (await session.execute(select(func.count()).where(Candle.asset_id == asset_id, Candle.timeframe == timeframe))).scalar()
        or 0
    )


async def load_frame(
    session: AsyncSession,
    asset_id: int,
    timeframe: str,
    *,
    limit: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    closed_by: datetime | None = None,
) -> pd.DataFrame:
    """Load candles as a DataFrame indexed by open time (UTC).

    `closed_by` drops any candle whose close time is after that instant (defensive: the DB holds
    closed candles only, but this guarantees no partially formed bar enters an analysis).
    """
    q = select(Candle.open_time, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume).where(
        Candle.asset_id == asset_id, Candle.timeframe == timeframe
    )
    if start is not None:
        q = q.where(Candle.open_time >= start)
    if end is not None:
        q = q.where(Candle.open_time <= end)
    if closed_by is not None:
        q = q.where(Candle.open_time <= closed_by - delta(timeframe))
    if limit:
        q = q.order_by(Candle.open_time.desc()).limit(limit)
    else:
        q = q.order_by(Candle.open_time.asc())
    rows = (await session.execute(q)).all()
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC", name="open_time"))
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time").sort_index()
    return df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
