"""Alpaca Market Data v2: US equities / ETFs. Requires ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY.

REST  : GET /v2/stocks/bars, GET /v2/stocks/snapshots
Stream: wss://stream.data.alpaca.markets/v2/{feed} (trades)

The free plan uses the IEX feed, which covers only IEX-exchange prints. Prices track the
consolidated tape closely but *volume is partial*; this is shown in the UI for these assets.
"""
from __future__ import annotations

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
    QuoteDTO,
    TickDTO,
    to_float,
)

TIMEFRAMES = {"5m": "5Min", "15m": "15Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}


def parse_ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:  # trim nanoseconds to microseconds for fromisoformat
        head, tail = s.split(".", 1)
        frac, tz = (tail[:-6], tail[-6:]) if tail[-6] in "+-" else (tail, "")
        s = f"{head}.{frac[:6]}{tz}"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def parse_bars(rows: list[dict], timeframe: str, now: datetime) -> list[CandleDTO]:
    step = timedelta(seconds=seconds(timeframe))
    out = []
    for b in rows:
        ot = parse_ts(b["t"])
        out.append(CandleDTO(ot, to_float(b["o"]), to_float(b["h"]), to_float(b["l"]), to_float(b["c"]),
                             to_float(b.get("v", 0)), closed=ot + step <= now))
    return out


class AlpacaProvider(Provider):
    name = "alpaca"
    asset_classes = ("stock",)
    supports_streaming = True

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.alpaca_api_key_id and s.alpaca_api_secret_key)

    @property
    def headers(self) -> dict:
        s = get_settings()
        return {"APCA-API-KEY-ID": s.alpaca_api_key_id, "APCA-API-SECRET-KEY": s.alpaca_api_secret_key}

    async def fetch_candles(self, symbol: str, timeframe: str, *, start: datetime | None = None,
                            end: datetime | None = None, limit: int = 1000) -> list[CandleDTO]:
        if not self.configured():
            raise ProviderNotConfigured("alpaca: API keys not set")
        s = get_settings()
        now = datetime.now(timezone.utc)
        if start is None:
            # equities trade ~6.5h/day: look back far enough to collect `limit` bars
            span = seconds(timeframe) * limit * (24 / 6.5 if timeframe != "1d" else 1.5) * 1.1
            start = now - timedelta(seconds=span)
        params = {
            "symbols": symbol,
            "timeframe": TIMEFRAMES[timeframe],
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            "adjustment": "split",
            "feed": s.alpaca_feed,
            "sort": "asc",
        }
        if end:
            params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[CandleDTO] = []
        base = s.alpaca_data_url.rstrip("/")
        for _ in range(50):  # pagination guard
            data = await self._get_json(f"{base}/v2/stocks/bars", params=params, headers=self.headers)
            if not isinstance(data, dict) or "bars" not in data:
                raise ProviderError("alpaca: unexpected bars response")
            out.extend(parse_bars((data.get("bars") or {}).get(symbol, []), timeframe, now))
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return out[-limit:]

    async def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]:
        if not self.configured():
            raise ProviderNotConfigured("alpaca: API keys not set")
        if not symbols:
            return {}
        s = get_settings()
        data = await self._get_json(f"{s.alpaca_data_url.rstrip('/')}/v2/stocks/snapshots",
                                    params={"symbols": ",".join(symbols), "feed": s.alpaca_feed}, headers=self.headers)
        out = {}
        for sym, snap in (data or {}).items():
            trade = (snap or {}).get("latestTrade") or {}
            prev = (snap or {}).get("prevDailyBar") or {}
            daily = (snap or {}).get("dailyBar") or {}
            if "p" not in trade:
                continue
            price = to_float(trade["p"])
            prev_close = prev.get("c")
            out[sym] = QuoteDTO(
                symbol=sym,
                price=price,
                change_pct=(price / float(prev_close) - 1) * 100 if prev_close else None,
                volume=float(daily["v"]) if "v" in daily else None,
                ts=parse_ts(trade["t"]),
            )
        return out

    async def stream(self, symbols: list[str], timeframes: list[str]) -> AsyncIterator[TickDTO]:
        if not self.configured():
            raise ProviderNotConfigured("alpaca: API keys not set")
        s = get_settings()
        url = f"{s.alpaca_ws_url.rstrip('/')}/{s.alpaca_feed}"
        async with websockets.connect(url, ping_interval=20, max_size=2**22) as ws:
            await ws.recv()  # [{"T":"success","msg":"connected"}]
            await ws.send(json.dumps({"action": "auth", "key": s.alpaca_api_key_id, "secret": s.alpaca_api_secret_key}))
            auth = json.loads(await ws.recv())
            if not any(m.get("msg") == "authenticated" for m in auth if isinstance(m, dict)):
                raise ProviderError(f"alpaca stream: authentication failed: {auth}")
            await ws.send(json.dumps({"action": "subscribe", "trades": symbols}))
            async for raw in ws:
                for m in json.loads(raw):
                    if m.get("T") == "t":
                        yield TickDTO(symbol=m["S"], ts=parse_ts(m["t"]), price=to_float(m["p"]))
                    elif m.get("T") == "error":
                        raise ProviderError(f"alpaca stream error {m.get('code')}: {m.get('msg')}")
