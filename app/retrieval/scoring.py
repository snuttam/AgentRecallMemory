"""Hybrid retrieval scoring. See docs/SCORING.md for rationale.

    final_score = similarity * W_SIMILARITY + recency * W_RECENCY + importance * W_IMPORTANCE
"""
import math
from datetime import datetime, timezone

from app.config import settings

# Defaults (0.6 / 0.2 / 0.2, 14 days) live in app/config.py and can be
# overridden from .env; they are exposed here as named constants.
W_SIMILARITY = settings.score_weight_similarity
W_RECENCY = settings.score_weight_recency
W_IMPORTANCE = settings.score_weight_importance
RECENCY_HALF_LIFE_DAYS = settings.recency_half_life_days

# Each retrieval nudges importance up by this much (capped at 1.0).
ACCESS_IMPORTANCE_BUMP = 0.02

assert abs(W_SIMILARITY + W_RECENCY + W_IMPORTANCE - 1.0) < 1e-6, "score weights must sum to 1.0"


def normalize_cosine(cosine_similarity: float) -> float:
    """Map cosine similarity from [-1, 1] to [0, 1]."""
    return min(1.0, max(0.0, (cosine_similarity + 1.0) / 2.0))


def recency_decay(created_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.exp(-age_days / RECENCY_HALF_LIFE_DAYS)


def final_score(similarity: float, recency: float, importance: float) -> float:
    return similarity * W_SIMILARITY + recency * W_RECENCY + importance * W_IMPORTANCE
