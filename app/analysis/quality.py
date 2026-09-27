"""Data-quality gate. The engine refuses to analyse data that fails these checks and reports
DATA UNAVAILABLE instead of guessing."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.core.timeframes import seconds


@dataclass
class DataQuality:
    ok: bool
    bars: int
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    last_close: datetime | None = None
    stale: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "bars": self.bars,
            "issues": self.issues,
            "warnings": self.warnings,
            "last_close": self.last_close.isoformat() if self.last_close else None,
            "stale": self.stale,
        }


def check(
    df: pd.DataFrame,
    timeframe: str,
    *,
    min_bars: int,
    calendar: str = "24x7",
    now: datetime | None = None,
) -> DataQuality:
    issues: list[str] = []
    warnings: list[str] = []
    n = len(df)
    if n == 0:
        return DataQuality(False, 0, ["no candles stored for this market/timeframe"])
    if n < min_bars:
        issues.append(f"only {n} closed candles available; {min_bars} required for reliable indicators")
    ohlc = df[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(ohlc).all():
        issues.append("missing (NaN) prices present")
    if (ohlc <= 0).any():
        issues.append("non-positive prices present")
    bad_hl = (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad_hl.tail(min_bars).any():
        issues.append(f"{int(bad_hl.tail(min_bars).sum())} candles with inconsistent high/low")
    step = seconds(timeframe)
    tail = df.tail(min_bars)
    if calendar == "24x7" and len(tail) > 1:
        expected = int((tail.index[-1] - tail.index[0]).total_seconds() // step) + 1
        missing = expected - len(tail)
        if missing > 0:
            ratio = missing / expected
            (issues if ratio > 0.02 else warnings).append(f"{missing} missing candles in the analysis window ({ratio:.1%})")
    # Extreme single-bar moves are flagged (possible bad ticks) but not auto-removed.
    rng = (df["high"] - df["low"]).tail(min_bars)
    med = float(rng.median()) if len(rng) else 0.0
    if med > 0 and (rng > 25 * med).any():
        warnings.append("an abnormally large candle (>25x median range) is present - verify the data source")

    last_close = (df.index[-1] + pd.Timedelta(seconds=step)).to_pydatetime()
    stale = False
    if now is not None:
        age = (now - last_close).total_seconds()
        limit = 2 * step + 120
        if calendar == "24x7" and age > limit:
            stale = True
            issues.append(f"latest candle closed {age / 60:.0f} min ago - live feed appears stale")
        elif calendar != "24x7" and age > max(limit, 3 * 86400 + 3600):
            stale = True
            issues.append(f"latest candle closed {age / 3600:.1f} h ago - market closed or feed stale")
        elif calendar != "24x7" and age > limit:
            warnings.append("market appears closed; analysis uses the last completed session")
    return DataQuality(not issues, n, issues, warnings, last_close.replace(tzinfo=timezone.utc) if last_close.tzinfo is None else last_close, stale)
