#!/usr/bin/env bash
# Creates the Odoo "demo" database (with demo data) and the technical user with
# its API key, which is written to .env. Safe to re-run: the key is rotated.
set -euo pipefail

cd "$(dirname "$0")/.."

readonly DB_NAME="demo"
readonly MODULES="sale_management,stock,account"
readonly ENV_FILE=".env"

fail() {
  echo "init_odoo: $1" >&2
  exit 1
}

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.example "$ENV_FILE"
  fail "created $ENV_FILE from .env.example; set POSTGRES_PASSWORD and run again"
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
[[ -n "${POSTGRES_PASSWORD:-}" ]] || fail "POSTGRES_PASSWORD is empty in $ENV_FILE"

docker compose up -d --wait db

database_exists() {
  docker compose exec -T db psql -U odoo -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1
}

if database_exists; then
  echo "init_odoo: database '${DB_NAME}' already exists, skipping creation"
else
  echo "init_odoo: creating '${DB_NAME}' with ${MODULES} and demo data (takes a few minutes)"
  docker compose run --rm -T odoo odoo -d "$DB_NAME" -i "$MODULES" --stop-after-init --no-http
fi

echo "init_odoo: creating technical user '${ODOO_USERNAME:-ai_assistant}' and API key"
key_line="$(
  docker compose run --rm -T -e ODOO_TECH_LOGIN="${ODOO_USERNAME:-ai_assistant}" odoo \
    odoo shell -d "$DB_NAME" --no-http --log-level=warn <scripts/odoo_create_tech_user.py |
    grep '^ODOO_API_KEY=' | tail -n 1
)"
[[ -n "$key_line" ]] || fail "could not generate the API key"

if grep -q '^ODOO_API_KEY=' "$ENV_FILE"; then
  sed -i "s|^ODOO_API_KEY=.*|${key_line}|" "$ENV_FILE"
else
  printf '%s\n' "$key_line" >>"$ENV_FILE"
fi
echo "init_odoo: ODOO_API_KEY updated in $ENV_FILE"

# Recreate (not restart) so the api container reads the new .env.
docker compose up -d --force-recreate api
echo "init_odoo: done. Odoo: http://localhost:${ODOO_PORT:-8069}  API: http://localhost:8000/health"
