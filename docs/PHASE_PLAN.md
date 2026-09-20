# Phase Plan

## Phase 1 — Core loop (build this first, end to end)
Goal: write a memory, get it back on a relevant query. Nothing else matters
until this works and is tested.

- [x] `docker-compose.yml` up, `scripts/setup_db.sh` provisions db + pgvector
- [x] `app/models.py`: `Memory` table (id, user_id, text, embedding vector(384),
      source, importance_score, access_count, created_at, expires_at)
- [x] Alembic migration for the above, including the pgvector index
      (`CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops)` — fine
      to add this once there's enough data to matter; a flat scan is fine at
      Phase 1 volumes)
- [x] `app/embeddings/sentence_transformers.py`: wraps `all-MiniLM-L6-v2`
      behind the `EmbeddingProvider` interface
- [x] `POST /memories`: embed text, insert row, return the created memory
- [x] `GET /memories/search`: embed query, naive cosine similarity search via
      pgvector, scoped to `user_id`, return top-k (recency/importance
      blending can be a stub that just returns similarity in Phase 1 — get
      the loop working before tuning weights)
- [x] `DELETE /memories/{id}`: hard delete, idempotent, scoped to `user_id`
- [x] Tests for all three endpoints, including a cross-user isolation test
      (user A can never retrieve or delete user B's memories)

Do not build the decay job, consolidation, or weighted scoring until this
phase has passing tests.

## Phase 2 — Hybrid scoring
- [x] Implement full `final_score = similarity*w1 + recency_decay*w2 +
      importance*w3` in `app/retrieval/scoring.py`
- [x] `access_count` increments on retrieval; feeds back into importance
- [x] Document weight choices and any tuning in `docs/SCORING.md`

## Phase 3 — Decay & consolidation
- [x] APScheduler background job (`app/jobs/decay.py`):
  - decays `importance_score` for memories not accessed recently
  - expires memories past `expires_at` (if set)
  - flags/merges near-duplicate memories (embedding similarity above a
    threshold, same `user_id`) — merge strategy: keep the higher-importance
    one, sum access counts, log what was merged
- [x] Job runs on a schedule (`DECAY_JOB_INTERVAL_HOURS`), not on every
      request

## Phase 4 — Scale and reliability
Add caching (Redis) for hot queries, add connection pooling and basic rate
limiting, write a load test (Locust or a simple async script) that simulates
concurrent writes/reads, and see where it breaks. Fix the first real
bottleneck you find — that bottleneck-and-fix story is exactly what
"scalability, performance, reliability engineering" questions in an interview
are fishing for.

## Phase 5 — Usage insights → roadmap
Build a synthetic usage generator that simulates many users writing/querying
memories over simulated time, log real metrics (retrieval latency, hit rate
against your eval set, memory growth rate per user, decay effectiveness), and
put together a small dashboard or notebook summarizing them. Then write
yourself a one-page "if this were a real product, here's what I'd prioritize
next and why" — that's the artifact that lets you talk convincingly about
"contributing to roadmap decisions based on production insights," because
you'll have actually done a version of that exercise.

## Phase 6 — Hardening (only after 1–5 work and are tested)
- [ ] Auth (replace trusting `user_id` from the request body/query with a
      real identity layer)
- [ ] Rate limiting
- [ ] Usage metrics endpoint (write/search volume, latency, decay job stats)
- [ ] Evaluate whether pgvector still fits at current data volume, or
      whether a dedicated vector DB (Chroma, etc.) is now justified — this is
      a scaling decision to revisit with real numbers, not a Phase 1 default
