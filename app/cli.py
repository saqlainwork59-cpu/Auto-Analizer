"""Operational CLI.

  python -m app.cli create-admin --email you@example.com
  python -m app.cli seed
  python -m app.cli backfill --symbol BTC/USDT --timeframe 1h --days 365
  python -m app.cli analyze --symbol BTC/USDT --timeframe 1h
  python -m app.cli research --kind ml_train --timeframe 1h
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import init_engine, session_factory
from app.models import Asset, ResearchJob, User


async def create_admin(email: str, password: str | None) -> None:
    from app.security.passwords import hash_password, password_problems

    from email_validator import EmailNotValidError, validate_email

    try:  # same rules as the API, so the account can actually sign in
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        sys.exit(f"Invalid email: {exc}")
    password = password or getpass.getpass("Password: ")
    problems = password_problems(password, email)
    if problems:
        sys.exit("Password " + "; ".join(problems))
    async with session_factory()() as s:
        u = (await s.execute(select(User).where(func.lower(User.email) == email.lower()))).scalar_one_or_none()
        if u:
            u.role, u.password_hash, u.is_active = "admin", hash_password(password), True
        else:
            s.add(User(email=email.lower(), password_hash=hash_password(password), role="admin",
                       settings={"theme": "system", "default_timeframe": "1h"}))
        await s.commit()
    print(f"admin ready: {email}")


async def _asset(s, symbol: str) -> Asset:
    a = (await s.execute(select(Asset).where(Asset.symbol == symbol))).scalar_one_or_none()
    if a is None:
        sys.exit(f"unknown market {symbol}")
    return a


async def main() -> None:
    p = argparse.ArgumentParser(prog="parallax")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--password")
    sub.add_parser("seed")
    b = sub.add_parser("backfill")
    b.add_argument("--symbol", required=True)
    b.add_argument("--timeframe", required=True)
    b.add_argument("--days", type=int, default=365)
    c = sub.add_parser("analyze")
    c.add_argument("--symbol", required=True)
    c.add_argument("--timeframe", required=True)
    r = sub.add_parser("research")
    r.add_argument("--kind", choices=["ml_train", "optimize_weights"], required=True)
    r.add_argument("--timeframe", default="1h")
    args = p.parse_args()

    init_engine()
    from app.core.bus import bus

    await bus.connect()
    if args.cmd == "create-admin":
        await create_admin(args.email, args.password)
    elif args.cmd == "seed":
        from app.seed import ensure_seed

        await ensure_seed()
        print("seeded")
    elif args.cmd == "backfill":
        from app.data.backfill import backfill_range

        async with session_factory()() as s:
            asset = await _asset(s, args.symbol)
            end = datetime.now(timezone.utc)
            n = await backfill_range(s, asset, args.timeframe, end - timedelta(days=args.days), end)
            print(f"stored {n} candles")
    elif args.cmd == "analyze":
        from app.signals.service import analyze

        async with session_factory()() as s:
            asset = await _asset(s, args.symbol)
            ev, sig = await analyze(s, asset, args.timeframe, publish=False)
            print(json.dumps({"headline": ev.headline, "score": ev.score, "regime": ev.regime,
                              "explanation": ev.explanation, "signal_id": sig.id if sig else None}, indent=2))
    elif args.cmd == "research":
        from app.ml.jobs import run_job

        async with session_factory()() as s:
            job = ResearchJob(kind=args.kind, params={"timeframe": args.timeframe}, status="running")
            s.add(job)
            await s.commit()
            print(json.dumps(await run_job(s, job), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
