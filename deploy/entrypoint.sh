#!/bin/sh
# Bring the database to the right shape, then start the API.
#
# The schema is applied here rather than through Postgres's
# docker-entrypoint-initdb.d, because that directory only runs when the data
# volume is empty AND it would only cover schema.sql. The migrations under
# backend/db/migrations are a separate set, and forgetting them is the single
# most common way this app fails to start -- it dies at the connect stage
# looking for a table that was never created. Doing both here means a fresh VM
# and an upgraded one converge on the same schema with no manual step.
set -eu

DB_HOST="${POSTGRES_HOST:-postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_NAME="${POSTGRES_DB:-sfcleanup}"
export PGPASSWORD="${POSTGRES_PASSWORD:-postgres}"

psql_do() {
  psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
       --dbname="$DB_NAME" --no-password --quiet --set ON_ERROR_STOP=1 "$@"
}

echo "[entrypoint] waiting for postgres at ${DB_HOST}:${DB_PORT}"
i=0
until pg_isready --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
                 --dbname="$DB_NAME" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "[entrypoint] postgres did not become ready in 60s" >&2
    exit 1
  fi
  sleep 1
done
echo "[entrypoint] postgres is up"

# schema.sql is not uniformly idempotent, so it runs only against a database
# that has no tables at all. Migrations are written with IF NOT EXISTS and are
# safe to replay on every boot.
TABLES=$(psql_do --tuples-only --no-align --command \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")

if [ "${TABLES:-0}" -eq 0 ]; then
  echo "[entrypoint] empty database -- applying schema.sql"
  psql_do --file=/app/backend/db/schema.sql >/dev/null
else
  echo "[entrypoint] ${TABLES} relations present -- skipping schema.sql"
fi

for f in /app/backend/db/migrations/*.sql; do
  [ -e "$f" ] || continue
  echo "[entrypoint] migration $(basename "$f")"
  psql_do --file="$f" >/dev/null
done

FINAL=$(psql_do --tuples-only --no-align --command \
  "SELECT count(*) FROM information_schema.tables
    WHERE table_schema='public' AND table_type='BASE TABLE'")
echo "[entrypoint] schema ready: ${FINAL} tables"

# A missing sf CLI does not stop the API booting -- the dashboard, reports and
# assistant all work without it -- but retrieve and the delete rehearsal will
# fail, so say so loudly now rather than mid-run.
if command -v sf >/dev/null 2>&1; then
  echo "[entrypoint] sf CLI $(sf --version 2>/dev/null | head -1)"
else
  echo "[entrypoint] WARNING: sf CLI missing; retrieve and rehearsal will fail" >&2
fi

echo "[entrypoint] starting uvicorn on 0.0.0.0:8000"
# No --reload, ever. uvicorn sets use_subprocess=True when reload is on, which
# selects an event loop that cannot spawn subprocesses on some platforms and
# kills the retrieve stage. Workers stay at 1: runs hold in-process state
# (the live event bus and chat turn buffers), so a second worker would serve
# requests that cannot see them.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers
