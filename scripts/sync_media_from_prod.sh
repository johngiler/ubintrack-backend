#!/usr/bin/env bash
# Trae media/ de producción → local (rsync unidireccional).
# Sirve para que los archivos calcen con un dump de prod restaurado en local.
#
# Requisito: host SSH «ubintrack-api» en ~/.ssh/config
#
# Uso:
#   ./scripts/sync_media_from_prod.sh
#   ./scripts/sync_media_from_prod.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_HOST="${REMOTE_HOST:-ubintrack-api}"
REMOTE_PATH="${REMOTE_PATH:-/home/git/backend}"
REMOTE_MEDIA="${REMOTE_PATH}/media/"
LOCAL_MEDIA="${BACKEND_DIR}/media/"

DRY_RUN=0

usage() {
  echo "Uso: $0 [--dry-run]" >&2
  echo "  Sincroniza ${REMOTE_HOST}:${REMOTE_MEDIA} → ${LOCAL_MEDIA}" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage
      ;;
  esac
done

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync no está en el PATH." >&2
  exit 1
fi

mkdir -p "$LOCAL_MEDIA"

RSYNC_OPTS=(-avz --delete --progress -e ssh)
RSYNC_OPTS+=(--exclude ".DS_Store" --exclude "Thumbs.db")

if [[ "$DRY_RUN" -eq 1 ]]; then
  RSYNC_OPTS+=(--dry-run)
  echo "[sync_media] DRY-RUN (no escribe en disco)"
fi

echo "[sync_media] Origen:  ${REMOTE_HOST}:${REMOTE_MEDIA}"
echo "[sync_media] Destino: ${LOCAL_MEDIA}"

rsync "${RSYNC_OPTS[@]}" "${REMOTE_HOST}:${REMOTE_MEDIA}" "$LOCAL_MEDIA"

echo "[sync_media] OK"
