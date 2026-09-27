"""Real-time message bus + small key/value cache.

Production uses Redis (pub/sub between the worker and every API replica). When Redis is
unreachable in development/tests an in-process fallback is used so the app still starts; the
admin panel shows which backend is active.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

log = logging.getLogger(__name__)

CH_CANDLES = "parallax:candles"      # live/closed candle updates
CH_SIGNALS = "parallax:signals"      # new signal evaluations
CH_SYSTEM = "parallax:system"        # flags/feed status changes
CH_PAPER = "parallax:paper"          # paper-trade events (per user filtered at WS layer)


class _MemoryBackend:
    def __init__(self) -> None:
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, channel: str, message: str) -> None:
        for q in list(self._subs[channel]):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, channels: list[str]) -> AsyncIterator[tuple[str, str]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        wrapped: dict[str, asyncio.Queue] = {}
        for ch in channels:
            cq: asyncio.Queue = asyncio.Queue(maxsize=1000)
            wrapped[ch] = cq
            self._subs[ch].add(cq)

        async def pump(ch: str, cq: asyncio.Queue) -> None:
            while True:
                await q.put((ch, await cq.get()))

        tasks = [asyncio.create_task(pump(ch, cq)) for ch, cq in wrapped.items()]
        try:
            while True:
                yield await q.get()
        finally:
            for t in tasks:
                t.cancel()
            for ch, cq in wrapped.items():
                self._subs[ch].discard(cq)

    async def get(self, key: str) -> str | None:
        v = self._kv.get(key)
        if not v:
            return None
        value, exp = v
        if exp and exp < time.time():
            self._kv.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._kv[key] = (value, time.time() + ex if ex else None)

    async def incr_window(self, key: str, window: int) -> int:
        cur = await self.get(key)
        n = int(cur or 0) + 1
        if cur is None:
            await self.set(key, str(n), ex=window)
        else:
            exp = self._kv[key][1]
            self._kv[key] = (str(n), exp)
        return n

    async def ping(self) -> bool:
        return True


class _RedisBackend:
    def __init__(self, url: str) -> None:
        self.r = aioredis.from_url(url, decode_responses=True)

    async def publish(self, channel: str, message: str) -> None:
        await self.r.publish(channel, message)

    async def subscribe(self, channels: list[str]) -> AsyncIterator[tuple[str, str]]:
        pubsub = self.r.pubsub()
        await pubsub.subscribe(*channels)
        try:
            async for msg in pubsub.listen():
                if msg and msg.get("type") == "message":
                    yield msg["channel"], msg["data"]
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    async def get(self, key: str) -> str | None:
        return await self.r.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self.r.set(key, value, ex=ex)

    async def incr_window(self, key: str, window: int) -> int:
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        n, _ = await pipe.execute()
        return int(n)

    async def ping(self) -> bool:
        return bool(await self.r.ping())


class Bus:
    def __init__(self) -> None:
        self._backend: _MemoryBackend | _RedisBackend | None = None
        self.kind = "uninitialised"

    async def connect(self) -> None:
        settings = get_settings()
        try:
            backend = _RedisBackend(settings.redis_url)
            await asyncio.wait_for(backend.ping(), timeout=3)
            self._backend, self.kind = backend, "redis"
        except Exception as exc:  # noqa: BLE001
            if settings.is_production:
                raise RuntimeError(f"Redis is required in production: {exc}") from exc
            log.warning("Redis unavailable (%s); using in-process bus (single process only)", exc)
            self._backend, self.kind = _MemoryBackend(), "memory"

    def use_memory(self) -> None:
        self._backend, self.kind = _MemoryBackend(), "memory"

    @property
    def backend(self) -> _MemoryBackend | _RedisBackend:
        if self._backend is None:
            self.use_memory()
        assert self._backend is not None
        return self._backend

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        try:
            await self.backend.publish(channel, json.dumps(payload, default=str))
        except Exception as exc:  # noqa: BLE001 - never let a publish failure break ingestion
            log.warning("publish failed on %s: %s", channel, exc)

    async def subscribe(self, channels: list[str]) -> AsyncIterator[tuple[str, dict]]:
        async for ch, raw in self.backend.subscribe(channels):
            try:
                yield ch, json.loads(raw)
            except json.JSONDecodeError:
                continue

    async def get_json(self, key: str) -> Any | None:
        raw = await self.backend.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ex: int | None = None) -> None:
        await self.backend.set(key, json.dumps(value, default=str), ex=ex)

    async def incr_window(self, key: str, window: int) -> int:
        return await self.backend.incr_window(key, window)

    async def healthy(self) -> bool:
        try:
            return await asyncio.wait_for(self.backend.ping(), timeout=2)
        except Exception:  # noqa: BLE001
            return False


bus = Bus()
