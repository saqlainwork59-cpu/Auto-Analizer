from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import StrategyParams
from app.analysis.prepare import prepare
from app.config import get_settings
from app.core import flags
from app.core.bus import bus
from app.core.timeframes import TIMEFRAMES, delta, seconds
from app.data.providers.registry import get_provider
from app.data.store import load_frame
from app.db import get_session
from app.models import Asset, Candle, DataFeed, Signal, User
from app.security.deps import current_user
from app.security.ratelimit import limiter
from app.signals.serialize import signal_to_dict
from app.signals.service import analyze
from app.signals.strategy import get_active

router = APIRouter(prefix="/api/markets", tags=["markets"])
FRESH = {"24x7": 180, "fx": 1200, "equity": 1200}


def asset_out(a: Asset) -> dict:
    return {"id": a.id, "symbol": a.symbol, "name": a.name, "asset_class": a.asset_class, "provider": a.provider,
            "calendar": a.calendar, "price_precision": a.price_precision, "is_active": a.is_active, "meta": a.meta or {}}


async def data_status(session: AsyncSession, a: Asset) -> tuple[str, str | None]:
    prov = get_provider(a.provider)
    if not prov.configured():
        return "unavailable", f"{a.provider} API credentials are not configured on the server"
    if not await flags.get_flag(f"feed:{a.provider}", session):
        return "unavailable", f"{a.provider} feed disabled by an administrator"
    return "ok", None


async def price_info(a: Asset) -> dict | None:
    px = await bus.get_json(f"px:last:{a.id}")
    if not px:
        return None
    ts = datetime.fromisoformat(px["ts"])
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return {"price": px["price"], "ts": px["ts"], "source": px.get("source"), "age_seconds": round(age, 1),
            "stale": age > FRESH.get(a.calendar, 600)}


@router.get("")
async def list_markets(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    assets = (await session.execute(select(Asset).where(Asset.is_active.is_(True)).order_by(Asset.asset_class, Asset.symbol))).scalars().all()
    tfs = get_settings().analysis_tf_list
    # latest evaluation per (asset, timeframe)
    sub = (select(Signal.asset_id, Signal.timeframe, func.max(Signal.bar_time).label("bt"))
           .group_by(Signal.asset_id, Signal.timeframe).subquery())
    latest = (await session.execute(
        select(Signal).join(sub, and_(Signal.asset_id == sub.c.asset_id, Signal.timeframe == sub.c.timeframe,
                                      Signal.bar_time == sub.c.bt))
    )).unique().scalars().all()
    by_asset: dict[int, dict] = {}
    for s in latest:
        by_asset.setdefault(s.asset_id, {})[s.timeframe] = {
            "id": s.id, "status": s.status, "direction": s.direction, "score": s.score, "regime": s.regime,
            "bar_time": s.bar_time, "headline": s.headline}
    # previous daily close for change %
    out = []
    for a in assets:
        st, reason = await data_status(session, a)
        px = await price_info(a) if st == "ok" else None
        daily = (await session.execute(select(Candle.close, Candle.open_time).where(Candle.asset_id == a.id, Candle.timeframe == "1d")
                                       .order_by(Candle.open_time.desc()).limit(2))).all()
        change = None
        if px and daily:
            # reference = close of the last *completed* daily candle before the price timestamp
            ref = next((c for c, ot in daily if ot + delta("1d") <= datetime.fromisoformat(px["ts"])), None)
            if ref:
                change = (px["price"] / ref - 1) * 100
        if st == "ok" and px is None:
            st, reason = "unavailable", "no price received yet from the feed"
        elif px and px["stale"]:
            st, reason = "stale", f"last price {px['age_seconds'] / 60:.0f} min old (market closed or feed delayed)"
        out.append({**asset_out(a), "data_status": st, "data_reason": reason, "last": px, "change_pct": change,
                    "analysis": {tf: by_asset.get(a.id, {}).get(tf) for tf in tfs}})
    return {"markets": out, "analysis_timeframes": tfs}


@router.get("/feeds")
async def feeds(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(DataFeed).order_by(DataFeed.id))).scalars().all()
    return [{"id": r.id, "provider": r.provider, "status": r.status, "description": r.description,
             "last_message_at": r.last_message_at, "last_error": r.last_error} for r in rows]


async def _asset(session: AsyncSession, asset_id: int) -> Asset:
    a = await session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, detail="Market not found")
    return a


