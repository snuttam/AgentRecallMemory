# Performance & reliability (Phase 4)

Load test: `scripts/loadtest.py` — 20 users x 20 seeded memories, 32 concurrent
clients, 20s, 80% searches / 20% writes, skewed query pool (some queries hot).
Machine: 10-core Apple Silicon laptop; Postgres and Redis in Docker on the same
box, so the load generator, torch, and the DB all compete for the same cores.
Numbers are for comparing configurations here, not absolute capacity.

```
docker compose up -d
RATE_LIMIT_PER_MINUTE=0 uvicorn app.main:app --workers 4 &
python scripts/loadtest.py --concurrency 32 --duration 20
```

## What broke, in the order it was found

1. **Throughput ceiling ~117-134 rps, latency = concurrency / throughput.**
   Isolated microbenchmarks: embedding capped at ~170/s regardless of
   concurrency (one text per inference call); a trivial DB query did ~600/s.
   Batched inference measured 3-20x faster per text.
   *Fix:* micro-batching in `SentenceTransformerProvider` — concurrent `embed()`
   calls are coalesced into one model call. No artificial wait, so an idle
   server adds no latency.
2. **Deadlocks under concurrent searches (HTTP 500s, 10s p99).**
   `record_access` ran `UPDATE ... WHERE id IN (...)` on the returned memories.
   Two concurrent searches for the same user return overlapping memories in
   different (score) order, so they took row locks in opposite orders.
   Phase 2's access-count feature introduced this; it only shows under
   concurrency. *Fix:* lock the rows first with
   `SELECT ... ORDER BY id FOR UPDATE`, then update. Zero deadlocks since.
3. **Single process pinned at ~90% of one core.** Everything except inference
   (ORM, pydantic, JSON) runs on one event loop. *Fix:* multiple uvicorn workers
   (`--workers N`). The Phase 3 decay job is safe with N workers thanks to its
   advisory lock.
4. **Worker segfaults (client `ReadError` / `RemoteProtocolError`).**
   sentence-transformers silently auto-selects Apple's MPS GPU backend, which
   crashes (SIGSEGV in libtorch's Metal kernels) when several worker processes
   use it. It also means the early numbers above were measured on the GPU.
   *Fix:* explicit `EMBEDDING_DEVICE=cpu` default, plus `EMBEDDING_TORCH_THREADS`
   so N workers don't each spawn one thread per core.

## Results (same test, same machine)

| Config | total rps | search p50 / p95 | errors |
|---|---|---|---|
| Phase 3 code, 1 worker (MPS GPU, unnoticed) | 117 | 270 / 337 ms | 0 (deadlocks not yet triggered) |
| + batching, no cache, 1 worker | 119 | 222 / 416 ms | 21x HTTP 500 (deadlock) |
| + deadlock fix + cache, 1 worker | 134 | 246 / 415 ms | 0 |
| CPU, 1 worker | 146 | 247 / 377 ms | 0 |
| CPU, 4 workers, 1 torch thread, **cache off** | 152 | 84 / 660 ms | 0 |
| CPU, 4 workers, 1 torch thread, **cache on** | 185 | 98 / 545 ms | 0 |
| CPU, 2 workers, 4 torch threads | 171 | 100 / 596 ms | 0 |

Honest reading: the early 117 vs 146 rps gap is GPU-vs-CPU, not a regression —
the GPU path just wasn't usable with more than one process. Going from 1 to 4
workers cuts median latency ~2.5x but throughput only grows ~25%, because this
box is CPU-saturated by torch + Postgres + the load generator together. Redis
caching is worth ~20% here (152 -> 185 rps) at a 20% write mix; it would be more
on a read-heavy workload or with a slower model, since a hit skips embedding and
the vector scan entirely.

## Remaining bottlenecks (not fixed)

- **p95 (~550ms) is 5x p50.** Likely hot-row contention: every search updates
  `access_count` on the 5 rows it returns, and this test has only 20 memories
  per user, so concurrent searches for the same user queue on the same rows.
  Realistic data spreads this out, but the durable fix is to buffer access
  stats in Redis and flush them in batches (or drop the exact-count guarantee).
- Cache hits still touch Postgres (row hydration + access update), so the cache
  saves the embedding and vector scan but not the DB round trip.
- Every worker holds its own model copy (~500MB) and connection pool
  (`DB_POOL_SIZE + DB_MAX_OVERFLOW` per worker): 4 workers can open up to 120
  Postgres connections against the default `max_connections=100`.

## Design notes

- **Cache** (`app/cache.py`): key = `search:{user}:{version}:{sha1(top_k, query)}`,
  value = ranked `(id, score)` pairs, TTL 60s. Writes/deletes bump a per-user
  version, orphaning that user's cached searches. Scores can be up to a TTL
  stale (recency/importance drift); memory rows and access counts are always
  re-read. The decay job doesn't invalidate: TTL bounds the staleness and rows
  deleted by it are dropped on rehydration.
- **Rate limit** (`app/ratelimit.py`): fixed 60s window per `user_id`, Redis
  `INCR`, 429 + `Retry-After`. Fixed windows allow up to 2x burst at a window
  boundary; acceptable here. Keyed on the claimed `user_id` because there is no
  auth yet (Phase 6).
- **Fail open:** Redis is an optimization. On any Redis error the request
  proceeds uncached and unthrottled, and a 5s circuit breaker skips Redis so an
  outage costs one 200ms timeout, not one per request.
- **Pooling:** pool size, overflow, timeout and recycle are configurable
  (`DB_POOL_*`); `pool_pre_ping` was already on.
