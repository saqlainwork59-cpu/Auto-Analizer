"""Live analysis: load closed candles from the database, run the engine, persist everything."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import quality as dq
from app.analysis.engine import Evaluation, evaluate
from app.analysis.params import StrategyParams
from app.analysis.prepare import PreparedFrame, prepare
from app.config import get_settings
from app.core import flags
from app.core.bus import CH_SIGNALS, bus
from app.core.timeframes import ORDER, delta, last_closed_open_time, seconds
from app.data.store import load_frame
from app.models import Asset, IndicatorSnapshot, Signal, SignalFeature, SignalOutcome
from app.signals.serialize import signal_to_dict
from app.signals.strategy import get_active, version_label

log = logging.getLogger(__name__)
HISTORY_BARS = 700  # per timeframe; comfortably above min_bars + indicator warm-up


async def load_frames(session: AsyncSession, asset: Asset, now: datetime, params: StrategyParams,
                      timeframes: list[str] | None = None) -> tuple[dict[str, PreparedFrame], dict]:
    frames: dict[str, PreparedFrame] = {}
    raw: dict = {}
    for tf in timeframes or get_settings().context_tf_list:
        df = await load_frame(session, asset.id, tf, limit=max(HISTORY_BARS, params.min_bars + 300), closed_by=now)
        raw[tf] = df
        if len(df) >= 60:
            frames[tf] = prepare(df, tf, params)
    return frames, raw


async def analyze(session: AsyncSession, asset: Asset, tf: str, *, now: datetime | None = None,
                  persist: bool = True, publish: bool = True) -> tuple[Evaluation, Signal | None]:
    now = now or datetime.now(timezone.utc)
    strategy, params = await get_active(session)
    # only the trading timeframe and higher ones feed the engine (lower timeframes are not used)
    tfs = sorted({t for t in get_settings().context_tf_list if ORDER.index(t) >= ORDER.index(tf)} | {tf}, key=ORDER.index)
    frames, raw = await load_frames(session, asset, now, params, tfs)
    quality = dq.check(raw[tf] if tf in raw else _empty(), tf, min_bars=params.min_bars,
                       calendar=asset.calendar, now=now)

    from app.ml.registry import production_scorer  # local import: ML is optional

    scorer, model_row = await production_scorer(session)
    pf = frames.get(tf)
    t = len(pf) - 1 if pf is not None else -1
    ev = evaluate(frames, tf, t, params, symbol=asset.symbol, quality=quality, ml_scorer=scorer)

    if ev.actionable and not await flags.get_flag("signals_enabled", session):
        ev.status, ev.direction = "WAIT", "WAIT"
        ev.headline = "WAIT — signal engine paused by administrator"
        ev.explanation = "The signal engine is paused by an administrator (emergency control). No trade signals are issued."

    if ev.bar_time is None:
        ev.bar_time = last_closed_open_time(now, tf) + delta(tf)

    if not persist:
        return ev, None

    existing = (
        await session.execute(
            select(Signal).where(Signal.asset_id == asset.id, Signal.timeframe == tf, Signal.bar_time == ev.bar_time,
                                 Signal.strategy_version == version_label(strategy))
        )
    ).unique().scalar_one_or_none()
    if existing is not None:
        return ev, existing

    sig = to_signal_row(ev, asset, strategy.id, version_label(strategy), params,
                        model_row.id if (model_row is not None and ev.ml_probability is not None) else None)
    session.add(sig)
    await session.flush()
    session.add(SignalFeature(signal_id=sig.id, features=ev.features or {}))
    if ev.actionable:
        session.add(SignalOutcome(signal_id=sig.id, status="PENDING"))
    if pf is not None and t >= 0:
        from app.analysis.engine import _indicator_snapshot

        await session.merge(IndicatorSnapshot(asset_id=asset.id, timeframe=tf, open_time=pf.df.index[t].to_pydatetime(),
                                      values=_indicator_snapshot(pf, t)))
    try:
        await session.commit()
    except Exception:  # duplicate from a concurrent worker; keep the first write
        await session.rollback()
        raise
    await session.refresh(sig)
    if publish:
        await bus.publish(CH_SIGNALS, {"type": "signal", "signal": signal_to_dict(sig, asset)})
    return ev, sig


def _empty():
    import pandas as pd

    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC"))


def to_signal_row(ev: Evaluation, asset: Asset, strategy_id: int | None, strategy_version: str, params: StrategyParams,
                  model_version_id: int | None) -> Signal:
    lv = ev.levels
    return Signal(
        bar_time=ev.bar_time,
        asset_id=asset.id,
        timeframe=ev.timeframe,
        status=ev.status,
        direction=ev.direction,
        setup_type=ev.setup_type,
        regime=ev.regime,
        score=float(ev.score or 0.0),
        entry_low=lv.entry_low if lv and ev.actionable else None,
        entry_high=lv.entry_high if lv and ev.actionable else None,
        entry_ref=lv.entry_ref if lv and ev.actionable else None,
        stop_loss=lv.stop if lv and ev.actionable else None,
        tp1=lv.tp1 if lv and ev.actionable else None,
        tp2=lv.tp2 if lv and ev.actionable else None,
        rr_tp1=lv.rr1 if lv else None,
        rr_tp2=lv.rr2 if lv else None,
        expires_at=ev.expires_at,
        headline=ev.headline[:200],
        explanation=ev.explanation,
        reasons=ev.reasons,
        risks=ev.risks,
        invalidation=ev.invalidation,
        components=[c.as_dict() for c in ev.components],
        mtf=_jsonable(ev.mtf),
        levels_meta={
            "methods": lv.methods if lv else {},
            "risk_atr": lv.risk_atr if lv else None,
            "zones": ev.zones,
            "candidate_direction": ev.candidate_direction,
            # the exact levels of a rejected candidate are kept for auditing, but not presented as a trade
            "candidate_levels": ({"entry_ref": lv.entry_ref, "stop": lv.stop, "tp1": lv.tp1, "tp2": lv.tp2}
                                 if lv and not ev.actionable else None),
            "sim": {"expiry_bars": params.expiry_bars, "max_hold_bars": params.max_hold_bars,
                    "tp1_fraction": params.tp1_fraction, "move_stop_to_breakeven": params.move_stop_to_breakeven},
            "min_score": params.min_score,
            "min_rr": params.min_rr,
        },
        data_quality=ev.data_quality,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        engine_version=ev.engine_version,
        model_version_id=model_version_id,
        ml_probability=ev.ml_probability,
    )


def _jsonable(obj):
    import numpy as np

    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def bar_is_due(now: datetime, tf: str, last_analyzed: datetime | None) -> bool:
    expected = last_closed_open_time(now, tf) + timedelta(seconds=seconds(tf))
    return last_analyzed is None or last_analyzed < expected


__all__ = ["analyze", "load_frames", "ORDER"]
