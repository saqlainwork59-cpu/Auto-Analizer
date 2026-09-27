"""Timeframe helpers. All timestamps in the platform are UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TIMEFRAMES: dict[str, int] = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
ORDER = ["5m", "15m", "1h", "4h", "1d"]


def seconds(tf: str) -> int:
    try:
        return TIMEFRAMES[tf]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {tf}") from exc


def delta(tf: str) -> timedelta:
    return timedelta(seconds=seconds(tf))


def higher_than(tf: str) -> list[str]:
    return ORDER[ORDER.index(tf) + 1 :]


def floor_time(ts: datetime, tf: str) -> datetime:
    s = seconds(tf)
    epoch = int(ts.replace(tzinfo=ts.tzinfo or timezone.utc).timestamp())
    return datetime.fromtimestamp(epoch - epoch % s, tz=timezone.utc)


def last_closed_open_time(now: datetime, tf: str) -> datetime:
    """Open time of the most recent candle that has fully closed at `now`."""
    return floor_time(now, tf) - delta(tf)


def is_valid(tf: str) -> bool:
    return tf in TIMEFRAMES
