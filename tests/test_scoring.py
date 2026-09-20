from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.db import SessionLocal
from app.models import Memory
from app.retrieval import scoring


def test_weights_sum_to_one():
    assert scoring.W_SIMILARITY + scoring.W_RECENCY + scoring.W_IMPORTANCE == pytest.approx(1.0)


def test_recency_decay_shape():
    now = datetime.now(timezone.utc)
    assert scoring.recency_decay(now, now) == pytest.approx(1.0)
    old = scoring.recency_decay(now - timedelta(days=30), now)
    older = scoring.recency_decay(now - timedelta(days=90), now)
    assert 0 < older < old < 1


def test_final_score_blend():
    assert scoring.final_score(1.0, 1.0, 1.0) == pytest.approx(1.0)
    assert scoring.final_score(0.0, 0.0, 0.0) == 0.0
    assert scoring.final_score(1.0, 0.0, 0.0) == pytest.approx(scoring.W_SIMILARITY)


async def _write(client, user_id, text, **extra):
    r = await client.post("/memories", json={"text": text, "user_id": user_id, "source": "t", **extra})
    return r.json()


async def _search(client, user_id, query, **params):
    r = await client.get("/memories/search", params={"query": query, "user_id": user_id, **params})
    return r.json()


async def test_recent_beats_stale_when_similarity_ties(client, user_id):
    # Same text => identical similarity; only recency/importance differ.
    stale = await _write(client, user_id, "I prefer dark mode in my editor")
    fresh = await _write(client, user_id, "I prefer dark mode in my editor")
    async with SessionLocal() as s:
        await s.execute(
            update(Memory)
            .where(Memory.id == stale["id"])
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=120))
        )
        await s.commit()
    results = await _search(client, user_id, "editor theme preference")
    assert [r["id"] for r in results[:2]] == [fresh["id"], stale["id"]]
    assert results[0]["score"] > results[1]["score"]


async def test_importance_beats_lower_importance_when_similarity_ties(client, user_id):
    low = await _write(client, user_id, "Deploys happen on Fridays", importance_hint=0.1)
    high = await _write(client, user_id, "Deploys happen on Fridays", importance_hint=0.95)
    results = await _search(client, user_id, "when do we deploy?")
    assert results[0]["id"] == high["id"]
    assert results[1]["id"] == low["id"]


async def test_search_increments_access_count_and_importance(client, user_id):
    m = await _write(client, user_id, "My dog is named Biscuit", importance_hint=0.5)
    first = (await _search(client, user_id, "dog name"))[0]
    second = (await _search(client, user_id, "dog name"))[0]
    assert first["id"] == m["id"]
    assert first["access_count"] == 1
    assert second["access_count"] == 2
    assert second["importance_score"] == pytest.approx(0.5 + 2 * scoring.ACCESS_IMPORTANCE_BUMP)


async def test_only_returned_results_are_counted(client, user_id):
    for i in range(3):
        await _write(client, user_id, f"note {i}")
    await _search(client, user_id, "note", top_k=1)
    counts = sorted(r["access_count"] for r in await _search(client, user_id, "note", top_k=3))
    # The second search bumped all three; the first bumped exactly one of them.
    assert counts == [1, 1, 2]


async def test_importance_capped_at_one(client, user_id):
    await _write(client, user_id, "cap test", importance_hint=1.0)
    r = (await _search(client, user_id, "cap test"))[0]
    assert r["importance_score"] == 1.0
