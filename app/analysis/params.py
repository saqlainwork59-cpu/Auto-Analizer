"""Strategy parameters. Every number that influences a signal lives here, is versioned in the
`strategies` table, and is copied into each signal / backtest for auditability.

The defaults are *starting points*, not claims of optimality. They are meant to be tuned through
walk-forward research (app/ml/weights.py) and only promoted after out-of-sample validation.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

ENGINE_VERSION = "1.0.0"

COMPONENTS = [
    "trend_alignment",
    "momentum",
    "market_structure",
    "volume",
    "support_resistance",
    "multi_timeframe",
    "risk_reward",
]


class RegimeParams(BaseModel):
    adx_trend: float = Field(23.0, ge=5, le=60)
    adx_range: float = Field(20.0, ge=5, le=60)
    high_vol_rank: float = Field(0.93, ge=0.5, le=1.0)
    low_vol_rank: float = Field(0.10, ge=0.0, le=0.5)
    breakout_vol_z: float = Field(1.0, ge=0.0, le=5.0)
    breakout_memory_bars: int = Field(3, ge=1, le=10)


class MLFilterParams(BaseModel):
    enabled: bool = False
    min_probability: float = Field(0.5, ge=0.0, le=1.0)


class StrategyParams(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "trend_alignment": 20,
            "momentum": 15,
            "market_structure": 20,
            "volume": 10,
            "support_resistance": 15,
            "multi_timeframe": 15,
            "risk_reward": 5,
        }
    )
    min_score: float = Field(70.0, ge=0, le=100)
    min_rr: float = Field(1.5, ge=0.5, le=10)
    min_stop_atr: float = Field(0.5, ge=0.1, le=5)
    max_stop_atr: float = Field(3.0, ge=0.5, le=10)
    stop_buffer_atr: float = Field(0.25, ge=0, le=2)
    expiry_bars: int = Field(4, ge=1, le=50)
    max_hold_bars: int = Field(48, ge=2, le=1000)
    tp1_fraction: float = Field(0.5, ge=0, le=1)
    move_stop_to_breakeven: bool = True
    mtf_conflict_threshold: float = Field(0.4, ge=0.1, le=1.0)
    allow_high_volatility: bool = False
    allow_setups: list[str] = Field(default_factory=lambda: ["trend_pullback", "breakout_retest", "range_reversion"])
    swing_left: int = Field(3, ge=1, le=10)
    swing_right: int = Field(3, ge=1, le=10)
    zone_lookback: int = Field(150, ge=30, le=1000)
    min_bars: int = Field(250, ge=100, le=5000)
    regime: RegimeParams = Field(default_factory=RegimeParams)
    ml_filter: MLFilterParams = Field(default_factory=MLFilterParams)

    @model_validator(mode="after")
    def _check(self) -> "StrategyParams":
        unknown = set(self.weights) - set(COMPONENTS)
        if unknown:
            raise ValueError(f"unknown score components: {sorted(unknown)}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-negative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one weight must be positive")
        for c in COMPONENTS:
            self.weights.setdefault(c, 0.0)
        if self.min_stop_atr >= self.max_stop_atr:
            raise ValueError("min_stop_atr must be below max_stop_atr")
        if self.regime.adx_range > self.regime.adx_trend:
            raise ValueError("regime.adx_range must be <= regime.adx_trend")
        bad = set(self.allow_setups) - {"trend_pullback", "breakout_retest", "range_reversion"}
        if bad:
            raise ValueError(f"unknown setups: {sorted(bad)}")
        return self


DEFAULT_STRATEGY_NAME = "multi-factor"
