"""ORM models. Every table the platform needs for auditability lives here."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, BigIntPK, JSONType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- users / auth
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    family: Mapped[str] = mapped_column(String(64), index=True)  # rotation family, for reuse detection
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))


class ApiCredential(Base):
    """User-supplied third-party credentials (future broker keys). Stored encrypted (Fernet)."""

    __tablename__ = "api_credentials"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    encrypted_key: Mapped[str] = mapped_column(Text)
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))  # last 4 chars only, for display
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)


# --------------------------------------------------------------------------- market data
class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("provider", "provider_symbol", name="uq_asset_provider_symbol"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # display, e.g. BTC/USDT
    name: Mapped[str] = mapped_column(String(120))
    asset_class: Mapped[str] = mapped_column(String(16), index=True)  # crypto|forex|stock|index|commodity
    provider: Mapped[str] = mapped_column(String(24))  # binance|twelvedata|alpaca
    provider_symbol: Mapped[str] = mapped_column(String(40))
    calendar: Mapped[str] = mapped_column(String(12), default="24x7")  # 24x7|fx|equity
    price_precision: Mapped[int] = mapped_column(Integer, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_at: Mapped[datetime | None] = mapped_column(TS)
    delisted_at: Mapped[datetime | None] = mapped_column(TS)  # kept to avoid survivorship bias
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)


class Candle(Base):
    __tablename__ = "market_data"
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(TS, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24))
    ingested_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class IndicatorSnapshot(Base):
    """Indicator values computed at a closed bar (written by the analysis engine for auditing)."""

    __tablename__ = "indicators"
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(TS, primary_key=True)
    values: Mapped[dict] = mapped_column(JSONType)
    computed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DataFeed(Base):
    __tablename__ = "data_feeds"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. binance-ws, twelvedata-rest
    provider: Mapped[str] = mapped_column(String(24))
    description: Mapped[str] = mapped_column(String(200), default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|degraded|down|disabled|unconfigured
    last_message_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# --------------------------------------------------------------------------- strategies & signals
class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_strategy_version"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|candidate|active|retired
    params: Mapped[dict] = mapped_column(JSONType)
    validation: Mapped[dict | None] = mapped_column(JSONType)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(TS)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_version"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|production|retired|rejected
    algorithm: Mapped[str] = mapped_column(String(60))
    artifact_path: Mapped[str] = mapped_column(String(400))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    feature_names: Mapped[list] = mapped_column(JSONType)
    dataset: Mapped[dict] = mapped_column(JSONType)  # ranges, hash, row counts
    metrics: Mapped[dict] = mapped_column(JSONType)  # per-fold & out-of-sample metrics
    overfit: Mapped[dict] = mapped_column(JSONType)
    params: Mapped[dict] = mapped_column(JSONType)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(TS)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("asset_id", "timeframe", "bar_time", "strategy_version", name="uq_signal_bar"),
        Index("ix_signals_created", "created_at"),
        Index("ix_signals_status_created", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    bar_time: Mapped[datetime] = mapped_column(TS)  # close time of the analysed (closed) candle
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(20))  # ACTIONABLE | WAIT | DATA_UNAVAILABLE
    direction: Mapped[str] = mapped_column(String(8))  # BUY | SELL | WAIT
    setup_type: Mapped[str | None] = mapped_column(String(40))
    regime: Mapped[str] = mapped_column(String(20))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    entry_low: Mapped[float | None] = mapped_column(Float)
    entry_high: Mapped[float | None] = mapped_column(Float)
    entry_ref: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float | None] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    rr_tp1: Mapped[float | None] = mapped_column(Float)
    rr_tp2: Mapped[float | None] = mapped_column(Float)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    headline: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    risks: Mapped[list] = mapped_column(JSONType, default=list)
    invalidation: Mapped[list] = mapped_column(JSONType, default=list)
    components: Mapped[list] = mapped_column(JSONType, default=list)  # score breakdown
    mtf: Mapped[dict] = mapped_column(JSONType, default=dict)
    levels_meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    data_quality: Mapped[dict] = mapped_column(JSONType, default=dict)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"))
    strategy_version: Mapped[str] = mapped_column(String(40))
    engine_version: Mapped[str] = mapped_column(String(20))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    ml_probability: Mapped[float | None] = mapped_column(Float)

    asset: Mapped[Asset] = relationship(lazy="joined")
    outcome: Mapped["SignalOutcome | None"] = relationship(back_populates="signal", lazy="joined", uselist=False)


class SignalFeature(Base):
    __tablename__ = "signal_features"
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True)
    features: Mapped[dict] = mapped_column(JSONType)


class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    # PENDING | OPEN | WIN_TP2 | WIN_TP1 | LOSS | BREAKEVEN | TIME_EXIT | EXPIRED | INVALIDATED
    filled_at: Mapped[datetime | None] = mapped_column(TS)
    fill_price: Mapped[float | None] = mapped_column(Float)
    exit_at: Mapped[datetime | None] = mapped_column(TS)
    exit_price: Mapped[float | None] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    mfe_r: Mapped[float | None] = mapped_column(Float)
    mae_r: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    signal: Mapped[Signal] = relationship(back_populates="outcome")


# --------------------------------------------------------------------------- backtests
class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|failed
    params: Mapped[dict] = mapped_column(JSONType)
    strategy_snapshot: Mapped[dict] = mapped_column(JSONType)
    metrics: Mapped[dict | None] = mapped_column(JSONType)
    equity_curve: Mapped[list | None] = mapped_column(JSONType)
    data_summary: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    setup_type: Mapped[str | None] = mapped_column(String(40))
    regime: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[float] = mapped_column(Float)
    signal_time: Mapped[datetime] = mapped_column(TS)
    entry_time: Mapped[datetime | None] = mapped_column(TS)
    exit_time: Mapped[datetime | None] = mapped_column(TS)
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(16))
    r_multiple: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float)
    equity_after: Mapped[float] = mapped_column(Float)


# --------------------------------------------------------------------------- paper trading
class PaperAccount(Base):
    __tablename__ = "portfolio"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    starting_balance: Mapped[float] = mapped_column(Float)
    cash_balance: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.001)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    reset_at: Mapped[datetime | None] = mapped_column(TS)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    __table_args__ = (Index("ix_paper_trades_account_status", "account_id", "status"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("portfolio.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(8))  # BUY | SELL
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")  # OPEN | CLOSED
    opened_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    exit_price: Mapped[float | None] = mapped_column(Float)
    close_reason: Mapped[str | None] = mapped_column(String(16))  # MANUAL | STOP | TARGET
    pnl: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    price_source: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str | None] = mapped_column(String(500))

    asset: Mapped[Asset] = relationship(lazy="joined")


# --------------------------------------------------------------------------- watchlist & alerts
class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    asset: Mapped[Asset] = relationship(lazy="joined")


class AlertRule(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(12))  # telegram | email | browser
    destination_encrypted: Mapped[str | None] = mapped_column(Text)  # chat id / email, encrypted at rest
    destination_hint: Mapped[str | None] = mapped_column(String(80))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score: Mapped[float] = mapped_column(Float, default=75.0)
    asset_ids: Mapped[list | None] = mapped_column(JSONType)  # None = all watchlist assets
    timeframes: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "signal_id", name="uq_alert_signal"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"))
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12))  # sent | failed | skipped
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# --------------------------------------------------------------------------- operations
class SystemFlag(Base):
    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String(300))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (Index("ix_system_logs_created", "created_at"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    level: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSONType, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(120))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)


class ResearchJob(Base):
    """Long-running research tasks (ML training, weight optimisation) executed by the worker."""

    __tablename__ = "research_jobs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))  # ml_train | optimize_weights
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|failed
    params: Mapped[dict] = mapped_column(JSONType)
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
