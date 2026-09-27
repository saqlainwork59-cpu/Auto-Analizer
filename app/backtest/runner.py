"""Executes queued backtests (called by the worker)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import StrategyParams
from app.backtest.engine import BacktestConfig, run_backtest
from app.config import get_settings
from app.core.timeframes import ORDER, delta
from app.data.backfill import backfill_range
from app.data.providers.base import ProviderError
from app.data.store import load_frame
from app.models import Asset, Backtest, BacktestTrade, utcnow


async def execute(session: AsyncSession, bt: Backtest) -> None:
    p = bt.params
    asset = await session.get(Asset, int(p["asset_id"]))
    if asset is None:
        raise ValueError("market not found")
    params = StrategyParams.model_validate(bt.strategy_snapshot)
    tf = p["timeframe"]
    start = datetime.fromisoformat(p["start"]).astimezone(timezone.utc)
    end = datetime.fromisoformat(p["end"]).astimezone(timezone.utc)
    tfs = sorted(set(get_settings().context_tf_list) | {tf}, key=ORDER.index)
    tfs = [t for t in tfs if ORDER.index(t) >= ORDER.index(tf)]  # lower timeframes are not used by the engine
    warm = {t: delta(t) * (params.min_bars + 250) for t in tfs}
    notes = []
    dfs = {}
    for t in tfs:
        df = await load_frame(session, asset.id, t, start=start - warm[t], end=end)
        need_from = start - warm[t]
        if df.empty or df.index[0] > need_from + delta(t) * 5:
            try:
                await backfill_range(session, asset, t, need_from, end)
                df = await load_frame(session, asset.id, t, start=start - warm[t], end=end)
            except ProviderError as exc:
                notes.append(f"{t}: history download failed ({exc}); using stored data only")
        dfs[t] = df
    if dfs[tf].empty:
        raise ValueError("no historical data available for this market/timeframe (provider unavailable or not configured)")

    progress = {"v": 0.0}

    def cb(v: float) -> None:
        progress["v"] = v

    task = asyncio.create_task(asyncio.to_thread(
        run_backtest, dfs, params,
        BacktestConfig(timeframe=tf, start=start, end=end, starting_capital=float(p["starting_capital"]),
                       risk_per_trade_pct=float(p["risk_per_trade_pct"]), fee_pct=float(p["fee_pct"]),
                       slippage_pct=float(p["slippage_pct"]), max_leverage=float(p.get("max_leverage", 1.0)),
                       periods_per_year=365 if asset.calendar == "24x7" else 252, symbol=asset.symbol),
        cb,
    ))
    while not task.done():
        await asyncio.sleep(1.0)
        bt.progress = round(progress["v"], 3)
        await session.commit()
    res = task.result()
    for tr in res.trades:
        session.add(BacktestTrade(backtest_id=bt.id, **{k: tr[k] for k in (
            "direction", "setup_type", "regime", "score", "signal_time", "entry_time", "exit_time", "entry_price",
            "exit_price", "stop_loss", "tp1", "tp2", "quantity", "outcome", "r_multiple", "pnl", "fees", "equity_after")}))
    bt.metrics = _clean({**res.metrics, "breakdowns": res.breakdowns})
    bt.equity_curve = res.equity_curve
    first = {t: (df.index[0].isoformat() if len(df) else None) for t, df in dfs.items()}
    bt.data_summary = _clean({**res.data_summary, "first_candle_by_timeframe": first, "notes": notes})
    bt.status, bt.progress, bt.finished_at = "done", 1.0, utcnow()
    await session.commit()


def _clean(obj):
    import math

    import numpy as np

    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

