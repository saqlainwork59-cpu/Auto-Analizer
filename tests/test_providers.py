"""Provider adapters tested against documented response formats (HTTP mocked with respx).
Also covers API-failure handling: errors must surface, never be replaced by invented prices."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.data.providers.alpaca import AlpacaProvider, parse_ts
from app.data.providers.base import ProviderError, ProviderNotConfigured, ProviderRateLimited, TokenBucket
from app.data.providers.binance import BinanceProvider, parse_stream_message
from app.data.providers.twelvedata import TwelveDataProvider, parse_time_series

BINANCE_KLINE = [1499040000000, "0.01634790", "0.80000000", "0.01575800", "0.01577100", "148976.11427815",
                 1499644799999, "2434.19055334", 308, "1756.87402397", "28.46694368", "0"]


@respx.mock
async def test_binance_klines_parse_and_paginate():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step = 3600_000
    start = now_ms - step * 1500
    page1 = [[start + i * step, "1", "2", "0.5", "1.5", "10", start + (i + 1) * step - 1, "0", 1, "0", "0", "0"] for i in range(1000)]
    page2 = [[start + (1000 + i) * step, "1", "2", "0.5", "1.5", "10", start + (1001 + i) * step - 1, "0", 1, "0", "0", "0"]
             for i in range(200)]
    route = respx.get("https://api.binance.com/api/v3/klines").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    p = BinanceProvider()
    out = await p.fetch_candles("BTCUSDT", "1h", start=datetime.fromtimestamp(start / 1000, tz=timezone.utc), limit=1500)
    assert len(out) == 1200 and route.call_count == 2
    assert out[0].open == 1.0 and out[0].high == 2.0 and out[0].volume == 10.0
    assert all(c.closed for c in out[:-1])


@respx.mock
async def test_binance_geo_block_and_rate_limit_surface_as_errors():
    respx.get("https://api.binance.com/api/v3/klines").mock(return_value=httpx.Response(451, json={"msg": "restricted"}))
    with pytest.raises(ProviderError, match="region"):
        await BinanceProvider().fetch_candles("BTCUSDT", "1h", limit=10)
    respx.get("https://api.binance.com/api/v3/klines").mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderRateLimited):
        await BinanceProvider().fetch_candles("BTCUSDT", "1h", limit=10)


@respx.mock
async def test_network_failure_is_an_error_not_data():
    respx.get("https://api.binance.com/api/v3/klines").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ProviderError, match="network"):
        await BinanceProvider().fetch_candles("BTCUSDT", "1h", limit=10)


@respx.mock
async def test_malformed_payload_rejected():
    respx.get("https://api.binance.com/api/v3/klines").mock(return_value=httpx.Response(200, json=[[1, "x"]]))
    with pytest.raises(ProviderError):
        await BinanceProvider().fetch_candles("BTCUSDT", "1h", limit=10)
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=httpx.Response(200, json=[[1499040000000, "NaN", "1", "1", "1", "1", 1499043599999]]))
    with pytest.raises(ProviderError):
        await BinanceProvider().fetch_candles("BTCUSDT", "1h", limit=10)


def test_binance_stream_message():
    msg = {"stream": "btcusdt@kline_15m", "data": {"e": "kline", "E": 1700000000123, "s": "BTCUSDT", "k": {
        "t": 1699999200000, "T": 1700000099999, "s": "BTCUSDT", "i": "15m", "o": "37000.1", "c": "37010.5",
        "h": "37020", "l": "36990", "v": "12.5", "x": True}}}
    tick = parse_stream_message(msg)
    assert tick.timeframe == "15m" and tick.candle.closed and tick.price == 37010.5
    assert parse_stream_message({"data": {"e": "trade"}}) is None


def test_twelvedata_parse_and_errors():
    now = datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc)
    data = {"meta": {"symbol": "EUR/USD"}, "status": "ok", "values": [
        {"datetime": "2024-01-02 11:00:00", "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15"},
        {"datetime": "2024-01-02 10:00:00", "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.1"}]}
    out = parse_time_series(data, "1h", now)
    assert [c.open_time.hour for c in out] == [10, 11]
    assert out[0].volume is None  # FX: no volume -> None, not 0
    assert out[1].closed  # 11:00 + 1h <= 12:00
    with pytest.raises(ProviderRateLimited):
        parse_time_series({"status": "error", "code": 429, "message": "run out of API credits"}, "1h", now)
    with pytest.raises(ProviderError, match="plan"):
        parse_time_series({"status": "error", "code": 403, "message": "upgrade"}, "1h", now)


async def test_unconfigured_providers_refuse(monkeypatch):
    with pytest.raises(ProviderNotConfigured):
        await TwelveDataProvider().fetch_candles("EUR/USD", "1h")
    with pytest.raises(ProviderNotConfigured):
        await AlpacaProvider().fetch_candles("AAPL", "1h")


@respx.mock
async def test_alpaca_bars_pagination(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "alpaca_api_key_id", "k" * 20)
    monkeypatch.setattr(s, "alpaca_api_secret_key", "s" * 20)
    base = datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc)
    mk = lambda i: {"t": (base + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"), "o": 10, "h": 11,  # noqa: E731
                    "l": 9, "c": 10.5, "v": 1000, "n": 5, "vw": 10.2}
    route = respx.get("https://data.alpaca.markets/v2/stocks/bars").mock(side_effect=[
        httpx.Response(200, json={"bars": {"AAPL": [mk(i) for i in range(3)]}, "next_page_token": "abc"}),
        httpx.Response(200, json={"bars": {"AAPL": [mk(i) for i in range(3, 5)]}, "next_page_token": None}),
    ])
    out = await AlpacaProvider().fetch_candles("AAPL", "15m", start=base, limit=100)
    assert len(out) == 5 and route.call_count == 2
    assert route.calls[0].request.headers["APCA-API-KEY-ID"] == "k" * 20
    assert parse_ts("2024-01-03T14:30:00.123456789Z") == datetime(2024, 1, 3, 14, 30, 0, 123456, tzinfo=timezone.utc)


async def test_token_bucket_limits_rate():
    import time

    b = TokenBucket(per_minute=600)  # 10/s
    t0 = time.monotonic()
    for _ in range(605):
        await b.acquire()
    assert time.monotonic() - t0 >= 0.4
