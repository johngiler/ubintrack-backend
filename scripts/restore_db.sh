#!/usr/bin/env bash
# Restaura un dump .sql sobre la DB actual (DROP + CREATE + import).
# Lee POSTGRES_* de backend/.env. Sirve para local (Docker) y prod (nativo).
#
# Uso:
#   ./scripts/restore_db.sh data/db/2026-08-03_103045.sql
#   ./scripts/restore_db.sh --yes data/db/2026-08-03_103045.sql
#
# ATENCIÓN: destruye todos los datos de POSTGRES_DB en el host indicado por .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$BACKEND_DIR/.env"

YES=0
SQL_FILE=""

usage() {
  echo "Uso: $0 [--yes] <ruta.sql>" >&2
  echo "  --yes   no pedir confirmación" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      YES=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      if [[ -n "$SQL_FILE" ]]; then
        usage
      fi
      SQL_FILE="$1"
      shift
      ;;
  esac
done

if [[ -z "$SQL_FILE" ]]; then
  usage
fi

if [[ ! -f "$SQL_FILE" ]]; then
  # Resolver relativo a backend/ si no existe como ruta absoluta/relativa actual
  if [[ -f "$BACKEND_DIR/$SQL_FILE" ]]; then
    SQL_FILE="$BACKEND_DIR/$SQL_FILE"
  else
    echo "ERROR: no existe el archivo: $SQL_FILE" >&2
    exit 1
  fi
fi

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

for cmd in psql dropdb createdb; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd no está en el PATH." >&2
    echo "  macOS: brew install libpq && brew link --force libpq" >&2
    echo "  Debian/Ubuntu: apt install postgresql-client" >&2
    exit 1
  fi
done

export PGPASSWORD="$POSTGRES_PASSWORD"
export PGHOST="$POSTGRES_HOST"
export PGPORT="$POSTGRES_PORT"
export PGUSER="$POSTGRES_USER"

echo "[restore_db] Origen:  $SQL_FILE"
echo "[restore_db] Destino: $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"

if [[ "$YES" -ne 1 ]]; then
  read -r -p "Esto DROP + recreará la base '$POSTGRES_DB'. ¿Continuar? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *)
      echo "[restore_db] Cancelado."
      exit 0
      ;;
  esac
fi

echo "[restore_db] Terminando conexiones activas..."
psql -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();" \
  >/dev/null

echo "[restore_db] DROP DATABASE $POSTGRES_DB..."
dropdb --if-exists "$POSTGRES_DB"

echo "[restore_db] CREATE DATABASE $POSTGRES_DB..."
createdb --owner="$POSTGRES_USER" "$POSTGRES_DB"

echo "[restore_db] Importando SQL..."
psql -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f "$SQL_FILE" >/dev/null

echo "[restore_db] OK — base restaurada."
