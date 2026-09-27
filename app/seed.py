"""Idempotent bootstrap: default markets, default strategy, feed rows."""
from __future__ import annotations

from sqlalchemy import select

from app.data.catalog import DEFAULT_ASSETS
from app.db import session_factory
from app.models import Asset
from app.signals.strategy import get_active


async def ensure_seed() -> None:
    async with session_factory()() as s:
        existing = {a.symbol for a in (await s.execute(select(Asset))).scalars()}
        for spec in DEFAULT_ASSETS:
            if spec["symbol"] not in existing:
                s.add(Asset(**{**spec, "meta": spec.get("meta", {})}))
        await s.commit()
        await get_active(s)
