"""Signal engine: one deterministic function that turns prepared multi-timeframe data into an
auditable evaluation. The *same* function is used live, in backtests, and to build ML datasets.

Decision order (any failing gate returns WAIT with the reason):
  1. data quality            -> DATA_UNAVAILABLE
  2. regime playbook         -> WAIT (no applicable setup / awaiting confirmation)
  3. level geometry & R:R    -> WAIT (poor reward/risk, stop too wide)
  4. multi-timeframe         -> WAIT - conflicting market structure
  5. score >= min_score      -> otherwise WAIT - evidence too weak
  6. optional ML filter      -> only if an approved model is enabled
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np

from app.analysis import mtf as mtf_mod
from app.analysis.params import ENGINE_VERSION, StrategyParams
from app.analysis.prepare import REGIMES, PreparedFrame
from app.analysis.quality import DataQuality
from app.analysis.scoring import Component, score_setup
from app.analysis.setups import Levels, NoSetup, Setup, build_levels, detect, fmt, zones_for_display
from app.core.timeframes import seconds

SETUP_TYPES = ["trend_pullback", "breakout_retest", "range_reversion"]
SETUP_LABEL = {"trend_pullback": "trend pullback", "breakout_retest": "breakout retest", "range_reversion": "range reversion"}
COMPONENT_LABEL = {
    "trend_alignment": "Trend / regime alignment",
    "momentum": "Momentum",
    "market_structure": "Market structure",
    "volume": "Volume confirmation",
    "support_resistance": "Support / resistance",
    "multi_timeframe": "Multi-timeframe confirmation",
    "risk_reward": "Risk / reward quality",
}


@dataclass
class Evaluation:
    status: str                      # ACTIONABLE | WAIT | DATA_UNAVAILABLE
    direction: str                   # BUY | SELL | WAIT
    timeframe: str
    regime: str
    bar_time: datetime | None        # close time of the analysed candle
    headline: str
    explanation: str
    score: float = 0.0
    setup_type: str | None = None
    levels: Levels | None = None
    components: list[Component] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    mtf: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    zones: list[dict] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)
    expires_at: datetime | None = None
    ml_probability: float | None = None
    candidate_direction: int = 0     # direction of the candidate setup even when the decision is WAIT
    engine_version: str = ENGINE_VERSION

    @property
    def actionable(self) -> bool:
        return self.status == "ACTIONABLE"


def _regime_text(r: str) -> str:
    return r.replace("_", " ")


def _indicator_snapshot(pf: PreparedFrame, t: int) -> dict:
    keys = ["close", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
            "bb_up", "bb_mid", "bb_low", "bb_width", "adx14", "plus_di", "minus_di", "vwap", "vol_z",
            "atr_rank", "bbw_rank", "don_high20", "don_low20", "ema50_slope", "struct", "bias"]
    out = {}
    for k in keys:
        v = pf.a(k)[t]
        out[k] = float(v) if v is not None and np.isfinite(v) else None
    out["regime"] = str(pf.a("regime")[t])
    return out


def build_features(pf: PreparedFrame, t: int, regime: str, setup: Setup | None, lv: Levels | None,
                   mtf: dict, comps: list[Component]) -> dict:
    """Flat numeric feature vector (used for ML research; stored with every signal)."""
    atr = float(pf.a("atr14")[t])
    c = float(pf.a("close")[t])
    d = setup.d if setup else 0

    def g(col: str) -> float:
        v = pf.a(col)[t]
        return float(v) if np.isfinite(v) else np.nan

    f: dict[str, float] = {
        "direction": float(d),
        "rsi14_dir": (g("rsi14") - 50) * d if d else g("rsi14") - 50,
        "macd_hist_atr_dir": g("macd_hist") / atr * d if d else g("macd_hist") / atr,
        "adx14": g("adx14"),
        "atr_rank": g("atr_rank"),
        "bbw_rank": g("bbw_rank"),
        "vol_z": g("vol_z") if pf.has_volume else np.nan,
        "dist_ema20_atr_dir": (c - g("ema20")) / atr * (d or 1),
        "dist_ema50_atr_dir": (c - g("ema50")) / atr * (d or 1),
        "ema50_slope_dir": g("ema50_slope") * (d or 1),
        "struct_dir": g("struct") * (d or 1),
        "bias_dir": g("bias") * (d or 1),
        "mtf_agreement": float(mtf.get("agreement")) if mtf.get("agreement") is not None else np.nan,
        "mtf_opposition": float(mtf.get("opposition")) if mtf.get("opposition") is not None else np.nan,
        "rr1": lv.rr1 if lv else np.nan,
        "rr2": lv.rr2 if lv else np.nan,
        "risk_atr": lv.risk_atr if lv else np.nan,
        "atr_pct": g("atr_pct"),
    }
    for r in REGIMES:
        f[f"regime_{r}"] = 1.0 if regime == r else 0.0
    for s in SETUP_TYPES:
        f[f"setup_{s}"] = 1.0 if (setup and setup.setup_type == s) else 0.0
    for comp in comps:
        f[f"comp_{comp.name}"] = comp.score if comp.available else np.nan
    return {k: (None if (isinstance(v, float) and not np.isfinite(v)) else round(float(v), 6)) for k, v in f.items()}


def _wait(pf: PreparedFrame | None, tf: str, regime: str, bar_time: datetime | None, reason: str, detail: list[str],
          mtf: dict, dq: dict, status: str = "WAIT", **extra) -> Evaluation:
    head = "WAIT — " + reason if status == "WAIT" else "DATA UNAVAILABLE — " + reason
    if status == "WAIT" and not reason.startswith("conflicting"):
        head = "WAIT — No high-quality setup detected"
    expl = (
        f"No trade: {reason}."
        + (" " + " ".join(d.rstrip(".") + "." for d in detail) if detail else "")
        + (f" Market regime: {_regime_text(regime)}." if regime else "")
    )
    if status == "DATA_UNAVAILABLE":
        expl = f"Analysis not performed: {reason}. " + " ".join(detail)
    return Evaluation(status=status, direction="WAIT", timeframe=tf, regime=regime, bar_time=bar_time, headline=head,
                      explanation=expl.strip(), reasons=[], risks=detail, mtf=mtf, data_quality=dq, **extra)


def evaluate(
    frames: dict[str, PreparedFrame],
    tf: str,
    t: int,
    params: StrategyParams,
    *,
    symbol: str = "",
    quality: DataQuality | None = None,
    ml_scorer: Callable[[dict], float | None] | None = None,
) -> Evaluation:
    pf = frames.get(tf)
    dq = quality.as_dict() if quality else {}
    if quality is not None and not quality.ok:
        return _wait(None, tf, "UNCERTAIN", quality.last_close, "data quality check failed", quality.issues, {}, dq,
                     status="DATA_UNAVAILABLE")
    if pf is None or t < 0 or t >= len(pf):
        return _wait(None, tf, "UNCERTAIN", None, "no market data available", [], {}, dq, status="DATA_UNAVAILABLE")
    if t < params.min_bars:
        return _wait(None, tf, "UNCERTAIN", None, "insufficient history",
                     [f"{t + 1} bars available, {params.min_bars} required"], {}, dq, status="DATA_UNAVAILABLE")

    epoch_close = int(pf.close_times[t])
    bar_time = datetime.fromtimestamp(epoch_close, tz=timezone.utc)
    regime = str(pf.a("regime")[t])
    snap = mtf_mod.snapshot(frames, epoch_close)
    zones = [z.as_dict() for z in zones_for_display(pf, t, params)]
    base_mtf = {"timeframes": snap}

    setup = detect(pf, t, regime, params)
    if isinstance(setup, NoSetup):
        feats = build_features(pf, t, regime, None, None, {}, [])
        return _wait(pf, tf, regime, bar_time, setup.reason, setup.detail, base_mtf, dq, zones=zones, features=feats)

    lv = build_levels(setup, pf, t, params)
    mtf = mtf_mod.alignment(snap, tf, setup.d, params.mtf_conflict_threshold)
    if isinstance(lv, NoSetup):
        feats = build_features(pf, t, regime, setup, None, mtf, [])
        return _wait(pf, tf, regime, bar_time, lv.reason, lv.detail, mtf, dq, zones=zones, features=feats,
                     setup_type=setup.setup_type, candidate_direction=setup.d)

    score, comps = score_setup(pf, t, setup, lv, mtf, params)
    feats = build_features(pf, t, regime, setup, lv, mtf, comps)
    side = "BUY" if setup.d > 0 else "SELL"
    common = dict(zones=zones, features=feats, setup_type=setup.setup_type, levels=lv, components=comps,
                  score=round(score, 1), candidate_direction=setup.d)

    if mtf["conflict"]:
        return _wait(pf, tf, regime, bar_time, "conflicting market structure",
                     [f"{side} candidate on {tf} opposed by higher timeframes: " + ", ".join(mtf["opposing"])],
                     mtf, dq, **common)
    if score < params.min_score:
        weakest = sorted((c for c in comps if c.available), key=lambda c: c.score)[:2]
        detail = [f"{side} candidate ({SETUP_LABEL[setup.setup_type]}) scored {score:.0f}/100, below the {params.min_score:.0f} threshold"]
        detail += [f"weak {COMPONENT_LABEL[c.name].lower()}: " + "; ".join(c.against[:2]) for c in weakest if c.against]
        return _wait(pf, tf, regime, bar_time, "evidence too weak", detail, mtf, dq, **common)

    prob = None
    if ml_scorer is not None:
        prob = ml_scorer(feats)
        if params.ml_filter.enabled and prob is not None and prob < params.ml_filter.min_probability:
            return _wait(pf, tf, regime, bar_time, "ML filter rejected the setup",
                         [f"validated model estimates P(TP1 before stop) = {prob:.0%}, below {params.ml_filter.min_probability:.0%}"],
                         mtf, dq, ml_probability=prob, **common)

    reasons: list[str] = list(setup.context) + list(setup.triggers)
    for c in sorted(comps, key=lambda c: -c.weight * c.score):
        if c.available and c.score >= 0.5:
            reasons.extend(e for e in c.evidence if e not in reasons)
    risks: list[str] = []
    for c in comps:
        if c.available:
            risks.extend(a for a in c.against if a not in risks)
        elif c.against:
            risks.extend(c.against)
    risks.extend(lv.notes)
    if regime == "BREAKOUT":
        risks.append("breakouts frequently fail; a close back inside the range invalidates the idea")

    expires_at = bar_time + timedelta(seconds=seconds(tf) * params.expiry_bars)
    word_stop = "below" if setup.d > 0 else "above"
    invalidation = [
        f"{tf} candle closes {word_stop} {fmt(lv.stop)} (stop-loss) - setup invalid",
        f"entry zone {fmt(lv.entry_low)}-{fmt(lv.entry_high)} not reached within {params.expiry_bars} bars (expires {expires_at:%Y-%m-%d %H:%M} UTC)",
        f"price reaches TP1 {fmt(lv.tp1)} before entry - move missed, do not chase",
    ]
    if setup.setup_type == "trend_pullback":
        invalidation.append(f"momentum reversal: MACD histogram turns {'negative' if setup.d > 0 else 'positive'} before entry")
    if setup.setup_type == "breakout_retest" and setup.key_level:
        invalidation.append(f"close back {'below' if setup.d > 0 else 'above'} the broken level {fmt(setup.key_level)} (failed breakout)")
    if mtf.get("supporting"):
        invalidation.append("higher-timeframe bias flips against the trade (" + ", ".join(t_.split()[0] for t_ in mtf["supporting"]) + ")")

    supporting_tfs = ", ".join(mtf.get("supporting", [])) or "no higher timeframe"
    top = [c for c in sorted(comps, key=lambda c: -c.weight * c.score) if c.available and c.score >= 0.67]
    explanation = (
        f"{side} setup detected on {symbol or 'this market'} {tf} ({SETUP_LABEL[setup.setup_type]}, regime {_regime_text(regime)}). "
        f"Higher-timeframe context: {supporting_tfs}"
        + (f" (weighted agreement {mtf['agreement']:+.2f})" if mtf.get("agreement") is not None else "")
        + ". "
        + " ".join(s[:1].upper() + s[1:].rstrip(".") + "." for s in (setup.context + setup.triggers)[:4])
        + f" The stop at {fmt(lv.stop)} is based on {lv.methods['stop']} ({lv.risk_atr:.1f} ATR of risk); "
        f"TP1 {fmt(lv.tp1)} uses the {lv.methods['tp1']} for R:R 1:{lv.rr1:.2f}, which meets the configured 1:{params.min_rr:.2f} minimum. "
        f"Strongest evidence: {', '.join(COMPONENT_LABEL[c.name].lower() for c in top[:3]) or 'none above 67%'}. "
        f"Score {score:.0f}/100 vs threshold {params.min_score:.0f}. This is a probabilistic setup, not a guaranteed outcome."
    )
    return Evaluation(
        status="ACTIONABLE", direction=side, timeframe=tf, regime=regime, bar_time=bar_time,
        headline=f"{side} — {SETUP_LABEL[setup.setup_type]} ({score:.0f}/100)",
        explanation=explanation, reasons=reasons, risks=risks, invalidation=invalidation, mtf=mtf,
        data_quality=dq, expires_at=expires_at, ml_probability=prob, **common,
    )
