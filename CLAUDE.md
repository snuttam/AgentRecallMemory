# CLAUDE.md — Recall: Persistent Memory Service for AI Agents

This file is read automatically by Claude Code at the start of every session in
this repo. It is the source of truth for architecture, conventions, and
environment setup. Read it fully before writing code.

## 0. First-run environment check (do this before anything else)

Recall requires **PostgreSQL 16+ with the `pgvector` extension**. Before
writing or running any code, verify the environment:

1. Check whether Docker is available: `docker --version`.
   - If Docker is available, prefer it. Run `docker compose up -d` from the
     repo root (uses `docker-compose.yml`, which pulls the `pgvector/pgvector`
     image — Postgres + pgvector already built in, nothing to compile).
   - Wait for the container to be healthy (`docker compose ps`), then run
     `scripts/setup_db.sh` to create the database, enable the `vector`
     extension, and apply the initial schema.
2. If Docker is **not** available, run `scripts/setup_db.sh` directly — it
   detects the OS, installs PostgreSQL via the system package manager if
   missing, installs the `pgvector` extension (via `apt install
   postgresql-16-pgvector` on Debian/Ubuntu, or builds from source as a
   fallback on other systems), and provisions the database.
3. Never assume Postgres/pgvector are present. Always run the check in
   `scripts/setup_db.sh --check` first; only install if that check fails.
4. If installation requires `sudo` and it's unavailable in the sandbox, stop
   and report exactly what's missing and what command would fix it — don't
   silently skip the storage layer or fall back to an in-memory store without
   flagging it.
5. Copy `.env.example` to `.env` if `.env` doesn't exist, and fill in
   `DATABASE_URL` to match whatever was provisioned in steps 1–2.

Do not proceed to application code until `psql $DATABASE_URL -c "SELECT
'ok';"` succeeds and `SELECT * FROM pg_extension WHERE extname = 'vector';`
returns a row.

## 1. Project summary

Recall is a backend memory service for AI agents. Applications write memories
in (`POST /memories`) and retrieve the relevant ones back out (`GET
/memories/search`), with explicit deletion for privacy compliance (`DELETE
/memories/{id}`). It is not a vector-search demo — retrieval blends semantic
similarity with recency and importance, and a background job handles decay
and consolidation so the store doesn't grow unbounded with stale duplicates.

Build in phases (see `docs/PHASE_PLAN.md`). **Do not start Phase 2 work
(decay/consolidation, hybrid scoring tuning, auth) until Phase 1 — write a
memory, get it back on a relevant query, end to end — actually works and has
a passing test for it.**

## 2. Tech stack

- **API**: FastAPI (Python 3.11+), async throughout (`asyncpg`, not `psycopg2`)
- **Structured store**: PostgreSQL 16+
- **Vector store**: pgvector extension on the same Postgres instance (Phase 1
  decision — see `docs/PHASE_PLAN.md` for when/why to split to a dedicated
  vector DB later; don't pre-optimize for that split now)
- **ORM/migrations**: SQLAlchemy 2.x (async) + Alembic
- **Embeddings**: `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim) as the
  default local model, so Phase 1 has zero external API dependency. Keep the
  embedding call behind a small interface (`app/embeddings/base.py`) so
  swapping in OpenAI/Cohere later is a config change, not a rewrite.
- **Cache / rate limiting**: Redis (Phase 4; optional at runtime, fails open —
  see `docs/PERFORMANCE.md`)
- **Scheduling**: APScheduler for the decay/consolidation background job
  (in-process for Phase 1; revisit if this needs to survive process restarts
  reliably at scale)
- **Testing**: pytest + pytest-asyncio + httpx (ASGI test client)

## 3. Repository layout

```
recall/
├── CLAUDE.md
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── alembic.ini
├── scripts/
│   └── setup_db.sh
├── docs/
│   ├── PHASE_PLAN.md
│   └── SCORING.md
├── app/
│   ├── main.py                 # FastAPI app, router registration
│   ├── config.py                # pydantic-settings, loads .env
│   ├── db.py                    # async engine/session setup
│   ├── models.py                 # SQLAlchemy models (Memory table)
│   ├── schemas.py                 # Pydantic request/response models
│   ├── embeddings/
│   │   ├── base.py               # EmbeddingProvider interface
│   │   └── sentence_transformers.py
│   ├── retrieval/
│   │   ├── search.py             # hybrid scoring (see docs/SCORING.md)
│   │   └── scoring.py
│   ├── jobs/
│   │   └── decay.py              # scheduled decay/consolidation job
│   └── routers/
│       └── memories.py           # POST/GET/DELETE /memories
├── migrations/                    # Alembic versions
└── tests/
    ├── test_write.py
    ├── test_search.py
    └── test_delete.py
```

## 4. API contract (Phase 1)

- `POST /memories`
  Body: `{ "text": str, "user_id": str, "source": str, "importance_hint":
  float | null }`. Server sets `id`, `created_at`, `access_count=0`. Embeds
  `text` and stores both the row and the vector.
- `GET /memories/search?query=...&user_id=...&top_k=5`
  Returns memories ranked by the blended score (below), scoped strictly to
  `user_id` — never return another user's memories, even ranked at zero.
- `DELETE /memories/{id}`
  Hard delete (Phase 1). Must be idempotent — deleting a nonexistent id
  returns 204, not 404, to avoid leaking existence info across users.

Every endpoint requires `user_id` (or resolves it from auth once auth exists,
which it doesn't in Phase 1). Per-user isolation is enforced at the query
layer, not just the API layer — never construct a query that could cross
`user_id` boundaries even accidentally.

## 5. Retrieval scoring

```
final_score = (similarity * w1) + (recency_decay * w2) + (importance * w3)
```

- `similarity`: cosine similarity from pgvector, normalized to [0, 1]
- `recency_decay`: exponential decay from `timestamp`, e.g. `exp(-age_days /
  half_life_days)`
- `importance`: stored `importance_score`, updated by `access_count` and the
  decay job
- Defaults: `w1=0.6, w2=0.2, w3=0.2` — tune later, but keep the weights as
  named constants in `app/retrieval/scoring.py`, not magic numbers inline, so
  they're easy to explain and adjust.

Full rationale goes in `docs/SCORING.md` — update it whenever weights or the
formula change, since "why these weights" is part of the deliverable.

## 6. Conventions

- All DB access is async (`asyncpg` via SQLAlchemy's async engine). No
  blocking calls in request handlers.
- Every new endpoint gets a test before being considered done.
- Migrations go through Alembic — never hand-edit the schema in prod-shaped
  code, even in Phase 1.
- Config via `app/config.py` (pydantic-settings) reading from `.env` — no
  hardcoded connection strings or API keys anywhere in `app/`.
- Keep the embedding provider and the vector store behind interfaces
  (`app/embeddings/base.py`) so Phase 1's choices (sentence-transformers,
  pgvector) can be swapped without touching route/business logic.

## 7. Commands

```bash
# environment
scripts/setup_db.sh --check      # verify postgres+pgvector, no changes
scripts/setup_db.sh              # provision if missing

# migrations
alembic upgrade head

# run
uvicorn app.main:app --reload

# load test (server must be running; see docs/PERFORMANCE.md)
python scripts/loadtest.py --concurrency 32 --duration 20

# usage simulation + dashboard (Phase 5; writes data/sim_*.json, docs/usage_report.html)
PYTHONPATH=. python scripts/simulate.py --label decay_on --decay on
python scripts/report.py data/sim_*.json

# test (needs `docker compose up -d` for Postgres and Redis)
pytest
```
