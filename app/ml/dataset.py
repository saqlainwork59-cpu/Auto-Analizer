"""Leakage-safe dataset construction.

Each row is a *candidate* setup the rule engine found at bar t (valid geometry), described only by
features computable at t. The label comes from the shared trade simulator on bars t+1 ... and the
row records when that label became known (`label_end`) so the splitter can purge overlaps.
Assets that were later delisted are included when their history exists (survivorship control).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.engine import evaluate
from app.analysis.params import StrategyParams
from app.analysis.prepare import prepare
from app.analysis.tradesim import simulate

META_COLS = ["asset", "timeframe", "t_start", "t_end", "label", "r_multiple", "score", "actionable", "outcome"]


@dataclass
class Dataset:
    frame: pd.DataFrame           # META_COLS + feature columns, sorted by t_start
    feature_names: list[str]

    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(json.dumps(self.feature_names).encode())
        h.update(pd.util.hash_pandas_object(self.frame[META_COLS + self.feature_names], index=False).values.tobytes())
        return h.hexdigest()


def build_rows(dfs: dict[str, pd.DataFrame], tf: str, params: StrategyParams, asset: str) -> list[dict]:
    frames = {k: prepare(v, k, params) for k, v in dfs.items() if len(v) >= 60}
    if tf not in frames:
        return []
    pf = frames[tf]
    o, h, lo, c = pf.a("open"), pf.a("high"), pf.a("low"), pf.a("close")
    horizon = params.expiry_bars + params.max_hold_bars + 1
    rows = []
    for t in range(params.min_bars, len(pf) - 1):
        ev = evaluate(frames, tf, t, params, symbol=asset)
        if ev.levels is None or ev.candidate_direction == 0:
            continue
        lv = ev.levels
        a, b = t + 1, min(len(pf), t + 1 + horizon)
        res = simulate(ev.candidate_direction, lv.entry_ref, lv.stop, lv.tp1, lv.tp2, o[a:b], h[a:b], lo[a:b], c[a:b],
                       expiry_bars=params.expiry_bars, max_hold_bars=params.max_hold_bars,
                       tp1_fraction=params.tp1_fraction, move_stop_to_breakeven=params.move_stop_to_breakeven)
        if not res.complete or res.fill_idx is None:
            continue  # unknown label (end of data) or never filled: not a trade outcome
        end_bar = a + (res.exit_idx if res.exit_idx is not None else b - a - 1)
        row = {
            "asset": asset,
            "timeframe": tf,
            "t_start": int(pf.close_times[t]),
            "t_end": int(pf.close_times[min(end_bar, len(pf) - 1)]),
            "label": int((res.r_multiple or 0) > 0),
            "r_multiple": float(res.r_multiple or 0.0),
            "score": float(ev.score),
            "actionable": int(ev.actionable),
            "outcome": res.status,
        }
        row.update({k: (np.nan if v is None else v) for k, v in ev.features.items()})
        rows.append(row)
    return rows


def assemble(rows: list[dict]) -> Dataset:
    if not rows:
        raise ValueError("no candidate setups found - more history is required to build a dataset")
    df = pd.DataFrame(rows).sort_values("t_start").reset_index(drop=True)
    feats = [c for c in df.columns if c not in META_COLS]
    # drop features that are constant or entirely missing (carry no information)
    feats = [f for f in feats if df[f].notna().any() and df[f].nunique(dropna=True) > 1]
    return Dataset(df, sorted(feats))
