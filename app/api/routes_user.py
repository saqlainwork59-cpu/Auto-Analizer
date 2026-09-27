"""Watchlist, backtests, paper trading, alerts, credentials and broker endpoints (all user-scoped)."""
from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import StrategyParams
from app.brokers.base import ExecutionDisabled, execution_gate
from app.config import get_settings
from app.core import flags
from app.core.audit import audit
from app.db import get_session
from app.models import (
    AlertDelivery,
    AlertRule,
    ApiCredential,
    Asset,
    Backtest,
    BacktestTrade,
    PaperTrade,
    Strategy,
    User,
    WatchlistItem,
)
from app.paper import service as paper
from app.schemas import (
    AlertIn,
    AlertPatch,
    BacktestIn,
    BrokerOrderIn,
    CredentialIn,
    PaperOpenIn,
    PaperResetIn,
    WatchlistAddIn,
    WatchlistOrderIn,
)
from app.security.crypto import decrypt, encrypt, hint
from app.security.deps import current_user
from app.security.passwords import verify_password
from app.security.ratelimit import client_ip, limiter
from app.signals.strategy import get_active, version_label

router = APIRouter(prefix="/api", tags=["user"])


# ------------------------------------------------------------------------------------------ watchlist
@router.get("/watchlist")
async def get_watchlist(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(WatchlistItem).where(WatchlistItem.user_id == user.id)
                                  .order_by(WatchlistItem.position, WatchlistItem.created_at))).unique().scalars().all()
    return [{"asset_id": r.asset_id, "symbol": r.asset.symbol, "name": r.asset.name, "asset_class": r.asset.asset_class,
             "position": r.position} for r in rows]


@router.post("/watchlist", status_code=201)
async def add_watchlist(body: WatchlistAddIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if await session.get(Asset, body.asset_id) is None:
        raise HTTPException(404, detail="Market not found")
    if await session.get(WatchlistItem, (user.id, body.asset_id)) is None:
        n = (await session.execute(select(func.count()).where(WatchlistItem.user_id == user.id))).scalar() or 0
        if n >= 200:
            raise HTTPException(422, detail="Watchlist limit reached (200)")
        session.add(WatchlistItem(user_id=user.id, asset_id=body.asset_id, position=n))
        await session.commit()
    return {"ok": True}


@router.delete("/watchlist/{asset_id}")
async def remove_watchlist(asset_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await session.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.asset_id == asset_id))
    await session.commit()
    return {"ok": True}


