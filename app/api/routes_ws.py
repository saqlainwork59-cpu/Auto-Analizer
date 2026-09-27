"""WebSocket fan-out of live events. Authenticated via the httpOnly access cookie; the Origin
header is checked to prevent cross-site WebSocket hijacking."""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.bus import CH_CANDLES, CH_PAPER, CH_SIGNALS, CH_SYSTEM, bus
from app.security.tokens import ACCESS_COOKIE, decode_access_token

router = APIRouter()
MAX_SUBSCRIPTIONS = 100


@router.websocket("/api/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    allowed = set(get_settings().cors_origin_list) | {get_settings().public_app_url.rstrip("/")}
    if origin and origin.rstrip("/") not in allowed:
        await ws.close(code=4403)
        return
    token = ws.cookies.get(ACCESS_COOKIE) or ws.query_params.get("token")
    payload = decode_access_token(token) if token else None
    if not payload:
        await ws.close(code=4401)
        return
    user_id, role = int(payload["sub"]), payload.get("role")
    await ws.accept()
    subs: set[int] = set()
    send_lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(msg, default=str))

    async def pump() -> None:
        async for channel, msg in bus.subscribe([CH_CANDLES, CH_SIGNALS, CH_SYSTEM, CH_PAPER]):
            t = msg.get("type")
            if channel == CH_CANDLES and msg.get("asset_id") not in subs:
                continue
            if t in ("notify",) or channel == CH_PAPER:
                if msg.get("user_id") != user_id:
                    continue
            if channel == CH_SYSTEM and role != "admin" and t != "flag":
                continue
            await send(msg)

    task = asyncio.create_task(pump())
    try:
        await send({"type": "hello", "user_id": user_id})
        while True:
            raw = await ws.receive_text()
            if len(raw) > 4096:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            action = msg.get("action")
            ids = [int(x) for x in msg.get("asset_ids", []) if isinstance(x, int) or str(x).isdigit()][:MAX_SUBSCRIPTIONS]
            if action == "subscribe":
                subs.update(ids)
                if len(subs) > MAX_SUBSCRIPTIONS:
                    subs = set(list(subs)[:MAX_SUBSCRIPTIONS])
            elif action == "unsubscribe":
                subs.difference_update(ids)
            elif action == "ping":
                await send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
