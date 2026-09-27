"""Paper (simulated) trading on live prices. Entirely separate from any real-money path:
there is no code path from here to a broker adapter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.metrics import profit_factor, streaks
from app.core import flags
from app.core.bus import CH_PAPER, bus
from app.models import Asset, PaperAccount, PaperTrade, Signal, utcnow

MAX_GROSS_LEVERAGE = 2.0
PRICE_MAX_AGE = {"24x7": 120, "fx": 300, "equity": 300}


class PaperError(ValueError):
    pass


@dataclass
class LivePrice:
    price: float
    ts: datetime
    source: str


async def live_price(asset: Asset) -> LivePrice | None:
    """Latest streamed/polled price from the feed. None if missing or stale - never a guessed value."""
    data = await bus.get_json(f"px:last:{asset.id}")
    if not data:
        return None
    ts = datetime.fromisoformat(data["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > PRICE_MAX_AGE.get(asset.calendar, 300):
        return None
    return LivePrice(float(data["price"]), ts, data.get("source", "feed"))


async def get_account(session: AsyncSession, user_id: int) -> PaperAccount:
    acc = (await session.execute(select(PaperAccount).where(PaperAccount.user_id == user_id))).scalar_one_or_none()
    if acc is None:
        acc = PaperAccount(user_id=user_id, starting_balance=10_000.0, cash_balance=10_000.0, peak_equity=10_000.0)
        session.add(acc)
        await session.commit()
        await session.refresh(acc)
    return acc


async def open_trades(session: AsyncSession, account_id: int) -> list[PaperTrade]:
    return list((await session.execute(
        select(PaperTrade).where(PaperTrade.account_id == account_id, PaperTrade.status == "OPEN").order_by(PaperTrade.opened_at)
    )).unique().scalars())


def _pnl(t: PaperTrade, price: float) -> float:
    d = 1 if t.direction == "BUY" else -1
    return d * t.quantity * (price - t.entry_price)


async def valuation(session: AsyncSession, acc: PaperAccount) -> dict:
    trades = await open_trades(session, acc.id)
    unreal = 0.0
    priced_all = True
    positions = []
    for t in trades:
        lp = await live_price(t.asset)
        if lp is None:
            priced_all = False
        u = _pnl(t, lp.price) if lp else None
        unreal += u or 0.0
        positions.append({"trade": t, "price": lp.price if lp else None, "unrealized": u})
    equity = acc.cash_balance + unreal
    if priced_all:
        if equity > acc.peak_equity:
            acc.peak_equity = equity
        dd = (acc.peak_equity - equity) / acc.peak_equity * 100 if acc.peak_equity > 0 else 0.0
        acc.max_drawdown_pct = max(acc.max_drawdown_pct, dd)
        await session.commit()
    else:
        dd = None
    return {"equity": equity if priced_all else None, "equity_partial": equity, "unrealized": unreal,
            "priced_all": priced_all, "current_drawdown_pct": dd, "positions": positions}


async def open_trade(session: AsyncSession, user_id: int, asset: Asset, direction: str, *, quantity: float | None,
                     risk_pct: float | None, stop_loss: float | None, take_profit: float | None,
                     signal_id: int | None, notes: str | None) -> PaperTrade:
    if not await flags.get_flag("paper_trading_enabled", session):
        raise PaperError("Paper trading is temporarily disabled by an administrator")
    if direction not in ("BUY", "SELL"):
        raise PaperError("direction must be BUY or SELL")
    lp = await live_price(asset)
    if lp is None:
        raise PaperError("Live price unavailable for this market - cannot open a simulated trade")
    d = 1 if direction == "BUY" else -1
    price = lp.price
    if stop_loss is not None and d * (price - stop_loss) <= 0:
        raise PaperError("stop-loss must be below the entry for BUY and above it for SELL")
    if take_profit is not None and d * (take_profit - price) <= 0:
        raise PaperError("take-profit must be above the entry for BUY and below it for SELL")
    acc = await get_account(session, user_id)
    val = await valuation(session, acc)
    equity = val["equity_partial"]
    if quantity is None:
        if risk_pct is None or stop_loss is None:
            raise PaperError("provide a quantity, or a risk % together with a stop-loss")
        if not (0 < risk_pct <= 5):
            raise PaperError("risk per trade must be between 0 and 5%")
        quantity = equity * risk_pct / 100 / abs(price - stop_loss)
    if quantity <= 0:
        raise PaperError("quantity must be positive")
    gross = sum(t.quantity * t.entry_price for t in await open_trades(session, acc.id)) + quantity * price
    if gross > equity * MAX_GROSS_LEVERAGE:
        raise PaperError(f"position would exceed {MAX_GROSS_LEVERAGE:.0f}x account equity")
    if signal_id is not None:
        sig = await session.get(Signal, signal_id)
        if sig is None or sig.asset_id != asset.id:
            raise PaperError("signal does not belong to this market")
    fee = acc.fee_rate * quantity * price
    acc.cash_balance -= fee
    acc.fees_paid += fee
    trade = PaperTrade(account_id=acc.id, user_id=user_id, asset_id=asset.id, signal_id=signal_id, direction=direction,
                       quantity=quantity, entry_price=price, stop_loss=stop_loss, take_profit=take_profit, fees=fee,
                       price_source=f"{lp.source} @ {lp.ts.isoformat()}", notes=notes)
    session.add(trade)
    await session.commit()
    await session.refresh(trade)
    await bus.publish(CH_PAPER, {"type": "paper", "user_id": user_id, "event": "opened", "trade_id": trade.id})
    return trade


async def _close(session: AsyncSession, acc: PaperAccount, t: PaperTrade, price: float, reason: str) -> PaperTrade:
    fee = acc.fee_rate * t.quantity * price
    pnl = _pnl(t, price) - fee - t.fees
    acc.cash_balance += _pnl(t, price) - fee
    acc.realized_pnl += pnl
    acc.fees_paid += fee
    t.status, t.closed_at, t.exit_price, t.close_reason = "CLOSED", utcnow(), price, reason
    t.fees += fee
    t.pnl = pnl
    return t


async def close_trade(session: AsyncSession, user_id: int, trade_id: int) -> PaperTrade:
    t = await session.get(PaperTrade, trade_id)
    if t is None or t.user_id != user_id:  # server-side ownership check
        raise PaperError("trade not found")
    if t.status != "OPEN":
        raise PaperError("trade already closed")
    lp = await live_price(t.asset)
    if lp is None:
        raise PaperError("Live price unavailable - cannot close at a verified price right now")
    acc = await get_account(session, user_id)
    await _close(session, acc, t, lp.price, "MANUAL")
    await session.commit()
    await valuation(session, acc)
    await bus.publish(CH_PAPER, {"type": "paper", "user_id": user_id, "event": "closed", "trade_id": t.id})
    return t


async def reset_account(session: AsyncSession, user_id: int, balance: float) -> PaperAccount:
    if not (100 <= balance <= 100_000_000):
        raise PaperError("balance must be between 100 and 100,000,000")
    acc = await get_account(session, user_id)
    for t in await open_trades(session, acc.id):
        t.status, t.closed_at, t.close_reason, t.pnl = "CLOSED", utcnow(), "RESET", 0.0
    acc.starting_balance = acc.cash_balance = acc.peak_equity = balance
    acc.realized_pnl = acc.fees_paid = acc.max_drawdown_pct = 0.0
    acc.reset_at = utcnow()
    await session.commit()
    return acc


async def check_protective_orders(session: AsyncSession, asset: Asset, low: float, high: float, last: float) -> int:
    """Close open paper trades whose stop / target was touched. Stop checked first (conservative)."""
    rows = list((await session.execute(
        select(PaperTrade).where(PaperTrade.asset_id == asset.id, PaperTrade.status == "OPEN")
    )).unique().scalars())
    closed = 0
    for t in rows:
        d = 1 if t.direction == "BUY" else -1
        acc = await session.get(PaperAccount, t.account_id)
        adverse = low if d > 0 else high
        favourable = high if d > 0 else low
        if t.stop_loss is not None and d * (adverse - t.stop_loss) <= 0:
            await _close(session, acc, t, t.stop_loss, "STOP")
        elif t.take_profit is not None and d * (favourable - t.take_profit) >= 0:
            await _close(session, acc, t, t.take_profit, "TARGET")
        else:
            continue
        closed += 1
        await bus.publish(CH_PAPER, {"type": "paper", "user_id": t.user_id, "event": "closed", "trade_id": t.id})
    if closed:
        await session.commit()
    return closed


async def statistics(session: AsyncSession, acc: PaperAccount) -> dict:
    closed = list((await session.execute(
        select(PaperTrade).where(PaperTrade.account_id == acc.id, PaperTrade.status == "CLOSED",
                                 PaperTrade.close_reason != "RESET").order_by(PaperTrade.closed_at)
    )).unique().scalars())
    pnls = [t.pnl or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    pf = profit_factor(pnls)
    _, l_streak = streaks(pnls)
    by_signal = [t for t in closed if t.signal_id]
    return {
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": sum(1 for p in pnls if p < 0),
        "win_rate": len(wins) / len(closed) if closed else None,
        "realized_pnl": acc.realized_pnl,
        "profit_factor": None if pf is None else (None if pf == float("inf") else pf),
        "longest_losing_streak": l_streak,
        "signal_linked_trades": len(by_signal),
        "signal_linked_pnl": sum(t.pnl or 0 for t in by_signal),
        "manual_trades_pnl": sum(t.pnl or 0 for t in closed if not t.signal_id),
    }
