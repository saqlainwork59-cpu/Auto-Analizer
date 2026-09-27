from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import StrategyParams
from app.config import get_settings
from app.core import flags
from app.core.audit import audit
from app.core.bus import CH_SYSTEM, bus
from app.data.providers.base import ProviderError
from app.data.providers.registry import all_provider_names, get_provider
from app.db import get_session
from app.ml import registry
from app.models import (
    Asset,
    AuditLog,
    Backtest,
    DataFeed,
    ModelVersion,
    PaperTrade,
    ResearchJob,
    Signal,
    Strategy,
    SystemLog,
    User,
)
from app.schemas import ActivateStrategyIn, AssetIn, AssetPatch, FlagIn, ResearchJobIn, StrategyDraftIn, UserPatch
from app.security.deps import require_admin
from app.security.ratelimit import client_ip
from app.signals import strategy as strat

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status")
async def status(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    day = now - timedelta(days=1)
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    hb = await bus.get_json("worker:heartbeat")
    worker_ok = bool(hb) and (now - datetime.fromisoformat(hb["ts"])).total_seconds() < 45
    feeds = (await session.execute(select(DataFeed).order_by(DataFeed.id))).scalars().all()
    sig_counts = dict((await session.execute(select(Signal.status, func.count()).where(Signal.created_at >= day)
                                             .group_by(Signal.status))).all())
    last_signal = (await session.execute(select(func.max(Signal.created_at)))).scalar()
    errors = (await session.execute(select(func.count()).where(SystemLog.level == "ERROR", SystemLog.created_at >= day))).scalar()
    users_total = (await session.execute(select(func.count(User.id)))).scalar()
    users_active = (await session.execute(select(func.count(User.id)).where(User.last_login_at >= now - timedelta(days=7)))).scalar()
    return {
        "time": now,
        "environment": get_settings().app_env,
        "database": "ok" if db_ok else "down",
        "bus": {"kind": bus.kind, "ok": await bus.healthy()},
        "worker": {"ok": worker_ok, "heartbeat": hb},
        "providers": {p: {"configured": get_provider(p).configured()} for p in all_provider_names()},
        "feeds": [{"id": f.id, "provider": f.provider, "status": f.status, "last_message_at": f.last_message_at,
                   "last_error": f.last_error, "updated_at": f.updated_at} for f in feeds],
        "flags": await flags.all_flags(session),
        "signals_24h": sig_counts,
        "last_signal_at": last_signal,
        "errors_24h": errors,
        "users": {"total": users_total, "active_7d": users_active},
        "open_paper_trades": (await session.execute(select(func.count()).where(PaperTrade.status == "OPEN"))).scalar(),
        "backtests_running": (await session.execute(select(func.count()).where(Backtest.status.in_(["queued", "running"])))).scalar(),
    }


@router.post("/flags/{key}")
async def set_flag(key: str, body: FlagIn, request: Request, admin: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    if key not in flags.FLAGS:
        raise HTTPException(404, detail="Unknown control")
    if key == "broker_execution_enabled" and body.enabled and not get_settings().broker_execution_enabled:
        raise HTTPException(409, detail="Broker execution is disabled at deployment level (BROKER_EXECUTION_ENABLED=false)")
    await flags.set_flag(session, key, body.enabled, admin.id, body.reason)
    await audit(session, "admin.flag", user_id=admin.id, target=key, ip=client_ip(request),
                detail={"enabled": body.enabled, "reason": body.reason})
    await bus.publish(CH_SYSTEM, {"type": "flag", "key": key, "enabled": body.enabled})
    return {"ok": True}


@router.get("/logs")
async def logs(level: str | None = Query(None, pattern="^(DEBUG|INFO|WARNING|ERROR)$"), limit: int = Query(200, le=1000),
               admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    q = select(SystemLog).order_by(SystemLog.id.desc()).limit(limit)
    if level:
        q = q.where(SystemLog.level == level)
    return [{"id": r.id, "created_at": r.created_at, "level": r.level, "source": r.source, "message": r.message,
             "context": r.context} for r in (await session.execute(q)).scalars()]


@router.get("/audit")
async def audit_log(limit: int = Query(200, le=1000), admin: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))).scalars()
    return [{"id": r.id, "created_at": r.created_at, "user_id": r.user_id, "action": r.action, "target": r.target,
             "ip": r.ip, "detail": r.detail} for r in rows]


@router.get("/users")
async def users(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(User).order_by(User.id))).scalars()
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
             "last_login_at": u.last_login_at} for u in rows]


@router.patch("/users/{user_id}")
async def patch_user(user_id: int, body: UserPatch, request: Request, admin: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(404, detail="User not found")
    if u.id == admin.id and (body.role == "user" or body.is_active is False):
        raise HTTPException(409, detail="You cannot demote or deactivate your own account")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    await session.commit()
    await audit(session, "admin.user_update", user_id=admin.id, target=str(user_id), ip=client_ip(request),
                detail=body.model_dump(exclude_unset=True))
    return {"ok": True}


# ------------------------------------------------------------------------------------------ markets
@router.get("/assets")
async def assets(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Asset).order_by(Asset.asset_class, Asset.symbol))).scalars()
    return [{"id": a.id, "symbol": a.symbol, "name": a.name, "asset_class": a.asset_class, "provider": a.provider,
             "provider_symbol": a.provider_symbol, "calendar": a.calendar, "is_active": a.is_active,
             "delisted_at": a.delisted_at} for a in rows]


