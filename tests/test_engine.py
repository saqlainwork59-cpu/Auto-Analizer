"""Signal-engine behaviour on synthetic series (test fixtures only, never shown to users)."""
import re

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.analysis import mtf as mtf_mod
from app.analysis.engine import evaluate
from app.analysis.params import StrategyParams
from app.analysis.prepare import prepare
from app.analysis.quality import check
from tests.synthetic import make_ohlcv, resample

BANNED = re.compile(r"guarantee(d)? profit|100% accurate|risk-free|never lose", re.I)


@pytest.fixture(scope="module")
def market():
    m5 = make_ohlcv(n=12 * 24 * 90, tf_seconds=300, seed=11)
    dfs = {"15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}
    p = StrategyParams()
    frames = {tf: prepare(df, tf, p) for tf, df in dfs.items()}
    evals = [(t, evaluate(frames, "15m", t, p, symbol="SYN/USD")) for t in range(300, len(frames["15m"]))]
    return dfs, frames, p, evals


def test_mostly_wait_and_some_actionable(market):
    _, _, _, evals = market
    actionable = [e for _, e in evals if e.actionable]
    waits = [e for _, e in evals if e.status == "WAIT"]
    assert len(waits) > 10 * len(actionable) > 0  # the engine is selective by design


def test_every_actionable_signal_is_internally_consistent(market):
    _, frames, p, evals = market
    atr = frames["15m"].a("atr14")
    for t, e in evals:
        if not e.actionable:
            continue
        lv = e.levels
        d = 1 if e.direction == "BUY" else -1
        assert lv.entry_low <= lv.entry_ref <= lv.entry_high
        assert d * (lv.entry_ref - lv.stop) > 0 and d * (lv.tp1 - lv.entry_ref) > 0 and d * (lv.tp2 - lv.tp1) > 0
        assert lv.rr1 >= p.min_rr - 1e-9
        assert p.min_stop_atr - 1e-6 <= lv.risk / atr[t] <= p.max_stop_atr + 1e-6
        assert e.score >= p.min_score
        assert e.reasons and e.invalidation and e.expires_at > e.bar_time
        assert sum(c.weight for c in e.components) == pytest.approx(sum(p.weights.values()))
        assert not BANNED.search(e.explanation + " ".join(e.reasons))


def test_wait_message_and_no_levels(market):
    _, _, _, evals = market
    w = next(e for _, e in evals if e.status == "WAIT" and e.setup_type is None)
    assert w.headline.startswith("WAIT") and w.direction == "WAIT"
    assert "No high-quality setup detected" in w.headline


def test_conflicting_higher_timeframes_force_wait(market):
    dfs, frames, p, evals = market
    t, e = next((t, e) for t, e in evals if e.actionable)
    d = 1 if e.direction == "BUY" else -1
    forced = dict(frames)
    for tf in ("1h", "4h", "1d"):
        pf = prepare(dfs[tf], tf, p)
        pf.arrays["bias"] = np.full(len(pf), -0.9 * d)  # strongly opposite on every higher timeframe
        forced[tf] = pf
    e2 = evaluate(forced, "15m", t, p)
    assert e2.status == "WAIT" and "conflicting market structure" in e2.headline


def test_threshold_controls_decision(market):
    _, frames, _, evals = market
    t, e = next((t, e) for t, e in evals if e.actionable)
    strict = StrategyParams(min_score=min(100.0, e.score + 0.5))
    e2 = evaluate(frames, "15m", t, strict)
    assert e2.status == "WAIT" and "evidence too weak" in e2.explanation


def test_weights_are_configurable_and_validated(market):
    _, frames, p, evals = market
    t, e = next((t, e) for t, e in evals if e.actionable)
    w = {k: 0.0 for k in p.weights}
    w["risk_reward"] = 1.0
    e2 = evaluate(frames, "15m", t, StrategyParams(weights=w, min_score=0))
    rr_comp = next(c for c in e.components if c.name == "risk_reward")
    assert e2.score == pytest.approx(100 * rr_comp.score, abs=0.1)
    with pytest.raises(ValidationError):
        StrategyParams(weights={"trend_alignment": -1})
    with pytest.raises(ValidationError):
        StrategyParams(weights={"made_up": 10})


def test_regime_gates_playbooks(market):
    _, _, _, evals = market
    for _, e in evals:
        if e.actionable:
            assert e.regime in ("TRENDING_UP", "TRENDING_DOWN", "BREAKOUT", "RANGING")
            if e.setup_type == "trend_pullback":
                assert (e.regime == "TRENDING_UP") == (e.direction == "BUY")
            if e.setup_type == "range_reversion":
                assert e.regime == "RANGING"
        if e.regime in ("LOW_VOLATILITY", "HIGH_VOLATILITY", "UNCERTAIN"):
            assert not e.actionable


def test_data_quality_gate():
    df = make_ohlcv(n=400, tf_seconds=900, seed=1)
    q = check(df.iloc[:100], "15m", min_bars=250)
    assert not q.ok and "only 100" in q.issues[0]
    gappy = df.drop(df.index[200:260])
    assert not check(gappy, "15m", min_bars=250).ok
    stale = check(df, "15m", min_bars=250, now=df.index[-1].to_pydatetime() + pd.Timedelta(hours=5))
    assert stale.stale and not stale.ok
    bad = df.copy()
    bad.iloc[-5, bad.columns.get_loc("high")] = bad.iloc[-5]["low"] - 1
    assert not check(bad, "15m", min_bars=250).ok
    p = StrategyParams()
    frames = {"15m": prepare(df, "15m", p)}
    e = evaluate(frames, "15m", len(df) - 1, p, quality=check(df.iloc[:100], "15m", min_bars=250))
    assert e.status == "DATA_UNAVAILABLE" and e.direction == "WAIT"


def test_mtf_alignment_math():
    snap = {"1h": {"available": True, "score": 0.8, "detail": "bullish"},
            "4h": {"available": True, "score": 0.6, "detail": "bullish"},
            "1d": {"available": True, "score": -0.7, "detail": "bearish"}}
    a = mtf_mod.alignment(snap, "15m", 1, 0.4)
    assert a["opposition"] == pytest.approx(1.5 / 3.75)
    assert a["conflict"] is True
    snap["1d"] = {"available": False}
    b = mtf_mod.alignment(snap, "15m", 1, 0.4)
    assert b["conflict"] is False and b["coverage"] == pytest.approx(2.25 / 3.75)


def test_explanations_cite_computed_numbers(market):
    _, _, _, evals = market
    e = next(e for _, e in evals if e.actionable)
    assert re.search(r"R:R 1:\d+\.\d{2}", e.explanation)
    assert f"{e.score:.0f}/100" in e.explanation
    assert "not a guaranteed outcome" in e.explanation
