"""Event-driven backtester.

At every closed bar of the trading timeframe the *live* signal engine is evaluated on data that
existed at that moment (indicators are causal; higher timeframes are read at their last fully
closed candle; swings are used only after confirmation). Orders are simulated on the *following*
bars with the shared trade simulator, then fees and slippage are applied and positions sized by
risk. Results are historical and do not predict future performance.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.analysis.engine import evaluate
from app.analysis.params import StrategyParams
from app.analysis.prepare import prepare
from app.analysis.tradesim import simulate
from app.backtest.metrics import breakdown, summarize_trades


@dataclass
class BacktestConfig:
    timeframe: str
    start: datetime
    end: datetime
    starting_capital: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    fee_pct: float = 0.1            # per side, % of notional
    slippage_pct: float = 0.05      # applied to stop / time exits (market orders)
    max_leverage: float = 1.0       # notional cap as a multiple of equity
    periods_per_year: int = 365
    symbol: str = ""

    def validate(self) -> None:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if not (0 < self.risk_per_trade_pct <= 10):
            raise ValueError("risk per trade must be between 0 and 10%")
        if not (0 <= self.fee_pct <= 2):
            raise ValueError("fee must be between 0 and 2% per side")
        if not (0 <= self.slippage_pct <= 2):
            raise ValueError("slippage must be between 0 and 2%")
        if self.starting_capital <= 0:
            raise ValueError("starting capital must be positive")
        if not (0 < self.max_leverage <= 10):
            raise ValueError("max leverage must be between 0 and 10")


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[dict]
    trades: list[dict]
    data_summary: dict
    breakdowns: dict = field(default_factory=dict)


def run_backtest(
    dfs: dict[str, pd.DataFrame],
    params: StrategyParams,
    cfg: BacktestConfig,
    progress: Callable[[float], None] | None = None,
) -> BacktestResult:
    cfg.validate()
    tf = cfg.timeframe
    if tf not in dfs or dfs[tf].empty:
        raise ValueError(f"no {tf} data available for the requested market")
    frames = {k: prepare(v, k, params) for k, v in dfs.items() if len(v) >= 60}
    pf = frames[tf]
    idx = pf.df.index
    o, h, lo, c = pf.a("open"), pf.a("high"), pf.a("low"), pf.a("close")
    start_ep, end_ep = int(cfg.start.timestamp()), int(cfg.end.timestamp())
    in_range = np.flatnonzero((pf.close_times >= start_ep) & (pf.close_times <= end_ep))
    if len(in_range) == 0:
        raise ValueError("no closed candles inside the requested date range")
    first_t = max(int(in_range[0]), params.min_bars)
    last_t = int(in_range[-1])

    equity = cfg.starting_capital
    eq_times: list = [datetime.fromtimestamp(int(pf.close_times[first_t]), tz=timezone.utc)]
    eq_vals: list[float] = [equity]
    trades: list[dict] = []
    signals = unfilled = evaluated = 0
    t = first_t
    total = max(1, last_t - first_t)
    while t <= last_t:
        if progress and (t - first_t) % 250 == 0:
            progress((t - first_t) / total)
        ev = evaluate(frames, tf, t, params, symbol=cfg.symbol)
        evaluated += 1
        if not ev.actionable or ev.levels is None:
            t += 1
            continue
        signals += 1
        lv = ev.levels
        d = 1 if ev.direction == "BUY" else -1
        a = t + 1
        b = min(last_t + 1, a + params.expiry_bars + params.max_hold_bars + 1)
        res = simulate(d, lv.entry_ref, lv.stop, lv.tp1, lv.tp2, o[a:b], h[a:b], lo[a:b], c[a:b],
                       expiry_bars=params.expiry_bars, max_hold_bars=params.max_hold_bars,
                       tp1_fraction=params.tp1_fraction, move_stop_to_breakeven=params.move_stop_to_breakeven,
                       slippage_pct=cfg.slippage_pct / 100)
        if res.fill_idx is None:
            unfilled += 1
            # the pending order occupied the slot until it expired/was invalidated
            t = a + (min(params.expiry_bars, b - a) if res.status in ("EXPIRED", "PENDING") else 0)
            continue
        fill = float(res.fill_price)
        risk_unit = abs(fill - lv.stop)
        qty = (equity * cfg.risk_per_trade_pct / 100) / risk_unit
        qty = min(qty, equity * cfg.max_leverage / fill)
        legs = list(res.legs)
        status = res.status
        exit_i = res.exit_idx
        if not res.complete:  # data ended with the position open: mark to the last close
            legs.append((1.0 - sum(f for f, _, _ in legs), float(c[b - 1]), "end of data"))
            status, exit_i = "END_OF_DATA", b - 1 - a
        gross = sum(f * qty * d * (p - fill) for f, p, _ in legs)
        fees = cfg.fee_pct / 100 * (qty * fill + sum(f * qty * p for f, p, _ in legs))
        pnl = gross - fees
        equity += pnl
        exit_bar = a + exit_i
        exit_time = datetime.fromtimestamp(int(pf.close_times[exit_bar]), tz=timezone.utc)
        avg_exit = sum(f * p for f, p, _ in legs) / max(1e-12, sum(f for f, _, _ in legs))
        trades.append({
            "direction": ev.direction,
            "setup_type": ev.setup_type,
            "regime": ev.regime,
            "score": ev.score,
            "signal_time": ev.bar_time,
            "entry_time": idx[a + res.fill_idx].to_pydatetime(),
            "exit_time": exit_time,
            "entry_price": fill,
            "exit_price": avg_exit,
            "stop_loss": lv.stop,
            "tp1": lv.tp1,
            "tp2": lv.tp2,
            "quantity": qty,
            "outcome": status,
            "planned_rr": lv.rr1,
            "r_multiple": pnl / (qty * risk_unit),
            "pnl": pnl,
            "fees": fees,
            "equity_after": equity,
        })
        eq_times.append(exit_time)
        eq_vals.append(equity)
        if equity <= 0:
            break
        t = exit_bar + 1
    if progress:
        progress(1.0)

    metrics = summarize_trades(trades, cfg.starting_capital, eq_times, eq_vals, cfg.periods_per_year)
    metrics.update({"signals_generated": signals, "unfilled_signals": unfilled, "bars_evaluated": evaluated})
    data_summary = {
        "requested_start": cfg.start.isoformat(),
        "requested_end": cfg.end.isoformat(),
        "evaluated_from": datetime.fromtimestamp(int(pf.close_times[first_t]), tz=timezone.utc).isoformat(),
        "evaluated_to": datetime.fromtimestamp(int(pf.close_times[last_t]), tz=timezone.utc).isoformat(),
        "bars_by_timeframe": {k: len(v) for k, v in dfs.items()},
        "warmup_bars": params.min_bars,
        "assumptions": [
            "Signals use only candles closed at the decision time; higher timeframes use their last closed candle.",
            "Entries are limit orders at the entry reference; unfilled orders expire.",
            "When stop and target fall inside the same candle the stop is assumed first (conservative).",
            f"Fees {cfg.fee_pct}% per side; slippage {cfg.slippage_pct}% on stop/time exits.",
            "One position at a time; position size = risk % of current equity / stop distance.",
            "Historical results; they do not guarantee or predict future performance.",
        ],
    }
    curve = [{"time": ti.isoformat() if hasattr(ti, "isoformat") else str(ti), "equity": v} for ti, v in zip(eq_times, eq_vals)]
    return BacktestResult(metrics, curve, trades, data_summary, {
        "by_setup": breakdown(trades, "setup_type"),
        "by_regime": breakdown(trades, "regime"),
        "by_direction": breakdown(trades, "direction"),
    })