@router.post("/assets", status_code=201)
async def add_asset(body: AssetIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(Asset).where(Asset.symbol == body.symbol))).scalar_one_or_none():
        raise HTTPException(409, detail="Symbol already exists")
    a = Asset(**body.model_dump(), meta={})
    session.add(a)
    await session.commit()
    await audit(session, "admin.asset_add", user_id=admin.id, target=body.symbol, ip=client_ip(request))
    return {"id": a.id, "note": "Restart the worker to start streaming the new market."}


@router.patch("/assets/{asset_id}")
async def patch_asset(asset_id: int, body: AssetPatch, request: Request, admin: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    a = await session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, detail="Market not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    await session.commit()
    await audit(session, "admin.asset_update", user_id=admin.id, target=a.symbol, ip=client_ip(request),
                detail={k: str(v) for k, v in body.model_dump(exclude_unset=True).items()})
    return {"ok": True}


@router.post("/providers/{name}/test")
async def test_provider(name: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if name not in all_provider_names():
        raise HTTPException(404, detail="Unknown provider")
    a = (await session.execute(select(Asset).where(Asset.provider == name).limit(1))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail="No market uses this provider")
    try:
        candles = await get_provider(name).fetch_candles(a.provider_symbol, "1h", limit=3)
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "symbol": a.symbol, "candles": len(candles),
            "latest_open_time": candles[-1].open_time if candles else None}


# ------------------------------------------------------------------------------------------ strategies & models
def strategy_out(s: Strategy) -> dict:
    return {"id": s.id, "name": s.name, "version": s.version, "status": s.status, "params": s.params,
            "validation": s.validation, "notes": s.notes, "created_at": s.created_at, "approved_at": s.approved_at}


@router.get("/strategies")
async def strategies(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await strat.get_active(session)
    rows = (await session.execute(select(Strategy).order_by(Strategy.id.desc()))).scalars()
    return [strategy_out(s) for s in rows]


@router.post("/strategies", status_code=201)
async def create_strategy(body: StrategyDraftIn, request: Request, admin: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)):
    try:
        params = StrategyParams.model_validate(body.params)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_url=False, include_context=False)) from exc
    row = await strat.create_version(session, params, admin.id, body.notes, status="draft")
    await audit(session, "admin.strategy_draft", user_id=admin.id, target=f"v{row.version}", ip=client_ip(request))
    return strategy_out(row)


@router.post("/strategies/{strategy_id}/activate")
async def activate_strategy(strategy_id: int, body: ActivateStrategyIn, request: Request,
                            admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    row = await session.get(Strategy, strategy_id)
    if row is None:
        raise HTTPException(404, detail="Strategy not found")
    validated = bool((row.validation or {}).get("passes"))
    if not validated and not body.acknowledge_unvalidated_reason:
        raise HTTPException(409, detail="This version has not passed out-of-sample validation. Run a backtest / weight "
                                        "study first, or provide an explicit reason to activate an unvalidated version.")
    await strat.activate(session, strategy_id, admin.id)
    await audit(session, "admin.strategy_activate", user_id=admin.id, target=f"v{row.version}", ip=client_ip(request),
                detail={"validated": validated, "reason": body.acknowledge_unvalidated_reason})
    return {"ok": True}


@router.get("/models")
async def models(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ModelVersion).order_by(ModelVersion.id.desc()))).scalars()
    return [{"id": m.id, "name": m.name, "version": m.version, "status": m.status, "algorithm": m.algorithm,
             "created_at": m.created_at, "approved_at": m.approved_at, "metrics": m.metrics, "overfit": m.overfit,
             "dataset": m.dataset, "feature_names": m.feature_names, "artifact_sha256": m.artifact_sha256} for m in rows]


@router.post("/models/{model_id}/promote")
async def promote(model_id: int, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    try:
        m = await registry.promote(session, model_id, admin.id)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    await audit(session, "admin.model_promote", user_id=admin.id, target=f"{m.name} v{m.version}", ip=client_ip(request))
    return {"ok": True, "note": "The model is used as an informational probability. It filters signals only if "
                                "`ml_filter.enabled` is set in the active strategy."}


@router.post("/models/{model_id}/retire")
async def retire(model_id: int, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await registry.retire(session, model_id)
    await audit(session, "admin.model_retire", user_id=admin.id, target=str(model_id), ip=client_ip(request))
    return {"ok": True}


@router.get("/jobs")
async def jobs(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ResearchJob).order_by(ResearchJob.id.desc()).limit(50))).scalars()
    return [{"id": j.id, "kind": j.kind, "status": j.status, "params": j.params, "result": j.result, "error": j.error,
             "created_at": j.created_at, "finished_at": j.finished_at} for j in rows]


@router.post("/jobs", status_code=202)
async def create_job(body: ResearchJobIn, request: Request, admin: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    job = ResearchJob(kind=body.kind, params=body.model_dump(exclude={"kind"}), created_by=admin.id)
    session.add(job)
    await session.commit()
    await audit(session, "admin.research_job", user_id=admin.id, target=body.kind, ip=client_ip(request))
    return {"id": job.id, "status": job.status}
