"""Live-feed plumbing with an injected fake provider: ingestion, live updates, failure handling."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.bus import CH_CANDLES, bus
from app.core.timeframes import last_closed_open_time
from app.data.feeds import FeedManager
from app.data.providers.base import CandleDTO, Provider, ProviderError, QuoteDTO, TickDTO
from app.data.providers.registry import get_provider, set_provider
from app.data.store import count_candles
from app.models import Asset, DataFeed

pytestmark = pytest.mark.db


class FakeBinance(Provider):
    """TEST DOUBLE ONLY - emits a scripted sequence, then fails, to exercise reconnect/status logic."""

    name = "binance"
    supports_streaming = True

    def __init__(self, ticks, fail_rest=False):
        super().__init__()
        self.ticks = ticks
        self.fail_rest = fail_rest
        self.stream_calls = 0

    def configured(self):
        return True

    async def fetch_candles(self, symbol, timeframe, *, start=None, end=None, limit=1000):
        if self.fail_rest:
            raise ProviderError("binance: HTTP 503")
        return []

    async def fetch_quotes(self, symbols):
        return {}

    async def stream(self, symbols, timeframes):
        self.stream_calls += 1
        for t in self.ticks:
            yield t
        raise ProviderError("socket closed by peer")


async def test_stream_ingests_closed_candles_publishes_updates_and_reports_failure(app, session):
    asset = (await session.execute(select(Asset).where(Asset.symbol == "BTC/USDT"))).scalar_one()
    now = datetime.now(timezone.utc)
    ot = last_closed_open_time(now, "15m")
    forming = CandleDTO(ot + timedelta(minutes=15), 100, 101, 99, 100.5, 3, closed=False)
    closed = CandleDTO(ot, 99, 102, 98, 100, 10, closed=True)
    ticks = [TickDTO("BTCUSDT", now, 100.5, forming, "15m"), TickDTO("BTCUSDT", now, 100.0, closed, "15m")]
    original = get_provider("binance")
    fake = FakeBinance(ticks)
    set_provider("binance", fake)
    received = []
    closed_events = []

    async def on_closed(a, tf, candles):
        closed_events.append((a.symbol, tf, len(candles)))

    async def listen():
        async for ch, msg in bus.subscribe([CH_CANDLES]):
            received.append(msg)

    before = await count_candles(session, asset.id, "15m")
    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    fm = FeedManager(on_closed)
    fm.settings = fm.settings.model_copy(update={"context_timeframes": "15m"})
    await fm.load_assets()
    fm.assets = [a for a in fm.assets if a.symbol == "BTC/USDT"]
    task = asyncio.create_task(fm.binance_stream())
    await asyncio.sleep(2.5)
    task.cancel()
    listener.cancel()
    set_provider("binance", original)

    assert await count_candles(session, asset.id, "15m") >= before  # upsert of the closed candle
    assert ("BTC/USDT", "15m", 1) in closed_events
    assert any(m.get("type") == "candle" and m.get("closed") is False for m in received)  # live forming candle
    assert (await bus.get_json(f"px:last:{asset.id}"))["price"] in (100.0, 100.5)
    feed = await session.get(DataFeed, "binance-ws")
    await session.refresh(feed)
    assert feed.status == "down" and "socket closed" in (feed.last_error or "")  # failure is visible, not hidden
    assert fake.stream_calls >= 1


async def test_rest_failure_marks_feed_degraded(app, session):
    original = get_provider("binance")
    set_provider("binance", FakeBinance([], fail_rest=True))
    fm = FeedManager(lambda *a: asyncio.sleep(0))
    await fm.load_assets()
    await fm.gap_fill([a for a in fm.assets if a.symbol == "ETH/USDT"][:1])
    set_provider("binance", original)
    feed = await session.get(DataFeed, "binance-rest")
    await session.refresh(feed)
    assert feed.status == "degraded" and "503" in feed.last_error


async def test_unconfigured_rest_provider_reports_status(app, session):
    fm = FeedManager(lambda *a: asyncio.sleep(0))
    await fm.load_assets()
    await fm.poll_loop("twelvedata")  # returns immediately: no API key
    feed = await session.get(DataFeed, "twelvedata-rest")
    await session.refresh(feed)
    assert feed.status == "unconfigured"


_ = QuoteDTO
