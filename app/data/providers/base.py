"""Market-data provider interface.

Providers return *real* data or raise. There is deliberately no synthetic / fallback price path:
callers turn exceptions into a visible "Data unavailable" state.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.config import get_settings


class ProviderError(RuntimeError):
    """Provider responded with an error or unparseable data."""


class ProviderNotConfigured(ProviderError):
    """Credentials missing - the market is shown as 'Data unavailable (provider not configured)'."""


class ProviderRateLimited(ProviderError):
    pass


@dataclass(frozen=True)
class CandleDTO:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    closed: bool


@dataclass(frozen=True)
class QuoteDTO:
    symbol: str              # provider symbol
    price: float
    change_pct: float | None
    volume: float | None
    ts: datetime
    market_open: bool | None = None


@dataclass(frozen=True)
class TickDTO:
    """A streamed update: either a trade/price tick or a (forming or closed) candle."""

    symbol: str
    ts: datetime
    price: float
    candle: CandleDTO | None = None
    timeframe: str | None = None


class TokenBucket:
    """Async token bucket for provider credit limits (e.g. Twelve Data 8 credits/min on the free plan)."""

    def __init__(self, per_minute: int) -> None:
        self.capacity = max(1, per_minute)
        self.tokens = float(self.capacity)
        self.rate = self.capacity / 60.0
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        n = min(n, self.capacity)
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                await asyncio.sleep((n - self.tokens) / self.rate)


class Provider(ABC):
    name: str = "base"
    asset_classes: tuple[str, ...] = ()
    supports_streaming: bool = False

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=get_settings().http_timeout_seconds,
                                             headers={"User-Agent": "parallax-analysis/1.0"})
        return self._client

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def fetch_candles(self, symbol: str, timeframe: str, *, start: datetime | None = None,
                            end: datetime | None = None, limit: int = 1000) -> list[CandleDTO]: ...

    @abstractmethod
    async def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]: ...

    async def stream(self, symbols: list[str], timeframes: list[str]) -> AsyncIterator[TickDTO]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]

    async def _get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None):
        try:
            r = await self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: network error: {exc.__class__.__name__}") from exc
        if r.status_code == 429:
            raise ProviderRateLimited(f"{self.name}: rate limited (HTTP 429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"{self.name}: authentication/permission error (HTTP {r.status_code})")
        if r.status_code == 451:
            raise ProviderError(f"{self.name}: service unavailable from this server's region (HTTP 451)")
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name}: invalid JSON response") from exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def utc_ms(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def to_float(v) -> float:
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        raise ProviderError("non-finite numeric value in provider payload")
    return f
