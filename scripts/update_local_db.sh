#!/usr/bin/env bash
#
# Pull a fresh Postgres dump from production (ubintrack-api) into local and restore it.
#
# Steps:
#   1. ssh ubintrack-api -> ./scripts/backup_db.sh
#   2. scp the dump -> backend/data/db/
#   3. ./scripts/restore_db.sh <dump>  (local .env)
#
# Requires: SSH Host ubintrack-api (same as deploy.sh / sync_media_from_prod.sh).
# Remote path: /home/git/backend on api.ubintrack.com.
#
# Usage (from backend/ or anywhere):
#   ./scripts/update_local_db.sh
#   ./scripts/update_local_db.sh --yes   # skip restore confirmation
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_HOST="ubintrack-api"
REMOTE_PATH="/home/git/backend"
LOCAL_DUMP_DIR="$BACKEND_DIR/data/db"

YES=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y)
      YES=1
      ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: $0 [--yes]" >&2
      exit 1
      ;;
  esac
done

if ! command -v ssh >/dev/null 2>&1 || ! command -v scp >/dev/null 2>&1; then
  echo "ERROR: ssh/scp not found." >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/restore_db.sh" ]]; then
  echo "ERROR: restore_db.sh not found at $SCRIPT_DIR/restore_db.sh" >&2
  exit 1
fi

mkdir -p "$LOCAL_DUMP_DIR"

echo "[update_local_db] 1/3 Backup on ${REMOTE_HOST}..."
# backup_db.sh prints the dump path as its last line.
REMOTE_DUMP="$(
  ssh "$REMOTE_HOST" "cd '$REMOTE_PATH' && ./scripts/backup_db.sh" \
    | tee /dev/stderr \
    | tail -n 1 \
    | tr -d '\r'
)"

if [[ -z "$REMOTE_DUMP" || "$REMOTE_DUMP" != *.sql ]]; then
  echo "ERROR: could not parse remote dump path from backup_db.sh output." >&2
  echo "Got: ${REMOTE_DUMP:-<empty>}" >&2
  exit 1
fi

BASENAME="$(basename "$REMOTE_DUMP")"
LOCAL_DUMP="$LOCAL_DUMP_DIR/$BASENAME"

echo "[update_local_db] 2/3 Download ${REMOTE_HOST}:${REMOTE_DUMP} -> ${LOCAL_DUMP}"
scp "${REMOTE_HOST}:${REMOTE_DUMP}" "$LOCAL_DUMP"

if [[ ! -s "$LOCAL_DUMP" ]]; then
  echo "ERROR: downloaded dump is missing or empty: $LOCAL_DUMP" >&2
  exit 1
fi

echo "[update_local_db] 3/3 Restore into local database..."
RESTORE_ARGS=("$LOCAL_DUMP")
if [[ "$YES" -eq 1 ]]; then
  RESTORE_ARGS+=(--yes)
fi
"$SCRIPT_DIR/restore_db.sh" "${RESTORE_ARGS[@]}"

echo "[update_local_db] Done. Local DB restored from $BASENAME"
