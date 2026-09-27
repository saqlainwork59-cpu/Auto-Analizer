"""Performance statistics shared by backtests, paper trading and the live signal audit."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(equity: list[float] | np.ndarray) -> tuple[float, float]:
    """Return (max drawdown as a positive fraction, max drawdown in currency)."""
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return 0.0, 0.0
    peak = np.maximum.accumulate(eq)
    dd_abs = peak - eq
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, dd_abs / peak, 0.0)
    return float(dd.max()), float(dd_abs.max())


def streaks(pnls: list[float]) -> tuple[int, int]:
    """(longest winning streak, longest losing streak)."""
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif p < 0:
            cur_l, cur_w = cur_l + 1, 0
        else:
            cur_w = cur_l = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def profit_factor(pnls: list[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    if gl == 0:
        return None if gp == 0 else math.inf
    return gp / gl


def sharpe_from_equity(times: list, equity: list[float], periods_per_year: int) -> float | None:
    """Annualised Sharpe of daily returns of the (realised) equity curve, risk-free rate 0.
    Returns None when there is too little history for the number to mean anything (< 30 days)."""
    if len(times) < 2:
        return None
    s = pd.Series(equity, index=pd.to_datetime(times, utc=True)).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    daily = s.resample("1D").last().ffill()
    if len(daily) < 30:
        return None
    r = daily.pct_change().dropna()
    sd = r.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return None
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def summarize_trades(trades: list[dict], starting_capital: float, equity_times: list, equity_values: list[float],
                     periods_per_year: int) -> dict:
    pnls = [t["pnl"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    final = equity_values[-1] if equity_values else starting_capital
    dd_pct, dd_abs = max_drawdown(equity_values)
    w_streak, l_streak = streaks(pnls)
    pf = profit_factor(pnls)
    n = len(trades)
    return {
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": n - len(wins) - len(losses),
        "win_rate": (len(wins) / n) if n else None,
        "avg_planned_rr": float(np.mean([t["planned_rr"] for t in trades])) if n else None,
        "avg_r_multiple": float(np.mean(rs)) if n else None,
        "expectancy_r": float(np.mean(rs)) if n else None,
        "avg_win": float(np.mean(wins)) if wins else None,
        "avg_loss": float(np.mean(losses)) if losses else None,
        "net_profit": final - starting_capital,
        "net_return_pct": (final / starting_capital - 1) * 100 if starting_capital else None,
        "final_equity": final,
        "max_drawdown_pct": dd_pct * 100,
        "max_drawdown_abs": dd_abs,
        "profit_factor": (None if pf is None else ("inf" if math.isinf(pf) else pf)),
        "sharpe_ratio": sharpe_from_equity(equity_times, equity_values, periods_per_year),
        "avg_trade": float(np.mean(pnls)) if n else None,
        "longest_losing_streak": l_streak,
        "longest_winning_streak": w_streak,
        "fees_paid": float(sum(t.get("fees", 0.0) for t in trades)),
        "sample_size_warning": n < 30,
    }


def breakdown(trades: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        groups.setdefault(str(t.get(key) or "unknown"), []).append(t)
    out = []
    for k, ts in sorted(groups.items()):
        rs = [t["r_multiple"] for t in ts]
        wins = sum(1 for t in ts if t["pnl"] > 0)
        pf = profit_factor([t["pnl"] for t in ts])
        out.append({
            "key": k,
            "trades": len(ts),
            "win_rate": wins / len(ts),
            "avg_r": float(np.mean(rs)),
            "total_r": float(np.sum(rs)),
            "profit_factor": None if pf is None else ("inf" if math.isinf(pf) else pf),
        })
    return out
