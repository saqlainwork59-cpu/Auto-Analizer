"""Live data feed manager (runs inside the worker).

  * Binance: WebSocket kline streams (forming + closed candles), REST gap-fill on (re)connect.
  * Twelve Data / Alpaca: REST polling right after each candle closes (credit-aware), optional
    WebSocket price ticks for live prices.
Every feed reports its health to `data_feeds`; a failing feed makes its markets show
"Data unavailable" - nothing is ever synthesised.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.core import flags
from app.core.audit import system_log
from app.core.bus import CH_CANDLES, CH_SYSTEM, bus
from app.core.timeframes import delta, last_closed_open_time, seconds
from app.data.backfill import backfill_recent
from app.data.providers.base import CandleDTO, ProviderError, ProviderNotConfigured, TickDTO
from app.data.providers.registry import get_provider
from app.data.store import latest_open_time, upsert_candles
from app.db import session_factory
from app.models import Asset, DataFeed, utcnow

log = logging.getLogger(__name__)

FEEDS = {
    "binance-ws": ("binance", "Binance kline WebSocket (crypto)"),
    "binance-rest": ("binance", "Binance REST backfill / gap repair"),
    "twelvedata-rest": ("twelvedata", "Twelve Data REST candles (forex, metals, indices)"),
    "twelvedata-ws": ("twelvedata", "Twelve Data price WebSocket (plan dependent)"),
    "alpaca-rest": ("alpaca", "Alpaca REST bars (US equities)"),
    "alpaca-ws": ("alpaca", "Alpaca trade WebSocket (US equities)"),
}

OnClosed = Callable[[Asset, str, list[CandleDTO]], Awaitable[None]]


def candle_json(c: CandleDTO) -> dict:
    return {"time": int(c.open_time.timestamp()), "open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume}


async def set_feed_status(feed_id: str, status: str, error: str | None = None, message: bool = False) -> None:
    async with session_factory()() as s:
        row = await s.get(DataFeed, feed_id)
        if row is None:
            prov, desc = FEEDS[feed_id]
            row = DataFeed(id=feed_id, provider=prov, description=desc)
            s.add(row)
        changed = row.status != status
        row.status = status
        if error is not None:
            row.last_error = error[:1000]
        if message:
            row.last_message_at = utcnow()
        row.updated_at = utcnow()
        await s.commit()
    if changed:
        await bus.publish(CH_SYSTEM, {"type": "feed", "feed": feed_id, "status": status, "error": error})


class FeedManager:
    def __init__(self, on_closed: OnClosed) -> None:
        self.on_closed = on_closed
        self.settings = get_settings()
        self._last_pub: dict[tuple, float] = defaultdict(float)
        self._msg_mark: dict[str, float] = defaultdict(float)
        self.assets: list[Asset] = []

    async def load_assets(self) -> None:
        async with session_factory()() as s:
            self.assets = list((await s.execute(select(Asset).where(Asset.is_active.is_(True)))).scalars())

    def by_provider(self, provider: str) -> list[Asset]:
        return [a for a in self.assets if a.provider == provider]

    def timeframes(self, provider: str) -> list[str]:
        return self.settings.context_tf_list if provider == "binance" else self.settings.provider_timeframes(provider)

    # ------------------------------------------------------------------ live price / forming candle
    async def publish_tick(self, asset: Asset, tick: TickDTO, source: str) -> None:
        await bus.set_json(f"px:last:{asset.id}", {"price": tick.price, "ts": tick.ts.isoformat(), "source": source}, ex=86400)
        now = time.monotonic()
        key = (asset.id, tick.timeframe)
        if now - self._last_pub[key] >= 1.0:  # throttle UI updates to 1/s per market/timeframe
            self._last_pub[key] = now
            payload = {"type": "price", "asset_id": asset.id, "symbol": asset.symbol, "price": tick.price,
                       "ts": tick.ts.isoformat(), "source": source}
            if tick.candle is not None and tick.timeframe:
                payload.update(type="candle", timeframe=tick.timeframe, candle=candle_json(tick.candle), closed=False)
                await bus.set_json(f"px:forming:{asset.id}:{tick.timeframe}", candle_json(tick.candle), ex=seconds(tick.timeframe) * 2)
            await bus.publish(CH_CANDLES, payload)

    async def mark_message(self, feed_id: str) -> None:
        now = time.monotonic()
        if now - self._msg_mark[feed_id] > 15:
            self._msg_mark[feed_id] = now
            await set_feed_status(feed_id, "ok", message=True)

    async def handle_closed(self, asset: Asset, tf: str, candles: list[CandleDTO]) -> None:
        closed = [c for c in candles if c.closed]
        if not closed:
            return
        async with session_factory()() as s:
            await upsert_candles(s, asset.id, tf, closed, asset.provider)
        last = closed[-1]
        await bus.publish(CH_CANDLES, {"type": "candle", "asset_id": asset.id, "symbol": asset.symbol, "timeframe": tf,
                                       "candle": candle_json(last), "closed": True})
        if asset.provider != "binance" or not await bus.get_json(f"px:last:{asset.id}"):
            # REST-only markets: the latest *closed* price is the freshest verified price we have
            close_ts = last.open_time + delta(tf)
            cur = await bus.get_json(f"px:last:{asset.id}")
            if not cur or datetime.fromisoformat(cur["ts"]) < close_ts:
                await bus.set_json(f"px:last:{asset.id}", {"price": last.close, "ts": close_ts.isoformat(),
                                                           "source": f"{asset.provider} {tf} close"}, ex=86400)
        await self.on_closed(asset, tf, closed)

    # ------------------------------------------------------------------ startup backfill
    async def initial_backfill(self) -> None:
        bars = self.settings.backfill_bars
        for asset in self.assets:
            prov = get_provider(asset.provider)
            if not prov.configured() or not await flags.get_flag(f"feed:{asset.provider}"):
                continue
            for tf in self.timeframes(asset.provider):
                try:
                    async with session_factory()() as s:
                        n = await backfill_recent(s, asset, tf, bars)
                    log.info("backfill %s %s: %d candles", asset.symbol, tf, n)
                    await set_feed_status(f"{asset.provider}-rest", "ok", message=True)
                except ProviderError as exc:
                    await set_feed_status(f"{asset.provider}-rest", "degraded", str(exc))
                    await system_log("WARNING", "backfill", f"{asset.symbol} {tf}: {exc}")

    # ------------------------------------------------------------------ Binance WebSocket
    async def binance_stream(self) -> None:
        assets = self.by_provider("binance")
        if not assets:
            await set_feed_status("binance-ws", "disabled", "no active crypto markets")
            return
        by_symbol = {a.provider_symbol: a for a in assets}
        tfs = self.timeframes("binance")
        prov = get_provider("binance")
        backoff = 1.0
        while True:
            if not await flags.get_flag("feed:binance"):
                await set_feed_status("binance-ws", "disabled", "disabled by administrator")
                await asyncio.sleep(10)
                continue
            try:
                await self.gap_fill(assets)
                async for tick in prov.stream(list(by_symbol), tfs):
                    asset = by_symbol.get(tick.symbol)
                    if asset is None:
                        continue
                    backoff = 1.0
                    await self.mark_message("binance-ws")
                    await self.publish_tick(asset, tick, "binance ws")
                    if tick.candle and tick.candle.closed and tick.timeframe:
                        await self.handle_closed(asset, tick.timeframe, [tick.candle])
                    if not await flags.get_flag("feed:binance"):
                        break
                raise ProviderError("stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure -> visible status + reconnect
                await set_feed_status("binance-ws", "down", f"{exc.__class__.__name__}: {exc}")
                log.warning("binance ws error: %s; reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def gap_fill(self, assets: list[Asset]) -> None:
        for asset in assets:
            for tf in self.timeframes(asset.provider):
                try:
                    async with session_factory()() as s:
                        last = await latest_open_time(s, asset.id, tf)
                    prov = get_provider(asset.provider)
                    start = (last - delta(tf)) if last else None
                    candles = await prov.fetch_candles(asset.provider_symbol, tf, start=start,
                                                       limit=1000 if start else self.settings.backfill_bars)
                    await self.handle_closed(asset, tf, candles)
                except ProviderError as exc:
                    await set_feed_status(f"{asset.provider}-rest", "degraded", str(exc))

    async def reconcile_loop(self) -> None:
        """Periodic REST reconciliation for streamed markets (catches silently dropped messages)."""
        while True:
            await asyncio.sleep(900)
            if await flags.get_flag("feed:binance"):
                await self.gap_fill(self.by_provider("binance"))

    # ------------------------------------------------------------------ REST polling (Twelve Data / Alpaca)
    async def poll_loop(self, provider: str) -> None:
        feed = f"{provider}-rest"
        prov = get_provider(provider)
        assets = self.by_provider(provider)
        if not assets:
            await set_feed_status(feed, "disabled", "no active markets for this provider")
            return
        if not prov.configured():
            await set_feed_status(feed, "unconfigured", "API credentials not set - markets show 'Data unavailable'")
            return
        next_check: dict[tuple[int, str], datetime] = {}
        while True:
            if not await flags.get_flag(f"feed:{provider}"):
                await set_feed_status(feed, "disabled", "disabled by administrator")
                await asyncio.sleep(15)
                continue
            now = datetime.now(timezone.utc)
            for asset in assets:
                for tf in self.timeframes(provider):
                    key = (asset.id, tf)
                    if next_check.get(key, now) > now:
                        continue
                    expected = last_closed_open_time(now, tf)
                    async with session_factory()() as s:
                        last = await latest_open_time(s, asset.id, tf)
                    if last is not None and last >= expected:
                        next_check[key] = expected + 2 * delta(tf) + timedelta(seconds=10)
                        continue
                    try:
                        candles = await prov.fetch_candles(asset.provider_symbol, tf, limit=10)
                        await self.handle_closed(asset, tf, candles)
                        await set_feed_status(feed, "ok", message=True)
                        got = max((c.open_time for c in candles if c.closed), default=None)
                        if got is not None and got >= expected:
                            next_check[key] = expected + 2 * delta(tf) + timedelta(seconds=10)
                        else:  # market closed or provider lag: back off to protect API credits
                            next_check[key] = now + timedelta(seconds=min(max(seconds(tf) / 3, 60), 900))
                    except ProviderNotConfigured as exc:
                        await set_feed_status(feed, "unconfigured", str(exc))
                        return
                    except ProviderError as exc:
                        await set_feed_status(feed, "degraded", str(exc))
                        next_check[key] = now + timedelta(seconds=120)
            await asyncio.sleep(5)

    async def tick_stream(self, provider: str) -> None:
        feed = f"{provider}-ws"
        if provider == "twelvedata" and not self.settings.twelvedata_ws_enabled:
            await set_feed_status(feed, "disabled", "TWELVEDATA_WS_ENABLED=false (enable if your plan includes WebSocket)")
            return
        prov = get_provider(provider)
        assets = self.by_provider(provider)
        if not assets or not prov.configured():
            await set_feed_status(feed, "unconfigured" if assets else "disabled", "not configured")
            return
        by_symbol = {a.provider_symbol: a for a in assets}
        backoff = 1.0
        while True:
            if not await flags.get_flag(f"feed:{provider}"):
                await set_feed_status(feed, "disabled", "disabled by administrator")
                await asyncio.sleep(10)
                continue
            try:
                async for tick in prov.stream(list(by_symbol), []):
                    asset = by_symbol.get(tick.symbol)
                    if asset:
                        backoff = 1.0
                        await self.mark_message(feed)
                        await self.publish_tick(asset, tick, f"{provider} ws")
                raise ProviderError("stream ended")
            except asyncio.CancelledError:
                raise
            except ProviderNotConfigured as exc:
                await set_feed_status(feed, "unconfigured", str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                await set_feed_status(feed, "down", f"{exc.__class__.__name__}: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def run(self) -> None:
        await self.load_assets()
        for fid in FEEDS:
            prov = FEEDS[fid][0]
            if not get_provider(prov).configured():
                await set_feed_status(fid, "unconfigured", "API credentials not set")
        await self.initial_backfill()
        await asyncio.gather(
            self.binance_stream(),
            self.reconcile_loop(),
            self.poll_loop("twelvedata"),
            self.poll_loop("alpaca"),
            self.tick_stream("twelvedata"),
            self.tick_stream("alpaca"),
        )
