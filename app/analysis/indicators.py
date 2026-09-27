"""Technical indicators, implemented directly in NumPy/pandas so every formula is auditable.

All indicators are *causal*: the value at bar t depends only on bars <= t. This is what makes
the backtester free of look-ahead bias (verified in tests/test_no_lookahead.py).

Conventions follow the widely used definitions (Wilder smoothing seeded with a simple average,
as in Wilder 1978 / TradingView `ta.rma`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    """EMA seeded with the SMA of the first n values (standard definition)."""
    values = x.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    alpha = 2.0 / (n + 1)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < n:
        return pd.Series(out, index=x.index)
    start = valid[0]
    seed_end = start + n
    out[seed_end - 1] = np.mean(values[start:seed_end])
    for i in range(seed_end, len(values)):
        v = values[i]
        out[i] = out[i - 1] if np.isnan(v) else alpha * v + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=x.index)


def rma(x: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n), seeded with the SMA of the first n values."""
    values = x.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < n:
        return pd.Series(out, index=x.index)
    start = valid[0]
    seed_end = start + n
    out[seed_end - 1] = np.mean(values[start:seed_end])
    for i in range(seed_end, len(values)):
        v = values[i]
        out[i] = out[i - 1] if np.isnan(v) else (out[i - 1] * (n - 1) + v) / n
    return pd.Series(out, index=x.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = (-diff).clip(lower=0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out.where(avg_gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)  # population std, per Bollinger
    return mid, mid + k * sd, mid - k * sd


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(high, low, close)
    tr.iloc[0] = np.nan
    atr_ = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(plus_dm, n) / atr_
        mdi = 100 * rma(minus_dm, n) / atr_
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    dx = dx.replace([np.inf, -np.inf], np.nan)
    return rma(dx, n), pdi, mdi


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to each UTC day. Returns NaN where volume is unavailable."""
    vol = df["volume"].fillna(0.0)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.floor("D")
    pv = (typical * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = pv / cv
    return out.where(cv > 0)


def rolling_rank(x: pd.Series, n: int) -> pd.Series:
    """Percentile of the current value within the trailing window of n values (0..1). Causal.

    Vectorised with a sliding window view; ties count half. Requires at least n//2 valid history values.
    """
    arr = x.to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    if len(arr) < n:
        return pd.Series(out, index=x.index)
    win = np.lib.stride_tricks.sliding_window_view(arr, n)
    cur = win[:, -1:]
    hist = win[:, :-1]
    valid = ~np.isnan(hist)
    cnt = valid.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        less = np.where(valid, hist < cur, False).sum(axis=1)
        eq = np.where(valid, hist == cur, False).sum(axis=1)
        rank = (less + 0.5 * eq) / cnt
    ok = (cnt >= n // 2) & ~np.isnan(cur[:, 0])
    out[n - 1 :] = np.where(ok, rank, np.nan)
    return pd.Series(out, index=x.index)


def compute_all(df: pd.DataFrame, *, intraday: bool = True) -> pd.DataFrame:
    """Return a copy of OHLCV `df` with all indicator columns attached.

    `df` must be indexed by candle open time (UTC) and contain open/high/low/close/volume.
    """
    out = df.copy()
    c, h, low = out["close"], out["high"], out["low"]
    has_volume = bool(out["volume"].fillna(0).gt(0).mean() > 0.8) if "volume" in out else False
    if "volume" not in out:
        out["volume"] = np.nan

    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["ema200"] = ema(c, 200)
    out["rsi14"] = rsi(c, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["atr14"] = atr(h, low, c, 14)
    out["bb_mid"], out["bb_up"], out["bb_low"] = bollinger(c)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["bb_width"] = (out["bb_up"] - out["bb_low"]) / out["bb_mid"]
        out["atr_pct"] = out["atr14"] / c
    out["adx14"], out["plus_di"], out["minus_di"] = adx(h, low, c, 14)
    out["vwap"] = session_vwap(out) if (intraday and has_volume) else np.nan
    if has_volume:
        vmean = out["volume"].rolling(20, min_periods=20).mean()
        vstd = out["volume"].rolling(20, min_periods=20).std(ddof=0)
        out["vol_z"] = ((out["volume"] - vmean) / vstd.replace(0, np.nan)).clip(-5, 10)
        out["vol_ratio"] = out["volume"] / vmean
    else:
        out["vol_z"] = np.nan
        out["vol_ratio"] = np.nan
    # Donchian channel of the *previous* 20 bars (excludes the current bar -> breakout test is causal).
    out["don_high20"] = h.shift(1).rolling(20, min_periods=20).max()
    out["don_low20"] = low.shift(1).rolling(20, min_periods=20).min()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["ema50_slope"] = (out["ema50"] - out["ema50"].shift(5)) / out["atr14"]
    out["atr_rank"] = rolling_rank(out["atr_pct"], 100)
    out["bbw_rank"] = rolling_rank(out["bb_width"], 100)
    out.attrs["has_volume"] = has_volume
    return out
