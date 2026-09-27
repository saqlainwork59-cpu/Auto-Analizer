from __future__ import annotations

from app.models import Asset, Signal, SignalOutcome


def outcome_to_dict(o: SignalOutcome | None) -> dict | None:
    if o is None:
        return None
    return {
        "status": o.status,
        "filled_at": o.filled_at,
        "fill_price": o.fill_price,
        "exit_at": o.exit_at,
        "exit_price": o.exit_price,
        "r_multiple": o.r_multiple,
        "mfe_r": o.mfe_r,
        "mae_r": o.mae_r,
        "detail": o.detail,
        "updated_at": o.updated_at,
    }


def signal_to_dict(s: Signal, asset: Asset | None = None, *, full: bool = True) -> dict:
    a = asset or s.asset
    d = {
        "id": s.id,
        "created_at": s.created_at,
        "bar_time": s.bar_time,
        "asset": {"id": a.id, "symbol": a.symbol, "name": a.name, "asset_class": a.asset_class,
                  "price_precision": a.price_precision} if a else None,
        "timeframe": s.timeframe,
        "status": s.status,
        "direction": s.direction,
        "setup_type": s.setup_type,
        "regime": s.regime,
        "score": s.score,
        "entry_low": s.entry_low,
        "entry_high": s.entry_high,
        "entry_ref": s.entry_ref,
        "stop_loss": s.stop_loss,
        "tp1": s.tp1,
        "tp2": s.tp2,
        "rr_tp1": s.rr_tp1,
        "rr_tp2": s.rr_tp2,
        "expires_at": s.expires_at,
        "headline": s.headline,
        "strategy_version": s.strategy_version,
        "engine_version": s.engine_version,
        "model_version_id": s.model_version_id,
        "ml_probability": s.ml_probability,
        "outcome": outcome_to_dict(s.__dict__.get("outcome")),
    }
    if full:
        d.update(
            explanation=s.explanation,
            reasons=s.reasons,
            risks=s.risks,
            invalidation=s.invalidation,
            components=s.components,
            mtf=s.mtf,
            levels_meta=s.levels_meta,
            data_quality=s.data_quality,
        )
    return d
