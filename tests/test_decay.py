from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.jobs import decay
from app.models import Memory


def ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


async def _write(client, user_id, text, **extra):
    r = await client.post("/memories", json={"text": text, "user_id": user_id, "source": "t", **extra})
    assert r.status_code == 201
    return r.json()


async def _set(mem_id, **values):
    async with SessionLocal() as s:
        await s.execute(update(Memory).where(Memory.id == mem_id).values(**values))
        await s.commit()


async def _get(mem_id):
    async with SessionLocal() as s:
        return (await s.execute(select(Memory).where(Memory.id == mem_id))).scalar_one_or_none()


async def test_expired_memories_are_deleted(client, user_id):
    expired = await _write(client, user_id, "short lived")
    alive = await _write(client, user_id, "long lived, totally unrelated content about astronomy")
    await _set(expired["id"], expires_at=ago(1))
    stats = await decay.run_decay_job(user_id=user_id)
    assert stats.expired == 1
    assert await _get(expired["id"]) is None
    assert await _get(alive["id"]) is not None


async def test_stale_memory_importance_decays_recent_does_not(client, user_id):
    stale = await _write(client, user_id, "stale one about gardening", importance_hint=0.8)
    fresh = await _write(client, user_id, "fresh one about quantum computing", importance_hint=0.8)
    await _set(stale["id"], last_accessed_at=ago(30), created_at=ago(60))
    await _set(fresh["id"], last_accessed_at=ago(1))
    await decay.run_decay_job(user_id=user_id)
    assert (await _get(stale["id"])).importance_score == pytest.approx(0.8 * decay.DECAY_FACTOR)
    assert (await _get(fresh["id"])).importance_score == pytest.approx(0.8)


async def test_never_accessed_memory_decays_from_created_at(client, user_id):
    m = await _write(client, user_id, "never read, about cooking", importance_hint=0.5)
    await _set(m["id"], created_at=ago(30))
    await decay.run_decay_job(user_id=user_id)
    assert (await _get(m["id"])).importance_score == pytest.approx(0.5 * decay.DECAY_FACTOR)


async def test_decay_stops_at_floor(client, user_id):
    m = await _write(client, user_id, "floor test about sailing", importance_hint=0.06)
    await _set(m["id"], created_at=ago(30))
    for _ in range(3):
        await decay.run_decay_job(user_id=user_id)
    assert (await _get(m["id"])).importance_score == pytest.approx(decay.MIN_IMPORTANCE)


async def test_search_marks_memory_accessed_so_it_does_not_decay(client, user_id):
    m = await _write(client, user_id, "I work at a bakery", importance_hint=0.5)
    await _set(m["id"], created_at=ago(60))
    await client.get("/memories/search", params={"query": "bakery job", "user_id": user_id})
    before = (await _get(m["id"])).importance_score
    await decay.run_decay_job(user_id=user_id)
    assert (await _get(m["id"])).importance_score == pytest.approx(before)


async def test_duplicates_merge_keeping_higher_importance_and_summing_access(client, user_id):
    low = await _write(client, user_id, "My favorite color is blue", importance_hint=0.3)
    high = await _write(client, user_id, "My favorite color is blue", importance_hint=0.9)
    distinct = await _write(client, user_id, "The stock market closed higher today")
    await _set(low["id"], access_count=4)
    await _set(high["id"], access_count=2)

    stats = await decay.run_decay_job(user_id=user_id)

    assert stats.merged_away == 1
    kept, removed = stats.merged[0]
    assert str(kept) == high["id"] and [str(x) for x in removed] == [low["id"]]
    assert await _get(low["id"]) is None
    survivor = await _get(high["id"])
    assert survivor.access_count == 6
    assert await _get(distinct["id"]) is not None


async def test_consolidation_never_crosses_users(client, user_id):
    other = f"{user_id}-other"
    mine = await _write(client, user_id, "I live in Lisbon")
    theirs = await _write(client, other, "I live in Lisbon")
    await decay.run_decay_job(user_id=user_id)
    await decay.run_decay_job(user_id=other)
    assert await _get(mine["id"]) is not None
    assert await _get(theirs["id"]) is not None


async def test_job_skips_when_lock_held(client, user_id):
    async with SessionLocal() as s, s.begin():
        from sqlalchemy import func
        await s.execute(select(func.pg_advisory_xact_lock(decay.ADVISORY_LOCK_KEY)))
        stats = await decay.run_decay_job(user_id=user_id)
    assert stats.skipped


def test_scheduler_uses_configured_interval():
    job = decay.create_scheduler().get_jobs()[0]
    assert job.trigger.interval == timedelta(hours=settings.decay_job_interval_hours)


async def test_default_ttl_sets_expires_at(client, user_id, monkeypatch):
    monkeypatch.setattr(settings, "default_ttl_days", 30)
    m = await _write(client, user_id, "ttl memory")
    expires = datetime.fromisoformat(m["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(timezone.utc) <= timedelta(days=30)
