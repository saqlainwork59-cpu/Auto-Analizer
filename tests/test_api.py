"""Integration tests: real PostgreSQL + the FastAPI app (auth, security, persistence, paper trading,
kill switches, signals, performance, concurrency)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from app.core import flags
from app.core.bus import bus
from app.core.timeframes import last_closed_open_time, seconds
from app.data.providers.base import CandleDTO
from app.data.store import load_frame, upsert_candles
from app.models import Asset, Signal, SignalOutcome, User
from app.security.passwords import hash_password
from tests.helpers import csrf_headers, register
from tests.synthetic import make_ohlcv, resample

pytestmark = pytest.mark.db


async def make_admin(session, email="admin@example.com", password="Admin-Passw0rd!"):
    u = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if u is None:
        session.add(User(email=email, password_hash=hash_password(password), role="admin", settings={}))
        await session.commit()
    return email, password


async def login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------------------------------ auth & security
async def test_register_login_me_logout(client):
    u = await register(client)
    r = await client.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["email"] == u["email"]
    assert "password_hash" not in r.text
    await client.post("/api/auth/logout", headers=csrf_headers(client))
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401
    await login(client, u["email"], u["password"])
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_password_policy_and_duplicate(client):
    r = await client.post("/api/auth/register", json={"email": "weak@example.com", "password": "alllowercase"})
    assert r.status_code == 422
    u = await register(client)
    client.cookies.clear()
    r = await client.post("/api/auth/register", json={"email": u["email"].upper(), "password": "Another-Str0ng1"})
    assert r.status_code == 409


async def test_passwords_are_hashed(client, session):
    u = await register(client)
    row = (await session.execute(select(User).where(User.email == u["email"]))).scalar_one()
    assert row.password_hash.startswith("$argon2id$") and u["password"] not in row.password_hash


async def test_invalid_login_generic_error_and_lockout(client):
    u = await register(client)
    client.cookies.clear()
    for _ in range(5):
        r = await client.post("/api/auth/login", json={"email": u["email"], "password": "wrong-password-1A"})
        assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"
    r = await client.post("/api/auth/login", json={"email": u["email"], "password": u["password"]})
    assert r.status_code == 423  # locked even with the right password
    r = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401


async def test_csrf_required_for_cookie_mutations(client):
    await register(client)
    r = await client.post("/api/watchlist", json={"asset_id": 1})
    assert r.status_code == 403
    r = await client.post("/api/watchlist", json={"asset_id": 1}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    r = await client.post("/api/watchlist", json={"asset_id": 1}, headers=csrf_headers(client))
    assert r.status_code == 201


async def test_refresh_rotation_and_reuse_detection(client):
    await register(client)
    old = client.cookies.get("px_refresh", path="/api/auth")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200
    new = client.cookies.get("px_refresh", path="/api/auth")
    assert new and new != old
    # replaying the old (rotated) token revokes the whole family
    replay = httpx.AsyncClient(transport=client._transport, base_url="http://testserver", cookies={"px_refresh": old})
    assert (await replay.post("/api/auth/refresh")).status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401
    await replay.aclose()


async def test_protected_routes_require_auth(client):
    client.cookies.clear()
    for path in ("/api/markets", "/api/signals", "/api/paper", "/api/backtests", "/api/performance", "/api/watchlist"):
        assert (await client.get(path)).status_code == 401, path
    r = await client.get("/api/markets", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_admin_routes_forbidden_for_users(client):
    await register(client)
    assert (await client.get("/api/admin/status")).status_code == 403
    r = await client.post("/api/admin/flags/signals_enabled", json={"enabled": False}, headers=csrf_headers(client))
    assert r.status_code == 403


async def test_security_headers_and_validation(client):
    await register(client)
    r = await client.get("/api/markets")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert (await client.get("/api/markets/1/candles?tf=7m")).status_code == 422
    assert (await client.get("/api/signals?limit=100000")).status_code == 422
    r = await client.post("/api/backtests", headers=csrf_headers(client), json={
        "asset_id": 1, "timeframe": "1h", "start": "2024-05-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"})
    assert r.status_code == 422
    r = await client.post("/api/backtests", headers=csrf_headers(client), json={
        "asset_id": 1, "timeframe": "1h", "start": "2024-01-01T00:00:00Z", "end": "2024-05-01T00:00:00Z", "hack": 1})
    assert r.status_code == 422  # unknown fields rejected
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": 1, "direction": "LONG", "quantity": 1})
    assert r.status_code == 422


async def test_login_rate_limit(client, monkeypatch):
    from app.security import ratelimit

    calls = {"n": 0}
    real = ratelimit.hit

    async def fake_hit(bucket, identity, limit, window=60):
        if bucket == "login":
            calls["n"] += 1
            return calls["n"] <= 8, 0  # production default login limit
        return await real(bucket, identity, limit, window)

    monkeypatch.setattr(ratelimit, "hit", fake_hit)
    codes = [(await client.post("/api/auth/login", json={"email": "x@example.com", "password": "y"})).status_code
             for _ in range(12)]
    assert 429 in codes


# ------------------------------------------------------------------------------------------ market data
async def seed_candles(session, symbol="BTC/USDT", days=45, seed=3):
    asset = (await session.execute(select(Asset).where(Asset.symbol == symbol))).scalar_one()
    now = datetime.now(timezone.utc)
    end_open = last_closed_open_time(now, "5m")
    n = days * 288
    start = end_open - timedelta(minutes=5 * (n - 1))
    m5 = make_ohlcv(n=n, tf_seconds=300, seed=seed, start=start.isoformat(), base=30000)
    frames = {"5m": m5, "15m": resample(m5, "15min"), "1h": resample(m5, "1h"), "4h": resample(m5, "4h"),
              "1d": resample(m5, "1D")}
    for tf, df in frames.items():
        closed = df[df.index + pd.Timedelta(seconds=seconds(tf)) <= now]
        candles = [CandleDTO(ix.to_pydatetime(), r.open, r.high, r.low, r.close, r.volume, True) for ix, r in closed.iterrows()]
        await upsert_candles(session, asset.id, tf, candles, "test-fixture")
    return asset


async def test_data_unavailable_when_provider_not_configured(client):
    await register(client)
    r = await client.get("/api/markets")
    m = {x["symbol"]: x for x in r.json()["markets"]}
    assert m["EUR/USD"]["data_status"] == "unavailable" and "not configured" in m["EUR/USD"]["data_reason"]
    assert m["EUR/USD"]["last"] is None  # never a made-up price
    eur = m["EUR/USD"]["id"]
    r = await client.get(f"/api/markets/{eur}/candles?tf=1h")
    assert r.json()["candles"] == [] and r.json()["data_status"] == "unavailable"


async def test_candles_analysis_and_persistence(client, session):
    asset = await seed_candles(session)
    await register(client)
    r = await client.get(f"/api/markets/{asset.id}/candles?tf=1h&limit=200")
    body = r.json()
    assert len(body["candles"]) == 200 and body["stale"] is False
    assert {"ema20", "rsi14", "macd_hist", "bb_up"} <= set(body["overlays"])
    times = [c["time"] for c in body["candles"]]
    assert times == sorted(times)
    r = await client.post(f"/api/markets/{asset.id}/analyze?tf=1h", headers=csrf_headers(client))
    sig = r.json()["signal"]
    assert sig["status"] in ("ACTIONABLE", "WAIT") and sig["timeframe"] == "1h"
    assert sig["explanation"] and sig["mtf"]["timeframes"]
    # same closed bar -> same stored evaluation (idempotent), never a re-roll
    r2 = await client.post(f"/api/markets/{asset.id}/analyze?tf=1h", headers=csrf_headers(client))
    assert r2.json()["signal"]["id"] == sig["id"]
    detail = (await client.get(f"/api/signals/{sig['id']}")).json()
    assert detail["features"] and "regime_TRENDING_UP" in detail["features"]
    # persistence: a fresh session sees the same row
    from app.db import session_factory

    async with session_factory()() as s2:
        assert (await s2.get(Signal, sig["id"])) is not None


async def test_stale_data_blocks_analysis(client, session):
    asset = await seed_candles(session, symbol="ETH/USDT", days=40, seed=4)
    await register(client)
    from app.signals.service import analyze

    future = datetime.now(timezone.utc) + timedelta(hours=6)
    ev, _ = await analyze(session, asset, "15m", now=future, persist=False)
    assert ev.status == "DATA_UNAVAILABLE" and any("stale" in i for i in ev.data_quality["issues"])


async def test_signal_outcome_tracking(session):
    asset = await seed_candles(session, symbol="SOL/USDT", days=40, seed=5)
    df = await load_frame(session, asset.id, "1h")
    i = len(df) - 60
    bar_time = df.index[i].to_pydatetime() + timedelta(hours=1)
    nxt = df.iloc[i + 1]
    entry = float(nxt["open"])  # guaranteed to be touched on the next bar
    risk = float(df["close"].iloc[i]) * 0.5  # far stop and targets -> stays open until time exit
    sig = Signal(bar_time=bar_time, asset_id=asset.id, timeframe="1h", status="ACTIONABLE", direction="BUY",
                 setup_type="trend_pullback", regime="TRENDING_UP", score=80, entry_low=entry, entry_high=entry,
                 entry_ref=entry, stop_loss=entry - risk, tp1=entry + 2 * risk, tp2=entry + 3 * risk, rr_tp1=2, rr_tp2=3,
                 headline="test", explanation="test", strategy_version="test@v0", engine_version="test",
                 levels_meta={"sim": {"expiry_bars": 4, "max_hold_bars": 10, "tp1_fraction": 0.5,
                                      "move_stop_to_breakeven": True}})
    session.add(sig)
    await session.flush()
    session.add(SignalOutcome(signal_id=sig.id, status="PENDING"))
    await session.commit()
    from app.signals.outcomes import update_all_open

    await update_all_open(session)
    out = await session.get(SignalOutcome, sig.id)
    await session.refresh(out)
    assert out.status == "TIME_EXIT" and out.filled_at is not None and out.r_multiple is not None


# ------------------------------------------------------------------------------------------ paper trading
async def set_price(asset_id: int, price: float, age_s: float = 0):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    await bus.set_json(f"px:last:{asset_id}", {"price": price, "ts": ts.isoformat(), "source": "test"}, ex=600)


async def test_paper_trading_flow_and_pnl(client, session):
    await register(client)
    asset = (await session.execute(select(Asset).where(Asset.symbol == "BNB/USDT"))).scalar_one()
    await bus.backend.set(f"px:last:{asset.id}", "", ex=1)
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": asset.id, "direction": "BUY", "quantity": 1})
    assert r.status_code == 422 and "unavailable" in r.json()["detail"]  # no live price -> refuse
    await set_price(asset.id, 500.0, age_s=3600)
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": asset.id, "direction": "BUY", "quantity": 1})
    assert r.status_code == 422  # stale price -> refuse
    await set_price(asset.id, 500.0)
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": asset.id, "direction": "BUY", "quantity": 2, "stop_loss": 490, "take_profit": 520})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    bad = await client.post("/api/paper/trades", headers=csrf_headers(client),
                            json={"asset_id": asset.id, "direction": "BUY", "quantity": 1, "stop_loss": 510})
    assert bad.status_code == 422
    await set_price(asset.id, 510.0)
    acct = (await client.get("/api/paper")).json()
    assert acct["unrealized_pnl"] == pytest.approx(20.0)
    r = await client.post(f"/api/paper/trades/{tid}/close", headers=csrf_headers(client))
    t = r.json()
    fees = 0.001 * 2 * 500 + 0.001 * 2 * 510
    assert t["pnl"] == pytest.approx(20.0 - fees)
    acct = (await client.get("/api/paper")).json()
    assert acct["account"]["cash_balance"] == pytest.approx(10_000 + 20.0 - fees)
    assert acct["stats"]["closed_trades"] == 1
    assert (await client.post(f"/api/paper/trades/{tid}/close", headers=csrf_headers(client))).status_code == 422


async def test_paper_protective_orders(client, session):
    u = await register(client)
    asset = (await session.execute(select(Asset).where(Asset.symbol == "XRP/USDT"))).scalar_one()
    await set_price(asset.id, 1.0)
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": asset.id, "direction": "SELL", "risk_pct": 1, "stop_loss": 1.05, "take_profit": 0.9})
    assert r.status_code == 201
    qty = r.json()["quantity"]
    assert qty == pytest.approx(10_000 * 0.01 / 0.05, rel=1e-6)  # risk-based sizing
    from app.paper.service import check_protective_orders

    closed = await check_protective_orders(session, asset, low=0.95, high=1.06, last=1.0)  # both touched -> stop first
    assert closed >= 1
    trades = (await client.get("/api/paper/trades?status=CLOSED")).json()
    mine = [t for t in trades if t["id"] == r.json()["id"]][0]
    assert mine["close_reason"] == "STOP" and mine["exit_price"] == pytest.approx(1.05)
    _ = u


async def test_users_cannot_access_each_others_data(app, session):
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://testserver") as a, \
            httpx.AsyncClient(transport=t, base_url="http://testserver") as b:
        await register(a)
        await register(b)
        asset = (await session.execute(select(Asset).where(Asset.symbol == "BTC/USDT"))).scalar_one()
        await set_price(asset.id, 30000)
        tid = (await a.post("/api/paper/trades", headers=csrf_headers(a),
                            json={"asset_id": asset.id, "direction": "BUY", "quantity": 0.01})).json()["id"]
        assert (await b.post(f"/api/paper/trades/{tid}/close", headers=csrf_headers(b))).status_code == 422
        assert all(x["id"] != tid for x in (await b.get("/api/paper/trades")).json())
        bt = await a.post("/api/backtests", headers=csrf_headers(a), json={
            "asset_id": asset.id, "timeframe": "1h", "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"})
        assert bt.status_code == 202
        assert (await b.get(f"/api/backtests/{bt.json()['id']}")).status_code == 404
        r = await a.post("/api/alerts", headers=csrf_headers(a), json={"channel": "email", "destination": "a@example.com"})
        assert r.status_code == 201 and "a@example.com" not in r.text  # destination stored encrypted, only a hint returned
        assert (await b.delete(f"/api/alerts/{r.json()['id']}", headers=csrf_headers(b))).status_code == 404


# ------------------------------------------------------------------------------------------ admin & kill switches
async def test_kill_switches(client, session):
    email, pw = await make_admin(session)
    await login(client, email, pw)
    status = (await client.get("/api/admin/status")).json()
    assert status["database"] == "ok" and any(f["key"] == "signals_enabled" for f in status["flags"])
    r = await client.post("/api/admin/flags/paper_trading_enabled", json={"enabled": False, "reason": "test"},
                          headers=csrf_headers(client))
    assert r.status_code == 200
    flags.clear_cache()
    asset = (await session.execute(select(Asset).where(Asset.symbol == "BTC/USDT"))).scalar_one()
    await set_price(asset.id, 30000)
    r = await client.post("/api/paper/trades", headers=csrf_headers(client),
                          json={"asset_id": asset.id, "direction": "BUY", "quantity": 0.01})
    assert r.status_code == 422 and "disabled" in r.json()["detail"]
    await client.post("/api/admin/flags/paper_trading_enabled", json={"enabled": True}, headers=csrf_headers(client))
    # broker execution cannot be enabled while the deploy-time flag is off
    r = await client.post("/api/admin/flags/broker_execution_enabled", json={"enabled": True}, headers=csrf_headers(client))
    assert r.status_code == 409
    audit = (await client.get("/api/admin/audit")).json()
    assert any(a["action"] == "admin.flag" for a in audit)
    flags.clear_cache()


async def test_signals_kill_switch_downgrades_to_wait(session, monkeypatch):
    from app.analysis import engine as eng
    from app.signals import service

    asset = await seed_candles(session, symbol="BNB/USDT", days=40, seed=9)
    real = eng.evaluate

    def always_actionable(*a, **k):
        e = real(*a, **k)
        e.status, e.direction = "ACTIONABLE", "BUY"
        return e

    monkeypatch.setattr(service, "evaluate", always_actionable)
    await flags.set_flag(session, "signals_enabled", False, None, "test")
    ev, _ = await service.analyze(session, asset, "4h", persist=False)
    assert ev.status == "WAIT" and "paused" in ev.headline
    await flags.set_flag(session, "signals_enabled", True, None, None)


async def test_broker_orders_always_blocked(client):
    await register(client)
    r = await client.post("/api/broker/orders", headers=csrf_headers(client), json={
        "symbol": "BTC/USDT", "side": "BUY", "quantity": 1, "password": "x", "confirmation_token": "y"})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


async def test_strategy_activation_requires_validation(client, session):
    email, pw = await make_admin(session)
    await login(client, email, pw)
    from app.analysis.params import StrategyParams

    r = await client.post("/api/admin/strategies", headers=csrf_headers(client),
                          json={"params": {**StrategyParams().model_dump(), "min_score": 60}, "notes": "draft"})
    assert r.status_code == 201
    sid = r.json()["id"]
    r = await client.post(f"/api/admin/strategies/{sid}/activate", headers=csrf_headers(client), json={})
    assert r.status_code == 409
    r = await client.post("/api/admin/strategies", headers=csrf_headers(client), json={"params": {"weights": {"bogus": 1}}})
    assert r.status_code == 422


async def test_performance_includes_losses(client, session):
    await register(client)
    asset = (await session.execute(select(Asset).where(Asset.symbol == "BTC/USDT"))).scalar_one()
    now = datetime.now(timezone.utc)
    for k, (st, r_mult) in enumerate([("LOSS", -1.0), ("WIN_TP2", 2.5), ("LOSS", -1.0), ("EXPIRED", None)]):
        s = Signal(bar_time=now - timedelta(hours=100 + k), asset_id=asset.id, timeframe="4h", status="ACTIONABLE",
                   direction="BUY", setup_type="breakout_retest", regime="BREAKOUT", score=75, rr_tp1=2.0,
                   headline="t", explanation="t", strategy_version=f"perf-test@v{k}", engine_version="t")
        session.add(s)
        await session.flush()
        session.add(SignalOutcome(signal_id=s.id, status=st, r_multiple=r_mult, exit_at=now - timedelta(hours=90 - k)))
    await session.commit()
    p = (await client.get("/api/performance?days=30")).json()
    assert p["overall"]["losses"] >= 2 and p["overall"]["wins"] >= 1
    assert p["by_outcome"]["LOSS"] >= 2 and p["by_outcome"]["EXPIRED"] >= 1
    losing = (await client.get("/api/signals?outcome=LOSS")).json()
    assert losing["total"] >= 2


# ------------------------------------------------------------------------------------------ concurrency
async def test_concurrent_users(app, session):
    t = httpx.ASGITransport(app=app)

    async def one_user(i: int):
        async with httpx.AsyncClient(transport=t, base_url="http://testserver") as c:
            u = await register(c)
            r1 = await c.get("/api/markets")
            r2 = await c.post("/api/watchlist", json={"asset_id": 1}, headers=csrf_headers(c))
            r3 = await c.get("/api/paper")
            return u, r1.status_code, r2.status_code, r3.status_code

    results = await asyncio.gather(*(one_user(i) for i in range(25)))
    assert all(r[1:] == (200, 201, 200) for r in results)
    assert len({r[0]["id"] for r in results}) == 25


def test_websocket_requires_auth_and_origin(app):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    tc = TestClient(app)  # no context manager: do not run the app lifespan in a second event loop
    with pytest.raises(WebSocketDisconnect) as exc:
        with tc.websocket_connect("/api/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with tc.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert exc.value.code == 4403


async def test_alert_dispatch_dedup_and_telegram(client, session, monkeypatch):
    import respx

    from app.alerts.dispatcher import dispatch
    from app.config import get_settings
    from app.models import AlertDelivery

    u = await register(client)
    asset = (await session.execute(select(Asset).where(Asset.symbol == "ETH/USDT"))).scalar_one()
    await client.post("/api/watchlist", json={"asset_id": asset.id}, headers=csrf_headers(client))
    r = await client.post("/api/alerts", headers=csrf_headers(client), json={"channel": "browser", "min_score": 70})
    assert r.status_code == 201
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "123:abc")
    r = await client.post("/api/alerts", headers=csrf_headers(client),
                          json={"channel": "telegram", "destination": "987654321", "min_score": 70, "asset_ids": [asset.id]})
    assert r.status_code == 201
    now = datetime.now(timezone.utc)
    sig = Signal(bar_time=now, asset_id=asset.id, timeframe="1h", status="ACTIONABLE", direction="BUY",
                 setup_type="trend_pullback", regime="TRENDING_UP", score=82, entry_low=100, entry_high=101, entry_ref=100.5,
                 stop_loss=98, tp1=106, tp2=109, rr_tp1=2.2, rr_tp2=3.4, expires_at=now + timedelta(hours=4),
                 headline="BUY", explanation="x", strategy_version="alert-test@v1", engine_version="t")
    session.add(sig)
    await session.commit()
    sig = (await session.execute(select(Signal).where(Signal.id == sig.id))).unique().scalar_one()
    with respx.mock:
        route = respx.post("https://api.telegram.org/bot123:abc/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True}))
        sent = await dispatch(session, sig)
        assert sent >= 2 and route.called
        body = route.calls[0].request.content.decode()
        assert "ETH/USDT" in body and "Score: 82/100" in body and f"/signals/{sig.id}" in body
        assert await dispatch(session, sig) == 0  # never delivered twice
    rows = (await session.execute(select(AlertDelivery).where(AlertDelivery.signal_id == sig.id))).scalars().all()
    assert {r.status for r in rows} == {"sent"}
    _ = u
