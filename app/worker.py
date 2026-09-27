"""Background worker: live feeds, signal engine, outcome tracking, paper-trade protection,
alerts, backtests and research jobs.

Run:  python -m app.worker
Only one worker should own the live feeds; a Redis lock enforces this when several run.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal as os_signal
import socket
from datetime import datetime, timezone

from sqlalchemy import select

from app.alerts.dispatcher import dispatch
from app.backtest import runner as bt_runner
from app.config import get_settings
from app.core.audit import system_log
from app.core.bus import bus
from app.data.feeds import FeedManager
from app.data.providers.base import CandleDTO
from app.db import init_engine, session_factory
from app.logging_setup import setup_logging
from app.ml.jobs import run_job
from app.models import Asset, Backtest, ResearchJob, utcnow
from app.paper.service import check_protective_orders
from app.seed import ensure_seed
from app.signals import outcomes
from app.signals.service import analyze

log = logging.getLogger("parallax.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self.pending: set[tuple[int, str]] = set()
        self.feeds = FeedManager(self.on_closed)

    async def on_closed(self, asset: Asset, tf: str, candles: list[CandleDTO]) -> None:
        last = candles[-1]
        async with session_factory()() as s:
            a = await s.get(Asset, asset.id)
            if a is not None:
                await check_protective_orders(s, a, min(c.low for c in candles), max(c.high for c in candles), last.close)
        if tf in self.settings.analysis_tf_list:
            key = (asset.id, tf)
            if key not in self.pending:
                self.pending.add(key)
                # small debounce so simultaneous higher-timeframe closes are stored before analysis
                asyncio.get_running_loop().call_later(4.0, self.queue.put_nowait, key)

    async def analysis_consumer(self) -> None:
        while True:
            asset_id, tf = await self.queue.get()
            self.pending.discard((asset_id, tf))
            try:
                async with session_factory()() as s:
                    asset = await s.get(Asset, asset_id)
                    if asset is None or not asset.is_active:
                        continue
                    ev, sig = await analyze(s, asset, tf)
                    if sig is not None and sig.status == "ACTIONABLE":
                        await system_log("INFO", "signal-engine", f"{asset.symbol} {tf} {sig.direction} score {sig.score:.0f}",
                                         {"signal_id": sig.id})
                        await dispatch(s, sig)
            except Exception as exc:  # noqa: BLE001
                log.exception("analysis failed for %s %s", asset_id, tf)
                await system_log("ERROR", "signal-engine", f"analysis failed for asset {asset_id} {tf}: {exc}")

    async def outcome_loop(self) -> None:
        while True:
            try:
                async with session_factory()() as s:
                    n = await outcomes.update_all_open(s)
                    if n:
                        log.info("updated %d signal outcomes", n)
            except Exception as exc:  # noqa: BLE001
                await system_log("ERROR", "outcomes", f"outcome update failed: {exc}")
            await asyncio.sleep(60)

    async def backtest_loop(self) -> None:
        while True:
            job_id = None
            try:
                async with session_factory()() as s:
                    bt = (await s.execute(select(Backtest).where(Backtest.status == "queued").order_by(Backtest.id)
                                          .limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
                    if bt is not None:
                        job_id = bt.id
                        bt.status = "running"
                        await s.commit()
                        await bt_runner.execute(s, bt)
            except Exception as exc:  # noqa: BLE001
                log.exception("backtest failed")
                if job_id is not None:
                    async with session_factory()() as s:
                        bt = await s.get(Backtest, job_id)
                        if bt:
                            bt.status, bt.error, bt.finished_at = "failed", str(exc)[:2000], utcnow()
                            await s.commit()
            await asyncio.sleep(2 if job_id else 3)

    async def research_loop(self) -> None:
        while True:
            job_id = None
            try:
                async with session_factory()() as s:
                    job = (await s.execute(select(ResearchJob).where(ResearchJob.status == "queued").order_by(ResearchJob.id)
                                           .limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
                    if job is not None:
                        job_id = job.id
                        job.status = "running"
                        await s.commit()
                        result = await run_job(s, job)
                        job.status, job.result, job.finished_at = "done", _json_safe(result), utcnow()
                        await s.commit()
                        await system_log("INFO", "research", f"job {job.id} ({job.kind}) finished")
            except Exception as exc:  # noqa: BLE001
                log.exception("research job failed")
                if job_id is not None:
                    async with session_factory()() as s:
                        job = await s.get(ResearchJob, job_id)
                        if job:
                            job.status, job.error, job.finished_at = "failed", str(exc)[:2000], utcnow()
                            await s.commit()
            await asyncio.sleep(5)

    async def heartbeat(self) -> None:
        while True:
            await bus.set_json("worker:heartbeat", {"id": WORKER_ID, "ts": datetime.now(timezone.utc).isoformat(),
                                                    "queue": self.queue.qsize()}, ex=60)
            await asyncio.sleep(10)

    async def acquire_feed_lock(self) -> bool:
        if bus.kind != "redis":
            return True
        r = bus.backend.r  # type: ignore[attr-defined]
        while True:
            if await r.set("worker:feed-lock", WORKER_ID, nx=True, ex=30):
                self._lock_task = asyncio.create_task(self._renew_lock())
                return True
            await asyncio.sleep(10)  # another worker owns feeds; stand by as a hot spare

    async def _renew_lock(self) -> None:
        r = bus.backend.r  # type: ignore[attr-defined]
        while True:
            await asyncio.sleep(10)
            if await r.get("worker:feed-lock") == WORKER_ID:
                await r.expire("worker:feed-lock", 30)
            else:
                log.error("lost feed lock; exiting so the supervisor restarts us")
                os._exit(1)

    async def run(self) -> None:
        init_engine()
        await bus.connect()
        await ensure_seed()
        await system_log("INFO", "worker", f"worker {WORKER_ID} started (bus={bus.kind})")
        jobs = [self.heartbeat(), self.backtest_loop(), self.research_loop(), self.outcome_loop()]
        try:
            if await self.acquire_feed_lock():
                jobs += [self.feeds.run(), self.analysis_consumer()]
            await asyncio.gather(*jobs)
        finally:
            lock_task = getattr(self, "_lock_task", None)
            if lock_task is not None:
                lock_task.cancel()
                if bus.kind == "redis":
                    r = bus.backend.r  # type: ignore[attr-defined]
                    if await r.get("worker:feed-lock") == WORKER_ID:
                        await r.delete("worker:feed-lock")


def _json_safe(obj):
    from app.backtest.runner import _clean

    return _clean(obj)


def main() -> None:
    setup_logging()
    w = Worker()
    loop = asyncio.new_event_loop()
    task = loop.create_task(w.run())
    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    main()
