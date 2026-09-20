from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    score_weight_similarity: float = 0.6
    score_weight_recency: float = 0.2
    score_weight_importance: float = 0.2
    recency_half_life_days: float = 14.0

    decay_job_interval_hours: float = 24.0
    default_ttl_days: int | None = None

    # Phase 4: connection pool, cache, rate limit
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800

    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 60
    rate_limit_per_minute: int = 600  # per user_id; 0 disables

    embedding_max_batch: int = 32
    # Explicit device: sentence-transformers auto-picks Apple's MPS GPU backend,
    # which segfaults when several worker processes use it concurrently.
    embedding_device: str = "cpu"
    embedding_torch_threads: int = 0  # 0 = torch default (all cores); lower it when running many workers


settings = Settings()
