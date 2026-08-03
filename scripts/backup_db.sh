#!/usr/bin/env bash
# Backup lógico de Postgres (local Docker o prod nativo).
# Lee POSTGRES_* de backend/.env y escribe data/db/YYYY-MM-DD_HHMMSS.sql
#
# Uso:
#   ./scripts/backup_db.sh
#   ./scripts/backup_db.sh /ruta/alternativa.sql   # destino opcional

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$BACKEND_DIR/.env"
DUMP_DIR="$BACKEND_DIR/data/db"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env no encontrado en $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

for v in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  if [[ -z "${!v:-}" ]]; then
    echo "ERROR: $v no está definido en .env" >&2
    exit 1
  fi
done

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump no está en el PATH." >&2
  echo "  macOS: brew install libpq && brew link --force libpq" >&2
  echo "  Debian/Ubuntu: apt install postgresql-client" >&2
  exit 1
fi

mkdir -p "$DUMP_DIR"

if [[ $# -ge 1 && -n "${1:-}" ]]; then
  OUT_FILE="$1"
  mkdir -p "$(dirname "$OUT_FILE")"
else
  STAMP="$(date +%Y-%m-%d_%H%M%S)"
  OUT_FILE="$DUMP_DIR/${STAMP}.sql"
fi

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[backup_db] Dumping $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
echo "[backup_db] -> $OUT_FILE"

pg_dump \
  --host="$POSTGRES_HOST" \
  --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" \
  --dbname="$POSTGRES_DB" \
  --format=plain \
  --no-owner \
  --no-acl \
  --file="$OUT_FILE"

BYTES="$(wc -c < "$OUT_FILE" | tr -d ' ')"
echo "[backup_db] OK (${BYTES} bytes)"
echo "$OUT_FILE"
