"""Redis helpers: search-result cache and rate-limit counters.

Redis is an optimization, never a dependency: every call fails open. After a
failure a short circuit breaker skips Redis entirely so a dead Redis costs one
timeout, not one per request.
"""
import asyncio
import hashlib
import json
import logging
import time

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger("recall.cache")

BREAKER_SECONDS = 5.0
VERSION_TTL_SECONDS = 86400

_client: aioredis.Redis | None = None
_down_until = 0.0


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
    return _client


async def guard(op):
    """Run `op(redis)`; return None (and trip the breaker) if Redis is unavailable."""
    global _down_until
    if time.monotonic() < _down_until:
        return None
    try:
        return await op(_redis())
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        _down_until = time.monotonic() + BREAKER_SECONDS
        logger.warning("redis unavailable, bypassing for %.0fs: %s", BREAKER_SECONDS, exc)
        return None


# --- search cache -----------------------------------------------------------
# Keys embed a per-user version; writes/deletes bump it, orphaning that user's
# cached searches (they then expire via TTL). Cheaper than tracking key sets.


async def search_key(user_id: str, query: str, top_k: int) -> str | None:
    if not settings.cache_enabled:
        return None
    version = await guard(lambda r: r.get(f"ver:{user_id}"))
    if version is None and time.monotonic() < _down_until:
        return None
    digest = hashlib.sha1(f"{top_k}\x00{query}".encode()).hexdigest()
    return f"search:{user_id}:{version or 0}:{digest}"


async def get_search(key: str) -> list[tuple[str, float]] | None:
    raw = await guard(lambda r: r.get(key))
    return None if raw is None else [(i, s) for i, s in json.loads(raw)]


async def set_search(key: str, ranked: list[tuple[str, float]]) -> None:
    await guard(lambda r: r.set(key, json.dumps(ranked), ex=settings.cache_ttl_seconds))


async def invalidate_user(user_id: str) -> None:
    if not settings.cache_enabled:
        return

    async def op(r):
        pipe = r.pipeline()
        pipe.incr(f"ver:{user_id}")
        pipe.expire(f"ver:{user_id}", VERSION_TTL_SECONDS)
        await pipe.execute()

    await guard(op)


# --- rate limiting ----------------------------------------------------------


async def rate_limit_hit(user_id: str) -> tuple[int, int] | None:
    """Count a request in the user's current 60s window.

    Returns (count, seconds_until_window_resets), or None if Redis is down.
    """
    window = int(time.time() // 60)
    key = f"rl:{user_id}:{window}"

    async def op(r):
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)
        count, _ = await pipe.execute()
        return count

    count = await guard(op)
    if count is None:
        return None
    return count, max(1, 60 - int(time.time() % 60))
