"""Multi-timeframe confirmation.

For a trade on timeframe T, every *higher* timeframe is read at its last candle that had fully
closed at T's signal-bar close (no peeking at a forming higher-timeframe candle). Higher
timeframes carry more weight. Strong opposition from higher timeframes produces
"WAIT - conflicting market structure" instead of a forced trade.
"""
from __future__ import annotations

import numpy as np

from app.analysis.prepare import PreparedFrame, bias_label
from app.core.timeframes import ORDER

TF_WEIGHT = {"5m": 0.5, "15m": 0.75, "1h": 1.0, "4h": 1.25, "1d": 1.5}


def snapshot(frames: dict[str, PreparedFrame], epoch_close: int) -> dict[str, dict]:
    """Bias/regime of every available timeframe as of `epoch_close`."""
    out: dict[str, dict] = {}
    for tf in ORDER:
        pf = frames.get(tf)
        if pf is None or len(pf) == 0:
            out[tf] = {"available": False, "bias": "unavailable", "score": None, "regime": None}
            continue
        i = pf.index_at_or_before(epoch_close)
        if i < 0 or not np.isfinite(pf.a("bias")[i]):
            out[tf] = {"available": False, "bias": "unavailable", "score": None, "regime": None}
            continue
        sc = float(pf.a("bias")[i])
        rsi = float(pf.a("rsi14")[i])
        e20 = float(pf.a("ema20")[i])
        c = float(pf.a("close")[i])
        state = bias_label(sc)
        # A lower-timeframe dip inside a higher-timeframe uptrend is a pullback, not a reversal.
        if state == "bullish" and (c < e20 or rsi < 50):
            state_detail = "bullish (pulling back)"
        elif state == "bearish" and (c > e20 or rsi > 50):
            state_detail = "bearish (pulling back)"
        else:
            state_detail = state
        out[tf] = {
            "available": True,
            "bias": state,
            "detail": state_detail,
            "score": round(sc, 3),
            "regime": str(pf.a("regime")[i]),
            "bar_open": pf.df.index[i].isoformat(),
        }
    return out


def alignment(snap: dict[str, dict], trade_tf: str, d: int, conflict_threshold: float) -> dict:
    higher = [tf for tf in ORDER[ORDER.index(trade_tf) + 1 :] if snap.get(tf, {}).get("available")]
    missing = [tf for tf in ORDER[ORDER.index(trade_tf) + 1 :] if not snap.get(tf, {}).get("available")]
    if not higher:
        return {"agreement": None, "opposition": None, "conflict": False, "supporting": [], "opposing": [],
                "missing": missing, "timeframes": snap}
    wsum = sum(TF_WEIGHT[tf] for tf in higher)
    wall = wsum + sum(TF_WEIGHT[tf] for tf in missing)
    # Missing higher timeframes reduce confidence: agreement is scaled by weighted coverage.
    coverage = wsum / wall
    agree = coverage * sum(TF_WEIGHT[tf] * float(np.clip(snap[tf]["score"] * d, -1, 1)) for tf in higher) / wsum
    opp_share = sum(TF_WEIGHT[tf] for tf in higher if snap[tf]["score"] * d <= -0.35) / wsum
    sup_share = sum(TF_WEIGHT[tf] for tf in higher if snap[tf]["score"] * d >= 0.35) / wsum
    supporting = [f"{tf} {snap[tf]['detail']}" for tf in higher if snap[tf]["score"] * d >= 0.35]
    opposing = [f"{tf} {snap[tf]['detail']}" for tf in higher if snap[tf]["score"] * d <= -0.35]
    neutral = [f"{tf} neutral" for tf in higher if abs(snap[tf]["score"]) < 0.35]
    return {
        "agreement": round(agree, 3),
        "coverage": round(coverage, 3),
        "support": round(sup_share, 3),
        "opposition": round(opp_share, 3),
        "conflict": opp_share >= conflict_threshold,
        "supporting": supporting,
        "opposing": opposing,
        "neutral": neutral,
        "missing": missing,
        "timeframes": snap,
    }
