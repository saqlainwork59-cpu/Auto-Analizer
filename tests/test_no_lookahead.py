"""The most important test in the suite: the evaluation at bar t must be identical whether or not
the future exists. If any indicator, swing, regime or higher-timeframe lookup peeked ahead, the
truncated and full-history evaluations would differ."""
import numpy as np
import pytest

from app.analysis.engine import evaluate
from app.analysis.params import StrategyParams
from app.analysis.prepare import prepare
from tests.synthetic import make_ohlcv, resample


@pytest.fixture(scope="module")
def data():
    m5 = make_ohlcv(n=12 * 24 * 60, tf_seconds=300, seed=5)
    return {"15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}


def _sig(e):
    lv = e.levels
    return (e.status, e.direction, e.regime, e.setup_type, round(e.score, 6),
            None if lv is None else tuple(round(x, 8) for x in (lv.entry_low, lv.entry_high, lv.stop, lv.tp1, lv.tp2)),
            tuple(sorted((k, v) for k, v in e.features.items() if v is not None)))


def test_truncated_equals_full(data):
    p = StrategyParams()
    full = {tf: prepare(df, tf, p) for tf, df in data.items()}
    n = len(full["15m"])
    rng = np.random.default_rng(0)
    checkpoints = sorted(set(rng.integers(300, n - 50, 25).tolist()))
    # include bars where the full-history engine is actionable (the interesting cases)
    act = [t for t in range(300, n - 50) if evaluate(full, "15m", t, p).actionable][:15]
    for t in sorted(set(checkpoints + act)):
        cutoff = data["15m"].index[t] + (data["15m"].index[1] - data["15m"].index[0])  # close time of bar t
        trunc = {}
        for tf, df in data.items():
            step = df.index[1] - df.index[0]
            closed = df[df.index + step <= cutoff]  # only candles fully closed at bar t's close
            trunc[tf] = prepare(closed, tf, p)
        e_full = evaluate(full, "15m", t, p)
        e_trunc = evaluate(trunc, "15m", len(trunc["15m"]) - 1, p)
        assert _sig(e_full) == _sig(e_trunc), f"look-ahead detected at bar {t}"
    assert act, "fixture should contain actionable bars"


def test_random_walk_has_no_edge():
    """On a driftless random walk no strategy can have a real edge. A clearly positive expectancy
    here would indicate look-ahead bias or an optimistic fill model."""
    from datetime import datetime, timezone

    from app.backtest.engine import BacktestConfig, run_backtest

    exps = []
    trades = 0
    for seed in (21, 22, 23):
        m5 = make_ohlcv(n=12 * 24 * 150, tf_seconds=300, seed=seed, drift_regimes=False)
        dfs = {"15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}
        r = run_backtest(dfs, StrategyParams(), BacktestConfig("15m", datetime(2024, 1, 1, tzinfo=timezone.utc),
                                                               datetime(2024, 12, 31, tzinfo=timezone.utc),
                                                               fee_pct=0, slippage_pct=0))
        if r.metrics["total_trades"]:
            exps.append(r.metrics["expectancy_r"] * r.metrics["total_trades"])
            trades += r.metrics["total_trades"]
    assert trades > 50
    assert sum(exps) / trades < 0.15
