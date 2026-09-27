"""Fixed-window rate limiting backed by Redis (shared across API replicas)."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, status

from app.core.bus import bus


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers (configured in Docker) already resolves X-Forwarded-For from trusted proxies.
    return request.client.host if request.client else "unknown"


async def hit(bucket: str, identity: str, limit: int, window: int = 60) -> tuple[bool, int]:
    window_id = int(time.time() // window)
    n = await bus.incr_window(f"rl:{bucket}:{identity}:{window_id}", window + 1)
    return n <= limit, max(0, limit - n)


def limiter(bucket: str, limit: int, window: int = 60):
    """FastAPI dependency factory: `Depends(limiter("login", 8))`."""

    async def _dep(request: Request) -> None:
        ok, _ = await hit(bucket, client_ip(request), limit, window)
        if not ok:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down.",
                headers={"Retry-After": str(window)},
            )

    return _dep
