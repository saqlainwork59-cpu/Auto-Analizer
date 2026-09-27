"""Score-weight research. Weights are *searched on the development period only* and then checked
once on an untouched holdout against the currently active weights. The output is a candidate
strategy version; an admin must activate it, and activation is refused if it did not validate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.params import COMPONENTS, StrategyParams
from app.ml.dataset import Dataset
from app.ml.pipeline import holdout_split


def scores_for(df: pd.DataFrame, weights: dict[str, float]) -> np.ndarray:
    comp = np.stack([df.get(f"comp_{c}", pd.Series(np.nan, index=df.index)).to_numpy(dtype=float) for c in COMPONENTS], axis=1)
    w = np.array([weights.get(c, 0.0) for c in COMPONENTS])
    avail = np.isfinite(comp)
    num = np.nansum(np.where(avail, comp * w, 0.0), axis=1)
    den = np.sum(np.where(avail, w, 0.0), axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, 100 * num / den, 0.0)


def evaluate_weights(df: pd.DataFrame, weights: dict[str, float], p: StrategyParams) -> dict:
    s = scores_for(df, weights)
    opp = df.get("mtf_opposition", pd.Series(0.0, index=df.index)).fillna(0.0).to_numpy()
    take = (s >= p.min_score) & (opp < p.mtf_conflict_threshold)
    r = df["r_multiple"].to_numpy(dtype=float)
    n = int(take.sum())
    if n == 0:
        return {"trades": 0, "expectancy_r": None, "win_rate": None, "objective": -np.inf}
    e = float(r[take].mean())
    sd = float(r[take].std(ddof=1)) if n > 1 else np.inf
    return {
        "trades": n,
        "expectancy_r": e,
        "win_rate": float((r[take] > 0).mean()),
        "total_r": float(r[take].sum()),
        # t-statistic of the mean R: rewards consistency, penalises tiny samples
        "objective": e / sd * np.sqrt(n) if sd > 0 and np.isfinite(sd) else -np.inf,
    }


def optimize(ds: Dataset, current: StrategyParams, n_samples: int = 400, holdout_frac: float = 0.25, seed: int = 0,
             min_trades: int = 30) -> dict:
    df = ds.frame
    ts, te = df["t_start"].to_numpy(), df["t_end"].to_numpy()
    dev, hold = holdout_split(ts, te, holdout_frac)
    d_dev, d_hold = df.iloc[dev], df.iloc[hold]
    rng = np.random.default_rng(seed)
    best = {"weights": dict(current.weights), **evaluate_weights(d_dev, current.weights, current)}
    for _ in range(n_samples):
        raw = rng.dirichlet(np.ones(len(COMPONENTS)))
        w = {c: round(float(x) * 100, 2) for c, x in zip(COMPONENTS, raw)}
        m = evaluate_weights(d_dev, w, current)
        if m["trades"] >= min_trades and m["objective"] > best["objective"]:
            best = {"weights": w, **m}
    cur_hold = evaluate_weights(d_hold, current.weights, current)
    new_hold = evaluate_weights(d_hold, best["weights"], current)
    passes = (
        new_hold["trades"] >= min_trades
        and new_hold["expectancy_r"] is not None
        and (cur_hold["expectancy_r"] is None or new_hold["expectancy_r"] > cur_hold["expectancy_r"])
        and new_hold["expectancy_r"] > 0
    )
    clean = lambda m: {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in m.items()}  # noqa: E731
    return {
        "proposed_weights": best["weights"],
        "development": clean({k: v for k, v in best.items() if k != "weights"}),
        "holdout_current": clean(cur_hold),
        "holdout_proposed": clean(new_hold),
        "passes": bool(passes),
        "dataset_sha256": ds.sha256,
        "rows": {"development": int(len(dev)), "holdout": int(len(hold))},
        "note": "Development-period search, single holdout check. Passing is necessary, not sufficient: "
                "review the sample size and regime mix before activating.",
    }