@router.put("/watchlist/order")
async def order_watchlist(body: WatchlistOrderIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    for pos, aid in enumerate(body.asset_ids):
        item = await session.get(WatchlistItem, (user.id, aid))
        if item:
            item.position = pos
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------ backtests
def bt_out(b: Backtest, full: bool = False) -> dict:
    d = {"id": b.id, "status": b.status, "params": b.params, "progress": b.progress, "created_at": b.created_at,
         "finished_at": b.finished_at, "error": b.error,
         "summary": {k: (b.metrics or {}).get(k) for k in ("total_trades", "win_rate", "net_return_pct", "max_drawdown_pct",
                                                            "profit_factor")} if b.metrics else None}
    if full:
        d.update(metrics=b.metrics, equity_curve=b.equity_curve, data_summary=b.data_summary,
                 strategy=b.strategy_snapshot)
    return d


@router.post("/backtests", status_code=202, dependencies=[Depends(limiter("backtest", 6))])
async def create_backtest(body: BacktestIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    asset = await session.get(Asset, body.asset_id)
    if asset is None:
        raise HTTPException(404, detail="Market not found")
    running = (await session.execute(select(func.count()).where(Backtest.user_id == user.id,
                                                                Backtest.status.in_(["queued", "running"])))).scalar() or 0
    if running >= 2:
        raise HTTPException(429, detail="You already have 2 backtests queued or running")
    if body.strategy_id:
        row = await session.get(Strategy, body.strategy_id)
        if row is None:
            raise HTTPException(404, detail="Strategy not found")
        params = StrategyParams.model_validate(row.params)
        label = version_label(row)
    else:
        row, params = await get_active(session)
        label = version_label(row)
    upd = {}
    if body.setups:
        upd["allow_setups"] = list(body.setups)
    if body.min_score is not None:
        upd["min_score"] = body.min_score
    if body.min_rr is not None:
        upd["min_rr"] = body.min_rr
    params = StrategyParams.model_validate({**params.model_dump(), **upd})
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
    end = body.end if body.end.tzinfo else body.end.replace(tzinfo=timezone.utc)
    bt = Backtest(user_id=user.id, status="queued",
                  params={"asset_id": asset.id, "symbol": asset.symbol, "timeframe": body.timeframe,
                          "start": start.isoformat(), "end": end.isoformat(), "strategy": label,
                          "starting_capital": body.starting_capital, "risk_per_trade_pct": body.risk_per_trade_pct,
                          "fee_pct": body.fee_pct, "slippage_pct": body.slippage_pct, "max_leverage": body.max_leverage,
                          "overrides": upd},
                  strategy_snapshot=params.model_dump())
    session.add(bt)
    await session.commit()
    await session.refresh(bt)
    return bt_out(bt)


@router.get("/backtests")
async def list_backtests(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Backtest).where(Backtest.user_id == user.id).order_by(Backtest.id.desc()).limit(50))).scalars()
    return [bt_out(b) for b in rows]


