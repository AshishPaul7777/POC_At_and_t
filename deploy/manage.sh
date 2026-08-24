#!/usr/bin/env bash
# Day-two operations. Everything runs inside the backend container, so the VM
# needs no Python, no Node and no Salesforce CLI of its own.
#
#   ./deploy/manage.sh setup-org [alias]   connect and probe an org  (do this first)
#   ./deploy/manage.sh retrieve  [alias]   pull org metadata to the workspace
#   ./deploy/manage.sh verify              auth, governor and routing checks
#   ./deploy/manage.sh logs      [service] follow logs
#   ./deploy/manage.sh psql                open a database shell
#   ./deploy/manage.sh backup              dump the database to ./backups
#   ./deploy/manage.sh restore <file>      restore a dump  (destructive)
#   ./deploy/manage.sh migrate             replay migrations without a restart
#   ./deploy/manage.sh shell               a shell in the backend container
#   ./deploy/manage.sh status              health and disk usage
#   ./deploy/manage.sh reload-env           recreate containers after editing .env
#   ./deploy/manage.sh stop | start | restart | update
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# Load POSTGRES_* for the psql/backup helpers without exporting the whole file.
if [ -f .env ]; then
  PGUSER=$(grep -E '^POSTGRES_USER=' .env | head -1 | cut -d= -f2-); PGUSER=${PGUSER:-postgres}
  PGDB=$(grep -E '^POSTGRES_DB=' .env | head -1 | cut -d= -f2-);     PGDB=${PGDB:-sfcleanup}
else
  PGUSER=postgres; PGDB=sfcleanup
fi

cmd=${1:-help}; shift || true

case "$cmd" in
  setup-org)
    # Idempotent: safe to re-run whenever the org's configuration changes.
    say "connecting and probing the org (~60 API calls)"
    $COMPOSE exec backend python scripts/setup_org.py "$@"
    ;;

  retrieve)
    say "retrieving org metadata into the workspace volume"
    $COMPOSE exec backend python scripts/run_retrieve.py "$@"
    ;;

  verify)
    say "auth, governor and field-population probe"
    $COMPOSE exec backend python scripts/verify_client.py
    say "routing registry against the live org"
    $COMPOSE exec backend python scripts/verify_routing.py
    ;;

  migrate)
    # The entrypoint does this on every boot; this is for applying a new
    # migration without waiting for a restart.
    say "replaying migrations"
    for f in backend/db/migrations/*.sql; do
      say "$(basename "$f")"
      $COMPOSE exec -T postgres psql -U "$PGUSER" -d "$PGDB" -q -v ON_ERROR_STOP=1 < "$f"
    done
    ;;

  psql)    exec $COMPOSE exec postgres psql -U "$PGUSER" -d "$PGDB" ;;
  shell)   exec $COMPOSE exec backend /bin/bash ;;
  logs)    exec $COMPOSE logs -f --tail=120 "${1:-}" ;;

  backup)
    mkdir -p backups
    out="backups/sfcleanup-$(date +%Y%m%d-%H%M%S).sql.gz"
    say "dumping to ${out}"
    # -T: no TTY, or the gzip stream gets mangled by terminal translation.
    $COMPOSE exec -T postgres pg_dump -U "$PGUSER" -d "$PGDB" | gzip > "$out"
    say "$(du -h "$out" | cut -f1) written"
    ;;

  restore)
    file=${1:?usage: manage.sh restore <file.sql.gz>}
    [ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }
    printf 'This REPLACES the contents of %s. Type the database name to confirm: ' "$PGDB"
    read -r answer
    [ "$answer" = "$PGDB" ] || { echo "aborted"; exit 1; }
    say "restoring ${file}"
    gunzip -c "$file" | $COMPOSE exec -T postgres psql -U "$PGUSER" -d "$PGDB" -q
    say "restored — restart the backend so it re-reads the schema"
    ;;

  status)
    $COMPOSE ps
    echo
    say "database"
    $COMPOSE exec -T postgres psql -U "$PGUSER" -d "$PGDB" -tAc \
      "SELECT count(*)||' tables' FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'" || true
    $COMPOSE exec -T postgres psql -U "$PGUSER" -d "$PGDB" -tAc \
      "SELECT count(*)||' runs, latest '||coalesce(max(started_at)::text,'none') FROM runs" || true
    echo
    say "workspace volume"
    $COMPOSE exec -T backend du -sh /app/.cache 2>/dev/null || true
    ;;

  reload-env)
    # "restart" reboots the existing containers with the environment they were
    # created with, so an edited .env has no effect and the symptom is silence.
    # Recreating is what actually re-reads the file.
    say "recreating containers with the current .env"
    $COMPOSE up -d --force-recreate backend
    say "done — check: ./deploy/manage.sh logs backend | grep auth_superuser"
    ;;

  stop)    $COMPOSE stop ;;
  start)   $COMPOSE start ;;
  restart) $COMPOSE restart "${1:-}" ;;

  update)
    # Pull code changes, rebuild, and let the entrypoint reconcile the schema.
    say "pulling"
    git pull --ff-only 2>/dev/null || say "not a git checkout — copy the new files up yourself"
    exec ./deploy/deploy.sh
    ;;

  *)
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
