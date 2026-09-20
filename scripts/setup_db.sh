#!/usr/bin/env bash
# scripts/setup_db.sh
#
# Ensures PostgreSQL + pgvector are available and the recall database exists
# with the vector extension enabled. Prefers Docker if present; falls back to
# a native install otherwise.
#
# Usage:
#   scripts/setup_db.sh            # check, then install/provision if needed
#   scripts/setup_db.sh --check    # only check, never install; exit 1 if missing

set -euo pipefail

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=true
fi

DB_NAME="${POSTGRES_DB:-recall}"
DB_USER="${POSTGRES_USER:-recall}"
DB_PASS="${POSTGRES_PASSWORD:-recall_dev_password}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

log() { echo "[setup_db] $*"; }

# Use host psql if present; otherwise fall back to psql inside the recall-db container.
run_psql() {
  if command -v psql >/dev/null 2>&1; then
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$@"
  else
    docker exec recall-db psql -U "$DB_USER" -d "$DB_NAME" "$@"
  fi
}

check_running_postgres_with_pgvector() {
  run_psql \
    -tAc "SELECT 1 FROM pg_extension WHERE extname = 'vector';" 2>/dev/null | grep -q 1
}

if check_running_postgres_with_pgvector; then
  log "Postgres is reachable at $DB_HOST:$DB_PORT and pgvector is enabled. Nothing to do."
  exit 0
fi

if $CHECK_ONLY; then
  log "Check failed: Postgres+pgvector not ready at $DB_HOST:$DB_PORT."
  exit 1
fi

log "Postgres+pgvector not ready. Attempting to provision..."

# --- Path 1: Docker ---
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  log "Docker is available. Starting db via docker compose..."
  docker compose up -d db

  log "Waiting for container health check..."
  for i in $(seq 1 30); do
    status="$(docker inspect --format='{{.State.Health.Status}}' recall-db 2>/dev/null || echo "starting")"
    if [[ "$status" == "healthy" ]]; then
      break
    fi
    sleep 2
  done

  DB_HOST="localhost"
  DB_PORT="5432"

  log "Enabling vector extension..."
  run_psql -c "CREATE EXTENSION IF NOT EXISTS vector;"

  log "Done via Docker. DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASS@$DB_HOST:$DB_PORT/$DB_NAME"
  exit 0
fi

log "Docker not available. Falling back to native install."

# --- Path 2: native install (Debian/Ubuntu) ---
if command -v apt-get >/dev/null 2>&1; then
  log "Detected apt. Installing postgresql and pgvector..."
  sudo apt-get update -y
  sudo apt-get install -y postgresql postgresql-contrib

  PG_VERSION="$(psql -V | grep -oP '\d+' | head -1)"
  if ! sudo apt-get install -y "postgresql-${PG_VERSION}-pgvector"; then
    log "Packaged pgvector unavailable for PG ${PG_VERSION}; building from source."
    sudo apt-get install -y git build-essential postgresql-server-dev-"${PG_VERSION}"
    git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git /tmp/pgvector
    (cd /tmp/pgvector && make && sudo make install)
  fi

  sudo service postgresql start

  sudo -u postgres psql -c "DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
    END IF;
  END \$\$;"
  sudo -u postgres psql -c "SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec"
  PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;"

  log "Done via native apt install."
  exit 0
fi

# --- Path 3: macOS via Homebrew ---
if command -v brew >/dev/null 2>&1; then
  log "Detected Homebrew. Installing postgresql and pgvector..."
  brew install postgresql@16 pgvector
  brew services start postgresql@16
  createuser -s "$DB_USER" 2>/dev/null || true
  createdb "$DB_NAME" -O "$DB_USER" 2>/dev/null || true
  PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;"
  log "Done via Homebrew."
  exit 0
fi

log "ERROR: No supported install path found (no Docker, no apt, no Homebrew)."
log "Install PostgreSQL 16+ and the pgvector extension manually, then re-run with --check."
exit 1
