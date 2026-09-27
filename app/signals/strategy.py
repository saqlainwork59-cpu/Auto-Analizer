"""Strategy versioning. Exactly one version is `active`; changes create a new version that must be
explicitly activated by an admin (never automatically)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.params import DEFAULT_STRATEGY_NAME, StrategyParams
from app.models import Strategy, utcnow


async def get_active(session: AsyncSession) -> tuple[Strategy, StrategyParams]:
    row = (
        await session.execute(select(Strategy).where(Strategy.status == "active").order_by(Strategy.id.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = Strategy(
            name=DEFAULT_STRATEGY_NAME,
            version=1,
            status="active",
            params=StrategyParams().model_dump(),
            notes="Initial default parameters (not yet optimised - run walk-forward research before relying on them).",
            approved_at=utcnow(),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row, StrategyParams.model_validate(row.params)


def version_label(row: Strategy) -> str:
    return f"{row.name}@v{row.version}"


async def create_version(session: AsyncSession, params: StrategyParams, user_id: int | None, notes: str | None,
                         status: str = "draft", validation: dict | None = None) -> Strategy:
    current_max = (await session.execute(select(func.max(Strategy.version)).where(Strategy.name == DEFAULT_STRATEGY_NAME))).scalar() or 0
    row = Strategy(name=DEFAULT_STRATEGY_NAME, version=current_max + 1, status=status, params=params.model_dump(),
                   notes=notes, created_by=user_id, validation=validation)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def activate(session: AsyncSession, strategy_id: int, user_id: int) -> Strategy:
    row = await session.get(Strategy, strategy_id)
    if row is None:
        raise ValueError("strategy not found")
    for other in (await session.execute(select(Strategy).where(Strategy.status == "active"))).scalars():
        other.status = "retired"
    row.status, row.approved_by, row.approved_at = "active", user_id, utcnow()
    await session.commit()
    return row
