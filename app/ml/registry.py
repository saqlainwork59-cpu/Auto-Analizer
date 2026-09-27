"""Model registry: versioned, hashed artifacts; promotion only through an explicit, gated admin action."""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable

import joblib
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ml.pipeline import TrainResult
from app.models import ModelVersion, utcnow

log = logging.getLogger(__name__)
MODEL_NAME = "setup-outcome-classifier"
_cache: dict[int, tuple[object, list[str]]] = {}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def register(session: AsyncSession, res: TrainResult, params: dict, notes: str | None = None) -> ModelVersion:
    version = ((await session.execute(select(func.max(ModelVersion.version)).where(ModelVersion.name == MODEL_NAME))).scalar() or 0) + 1
    d = get_settings().model_dir
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{MODEL_NAME}-v{version}.joblib")
    joblib.dump({"model": res.model, "feature_names": res.feature_names, "kind": res.kind}, path)
    row = ModelVersion(
        name=MODEL_NAME, version=version, status="candidate" if res.passes_gate else "rejected",
        algorithm=res.kind, artifact_path=path, artifact_sha256=_sha256(path), feature_names=res.feature_names,
        dataset=res.dataset_info, metrics=res.metrics, overfit={**res.overfit, "passes_gate": res.passes_gate},
        params=params, notes=notes,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def promote(session: AsyncSession, model_id: int, user_id: int) -> ModelVersion:
    row = await session.get(ModelVersion, model_id)
    if row is None:
        raise ValueError("model not found")
    if not (row.overfit or {}).get("passes_gate"):
        raise ValueError("model did not pass the out-of-sample validation gate and cannot be promoted")
    if row.status not in ("candidate", "retired"):
        raise ValueError(f"model in status {row.status} cannot be promoted")
    if _sha256(row.artifact_path) != row.artifact_sha256:
        raise ValueError("artifact hash mismatch - file changed since training")
    for other in (await session.execute(select(ModelVersion).where(ModelVersion.status == "production"))).scalars():
        other.status = "retired"
    row.status, row.approved_by, row.approved_at = "production", user_id, utcnow()
    await session.commit()
    return row


async def retire(session: AsyncSession, model_id: int) -> None:
    row = await session.get(ModelVersion, model_id)
    if row is not None:
        row.status = "retired"
        await session.commit()


async def production_scorer(session: AsyncSession) -> tuple[Callable[[dict], float | None] | None, ModelVersion | None]:
    row = (
        await session.execute(select(ModelVersion).where(ModelVersion.status == "production").order_by(ModelVersion.id.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return None, None
    if row.id not in _cache:
        try:
            if _sha256(row.artifact_path) != row.artifact_sha256:
                log.error("model %s artifact hash mismatch; refusing to load", row.id)
                return None, None
            blob = joblib.load(row.artifact_path)
            _cache[row.id] = (blob["model"], blob["feature_names"])
        except (OSError, KeyError, ValueError) as exc:
            log.error("cannot load model %s: %s", row.id, exc)
            return None, None
    model, names = _cache[row.id]

    def score(features: dict) -> float | None:
        x = np.array([[np.nan if features.get(n) is None else float(features[n]) for n in names]])
        try:
            return float(model.predict_proba(x)[0, 1])  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None

    return score, row
