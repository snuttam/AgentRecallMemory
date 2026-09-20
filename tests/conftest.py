import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Tests use their own Redis DB so they never touch dev cache/rate-limit keys.
os.environ["REDIS_URL"] = "redis://localhost:6379/15"

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    import redis

    redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


@pytest_asyncio.fixture(scope="session")
async def client(migrated_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def user_id() -> str:
    # Unique per test so runs never collide with leftover rows.
    return f"test-{uuid.uuid4()}"
