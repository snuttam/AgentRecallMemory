import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MemoryCreate(BaseModel):
    text: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    source: str
    importance_hint: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    text: str
    source: str
    importance_score: float
    access_count: int
    created_at: datetime
    expires_at: datetime | None


class SearchResult(MemoryOut):
    score: float
