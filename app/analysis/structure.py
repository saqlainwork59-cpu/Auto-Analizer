"""Price-action primitives: swing points, market structure, support/resistance zones,
RSI divergence and candle patterns.

Look-ahead protection: a swing high at bar i needs `right` bars after it to be confirmed, so it
is only *known* at bar i + right. Every consumer filters swings by `confirmed_at <= t`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Swing:
    idx: int            # bar index of the extreme
    confirmed_at: int   # first bar index at which the swing is known
    price: float
    kind: str           # "high" | "low"


def find_swings(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3) -> list[Swing]:
    n = len(high)
    swings: list[Swing] = []
    for i in range(left, n - right):
        h = high[i]
        if h >= high[i - left : i].max() and h > high[i + 1 : i + right + 1].max():
            swings.append(Swing(i, i + right, float(h), "high"))
        lo = low[i]
        if lo <= low[i - left : i].min() and lo < low[i + 1 : i + right + 1].min():
            swings.append(Swing(i, i + right, float(lo), "low"))
    return swings


def known_swings(swings: list[Swing], t: int, lookback: int | None = None) -> list[Swing]:
    lo = t - lookback if lookback else -1
    return [s for s in swings if s.confirmed_at <= t and s.idx > lo]


@dataclass
class Structure:
    label: str                      # bullish | bearish | range | undefined
    last_high: float | None = None
    prev_high: float | None = None
    last_low: float | None = None
    prev_low: float | None = None
    bos: str | None = None          # "up" if close broke the last swing high, "down" if below the last swing low
    notes: list[str] = field(default_factory=list)


def market_structure(swings: list[Swing], close: float) -> Structure:
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return Structure("undefined", notes=["not enough confirmed swings"])
    h1, h2 = highs[-2].price, highs[-1].price
    l1, l2 = lows[-2].price, lows[-1].price
    hh, hl = h2 > h1, l2 > l1
    lh, ll = h2 < h1, l2 < l1
    if hh and hl:
        label = "bullish"
    elif lh and ll:
        label = "bearish"
    else:
        label = "range"
    bos = "up" if close > h2 else "down" if close < l2 else None
    notes = [
        f"{'higher' if hh else 'lower' if lh else 'equal'} high ({h1:.6g} -> {h2:.6g})",
        f"{'higher' if hl else 'lower' if ll else 'equal'} low ({l1:.6g} -> {l2:.6g})",
    ]
    return Structure(label, h2, h1, l2, l1, bos, notes)


@dataclass
class Zone:
    low: float
    high: float
    touches: int
    last_idx: int
    kind: str  # support | resistance

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def as_dict(self) -> dict:
        return {"low": self.low, "high": self.high, "touches": self.touches, "kind": self.kind}


def sr_zones(swings: list[Swing], close: float, atr: float, t: int, min_touches: int = 1) -> list[Zone]:
    """Cluster confirmed swing prices into zones (tolerance 0.6 ATR). Zones are classified relative
    to the current close: fully below = support, fully above = resistance."""
    if not swings or not np.isfinite(atr) or atr <= 0:
        return []
    tol = 0.6 * atr
    pts = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = [[pts[0]]]
    for s in pts[1:]:
        if s.price - clusters[-1][0].price <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    zones: list[Zone] = []
    pad = 0.1 * atr
    for cl in clusters:
        if len(cl) < min_touches:
            continue
        lo, hi = min(s.price for s in cl) - pad, max(s.price for s in cl) + pad
        if hi < close:
            kind = "support"
        elif lo > close:
            kind = "resistance"
        else:
            continue  # price is inside the zone: neither clean support nor resistance
        zones.append(Zone(lo, hi, len(cl), max(s.idx for s in cl), kind))
    return zones


def nearest(zones: list[Zone], price: float, kind: str) -> list[Zone]:
    if kind == "support":
        return sorted((z for z in zones if z.kind == "support"), key=lambda z: price - z.high)
    return sorted((z for z in zones if z.kind == "resistance"), key=lambda z: z.low - price)


def divergence(swings: list[Swing], rsi: np.ndarray, t: int, max_age: int = 10, max_span: int = 60) -> str | None:
    """Regular RSI divergence between the last two confirmed swings of the same kind."""
    for kind, label in (("low", "bullish"), ("high", "bearish")):
        pts = [s for s in swings if s.kind == kind]
        if len(pts) < 2:
            continue
        a, b = pts[-2], pts[-1]
        if t - b.confirmed_at > max_age or b.idx - a.idx > max_span:
            continue
        ra, rb = rsi[a.idx], rsi[b.idx]
        if not (np.isfinite(ra) and np.isfinite(rb)):
            continue
        if kind == "low" and b.price < a.price and rb > ra + 2:
            return label
        if kind == "high" and b.price > a.price and rb < ra - 2:
            return label
    return None


def candle_patterns(o: np.ndarray, h: np.ndarray, low: np.ndarray, c: np.ndarray, t: int) -> list[tuple[str, str]]:
    """Patterns completed at bar t. Returns (name, bias) with bias bullish|bearish|neutral."""
    if t < 1:
        return []
    out: list[tuple[str, str]] = []
    rng = h[t] - low[t]
    if rng <= 0:
        return out
    body = abs(c[t] - o[t])
    upper = h[t] - max(c[t], o[t])
    lower = min(c[t], o[t]) - low[t]
    po, pc = o[t - 1], c[t - 1]
    if c[t] > o[t] and pc < po and c[t] >= po and o[t] <= pc and body > abs(pc - po):
        out.append(("bullish engulfing", "bullish"))
    if c[t] < o[t] and pc > po and c[t] <= po and o[t] >= pc and body > abs(pc - po):
        out.append(("bearish engulfing", "bearish"))
    if lower >= 2 * max(body, 1e-12) and upper <= 0.35 * rng and lower >= 0.55 * rng:
        out.append(("hammer / bullish pin bar", "bullish"))
    if upper >= 2 * max(body, 1e-12) and lower <= 0.35 * rng and upper >= 0.55 * rng:
        out.append(("shooting star / bearish pin bar", "bearish"))
    if body <= 0.1 * rng:
        out.append(("doji", "neutral"))
    if h[t] < h[t - 1] and low[t] > low[t - 1]:
        out.append(("inside bar", "neutral"))
    return out


def recent_extreme(values: pd.Series | np.ndarray, t: int, lookback: int, kind: str) -> float:
    arr = np.asarray(values, dtype=float)[max(0, t - lookback + 1) : t + 1]
    return float(np.nanmin(arr) if kind == "min" else np.nanmax(arr))
