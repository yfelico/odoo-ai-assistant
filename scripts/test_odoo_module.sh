#!/usr/bin/env bash
# Runs the tests of the ai_assistant_chat Odoo module in a throwaway database,
# which is dropped afterwards. Requires the db service (docker compose).
set -euo pipefail

cd "$(dirname "$0")/.."

readonly DB_NAME="ai_chat_test"
readonly MODULE="ai_assistant_chat"
log_file="$(mktemp)"

drop_db() {
  docker compose exec -T db dropdb -U odoo --if-exists "$DB_NAME" >/dev/null
}

cleanup() {
  drop_db
  rm -f "$log_file"
}
trap cleanup EXIT

docker compose up -d --wait db
drop_db

# A separate HTTP port, so the tests do not clash with the running Odoo.
# MSYS_NO_PATHCONV stops Git Bash on Windows from turning "/module" into a path.
MSYS_NO_PATHCONV=1 docker compose run --rm -T odoo odoo -d "$DB_NAME" -i "$MODULE" \
  --test-enable --test-tags "/${MODULE}" --stop-after-init \
  --without-demo=all --http-port 8070 --log-level=test 2>&1 | tee "$log_file" || odoo_status=$?

# Always print the summary, also when Odoo exits with an error.
summary="$(grep -E "[0-9]+ failed, [0-9]+ error\(s\) of [0-9]+ tests" "$log_file" | tail -n 1 || true)"
[[ -n "$summary" ]] || { echo "test_odoo_module: no test summary found" >&2; exit 1; }
echo "test_odoo_module: ${summary#*: }"
[[ "${odoo_status:-0}" -eq 0 && "$summary" =~ \ 0\ failed,\ 0\ error ]] || exit 1
