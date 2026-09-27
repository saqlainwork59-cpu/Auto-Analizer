"""Research jobs executed by the worker (CPU-heavy parts run in a thread)."""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import StrategyParams
from app.config import get_settings
from app.core.timeframes import ORDER
from app.data.store import load_frame
from app.ml import pipeline, registry, weights
from app.ml.dataset import assemble, build_rows
from app.models import Asset, ResearchJob
from app.signals import strategy as strat


async def _dataset(session: AsyncSession, asset_ids: list[int] | None, tf: str, params: StrategyParams):
    q = select(Asset)
    if asset_ids:
        q = q.where(Asset.id.in_(asset_ids))
    assets = (await session.execute(q)).scalars().all()  # includes inactive/delisted: survivorship control
    rows = []
    tfs = sorted(set(get_settings().context_tf_list) | {tf}, key=ORDER.index)
    for a in assets:
        dfs = {k: await load_frame(session, a.id, k) for k in tfs}
        if len(dfs.get(tf, [])) < params.min_bars + 100:
            continue
        rows.extend(await asyncio.to_thread(build_rows, dfs, tf, params, a.symbol))
    return assemble(rows)


async def run_job(session: AsyncSession, job: ResearchJob) -> dict:
    p = job.params or {}
    tf = p.get("timeframe", "1h")
    if tf not in ORDER:
        raise ValueError("invalid timeframe")
    _, params = await strat.get_active(session)
    ds = await _dataset(session, p.get("asset_ids"), tf, params)
    if job.kind == "ml_train":
        res = await asyncio.to_thread(pipeline.train, ds, p.get("model", "hgb"), int(p.get("folds", 5)))
        row = await registry.register(session, res, {"timeframe": tf, **p, "strategy_params": params.model_dump()},
                                      notes=p.get("notes"))
        return {"model_version_id": row.id, "status": row.status, "passes_gate": res.passes_gate,
                "holdout": res.metrics["holdout"], "overfit": res.overfit}
    if job.kind == "optimize_weights":
        out = await asyncio.to_thread(weights.optimize, ds, params, int(p.get("samples", 400)))
        new_params = params.model_copy(update={"weights": out["proposed_weights"]})
        row = await strat.create_version(session, new_params, job.created_by,
                                         notes=f"Weight search on {tf}; holdout passes={out['passes']}",
                                         status="candidate" if out["passes"] else "draft", validation=out)
        return {"strategy_id": row.id, "version": row.version, **out}
    raise ValueError(f"unknown job kind {job.kind}")
