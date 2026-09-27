"""Walk-forward ML pipeline with purging, an untouched out-of-sample holdout, overfitting checks
and an explicit promotion gate. Nothing produced here reaches production automatically.

    |----------------- development (80%) -----------------|--- holdout (20%) ---|
    | f0 | f1 | f2 | f3 | f4 | f5 |                          touched exactly once
     train -> test (expanding window), train rows whose label window overlaps the
     test period are purged (prevents label leakage between adjacent samples).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.dataset import Dataset

GATE = {
    "min_holdout_rows": 100,
    "min_holdout_auc": 0.55,
    "max_train_val_auc_gap": 0.10,
    "max_fold_auc_std": 0.08,
}


def make_model(kind: str, seed: int = 0):
    if kind == "logreg":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(C=0.3, max_iter=2000)),
        ])
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=40,
                                              l2_regularization=1.0, random_state=seed)
    raise ValueError(f"unknown model kind {kind}")


def holdout_split(t_start: np.ndarray, t_end: np.ndarray, frac: float) -> tuple[np.ndarray, np.ndarray]:
    n = len(t_start)
    cut = int(n * (1 - frac))
    boundary = t_start[cut]
    dev = np.flatnonzero((np.arange(n) < cut) & (t_end < boundary))  # purge overlap into holdout
    hold = np.arange(cut, n)
    return dev, hold


def walk_forward_splits(t_start: np.ndarray, t_end: np.ndarray, idx: np.ndarray, n_folds: int) -> list[tuple[np.ndarray, np.ndarray]]:
    blocks = np.array_split(idx, n_folds + 1)
    splits = []
    for k in range(1, n_folds + 1):
        test = blocks[k]
        if len(test) == 0:
            continue
        test_start = t_start[test[0]]
        train = np.concatenate(blocks[:k])
        train = train[t_end[train] < test_start]  # purge
        splits.append((train, test))
    return splits


def _metrics(y: np.ndarray, p: np.ndarray, r: np.ndarray, threshold: float) -> dict:
    out = {"n": int(len(y)), "base_rate": float(y.mean()) if len(y) else None}
    if len(np.unique(y)) == 2:
        out["auc"] = float(roc_auc_score(y, p))
        out["log_loss"] = float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)))
    else:
        out["auc"] = None
        out["log_loss"] = None
    out["brier"] = float(brier_score_loss(y, p)) if len(y) else None
    take = p >= threshold
    out["expectancy_all_r"] = float(r.mean()) if len(r) else None
    out["expectancy_filtered_r"] = float(r[take].mean()) if take.any() else None
    out["filtered_share"] = float(take.mean()) if len(take) else None
    return out


@dataclass
class TrainResult:
    model: object
    kind: str
    feature_names: list[str]
    metrics: dict
    overfit: dict
    passes_gate: bool
    dataset_info: dict


def train(ds: Dataset, kind: str = "hgb", n_folds: int = 5, holdout_frac: float = 0.2, threshold: float = 0.5,
          seed: int = 0) -> TrainResult:
    df = ds.frame
    X = df[ds.feature_names].to_numpy(dtype=float)
    y = df["label"].to_numpy(dtype=int)
    r = df["r_multiple"].to_numpy(dtype=float)
    ts, te = df["t_start"].to_numpy(), df["t_end"].to_numpy()
    if len(df) < 300:
        raise ValueError(f"dataset too small for walk-forward validation ({len(df)} rows; need >= 300)")

    dev, hold = holdout_split(ts, te, holdout_frac)
    folds = []
    for tr, te_idx in walk_forward_splits(ts, te, dev, n_folds):
        if len(np.unique(y[tr])) < 2 or len(tr) < 50:
            continue
        m = make_model(kind, seed)
        m.fit(X[tr], y[tr])
        p_tr = m.predict_proba(X[tr])[:, 1]
        p_te = m.predict_proba(X[te_idx])[:, 1]
        fm = _metrics(y[te_idx], p_te, r[te_idx], threshold)
        fm["train_auc"] = float(roc_auc_score(y[tr], p_tr)) if len(np.unique(y[tr])) == 2 else None
        fm["train_rows"] = int(len(tr))
        folds.append(fm)
    if not folds:
        raise ValueError("walk-forward produced no usable folds")

    final = make_model(kind, seed)
    final.fit(X[dev], y[dev])
    p_dev = final.predict_proba(X[dev])[:, 1]
    p_hold = final.predict_proba(X[hold])[:, 1]
    hold_m = _metrics(y[hold], p_hold, r[hold], threshold)
    dev_auc = float(roc_auc_score(y[dev], p_dev)) if len(np.unique(y[dev])) == 2 else None
    base = float(y[dev].mean())
    hold_m["baseline_brier"] = float(np.mean((y[hold] - base) ** 2)) if len(hold) else None

    fold_aucs = [f["auc"] for f in folds if f["auc"] is not None]
    train_aucs = [f["train_auc"] for f in folds if f["train_auc"] is not None]
    val_auc = float(np.mean(fold_aucs)) if fold_aucs else None
    gap = (float(np.mean(train_aucs)) - val_auc) if (train_aucs and val_auc is not None) else None
    checks = [
        ("holdout rows", len(hold) >= GATE["min_holdout_rows"], f"{len(hold)} >= {GATE['min_holdout_rows']}"),
        ("holdout AUC", (hold_m["auc"] or 0) >= GATE["min_holdout_auc"], f"{hold_m['auc']} >= {GATE['min_holdout_auc']}"),
        ("beats base-rate Brier", hold_m["brier"] is not None and hold_m["baseline_brier"] is not None
         and hold_m["brier"] < hold_m["baseline_brier"], f"{hold_m['brier']} < {hold_m['baseline_brier']}"),
        ("filter improves expectancy", hold_m["expectancy_filtered_r"] is not None
         and hold_m["expectancy_filtered_r"] > (hold_m["expectancy_all_r"] or 0),
         f"{hold_m['expectancy_filtered_r']} > {hold_m['expectancy_all_r']}"),
        ("train/validation AUC gap", gap is not None and gap <= GATE["max_train_val_auc_gap"], f"gap {gap}"),
        ("fold stability", len(fold_aucs) >= 2 and float(np.std(fold_aucs)) <= GATE["max_fold_auc_std"],
         f"std {float(np.std(fold_aucs)) if fold_aucs else None}"),
    ]
    overfit = {
        "train_auc_mean": float(np.mean(train_aucs)) if train_aucs else None,
        "val_auc_mean": val_auc,
        "gap": gap,
        "fold_auc_std": float(np.std(fold_aucs)) if fold_aucs else None,
        "dev_auc_in_sample": dev_auc,
        "checks": [{"check": n, "passed": bool(ok), "detail": d} for n, ok, d in checks],
    }
    passes = all(ok for _, ok, _ in checks)
    info = {
        "rows": int(len(df)),
        "dev_rows": int(len(dev)),
        "holdout_rows": int(len(hold)),
        "purged_rows": int(len(df) - len(dev) - len(hold)),
        "dev_range": [int(ts[dev[0]]), int(ts[dev[-1]])] if len(dev) else None,
        "holdout_range": [int(ts[hold[0]]), int(ts[hold[-1]])] if len(hold) else None,
        "sha256": ds.sha256,
        "assets": sorted(df["asset"].unique().tolist()),
        "timeframes": sorted(df["timeframe"].unique().tolist()),
    }
    return TrainResult(final, kind, ds.feature_names, {"folds": folds, "holdout": hold_m, "threshold": threshold},
                       overfit, passes, info)
