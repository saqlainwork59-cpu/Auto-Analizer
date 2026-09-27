"""Binance spot market data (public endpoints, no API key).

REST  : GET /api/v3/klines, GET /api/v3/ticker/24hr
Stream: combined kline streams  <ws>/stream?streams=btcusdt@kline_15m/...

Binance.com is geo-restricted in some jurisdictions (HTTP 451, e.g. the US). Point
BINANCE_REST_URL / BINANCE_WS_URL at https://api.binance.us / wss://stream.binance.us:9443 there.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import websockets

from app.config import get_settings
from app.core.timeframes import seconds
from app.data.providers.base import CandleDTO, Provider, ProviderError, QuoteDTO, TickDTO, to_float, utc_ms

INTERVALS = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


def parse_kline(row: list, now_ms: int) -> CandleDTO:
    if not isinstance(row, list) or len(row) < 7:
        raise ProviderError("binance: malformed kline row")
    return CandleDTO(
        open_time=utc_ms(int(row[0])),
        open=to_float(row[1]),
        high=to_float(row[2]),
        low=to_float(row[3]),
        close=to_float(row[4]),
        volume=to_float(row[5]),
        closed=int(row[6]) < now_ms,
    )


class BinanceProvider(Provider):
    name = "binance"
    asset_classes = ("crypto",)
    supports_streaming = True

    def configured(self) -> bool:
        return True  # public market data

    @property
    def rest(self) -> str:
        return get_settings().binance_rest_url.rstrip("/")

    async def fetch_candles(self, symbol: str, timeframe: str, *, start: datetime | None = None,
                            end: datetime | None = None, limit: int = 1000) -> list[CandleDTO]:
        interval = INTERVALS[timeframe]
        out: list[CandleDTO] = []
        step_ms = seconds(timeframe) * 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if start is None:
            start_ms = now_ms - step_ms * (limit + 1)
        else:
            start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000) if end else None
        remaining = limit
        while remaining > 0:
            params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": min(1000, remaining)}
            if end_ms:
                params["endTime"] = end_ms
            data = await self._get_json(f"{self.rest}/api/v3/klines", params=params)
            if isinstance(data, dict):
                raise ProviderError(f"binance: {data.get('msg', 'unexpected response')}")
            if not data:
                break
            batch = [parse_kline(r, now_ms) for r in data]
            out.extend(batch)
            remaining -= len(batch)
            if len(batch) < params["limit"]:
                break
            start_ms = int(batch[-1].open_time.timestamp() * 1000) + step_ms
        return out

    async def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]:
        if not symbols:
            return {}
        data = await self._get_json(f"{self.rest}/api/v3/ticker/24hr",
                                    params={"symbols": json.dumps(symbols, separators=(",", ":"))})
        if not isinstance(data, list):
            raise ProviderError("binance: unexpected ticker response")
        out = {}
        for row in data:
            out[row["symbol"]] = QuoteDTO(
                symbol=row["symbol"],
                price=to_float(row["lastPrice"]),
                change_pct=to_float(row["priceChangePercent"]),
                volume=to_float(row.get("quoteVolume", 0)),
                ts=utc_ms(int(row["closeTime"])),
                market_open=True,
            )
        return out

    async def stream(self, symbols: list[str], timeframes: list[str]) -> AsyncIterator[TickDTO]:
        """Yield kline updates (forming and closed). Reconnection is handled by the caller."""
        streams = [f"{s.lower()}@kline_{INTERVALS[tf]}" for s in symbols for tf in timeframes]
        if not streams:
            return
        url = f"{get_settings().binance_ws_url.rstrip('/')}/stream?streams={'/'.join(streams)}"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                tick = parse_stream_message(msg)
                if tick is not None:
                    yield tick
                await asyncio.sleep(0)


def parse_stream_message(msg: dict) -> TickDTO | None:
    data = msg.get("data", msg)
    if data.get("e") != "kline":
        return None
    k = data["k"]
    tf = {v: kk for kk, v in INTERVALS.items()}.get(k["i"])
    if tf is None:
        return None
    candle = CandleDTO(
        open_time=utc_ms(int(k["t"])),
        open=to_float(k["o"]),
        high=to_float(k["h"]),
        low=to_float(k["l"]),
        close=to_float(k["c"]),
        volume=to_float(k["v"]),
        closed=bool(k["x"]),
    )
    return TickDTO(symbol=k["s"], ts=utc_ms(int(data["E"])), price=candle.close, candle=candle, timeframe=tf)
