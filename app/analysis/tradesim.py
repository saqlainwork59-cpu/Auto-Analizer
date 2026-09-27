"""Bar-by-bar trade simulation shared by the signal-outcome tracker, the backtester and the ML
labeller - so "how did this signal do?" is answered identically everywhere.

Conservative assumptions (documented, and shown in the UI):
  * Entry is a limit order at the entry reference; it fills only if price trades through it.
    A gap through the limit fills at the (better) open.
  * On the fill bar only the stop is checked (we cannot know whether the target printed after the fill).
  * If the stop and a target both lie inside one candle, the stop is assumed to have been hit first.
  * Gaps through the stop exit at the (worse) open. Stops and time exits pay slippage; limit targets do not.
  * If price reaches TP1 before the entry fills, the setup is recorded as EXPIRED (move missed).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SimResult:
    status: str                    # PENDING | OPEN | EXPIRED | INVALIDATED | LOSS | WIN_TP1 | WIN_TP2 | TIME_EXIT | BREAKEVEN
    complete: bool
    fill_idx: int | None = None
    fill_price: float | None = None
    exit_idx: int | None = None
    exit_price: float | None = None    # size-weighted average exit
    r_multiple: float | None = None    # gross, in units of planned risk
    mfe_r: float = 0.0
    mae_r: float = 0.0
    legs: list[tuple[float, float, str]] = field(default_factory=list)  # (fraction, price, reason)
    note: str = ""


def simulate(
    d: int,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    *,
    expiry_bars: int,
    max_hold_bars: int,
    tp1_fraction: float = 0.5,
    move_stop_to_breakeven: bool = True,
    slippage_pct: float = 0.0,
) -> SimResult:
    """Simulate from the first bar *after* the signal bar (arrays start there)."""
    risk = d * (entry - stop)
    if risk <= 0:
        raise ValueError("stop must be on the losing side of entry")
    n = len(o)
    fav = h if d > 0 else low     # favourable extreme
    adv = low if d > 0 else h     # adverse extreme

    def slip(price: float) -> float:
        return price * (1 - d * slippage_pct)  # long exits sell lower, short exits buy higher

    fill_idx: int | None = None
    fill_price = 0.0
    cur_stop = stop
    remaining = 1.0
    legs: list[tuple[float, float, str]] = []
    tp1_done = False
    mfe = mae = 0.0

    for k in range(n):
        if fill_idx is None:
            if k >= expiry_bars:
                return SimResult("EXPIRED", True, note="entry not reached before expiry")
            if d * (o[k] - stop) <= 0:
                return SimResult("INVALIDATED", True, note="price gapped through the stop before entry")
            touched = low[k] <= entry if d > 0 else h[k] >= entry
            if not touched:
                if d * (fav[k] - tp1) >= 0:
                    return SimResult("EXPIRED", True, note="TP1 reached before entry - move missed")
                continue
            fill_idx = k
            fill_price = min(o[k], entry) if d > 0 else max(o[k], entry)
            mae = min(mae, d * (adv[k] - fill_price) / risk)
            if d * (adv[k] - cur_stop) <= 0:
                legs.append((1.0, slip(cur_stop), "stop"))
                remaining = 0.0
                return _finish("LOSS", fill_idx, fill_price, k, legs, entry, risk, d, mfe, mae)
            continue

        # ---- in position
        mfe = max(mfe, d * (fav[k] - fill_price) / risk)
        mae = min(mae, d * (adv[k] - fill_price) / risk)
        if d * (adv[k] - cur_stop) <= 0:
            px = o[k] if d * (o[k] - cur_stop) <= 0 else cur_stop
            reason = "breakeven stop" if tp1_done and cur_stop == entry else "stop"
            legs.append((remaining, slip(px), reason))
            status = "WIN_TP1" if tp1_done else "LOSS"
            if tp1_done and tp1_fraction == 0:
                status = "BREAKEVEN"
            return _finish(status, fill_idx, fill_price, k, legs, entry, risk, d, mfe, mae)
        if not tp1_done and d * (fav[k] - tp1) >= 0:
            px = max(o[k], tp1) if d > 0 else min(o[k], tp1)
            frac = min(tp1_fraction, remaining)
            if frac > 0:
                legs.append((frac, px, "tp1"))
                remaining -= frac
            tp1_done = True
            if move_stop_to_breakeven:
                cur_stop = entry
            if remaining <= 1e-12:
                return _finish("WIN_TP1", fill_idx, fill_price, k, legs, entry, risk, d, mfe, mae)
        if tp1_done and d * (fav[k] - tp2) >= 0:
            px = max(o[k], tp2) if d > 0 else min(o[k], tp2)
            legs.append((remaining, px, "tp2"))
            return _finish("WIN_TP2", fill_idx, fill_price, k, legs, entry, risk, d, mfe, mae)
        if k - fill_idx >= max_hold_bars:
            legs.append((remaining, slip(c[k]), "time exit"))
            return _finish("WIN_TP1" if tp1_done else "TIME_EXIT", fill_idx, fill_price, k, legs, entry, risk, d, mfe, mae)

    if fill_idx is None:
        return SimResult("PENDING", False, note="awaiting entry")
    res = SimResult("OPEN", False, fill_idx, fill_price, mfe_r=mfe, mae_r=mae, legs=legs, note="position open")
    return res


def _finish(status: str, fill_idx: int, fill_price: float, k: int, legs: list, entry: float, risk: float, d: int,
            mfe: float, mae: float) -> SimResult:
    total = sum(f for f, _, _ in legs)
    avg_exit = sum(f * p for f, p, _ in legs) / total if total > 0 else fill_price
    r = sum(f * d * (p - fill_price) for f, p, _ in legs) / risk
    return SimResult(status, True, fill_idx, float(fill_price), k, float(avg_exit), float(r), float(mfe), float(mae), legs)
