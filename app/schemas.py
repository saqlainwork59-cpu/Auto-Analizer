"""Request validation models (strict: unknown fields rejected, ranges enforced)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.timeframes import TIMEFRAMES

TF = Literal["5m", "15m", "1h", "4h", "1d"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterIn(Strict):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(None, max_length=80)


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(Strict):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class WatchlistAddIn(Strict):
    asset_id: int = Field(gt=0)


class WatchlistOrderIn(Strict):
    asset_ids: list[int] = Field(max_length=200)


class BacktestIn(Strict):
    asset_id: int = Field(gt=0)
    timeframe: TF
    start: datetime
    end: datetime
    strategy_id: int | None = None
    setups: list[Literal["trend_pullback", "breakout_retest", "range_reversion"]] | None = None
    min_score: float | None = Field(None, ge=0, le=100)
    min_rr: float | None = Field(None, ge=0.5, le=10)
    starting_capital: float = Field(10_000, gt=0, le=1e9)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    fee_pct: float = Field(0.1, ge=0, le=2)
    slippage_pct: float = Field(0.05, ge=0, le=2)
    max_leverage: float = Field(1.0, gt=0, le=10)

    @field_validator("end")
    @classmethod
    def _range(cls, v: datetime, info):
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        if start and (v - start).days > 3 * 366:
            raise ValueError("maximum range is 3 years per run")
        return v


class PaperOpenIn(Strict):
    asset_id: int = Field(gt=0)
    direction: Literal["BUY", "SELL"]
    quantity: float | None = Field(None, gt=0, le=1e12)
    risk_pct: float | None = Field(None, gt=0, le=5)
    stop_loss: float | None = Field(None, gt=0)
    take_profit: float | None = Field(None, gt=0)
    signal_id: int | None = Field(None, gt=0)
    notes: str | None = Field(None, max_length=500)


class PaperResetIn(Strict):
    balance: float = Field(ge=100, le=100_000_000)


class AlertIn(Strict):
    channel: Literal["telegram", "email", "browser"]
    destination: str | None = Field(None, max_length=200)
    min_score: float = Field(75, ge=0, le=100)
    asset_ids: list[int] | None = Field(None, max_length=200)
    timeframes: list[TF] | None = None
    is_enabled: bool = True


class AlertPatch(Strict):
    min_score: float | None = Field(None, ge=0, le=100)
    asset_ids: list[int] | None = None
    timeframes: list[TF] | None = None
    is_enabled: bool | None = None


class CredentialIn(Strict):
    provider: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    label: str = Field(min_length=1, max_length=80)
    api_key: str = Field(min_length=8, max_length=512)
    api_secret: str | None = Field(None, max_length=512)


class UserSettingsIn(Strict):
    theme: Literal["light", "dark", "system"] | None = None
    default_timeframe: TF | None = None
    display_name: str | None = Field(None, max_length=80)


class FlagIn(Strict):
    enabled: bool
    reason: str | None = Field(None, max_length=300)


class StrategyDraftIn(Strict):
    params: dict
    notes: str | None = Field(None, max_length=2000)


class ActivateStrategyIn(Strict):
    acknowledge_unvalidated_reason: str | None = Field(None, min_length=10, max_length=500)


class ResearchJobIn(Strict):
    kind: Literal["ml_train", "optimize_weights"]
    timeframe: TF = "1h"
    asset_ids: list[int] | None = None
    model: Literal["hgb", "logreg"] = "hgb"
    folds: int = Field(5, ge=2, le=10)
    samples: int = Field(400, ge=50, le=5000)
    notes: str | None = Field(None, max_length=500)


class AssetIn(Strict):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    asset_class: Literal["crypto", "forex", "stock", "index", "commodity"]
    provider: Literal["binance", "twelvedata", "alpaca"]
    provider_symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9/._-]+$")
    calendar: Literal["24x7", "fx", "equity"]
    price_precision: int = Field(2, ge=0, le=10)


class AssetPatch(Strict):
    is_active: bool | None = None
    delisted_at: datetime | None = None
    name: str | None = Field(None, max_length=120)


class UserPatch(Strict):
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None


class BrokerOrderIn(Strict):
    symbol: str = Field(max_length=40)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    password: str = Field(min_length=1, max_length=128)
    confirmation_token: str = Field(min_length=1, max_length=200)


def valid_tf(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise ValueError("invalid timeframe")
    return tf
