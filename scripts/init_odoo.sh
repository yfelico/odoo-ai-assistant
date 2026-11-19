#!/usr/bin/env bash
# Creates the Odoo "demo" database (with demo data) and the technical user with
# its API key, which is written to .env, and installs the ai_assistant_chat module
# paired with the API through API_TOKEN (generated if empty). Safe to re-run: the
# Odoo key is rotated and the module is updated.
set -euo pipefail

cd "$(dirname "$0")/.."

readonly DB_NAME="demo"
readonly MODULES="sale_management,stock,account"
readonly ENV_FILE=".env"
readonly CHAT_MODULE="ai_assistant_chat"

fail() {
  echo "init_odoo: $1" >&2
  exit 1
}

# Replaces KEY=... in .env, or appends it.
set_env_value() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
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

set_env_value ODOO_API_KEY "${key_line#ODOO_API_KEY=}"
echo "init_odoo: ODOO_API_KEY updated in $ENV_FILE"

if [[ -z "${API_TOKEN:-}" ]]; then
  API_TOKEN="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  set_env_value API_TOKEN "$API_TOKEN"
  echo "init_odoo: generated API_TOKEN in $ENV_FILE"
fi

echo "init_odoo: installing or updating the ${CHAT_MODULE} module"
docker compose run --rm -T odoo odoo -d "$DB_NAME" -i "$CHAT_MODULE" -u "$CHAT_MODULE" \
  --stop-after-init --no-http
docker compose run --rm -T -e API_TOKEN="$API_TOKEN" odoo \
  odoo shell -d "$DB_NAME" --no-http --log-level=warn <scripts/odoo_configure_chat.py

# Recreate Odoo too: it picks up the module mount and reloads the updated module.
docker compose up -d --force-recreate odoo

# Recreate (not restart) so the api container reads the new .env.
docker compose up -d --force-recreate api
echo "init_odoo: done. Odoo: http://localhost:${ODOO_PORT:-8069}  API: http://localhost:8000/health"
