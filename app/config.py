"""Application settings, loaded from environment variables (never hard-coded secrets)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core -------------------------------------------------------------
    app_name: str = "Parallax"
    app_env: str = Field("development", description="development | test | production")
    secret_key: str = Field("dev-insecure-change-me-please-0123456789", min_length=16)
    # Fernet key (urlsafe base64, 32 bytes). Generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    encryption_key: str = ""
    public_app_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://parallax:parallax@localhost:5432/parallax"
    redis_url: str = "redis://localhost:6379/0"

    # --- auth -------------------------------------------------------------
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    cookie_secure: bool = False
    cookie_domain: str | None = None
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- rate limiting ----------------------------------------------------
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 8
    register_rate_limit_per_minute: int = 5

    # --- market data ------------------------------------------------------
    binance_rest_url: str = "https://api.binance.com"
    binance_ws_url: str = "wss://stream.binance.com:9443"
    twelvedata_api_key: str = ""
    twelvedata_credits_per_minute: int = 8
    twelvedata_ws_enabled: bool = False
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_ws_url: str = "wss://stream.data.alpaca.markets/v2"
    alpaca_feed: str = "iex"
    http_timeout_seconds: float = 15.0
    # Timeframes collected per REST-polled provider (each poll costs provider credits).
    twelvedata_timeframes: str = "15m,1h,4h,1d"
    alpaca_timeframes: str = "5m,15m,1h,4h,1d"

    backfill_bars: int = 1500
    analysis_timeframes: str = "15m,1h,4h"
    context_timeframes: str = "5m,15m,1h,4h,1d"

    # --- alerts -------------------------------------------------------------
    telegram_bot_token: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # --- ML ---------------------------------------------------------------
    model_dir: str = "./model_store"

    # --- execution (real money) -------------------------------------------
    # Hard, deploy-time kill switch. Even when true, execution also requires an admin
    # flag, a per-user opt-in with password re-authentication and a per-order confirmation.
    broker_execution_enabled: bool = False

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.lower()
        if v not in {"development", "test", "production"}:
            raise ValueError("app_env must be development, test or production")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def analysis_tf_list(self) -> list[str]:
        return [t.strip() for t in self.analysis_timeframes.split(",") if t.strip()]

    @property
    def context_tf_list(self) -> list[str]:
        return [t.strip() for t in self.context_timeframes.split(",") if t.strip()]

    def provider_timeframes(self, provider: str) -> list[str]:
        raw = {"twelvedata": self.twelvedata_timeframes, "alpaca": self.alpaca_timeframes}.get(provider, self.context_timeframes)
        return [t.strip() for t in raw.split(",") if t.strip()]

    def validate_production(self) -> list[str]:
        """Return a list of fatal configuration problems for production deployments."""
        problems = []
        if self.is_production:
            if self.secret_key.startswith("dev-") or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be a random value of at least 32 characters")
            if not self.encryption_key:
                problems.append("ENCRYPTION_KEY must be set (Fernet key)")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true behind HTTPS")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
