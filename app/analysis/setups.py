"""Regime-specific setup detection and the entry / stop-loss / take-profit engine.

The regime decides *which* playbook is allowed:
  TRENDING_UP/DOWN -> trend pullback (with the trend only)
  BREAKOUT         -> breakout retest (in the breakout direction)
  RANGING          -> range reversion (fade the range edges; never trend-follow inside a range)
  LOW_VOLATILITY   -> no trade: compression, wait for expansion
  HIGH_VOLATILITY  -> no trade by default: stops become unreliable
  UNCERTAIN        -> no trade

Levels are derived from structure and volatility (ATR), never from fixed percentages.
Direction is handled with a sign `d` (+1 long, -1 short) so long/short logic is exactly symmetric.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.analysis.params import StrategyParams
from app.analysis.prepare import PreparedFrame
from app.analysis.structure import Zone, candle_patterns, divergence, known_swings, sr_zones


@dataclass
class Setup:
    setup_type: str
    d: int                                  # +1 long / -1 short
    entry_low: float
    entry_high: float
    structural_stop: float                  # before buffer / clamping
    stop_basis: str
    triggers: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    explicit_targets: list[tuple[float, str]] | None = None
    key_level: float | None = None

    @property
    def entry_ref(self) -> float:
        return (self.entry_low + self.entry_high) / 2


@dataclass
class NoSetup:
    reason: str
    detail: list[str] = field(default_factory=list)


@dataclass
class Levels:
    entry_low: float
    entry_high: float
    entry_ref: float
    stop: float
    tp1: float
    tp2: float
    rr1: float
    rr2: float
    risk: float
    risk_atr: float
    notes: list[str]
    methods: dict[str, str]


def fmt(x: float) -> str:
    if not np.isfinite(x):
        return "n/a"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".") if ax < 10 else f"{x:.2f}"
    return f"{x:.6g}"


def _dir_word(d: int) -> str:
    return "bullish" if d > 0 else "bearish"


# ----------------------------------------------------------------------------- playbooks
def trend_pullback(pf: PreparedFrame, t: int, d: int) -> Setup | NoSetup:
    c, h, lo, o = pf.a("close"), pf.a("high"), pf.a("low"), pf.a("open")
    ema20, ema50, atr = pf.a("ema20")[t], pf.a("ema50")[t], pf.a("atr14")[t]
    hist, rsi = pf.a("macd_hist"), pf.a("rsi14")
    w = slice(max(0, t - 7), t + 1)
    touched = (np.min(lo[w]) <= ema20 + 0.25 * atr) if d > 0 else (np.max(h[w]) >= ema20 - 0.25 * atr)
    if not touched:
        dist = d * (c[t] - ema20) / atr
        return NoSetup(
            "trend intact but price is extended from the EMA20 value area - not chasing",
            [f"price is {dist:.1f} ATR {'above' if d > 0 else 'below'} EMA20 with no pullback in the last 8 bars"],
        )
    if d * (c[t] - ema50) < -0.5 * atr:
        return NoSetup("pullback is too deep (price beyond EMA50) - trend may be failing")

    triggers: list[str] = []
    if d * (hist[t] - hist[t - 1]) > 0 and d * (hist[t - 1] - hist[t - 2]) > 0:
        triggers.append(f"MACD histogram has turned {'up' if d > 0 else 'down'} for 2 bars ({hist[t - 2]:.4g} -> {hist[t]:.4g})")
    crossed = any(d * (rsi[k] - 50) > 0 and d * (rsi[k - 1] - 50) <= 0 for k in range(t - 2, t + 1))
    if crossed:
        triggers.append(f"RSI(14) crossed back {'above' if d > 0 else 'below'} 50 (now {rsi[t]:.1f})")
    pats = [name for name, bias in candle_patterns(o, h, lo, c, t) if bias == _dir_word(d)]
    if pats:
        triggers.append(f"{pats[0]} candle on the signal bar")
    if d * (c[t] - (h[t - 1] if d > 0 else lo[t - 1])) > 0:
        triggers.append(f"close {'above the prior bar high' if d > 0 else 'below the prior bar low'}")
    if d * (c[t] - ema20) > 0 and d * (c[t - 1] - pf.a("ema20")[t - 1]) <= 0:
        triggers.append(f"price reclaimed EMA20 ({fmt(ema20)})")
    if len(triggers) < 2:
        return NoSetup(
            "pullback in progress - awaiting momentum confirmation",
            [f"{len(triggers)} of 2 required confirmations present"] + triggers,
        )
    extreme = float(np.min(lo[w])) if d > 0 else float(np.max(h[w]))
    if d > 0:
        entry_high = c[t]
        entry_low = max(c[t] - 0.5 * atr, min(ema20, c[t]) - 0.25 * atr)
    else:
        entry_low = c[t]
        entry_high = min(c[t] + 0.5 * atr, max(ema20, c[t]) + 0.25 * atr)
    return Setup(
        "trend_pullback",
        d,
        float(min(entry_low, entry_high)),
        float(max(entry_low, entry_high)),
        extreme,
        f"pullback {'low' if d > 0 else 'high'} {fmt(extreme)}",
        triggers,
        [f"price pulled back to the EMA20 value area ({fmt(ema20)}) within the last 8 bars"],
        key_level=extreme,
    )


def breakout_retest(pf: PreparedFrame, t: int, d: int) -> Setup | NoSetup:
    c, atr = pf.a("close")[t], pf.a("atr14")[t]
    level = pf.a("breakout_level")[t]
    if not np.isfinite(level):
        return NoSetup("breakout level unavailable")
    ext = d * (c - level) / atr
    triggers = [f"close {'above' if d > 0 else 'below'} the 20-bar {'high' if d > 0 else 'low'} at {fmt(level)}"]
    if pf.has_volume:
        vz = pf.a("vol_z")
        k0 = max(0, t - 2)
        triggers.append(f"breakout volume z-score {np.nanmax(vz[k0 : t + 1]):.1f}")
    context = []
    if d > 0:
        entry_low, entry_high = level, level + 0.35 * atr if ext > 0.35 else c
    else:
        entry_high, entry_low = level, level - 0.35 * atr if ext > 0.35 else c
    if ext > 2.0:
        context.append(f"price is {ext:.1f} ATR beyond the breakout level - entry requires a retest (limit order)")
    stop = level - d * 0.75 * atr
    return Setup(
        "breakout_retest",
        d,
        float(min(entry_low, entry_high)),
        float(max(entry_low, entry_high)),
        float(stop),
        f"0.75 ATR back inside the broken level {fmt(level)}",
        triggers,
        context,
        key_level=float(level),
    )


def range_reversion(pf: PreparedFrame, t: int) -> Setup | NoSetup:
    c, h, lo, o = pf.a("close"), pf.a("high"), pf.a("low"), pf.a("open")
    rsi = pf.a("rsi14")
    atr = pf.a("atr14")[t]
    top, bot = pf.a("don_high20")[t], pf.a("don_low20")[t]
    if not (np.isfinite(top) and np.isfinite(bot)) or top - bot < 2 * atr:
        return NoSetup("range too narrow relative to volatility for a mean-reversion trade")
    pos = (c[t] - bot) / (top - bot)
    if c[t] > top + 0.25 * atr or c[t] < bot - 0.25 * atr:
        return NoSetup(
            "price closed decisively outside the range - not fading a possible breakout",
            [f"close {fmt(c[t])} vs range {fmt(bot)}-{fmt(top)}"],
        )
    if 0.25 < pos < 0.75:
        return NoSetup("price is mid-range - no edge", [f"range position {pos:.0%} (edges are <25% / >75%)"])
    d = 1 if pos <= 0.25 else -1
    r3 = rsi[t - 2 : t + 1]
    extreme_ok = (np.nanmin(r3) <= 38) if d > 0 else (np.nanmax(r3) >= 62)
    if not extreme_ok:
        return NoSetup(
            f"at range {'support' if d > 0 else 'resistance'} but RSI not stretched",
            [f"RSI(14) range over last 3 bars {np.nanmin(r3):.1f}-{np.nanmax(r3):.1f}"],
        )
    triggers = [f"RSI(14) reached {np.nanmin(r3) if d > 0 else np.nanmax(r3):.1f} at the range {'low' if d > 0 else 'high'}"]
    pats = [name for name, bias in candle_patterns(o, h, lo, c, t) if bias == _dir_word(d)]
    if pats:
        triggers.append(f"{pats[0]} rejection candle")
    div = divergence(known_swings(pf.swings, t, 80), rsi, t)
    if div == _dir_word(d):
        triggers.append(f"{div} RSI divergence")
    if d * (rsi[t] - rsi[t - 1]) > 0 and d * (rsi[t - 1] - rsi[t - 2]) > 0:
        triggers.append("RSI turning back toward the range middle")
    if len(triggers) < 2:
        return NoSetup("at range edge but no rejection / reversal confirmation yet", triggers)
    w = slice(max(0, t - 4), t + 1)
    extreme = float(min(np.min(lo[w]), bot)) if d > 0 else float(max(np.max(h[w]), top))
    if d > 0:
        entry_low, entry_high = c[t] - 0.3 * atr, c[t]
    else:
        entry_low, entry_high = c[t], c[t] + 0.3 * atr
    mid = (top + bot) / 2
    far = (top - 0.2 * atr) if d > 0 else (bot + 0.2 * atr)
    return Setup(
        "range_reversion",
        d,
        float(entry_low),
        float(entry_high),
        extreme,
        f"beyond the rejection {'low' if d > 0 else 'high'} {fmt(extreme)}",
        triggers,
        [f"20-bar range {fmt(bot)} - {fmt(top)}, price at {pos:.0%} of the range"],
        explicit_targets=[(float(mid), "range midpoint"), (float(far), "opposite range edge")],
        key_level=float(bot if d > 0 else top),
    )


def detect(pf: PreparedFrame, t: int, regime: str, p: StrategyParams) -> Setup | NoSetup:
    if regime == "TRENDING_UP" and "trend_pullback" in p.allow_setups:
        return trend_pullback(pf, t, 1)
    if regime == "TRENDING_DOWN" and "trend_pullback" in p.allow_setups:
        return trend_pullback(pf, t, -1)
    if regime == "BREAKOUT" and "breakout_retest" in p.allow_setups:
        d = int(pf.a("breakout_dir")[t])
        return breakout_retest(pf, t, d) if d else NoSetup("breakout direction ambiguous")
    if regime == "RANGING" and "range_reversion" in p.allow_setups:
        return range_reversion(pf, t)
    if regime == "LOW_VOLATILITY":
        return NoSetup("volatility compression - waiting for a confirmed expansion")
    if regime == "HIGH_VOLATILITY" and not p.allow_high_volatility:
        return NoSetup("volatility is extreme (top percentile) - stops are unreliable, standing aside")
    if regime == "UNCERTAIN":
        return NoSetup("market regime is uncertain - no playbook applies")
    return NoSetup(f"no enabled playbook for regime {regime}")


# ----------------------------------------------------------------------------- levels
def build_levels(setup: Setup, pf: PreparedFrame, t: int, p: StrategyParams) -> Levels | NoSetup:
    d = setup.d
    atr = float(pf.a("atr14")[t])
    close = float(pf.a("close")[t])
    entry = setup.entry_ref
    notes: list[str] = []
    methods: dict[str, str] = {"entry": "structure/value-area zone", "stop": setup.stop_basis}

    stop = setup.structural_stop - d * p.stop_buffer_atr * atr
    # Never park a stop inside (or just in front of) a support/resistance zone that protects the trade:
    # push it beyond the far edge of that zone so a normal re-test does not stop the position out.
    own_kind = "support" if d > 0 else "resistance"
    own = [z for z in sr_zones(known_swings(pf.swings, t, p.zone_lookback), close, atr, t) if z.kind == own_kind]
    own.sort(key=lambda z: -d * z.mid)  # nearest to entry first
    for z in own:
        near_edge, far_edge = (z.high, z.low) if d > 0 else (z.low, z.high)
        if d * (near_edge + d * 0.25 * atr - stop) >= 0 and d * (stop - far_edge) >= 0:
            stop = far_edge - d * p.stop_buffer_atr * atr
            methods["stop"] += f", moved beyond the {own_kind} zone {fmt(z.low)}-{fmt(z.high)}"
    risk = d * (entry - stop)
    if risk < p.min_stop_atr * atr:
        stop = entry - d * p.min_stop_atr * atr
        methods["stop"] += f" (widened to the {p.min_stop_atr} ATR noise floor)"
        notes.append(f"structural stop was inside normal noise; widened to {p.min_stop_atr} ATR")
    risk = d * (entry - stop)
    if risk > p.max_stop_atr * atr:
        return NoSetup(
            "structure-based stop would be too wide",
            [f"stop distance {risk / atr:.1f} ATR exceeds the {p.max_stop_atr} ATR limit"],
        )
    # the entire entry zone must sit on the correct side of the stop
    if (d > 0 and stop >= setup.entry_low) or (d < 0 and stop <= setup.entry_high):
        return NoSetup("stop would sit inside the entry zone - invalid geometry")

    targets: list[tuple[float, str]] = []
    if setup.explicit_targets:
        targets = [(tp, m) for tp, m in setup.explicit_targets if d * (tp - entry) > 0]
    else:
        swings = known_swings(pf.swings, t, p.zone_lookback)
        zones = sr_zones(swings, close, atr, t)
        opp = sorted(
            (z for z in zones if (z.kind == "resistance" if d > 0 else z.kind == "support")),
            key=lambda z: d * ((z.low if d > 0 else z.high) - entry),
        )
        for z in opp:
            edge = (z.low if d > 0 else z.high) - d * 0.05 * atr
            if d * (edge - entry) > 0:
                targets.append((edge, f"{'resistance' if d > 0 else 'support'} zone {fmt(z.low)}-{fmt(z.high)} ({z.touches} touch{'es' if z.touches > 1 else ''})"))
        if setup.setup_type == "breakout_retest" and setup.key_level is not None:
            height = float(pf.a("don_high20")[t] - pf.a("don_low20")[t])
            if np.isfinite(height) and height > 0:
                targets.append((setup.key_level + d * height, "measured move (range height projected)"))
                targets.sort(key=lambda x: d * (x[0] - entry))
    if not targets:
        targets = [(entry + d * 2.0 * atr, "2.0 ATR volatility projection (no opposing structure)")]
    tp1, m1 = targets[0]
    later = [x for x in targets[1:] if d * (x[0] - tp1) >= 0.5 * atr]
    tp2, m2 = later[0] if later else (tp1 + d * 1.5 * atr, "TP1 + 1.5 ATR volatility projection")
    methods["tp1"], methods["tp2"] = m1, m2
    rr1 = d * (tp1 - entry) / risk
    rr2 = d * (tp2 - entry) / risk
    lv = Levels(
        setup.entry_low, setup.entry_high, entry, float(stop), float(tp1), float(tp2),
        float(rr1), float(rr2), float(risk), float(risk / atr), notes, methods,
    )
    if rr1 < p.min_rr:
        return NoSetup(
            "reward/risk to the first target is below the configured minimum",
            [f"TP1 ({m1}) at {fmt(tp1)} gives R:R 1:{rr1:.2f} < required 1:{p.min_rr:.2f}"],
        )
    return lv


def zones_for_display(pf: PreparedFrame, t: int, p: StrategyParams, limit: int = 6) -> list[Zone]:
    swings = known_swings(pf.swings, t, p.zone_lookback)
    close, atr = float(pf.a("close")[t]), float(pf.a("atr14")[t])
    zones = sr_zones(swings, close, atr, t)
    zones.sort(key=lambda z: abs(z.mid - close))
    return zones[:limit]