def _tf(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise HTTPException(422, detail="Invalid timeframe")
    return tf


@router.get("/{asset_id}")
async def market(asset_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    a = await _asset(session, asset_id)
    st, reason = await data_status(session, a)
    return {**asset_out(a), "data_status": st, "data_reason": reason, "last": await price_info(a)}


@router.get("/{asset_id}/candles")
async def candles(asset_id: int, tf: str = Query("1h"), limit: int = Query(500, ge=10, le=5000),
                  until: int | None = Query(None, ge=0, description="epoch seconds; last candle open time to include"),
                  indicators: bool = True, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    a = await _asset(session, asset_id)
    _tf(tf)
    now = datetime.now(timezone.utc)
    warm = 300 if indicators else 0
    end = datetime.fromtimestamp(until, tz=timezone.utc) if until else None
    df = await load_frame(session, a.id, tf, limit=limit + warm, closed_by=now, end=end)
    st, reason = await data_status(session, a)
    if df.empty:
        return {"asset": asset_out(a), "timeframe": tf, "candles": [], "forming": None, "overlays": {},
                "data_status": "unavailable", "data_reason": reason or "no stored candles for this timeframe yet"}
    body = {"asset": asset_out(a), "timeframe": tf, "data_status": st, "data_reason": reason}
    times = (df.index.as_unit("ns").asi8 // 10**9).astype(int)
    sl = slice(max(0, len(df) - limit), len(df))
    body["candles"] = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
         "volume": None if v != v else float(v)}
        for t, o, h, lo, c, v in zip(times[sl], df["open"].to_numpy()[sl], df["high"].to_numpy()[sl],
                                     df["low"].to_numpy()[sl], df["close"].to_numpy()[sl], df["volume"].to_numpy()[sl])
    ]
    last_close = int(times[-1]) + seconds(tf)
    body["last_closed_at"] = datetime.fromtimestamp(last_close, tz=timezone.utc)
    body["stale"] = (not until) and (now.timestamp() - last_close) > 2 * seconds(tf) + 120
    body["forming"] = None if until else await bus.get_json(f"px:forming:{a.id}:{tf}")
    body["historical_view"] = bool(until)
    if indicators and len(df) >= 60:
        _, params = await get_active(session)
        pf = prepare(df, tf, params if isinstance(params, StrategyParams) else StrategyParams())
        d = pf.df.iloc[sl]
        tt = times[sl]

        def series(col: str) -> list[dict]:
            vals = d[col].to_numpy(dtype=float)
            return [{"time": int(t), "value": round(float(v), 10)} for t, v in zip(tt, vals) if np.isfinite(v)]

        body["overlays"] = {k: series(k) for k in ("ema20", "ema50", "ema200", "bb_up", "bb_mid", "bb_low", "vwap",
                                                     "rsi14", "macd", "macd_signal", "macd_hist")}
    else:
        body["overlays"] = {}
    sq = select(Signal).where(Signal.asset_id == a.id, Signal.timeframe == tf, Signal.status == "ACTIONABLE",
                              Signal.bar_time >= datetime.fromtimestamp(int(times[sl][0]), tz=timezone.utc))
    if end is not None:
        sq = sq.where(Signal.bar_time <= end + delta(tf))
    sigs = (await session.execute(sq.order_by(Signal.bar_time))).unique().scalars().all()
    body["signals"] = [signal_to_dict(s, a, full=False) for s in sigs]
    return body


@router.get("/{asset_id}/analysis")
async def latest_analysis(asset_id: int, tf: str = Query("1h"), user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    a = await _asset(session, asset_id)
    _tf(tf)
    s = (await session.execute(select(Signal).where(Signal.asset_id == a.id, Signal.timeframe == tf)
                               .order_by(Signal.bar_time.desc(), Signal.id.desc()).limit(1))).unique().scalar_one_or_none()
    return {"signal": signal_to_dict(s, a) if s else None}


@router.post("/{asset_id}/analyze", dependencies=[Depends(limiter("analyze", 20))])
async def run_analysis(asset_id: int, tf: str = Query("1h"), user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    """On-demand analysis of the latest *closed* candle. Returns the stored evaluation if this bar was
    already analysed (the result for a given closed bar never changes)."""
    a = await _asset(session, asset_id)
    _tf(tf)
    ev, sig = await analyze(session, a, tf)
    return {"signal": signal_to_dict(sig, a) if sig else None}
