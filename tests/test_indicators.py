import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ind

# Wilder RSI worked example (StockCharts "RSI" ChartSchool table). Published values were computed with
# rounded intermediate averages, hence a 0.1 tolerance; the exact check is against the naive reference below.
CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
          46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
          43.42, 42.66, 43.13]
PUBLISHED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46,
             41.87, 45.46, 37.30, 33.08, 37.77]


def naive_wilder_rsi(closes, n=14):
    diffs = np.diff(closes)
    gains, losses = np.maximum(diffs, 0), np.maximum(-diffs, 0)
    ag, al = gains[:n].mean(), losses[:n].mean()
    out = [100 - 100 / (1 + ag / al)]
    for g, lo in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + lo) / n
        out.append(100 - 100 / (1 + ag / al))
    return np.array(out)


def test_rsi_matches_wilder_reference():
    r = ind.rsi(pd.Series(CLOSES), 14).dropna().to_numpy()
    np.testing.assert_allclose(r, naive_wilder_rsi(np.array(CLOSES)), atol=1e-9)
    np.testing.assert_allclose(r, PUBLISHED, atol=0.1)


def test_rsi_edge_cases():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ind.rsi(up).dropna().eq(100).all()
    flat = pd.Series(np.full(40, 5.0))
    assert ind.rsi(flat).dropna().eq(50).all()
    assert ind.rsi(up).iloc[:14].isna().all()  # warm-up is NaN, never a fabricated value


def test_ema_seed_and_recursion():
    x = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    e = ind.ema(x, 3)
    assert np.isnan(e.iloc[1])
    assert e.iloc[2] == pytest.approx(2.0)  # SMA seed
    assert e.iloc[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
    const = ind.ema(pd.Series(np.full(50, 7.0)), 20).dropna()
    assert np.allclose(const, 7.0)


def test_atr_constant_range():
    n = 50
    close = pd.Series(np.full(n, 100.0))
    high, low = close + 1, close - 1
    a = ind.atr(high, low, close, 14).dropna()
    assert np.allclose(a, 2.0)


def test_macd_zero_for_constant_and_sign_for_trend():
    line, sig, hist = ind.macd(pd.Series(np.full(100, 10.0)))
    assert np.allclose(line.dropna(), 0) and np.allclose(hist.dropna(), 0)
    line, _, _ = ind.macd(pd.Series(np.linspace(10, 20, 100)))
    assert (line.dropna() > 0).all()


def test_bollinger_width_zero_on_constant_and_symmetric():
    mid, up, lo = ind.bollinger(pd.Series(np.full(40, 3.0)))
    assert np.allclose((up - lo).dropna(), 0)
    x = pd.Series(np.sin(np.arange(60)) + 10)
    mid, up, lo = ind.bollinger(x)
    assert np.allclose((up - mid).dropna(), (mid - lo).dropna())


def test_vwap_session_anchor():
    idx = pd.date_range("2024-01-01 22:00", periods=4, freq="1h", tz="UTC")  # crosses midnight
    df = pd.DataFrame({"high": [11, 12, 13, 14], "low": [9, 10, 11, 12], "close": [10, 11, 12, 13],
                       "volume": [1, 3, 2, 2]}, index=idx, dtype=float)
    v = ind.session_vwap(df)
    assert v.iloc[1] == pytest.approx((10 * 1 + 11 * 3) / 4)
    assert v.iloc[2] == pytest.approx(12.0)  # new UTC day resets the anchor
    assert v.iloc[3] == pytest.approx((12 * 2 + 13 * 2) / 4)


def test_adx_trending_vs_flat():
    n = 200
    close = pd.Series(np.linspace(100, 200, n))
    a, pdi, mdi = ind.adx(close + 0.5, close - 0.5, close)
    assert a.iloc[-1] > 40 and pdi.iloc[-1] > mdi.iloc[-1]


def test_rolling_rank_is_causal():
    x = pd.Series(np.arange(300, dtype=float))
    r = ind.rolling_rank(x, 100)
    assert r.iloc[150] == pytest.approx(1.0)
    y = x.copy()
    y.iloc[200:] = -1  # changing the future must not change the past
    assert ind.rolling_rank(y, 100).iloc[:200].equals(r.iloc[:200])


def test_compute_all_marks_missing_volume():
    idx = pd.date_range("2024-01-01", periods=300, freq="15min", tz="UTC")
    c = pd.Series(np.linspace(1, 2, 300), index=idx)
    df = pd.DataFrame({"open": c, "high": c + 0.01, "low": c - 0.01, "close": c, "volume": np.nan})
    out = ind.compute_all(df)
    assert out.attrs["has_volume"] is False
    assert out["vol_z"].isna().all() and out["vwap"].isna().all()
