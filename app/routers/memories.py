import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.db import get_session
from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import get_embedder
from app.models import Memory
from app.ratelimit import enforce_rate_limit
from app.retrieval.search import load_ranked, rank_memories, record_access
from app.schemas import MemoryCreate, MemoryOut, SearchResult

router = APIRouter(prefix="/memories", tags=["memories"])

DEFAULT_IMPORTANCE = 0.5


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    session: AsyncSession = Depends(get_session),
    embedder: EmbeddingProvider = Depends(get_embedder),
):
    await enforce_rate_limit(body.user_id)
    memory = Memory(
        user_id=body.user_id,
        text=body.text,
        source=body.source,
        embedding=await embedder.embed(body.text),
        importance_score=(
            body.importance_hint if body.importance_hint is not None else DEFAULT_IMPORTANCE
        ),
        access_count=0,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=settings.default_ttl_days)
            if settings.default_ttl_days
            else None
        ),
    )
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    await cache.invalidate_user(body.user_id)
    return memory


@router.get("/search", response_model=list[SearchResult])
async def search(
    query: str = Query(min_length=1),
    user_id: str = Query(min_length=1),
    top_k: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    embedder: EmbeddingProvider = Depends(get_embedder),
):
    await enforce_rate_limit(user_id)
    key = await cache.search_key(user_id, query, top_k)
    cached = await cache.get_search(key) if key else None
    if cached is not None:
        # Hit: skips embedding + vector scan. Access stats are still recorded
        # (cheap PK lookup + update), and rows are re-read so counts are fresh.
        results = await load_ranked(session, user_id, cached)
    else:
        results = await rank_memories(session, user_id, await embedder.embed(query), top_k)
        if key:
            await cache.set_search(key, [(str(m.id), score) for m, score in results])
    await record_access(session, user_id, results)
    return [
        SearchResult(**MemoryOut.model_validate(m).model_dump(), score=score)
        for m, score in results
    ]


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    user_id: str = Query(min_length=1),
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(user_id)
    # Scoped to user_id and always 204, so existence never leaks across users.
    await session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    await session.commit()
    await cache.invalidate_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
