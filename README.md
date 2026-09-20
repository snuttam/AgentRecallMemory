# Recall

Persistent memory service for AI agents. Write memories in, get the relevant
ones back out, delete on request. Retrieval blends semantic similarity with
recency and importance; a background job decays and consolidates the store.

- `POST /memories` — embed and store a memory
- `GET /memories/search` — ranked retrieval, strictly scoped to `user_id`
- `DELETE /memories/{id}` — hard delete, idempotent (always 204)

Stack: FastAPI, async SQLAlchemy + asyncpg, Postgres 16 + pgvector, Redis
(cache + rate limiting), sentence-transformers (`all-MiniLM-L6-v2`), APScheduler.
Architecture and conventions live in [`CLAUDE.md`](CLAUDE.md).

## Prerequisites

macOS or Linux, Docker (Desktop on macOS), Python 3.11+.

## Setup (one time)

```bash
# Start Docker Desktop first (macOS: open -a Docker), then Postgres+pgvector and Redis
docker compose up -d
docker compose ps                  # both services should be "healthy"

# Verify Postgres + pgvector (works without a host psql)
scripts/setup_db.sh --check

# Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Config (defaults already match docker-compose)
[ -f .env ] || cp .env.example .env

# Create the tables
alembic upgrade head
```

The first request that embeds text downloads the embedding model (~90MB), so it
is slow once.

## Run

```bash
uvicorn app.main:app --reload
```

Interactive docs: <http://localhost:8000/docs>. The decay job starts with the
app and runs every `DECAY_JOB_INTERVAL_HOURS` (default 24).

```bash
# Write
curl -s -X POST localhost:8000/memories -H 'content-type: application/json' \
  -d '{"text":"I love hiking in the Alps","user_id":"alice","source":"chat","importance_hint":0.8}'

# Search (ranked results, each with a score)
curl -s 'localhost:8000/memories/search?query=what%20outdoor%20activities%20does%20she%20like&user_id=alice&top_k=5'

# Delete (always 204; user_id is required)
curl -i -X DELETE 'localhost:8000/memories/<id-from-the-write>?user_id=alice'
```

## Test

```bash
pytest -q          # ~36 tests, ~10s
```

Needs Docker running with both Postgres and Redis up. Tests use the real
Postgres with unique per-test user ids, and Redis DB 15 so they never touch dev
cache or rate-limit keys.

## Load test, simulation, dashboard (optional)

```bash
# Load test. Rate limit off so it measures throughput, not 429s.
RATE_LIMIT_PER_MINUTE=0 uvicorn app.main:app --port 8000 --workers 4 &
python scripts/loadtest.py --concurrency 32 --duration 20
# Stop the server with Ctrl-C (or `fg`, then Ctrl-C). Don't kill only the parent.

# Usage simulation (~3 min per run), then the dashboard
PYTHONPATH=. python scripts/simulate.py --label decay_on  --decay on
PYTHONPATH=. python scripts/simulate.py --label decay_off --decay off
python scripts/report.py data/sim_*.json
open docs/usage_report.html        # macOS; use xdg-open on Linux
```

`data/` holds the results of earlier runs, so `report.py` works immediately.

## Configuration

Everything is read from `.env` (see `.env.example`): database URL, score weights
and recency half-life, decay interval, default TTL, DB pool sizes, Redis URL,
cache TTL, per-user rate limit, and embedding device/batch/threads.

## Gotchas

- **Multiple workers:** with `--workers N`, set `EMBEDDING_TORCH_THREADS=1` or
  `2`. Killing only the parent can leave an orphaned worker holding port 8000;
  if you get "Address already in use", run `lsof -ti :8000 | xargs kill -9`.
- **Embedding device:** keep `EMBEDDING_DEVICE=cpu` (the default). Apple's GPU
  backend segfaults when several worker processes use it.
- **Redis is optional at runtime:** if it is down the API still works, uncached
  and unthrottled. The tests do need it running.
- **Reset everything:** `docker compose down -v` wipes the database and Redis
  volumes; then `docker compose up -d` and `alembic upgrade head` again.
- **Stop services, keep data:** `docker compose down`.

## Docs

| File | What it covers |
|---|---|
| [`docs/architecture.pdf`](docs/architecture.pdf) | Architecture diagram: components, request flows, decay job (regenerate with `python scripts/make_architecture.py`, needs Chrome) |
| [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md) | Build phases and status |
| [`docs/SCORING.md`](docs/SCORING.md) | Scoring formula, weights, rationale, changelog |
| [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) | Load-test findings: bottlenecks found and fixed |
| [`docs/usage_report.html`](docs/usage_report.html) | Simulation dashboard (open in a browser) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What to build next, and why, from the simulation data |
