"""Signal outcome tracking: every ACTIONABLE signal is followed on subsequent *closed* candles with
the same simulation used by the backtester, so live performance statistics are auditable."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.tradesim import simulate
from app.data.store import load_frame
from app.models import Signal, SignalOutcome, utcnow

log = logging.getLogger(__name__)


async def update_outcome(session: AsyncSession, sig: Signal, now: datetime | None = None) -> SignalOutcome | None:
    out = sig.outcome
    if out is None or out.status not in ("PENDING", "OPEN"):
        return out
    sim_p = (sig.levels_meta or {}).get("sim", {})
    expiry = int(sim_p.get("expiry_bars", 4))
    max_hold = int(sim_p.get("max_hold_bars", 48))
    now = now or datetime.now(timezone.utc)
    df = await load_frame(session, sig.asset_id, sig.timeframe, start=sig.bar_time, closed_by=now)
    # bars strictly after the signal bar: the signal bar's open time is bar_time - tf, so open_time >= bar_time
    df = df[df.index >= sig.bar_time]
    df = df.head(expiry + max_hold + 2)
    if df.empty:
        return out
    d = 1 if sig.direction == "BUY" else -1
    res = simulate(
        d, sig.entry_ref, sig.stop_loss, sig.tp1, sig.tp2,
        df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(),
        expiry_bars=expiry, max_hold_bars=max_hold,
        tp1_fraction=float(sim_p.get("tp1_fraction", 0.5)),
        move_stop_to_breakeven=bool(sim_p.get("move_stop_to_breakeven", True)),
    )
    idx = df.index
    out.status = res.status
    out.filled_at = idx[res.fill_idx].to_pydatetime() if res.fill_idx is not None else None
    out.fill_price = res.fill_price
    out.exit_at = idx[res.exit_idx].to_pydatetime() if res.exit_idx is not None else None
    out.exit_price = res.exit_price
    out.r_multiple = res.r_multiple
    out.mfe_r, out.mae_r = res.mfe_r, res.mae_r
    out.detail = {"legs": [{"fraction": f, "price": p, "reason": r} for f, p, r in res.legs], "note": res.note,
                  "bars_evaluated": len(df), "basis": "closed candles, conservative intrabar ordering, gross of fees"}
    out.updated_at = utcnow()
    return out


async def update_all_open(session: AsyncSession, limit: int = 500) -> int:
    rows = (
        await session.execute(
            select(Signal)
            .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
            .where(SignalOutcome.status.in_(["PENDING", "OPEN"]))
            .order_by(Signal.bar_time.asc())
            .limit(limit)
        )
    ).unique().scalars().all()
    changed = 0
    for sig in rows:
        before = sig.outcome.status
        try:
            await update_outcome(session, sig)
        except Exception as exc:  # noqa: BLE001
            log.exception("outcome update failed for signal %s: %s", sig.id, exc)
            continue
        changed += int(sig.outcome.status != before)
    await session.commit()
    return changed
