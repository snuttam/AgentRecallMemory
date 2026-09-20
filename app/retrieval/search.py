from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory
from app.retrieval.scoring import (
    ACCESS_IMPORTANCE_BUMP,
    final_score,
    normalize_cosine,
    recency_decay,
)

# Candidates are pulled by similarity, then re-ranked by the blended score.
# The pool must be larger than top_k so a recent/important memory that is
# slightly less similar can still overtake.
CANDIDATE_MULTIPLIER = 4
MIN_CANDIDATES = 20

_NOT_EXPIRED = or_(Memory.expires_at.is_(None), Memory.expires_at > func.now())


async def rank_memories(
    session: AsyncSession, user_id: str, query_embedding: list[float], top_k: int
) -> list[tuple[Memory, float]]:
    """Vector search + blended re-rank. Read-only."""
    distance = Memory.embedding.cosine_distance(query_embedding)
    stmt = (
        select(Memory, distance.label("distance"))
        .where(Memory.user_id == user_id)
        .where(_NOT_EXPIRED)
        .order_by(distance)
        .limit(max(top_k * CANDIDATE_MULTIPLIER, MIN_CANDIDATES))
    )
    rows = (await session.execute(stmt)).all()
    scored = [
        (
            m,
            final_score(normalize_cosine(1.0 - d), recency_decay(m.created_at), m.importance_score),
        )
        for m, d in rows
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


async def load_ranked(
    session: AsyncSession, user_id: str, ranked: list[tuple[str, float]]
) -> list[tuple[Memory, float]]:
    """Rehydrate cached (id, score) pairs; rows deleted/expired since caching are dropped."""
    if not ranked:
        return []
    ids = [i for i, _ in ranked]
    rows = (
        await session.execute(
            select(Memory).where(Memory.id.in_(ids), Memory.user_id == user_id).where(_NOT_EXPIRED)
        )
    ).scalars().all()
    by_id = {str(m.id): m for m in rows}
    return [(by_id[i], s) for i, s in ranked if i in by_id]


async def record_access(session: AsyncSession, user_id: str, top: list[tuple[Memory, float]]) -> None:
    """Bump access_count / importance for returned memories and commit."""
    if not top:
        return
    # Atomic in SQL so concurrent searches don't lose increments. Rows are
    # locked in id order first: concurrent searches return overlapping memories
    # in different (score) orders, and updating in that order deadlocks.
    ids = [m.id for m, _ in top]
    locked = (
        select(Memory.id)
        .where(Memory.id.in_(ids), Memory.user_id == user_id)
        .order_by(Memory.id)
        .with_for_update()
    )
    updated = await session.execute(
        update(Memory)
        .where(Memory.id.in_(locked.scalar_subquery()))
        .values(
            access_count=Memory.access_count + 1,
            last_accessed_at=func.now(),
            importance_score=func.least(1.0, Memory.importance_score + ACCESS_IMPORTANCE_BUMP),
        )
        .returning(Memory.id, Memory.access_count, Memory.importance_score)
    )
    fresh = {r.id: r for r in updated}
    for m, _ in top:
        m.access_count = fresh[m.id].access_count
        m.importance_score = fresh[m.id].importance_score
    await session.commit()


async def search_memories(
    session: AsyncSession, user_id: str, query_embedding: list[float], top_k: int
) -> list[tuple[Memory, float]]:
    top = await rank_memories(session, user_id, query_embedding, top_k)
    await record_access(session, user_id, top)
    return top
