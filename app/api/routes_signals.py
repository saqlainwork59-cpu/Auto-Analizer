from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Asset, Signal, SignalFeature, SignalOutcome, User
from app.security.deps import current_user
from app.signals.serialize import signal_to_dict

router = APIRouter(prefix="/api", tags=["signals"])
CLOSED = ("WIN_TP2", "WIN_TP1", "LOSS", "BREAKEVEN", "TIME_EXIT")
NOT_TRIGGERED = ("EXPIRED", "INVALIDATED")


@router.get("/signals")
async def list_signals(
    status: str | None = Query(None, pattern="^(ACTIONABLE|WAIT|DATA_UNAVAILABLE)$"),
    direction: str | None = Query(None, pattern="^(BUY|SELL|WAIT)$"),
    asset_id: int | None = None,
    timeframe: str | None = Query(None, pattern="^(5m|15m|1h|4h|1d)$"),
    outcome: str | None = Query(None, max_length=20),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(Signal)
    cq = select(func.count(Signal.id))
    conds = []
    if status:
        conds.append(Signal.status == status)
    if direction:
        conds.append(Signal.direction == direction)
    if asset_id:
        conds.append(Signal.asset_id == asset_id)
    if timeframe:
        conds.append(Signal.timeframe == timeframe)
    if outcome:
        q = q.join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        cq = cq.join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        conds.append(SignalOutcome.status == outcome)
    for c in conds:
        q, cq = q.where(c), cq.where(c)
    total = (await session.execute(cq)).scalar() or 0
    rows = (await session.execute(q.order_by(Signal.bar_time.desc(), Signal.id.desc()).offset(offset).limit(limit))).unique().scalars().all()
    return {"total": total, "items": [signal_to_dict(s, full=False) for s in rows]}


@router.get("/signals/top")
async def top_setups(limit: int = Query(10, ge=1, le=50), user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(Signal).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .where(Signal.status == "ACTIONABLE", Signal.expires_at > now, SignalOutcome.status.in_(["PENDING", "OPEN"]))
        .order_by(Signal.score.desc()).limit(limit)
    )).unique().scalars().all()
    return {"items": [signal_to_dict(s, full=False) for s in rows], "as_of": now}


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    s = (await session.execute(select(Signal).where(Signal.id == signal_id))).unique().scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail="Signal not found")
    feats = await session.get(SignalFeature, s.id)
    d = signal_to_dict(s)
    d["features"] = feats.features if feats else {}
    return d


def _stats(rows: list[tuple[Signal, SignalOutcome]]) -> dict:
    closed = [(s, o) for s, o in rows if o.status in CLOSED and o.r_multiple is not None]
    rs = [o.r_multiple for _, o in closed]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    return {
        "signals": len(rows),
        "closed": len(closed),
        "open": sum(1 for _, o in rows if o.status == "OPEN"),
        "pending": sum(1 for _, o in rows if o.status == "PENDING"),
        "not_triggered": sum(1 for _, o in rows if o.status in NOT_TRIGGERED),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_r": float(np.mean(rs)) if rs else None,
        "total_r": float(np.sum(rs)) if rs else 0.0,
        "avg_planned_rr": float(np.mean([s.rr_tp1 for s, _ in closed if s.rr_tp1])) if closed else None,
        "profit_factor": (gp / gl) if gl > 0 else (None if gp == 0 else "inf"),
    }


def _group(rows, keyfn) -> list[dict]:
    groups: dict[str, list] = {}
    for s, o in rows:
        groups.setdefault(keyfn(s), []).append((s, o))
    return sorted(({"key": k, **_stats(v)} for k, v in groups.items()), key=lambda x: -x["signals"])


@router.get("/performance")
async def performance(days: int = Query(90, ge=1, le=3650), asset_id: int | None = None,
                      user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Audit of every ACTIONABLE signal the engine issued (losers included). R multiples are gross of fees,
    measured on closed candles with the same conservative rules as the backtester."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (select(Signal, SignalOutcome).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
         .where(Signal.status == "ACTIONABLE", Signal.bar_time >= since).order_by(Signal.bar_time))
    if asset_id:
        q = q.where(Signal.asset_id == asset_id)
    rows = [(s, o) for s, o in (await session.execute(q)).unique().all()]
    assets = {a.id: a.symbol for a in (await session.execute(select(Asset))).scalars()}
    closed = [(s, o) for s, o in rows if o.status in CLOSED and o.r_multiple is not None]
    closed.sort(key=lambda x: x[1].exit_at or x[0].bar_time)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    curve = []
    for s, o in closed:
        cum += o.r_multiple
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        curve.append({"time": (o.exit_at or s.bar_time).isoformat(), "cum_r": round(cum, 4), "signal_id": s.id})
    wait_count = (await session.execute(select(func.count(Signal.id)).where(Signal.status == "WAIT", Signal.bar_time >= since))).scalar()
    out = {
        "window_days": days,
        "overall": _stats(rows),
        "max_drawdown_r": max_dd,
        "equity_curve_r": curve,
        "evaluations_wait": wait_count,
        "by_asset": _group(rows, lambda s: assets.get(s.asset_id, str(s.asset_id))),
        "by_timeframe": _group(rows, lambda s: s.timeframe),
        "by_regime": _group(rows, lambda s: s.regime),
        "by_setup": _group(rows, lambda s: s.setup_type or "unknown"),
        "by_direction": _group(rows, lambda s: s.direction),
        "by_outcome": {st: sum(1 for _, o in rows if o.status == st) for st in
                       ("PENDING", "OPEN", *CLOSED, *NOT_TRIGGERED)},
        "methodology": [
            "Every ACTIONABLE signal is tracked; nothing is removed or hidden.",
            "Entry fills only if price trades through the entry reference before expiry.",
            "Stop assumed hit first when stop and target share a candle.",
            "TP1 closes part of the position and the stop moves to breakeven (as configured per strategy).",
            "R multiples are gross of fees and slippage.",
        ],
    }
    return _finite(out)


def _finite(o):
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_finite(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o
