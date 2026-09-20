import asyncio

import pytest
import redis.asyncio as aioredis

from app import cache
from app.config import settings
from app.embeddings.factory import get_embedder


@pytest.fixture
def embed_calls(monkeypatch):
    embedder = get_embedder()
    calls = []
    real = embedder.embed

    async def counting(text):
        calls.append(text)
        return await real(text)

    monkeypatch.setattr(embedder, "embed", counting)
    return calls


async def _write(client, user_id, text):
    r = await client.post("/memories", json={"text": text, "user_id": user_id, "source": "t"})
    assert r.status_code == 201
    return r.json()


async def _search(client, user_id, query="favorite food"):
    r = await client.get("/memories/search", params={"query": query, "user_id": user_id})
    assert r.status_code == 200
    return r.json()


# --- cache -------------------------------------------------------------------


async def test_repeat_search_hits_cache_and_skips_embedding(client, user_id, embed_calls):
    m = await _write(client, user_id, "My favorite food is ramen")
    first = await _search(client, user_id)
    second = await _search(client, user_id)
    assert len([c for c in embed_calls if c == "favorite food"]) == 1  # second was a hit
    assert [r["id"] for r in first] == [r["id"] for r in second] == [m["id"]]


async def test_cache_hit_still_records_access(client, user_id):
    await _write(client, user_id, "My favorite food is ramen")
    counts = [(await _search(client, user_id))[0]["access_count"] for _ in range(3)]
    assert counts == [1, 2, 3]


async def test_write_invalidates_cached_search(client, user_id):
    await _write(client, user_id, "I like pasta a little")
    await _search(client, user_id)  # populate cache
    new = await _write(client, user_id, "My favorite food is definitely sushi")
    assert new["id"] in [r["id"] for r in await _search(client, user_id)]


async def test_delete_invalidates_cached_search(client, user_id):
    m = await _write(client, user_id, "My favorite food is ramen")
    assert await _search(client, user_id)
    await client.delete(f"/memories/{m['id']}", params={"user_id": user_id})
    assert await _search(client, user_id) == []


async def test_cache_is_per_user(client, user_id):
    other = f"{user_id}-other"
    await _write(client, other, "My favorite food is ramen")
    assert await _search(client, other)
    assert await _search(client, user_id) == []  # same query text, different user


async def test_cache_can_be_disabled(client, user_id, embed_calls, monkeypatch):
    monkeypatch.setattr(settings, "cache_enabled", False)
    await _write(client, user_id, "My favorite food is ramen")
    await _search(client, user_id)
    await _search(client, user_id)
    assert len([c for c in embed_calls if c == "favorite food"]) == 2


# --- rate limiting -----------------------------------------------------------


async def test_rate_limit_returns_429_with_retry_after(client, user_id, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    codes = []
    for _ in range(5):
        r = await client.get("/memories/search", params={"query": "x", "user_id": user_id})
        codes.append(r.status_code)
    assert codes == [200, 200, 200, 429, 429]
    assert 1 <= int(r.headers["Retry-After"]) <= 60


async def test_rate_limit_is_per_user_and_covers_writes(client, user_id, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    for _ in range(2):
        await _write(client, user_id, "hello")
    r = await client.post("/memories", json={"text": "hello", "user_id": user_id, "source": "t"})
    assert r.status_code == 429
    r = await client.get("/memories/search", params={"query": "x", "user_id": f"{user_id}-fresh"})
    assert r.status_code == 200


# --- reliability -------------------------------------------------------------


async def test_redis_outage_fails_open(client, user_id, monkeypatch):
    dead = aioredis.from_url("redis://localhost:1", socket_connect_timeout=0.1, socket_timeout=0.1)
    monkeypatch.setattr(cache, "_client", dead)
    monkeypatch.setattr(cache, "_down_until", 0.0)
    m = await _write(client, user_id, "My favorite food is ramen")
    assert [r["id"] for r in await _search(client, user_id)] == [m["id"]]
    assert cache._down_until > 0  # breaker tripped: later calls skip Redis entirely


# --- embedding micro-batching ------------------------------------------------


async def test_concurrent_embeds_are_batched_and_correct():
    embedder = get_embedder()
    sizes = []
    real = embedder._encode_batch

    def spy(texts):
        sizes.append(len(texts))
        return real(texts)

    embedder._encode_batch = spy
    try:
        texts = [f"batch test sentence number {i}" for i in range(16)]
        vectors = await asyncio.gather(*(embedder.embed(t) for t in texts))
    finally:
        del embedder._encode_batch
    assert sum(sizes) == 16 and max(sizes) > 1  # coalesced, not 16 single calls
    # Batched results must equal the unbatched result for the same text.
    solo = await embedder.embed(texts[3])
    assert vectors[3] == pytest.approx(solo, abs=1e-5)
