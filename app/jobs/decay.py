"""Scheduled decay / expiry / consolidation job (Phase 3).

One run, in a single transaction, does three things in order:
  1. expire:      hard-delete memories past `expires_at`
  2. decay:       shrink `importance_score` of memories not accessed recently
  3. consolidate: merge near-duplicates within a user (keep the higher-importance
                  one, sum access counts, log what was merged)

Runs on an interval (DECAY_JOB_INTERVAL_HOURS), never per request.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db import SessionLocal
from app.models import Memory

logger = logging.getLogger("recall.decay")

# A memory is "stale" if not accessed (or, never accessed, not created) within this window.
STALE_AFTER_DAYS = 7
# Applied once per run to each stale memory, so the effective rate depends on
# DECAY_JOB_INTERVAL_HOURS. 0.9/day halves importance in ~6.6 days of staleness.
DECAY_FACTOR = 0.9
MIN_IMPORTANCE = 0.05
# Raw cosine similarity (not the [0, 1] normalized form) at or above which two
# memories of the same user count as duplicates.
DUPLICATE_SIMILARITY = 0.95

# Only one job may run at a time across processes (e.g. multiple uvicorn workers).
ADVISORY_LOCK_KEY = 0x52454341


@dataclass
class DecayStats:
    skipped: bool = False
    expired: int = 0
    decayed: int = 0
    merged: list[tuple[uuid.UUID, list[uuid.UUID]]] = field(default_factory=list)  # (kept, removed)

    @property
    def merged_away(self) -> int:
        return sum(len(removed) for _, removed in self.merged)


async def _expire(session: AsyncSession, user_id: str | None) -> int:
    stmt = delete(Memory).where(Memory.expires_at.is_not(None), Memory.expires_at <= func.now())
    if user_id is not None:
        stmt = stmt.where(Memory.user_id == user_id)
    return (await session.execute(stmt)).rowcount


async def _decay(session: AsyncSession, user_id: str | None) -> int:
    last_touch = func.coalesce(Memory.last_accessed_at, Memory.created_at)
    stmt = (
        update(Memory)
        .where(last_touch < func.now() - timedelta(days=STALE_AFTER_DAYS))
        .where(Memory.importance_score > MIN_IMPORTANCE)
        .values(
            importance_score=func.greatest(MIN_IMPORTANCE, Memory.importance_score * DECAY_FACTOR)
        )
    )
    if user_id is not None:
        stmt = stmt.where(Memory.user_id == user_id)
    return (await session.execute(stmt)).rowcount


def _clusters(pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> list[set[uuid.UUID]]:
    """Group duplicate pairs into connected components (A~B, B~C => {A, B, C})."""
    parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[uuid.UUID, set[uuid.UUID]] = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return list(groups.values())


async def _consolidate_user(session: AsyncSession, user_id: str) -> list[tuple[uuid.UUID, list[uuid.UUID]]]:
    a, b = aliased(Memory), aliased(Memory)
    # Pairwise within one user: O(n^2) per user, fine at Phase 3 volumes.
    pair_stmt = select(a.id, b.id).where(
        a.user_id == user_id,
        b.user_id == user_id,
        a.id < b.id,
        a.embedding.cosine_distance(b.embedding) <= 1.0 - DUPLICATE_SIMILARITY,
    )
    pairs = [(r[0], r[1]) for r in (await session.execute(pair_stmt)).all()]
    merged = []
    for cluster in _clusters(pairs):
        rows = (
            await session.execute(
                select(Memory).where(Memory.id.in_(cluster), Memory.user_id == user_id)
            )
        ).scalars().all()
        # Higher importance wins; ties go to the older memory.
        keeper = max(rows, key=lambda m: (m.importance_score, -m.created_at.timestamp()))
        others = [m for m in rows if m.id != keeper.id]
        touched = [m.last_accessed_at for m in rows if m.last_accessed_at is not None]
        keeper.access_count = sum(m.access_count for m in rows)
        keeper.last_accessed_at = max(touched) if touched else None
        await session.execute(delete(Memory).where(Memory.id.in_([m.id for m in others]), Memory.user_id == user_id))
        merged.append((keeper.id, [m.id for m in others]))
        logger.info(
            "merged duplicates user=%s kept=%s removed=%s access_count=%d",
            user_id, keeper.id, [str(m.id) for m in others], keeper.access_count,
        )
    return merged


async def run_decay_job(session_factory=SessionLocal, user_id: str | None = None) -> DecayStats:
    """Run one pass. `user_id` scopes the pass to a single user (used by tests)."""
    stats = DecayStats()
    async with session_factory() as session, session.begin():
        got_lock = (
            await session.execute(select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_KEY)))
        ).scalar_one()
        if not got_lock:
            logger.info("decay job already running elsewhere; skipping")
            stats.skipped = True
            return stats

        stats.expired = await _expire(session, user_id)
        stats.decayed = await _decay(session, user_id)

        if user_id is not None:
            user_ids = [user_id]
        else:
            user_ids = (
                await session.execute(
                    select(Memory.user_id).group_by(Memory.user_id).having(func.count() > 1)
                )
            ).scalars().all()
        for uid in user_ids:
            stats.merged.extend(await _consolidate_user(session, uid))

    logger.info(
        "decay job done: expired=%d decayed=%d merged_groups=%d removed_duplicates=%d",
        stats.expired, stats.decayed, len(stats.merged), stats.merged_away,
    )
    return stats


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_decay_job,
        "interval",
        hours=settings.decay_job_interval_hours,
        id="decay",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
