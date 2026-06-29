#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$BACKEND_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  exit 1
fi

# shellcheck source=/dev/null
set -a
source "$ENV_FILE"
set +a

for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
  if [[ -z "${!v}" ]]; then
    echo "ERROR: $v not set in .env" >&2
    exit 1
  fi
done

echo "[init_db] Creating user $POSTGRES_USER and database $POSTGRES_DB..."

PSQL="psql -v ON_ERROR_STOP=1"
if command -v sudo >/dev/null 2>&1 && id postgres >/dev/null 2>&1; then
  PSQL="sudo -u postgres psql -v ON_ERROR_STOP=1"
fi

$PSQL -tc "SELECT 1 FROM pg_roles WHERE rolname = '$POSTGRES_USER'" | grep -q 1 \
  || $PSQL -c "CREATE ROLE $POSTGRES_USER WITH LOGIN PASSWORD '$POSTGRES_PASSWORD';"

$PSQL -tc "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'" | grep -q 1 \
  || $PSQL -c "CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER;"

$PSQL -d "$POSTGRES_DB" -c "GRANT ALL ON SCHEMA public TO $POSTGRES_USER;"
$PSQL -d "$POSTGRES_DB" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $POSTGRES_USER;"

echo "[init_db] Done."
