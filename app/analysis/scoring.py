"""Transparent setup scoring. Each component returns a sub-score in [0, 1] plus the evidence
(computed facts) behind it. The final score is the weight-normalised sum x 100.

Components that cannot be evaluated (e.g. volume for spot FX, where no centralised volume
exists) are marked `available=False` and excluded from the normalisation - this is shown to
the user rather than silently scored as 0 or 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.analysis.params import StrategyParams
from app.analysis.prepare import PreparedFrame
from app.analysis.setups import Levels, Setup, fmt
from app.analysis.structure import divergence, known_swings, sr_zones


@dataclass
class Component:
    name: str
    weight: float
    score: float
    available: bool = True
    evidence: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
            "score": round(self.score, 4),
            "points": round(self.weight * self.score, 2) if self.available else None,
            "available": self.available,
            "evidence": self.evidence,
            "against": self.against,
        }


def _checks(items: list[tuple[bool, str, str]]) -> tuple[float, list[str], list[str]]:
    ok = [pos for passed, pos, _ in items if passed]
    bad = [neg for passed, _, neg in items if not passed]
    return (len(ok) / len(items) if items else 0.0), ok, bad


def trend_alignment(pf: PreparedFrame, t: int, s: Setup, p: StrategyParams, mtf: dict) -> tuple[float, list, list]:
    d = s.d
    c, e20, e50, e200 = (pf.a(k)[t] for k in ("close", "ema20", "ema50", "ema200"))
    slope = pf.a("ema50_slope")[t]
    if s.setup_type == "range_reversion":
        adx = pf.a("adx14")[t]
        items = [
            (adx < p.regime.adx_range, f"ADX {adx:.1f} confirms a non-trending market", f"ADX {adx:.1f} shows directional pressure"),
            (mtf.get("opposition", 1.0) < 0.5, "higher timeframes are not strongly trending against the trade",
             "higher timeframes are trending against the fade"),
        ]
        return _checks(items)
    up, dn = ("above", "below") if d > 0 else ("below", "above")
    items = [
        (d * (c - e50) > 0, f"price {up} EMA50 ({fmt(e50)})", f"price {dn} EMA50 ({fmt(e50)})"),
        (d * (e20 - e50) > 0, f"EMA20 {up} EMA50", f"EMA20 {dn} EMA50"),
        (np.isfinite(e200) and d * (e50 - e200) > 0, f"EMA50 {up} EMA200 (long-term trend aligned)", "EMA50/EMA200 not aligned"),
        (d * slope > 0.05, f"EMA50 slope {slope:+.2f} ATR/5 bars", f"EMA50 flat/against ({slope:+.2f})"),
    ]
    return _checks(items)


def momentum(pf: PreparedFrame, t: int, s: Setup, p: StrategyParams) -> tuple[float, list, list]:
    d = s.d
    rsi, hist = pf.a("rsi14"), pf.a("macd_hist")
    m, sig = pf.a("macd")[t], pf.a("macd_signal")[t]
    r = rsi[t]
    if s.setup_type == "range_reversion":
        # Fading an extreme: momentum is *expected* to still point against us; what matters is that it is turning.
        stretched = (np.nanmin(rsi[t - 2 : t + 1]) <= 35) if d > 0 else (np.nanmax(rsi[t - 2 : t + 1]) >= 65)
        items = [
            (d * (hist[t] - hist[t - 1]) > 0, "MACD histogram turning in trade direction", "MACD histogram still extending against"),
            (d * (r - rsi[t - 1]) > 0, f"RSI turning {'up' if d > 0 else 'down'} ({rsi[t - 1]:.1f} -> {r:.1f})",
             f"RSI still moving against ({r:.1f})"),
            (stretched, "RSI reached an exhaustion extreme", "RSI only moderately stretched"),
        ]
    else:
        lo, hi = (45, 70) if d > 0 else (30, 55)
        rsi_ok = lo <= r <= hi
        rsi_txt = (f"RSI(14) {r:.1f} in the healthy {lo}-{hi} band", f"RSI(14) {r:.1f} outside the {lo}-{hi} band")
        items = [
            (d * (hist[t] - hist[t - 1]) > 0, "MACD histogram rising in trade direction", "MACD histogram fading"),
            (d * (m - sig) > 0, "MACD line on the correct side of its signal line", "MACD line on the wrong side of its signal line"),
            (rsi_ok, *rsi_txt),
        ]
    score, ok, bad = _checks(items)
    div = divergence(known_swings(pf.swings, t, 80), rsi, t)
    if div:
        if (div == "bullish") == (d > 0):
            score = min(1.0, score + 0.2)
            ok.append(f"{div} RSI divergence supports the trade")
        else:
            score = max(0.0, score - 0.3)
            bad.append(f"{div} RSI divergence opposes the trade")
    return score, ok, bad


def market_structure(pf: PreparedFrame, t: int, s: Setup) -> tuple[float, list, list]:
    d = s.d
    st = pf.a("struct")[t]
    c = pf.a("close")[t]
    lsh, lsl = pf.a("last_swing_high")[t], pf.a("last_swing_low")[t]
    label = {1.0: "bullish (higher highs & higher lows)", -1.0: "bearish (lower highs & lower lows)", 0.0: "mixed / ranging"}.get(
        float(st) if np.isfinite(st) else None, "undefined"
    )
    if s.setup_type == "range_reversion":
        items = [
            (st == 0.0, f"structure is {label}, consistent with a range", f"structure is {label}"),
            (np.isfinite(s.key_level or np.nan), f"range edge defined at {fmt(s.key_level or np.nan)}", "range edge undefined"),
        ]
        return _checks(items)
    protect = lsl if d > 0 else lsh
    items = [
        (np.isfinite(st) and st == d, f"market structure {label}", f"market structure {label}"),
        (np.isfinite(protect) and d * (c - protect) > 0,
         f"price holding {'above the last swing low' if d > 0 else 'below the last swing high'} ({fmt(protect)})",
         "last protective swing has been violated"),
    ]
    score, ok, bad = _checks(items)
    if s.setup_type == "breakout_retest":
        score = min(1.0, score + 0.25)
        ok.append("break of structure in trade direction")
    return score, ok, bad


def volume(pf: PreparedFrame, t: int, s: Setup) -> tuple[float, list, list, bool]:
    if not pf.has_volume:
        return 0.0, [], ["volume not available for this market (excluded from score)"], False
    d = s.d
    v, c, o = pf.a("volume"), pf.a("close"), pf.a("open")
    vz = pf.a("vol_z")
    if s.setup_type == "breakout_retest":
        z = float(np.nanmax(vz[max(0, t - 2) : t + 1]))
        score = 1.0 if z >= 1.5 else 0.7 if z >= 1.0 else 0.3
        return score, [f"breakout volume z-score {z:.1f}"] if score >= 0.7 else [], [] if score >= 0.7 else [f"weak breakout volume (z {z:.1f})"], True
    w = slice(max(0, t - 9), t + 1)
    with_dir = float(np.nansum(np.where(d * (c[w] - o[w]) > 0, v[w], 0.0)))
    against = float(np.nansum(np.where(d * (c[w] - o[w]) < 0, v[w], 0.0)))
    ratio = with_dir / against if against > 0 else 2.0
    items = [
        (ratio > 1.1, f"volume on {'up' if d > 0 else 'down'} bars exceeds counter-moves ({ratio:.2f}x, 10 bars)",
         f"counter-trend volume dominates ({ratio:.2f}x)"),
        (np.isfinite(vz[t]) and vz[t] > -0.5, f"signal-bar volume normal/above ({vz[t]:+.1f} z)", f"signal-bar volume thin ({vz[t]:+.1f} z)"),
    ]
    sc, ok, bad = _checks(items)
    return sc, ok, bad, True


def support_resistance(pf: PreparedFrame, t: int, s: Setup, lv: Levels, p: StrategyParams) -> tuple[float, list, list]:
    d = s.d
    atr, close = pf.a("atr14")[t], pf.a("close")[t]
    zones = sr_zones(known_swings(pf.swings, t, p.zone_lookback), close, atr, t)
    own = sorted(
        (z for z in zones if z.kind == ("support" if d > 0 else "resistance")),
        key=lambda z: d * (lv.entry_ref - (z.high if d > 0 else z.low)),
    )
    near = own[0] if own else None
    dist = (d * (lv.entry_ref - (near.high if d > 0 else near.low)) / atr) if near else np.inf
    word = "support" if d > 0 else "resistance"
    key_near = s.key_level is not None and abs(lv.entry_ref - s.key_level) <= 1.0 * atr
    if near and dist <= 1.0:
        where = f"{word} zone {fmt(near.low)}-{fmt(near.high)}"
        near_txt = f"entry sits on the {where}" if dist <= 0.05 else f"entry {dist:.1f} ATR from the {where}"
    else:
        near_txt = f"entry within 1 ATR of the key level {fmt(s.key_level or np.nan)}"
    items = [
        (dist <= 1.0 or key_near, near_txt,
         f"no defined {word} close to the entry"),
        (bool(near and near.touches >= 2) or s.setup_type == "breakout_retest",
         f"{word} tested {near.touches if near else 1}x" if near else f"broken level acts as {word}",
         f"{word} only tested once"),
        (lv.rr1 >= 2.0, f"room to first target: {lv.rr1:.2f}R", f"first target only {lv.rr1:.2f}R away"),
    ]
    return _checks(items)


def risk_reward(lv: Levels, p: StrategyParams) -> tuple[float, list, list]:
    if lv.rr1 < p.min_rr:
        return 0.0, [], [f"R:R 1:{lv.rr1:.2f} below minimum"]
    score = min(1.0, 0.5 + 0.5 * (lv.rr1 - p.min_rr) / max(1e-9, 3.0 - p.min_rr))
    return score, [f"R:R to TP1 1:{lv.rr1:.2f}, to TP2 1:{lv.rr2:.2f}"], []


def score_setup(pf: PreparedFrame, t: int, s: Setup, lv: Levels, mtf: dict, p: StrategyParams) -> tuple[float, list[Component]]:
    w = p.weights
    comps: list[Component] = []

    sc, ok, bad = trend_alignment(pf, t, s, p, mtf)
    comps.append(Component("trend_alignment", w["trend_alignment"], sc, True, ok, bad))
    sc, ok, bad = momentum(pf, t, s, p)
    comps.append(Component("momentum", w["momentum"], sc, True, ok, bad))
    sc, ok, bad = market_structure(pf, t, s)
    comps.append(Component("market_structure", w["market_structure"], sc, True, ok, bad))
    sc, ok, bad, avail = volume(pf, t, s)
    comps.append(Component("volume", w["volume"], sc, avail, ok, bad))
    sc, ok, bad = support_resistance(pf, t, s, lv, p)
    comps.append(Component("support_resistance", w["support_resistance"], sc, True, ok, bad))
    agree = mtf.get("agreement")
    if agree is None:
        comps.append(Component("multi_timeframe", w["multi_timeframe"], 0.0, True, [], ["higher-timeframe data unavailable"]))
    else:
        comps.append(Component("multi_timeframe", w["multi_timeframe"], (agree + 1) / 2, True, mtf.get("supporting", []),
                                   mtf.get("opposing", []) + mtf.get("neutral", [])))
    sc, ok, bad = risk_reward(lv, p)
    comps.append(Component("risk_reward", w["risk_reward"], sc, True, ok, bad))

    total_w = sum(c.weight for c in comps if c.available)
    total = sum(c.weight * c.score for c in comps if c.available)
    return (100.0 * total / total_w if total_w > 0 else 0.0), comps
