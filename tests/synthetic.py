"""TEST FIXTURE ONLY: synthetic OHLCV generator used to exercise calculations in unit tests.
Never used by the application to display prices."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(n: int = 1500, tf_seconds: int = 900, seed: int = 7, start: str = "2024-01-01", drift_regimes: bool = True,
               volume: bool = True, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    if drift_regimes:
        k = 0
        while k < n:
            length = int(rng.integers(80, 300))
            drift[k : k + length] = rng.choice([-0.0012, 0.0, 0.0, 0.0012])
            k += length
    rets = drift + rng.normal(0, 0.004, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[base], close[:-1]]) * (1 + rng.normal(0, 0.0005, n))
    wick = np.abs(rng.normal(0, 0.003, (n, 2)))
    high = np.maximum(open_, close) * (1 + wick[:, 0])
    low = np.minimum(open_, close) * (1 - wick[:, 1])
    vol = rng.lognormal(10, 0.4, n) * (1 + 3 * np.abs(rets) / 0.004) if volume else np.full(n, np.nan)
    idx = pd.date_range(start=start, periods=n, freq=pd.Timedelta(seconds=tf_seconds), tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])
