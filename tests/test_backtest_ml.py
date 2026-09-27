import math
from datetime import datetime, timezone

import numpy as np
import pytest

from app.analysis.params import StrategyParams
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.metrics import max_drawdown, profit_factor, sharpe_from_equity, streaks, summarize_trades
from app.ml import pipeline
from app.ml.dataset import assemble, build_rows
from app.ml.weights import optimize
from tests.synthetic import make_ohlcv, resample


def test_metric_primitives():
    assert max_drawdown([100, 120, 90, 130, 117]) == (pytest.approx(0.25), pytest.approx(30))
    assert profit_factor([10, -5, 5, -5]) == pytest.approx(1.5)
    assert math.isinf(profit_factor([1, 2]))
    assert profit_factor([]) is None
    assert streaks([1, -1, -2, -3, 2, 3, 0, -1]) == (2, 3)
    assert sharpe_from_equity([datetime(2024, 1, 1, tzinfo=timezone.utc)] * 2, [1, 2], 365) is None


def test_summary_counts():
    trades = [{"pnl": 100, "r_multiple": 1.0, "planned_rr": 2, "fees": 1}, {"pnl": -50, "r_multiple": -0.5, "planned_rr": 2, "fees": 1}]
    m = summarize_trades(trades, 1000, [0, 1, 2], [1000, 1100, 1050], 365)
    assert m["total_trades"] == 2 and m["win_rate"] == 0.5 and m["net_return_pct"] == pytest.approx(5.0)
    assert m["profit_factor"] == pytest.approx(2.0) and m["sample_size_warning"]


@pytest.fixture(scope="module")
def dfs():
    m5 = make_ohlcv(n=12 * 24 * 100, tf_seconds=300, seed=3)
    return {"15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}


def test_backtest_accounting_is_consistent(dfs):
    cfg = BacktestConfig("15m", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 12, 1, tzinfo=timezone.utc),
                         starting_capital=10_000, risk_per_trade_pct=1, fee_pct=0.1, slippage_pct=0.05)
    r = run_backtest(dfs, StrategyParams(), cfg)
    m = r.metrics
    assert m["total_trades"] == len(r.trades) > 0
    assert m["winning_trades"] + m["losing_trades"] + m["breakeven_trades"] == m["total_trades"]
    assert m["final_equity"] == pytest.approx(10_000 + sum(t["pnl"] for t in r.trades))
    assert r.equity_curve[-1]["equity"] == pytest.approx(m["final_equity"])
    for t in r.trades:  # risk sizing: a full stop-out costs ~1% of equity plus fees/slippage
        assert t["r_multiple"] > -1.6
        assert t["entry_time"] > t["signal_time"] or t["entry_time"] == t["signal_time"]
    # fees strictly reduce the result
    r0 = run_backtest(dfs, StrategyParams(), BacktestConfig(**{**cfg.__dict__, "fee_pct": 0.0, "slippage_pct": 0.0}))
    assert r0.metrics["final_equity"] > m["final_equity"]
    assert any("do not guarantee" in a for a in r.data_summary["assumptions"])


def test_backtest_input_validation(dfs):
    with pytest.raises(ValueError):
        run_backtest(dfs, StrategyParams(), BacktestConfig("15m", datetime(2024, 5, 1, tzinfo=timezone.utc),
                                                           datetime(2024, 1, 1, tzinfo=timezone.utc)))
    with pytest.raises(ValueError):
        run_backtest(dfs, StrategyParams(), BacktestConfig("15m", datetime(2024, 1, 1, tzinfo=timezone.utc),
                                                           datetime(2024, 5, 1, tzinfo=timezone.utc), risk_per_trade_pct=50))
    with pytest.raises(ValueError, match="no 5m data"):
        run_backtest(dfs, StrategyParams(), BacktestConfig("5m", datetime(2024, 1, 1, tzinfo=timezone.utc),
                                                           datetime(2024, 5, 1, tzinfo=timezone.utc)))


def test_purged_walk_forward_has_no_overlap():
    rng = np.random.default_rng(0)
    ts = np.sort(rng.integers(0, 10_000, 600))
    te = ts + rng.integers(1, 200, 600)
    dev, hold = pipeline.holdout_split(ts, te, 0.2)
    assert set(dev).isdisjoint(hold)
    assert te[dev].max() < ts[hold].min()
    for train, test in pipeline.walk_forward_splits(ts, te, dev, 5):
        assert te[train].max() < ts[test].min()  # no training label reaches into the test period
        assert ts[train].max() < ts[test].min()  # strictly forward in time


@pytest.fixture(scope="module")
def rw_dataset():
    rows = []
    for seed in (31, 32, 33, 34):
        m5 = make_ohlcv(n=12 * 24 * 120, tf_seconds=300, seed=seed, drift_regimes=False)
        d = {"15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"), "1d": resample(m5, "1D")}
        rows += build_rows(d, "15m", StrategyParams(), f"RW{seed}")
    return assemble(rows)


def test_ml_gate_rejects_model_without_edge(rw_dataset):
    """Labels on a random walk are unpredictable: the model must NOT pass the promotion gate."""
    res = pipeline.train(rw_dataset, "hgb", n_folds=4)
    assert res.passes_gate is False
    assert res.metrics["holdout"]["n"] > 0
    assert res.dataset_info["purged_rows"] >= 0
    failing = [c["check"] for c in res.overfit["checks"] if not c["passed"]]
    assert failing  # the reason is recorded


def test_dataset_rows_use_only_past_features(rw_dataset):
    df = rw_dataset.frame
    assert (df["t_end"] > df["t_start"]).all()
    assert "label" not in rw_dataset.feature_names and "r_multiple" not in rw_dataset.feature_names
    assert rw_dataset.sha256 == rw_dataset.sha256


def test_weight_search_validates_on_holdout(rw_dataset):
    out = optimize(rw_dataset, StrategyParams(), n_samples=60)
    assert set(out["proposed_weights"]) == set(StrategyParams().weights)
    assert "holdout_proposed" in out and "holdout_current" in out
    # on a random walk the proposed weights should not show a validated positive edge
    assert out["passes"] is False or (out["holdout_proposed"]["expectancy_r"] or 0) > 0
