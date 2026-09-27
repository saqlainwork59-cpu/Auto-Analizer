"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import routes_admin, routes_auth, routes_market, routes_signals, routes_user, routes_ws
from app.config import get_settings
from app.core.bus import bus
from app.db import get_engine, init_engine
from app.logging_setup import setup_logging
from app.security.ratelimit import client_ip, hit

log = logging.getLogger("parallax.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    problems = s.validate_production()
    if problems:
        raise RuntimeError("Refusing to start with insecure production config: " + "; ".join(problems))
    init_engine()
    await bus.connect()
    if s.app_env != "test":
        from app.seed import ensure_seed

        await ensure_seed()
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    s = get_settings()
    if s.app_env != "test":
        setup_logging()
    app = FastAPI(
        title="Parallax Trading Analysis API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if s.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if s.is_production else "/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
    )

    @app.middleware("http")
    async def security_and_logging(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        if request.url.path.startswith("/api/") and request.url.path not in ("/api/health",):
            ok, _ = await hit("global", client_ip(request), s.rate_limit_per_minute, 60)
            if not ok:
                return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
            if int(request.headers.get("content-length") or 0) > 1_000_000:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        log.info("request", extra={"request_id": rid, "path": request.url.path, "status": response.status_code,
                                   "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                                   "user_id": getattr(request.state, "user_id", None)})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errs = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": "Invalid input", "errors": errs}, status_code=422)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/ready")
    async def ready():
        db_ok = True
        try:
            async with get_engine().connect() as c:
                await c.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        bus_ok = await bus.healthy()
        code = 200 if (db_ok and bus_ok) else 503
        return JSONResponse({"database": db_ok, "bus": bus_ok, "bus_kind": bus.kind}, status_code=code)

    for r in (routes_auth, routes_market, routes_signals, routes_user, routes_admin, routes_ws):
        app.include_router(r.router)
    return app


app = create_app()
