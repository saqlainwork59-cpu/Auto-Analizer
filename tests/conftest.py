"""Test configuration.

Pure-calculation tests need nothing. API/integration tests run against a real PostgreSQL
database (TEST_DATABASE_URL, default postgresql+asyncpg://parallax:parallax@localhost:5432/parallax_test)
and Redis if available (falls back to the in-process bus).
"""
from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://parallax:parallax@localhost:5432/parallax_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")  # isolated from dev data
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")
os.environ.setdefault("MODEL_DIR", "/tmp/parallax-test-models")
os.environ["TWELVEDATA_API_KEY"] = ""
os.environ["ALPACA_API_KEY_ID"] = ""
os.environ["ALPACA_API_SECRET_KEY"] = ""
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["REGISTER_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "100000"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    from app.db import Base, init_engine

    engine = init_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available for integration tests: {exc}")
    from app.core.bus import bus

    await bus.connect()
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def app(db_engine):
    from app.main import create_app
    from app.seed import ensure_seed

    await ensure_seed()
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def session(db_engine):
    from app.db import session_factory

    async with session_factory()() as s:
        yield s
