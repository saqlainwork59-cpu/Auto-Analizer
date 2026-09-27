"""Twelve Data: forex, indices, commodities (and optionally stocks). Requires TWELVEDATA_API_KEY.

REST  : GET /time_series, GET /quote     (1 credit per symbol per request)
Stream: wss://ws.twelvedata.com/v1/quotes/price (price ticks; availability depends on plan)

Note: spot FX has no centralised volume. Where Twelve Data returns no volume the engine marks
the volume component "not available" rather than inventing it.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import websockets

from app.config import get_settings
from app.core.timeframes import seconds
from app.data.providers.base import (
    CandleDTO,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    QuoteDTO,
    TickDTO,
    TokenBucket,
    to_float,
)

BASE = "https://api.twelvedata.com"
INTERVALS = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day"}


def parse_datetime(s: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def _check_error(data) -> None:
    if isinstance(data, dict) and data.get("status") == "error":
        code = data.get("code")
        msg = data.get("message", "unknown error")
        if code == 429:
            raise ProviderRateLimited(f"twelvedata: {msg}")
        if code in (401, 403):
            raise ProviderError(f"twelvedata: plan/permission error: {msg}")
        raise ProviderError(f"twelvedata: {msg}")


def parse_time_series(data: dict, timeframe: str, now: datetime) -> list[CandleDTO]:
    _check_error(data)
    values = data.get("values")
    if values is None:
        raise ProviderError("twelvedata: response has no 'values'")
    step = timedelta(seconds=seconds(timeframe))
    out = []
    for v in values:
        ot = parse_datetime(v["datetime"])
        vol = v.get("volume")
        volume = to_float(vol) if vol not in (None, "", "0") else None
        out.append(CandleDTO(ot, to_float(v["open"]), to_float(v["high"]), to_float(v["low"]), to_float(v["close"]),
                             volume, closed=ot + step <= now))
    out.sort(key=lambda c: c.open_time)
    return out


class TwelveDataProvider(Provider):
    name = "twelvedata"
    asset_classes = ("forex", "index", "commodity", "stock")
    supports_streaming = True

    def __init__(self, client=None) -> None:
        super().__init__(client)
        self.bucket = TokenBucket(get_settings().twelvedata_credits_per_minute)

    @property
    def key(self) -> str:
        return get_settings().twelvedata_api_key

    def configured(self) -> bool:
        return bool(self.key)

    async def fetch_candles(self, symbol: str, timeframe: str, *, start: datetime | None = None,
                            end: datetime | None = None, limit: int = 1000) -> list[CandleDTO]:
        if not self.configured():
            raise ProviderNotConfigured("twelvedata: TWELVEDATA_API_KEY not set")
        params = {
            "symbol": symbol,
            "interval": INTERVALS[timeframe],
            "outputsize": min(5000, max(1, limit)),
            "timezone": "UTC",
            "order": "asc",
            "apikey": self.key,
        }
        if start:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")
        await self.bucket.acquire(1)
        data = await self._get_json(f"{BASE}/time_series", params=params)
        return parse_time_series(data, timeframe, datetime.now(timezone.utc))

    async def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]:
        if not self.configured():
            raise ProviderNotConfigured("twelvedata: TWELVEDATA_API_KEY not set")
        if not symbols:
            return {}
        await self.bucket.acquire(len(symbols))
        data = await self._get_json(f"{BASE}/quote", params={"symbol": ",".join(symbols), "apikey": self.key})
        _check_error(data)
        rows = {symbols[0]: data} if len(symbols) == 1 else data
        out = {}
        for sym, q in rows.items():
            if not isinstance(q, dict) or q.get("status") == "error" or "close" not in q:
                continue
            out[sym] = QuoteDTO(
                symbol=sym,
                price=to_float(q["close"]),
                change_pct=to_float(q["percent_change"]) if q.get("percent_change") not in (None, "") else None,
                volume=to_float(q["volume"]) if q.get("volume") not in (None, "") else None,
                ts=datetime.fromtimestamp(int(q.get("timestamp") or q.get("last_quote_at") or 0), tz=timezone.utc),
                market_open=q.get("is_market_open"),
            )
        return out

    async def stream(self, symbols: list[str], timeframes: list[str]) -> AsyncIterator[TickDTO]:
        if not self.configured():
            raise ProviderNotConfigured("twelvedata: TWELVEDATA_API_KEY not set")
        url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={self.key}"
        async with websockets.connect(url, ping_interval=None, max_size=2**20) as ws:
            await ws.send(json.dumps({"action": "subscribe", "params": {"symbols": ",".join(symbols)}}))

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(10)
                    await ws.send(json.dumps({"action": "heartbeat"}))

            hb = asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("event") == "subscribe-status" and msg.get("fails"):
                        raise ProviderError(f"twelvedata stream: subscription failed for {msg['fails']}")
                    if msg.get("event") == "price" and "price" in msg:
                        yield TickDTO(
                            symbol=msg["symbol"],
                            ts=datetime.fromtimestamp(int(msg["timestamp"]), tz=timezone.utc),
                            price=to_float(msg["price"]),
                        )
            finally:
                hb.cancel()
