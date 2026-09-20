from functools import lru_cache

from app.config import settings
from app.embeddings.base import EmbeddingProvider


@lru_cache
def get_embedder() -> EmbeddingProvider:
    if settings.embedding_provider == "sentence_transformers":
        from app.embeddings.sentence_transformers import SentenceTransformerProvider

        return SentenceTransformerProvider(
            settings.embedding_model,
            settings.embedding_dim,
            settings.embedding_max_batch,
            settings.embedding_device,
            settings.embedding_torch_threads
        )
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")
