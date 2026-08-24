#!/usr/bin/env bash
# Build, start and verify the stack. Safe to re-run: this is also the upgrade
# path, since compose recreates only what changed and the entrypoint replays
# migrations idempotently.
#
#   ./deploy/deploy.sh            build changed images and bring everything up
#   ./deploy/deploy.sh --pull     also refresh the postgres/redis/nginx bases
#   ./deploy/deploy.sh --no-build reuse existing images (fastest restart)
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight ----------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker is not installed. See DEPLOYMENT.md step 1."
docker compose version >/dev/null 2>&1 || fail "the docker compose plugin is missing."
docker info >/dev/null 2>&1 || fail "cannot talk to the docker daemon. Is it running, and is your user in the docker group?"

[ -f .env ] || fail ".env not found. Run: cp .env.production.example .env && chmod 600 .env"

# Fail fast and by name, rather than letting the stack come up half-configured
# and surface as a confusing 500 twenty seconds later.
missing=()
for key in POSTGRES_PASSWORD AUTH_SECRET_KEY AUTH_SUPERUSER_EMAIL AUTH_SUPERUSER_PASSWORD SF_CLIENT_ID SF_CLIENT_SECRET SF_INSTANCE_URL; do
  value=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- || true)
  [ -n "${value}" ] || missing+=("$key")
done
if [ "${#missing[@]}" -gt 0 ]; then
  fail "these are empty in .env: ${missing[*]}"
fi

# Placeholders pass the "is it empty" check above and then fail much later, as
# a DNS error from setup-org that says nothing about .env.
for key in SF_INSTANCE_URL; do
  value=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)
  case "${value}" in
    *YOUR-DOMAIN*|*your-domain*|*example.com*)
      fail "${key} is still the template placeholder: ${value}" ;;
  esac
done

# A short superuser password is the whole front door. The backend enforces this
# too, but there it fails at boot as a log line nobody is watching.
pw=$(grep -E '^AUTH_SUPERUSER_PASSWORD=' .env | head -1 | cut -d= -f2-)
[ "${#pw}" -ge 12 ] || fail "AUTH_SUPERUSER_PASSWORD must be at least 12 characters."

secret=$(grep -E '^AUTH_SECRET_KEY=' .env | head -1 | cut -d= -f2-)
[ "${#secret}" -ge 32 ] || fail "AUTH_SECRET_KEY looks too short. Generate one with: openssl rand -hex 32"

if grep -qE '^LLM_ENABLED=true' .env; then
  key=$(grep -E '^ANTHROPIC_API_KEY=' .env | head -1 | cut -d= -f2- || true)
  [ -n "${key}" ] || say "WARNING: LLM_ENABLED=true but ANTHROPIC_API_KEY is empty. Narration and the assistant will be unavailable; verdicts are unaffected."
fi

BUILD=1
for arg in "$@"; do
  case "$arg" in
    --pull)     say "refreshing base images"; $COMPOSE pull postgres redis || true ;;
    --no-build) BUILD=0 ;;
    *)          fail "unknown option: $arg" ;;
  esac
done

# --- build and start ------------------------------------------------------------
if [ "$BUILD" -eq 1 ]; then
  say "building images (first run pulls Node and the Salesforce CLI; allow a few minutes)"
  $COMPOSE build
fi

say "starting the stack"
$COMPOSE up -d

# --- wait for health ------------------------------------------------------------
# The first boot applies the schema and every migration before uvicorn binds,
# so this legitimately takes longer than a restart.
say "waiting for the backend to become healthy"
for i in $(seq 1 60); do
  state=$($COMPOSE ps --format json backend 2>/dev/null \
          | grep -o '"Health":"[a-z]*"' | head -1 | cut -d'"' -f4 || true)
  case "${state:-}" in
    healthy) say "backend healthy after ~$((i * 5))s"; break ;;
    unhealthy)
      $COMPOSE logs --tail=40 backend
      fail "backend reported unhealthy. Logs above." ;;
  esac
  [ "$i" -eq 60 ] && { $COMPOSE logs --tail=40 backend; fail "backend did not become healthy in 5 minutes."; }
  sleep 5
done

PORT=$(grep -E '^HTTP_PORT=' .env | head -1 | cut -d= -f2- || true)
PORT=${PORT:-80}

say "verifying through nginx"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${PORT}/health" || echo 000)
[ "$code" = "200" ] || { $COMPOSE logs --tail=30 web backend; fail "/health returned ${code} through nginx."; }

echo
$COMPOSE ps
echo
say "up at http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}"
say "next: connect an org  ->  ./deploy/manage.sh setup-org"