@router.get("/backtests/{bt_id}")
async def get_backtest(bt_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    b = await session.get(Backtest, bt_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, detail="Backtest not found")
    trades = (await session.execute(select(BacktestTrade).where(BacktestTrade.backtest_id == b.id).order_by(BacktestTrade.id))).scalars()
    d = bt_out(b, full=True)
    d["trades"] = [{c.name: getattr(t, c.name) for c in BacktestTrade.__table__.columns} for t in trades]
    return d


@router.delete("/backtests/{bt_id}")
async def delete_backtest(bt_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    b = await session.get(Backtest, bt_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, detail="Backtest not found")
    if b.status == "running":
        raise HTTPException(409, detail="Backtest is running")
    await session.delete(b)
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------ paper trading
def trade_out(t: PaperTrade, price: float | None = None, unreal: float | None = None) -> dict:
    return {"id": t.id, "asset": {"id": t.asset.id, "symbol": t.asset.symbol, "price_precision": t.asset.price_precision},
            "signal_id": t.signal_id, "direction": t.direction, "quantity": t.quantity, "entry_price": t.entry_price,
            "stop_loss": t.stop_loss, "take_profit": t.take_profit, "status": t.status, "opened_at": t.opened_at,
            "closed_at": t.closed_at, "exit_price": t.exit_price, "close_reason": t.close_reason, "pnl": t.pnl,
            "fees": t.fees, "price_source": t.price_source, "notes": t.notes, "current_price": price,
            "unrealized_pnl": unreal}


@router.get("/paper")
async def paper_account(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    acc = await paper.get_account(session, user.id)
    val = await paper.valuation(session, acc)
    stats = await paper.statistics(session, acc)
    return {
        "account": {"starting_balance": acc.starting_balance, "cash_balance": acc.cash_balance,
                    "realized_pnl": acc.realized_pnl, "fees_paid": acc.fees_paid, "peak_equity": acc.peak_equity,
                    "max_drawdown_pct": acc.max_drawdown_pct, "fee_rate": acc.fee_rate, "reset_at": acc.reset_at},
        "equity": val["equity"],
        "equity_partial": val["equity_partial"],
        "priced_all": val["priced_all"],
        "unrealized_pnl": val["unrealized"],
        "current_drawdown_pct": val["current_drawdown_pct"],
        "positions": [trade_out(p["trade"], p["price"], p["unrealized"]) for p in val["positions"]],
        "stats": stats,
        "enabled": await flags.get_flag("paper_trading_enabled", session),
        "disclaimer": "Simulated trading with virtual funds. Fills use the latest verified feed price and do not "
                      "model order-book depth.",
    }


@router.get("/paper/trades")
async def paper_trades(status: str | None = Query(None, pattern="^(OPEN|CLOSED)$"), limit: int = Query(100, le=500),
                       user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    q = select(PaperTrade).where(PaperTrade.user_id == user.id)
    if status:
        q = q.where(PaperTrade.status == status)
    rows = (await session.execute(q.order_by(PaperTrade.opened_at.desc()).limit(limit))).unique().scalars()
    return [trade_out(t) for t in rows]


@router.post("/paper/trades", status_code=201, dependencies=[Depends(limiter("paper", 30))])
async def paper_open(body: PaperOpenIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    asset = await session.get(Asset, body.asset_id)
    if asset is None or not asset.is_active:
        raise HTTPException(404, detail="Market not found")
    try:
        t = await paper.open_trade(session, user.id, asset, body.direction, quantity=body.quantity, risk_pct=body.risk_pct,
                                   stop_loss=body.stop_loss, take_profit=body.take_profit, signal_id=body.signal_id,
                                   notes=body.notes)
    except paper.PaperError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return trade_out(t)


@router.post("/paper/trades/{trade_id}/close")
async def paper_close(trade_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    try:
        t = await paper.close_trade(session, user.id, trade_id)
    except paper.PaperError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return trade_out(t)


@router.post("/paper/reset")
async def paper_reset(body: PaperResetIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    try:
        acc = await paper.reset_account(session, user.id, body.balance)
    except paper.PaperError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    await audit(session, "paper.reset", user_id=user.id, detail={"balance": body.balance})
    return {"ok": True, "balance": acc.cash_balance}


# ------------------------------------------------------------------------------------------ alerts
def alert_out(a: AlertRule) -> dict:
    return {"id": a.id, "channel": a.channel, "destination_hint": a.destination_hint, "is_enabled": a.is_enabled,
            "min_score": a.min_score, "asset_ids": a.asset_ids, "timeframes": a.timeframes, "created_at": a.created_at}


@router.get("/alerts")
async def list_alerts(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id))).scalars()
    s = get_settings()
    return {"rules": [alert_out(a) for a in rows],
            "channels": {"telegram": bool(s.telegram_bot_token), "email": bool(s.smtp_host and s.smtp_from), "browser": True}}


@router.post("/alerts", status_code=201)
async def create_alert(body: AlertIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if body.channel in ("telegram", "email") and not body.destination:
        raise HTTPException(422, detail="destination is required for Telegram (chat id) and email alerts")
    if body.channel == "email" and body.destination and "@" not in body.destination:
        raise HTTPException(422, detail="invalid email address")
    if body.channel == "telegram" and body.destination and not body.destination.lstrip("-").isdigit():
        raise HTTPException(422, detail="Telegram chat id must be numeric")
    n = (await session.execute(select(func.count()).where(AlertRule.user_id == user.id))).scalar() or 0
    if n >= 20:
        raise HTTPException(422, detail="Alert limit reached (20)")
    rule = AlertRule(user_id=user.id, channel=body.channel, is_enabled=body.is_enabled, min_score=body.min_score,
                     asset_ids=body.asset_ids, timeframes=body.timeframes,
                     destination_encrypted=encrypt(body.destination) if body.destination else None,
                     destination_hint=hint(body.destination) if body.destination else None)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return alert_out(rule)


@router.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: int, body: AlertPatch, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rule = await session.get(AlertRule, alert_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404, detail="Alert not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(rule, k, v)
    await session.commit()
    return alert_out(rule)


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rule = await session.get(AlertRule, alert_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404, detail="Alert not found")
    await session.delete(rule)
    await session.commit()
    return {"ok": True}


@router.post("/alerts/{alert_id}/test", dependencies=[Depends(limiter("alert-test", 5))])
async def test_alert(alert_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    from app.alerts.dispatcher import send_email, send_telegram
    from app.core.bus import CH_SIGNALS, bus

    rule = await session.get(AlertRule, alert_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404, detail="Alert not found")
    text = "Parallax test alert - your alert channel is working. (No trade setup is implied by this message.)"
    try:
        if rule.channel == "telegram":
            await send_telegram(decrypt(rule.destination_encrypted or ""), text)
        elif rule.channel == "email":
            await send_email(decrypt(rule.destination_encrypted or ""), "Parallax test alert", text)
        else:
            await bus.publish(CH_SIGNALS, {"type": "notify", "user_id": user.id, "title": "Parallax test alert", "body": text})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail=f"Delivery failed: {exc}") from exc
    return {"ok": True}


@router.get("/alerts/deliveries")
async def deliveries(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AlertDelivery).join(AlertRule, AlertRule.id == AlertDelivery.alert_id)
                                  .where(AlertRule.user_id == user.id).order_by(AlertDelivery.id.desc()).limit(100))).scalars()
    return [{"id": d.id, "alert_id": d.alert_id, "signal_id": d.signal_id, "status": d.status, "error": d.error,
             "created_at": d.created_at} for d in rows]


# ------------------------------------------------------------------------------------------ credentials & broker
@router.get("/credentials")
async def list_credentials(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiCredential).where(ApiCredential.user_id == user.id))).scalars()
    return [{"id": c.id, "provider": c.provider, "label": c.label, "key_hint": c.key_hint, "created_at": c.created_at}
            for c in rows]


@router.post("/credentials", status_code=201, dependencies=[Depends(limiter("cred", 10))])
async def add_credential(body: CredentialIn, request: Request, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    c = ApiCredential(user_id=user.id, provider=body.provider, label=body.label, encrypted_key=encrypt(body.api_key),
                      encrypted_secret=encrypt(body.api_secret) if body.api_secret else None, key_hint=hint(body.api_key))
    session.add(c)
    await session.commit()
    await audit(session, "credential.add", user_id=user.id, target=body.provider, ip=client_ip(request))
    return {"id": c.id, "provider": c.provider, "label": c.label, "key_hint": c.key_hint}


@router.delete("/credentials/{cred_id}")
async def delete_credential(cred_id: int, request: Request, user: User = Depends(current_user),
                            session: AsyncSession = Depends(get_session)):
    c = await session.get(ApiCredential, cred_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, detail="Credential not found")
    await session.delete(c)
    await session.commit()
    await audit(session, "credential.delete", user_id=user.id, target=c.provider, ip=client_ip(request))
    return {"ok": True}


@router.get("/broker/status")
async def broker_status(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return {
        "env_enabled": get_settings().broker_execution_enabled,
        "admin_enabled": await flags.get_flag("broker_execution_enabled", session),
        "adapters": [],
        "message": "Real-money execution is disabled. Signals never place orders automatically.",
    }


@router.post("/broker/orders", dependencies=[Depends(limiter("broker", 5))])
async def broker_order(body: BrokerOrderIn, request: Request, user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user.id)
    reauth = verify_password(body.password, u.password_hash)
    try:
        await execution_gate(
            env_enabled=get_settings().broker_execution_enabled,
            admin_enabled=await flags.get_flag("broker_execution_enabled", session),
            user_opted_in=bool((u.settings or {}).get("live_execution_opt_in")),
            reauthenticated=reauth,
            confirmation_valid=False,  # confirmation tokens are only issued once a broker adapter is enabled
        )
    except ExecutionDisabled as exc:
        await audit(session, "broker.order_blocked", user_id=user.id, ip=client_ip(request),
                    detail={"symbol": body.symbol, "side": body.side, "reason": str(exc)})
        raise HTTPException(403, detail=str(exc)) from exc
    raise HTTPException(501, detail="No broker adapter is enabled")
