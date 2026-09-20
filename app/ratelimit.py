from fastapi import HTTPException, status

from app import cache
from app.config import settings


async def enforce_rate_limit(user_id: str) -> None:
    """Fixed-window limit per user_id. Fails open if Redis is unavailable."""
    if settings.rate_limit_per_minute <= 0:
        return
    hit = await cache.rate_limit_hit(user_id)
    if hit is None:
        return
    count, retry_after = hit
    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
