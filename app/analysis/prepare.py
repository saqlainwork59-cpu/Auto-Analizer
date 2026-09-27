"""Turn raw OHLCV into an analysis-ready frame: indicators, swings, rolling market structure,
market regime and directional bias - all causal, computed once per series so that the live
engine and the backtester evaluate *exactly* the same numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.analysis import indicators as ind
from app.analysis.params import StrategyParams
from app.analysis.structure import Swing, find_swings
from app.core.timeframes import seconds

REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "BREAKOUT", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNCERTAIN"]


@dataclass
class PreparedFrame:
    timeframe: str
    df: pd.DataFrame                      # indexed by open time (UTC)
    swings: list[Swing]
    close_times: np.ndarray               # int64 epoch seconds of each bar's close
    has_volume: bool
    arrays: dict[str, np.ndarray] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.df)

    def a(self, col: str) -> np.ndarray:
        arr = self.arrays.get(col)
        if arr is None:
            arr = self.df[col].to_numpy()
            self.arrays[col] = arr
        return arr

    def index_at_or_before(self, epoch_close: int) -> int:
        """Last bar whose *close* time is <= epoch_close (i.e. fully closed by then). -1 if none."""
        return int(np.searchsorted(self.close_times, epoch_close, side="right")) - 1


def _rolling_structure(swings: list[Swing], n: int, close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per bar: structure label (+1 bullish, -1 bearish, 0 range, nan undefined), last swing high, last swing low."""
    label = np.full(n, np.nan)
    last_hi = np.full(n, np.nan)
    last_lo = np.full(n, np.nan)
    by_conf: dict[int, list[Swing]] = {}
    for s in swings:
        by_conf.setdefault(s.confirmed_at, []).append(s)
    highs: list[float] = []
    lows: list[float] = []
    for t in range(n):
        for s in by_conf.get(t, []):
            (highs if s.kind == "high" else lows).append(s.price)
        if highs:
            last_hi[t] = highs[-1]
        if lows:
            last_lo[t] = lows[-1]
        if len(highs) >= 2 and len(lows) >= 2:
            hh, hl = highs[-1] > highs[-2], lows[-1] > lows[-2]
            lh, ll = highs[-1] < highs[-2], lows[-1] < lows[-2]
            label[t] = 1.0 if (hh and hl) else -1.0 if (lh and ll) else 0.0
    return label, last_hi, last_lo


def _regime(df: pd.DataFrame, p: StrategyParams, has_volume: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rp = p.regime
    c = df["close"].to_numpy()
    adx = df["adx14"].to_numpy()
    ema20, ema50, ema200 = df["ema20"].to_numpy(), df["ema50"].to_numpy(), df["ema200"].to_numpy()
    slope = df["ema50_slope"].to_numpy()
    atr_rank, bbw_rank = df["atr_rank"].to_numpy(), df["bbw_rank"].to_numpy()
    bbw = df["bb_width"].to_numpy()
    bbw_prev = df["bb_width"].shift(5).to_numpy()
    don_h, don_l = df["don_high20"].to_numpy(), df["don_low20"].to_numpy()
    vol_ok = (df["vol_z"].to_numpy() >= rp.breakout_vol_z) if has_volume else np.ones(len(df), bool)
    expanding = bbw > bbw_prev

    ev_up = (c > don_h) & vol_ok & expanding
    ev_dn = (c < don_l) & vol_ok & expanding
    # Remember the broken level for a few bars (retest window); the breakout remains valid while price holds beyond it.
    lvl_up = pd.Series(np.where(ev_up, don_h, np.nan)).ffill(limit=rp.breakout_memory_bars - 1).to_numpy()
    lvl_dn = pd.Series(np.where(ev_dn, don_l, np.nan)).ffill(limit=rp.breakout_memory_bars - 1).to_numpy()
    brk_up = np.isfinite(lvl_up) & (c > lvl_up)
    brk_dn = np.isfinite(lvl_dn) & (c < lvl_dn)

    warm = np.isfinite(ema200) & np.isfinite(adx) & np.isfinite(atr_rank) & np.isfinite(bbw_rank)
    trend_up = (adx >= rp.adx_trend) & (ema20 > ema50) & (c > ema50) & (slope > 0)
    trend_dn = (adx >= rp.adx_trend) & (ema20 < ema50) & (c < ema50) & (slope < 0)

    regime = np.full(len(df), "UNCERTAIN", dtype=object)
    # Order of precedence (first match wins) is applied by writing lowest priority first.
    regime[adx < rp.adx_range] = "RANGING"
    regime[bbw_rank <= rp.low_vol_rank] = "LOW_VOLATILITY"
    regime[trend_up] = "TRENDING_UP"
    regime[trend_dn] = "TRENDING_DOWN"
    regime[brk_up | brk_dn] = "BREAKOUT"
    regime[atr_rank >= rp.high_vol_rank] = "HIGH_VOLATILITY"
    regime[~warm] = "UNCERTAIN"
    brk_dir = np.where(brk_up & ~brk_dn, 1, np.where(brk_dn & ~brk_up, -1, 0))
    brk_level = np.where(brk_dir == 1, lvl_up, np.where(brk_dir == -1, lvl_dn, np.nan))
    return regime, brk_dir, brk_level


def _bias(df: pd.DataFrame, struct: np.ndarray) -> np.ndarray:
    """Directional bias score in [-1, 1] built from independent trend/momentum/structure evidence."""
    c = df["close"].to_numpy()
    s1 = np.sign(c - df["ema50"].to_numpy())
    s2 = np.sign(df["ema20"].to_numpy() - df["ema50"].to_numpy())
    slope = df["ema50_slope"].to_numpy()
    s3 = np.where(np.abs(slope) > 0.05, np.sign(slope), 0.0)
    s4 = np.nan_to_num(struct, nan=0.0)
    s5 = 0.5 * np.sign(df["macd_hist"].to_numpy()) + 0.5 * np.sign(df["macd"].to_numpy())
    score = 0.25 * s1 + 0.2 * s2 + 0.2 * s3 + 0.2 * s4 + 0.15 * s5
    warm = np.isfinite(df["ema50"].to_numpy()) & np.isfinite(slope) & np.isfinite(df["macd_hist"].to_numpy())
    return np.where(warm, score, np.nan)


def prepare(df: pd.DataFrame, timeframe: str, params: StrategyParams) -> PreparedFrame:
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    frame = ind.compute_all(df, intraday=timeframe != "1d")
    has_volume = bool(frame.attrs.get("has_volume"))
    swings = find_swings(frame["high"].to_numpy(), frame["low"].to_numpy(), params.swing_left, params.swing_right)
    struct, last_hi, last_lo = _rolling_structure(swings, len(frame), frame["close"].to_numpy())
    frame["struct"] = struct
    frame["last_swing_high"] = last_hi
    frame["last_swing_low"] = last_lo
    regime, brk_dir, brk_level = _regime(frame, params, has_volume)
    frame["regime"] = regime
    frame["breakout_dir"] = brk_dir
    frame["breakout_level"] = brk_level
    frame["bias"] = _bias(frame, struct)
    open_epoch = (frame.index.as_unit("ns").asi8 // 10**9) if len(frame) else np.array([], dtype=np.int64)
    close_times = (open_epoch + seconds(timeframe)).astype(np.int64)
    return PreparedFrame(timeframe, frame, swings, close_times, has_volume)


def bias_label(score: float) -> str:
    if not np.isfinite(score):
        return "unavailable"
    return "bullish" if score >= 0.35 else "bearish" if score <= -0.35 else "neutral"
